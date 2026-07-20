"""Coordinate-free semantic anchors for the offline review lane.

This module is intentionally a small, deterministic domain boundary.  It does
not import the conversion pipeline, a GIS library, or a model provider.  Source
facts and relation facts are reduced to a stable :class:`Anchor`; external
target facts are represented by content-addressed candidates.  A model may
only return a decision over candidate IDs that were already supplied to it.
Human approval is a separate, mandatory gate before a GCP binding can be
exported; both the model decision and approval carry the exact anchor identity
and facts digest so a same-drawing decision cannot be replayed on another
entity.  A binding contains IDs and evidence IDs only -- never coordinates,
geometry, or CRS data.  The deliberately small public API uses one canonical
name per domain operation so validation behavior cannot diverge behind aliases.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


SCHEMA_VERSION = "cad2gis.semantic_anchor.v1"
CANDIDATE_SCHEMA_VERSION = "cad2gis.semantic_anchor_candidate.v1"
BINDING_SCHEMA_VERSION = "cad2gis.gcp_binding.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$"
)

# A context key is rejected, rather than silently redacted.  Silent redaction
# makes it too easy for a caller to believe that a spatial fact was reviewed.
_SPATIAL_EXACT_KEYS = {
    "cad_x",
    "cad_y",
    "center",
    "coordinates",
    "coordinate",
    "coordinate_reference_system",
    "crs",
    "dx",
    "dy",
    "easting",
    "end",
    "epsg",
    "geometry",
    "geom",
    "lat",
    "latitude",
    "location",
    "lon",
    "longitude",
    "northing",
    "point",
    "points",
    "position",
    "projected_point",
    "start",
    "transform",
    "vertex",
    "vertices",
    "wkb",
    "wkt",
    "x",
    "y",
    "z",
}
_SPATIAL_KEY_TOKENS = (
    "coordinate",
    "geometry",
    "geom",
    "crs",
    "epsg",
    "easting",
    "northing",
    "longitude",
    "latitude",
    "projected_point",
    "native_point",
    "insertion_point",
    "bounding_box",
    "bbox",
    "bounds",
    "extent",
    "envelope",
    "shape",
    "transform",
    "vertex",
    "wkb",
    "wkt",
)

_TARGET_FACT_KEYS = {
    "target_id",
    "external_target_id",
    "id",
    "entity_key",
    "label",
    "name",
    "code",
    "type",
    "feature_class",
    "class",
    "layer",
    "category",
    "subtype",
    "status",
    "description",
    "tags",
    "aliases",
    "evidence_ids",
    "evidence_keys",
    "evidence_id",
    "evidence_key",
    "source_sha256",
    "source_hash",
}


class SemanticAnchorError(ValueError):
    """Fail-closed violation of the semantic-anchor review boundary."""


def _normalise_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _is_spatial_key(key: Any) -> bool:
    normalised = _normalise_key(key)
    return (
        normalised in _SPATIAL_EXACT_KEYS
        or any(token in normalised for token in _SPATIAL_KEY_TOKENS)
        or normalised.startswith(("x_", "y_", "z_"))
        or normalised.endswith(("_x", "_y", "_z"))
    )


def _assert_model_context_safe(value: Any, path: str = "$") -> None:
    """Reject spatial/CRS keys recursively before a context is exposed."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_spatial_key(key):
                raise SemanticAnchorError(
                    f"Forbidden spatial key in model context: {path}.{key}"
                )
            _assert_model_context_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_model_context_safe(item, f"{path}[{index}]")


def _validate_target_facts(value: Mapping[str, Any]) -> None:
    """Allow only explicit coordinate-free target-candidate fact fields."""
    unknown = set(value) - _TARGET_FACT_KEYS
    if unknown:
        raise SemanticAnchorError(
            f"Unknown or non-semantic target fact fields: {sorted(unknown)}"
        )
    _assert_model_context_safe(value, "$.candidate.facts")


