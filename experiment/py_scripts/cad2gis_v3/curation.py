"""Optional, proposal-only curation over deterministic CAD evidence.

This module is deliberately outside :mod:`cad2gis_v3.pipeline`.  It can expose
immutable reader/evidence facts to a human or an external review provider, but a
proposal can only select/rank IDs already present in its content-addressed
review bundle, or abstain.  It has no API for changing CAD/GIS facts.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


BUNDLE_SCHEMA_VERSION = "cad2gis.review_bundle.v2"
PROPOSAL_SCHEMA_VERSION = "cad2gis.curation_proposal.v1"
AUDIT_SCHEMA_VERSION = "cad2gis.curation_audit.v2"
INVENTORY_BATCH_SIZE = 32

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_PROPOSAL_KEYS = {
    "asset_id",
    "attribute",
    "attributes",
    "coordinate",
    "coordinates",
    "crs",
    "edge",
    "edges",
    "epsg",
    "feature_id",
    "geom",
    "geometry",
    "label",
    "latitude",
    "layer",
    "layers",
    "length",
    "longitude",
    "new_id",
    "node",
    "nodes",
    "point",
    "points",
    "span",
    "topology",
    "wkb",
    "wkt",
    "x",
    "y",
}
_FORBIDDEN_MUTATIONS = [
    "attributes",
    "coordinates",
    "crs",
    "geometry",
    "ids",
    "labels",
    "layers",
    "lengths",
    "span_measurements",
    "topology",
]
_MODEL_CONTEXT_DROP_TOKENS = (
    "alignment_point",
    "bounding_box",
    "coordinate",
    "easting",
    "endpoint",
    "epsg",
    "extent",
    "geometry",
    "insertion_point",
    "insertion",
    "latitude",
    "longitude",
    "matrix",
    "northing",
    "native_point",
    "origin",
    "point",
    "position",
    "projected_point",
    "transform",
    "vertex",
    "vertices",
    "wkb",
    "wkt",
)
_MODEL_CONTEXT_DROP_EXACT = {
    "cad_x", "cad_y", "center", "crs", "dx", "dy", "end", "lat", "location",
    "lon", "start", "x", "y", "z",
}
_FORBIDDEN_RATIONALE_FACT_RE = re.compile(
    r"(?i)(?:\b(?:x|y|lat|lon|latitude|longitude|easting|northing|length|longueur|"
    r"span|crs|epsg|geometry|coordinate|topology|new_id|asset_id|feature_id)\b\s*[:=]"
    r"|[-+]?\d+(?:\.\d+)?\s*[,/]\s*[-+]?\d+(?:\.\d+)?)"
)


class CurationError(ValueError):
    """Fail-closed curation boundary violation."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CurationError(f"Value is not canonical JSON: {exc}") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str | Path, *, max_bytes: int) -> dict[str, Any]:
    resolved = Path(path).resolve()
    size = resolved.stat().st_size
    if size > max_bytes:
        raise CurationError(f"JSON input exceeds {max_bytes} bytes: {resolved.name}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurationError(f"Invalid UTF-8 JSON in {resolved.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise CurationError(f"JSON root must be an object: {resolved.name}")
    return value


def write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> Path:
    """Write canonical review artifacts without exposing a partial file."""

    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
    ).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved.name}.", suffix=".tmp", dir=resolved.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, resolved)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return resolved


def _expect_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise CurationError(f"{path} fields mismatch; unknown={unknown}, missing={missing}")