def _canonical(value: Any, path: str = "$") -> Any:
    """Return a JSON-safe, deterministic representation of ``value``."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SemanticAnchorError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SemanticAnchorError(f"{path} has a non-string key")
            result[key] = _canonical(item, f"{path}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise SemanticAnchorError(f"{path} contains unsupported type {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            _canonical(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SemanticAnchorError(f"Value is not canonical JSON: {exc}") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SemanticAnchorError(f"{path} must be a lowercase SHA-256 digest")
    return value


def _require_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticAnchorError(f"{path} must be a non-empty string")
    return value.strip()


def _string(value: Any, path: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise SemanticAnchorError(f"{path} must be a string")
    result = value.strip()
    if not allow_empty and not result:
        raise SemanticAnchorError(f"{path} must not be empty")
    return result


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SemanticAnchorError(f"{path} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SemanticAnchorError(f"{path} must be a finite number")
    return result


def _non_negative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SemanticAnchorError(f"{path} must be a non-negative integer")
    return value


def _first(mapping: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return default


def _endpoint_value(value: Any, path: str) -> str | None:
    """Read a relation endpoint supplied as an ID or a small fact object."""

    if value in (None, ""):
        return None
    if isinstance(value, Mapping):
        value = _first(value, "entity_key", "source_entity_key", "target_entity_key", "id", default=None)
    if value in (None, ""):
        return None
    return _require_id(value, path)


def _string_ids(value: Any, path: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        raise SemanticAnchorError(f"{path} must be a string array")
    result = tuple(sorted({_require_id(item, f"{path}[]") for item in values}))
    if not allow_empty and not result:
        raise SemanticAnchorError(f"{path} must not be empty")
    return result


def _fact_ids(*facts: Mapping[str, Any] | None, relation_facts: Sequence[Mapping[str, Any]] = ()) -> tuple[str, ...]:
    values: list[str] = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, Mapping):
            continue
        values.extend(
            _string_ids(
                _first(fact, "evidence_ids", "evidence_keys", "evidence", default=None),
                f"facts[{index}].evidence_ids",
            )
        )
        single = _first(fact, "evidence_id", "evidence_key", default=None)
        if single not in (None, ""):
            values.append(_require_id(single, f"facts[{index}].evidence_id"))
    for index, fact in enumerate(relation_facts):
        values.extend(
            _string_ids(
                _first(fact, "evidence_ids", "evidence_keys", "evidence", default=None),
                f"relations[{index}].evidence_ids",
            )
        )
        single = _first(fact, "evidence_id", "evidence_key", default=None)
        if single not in (None, ""):
            values.append(_require_id(single, f"relations[{index}].evidence_id"))
    return tuple(sorted(set(values)))


def _relation_endpoint(
    relation: Mapping[str, Any], names: tuple[str, ...], path: str,
) -> str | None:
    value = _first(relation, *names, default=None)
    return _endpoint_value(value, path)


def _relation_ratio(relation: Mapping[str, Any], path: str) -> float | None:
    ratio = _first(
        relation,
        "branch_angle_length_ratio",
        "angle_length_ratio",
        "branch_ratio",
        "ratio",
        default=None,
    )
    if ratio not in (None, ""):
        return _number(ratio, f"{path}.angle_length_ratio")

    angle = _first(
        relation,
        "branch_angle",
        "branch_angle_degrees",
        "angle_degrees",
        "angle_deg",
        "angle",
        default=None,
    )
    length = _first(
        relation,
        "branch_length",
        "branch_length_native",
        "length_native",
        "branch_length_m",
        "length_m",
        "length",
        "distance",
        default=None,
    )
    if angle in (None, "") and length in (None, ""):
        return None
    if angle in (None, "") or length in (None, ""):
        raise SemanticAnchorError(
            f"{path} must provide both branch angle and branch length"
        )
    angle_number = _number(angle, f"{path}.angle")
    length_number = _number(length, f"{path}.length")
    if length_number == 0.0:
        raise SemanticAnchorError(f"{path}.length must not be zero")
    return angle_number / length_number


def _relation_label(
    relation: Mapping[str, Any], *, anchor_is_source: bool,
) -> str | None:
    if anchor_is_source:
        value = _first(
            relation,
            "adjacent_label",
            "target_label",
            "neighbor_label",
            "neighbour_label",
            "target_feature_label",
            "adjacent_feature_label",
            default=None,
        )
        target = _first(relation, "target", "to_feature", default=None)
    else:
        value = _first(
            relation,
            "adjacent_label",
            "source_label",
            "neighbor_label",
            "neighbour_label",
            "source_feature_label",
            "adjacent_feature_label",
            default=None,
        )
        target = _first(relation, "source", "from_feature", default=None)
    if value in (None, "") and isinstance(target, Mapping):
        value = _first(target, "label", "display_label", "text", default=None)
    if value in (None, ""):
        return None
    return _string(value, "relation.adjacent_label", allow_empty=False)


@dataclass(frozen=True)
class Anchor:
    """Stable semantic identity for one source entity.

    Only semantic/topological summaries are stored.  Native points, target
    coordinates, geometry payloads, and CRS values are intentionally absent.
    """

    source_sha256: str
    entity_key: str
    handle: str = ""
    label: str = ""
    type: str = ""
    layer: str = ""
    topology_degree: int = 0
    branch_angle_length_ratios: tuple[float, ...] = ()
    adjacent_labels: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.source_sha256, "anchor.source_sha256")
        _require_id(self.entity_key, "anchor.entity_key")
        _string(self.handle, "anchor.handle")
        _string(self.label, "anchor.label")
        _string(self.type, "anchor.type")
        _string(self.layer, "anchor.layer")
        _non_negative_int(self.topology_degree, "anchor.topology_degree")
        ratios = tuple(
            _number(item, "anchor.branch_angle_length_ratios[]")
            for item in self.branch_angle_length_ratios
        )
        labels = tuple(
            sorted({_string(item, "anchor.adjacent_labels[]", allow_empty=False) for item in self.adjacent_labels})
        )
        evidence = tuple(sorted({_require_id(item, "anchor.evidence_ids[]") for item in self.evidence_ids}))
        object.__setattr__(self, "handle", _string(self.handle, "anchor.handle"))
        object.__setattr__(self, "label", _string(self.label, "anchor.label"))
        object.__setattr__(self, "type", _string(self.type, "anchor.type"))
        object.__setattr__(self, "layer", _string(self.layer, "anchor.layer"))
        object.__setattr__(self, "branch_angle_length_ratios", ratios)
        object.__setattr__(self, "adjacent_labels", labels)
        object.__setattr__(self, "evidence_ids", evidence)

    @property
    def anchor_id(self) -> str:
        """Stable identity for the source entity, independent of mutable facts.

        Labels and topology summaries are deliberately excluded.  A source
        entity's stable identity is the schema version plus its immutable
        source hash, entity key, and handle.
        """

        return _digest({
            "schema_version": SCHEMA_VERSION,
            "source_sha256": self.source_sha256,
            "entity_key": self.entity_key,
            "handle": self.handle,
        })

    @property
    def facts_sha256(self) -> str:
        """Digest of all coordinate-free semantic anchor facts."""

        return _digest(self.to_dict())

    @property
    def anchor_facts_sha256(self) -> str:
        """Explicit alias for the full coordinate-free facts digest."""

        return self.facts_sha256

    @property
    def anchor_sha256(self) -> str:
        """Backward-compatible name for the complete facts digest."""

        return self.facts_sha256

    @property
    def source_hash(self) -> str:
        return self.source_sha256

    @property
    def branch_ratios(self) -> tuple[float, ...]:
        return self.branch_angle_length_ratios

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sha256": self.source_sha256,
            "entity_key": self.entity_key,
            "handle": self.handle,
            "label": self.label,
            "type": self.type,
            "layer": self.layer,
            "topology_degree": self.topology_degree,
            "branch_angle_length_ratios": list(self.branch_angle_length_ratios),
            "adjacent_labels": list(self.adjacent_labels),
            "evidence_ids": list(self.evidence_ids),
        }

    def to_model_context(self) -> dict[str, Any]:
        context = self.to_dict()
        # Expose the exact binding handles so a provider can echo them in a
        # decision.  They are hashes/IDs, not spatial facts.
        context["anchor_id"] = self.anchor_id
        context["facts_sha256"] = self.facts_sha256
        _assert_model_context_safe(context)
        return context

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Anchor":
        if not isinstance(payload, Mapping):
            raise SemanticAnchorError("Anchor must be an object")
        allowed = {
            "source_sha256", "entity_key", "handle", "label", "type", "layer",
            "topology_degree", "branch_angle_length_ratios", "adjacent_labels",
            "evidence_ids", "anchor_id", "facts_sha256", "anchor_facts_sha256",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise SemanticAnchorError(f"Unknown anchor fields: {sorted(unknown)}")
        anchor = cls(
            source_sha256=_require_sha256(payload.get("source_sha256"), "anchor.source_sha256"),
            entity_key=_require_id(payload.get("entity_key"), "anchor.entity_key"),
            handle=_string(payload.get("handle", ""), "anchor.handle"),
            label=_string(payload.get("label", ""), "anchor.label"),
            type=_string(payload.get("type", ""), "anchor.type"),
            layer=_string(payload.get("layer", ""), "anchor.layer"),
            topology_degree=_non_negative_int(payload.get("topology_degree", 0), "anchor.topology_degree"),
            branch_angle_length_ratios=tuple(
                _number(item, "anchor.branch_angle_length_ratios[]")
                for item in payload.get("branch_angle_length_ratios", ())
            ),
            adjacent_labels=_string_ids(payload.get("adjacent_labels", ()), "anchor.adjacent_labels"),
            evidence_ids=_string_ids(payload.get("evidence_ids", ()), "anchor.evidence_ids"),
        )
        supplied_anchor_id = payload.get("anchor_id")
        if supplied_anchor_id is not None and supplied_anchor_id != anchor.anchor_id:
            raise SemanticAnchorError("Anchor anchor_id does not match its stable identity")
        supplied_facts_sha = payload.get("facts_sha256", payload.get("anchor_facts_sha256"))
        if supplied_facts_sha is not None:
            if _require_sha256(supplied_facts_sha, "anchor.facts_sha256") != anchor.facts_sha256:
                raise SemanticAnchorError("Anchor facts hash does not match its facts")
        return anchor

    @classmethod
    def from_facts(
        cls,
        source_facts: Mapping[str, Any],
        feature_facts: Mapping[str, Any] | None = None,
        relation_facts: Iterable[Mapping[str, Any]] | None = None,
    ) -> "Anchor":
        if not isinstance(source_facts, Mapping):
            raise SemanticAnchorError("source_facts must be an object")
        feature = feature_facts if feature_facts is not None else {}
        if not isinstance(feature, Mapping):
            raise SemanticAnchorError("feature_facts must be an object")
        relations = tuple(relation_facts or ())
        if any(not isinstance(item, Mapping) for item in relations):
            raise SemanticAnchorError("relation_facts must contain objects")

        source_sha = _first(source_facts, "source_sha256", "source_hash", "sha256", default=None)
        source_sha = _require_sha256(source_sha, "source_facts.source_sha256")
        entity_key = _require_id(
            _first(source_facts, "entity_key", "source_entity_key", "entity_id", "id", default=None),
            "source_facts.entity_key",
        )
        handle = _string(
            _first(source_facts, "handle", "source_handle", "cad_handle", default=""),
            "source_facts.handle",
        )
        label = _string(
            _first(feature, "label", "display_label", "text", default=_first(source_facts, "label", "text", default="")),
            "feature_facts.label",
        )
        feature_type = _string(
            _first(
                feature,
                "type",
                "feature_type",
                "feature_class",
                "entity_type",
                "geometry_type",
                default=_first(source_facts, "type", "entity_type", "dwg_type", default=""),
            ),
            "feature_facts.type",
        )
        layer = _string(
            _first(
                feature,
                "layer",
                "source_layer",
                "dwg_layer",
                default=_first(source_facts, "layer", "source_layer", "dwg_layer", default=""),
            ),
            "feature_facts.layer",
        )

        attached_relations: list[tuple[str, Mapping[str, Any], bool]] = []
        for index, relation in enumerate(relations):
            relation_source = _relation_endpoint(
                relation,
                (
                    "source_entity_key", "source_key", "source_id", "from_entity_key",
                    "from", "source", "from_id",
                ),
                f"relation_facts[{index}].source",
            )
            relation_target = _relation_endpoint(
                relation,
                (
                    "target_entity_key", "target_key", "target_id", "to_entity_key",
                    "to", "target", "to_id",
                ),
                f"relation_facts[{index}].target",
            )
            relation_entity = _relation_endpoint(
                relation,
                ("entity_key", "entity_id"),
                f"relation_facts[{index}].entity",
            )
            if relation_source is None and relation_entity is not None:
                relation_source = relation_entity
            if relation_source is None and relation_target is None:
                raise SemanticAnchorError(
                    f"relation_facts[{index}] must identify a source or target entity"
                )
            if entity_key not in {relation_source, relation_target}:
                continue
            relation_key_value = _first(
                relation, "relation_key", "relation_id", "id", default=None
            )
            relation_digest = _digest(relation)
            stable_key = (
                _require_id(relation_key_value, f"relation_facts[{index}].relation_key")
                if relation_key_value not in (None, "")
                else relation_digest
            )
            # Keep a digest tie-breaker so duplicate relation keys cannot make
            # the ratio order depend on the input list order.
            stable_key = f"{stable_key}|{relation_digest}"
            attached_relations.append((
                stable_key,
                relation,
                relation_source == entity_key,
            ))

        # Explicit feature degree is accepted as a useful immutable fact when
        # there are no relation records.  When records exist, degree is derived
        # from those records so input ordering cannot alter the result.
        explicit_degree = _first(feature, "topology_degree", "degree", default=None)
        if attached_relations:
            topology_degree = len(attached_relations)
        elif explicit_degree is not None:
            topology_degree = _non_negative_int(explicit_degree, "feature_facts.topology_degree")
        else:
            topology_degree = 0

        ratios_with_keys: list[tuple[str, float]] = []
        labels: set[str] = set(
            _string_ids(
                _first(feature, "adjacent_labels", "neighbour_labels", "neighbor_labels", default=None),
                "feature_facts.adjacent_labels",
            )
        )
        for relation_key, relation, anchor_is_source in attached_relations:
            ratio = _relation_ratio(relation, f"relation_facts[{relation_key}]")
            if ratio is not None:
                ratios_with_keys.append((relation_key, ratio))
            relation_label = _relation_label(relation, anchor_is_source=anchor_is_source)
            if relation_label:
                labels.add(relation_label)
        ratios = tuple(ratio for _, ratio in sorted(ratios_with_keys, key=lambda item: item[0]))

        evidence = set(_fact_ids(source_facts, feature, relation_facts=relations))
        # The source entity key is immutable evidence for the anchor itself.
        evidence.add(entity_key)
        return cls(
            source_sha256=source_sha,
            entity_key=entity_key,
            handle=handle,
            label=label,
            type=feature_type,
            layer=layer,
            topology_degree=topology_degree,
            branch_angle_length_ratios=ratios,
            adjacent_labels=tuple(sorted(labels)),
            evidence_ids=tuple(sorted(evidence)),
        )


def build_anchor(
    source_facts: Mapping[str, Any] | None = None,
    feature_facts: Mapping[str, Any] | None = None,
    relation_facts: Iterable[Mapping[str, Any]] | None = None,
    **aliases: Any,
) -> Anchor:
    """Build one stable anchor from explicit source/feature/relation facts."""

    if source_facts is None:
        source_facts = aliases.pop("source", aliases.pop("source_fact", None))
    if feature_facts is None:
        feature_facts = aliases.pop("feature", aliases.pop("feature_fact", None))
    if relation_facts is None:
        relation_facts = aliases.pop("relations", aliases.pop("relation_facts", None))
    if aliases:
        raise SemanticAnchorError(f"Unknown build_anchor arguments: {sorted(aliases)}")
    return Anchor.from_facts(source_facts or {}, feature_facts, relation_facts)


def build_anchors(
    source_facts: Iterable[Mapping[str, Any]],
    feature_facts: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None = None,
    relation_facts: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[Anchor, ...]:
    """Build anchors for a source collection in deterministic entity order."""

    sources = tuple(source_facts)
    if any(not isinstance(item, Mapping) for item in sources):
        raise SemanticAnchorError("source_facts must contain objects")
    by_entity: dict[str, Mapping[str, Any]] = {}
    if isinstance(feature_facts, Mapping):
        by_entity = {str(key): value for key, value in feature_facts.items()}
    elif feature_facts is not None:
        for item in feature_facts:
            if not isinstance(item, Mapping):
                raise SemanticAnchorError("feature_facts must contain objects")
            key = _first(item, "entity_key", "source_entity_key", "entity_id", default=None)
            if key not in (None, ""):
                by_entity[str(key)] = item
    relation_list = tuple(relation_facts or ())
    anchors = []
    for source in sources:
        key = _first(source, "entity_key", "source_entity_key", "entity_id", default=None)
        if key in (None, ""):
            raise SemanticAnchorError("source_facts contains an entity without an entity_key")
        key_string = _require_id(key, "source_facts.entity_key")
        related = []
        for relation in relation_list:
            if not isinstance(relation, Mapping):
                raise SemanticAnchorError("relation_facts must contain objects")
            endpoints = tuple(
                _endpoint_value(value, "relation.endpoint")
                for value in (
                    _first(
                        relation,
                        "source_entity_key", "source_key", "source_id", "from_entity_key",
                        "from", "source", "from_id", default=None,
                    ),
                    _first(
                        relation,
                        "target_entity_key", "target_key", "target_id", "to_entity_key",
                        "to", "target", "to_id", default=None,
                    ),
                    _first(relation, "entity_key", "entity_id", default=None),
                )
            )
            if key_string in endpoints:
                related.append(relation)
        anchors.append(Anchor.from_facts(source, by_entity.get(key_string), related))
    return tuple(sorted(anchors, key=lambda item: (item.source_sha256, item.entity_key)))


@dataclass(frozen=True)
class TargetCandidate:
    """Content-addressed candidate derived from explicit external target facts."""

    candidate_id: str
    target_id: str
    facts: Mapping[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    source_sha256: str | None = None
    facts_sha256: str = ""

    def __post_init__(self) -> None:
        candidate_id = _require_id(self.candidate_id, "candidate.candidate_id")
        _require_id(self.target_id, "candidate.target_id")
        canonical_facts = _canonical(self.facts, "candidate.facts")
        if not isinstance(canonical_facts, dict):
            raise SemanticAnchorError("candidate.facts must be an object")
        _validate_target_facts(canonical_facts)
        evidence = tuple(sorted({_require_id(item, "candidate.evidence_ids[]") for item in self.evidence_ids}))
        source_sha = self.source_sha256
        if source_sha is not None:
            source_sha = _require_sha256(source_sha, "candidate.source_sha256")
        expected_facts_sha = _digest(canonical_facts)
        facts_sha = self.facts_sha256 or expected_facts_sha
        _require_sha256(facts_sha, "candidate.facts_sha256")
        if facts_sha != expected_facts_sha:
            raise SemanticAnchorError("Candidate facts hash mismatch")
        if candidate_id != expected_facts_sha:
            raise SemanticAnchorError(
                "Candidate ID is not content-addressed by its facts"
            )
        object.__setattr__(self, "facts", canonical_facts)
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "source_sha256", source_sha)
        object.__setattr__(self, "facts_sha256", facts_sha)

    @property
    def target_hash(self) -> str:
        return self.facts_sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate_id": self.candidate_id,
            "target_id": self.target_id,
            "facts": _canonical(self.facts),
            "facts_sha256": self.facts_sha256,
            "evidence_ids": list(self.evidence_ids),
            "source_sha256": self.source_sha256,
        }

    def to_model_context(self) -> dict[str, Any]:
        context = {
            "candidate_id": self.candidate_id,
            "target_id": self.target_id,
            "facts": _canonical(self.facts),
            "evidence_ids": list(self.evidence_ids),
        }
        if self.source_sha256 is not None:
            context["source_sha256"] = self.source_sha256
        _assert_model_context_safe(context)
        return context

    @classmethod
    def from_facts(
        cls,
        target_facts: Mapping[str, Any],
        *,
        source_sha256: str | None = None,
    ) -> "TargetCandidate":
        if not isinstance(target_facts, Mapping):
            raise SemanticAnchorError("target_facts must be an object")
        facts = _canonical(target_facts, "target_facts")
        target_id = _first(facts, "target_id", "external_target_id", "id", "entity_key", default=None)
        target_id = _require_id(target_id, "target_facts.target_id")
        embedded_source = _first(facts, "source_sha256", "source_hash", default=None)
        if source_sha256 is not None:
            source_sha256 = _require_sha256(source_sha256, "source_sha256")
        elif embedded_source not in (None, ""):
            source_sha256 = _require_sha256(embedded_source, "target_facts.source_sha256")
        evidence = _string_ids(
            _first(facts, "evidence_ids", "evidence_keys", default=None),
            "target_facts.evidence_ids",
        )
        single = _first(facts, "evidence_id", "evidence_key", default=None)
        if single not in (None, ""):
            evidence = tuple(sorted(set(evidence) | {_require_id(single, "target_facts.evidence_id")}))
        facts_sha = _digest(facts)
        return cls(
            candidate_id=facts_sha,
            target_id=target_id,
            facts=facts,
            evidence_ids=evidence,
            source_sha256=source_sha256,
            facts_sha256=facts_sha,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TargetCandidate":
        if not isinstance(payload, Mapping):
            raise SemanticAnchorError("Candidate must be an object")
        allowed = {
            "schema_version", "candidate_id", "target_id", "facts", "facts_sha256",
            "evidence_ids", "source_sha256",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise SemanticAnchorError(f"Unknown candidate fields: {sorted(unknown)}")
        if payload.get("schema_version", CANDIDATE_SCHEMA_VERSION) != CANDIDATE_SCHEMA_VERSION:
            raise SemanticAnchorError("Unsupported candidate schema_version")
        facts = payload.get("facts", {})
        expected_facts_sha = _digest(facts)
        facts_sha = _require_sha256(payload.get("facts_sha256"), "candidate.facts_sha256")
        if facts_sha != expected_facts_sha:
            raise SemanticAnchorError("Candidate facts hash mismatch")
        candidate_id = _require_id(payload.get("candidate_id"), "candidate.candidate_id")
        if candidate_id != expected_facts_sha:
            raise SemanticAnchorError("Candidate ID is not content-addressed by its facts")
        return cls(
            candidate_id=candidate_id,
            target_id=_require_id(payload.get("target_id"), "candidate.target_id"),
            facts=facts,
            evidence_ids=_string_ids(payload.get("evidence_ids", ()), "candidate.evidence_ids"),
            source_sha256=payload.get("source_sha256"),
            facts_sha256=facts_sha,
        )


def create_target_candidate(
    target_facts: Mapping[str, Any],
    *,
    source_sha256: str | None = None,
) -> TargetCandidate:
    return TargetCandidate.from_facts(target_facts, source_sha256=source_sha256)


def make_candidate_id(target_facts: Mapping[str, Any]) -> str:
    """Return the content address that ``create_target_candidate`` uses."""

    return create_target_candidate(target_facts).candidate_id


def create_target_candidates(
    target_facts: Iterable[Mapping[str, Any]],
    *,
    source_sha256: str | None = None,
) -> tuple[TargetCandidate, ...]:
    candidates = tuple(
        create_target_candidate(item, source_sha256=source_sha256)
        for item in target_facts
    )
    by_id: dict[str, TargetCandidate] = {}
    for candidate in candidates:
        if candidate.candidate_id in by_id:
            raise SemanticAnchorError(f"Duplicate candidate_id {candidate.candidate_id!r}")
        by_id[candidate.candidate_id] = candidate
    return tuple(sorted(candidates, key=lambda item: item.candidate_id))


def _coerce_candidates(value: Any) -> dict[str, TargetCandidate]:
    if isinstance(value, Mapping):
        if "candidate_id" in value:
            values: list[Any] = [value]
        else:
            values = []
            for key, item in value.items():
                if isinstance(item, TargetCandidate):
                    values.append(item)
                elif isinstance(item, Mapping):
                    item_value = dict(item)
                    item_value.setdefault("candidate_id", str(key))
                    values.append(item_value)
                else:
                    raise SemanticAnchorError("candidate map values must be candidates or objects")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        raise SemanticAnchorError("candidates must be a candidate array or map")
    result: dict[str, TargetCandidate] = {}
    for index, item in enumerate(values):
        if isinstance(item, TargetCandidate):
            candidate = item
        elif isinstance(item, Mapping):
            candidate = TargetCandidate.from_dict(item)
        else:
            raise SemanticAnchorError(f"candidates[{index}] must be a candidate object")
        if candidate.candidate_id in result:
            raise SemanticAnchorError(f"Duplicate candidate_id {candidate.candidate_id!r}")
        result[candidate.candidate_id] = candidate
    return result


@dataclass(frozen=True)
class ModelDecision:
    """A provider output constrained to existing candidate IDs."""

    action: Literal["select", "rank", "abstain"]
    candidate_ids: tuple[str, ...] = ()
    source_sha256: str | None = None
    evidence_ids: tuple[str, ...] = ()
    rationale: str = ""
    # These fields bind a provider response to one exact anchor.  They are
    # optional on the value object for backwards-compatible parsing of an
    # unbound proposal, but export validation requires all three.
    anchor_id: str | None = None
    entity_key: str | None = None
    facts_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.action not in {"select", "rank", "abstain"}:
            raise SemanticAnchorError("decision.action must be select, rank, or abstain")
        ids = tuple(_require_id(item, "decision.candidate_ids[]") for item in self.candidate_ids)
        if len(set(ids)) != len(ids):
            raise SemanticAnchorError("decision.candidate_ids must be unique")
        if self.action == "select" and len(ids) != 1:
            raise SemanticAnchorError("select requires exactly one candidate ID")
        if self.action == "rank" and len(ids) < 2:
            raise SemanticAnchorError("rank requires at least two candidate IDs")
        if self.action == "abstain" and ids:
            raise SemanticAnchorError("abstain must not contain candidate IDs")
        source = self.source_sha256
        if source is not None:
            source = _require_sha256(source, "decision.source_sha256")
        anchor_id = self.anchor_id
        entity_key = self.entity_key
        facts_sha = self.facts_sha256
        metadata = (anchor_id, entity_key, facts_sha)
        if any(item is not None for item in metadata) and not all(
            item is not None for item in metadata
        ):
            raise SemanticAnchorError(
                "decision anchor binding requires anchor_id, entity_key, and facts_sha256"
            )
        if anchor_id is not None:
            anchor_id = _require_id(anchor_id, "decision.anchor_id")
            entity_key = _require_id(entity_key, "decision.entity_key")
            facts_sha = _require_sha256(facts_sha, "decision.facts_sha256")
        evidence = tuple(sorted({_require_id(item, "decision.evidence_ids[]") for item in self.evidence_ids}))
        rationale = _string(self.rationale, "decision.rationale")
        object.__setattr__(self, "candidate_ids", ids)
        object.__setattr__(self, "source_sha256", source)
        object.__setattr__(self, "anchor_id", anchor_id)
        object.__setattr__(self, "entity_key", entity_key)
        object.__setattr__(self, "facts_sha256", facts_sha)
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "rationale", rationale)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "action": self.action,
            "candidate_ids": list(self.candidate_ids),
            "evidence_ids": list(self.evidence_ids),
            "rationale": self.rationale,
        }
        if self.source_sha256 is not None:
            value["source_sha256"] = self.source_sha256
        if self.anchor_id is not None:
            value.update({
                "anchor_id": self.anchor_id,
                "entity_key": self.entity_key,
                "facts_sha256": self.facts_sha256,
            })
        return value

    @property
    def anchor_facts_sha256(self) -> str | None:
        return self.facts_sha256

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ModelDecision":
        if not isinstance(payload, Mapping):
            raise SemanticAnchorError("Model decision must be an object")
        allowed = {
            "action", "candidate_id", "candidate_ids", "source_sha256", "source_hash",
            "evidence_ids", "rationale", "anchor_id", "entity_key", "facts_sha256",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise SemanticAnchorError(f"Unknown model decision fields: {sorted(unknown)}")
        raw_ids = payload.get("candidate_ids")
        singular_id = payload.get("candidate_id")
        if raw_ids is None and singular_id not in (None, ""):
            raw_ids = [singular_id]
        elif raw_ids is not None and singular_id not in (None, ""):
            singular = _require_id(singular_id, "decision.candidate_id")
            parsed_ids = _string_ids(raw_ids, "decision.candidate_ids")
            if parsed_ids != (singular,):
                raise SemanticAnchorError(
                    "decision.candidate_id disagrees with candidate_ids"
                )
        ids = _string_ids(raw_ids, "decision.candidate_ids")
        source = payload.get("source_sha256", payload.get("source_hash"))
        if source == "":
            source = None
        return cls(
            action=_string(payload.get("action"), "decision.action", allow_empty=False),
            candidate_ids=ids,
            source_sha256=source,
            evidence_ids=_string_ids(payload.get("evidence_ids", ()), "decision.evidence_ids"),
            rationale=_string(payload.get("rationale", ""), "decision.rationale"),
            anchor_id=payload.get("anchor_id"),
            entity_key=payload.get("entity_key"),
            facts_sha256=payload.get("facts_sha256"),
        )


def validate_model_decision(
    payload: ModelDecision | Mapping[str, Any],
    candidates: Any = None,
    source_sha256: str | None = None,
    *,
    evidence_ids: Iterable[str] | None = None,
    require_source_hash: bool = False,
    candidate_ids: Any = None,
    source_hash: str | None = None,
    anchor: Anchor | Mapping[str, Any] | None = None,
    expected_anchor: Anchor | Mapping[str, Any] | None = None,
    anchor_id: str | None = None,
    entity_key: str | None = None,
    facts_sha256: str | None = None,
    require_anchor_binding: bool = False,
) -> ModelDecision:
    """Validate a model output against the pre-existing candidate universe."""

    if candidates is None:
        candidates = candidate_ids
    if candidates is None:
        raise SemanticAnchorError("Existing candidate IDs are required")
    if source_sha256 is None:
        source_sha256 = source_hash
    expected_anchor_value = anchor if anchor is not None else expected_anchor
    if isinstance(source_sha256, Anchor):
        expected_anchor_value = source_sha256
        source_sha256 = source_sha256.source_sha256
    elif isinstance(source_sha256, Mapping) and expected_anchor_value is None:
        expected_anchor_value = source_sha256
        source_sha256 = None
    decision = payload if isinstance(payload, ModelDecision) else ModelDecision.from_payload(payload)
    # A provider adapter may retain only the bound ID inventory.  Accepting a
    # string ID sequence here does not weaken export: ``export_gcp_binding``
    # still requires full TargetCandidate objects for the selected target.
    try:
        candidate_map = _coerce_candidates(candidates)
        available_ids = set(candidate_map)
    except SemanticAnchorError:
        if isinstance(candidates, Mapping) and "candidate_id" not in candidates and all(
            isinstance(item, str) for item in candidates
        ):
            available_ids = set(_string_ids(tuple(candidates), "candidate_ids"))
            candidate_map = {}
        elif isinstance(candidates, (list, tuple, set, frozenset)) and all(
            isinstance(item, str) for item in candidates
        ):
            available_ids = set(_string_ids(candidates, "candidate_ids"))
            candidate_map = {}
        else:
            raise
    unknown = set(decision.candidate_ids) - available_ids
    if unknown:
        raise SemanticAnchorError(f"Decision references unknown candidate IDs: {sorted(unknown)}")
    expected_source = source_sha256
    if expected_anchor_value is not None:
        expected_anchor_value = _coerce_anchor(expected_anchor_value)
        if expected_source is None:
            expected_source = expected_anchor_value.source_sha256
        anchor_id = expected_anchor_value.anchor_id
        entity_key = expected_anchor_value.entity_key
        facts_sha256 = expected_anchor_value.facts_sha256
    if expected_source is not None:
        expected_source = _require_sha256(expected_source, "source_sha256")
        if decision.source_sha256 != expected_source:
            raise SemanticAnchorError("Decision source hash does not match anchor source")
    elif require_source_hash and decision.source_sha256 is None:
        raise SemanticAnchorError("Decision must include source hash")
    if evidence_ids is not None:
        known_evidence = set(_string_ids(tuple(evidence_ids), "evidence_ids"))
        unknown_evidence = set(decision.evidence_ids) - known_evidence
        if unknown_evidence:
            raise SemanticAnchorError(f"Decision references unknown evidence IDs: {sorted(unknown_evidence)}")
    expected_anchor_fields = (anchor_id, entity_key, facts_sha256)
    if any(item is not None for item in expected_anchor_fields):
        if not all(item is not None for item in expected_anchor_fields):
            raise SemanticAnchorError(
                "expected anchor binding requires anchor_id, entity_key, and facts_sha256"
            )
        if decision.anchor_id != _require_id(anchor_id, "anchor_id"):
            raise SemanticAnchorError("Decision anchor_id does not match anchor")
        if decision.entity_key != _require_id(entity_key, "entity_key"):
            raise SemanticAnchorError("Decision entity_key does not match anchor")
        if decision.facts_sha256 != _require_sha256(facts_sha256, "facts_sha256"):
            raise SemanticAnchorError("Decision facts hash does not match anchor facts")
    elif require_anchor_binding:
        raise SemanticAnchorError(
            "Decision must include anchor_id, entity_key, and facts_sha256"
        )
    return decision


@dataclass(frozen=True)
class HumanApproval:
    """Explicit human approval for exactly one existing candidate."""

    reviewer: str
    timestamp: str
    source_sha256: str
    candidate_id: str
    # Approval metadata is task-bound.  Unbound records may still be parsed for
    # diagnostics, but ``export_gcp_binding`` rejects them fail-closed.
    anchor_id: str | None = None
    entity_key: str | None = None
    facts_sha256: str | None = None

    def __post_init__(self) -> None:
        reviewer = _string(self.reviewer, "approval.reviewer", allow_empty=False)
        timestamp = _string(self.timestamp, "approval.timestamp", allow_empty=False)
        if not _RFC3339_RE.fullmatch(timestamp):
            raise SemanticAnchorError("approval.timestamp must be an RFC-3339 timestamp")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SemanticAnchorError("approval.timestamp must be a valid timestamp") from exc
        if parsed.tzinfo is None:
            raise SemanticAnchorError("approval.timestamp must include a timezone")
        source = _require_sha256(self.source_sha256, "approval.source_sha256")
        candidate = _require_id(self.candidate_id, "approval.candidate_id")
        anchor_id = self.anchor_id
        entity_key = self.entity_key
        facts_sha = self.facts_sha256
        metadata = (anchor_id, entity_key, facts_sha)
        if any(item is not None for item in metadata) and not all(
            item is not None for item in metadata
        ):
            raise SemanticAnchorError(
                "approval anchor binding requires anchor_id, entity_key, and facts_sha256"
            )
        if anchor_id is not None:
            anchor_id = _require_id(anchor_id, "approval.anchor_id")
            entity_key = _require_id(entity_key, "approval.entity_key")
            facts_sha = _require_sha256(facts_sha, "approval.facts_sha256")
        object.__setattr__(self, "reviewer", reviewer)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "source_sha256", source)
        object.__setattr__(self, "candidate_id", candidate)
        object.__setattr__(self, "anchor_id", anchor_id)
        object.__setattr__(self, "entity_key", entity_key)
        object.__setattr__(self, "facts_sha256", facts_sha)

    @property
    def source_hash(self) -> str:
        return self.source_sha256

    def to_dict(self) -> dict[str, str]:
        value = {
            "reviewer": self.reviewer,
            "timestamp": self.timestamp,
            "source_sha256": self.source_sha256,
            "candidate_id": self.candidate_id,
        }
        if self.anchor_id is not None:
            value.update({
                "anchor_id": self.anchor_id,
                "entity_key": self.entity_key,
                "facts_sha256": self.facts_sha256,
            })
        return value

    @property
    def anchor_facts_sha256(self) -> str | None:
        return self.facts_sha256

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "HumanApproval":
        if not isinstance(payload, Mapping):
            raise SemanticAnchorError("Human approval must be an object")
        allowed = {
            "reviewer", "timestamp", "source_sha256", "source_hash", "candidate_id",
            "anchor_id", "entity_key", "facts_sha256",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise SemanticAnchorError(f"Unknown approval fields: {sorted(unknown)}")
        source = payload.get("source_sha256", payload.get("source_hash"))
        return cls(
            reviewer=_string(payload.get("reviewer"), "approval.reviewer", allow_empty=False),
            timestamp=_string(payload.get("timestamp"), "approval.timestamp", allow_empty=False),
            source_sha256=_require_sha256(source, "approval.source_sha256"),
            candidate_id=_require_id(payload.get("candidate_id"), "approval.candidate_id"),
            anchor_id=payload.get("anchor_id"),
            entity_key=payload.get("entity_key"),
            facts_sha256=payload.get("facts_sha256"),
        )


def create_human_approval(
    reviewer: str,
    timestamp: str,
    source_sha256: str,
    candidate_id: str,
    *,
    anchor: Anchor | Mapping[str, Any] | None = None,
    anchor_id: str | None = None,
    entity_key: str | None = None,
    facts_sha256: str | None = None,
) -> HumanApproval:
    if anchor is not None:
        anchor_value = _coerce_anchor(anchor)
        anchor_id = anchor_value.anchor_id if anchor_id is None else anchor_id
        entity_key = anchor_value.entity_key if entity_key is None else entity_key
        facts_sha256 = anchor_value.facts_sha256 if facts_sha256 is None else facts_sha256
    return HumanApproval(
        reviewer,
        timestamp,
        source_sha256,
        candidate_id,
        anchor_id,
        entity_key,
        facts_sha256,
    )


def validate_human_approval(
    payload: HumanApproval | Mapping[str, Any],
    source_sha256: str | None = None,
    candidate_ids: Any = None,
    *,
    decision: ModelDecision | Mapping[str, Any] | None = None,
    anchor: Anchor | Mapping[str, Any] | None = None,
    expected_anchor: Anchor | Mapping[str, Any] | None = None,
    anchor_id: str | None = None,
    entity_key: str | None = None,
    facts_sha256: str | None = None,
    require_anchor_binding: bool = False,
) -> HumanApproval:
    """Validate reviewer, timestamp, source binding, and candidate existence."""

    approval = payload if isinstance(payload, HumanApproval) else HumanApproval.from_payload(payload)
    expected_anchor_value = anchor if anchor is not None else expected_anchor
    if isinstance(source_sha256, Anchor):
        expected_anchor_value = source_sha256
        source_sha256 = source_sha256.source_sha256
    elif isinstance(source_sha256, Mapping) and expected_anchor_value is None:
        expected_anchor_value = source_sha256
        source_sha256 = None
    if expected_anchor_value is not None:
        expected_anchor_value = _coerce_anchor(expected_anchor_value)
        if source_sha256 is None:
            source_sha256 = expected_anchor_value.source_sha256
        anchor_id = expected_anchor_value.anchor_id
        entity_key = expected_anchor_value.entity_key
        facts_sha256 = expected_anchor_value.facts_sha256
    if source_sha256 is not None:
        expected = _require_sha256(source_sha256, "source_sha256")
        if approval.source_sha256 != expected:
            raise SemanticAnchorError("Approval source hash does not match anchor source")
    candidate_map: dict[str, TargetCandidate] | None = None
    if candidate_ids is not None:
        if isinstance(candidate_ids, (Mapping, list, tuple, set, frozenset)):
            if isinstance(candidate_ids, Mapping) and "candidate_id" not in candidate_ids:
                candidate_map = _coerce_candidates(candidate_ids)
            elif isinstance(candidate_ids, Mapping):
                candidate_map = _coerce_candidates([candidate_ids])
            elif isinstance(candidate_ids, (list, tuple, set, frozenset)):
                try:
                    candidate_map = _coerce_candidates(candidate_ids)
                except SemanticAnchorError:
                    ids = {_require_id(item, "candidate_ids[]") for item in candidate_ids}
                    if approval.candidate_id not in ids:
                        raise SemanticAnchorError("Approval references unknown candidate ID")
                    candidate_map = None
        if candidate_map is not None and approval.candidate_id not in candidate_map:
            raise SemanticAnchorError("Approval references unknown candidate ID")
    if decision is not None:
        validated_decision = decision if isinstance(decision, ModelDecision) else ModelDecision.from_payload(decision)
        if validated_decision.action != "select":
            raise SemanticAnchorError("Only a select decision can be approved for GCP export")
        if approval.candidate_id != validated_decision.candidate_ids[0]:
            raise SemanticAnchorError("Approval candidate ID does not match selected candidate")
        # When the model response is already anchor-bound, an approval for a
        # different anchor must not be accepted even if source/candidate IDs
        # happen to match.
        if validated_decision.anchor_id is not None:
            if approval.anchor_id != validated_decision.anchor_id:
                raise SemanticAnchorError("Approval anchor_id does not match decision")
            if approval.entity_key != validated_decision.entity_key:
                raise SemanticAnchorError("Approval entity_key does not match decision")
            if approval.facts_sha256 != validated_decision.facts_sha256:
                raise SemanticAnchorError("Approval facts hash does not match decision")
    expected_anchor_fields = (anchor_id, entity_key, facts_sha256)
    if any(item is not None for item in expected_anchor_fields):
        if not all(item is not None for item in expected_anchor_fields):
            raise SemanticAnchorError(
                "expected approval binding requires anchor_id, entity_key, and facts_sha256"
            )
        if approval.anchor_id != _require_id(anchor_id, "anchor_id"):
            raise SemanticAnchorError("Approval anchor_id does not match anchor")
        if approval.entity_key != _require_id(entity_key, "entity_key"):
            raise SemanticAnchorError("Approval entity_key does not match anchor")
        if approval.facts_sha256 != _require_sha256(facts_sha256, "facts_sha256"):
            raise SemanticAnchorError("Approval facts hash does not match anchor")
    elif require_anchor_binding:
        raise SemanticAnchorError(
            "Approval must include anchor_id, entity_key, and facts_sha256"
        )
    return approval


@dataclass(frozen=True)
class GCPBinding:
    """Coordinate-free GCP binding: source/target IDs plus evidence IDs only."""

    source_id: str
    target_id: str
    evidence_ids: tuple[str, ...] = ()
    # Kept as an in-memory audit alias and serialized to preserve candidate
    # lineage through downstream binding stores.
    candidate_id: str = field(default="", repr=False, compare=False)
    schema_version: str = field(default=BINDING_SCHEMA_VERSION, repr=False, compare=False)

    def __post_init__(self) -> None:
        source = _require_id(self.source_id, "binding.source_id")
        target = _require_id(self.target_id, "binding.target_id")
        candidate = _require_id(self.candidate_id, "binding.candidate_id")
        schema = _string(self.schema_version, "binding.schema_version", allow_empty=False)
        if schema != BINDING_SCHEMA_VERSION:
            raise SemanticAnchorError("Unsupported GCP binding schema_version")
        evidence = tuple(sorted({_require_id(item, "binding.evidence_ids[]") for item in self.evidence_ids}))
        object.__setattr__(self, "source_id", source)
        object.__setattr__(self, "target_id", target)
        object.__setattr__(self, "candidate_id", candidate)
        object.__setattr__(self, "schema_version", schema)
        object.__setattr__(self, "evidence_ids", evidence)

    @property
    def source_entity_key(self) -> str:
        return self.source_id

    @property
    def target_entity_key(self) -> str:
        return self.target_id

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "evidence_ids": list(self.evidence_ids),
        }
        _assert_model_context_safe(value)
        return value

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GCPBinding":
        if not isinstance(payload, Mapping):
            raise SemanticAnchorError("GCP binding must be an object")
        expected = {"schema_version", "source_id", "target_id", "candidate_id", "evidence_ids"}
        actual = set(payload)
        if actual != expected:
            raise SemanticAnchorError(
                f"GCP binding fields mismatch; unknown={sorted(actual - expected)}, "
                f"missing={sorted(expected - actual)}"
            )
        if payload["schema_version"] != BINDING_SCHEMA_VERSION:
            raise SemanticAnchorError("Unsupported GCP binding schema_version")
        return cls(
            source_id=_require_id(payload["source_id"], "binding.source_id"),
            target_id=_require_id(payload["target_id"], "binding.target_id"),
            evidence_ids=_string_ids(payload["evidence_ids"], "binding.evidence_ids"),
            candidate_id=_require_id(payload["candidate_id"], "binding.candidate_id"),
            schema_version=_string(payload["schema_version"], "binding.schema_version", allow_empty=False),
        )


def validate_gcp_binding(
    payload: GCPBinding | Mapping[str, Any],
) -> GCPBinding:
    """Validate one serialized or in-memory binding at the export boundary."""

    if isinstance(payload, GCPBinding):
        # Reconstruct to force the same schema/candidate checks as parsing.
        return GCPBinding.from_dict(payload.to_dict())
    return GCPBinding.from_dict(payload)


def _coerce_anchor(value: Anchor | Mapping[str, Any]) -> Anchor:
    return value if isinstance(value, Anchor) else Anchor.from_dict(value)


def export_gcp_binding(
    anchor: Anchor | Mapping[str, Any],
    candidates: Any,
    decision: ModelDecision | Mapping[str, Any],
    approval: HumanApproval | Mapping[str, Any],
) -> GCPBinding:
    """Export one approved ``select`` as an ID/evidence-only GCP binding."""

    anchor_value = _coerce_anchor(anchor)
    candidate_map = _coerce_candidates(candidates)
    decision_value = validate_model_decision(
        decision,
        candidate_map,
        anchor_value.source_sha256,
        evidence_ids=anchor_value.evidence_ids,
        require_source_hash=True,
        anchor=anchor_value,
        require_anchor_binding=True,
    )
    if decision_value.action != "select":
        raise SemanticAnchorError("GCP export requires a selected candidate, not rank/abstain")
    approval_value = validate_human_approval(
        approval,
        anchor_value.source_sha256,
        candidate_map,
        decision=decision_value,
        anchor=anchor_value,
        require_anchor_binding=True,
    )
    candidate = candidate_map[approval_value.candidate_id]
    if candidate.source_sha256 is not None and candidate.source_sha256 != anchor_value.source_sha256:
        raise SemanticAnchorError("Candidate source hash does not match anchor source")
    evidence = tuple(sorted(set(anchor_value.evidence_ids) | set(candidate.evidence_ids) | set(decision_value.evidence_ids)))
    binding = GCPBinding(
        source_id=anchor_value.entity_key,
        target_id=candidate.target_id,
        evidence_ids=evidence,
        candidate_id=candidate.candidate_id,
    )
    # The final serialization is intentionally rechecked at the boundary.
    return validate_gcp_binding(binding)


def export_approved_gcp_bindings(
    anchor: Anchor | Mapping[str, Any],
    candidates: Any,
    decisions: Iterable[ModelDecision | Mapping[str, Any]],
    approvals: Iterable[HumanApproval | Mapping[str, Any]] | Mapping[str, Any],
) -> tuple[GCPBinding, ...]:
    """Export every approved select decision, skipping explicit abstentions."""

    anchor_value = _coerce_anchor(anchor)
    candidate_map = _coerce_candidates(candidates)
    if isinstance(approvals, Mapping) and "candidate_id" not in approvals:
        approval_values = list(approvals.values())
    elif isinstance(approvals, Mapping):
        approval_values = [approvals]
    else:
        approval_values = list(approvals)
    approval_map: dict[str, HumanApproval] = {}
    for value in approval_values:
        approval = value if isinstance(value, HumanApproval) else HumanApproval.from_payload(value)
        if approval.candidate_id in approval_map:
            raise SemanticAnchorError("Duplicate human approval candidate ID")
        approval_map[approval.candidate_id] = approval
    bindings: list[GCPBinding] = []
    for raw_decision in decisions:
        parsed = raw_decision if isinstance(raw_decision, ModelDecision) else ModelDecision.from_payload(raw_decision)
        if parsed.action == "abstain":
            continue
        if parsed.action == "rank":
            raise SemanticAnchorError("rank is not a final GCP selection")
        candidate_id = parsed.candidate_ids[0]
        approval = approval_map.get(candidate_id)
        if approval is None:
            raise SemanticAnchorError(f"Missing human approval for candidate ID {candidate_id!r}")
        bindings.append(export_gcp_binding(anchor_value, candidate_map, parsed, approval))
    return tuple(bindings)


def build_model_context(
    anchor: Anchor | Mapping[str, Any],
    candidates: Any,
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a coordinate-free, candidate-bound model context."""

    anchor_value = _coerce_anchor(anchor)
    candidate_map = _coerce_candidates(candidates)
    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "anchor": anchor_value.to_model_context(),
        "available_candidate_ids": sorted(candidate_map),
        "candidates": [
            candidate_map[item].to_model_context() for item in sorted(candidate_map)
        ],
    }
    if extra is not None:
        if not isinstance(extra, Mapping):
            raise SemanticAnchorError("model context extra must be an object")
        context["extra"] = _canonical(extra)
    _assert_model_context_safe(context)
    return context


def validate_model_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an already-built context before handing it to a model."""

    if not isinstance(context, Mapping):
        raise SemanticAnchorError("model context must be an object")
    value = _canonical(context)
    _assert_model_context_safe(value)
    return value


__all__ = [
    "SCHEMA_VERSION",
    "CANDIDATE_SCHEMA_VERSION",
    "BINDING_SCHEMA_VERSION",
    "SemanticAnchorError",
    "Anchor",
    "TargetCandidate",
    "ModelDecision",
    "HumanApproval",
    "GCPBinding",
    "build_anchor",
    "build_anchors",
    "create_target_candidate",
    "create_target_candidates",
    "make_candidate_id",
    "validate_model_decision",
    "create_human_approval",
    "validate_human_approval",
    "validate_gcp_binding",
    "build_model_context",
    "validate_model_context",
    "export_gcp_binding",
    "export_approved_gcp_bindings",
]