def _require_string(value: Any, path: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise CurationError(f"{path} must be a string")
    if not allow_empty and not value:
        raise CurationError(f"{path} must not be empty")
    if len(value) > 1_048_576:
        raise CurationError(f"{path} is too long")
    return value


def _require_sha256(value: Any, path: str) -> str:
    result = _require_string(value, path, allow_empty=False)
    if not _SHA256_RE.fullmatch(result):
        raise CurationError(f"{path} must be a lowercase SHA-256 digest")
    return result


def _require_string_list(
    value: Any, path: str, *, allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CurationError(f"{path} must be an array")
    result = tuple(_require_string(item, f"{path}[]", allow_empty=False) for item in value)
    if not allow_empty and not result:
        raise CurationError(f"{path} must not be empty")
    if len(set(result)) != len(result):
        raise CurationError(f"{path} must contain unique IDs")
    return result


def _require_number_or_none(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CurationError(f"{path} must be a finite number or null")
    result = float(value)
    if not math.isfinite(result):
        raise CurationError(f"{path} must be finite")
    return result


def _validate_json_value(value: Any, path: str = "$", depth: int = 0) -> None:
    if depth > 32:
        raise CurationError(f"{path} exceeds maximum JSON nesting")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CurationError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CurationError(f"{path} has a non-string key")
            _validate_json_value(item, f"{path}.{key}", depth + 1)
        return
    raise CurationError(f"{path} contains unsupported JSON type {type(value).__name__}")


def _json_object(value: Any, path: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CurationError(f"{path} contains malformed deterministic JSON") from exc
    if not isinstance(value, dict):
        raise CurationError(f"{path} must contain a JSON object")
    _validate_json_value(value, path)
    return value


def _safe_model_context(value: Any) -> Any:
    """Remove coordinate payloads before any cloud-visible context.

    Source text, business tags, and immutable measurement facts remain available
    for semantic review.  Coordinates, transforms, and geometry payloads stay in
    deterministic evidence.  The proposal schema cannot create or rewrite either
    measurements or spatial facts.
    """

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in _MODEL_CONTEXT_DROP_EXACT:
                continue
            if any(token in normalized for token in _MODEL_CONTEXT_DROP_TOKENS):
                continue
            result[str(key)] = _safe_model_context(item)
        return result
    if isinstance(value, list):
        return [_safe_model_context(item) for item in value]
    return value


def _native_geometry_summary(value: Any, path: str) -> dict[str, Any]:
    if value in (None, ""):
        points: list[Any] = []
    elif isinstance(value, str):
        try:
            points = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CurationError(f"{path} contains malformed point JSON") from exc
    else:
        points = value
    if not isinstance(points, list):
        raise CurationError(f"{path} must contain a point array")
    _validate_json_value(points, path)
    return {
        "point_count": len(points),
        # Coordinates stay in deterministic evidence; only their binding digest
        # is exposed to curation so a model cannot echo/rewrite them.
        "native_points_sha256": _digest(points),
    }


@dataclass(frozen=True)
class ReviewBundle:
    """Validated immutable context for proposal-only curation."""

    payload: dict[str, Any]

    @property
    def bundle_sha256(self) -> str:
        return str(self.payload["bundle_sha256"])

    @property
    def source_sha256(self) -> str:
        return str(self.payload["source"]["dwg_sha256"])

    @property
    def evidence_sha256(self) -> str:
        return str(self.payload["evidence"]["sha256"])

    @property
    def tasks(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.payload["tasks"])

    def to_dict(self) -> dict[str, Any]:
        return json.loads(_canonical_bytes(self.payload))


def _validate_object_record(value: Any, index: int) -> tuple[str, str]:
    path = f"$.objects[{index}]"
    if not isinstance(value, dict):
        raise CurationError(f"{path} must be an object")
    _expect_keys(value, {"evidence_id", "fact_sha256", "facts", "full_fact_sha256"}, path)
    evidence_id = _require_string(value["evidence_id"], f"{path}.evidence_id", allow_empty=False)
    fact_sha = _require_sha256(value["fact_sha256"], f"{path}.fact_sha256")
    _require_sha256(value["full_fact_sha256"], f"{path}.full_fact_sha256")
    facts = value["facts"]
    if not isinstance(facts, dict):
        raise CurationError(f"{path}.facts must be an object")
    _expect_keys(
        facts,
        {
            "block_attributes", "block_name", "cad_role", "dimension_measurement_present",
            "dimension_text_override", "extraction_backend",
            "disposition", "entity_type", "handle", "layer", "layout",
            "layout_role", "measurements", "owner_handle", "raw_properties",
            "reader_backend_status", "rotation", "scale_factors",
            "shape_binding",
            "source_file_name", "style", "text",
        },
        f"{path}.facts",
    )
    for name in (
        "block_name", "cad_role", "dimension_text_override", "disposition",
        "entity_type", "extraction_backend", "handle", "layer", "layout",
        "layout_role", "owner_handle", "reader_backend_status",
        "source_file_name", "text",
    ):
        _require_string(facts[name], f"{path}.facts.{name}")
    _json_object(facts["block_attributes"], f"{path}.facts.block_attributes")
    _json_object(facts["raw_properties"], f"{path}.facts.raw_properties")
    if not isinstance(facts["dimension_measurement_present"], bool):
        raise CurationError(f"{path}.facts.dimension_measurement_present must be boolean")
    measurements = facts["measurements"]
    if not isinstance(measurements, dict):
        raise CurationError(f"{path}.facts.measurements must be an object")
    _expect_keys(
        measurements,
        {
            "dimension_measurement", "immutable", "native_length",
            "native_length_source", "unit",
        },
        f"{path}.facts.measurements",
    )
    _require_number_or_none(
        measurements["native_length"], f"{path}.facts.measurements.native_length",
    )
    dimension_measurement = _require_number_or_none(
        measurements["dimension_measurement"],
        f"{path}.facts.measurements.dimension_measurement",
    )
    if (dimension_measurement is not None) != facts["dimension_measurement_present"]:
        raise CurationError(
            f"{path}.facts.dimension_measurement_present disagrees with measurements"
        )
    if measurements["immutable"] is not True:
        raise CurationError(f"{path}.facts.measurements.immutable must be true")
    for name in ("native_length_source", "unit"):
        _require_string(measurements[name], f"{path}.facts.measurements.{name}")
    _require_number_or_none(facts["rotation"], f"{path}.facts.rotation")
    for object_name, expected in (
        ("shape_binding", {"native_fingerprint", "vertex_count"}),
        ("style", {"aci_color", "linetype", "lineweight", "true_color"}),
    ):
        item = facts[object_name]
        if not isinstance(item, dict):
            raise CurationError(f"{path}.facts.{object_name} must be an object")
        _expect_keys(item, expected, f"{path}.facts.{object_name}")
    _require_sha256(
        facts["shape_binding"]["native_fingerprint"],
        f"{path}.facts.shape_binding.native_fingerprint",
    )
    point_count = facts["shape_binding"]["vertex_count"]
    if isinstance(point_count, bool) or not isinstance(point_count, int) or point_count < 0:
        raise CurationError(f"{path}.facts.shape_binding.vertex_count must be a non-negative integer")
    scale_factors = facts["scale_factors"]
    if not isinstance(scale_factors, list) or len(scale_factors) != 3:
        raise CurationError(f"{path}.facts.scale_factors must contain three values")
    for index, item in enumerate(scale_factors):
        _require_number_or_none(item, f"{path}.facts.scale_factors[{index}]")
    for name in ("aci_color", "lineweight"):
        value_number = facts["style"][name]
        if value_number is not None and (
            isinstance(value_number, bool) or not isinstance(value_number, int)
        ):
            raise CurationError(f"{path}.facts.style.{name} must be an integer or null")
    for name in ("linetype", "true_color"):
        _require_string(facts["style"][name], f"{path}.facts.style.{name}")
    if fact_sha != _digest(facts):
        raise CurationError(f"{path}.fact_sha256 does not bind its facts")
    return evidence_id, fact_sha


def _validate_candidate_record(value: Any, index: int) -> tuple[str, str, tuple[str, ...]]:
    path = f"$.candidates[{index}]"
    if not isinstance(value, dict):
        raise CurationError(f"{path} must be an object")
    _expect_keys(
        value,
        {"allowed_class", "candidate_id", "evidence_ids", "facts", "facts_sha256", "kind", "task_id"},
        path,
    )
    candidate_id = _require_string(value["candidate_id"], f"{path}.candidate_id", allow_empty=False)
    task_id = _require_string(value["task_id"], f"{path}.task_id", allow_empty=False)
    kind = _require_string(value["kind"], f"{path}.kind", allow_empty=False)
    if kind not in {"feature_candidate", "annotation_assignment", "inventory_batch"}:
        raise CurationError(f"{path}.kind is not supported")
    _require_string(value["allowed_class"], f"{path}.allowed_class", allow_empty=False)
    evidence_ids = _require_string_list(value["evidence_ids"], f"{path}.evidence_ids", allow_empty=False)
    facts = value["facts"]
    if not isinstance(facts, dict):
        raise CurationError(f"{path}.facts must be an object")
    if kind == "feature_candidate":
        expected = {
            "attributes", "display_label", "feature_class", "geometry_kind",
            "geometry_role", "label_provenance", "source_handle", "source_layer",
        }
        _expect_keys(facts, expected, f"{path}.facts")
        _json_object(facts["attributes"], f"{path}.facts.attributes")
        for name in expected - {"attributes"}:
            _require_string(facts[name], f"{path}.facts.{name}")
    elif kind == "annotation_assignment":
        expected = {
            "annotation_key", "deterministically_selected", "distance_native_m",
            "distance_status",
            "family_id", "rule_id", "status", "target_class", "target_feature_key",
            "target_handle",
        }
        _expect_keys(facts, expected, f"{path}.facts")
        for name in expected - {"deterministically_selected", "distance_native_m"}:
            _require_string(facts[name], f"{path}.facts.{name}")
        if not isinstance(facts["deterministically_selected"], bool):
            raise CurationError(f"{path}.facts.deterministically_selected must be boolean")
        distance = _require_number_or_none(
            facts["distance_native_m"], f"{path}.facts.distance_native_m",
        )
        expected_distance_status = "available" if distance is not None else "unavailable"
        if facts["distance_status"] != expected_distance_status:
            raise CurationError(f"{path}.facts.distance_status disagrees with distance_native_m")
    else:
        expected = {
            "batch_index", "batch_size", "object_fact_sha256", "signature",
        }
        _expect_keys(facts, expected, f"{path}.facts")
        for name in ("batch_index", "batch_size"):
            item = facts[name]
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise CurationError(f"{path}.facts.{name} must be a non-negative integer")
        if facts["batch_size"] != len(evidence_ids) or not 1 <= facts["batch_size"] <= INVENTORY_BATCH_SIZE:
            raise CurationError(f"{path}.facts.batch_size does not match evidence_ids")
        _require_sha256(
            facts["object_fact_sha256"], f"{path}.facts.object_fact_sha256",
        )
        signature = facts["signature"]
        if not isinstance(signature, dict):
            raise CurationError(f"{path}.facts.signature must be an object")
        signature_keys = {
            "block_name", "cad_role", "disposition", "entity_type", "layer",
            "layout_role", "reader_backend_status",
        }
        _expect_keys(signature, signature_keys, f"{path}.facts.signature")
        for name in signature_keys:
            _require_string(signature[name], f"{path}.facts.signature.{name}")
        if value["allowed_class"] != "CAD_INVENTORY_BATCH":
            raise CurationError(f"{path}.allowed_class must be CAD_INVENTORY_BATCH")
    facts_sha = _require_sha256(value["facts_sha256"], f"{path}.facts_sha256")
    if facts_sha != _digest(facts):
        raise CurationError(f"{path}.facts_sha256 does not bind its facts")
    return candidate_id, task_id, evidence_ids


def validate_review_bundle(payload: Mapping[str, Any]) -> ReviewBundle:
    """Validate structure, cross-references, and the content-addressed hash."""

    if not isinstance(payload, dict):
        raise CurationError("Review bundle must be a JSON object")
    _expect_keys(
        payload,
        {
            "bundle_sha256", "candidates", "coverage", "evidence", "objects",
            "policy", "schema_version", "source", "tasks",
        },
        "$",
    )
    if payload["schema_version"] != BUNDLE_SCHEMA_VERSION:
        raise CurationError("Unsupported review bundle schema_version")
    source = payload["source"]
    evidence = payload["evidence"]
    policy = payload["policy"]
    coverage = payload["coverage"]
    if not all(isinstance(item, dict) for item in (source, evidence, policy, coverage)):
        raise CurationError("source, evidence, policy, and coverage must be objects")
    _expect_keys(source, {"dwg_name", "dwg_sha256"}, "$.source")
    _expect_keys(evidence, {"file_name", "sha256"}, "$.evidence")
    _expect_keys(
        policy,
        {
            "allowed_actions", "conversion_import_allowed", "coordinate_payloads_visible",
            "forbidden_mutations", "immutable_measurements_visible", "stage",
        },
        "$.policy",
    )
    _require_string(source["dwg_name"], "$.source.dwg_name", allow_empty=False)
    _require_sha256(source["dwg_sha256"], "$.source.dwg_sha256")
    _require_string(evidence["file_name"], "$.evidence.file_name", allow_empty=False)
    _require_sha256(evidence["sha256"], "$.evidence.sha256")
    if policy != {
        "allowed_actions": ["select", "rank", "abstain"],
        "conversion_import_allowed": False,
        "coordinate_payloads_visible": False,
        "forbidden_mutations": _FORBIDDEN_MUTATIONS,
        "immutable_measurements_visible": True,
        "stage": "curate_after_deterministic_readcad_and_candidate_evidence",
    }:
        raise CurationError("Review bundle policy is not the immutable proposal-only policy")

    objects = payload["objects"]
    candidates = payload["candidates"]
    tasks = payload["tasks"]
    if not isinstance(objects, list) or not isinstance(candidates, list) or not isinstance(tasks, list):
        raise CurationError("objects, candidates, and tasks must be arrays")
    if len(objects) > 1_000_000 or len(candidates) > 1_000_000 or len(tasks) > 1_000_000:
        raise CurationError("Review bundle exceeds safety limits")

    evidence_ids: set[str] = set()
    for index, item in enumerate(objects):
        evidence_id, _ = _validate_object_record(item, index)
        if evidence_id in evidence_ids:
            raise CurationError(f"Duplicate evidence_id {evidence_id!r}")
        evidence_ids.add(evidence_id)

    candidate_map: dict[str, tuple[str, tuple[str, ...]]] = {}
    for index, item in enumerate(candidates):
        candidate_id, task_id, candidate_evidence = _validate_candidate_record(item, index)
        if candidate_id in candidate_map:
            raise CurationError(f"Duplicate candidate_id {candidate_id!r}")
        unknown_evidence = set(candidate_evidence) - evidence_ids
        if unknown_evidence:
            raise CurationError(f"Candidate {candidate_id!r} references unknown evidence IDs")
        candidate_map[candidate_id] = (task_id, candidate_evidence)

    task_ids: set[str] = set()
    referenced_candidates: set[str] = set()
    inventory_membership = Counter()
    for index, item in enumerate(tasks):
        path = f"$.tasks[{index}]"
        if not isinstance(item, dict):
            raise CurationError(f"{path} must be an object")
        _expect_keys(item, {"allowed_actions", "candidate_ids", "evidence_ids", "kind", "task_id"}, path)
        task_id = _require_string(item["task_id"], f"{path}.task_id", allow_empty=False)
        if task_id in task_ids:
            raise CurationError(f"Duplicate task_id {task_id!r}")
        task_ids.add(task_id)
        kind = _require_string(item["kind"], f"{path}.kind", allow_empty=False)
        if kind not in {"feature_review", "annotation_assignment_review", "inventory_review"}:
            raise CurationError(f"{path}.kind is not supported")
        task_candidates = _require_string_list(item["candidate_ids"], f"{path}.candidate_ids", allow_empty=False)
        task_evidence = _require_string_list(item["evidence_ids"], f"{path}.evidence_ids", allow_empty=False)
        expected_actions = ["select", "abstain"] if len(task_candidates) == 1 else ["select", "rank", "abstain"]
        if item["allowed_actions"] != expected_actions:
            raise CurationError(f"{path}.allowed_actions does not match candidate cardinality")
        if set(task_evidence) - evidence_ids:
            raise CurationError(f"{path} references unknown evidence IDs")
        if kind == "inventory_review":
            if len(task_candidates) != 1:
                raise CurationError(f"{path} inventory_review requires one batch candidate")
            for evidence_id in task_evidence:
                inventory_membership[evidence_id] += 1
        for candidate_id in task_candidates:
            if candidate_id not in candidate_map:
                raise CurationError(f"{path} references unknown candidate_id {candidate_id!r}")
            if candidate_map[candidate_id][0] != task_id:
                raise CurationError(f"Candidate {candidate_id!r} is bound to a different task")
            if not set(candidate_map[candidate_id][1]).issubset(task_evidence):
                raise CurationError(f"{path} omits candidate evidence")
            if candidate_id in referenced_candidates:
                raise CurationError(f"Candidate {candidate_id!r} is referenced by multiple tasks")
            referenced_candidates.add(candidate_id)
    if referenced_candidates != set(candidate_map):
        raise CurationError("Every candidate must be referenced by exactly one task")
    _expect_keys(
        coverage,
        {
            "inventory_covered_objects", "inventory_task_count",
            "multiply_tasked_objects", "object_count", "untasked_objects",
        },
        "$.coverage",
    )
    expected_coverage = {
        "object_count": len(evidence_ids),
        "inventory_task_count": sum(
            item["kind"] == "inventory_review" for item in tasks
        ),
        "inventory_covered_objects": sum(count > 0 for count in inventory_membership.values()),
        "untasked_objects": len(evidence_ids - set(inventory_membership)),
        "multiply_tasked_objects": sum(count > 1 for count in inventory_membership.values()),
    }
    for name, expected in expected_coverage.items():
        value = coverage[name]
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise CurationError(f"$.coverage.{name} must equal {expected}")
    if expected_coverage["untasked_objects"] or expected_coverage["multiply_tasked_objects"]:
        raise CurationError("Every readCAD object must belong to exactly one inventory task")

    bundle_sha = _require_sha256(payload["bundle_sha256"], "$.bundle_sha256")
    unhashed = dict(payload)
    unhashed.pop("bundle_sha256")
    if bundle_sha != _digest(unhashed):
        raise CurationError("Review bundle hash mismatch (stale or tampered bundle)")
    return ReviewBundle(json.loads(_canonical_bytes(payload)))


def load_review_bundle(path: str | Path) -> ReviewBundle:
    return validate_review_bundle(_read_json(path, max_bytes=256 * 1024 * 1024))


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _require_table(connection: sqlite3.Connection, table: str) -> set[str]:
    tables = {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if table not in tables:
        raise CurationError(f"Evidence store is missing required table {table!r}")
    return _table_columns(connection, table)


def _column_or_null(columns: set[str], name: str) -> str:
    return f'"{name}"' if name in columns else f'NULL AS "{name}"'


def build_review_bundle(
    evidence_path: str | Path,
    dwg_path: str | Path,
) -> ReviewBundle:
    """Build a deterministic, coordinate-free review bundle from evidence.

    The evidence database and DWG are opened read-only.  Every ``cad_entities``
    record is represented, including every INSERT instance; raw coordinates are
    replaced by a source-bound fingerprint.  Authoritative lengths, DIMENSION
    values, SPAN metrics, and candidate distances remain visible as immutable
    evidence, while the proposal protocol has no mutation fields for them.
    """

    evidence_file = Path(evidence_path).resolve()
    dwg_file = Path(dwg_path).resolve()
    if not evidence_file.is_file() or not dwg_file.is_file():
        raise CurationError("Evidence and DWG paths must be existing files")
    evidence_sha_before = _file_sha256(evidence_file)
    source_sha = _file_sha256(dwg_file)
    uri = f"file:{urllib.parse.quote(evidence_file.as_posix(), safe='/:')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        entity_columns = _require_table(connection, "cad_entities")
        required_entity_columns = {
            "entity_key", "source_sha256", "source_file", "cad_handle", "cad_layout",
            "layout_role", "cad_role", "dwg_layer", "dwg_type", "block_name",
            "block_attributes", "text", "native_points", "dimension_value", "aci_color",
            "true_color", "linetype", "lineweight", "rotation", "disposition", "scale_x",
            "scale_y", "scale_z",
        }
        missing = required_entity_columns - entity_columns
        if missing:
            raise CurationError(f"cad_entities is missing required columns: {sorted(missing)}")
        bound_hashes = {
            str(row[0]).lower()
            for row in connection.execute(
                "SELECT DISTINCT source_sha256 FROM cad_entities WHERE source_sha256 IS NOT NULL"
            )
        }
        if bound_hashes != {source_sha}:
            raise CurationError(
                f"DWG SHA-256 does not match evidence source binding: {sorted(bound_hashes)}"
            )

        select_names = [
            "entity_key", "source_file", "cad_handle", "cad_layout", "layout_role", "cad_role",
            "dwg_layer", "dwg_type", "block_name", "block_attributes", "text", "native_points",
            "dimension_value", "aci_color", "true_color", "linetype", "lineweight", "rotation",
            "disposition", "scale_x", "scale_y", "scale_z", "owner_handle",
            "dimension_text_override", "native_length", "extraction_backend",
            "reader_backend_status", "raw_properties",
        ]
        select_sql = ", ".join(_column_or_null(entity_columns, name) for name in select_names)
        objects: list[dict[str, Any]] = []
        for row in connection.execute(
            f'SELECT {select_sql} FROM "cad_entities" ORDER BY entity_key'
        ):
            full_fact_sha256 = _digest({
                name: row[name] for name in select_names
            })
            raw_properties = _json_object(
                row["raw_properties"], "cad_entities.raw_properties"
            )
            facts = {
                "source_file_name": Path(str(row["source_file"] or "")).name,
                "handle": str(row["cad_handle"] or ""),
                "layout": str(row["cad_layout"] or ""),
                "layout_role": str(row["layout_role"] or ""),
                "cad_role": str(row["cad_role"] or ""),
                "layer": str(row["dwg_layer"] or ""),
                "entity_type": str(row["dwg_type"] or ""),
                "block_name": str(row["block_name"] or ""),
                "block_attributes": _safe_model_context(
                    _json_object(row["block_attributes"], "cad_entities.block_attributes")
                ),
                "text": str(row["text"] or ""),
                "dimension_measurement_present": row["dimension_value"] is not None,
                "dimension_text_override": str(row["dimension_text_override"] or ""),
                "owner_handle": str(row["owner_handle"] or ""),
                "extraction_backend": str(row["extraction_backend"] or ""),
                "reader_backend_status": str(row["reader_backend_status"] or ""),
                "measurements": {
                    "native_length": _require_number_or_none(
                        row["native_length"], "cad_entities.native_length",
                    ),
                    "dimension_measurement": _require_number_or_none(
                        row["dimension_value"], "cad_entities.dimension_value",
                    ),
                    "native_length_source": str(
                        raw_properties.get("native_length_source") or ""
                    ),
                    "unit": "source_drawing_unit",
                    "immutable": True,
                },
                "style": {
                    "aci_color": None if row["aci_color"] is None else int(row["aci_color"]),
                    "true_color": str(row["true_color"] or ""),
                    "linetype": str(row["linetype"] or ""),
                    "lineweight": None if row["lineweight"] is None else int(row["lineweight"]),
                },
                "rotation": _require_number_or_none(row["rotation"], "cad_entities.rotation"),
                "scale_factors": [
                    _require_number_or_none(row["scale_x"], "cad_entities.scale_x"),
                    _require_number_or_none(row["scale_y"], "cad_entities.scale_y"),
                    _require_number_or_none(row["scale_z"], "cad_entities.scale_z"),
                ],
                "disposition": str(row["disposition"] or ""),
                "shape_binding": {
                    "vertex_count": _native_geometry_summary(
                        row["native_points"], "cad_entities.native_points"
                    )["point_count"],
                    "native_fingerprint": _native_geometry_summary(
                        row["native_points"], "cad_entities.native_points"
                    )["native_points_sha256"],
                },
                "raw_properties": _safe_model_context(
                    raw_properties
                ),
            }
            evidence_id = str(row["entity_key"] or "")
            if not evidence_id:
                raise CurationError("cad_entities contains an empty entity_key")
            objects.append({
                "evidence_id": evidence_id,
                "full_fact_sha256": full_fact_sha256,
                "fact_sha256": _digest(facts),
                "facts": facts,
            })

        object_ids = {item["evidence_id"] for item in objects}
        if len(object_ids) != len(objects):
            raise CurationError("cad_entities contains duplicate entity_key values")

        candidates: list[dict[str, Any]] = []
        grouped: dict[str, dict[str, Any]] = {}
        inventory_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for item in objects:
            facts = item["facts"]
            signature = (
                facts["layout_role"], facts["entity_type"], facts["layer"],
                facts["block_name"], facts["cad_role"], facts["disposition"],
                facts["reader_backend_status"],
            )
            inventory_groups[signature].append(item)
        inventory_task_count = 0
        for signature_index, (signature_values, members) in enumerate(
            sorted(inventory_groups.items(), key=lambda item: item[0])
        ):
            signature = dict(zip(
                (
                    "layout_role", "entity_type", "layer", "block_name", "cad_role",
                    "disposition", "reader_backend_status",
                ),
                signature_values,
            ))
            for batch_index, offset in enumerate(range(0, len(members), INVENTORY_BATCH_SIZE)):
                batch = members[offset:offset + INVENTORY_BATCH_SIZE]
                batch_ids = [item["evidence_id"] for item in batch]
                object_fact_sha256 = _digest([
                    item["full_fact_sha256"] for item in batch
                ])
                binding = {
                    "signature": signature,
                    "batch_index": batch_index,
                    "evidence_ids": batch_ids,
                    "object_fact_sha256": object_fact_sha256,
                }
                binding_sha = _digest(binding)
                task_id = f"inventory:{signature_index:04d}:{batch_index:04d}:{binding_sha[:12]}"
                candidate_id = f"inventory-batch:{binding_sha}"
                candidate_facts = {
                    "batch_index": batch_index,
                    "batch_size": len(batch_ids),
                    "object_fact_sha256": object_fact_sha256,
                    "signature": signature,
                }
                candidates.append({
                    "candidate_id": candidate_id,
                    "task_id": task_id,
                    "kind": "inventory_batch",
                    "allowed_class": "CAD_INVENTORY_BATCH",
                    "evidence_ids": batch_ids,
                    "facts_sha256": _digest(candidate_facts),
                    "facts": candidate_facts,
                })
                grouped[task_id] = {
                    "task_id": task_id,
                    "kind": "inventory_review",
                    "candidate_ids": [candidate_id],
                    "evidence_ids": set(batch_ids),
                }
                inventory_task_count += 1

        feature_columns = _require_table(connection, "feature_candidates")
        required_features = {
            "feature_key", "feature_class", "geometry_kind", "geometry_role",
            "source_entity_key", "source_handle", "source_layer", "attributes",
            "display_label", "label_provenance",
        }
        if required_features - feature_columns:
            raise CurationError("feature_candidates schema is incomplete")
        feature_source_by_key: dict[str, str] = {}
        feature_query = """
            SELECT feature_key, feature_class, geometry_kind, geometry_role,
                   source_entity_key, source_handle, source_layer, attributes,
                   display_label, label_provenance
            FROM feature_candidates ORDER BY feature_key
        """
        for row in connection.execute(feature_query):
            candidate_id = str(row["feature_key"] or "")
            source_id = str(row["source_entity_key"] or "")
            if not candidate_id or source_id not in object_ids:
                raise CurationError("feature_candidates contains an unbound ID")
            task_id = f"feature:{source_id}"
            facts = {
                "feature_class": str(row["feature_class"] or ""),
                "geometry_kind": str(row["geometry_kind"] or ""),
                "geometry_role": str(row["geometry_role"] or ""),
                "source_handle": str(row["source_handle"] or ""),
                "source_layer": str(row["source_layer"] or ""),
                "attributes": _safe_model_context(
                    _json_object(row["attributes"], "feature_candidates.attributes")
                ),
                "display_label": str(row["display_label"] or ""),
                "label_provenance": str(row["label_provenance"] or ""),
            }
            candidates.append({
                "candidate_id": candidate_id,
                "task_id": task_id,
                "kind": "feature_candidate",
                "allowed_class": facts["feature_class"],
                "evidence_ids": [source_id],
                "facts_sha256": _digest(facts),
                "facts": facts,
            })
            feature_source_by_key[candidate_id] = source_id
            group = grouped.setdefault(task_id, {
                "task_id": task_id,
                "kind": "feature_review",
                "candidate_ids": [],
                "evidence_ids": set(),
            })
            group["candidate_ids"].append(candidate_id)
            group["evidence_ids"].add(source_id)

        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "annotation_assignment_candidates" in tables:
            assignment_columns = _table_columns(connection, "annotation_assignment_candidates")
            required_assignments = {
                "annotation_key", "target_class", "target_key", "target_handle",
                "distance_native_m", "selected", "status", "rule_id",
            }
            if required_assignments - assignment_columns:
                raise CurationError("annotation_assignment_candidates schema is incomplete")
            select_assignment = [
                "annotation_key", "target_class", "target_key", "target_handle",
                "distance_native_m", "selected", "status", "rule_id", "family_id",
            ]
            assignment_sql = ", ".join(
                _column_or_null(assignment_columns, name) for name in select_assignment
            )
            rows = list(connection.execute(
                f'SELECT {assignment_sql} FROM annotation_assignment_candidates '
                "ORDER BY annotation_key, target_key, distance_native_m, status"
            ))
            for row in rows:
                annotation_id = str(row["annotation_key"] or "")
                target_feature_id = str(row["target_key"] or "")
                target_source_id = feature_source_by_key.get(target_feature_id)
                if annotation_id not in object_ids or target_source_id not in object_ids:
                    raise CurationError("annotation assignment contains an unbound evidence ID")
                facts = {
                    "annotation_key": annotation_id,
                    "target_feature_key": target_feature_id,
                    "target_handle": str(row["target_handle"] or ""),
                    "target_class": str(row["target_class"] or ""),
                    "distance_native_m": _require_number_or_none(
                        row["distance_native_m"],
                        "annotation_assignment_candidates.distance_native_m",
                    ),
                    "distance_status": "available" if row["distance_native_m"] is not None else "unavailable",
                    "deterministically_selected": bool(row["selected"]),
                    "status": str(row["status"] or ""),
                    "rule_id": str(row["rule_id"] or ""),
                    "family_id": str(row["family_id"] or ""),
                }
                candidate_binding = {"facts": facts}
                candidate_id = f"annotation-assignment:{_digest(candidate_binding)}"
                task_id = f"annotation:{annotation_id}"
                evidence_for_candidate = sorted({annotation_id, target_source_id})
                candidates.append({
                    "candidate_id": candidate_id,
                    "task_id": task_id,
                    "kind": "annotation_assignment",
                    "allowed_class": facts["target_class"],
                    "evidence_ids": evidence_for_candidate,
                    "facts_sha256": _digest(facts),
                    "facts": facts,
                })
                group = grouped.setdefault(task_id, {
                    "task_id": task_id,
                    "kind": "annotation_assignment_review",
                    "candidate_ids": [],
                    "evidence_ids": set(),
                })
                group["candidate_ids"].append(candidate_id)
                group["evidence_ids"].update(evidence_for_candidate)

        tasks: list[dict[str, Any]] = []
        for task_id, group in sorted(grouped.items()):
            candidate_ids = sorted(group["candidate_ids"])
            tasks.append({
                "task_id": task_id,
                "kind": group["kind"],
                "candidate_ids": candidate_ids,
                "evidence_ids": sorted(group["evidence_ids"]),
                "allowed_actions": ["select", "abstain"] if len(candidate_ids) == 1 else ["select", "rank", "abstain"],
            })
        candidates.sort(key=lambda item: item["candidate_id"])
    finally:
        connection.close()

    evidence_sha_after = _file_sha256(evidence_file)
    if evidence_sha_before != evidence_sha_after:
        raise CurationError("Evidence changed while review bundle was being built")
    payload: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source": {"dwg_name": dwg_file.name, "dwg_sha256": source_sha},
        "evidence": {"file_name": evidence_file.name, "sha256": evidence_sha_before},
        "policy": {
            "stage": "curate_after_deterministic_readcad_and_candidate_evidence",
            "allowed_actions": ["select", "rank", "abstain"],
            "forbidden_mutations": _FORBIDDEN_MUTATIONS,
            "conversion_import_allowed": False,
            "coordinate_payloads_visible": False,
            "immutable_measurements_visible": True,
        },
        "coverage": {
            "object_count": len(objects),
            "inventory_task_count": inventory_task_count,
            "inventory_covered_objects": len(objects),
            "untasked_objects": 0,
            "multiply_tasked_objects": 0,
        },
        "objects": objects,
        "candidates": candidates,
        "tasks": tasks,
    }
    payload["bundle_sha256"] = _digest(payload)
    return validate_review_bundle(payload)


@dataclass(frozen=True)
class CurationDecision:
    task_id: str
    action: str
    candidate_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    confidence: float
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "candidate_ids": list(self.candidate_ids),
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class CurationProposal:
    schema_version: str
    bundle_sha256: str
    source_sha256: str
    evidence_sha256: str
    decisions: tuple[CurationDecision, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bundle_sha256": self.bundle_sha256,
            "source_sha256": self.source_sha256,
            "evidence_sha256": self.evidence_sha256,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


def _scan_forbidden_proposal_keys(value: Any, path: str = "$" ) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_PROPOSAL_KEYS:
                raise CurationError(f"Forbidden CAD/GIS fact field in proposal: {path}.{key}")
            _scan_forbidden_proposal_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_forbidden_proposal_keys(item, f"{path}[{index}]")


def validate_proposal(
    payload: Mapping[str, Any], bundle: ReviewBundle,
) -> CurationProposal:
    """Reject all output except bound select/rank/abstain decisions."""

    if not isinstance(payload, dict):
        raise CurationError("Proposal must be a JSON object")
    _scan_forbidden_proposal_keys(payload)
    _expect_keys(
        payload,
        {"bundle_sha256", "decisions", "evidence_sha256", "schema_version", "source_sha256"},
        "$",
    )
    if payload["schema_version"] != PROPOSAL_SCHEMA_VERSION:
        raise CurationError("Unsupported proposal schema_version")
    bindings = {
        "bundle_sha256": bundle.bundle_sha256,
        "source_sha256": bundle.source_sha256,
        "evidence_sha256": bundle.evidence_sha256,
    }
    for name, expected in bindings.items():
        actual = _require_sha256(payload[name], f"$.{name}")
        if actual != expected:
            raise CurationError(f"Proposal {name} does not match the review bundle")
    raw_decisions = payload["decisions"]
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise CurationError("Proposal decisions must be a non-empty array")
    if len(raw_decisions) > len(bundle.tasks):
        raise CurationError("Proposal has more decisions than review tasks")
    task_map = {task["task_id"]: task for task in bundle.tasks}
    decisions: list[CurationDecision] = []
    seen_tasks: set[str] = set()
    for index, value in enumerate(raw_decisions):
        path = f"$.decisions[{index}]"
        if not isinstance(value, dict):
            raise CurationError(f"{path} must be an object")
        _expect_keys(
            value,
            {"action", "candidate_ids", "confidence", "evidence_ids", "rationale", "task_id"},
            path,
        )
        task_id = _require_string(value["task_id"], f"{path}.task_id", allow_empty=False)
        if task_id not in task_map:
            raise CurationError(f"{path}.task_id is not in the review bundle")
        if task_id in seen_tasks:
            raise CurationError(f"Proposal repeats task_id {task_id!r}")
        seen_tasks.add(task_id)
        task = task_map[task_id]
        action = _require_string(value["action"], f"{path}.action", allow_empty=False)
        if action not in task["allowed_actions"]:
            raise CurationError(f"{path}.action is not allowed for this task")
        candidate_ids = _require_string_list(value["candidate_ids"], f"{path}.candidate_ids")
        evidence_ids = _require_string_list(value["evidence_ids"], f"{path}.evidence_ids")
        if set(candidate_ids) - set(task["candidate_ids"]):
            raise CurationError(f"{path} contains a candidate ID not allowed for this task")
        if set(evidence_ids) - set(task["evidence_ids"]):
            raise CurationError(f"{path} contains an evidence ID not allowed for this task")
        if task["kind"] == "inventory_review" and set(evidence_ids) != set(task["evidence_ids"]):
            raise CurationError(
                f"{path} inventory review must acknowledge every object in its batch"
            )
        if action == "select" and len(candidate_ids) != 1:
            raise CurationError(f"{path} select requires exactly one candidate ID")
        if action == "rank" and len(candidate_ids) < 2:
            raise CurationError(f"{path} rank requires at least two candidate IDs")
        if action == "abstain" and candidate_ids:
            raise CurationError(f"{path} abstain must not contain candidate IDs")
        if action in {"select", "rank"} and not evidence_ids:
            raise CurationError(f"{path} {action} requires at least one existing evidence ID")
        confidence = _require_number_or_none(value["confidence"], f"{path}.confidence")
        if confidence is None or not 0.0 <= confidence <= 1.0:
            raise CurationError(f"{path}.confidence must be between 0 and 1")
        rationale = _require_string(value["rationale"], f"{path}.rationale", allow_empty=False)
        if len(rationale) > 500:
            raise CurationError(f"{path}.rationale exceeds 500 characters")
        if any(character in rationale for character in "{}[]") or _FORBIDDEN_RATIONALE_FACT_RE.search(rationale):
            raise CurationError(f"{path}.rationale must not embed CAD/GIS fact fields or values")
        decisions.append(CurationDecision(
            task_id=task_id,
            action=action,
            candidate_ids=candidate_ids,
            evidence_ids=evidence_ids,
            confidence=confidence,
            rationale=rationale,
        ))
    return CurationProposal(
        schema_version=PROPOSAL_SCHEMA_VERSION,
        bundle_sha256=bundle.bundle_sha256,
        source_sha256=bundle.source_sha256,
        evidence_sha256=bundle.evidence_sha256,
        decisions=tuple(decisions),
    )


def load_and_validate_proposal(
    proposal_path: str | Path, bundle: ReviewBundle,
) -> CurationProposal:
    return validate_proposal(_read_json(proposal_path, max_bytes=4 * 1024 * 1024), bundle)


def proposal_json_schema(bundle: ReviewBundle, task_id: str) -> dict[str, Any]:
    """Return a strict provider schema bound to one existing review task."""

    task_map = {task["task_id"]: task for task in bundle.tasks}
    if task_id not in task_map:
        raise CurationError(f"Unknown review task ID: {task_id}")
    task = task_map[task_id]
    decision = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "task_id", "action", "candidate_ids", "evidence_ids", "confidence", "rationale",
        ],
        "properties": {
            "task_id": {"type": "string", "enum": [task_id]},
            "action": {"type": "string", "enum": task["allowed_actions"]},
            "candidate_ids": {
                "type": "array",
                "items": {"type": "string", "enum": task["candidate_ids"]},
                "maxItems": len(task["candidate_ids"]),
            },
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string", "enum": task["evidence_ids"]},
                "minItems": (
                    len(task["evidence_ids"])
                    if task["kind"] == "inventory_review" else 0
                ),
                "maxItems": len(task["evidence_ids"]),
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "bundle_sha256", "source_sha256", "evidence_sha256", "decisions",
        ],
        "properties": {
            "schema_version": {"type": "string", "enum": [PROPOSAL_SCHEMA_VERSION]},
            "bundle_sha256": {"type": "string", "enum": [bundle.bundle_sha256]},
            "source_sha256": {"type": "string", "enum": [bundle.source_sha256]},
            "evidence_sha256": {"type": "string", "enum": [bundle.evidence_sha256]},
            "decisions": {"type": "array", "minItems": 1, "maxItems": 1, "items": decision},
        },
    }


def create_audit(
    proposal: CurationProposal,
    *,
    implementation: Mapping[str, Any],
    channel: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    implementation_value = dict(implementation)
    _expect_keys(
        implementation_value,
        {"schema_version", "scope", "scope_version", "files", "sha256"},
        "$.implementation",
    )
    if implementation_value["scope"] != "offline-curation":
        raise CurationError("Audit implementation scope must be offline-curation")
    if (
        isinstance(implementation_value["scope_version"], bool)
        or not isinstance(implementation_value["scope_version"], int)
        or implementation_value["scope_version"] < 1
    ):
        raise CurationError("Audit implementation scope_version must be positive")
    files = implementation_value["files"]
    if not isinstance(files, list) or not files:
        raise CurationError("Audit implementation files must be a non-empty array")
    for index, item in enumerate(files):
        if not isinstance(item, Mapping):
            raise CurationError(f"Audit implementation file {index} must be an object")
        _expect_keys(dict(item), {"path", "sha256", "size_bytes"}, f"$.implementation.files[{index}]")
        _require_string(item["path"], f"$.implementation.files[{index}].path", allow_empty=False)
        _require_sha256(item["sha256"], f"$.implementation.files[{index}].sha256")
        if (
            isinstance(item["size_bytes"], bool)
            or not isinstance(item["size_bytes"], int)
            or item["size_bytes"] < 0
        ):
            raise CurationError(
                f"$.implementation.files[{index}].size_bytes must be non-negative"
            )
    recorded_digest = _require_sha256(
        implementation_value["sha256"], "$.implementation.sha256",
    )
    descriptor = {
        key: value for key, value in implementation_value.items() if key != "sha256"
    }
    if _digest(descriptor) != recorded_digest:
        raise CurationError("Audit implementation hash mismatch")
    channel_value = {
        "kind": "manual",
        "provider": "",
        "protocol": "",
        "model": "",
        "capability": "",
        "base_url_profile_sha256": "",
        "task_id": "",
        "request_sha256": "",
        "response_sha256": "",
        "response_id": "",
    }
    if channel:
        unknown = set(channel) - set(channel_value)
        if unknown:
            raise CurationError(f"Unknown audit channel fields: {sorted(unknown)}")
        channel_value.update(channel)
    proposal_value = proposal.to_dict()
    payload: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "status": "validated_proposal_only",
        "bundle_sha256": proposal.bundle_sha256,
        "source_sha256": proposal.source_sha256,
        "evidence_sha256": proposal.evidence_sha256,
        "proposal_sha256": _digest(proposal_value),
        "proposal": proposal_value,
        "implementation": implementation_value,
        "channel": channel_value,
        "validation": {
            "all_candidate_ids_bound": True,
            "all_evidence_ids_bound": True,
            "forbidden_fact_fields_absent": True,
            "conversion_import_allowed": False,
        },
    }
    payload["audit_sha256"] = _digest(payload)
    return payload
