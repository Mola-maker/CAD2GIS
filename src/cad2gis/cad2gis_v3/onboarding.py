"""AI-assisted, source-bound onboarding for previously unseen CAD drawings.

The model selects only observed CAD layer/block identifiers and one
deterministically derived CRS/unit candidate.  Compilation, census derivation,
coverage policy, hashing, and conversion admission remain deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyproj import CRS

from .config import MappingRegistry, SourceProfile
from .curation_providers import (
    OpenAICompatibleProvider,
    ReviewProvider,
    ReviewRequest,
    ReviewResponse,
    load_provider_config,
)
from .ingest import ingest
from .plan_domain import build_plan_domain
from .project_profile import (
    _atomic_write_json,
    _read_json,
    bootstrap_project,
    inventory_sha256,
    validate_project,
)
from .semantics import classify_entities
from .units import resolve_insunits


ONBOARDING_BUNDLE_SCHEMA = "cad2gis.ai_onboarding_bundle.v1"
ONBOARDING_PROPOSAL_SCHEMA = "cad2gis.ai_onboarding_proposal.v1"
_FEATURE_CLASSES = ("BOITE", "PTECH", "SITE")
_CGEOCS_CRS = {
    "UTM84-49S": "EPSG:32749",
    "INDONESIAN1974.UTM-46N": "EPSG:23846",
    "WGS84.PSEUDOMERCATOR": "EPSG:3857",
}


class OnboardingError(ValueError):
    """An AI onboarding proposal escaped its source-bound evidence contract."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _read_project(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    profile = _read_json(root / "config" / "source_profile.json")
    registry = _read_json(root / "config" / "mapping_registry.json")
    inventory = _read_json(root / "review" / "source_inventory.json")
    stored_hash = str(inventory.get("inventory_sha256", ""))
    actual_hash = inventory_sha256(inventory)
    if stored_hash != actual_hash:
        raise OnboardingError(
            "Source inventory hash is stale; re-bootstrap this project pack"
        )
    return profile, registry, inventory


def _crs_candidates(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    drawing = profile.get("drawing")
    if not isinstance(drawing, Mapping):
        return []
    cgeocs = str(drawing.get("dwg_cgeocs") or "").strip()
    raw_insunits = drawing.get("dwg_insunits")
    if not cgeocs or isinstance(raw_insunits, bool) or not isinstance(raw_insunits, int):
        return []
    crs_token = _CGEOCS_CRS.get(cgeocs.upper())
    if crs_token is None:
        return []
    unit = resolve_insunits(raw_insunits)
    crs = CRS.from_user_input(crs_token)
    if not crs.is_projected:
        return []
    axes = tuple(crs.axis_info[:2])
    if len(axes) != 2:
        return []
    axis_scales = tuple(float(axis.unit_conversion_factor) for axis in axes)
    if (
        any(scale <= 0.0 for scale in axis_scales)
        or abs(axis_scales[0] - axis_scales[1]) > 1e-12
    ):
        return []
    axis_names = tuple(str(axis.unit_name or "").strip() for axis in axes)
    if not axis_names[0] or axis_names[0].casefold() != axis_names[1].casefold():
        return []
    authority = crs.to_authority()
    canonical_crs = (
        f"{authority[0]}:{authority[1]}" if authority else crs.to_string()
    )
    candidate = {
        "candidate_id": (
            f"CAD-METADATA:{cgeocs.upper()}:INSUNITS-{raw_insunits}"
        ),
        "source_crs": canonical_crs,
        # Preserve the drawing's local projected CRS. QGIS can reproject it
        # on the fly; no datum change is invented during onboarding.
        "target_crs": canonical_crs,
        "drawing_units": axis_names[0],
        "source_coordinate_scale_to_m": axis_scales[0],
        "source_coordinate_scale_reviewed": True,
        "evidence": {
            "dwg_cgeocs": cgeocs,
            "dwg_insunits": raw_insunits,
            "dwg_insunits_name": unit.name,
            "dwg_insunits_role": "block_insertion_scale_hint",
            "coordinate_unit_basis": "source_crs_axis",
            "source_crs_axis_unit": axis_names[0],
            "source_crs_axis_metres_per_unit": axis_scales[0],
            "authority": "DWG_DIRECT",
        },
    }
    candidate["candidate_sha256"] = _canonical_sha256(candidate)
    return [candidate]


def _role_suggestions(
    layers: Mapping[str, Any],
    insert_layers: Mapping[str, Any],
    insert_instances: Any,
    named_blocks: Mapping[str, Any],
) -> dict[str, Any]:
    # OVERFIT-RISK: the layer-name heuristics below (``HOME NUMBER``,
    # ``HP(...)``, ``SPAN``/``DIMENSI``, ``SLING WIRE``, ``FAT AREA``,
    # ``BOUNDARY FAT``, ``ZPM``) were observed on the four Indonesian
    # baseline drawings.  They are suggestions for the AI proposal, not
    # conversion gates; validation-set drawings must still pass through
    # their own source inventory and deterministic census.
    result: dict[str, Any] = {
        "route_layers": [],
        "homepass_layers": [],
        "span_dimension_layers": [],
        "sling_wire_layers": [],
        "zpm_boundary_layers": [],
        "boite_insert_layers": [],
        "ptech_insert_layers": [],
        "site_insert_layers": [],
        "block_families": {
            feature_class: [] for feature_class in _FEATURE_CLASSES
        },
    }
    for layer in layers:
        upper = str(layer).upper()
        if (
            re.search(r"\bFO\b.*\b(?:CORE|CABLE)\b", upper)
            and "LABEL" not in upper
        ):
            result["route_layers"].append(str(layer))
        if (
            "HOME NUMBER" in upper
            or "HOMENUMBER" in upper
            or "HOME PASS" in upper
            or "HOMEPASS" in upper
            or re.fullmatch(r"HP(?:\s*\([A-Z]\)|\s+REDUCE)?", upper)
            or upper.endswith("REDUCE")
        ):
            result["homepass_layers"].append(str(layer))
        if "SPAN" in upper or "DIMENSI" in upper:
            result["span_dimension_layers"].append(str(layer))
        if "SLING WIRE" in upper:
            result["sling_wire_layers"].append(str(layer))
        if (
            "FAT AREA" in upper
            or "BOUNDARY FAT" in upper
            or re.search(r"(?:^|[\s_-])ZPM(?:$|[\s_-])", upper)
        ):
            result["zpm_boundary_layers"].append(str(layer))
    class_by_layer: dict[str, Counter[str]] = defaultdict(Counter)
    if isinstance(insert_instances, list):
        for item in insert_instances:
            if not isinstance(item, Mapping):
                continue
            layer = str(item.get("layer") or "").strip()
            feature_class = _asset_class_hint(
                str(item.get("block_name") or "")
            )
            if layer and feature_class:
                class_by_layer[layer][feature_class] += 1
    for block in named_blocks:
        feature_class = _asset_class_hint(str(block))
        if feature_class:
            result["block_families"][feature_class].append(str(block))
    for layer in insert_layers:
        upper = str(layer).upper()
        observed = class_by_layer.get(str(layer), Counter())
        feature_class = ""
        if observed:
            ranked = observed.most_common()
            total_instances = int(insert_layers.get(layer, 0) or 0)
            dominant_share = (
                ranked[0][1] / total_instances if total_instances > 0 else 0.0
            )
            if (
                dominant_share >= 0.6
                and (len(ranked) == 1 or ranked[0][1] > ranked[1][1])
            ):
                feature_class = ranked[0][0]
        if not feature_class:
            feature_class = _asset_class_hint(upper)
        if feature_class:
            result[f"{feature_class.casefold()}_insert_layers"].append(
                str(layer)
            )
    for key, values in result.items():
        if key == "block_families":
            for feature_class, blocks in values.items():
                values[feature_class] = sorted(
                    set(blocks),
                    key=str.casefold,
                )
        else:
            result[key] = sorted(set(values), key=str.casefold)
    return result


def _asset_class_hint(value: str) -> str:
    upper = value.strip().upper()
    if not upper or upper.startswith("*"):
        return ""
    if "POLE" in upper:
        return "PTECH"
    if any(token in upper for token in ("FAT", "CLOSURE", "OTB")):
        return "BOITE"
    if re.search(r"(?:^|[\s_-])(?:FDT|HUB|OLT)(?:$|[\s_-])", upper):
        return "SITE"
    return ""


def _text_samples(
    carriers: Any,
    *,
    limit_per_layer: int = 8,
    max_layers: int = 80,
) -> dict[str, list[dict[str, Any]]]:
    """Collect drawing-space text samples with colour, scaling to prefix families.

    Output shape: ``{layer: [{"text": ..., "aci_color": N}, ...]}``.  Only
    Model/plan carriers are sampled (block-definition copies are skipped) so
    validation mirrors the entities ``classify_entities`` actually consumes.
    When a layer's samples share a common prefix (e.g.
    ``EXT.MR.MF.LBB.S02.P``) the sample limit is extended so every prefix
    family is represented, mirroring the runtime requirement that each label
    family needs sufficient evidence.
    """
    if not isinstance(carriers, list):
        return {}
    raw_by_layer: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for raw in carriers:
        if not isinstance(raw, Mapping):
            continue
        text = str(raw.get("text") or "").strip()
        layer = str(raw.get("layer") or "").strip()
        if not text or not layer:
            continue
        # Family validation must mirror the runtime drawing-space view:
        # ``classify_entities`` only sees Model/plan entities, while
        # block-definition ATTRIB/TEXT copies repeat the same labels as
        # template documentation.  Sampling those copies makes a physical
        # layer like FAT look like prose (``\\pxqc;FAT A014``, ``A07``
        # template text) and hides its real ``KLDYA.011.C01`` family.
        layout = str(raw.get("layout") or "").strip()
        if layout.startswith("BLOCKDEF"):
            continue
        aci_color = raw.get("aci_color")
        raw_by_layer[layer].append((text[:160], aci_color))

    def _dedupe(values: list[tuple[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, Any]] = set()
        result: list[dict[str, Any]] = []
        for text, color in values:
            key = (text, color)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "text": text,
                **({"aci_color": int(color)} if isinstance(color, int) else {}),
            })
        return result

    grouped: dict[str, list[dict[str, Any]]] = {}
    for layer, values in raw_by_layer.items():
        samples = _dedupe(values)
        if not samples:
            continue
        if len(samples) <= limit_per_layer:
            grouped[layer] = samples
            continue
        # Prefix-family aware scaling: group by token width, then by the
        # longest common token prefix (split on non-alphanumeric separators),
        # and take up to ``limit_per_layer`` samples per prefix family.
        # Multi-field asset identifiers are sampled before single-token
        # stubs (``A07`` beside ``KLDYA.011.C01``) so the stub families
        # cannot consume the whole layer budget and starve the full
        # identifiers out of the derivation evidence.
        families: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for sample in samples:
            tokens = re.split(r"[^A-Za-z0-9]+", sample["text"])
            width = len(tokens)
            prefix = (
                ".".join(tokens[:-1]) if width > 1 else "\x00single-token"
            )
            families[(width, prefix)].append(sample)
        expanded: list[dict[str, Any]] = []
        for (width, prefix) in sorted(
            families, key=lambda item: (-item[0], item[1])
        ):
            expanded.extend(families[(width, prefix)][:limit_per_layer])
        grouped[layer] = expanded[: limit_per_layer * 4]
    ranked = sorted(
        grouped,
        key=lambda layer: (-len(grouped[layer]), layer.casefold()),
    )[:max_layers]
    return {layer: grouped[layer] for layer in ranked}


def _insert_census(instances: Any) -> dict[str, Any]:
    by_layer: Counter[str] = Counter()
    named_blocks: Counter[str] = Counter()
    attribute_keys: dict[str, set[str]] = defaultdict(set)
    if not isinstance(instances, list):
        return {
            "by_layer": {},
            "named_blocks": {},
            "attribute_keys_by_layer": {},
        }
    for item in instances:
        if not isinstance(item, Mapping):
            continue
        layer = str(item.get("layer") or "").strip()
        block = str(item.get("block_name") or "").strip()
        if layer:
            by_layer[layer] += 1
        if block and not block.startswith("*"):
            named_blocks[block] += 1
        attributes = item.get("attributes")
        if layer and isinstance(attributes, Mapping):
            attribute_keys[layer].update(
                str(key).strip()
                for key in attributes
                if str(key).strip()
            )
    return {
        "by_layer": dict(sorted(by_layer.items(), key=lambda item: item[0].casefold())),
        "named_blocks": dict(
            sorted(named_blocks.items(), key=lambda item: item[0].casefold())
        ),
        "attribute_keys_by_layer": {
            layer: sorted(keys, key=str.casefold)
            for layer, keys in sorted(
                attribute_keys.items(),
                key=lambda item: item[0].casefold(),
            )
            if keys
        },
    }


def prepare_onboarding_bundle(project_dir: str | Path) -> dict[str, Any]:
    """Build compact model context from one immutable source inventory."""

    root = Path(project_dir).expanduser().resolve()
    profile, _, inventory = _read_project(root)
    layers = {
        str(key): int(value)
        for key, value in inventory.get("layers", {}).items()
    }
    blocks = {
        str(key): int(value)
        for key, value in inventory.get("block_names", {}).items()
        if not str(key).startswith("*")
    }
    insert_census = _insert_census(inventory.get("block_instances"))
    bundle: dict[str, Any] = {
        "schema_version": ONBOARDING_BUNDLE_SCHEMA,
        "project_id": profile.get("project_id"),
        "source": dict(inventory.get("source", {})),
        "inventory_sha256": inventory.get("inventory_sha256"),
        "document_metadata": dict(inventory.get("document_metadata", {})),
        "counts": dict(inventory.get("counts", {})),
        "layers": dict(sorted(layers.items(), key=lambda item: item[0].casefold())),
        "named_blocks": dict(
            sorted(blocks.items(), key=lambda item: item[0].casefold())
        ),
        "insert_instances": insert_census,
        "text_samples_by_layer": _text_samples(
            inventory.get("annotation_carriers")
        ),
        "crs_candidates": _crs_candidates(profile),
        "deterministic_role_suggestions": _role_suggestions(
            layers,
            insert_census["by_layer"],
            inventory.get("block_instances"),
            blocks,
        ),
        "authority": {
            "model_may_select_observed_identifiers_only": True,
            "model_may_not_supply_coordinates_lengths_crs_or_gcp": True,
            "unmapped_entities_are_preserved_as_abstentions": True,
        },
    }
    bundle["bundle_sha256"] = _canonical_sha256(bundle)
    bundle["proposal_schema"] = onboarding_proposal_json_schema(bundle)
    return bundle


def onboarding_proposal_json_schema(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return a strict task-bound schema for one onboarding bundle."""

    layers = sorted(str(item) for item in bundle.get("layers", {}))
    blocks = sorted(str(item) for item in bundle.get("named_blocks", {}))
    candidate_ids = [
        str(item["candidate_id"])
        for item in bundle.get("crs_candidates", ())
        if isinstance(item, Mapping) and item.get("candidate_id")
    ]
    layer_array = {
        "type": "array",
        "items": {"type": "string", "enum": layers},
        "uniqueItems": True,
    }
    block_array = {
        "type": "array",
        "items": {"type": "string", "enum": blocks},
        "uniqueItems": True,
    }
    family_object = {
        "type": "object",
        "properties": {
            feature_class: block_array for feature_class in _FEATURE_CLASSES
        },
        "required": list(_FEATURE_CLASSES),
        "additionalProperties": False,
    }
    layer_family_object = {
        "type": "object",
        "properties": {
            feature_class: layer_array for feature_class in _FEATURE_CLASSES
        },
        "required": list(_FEATURE_CLASSES),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "schema_version": {"const": ONBOARDING_PROPOSAL_SCHEMA},
            "bundle_sha256": {"const": bundle.get("bundle_sha256")},
            "source_sha256": {
                "const": bundle.get("source", {}).get("sha256")
            },
            "inventory_sha256": {"const": bundle.get("inventory_sha256")},
            "crs_candidate_id": {
                "type": "string",
                "enum": candidate_ids,
            },
            "route_layers": layer_array,
            "homepass_layers": layer_array,
            "span_dimension_layers": layer_array,
            "sling_wire_layers": layer_array,
            "zpm_boundary_layers": layer_array,
            "block_families": family_object,
            "insert_layer_families": layer_family_object,
            "confidence": {
                "type": "object",
                "properties": {
                    "semantics": {"type": "number", "minimum": 0, "maximum": 1},
                    "crs": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["semantics", "crs"],
                "additionalProperties": False,
            },
            "rationale": {"type": "string", "minLength": 1, "maxLength": 4000},
            "annotation_families": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "family_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                        "text_pattern": {"type": "string", "minLength": 1},
                        "target_class": {"type": "string", "enum": list(_FEATURE_CLASSES)},
                        "max_distance_native_m": {"type": "number", "minimum": 0.1, "maximum": 100.0},
                        "aci_color": {"type": "integer", "minimum": 1, "maximum": 255},
                        "source_layer": {"type": "string", "minLength": 1},
                    },
                    "required": ["family_id", "text_pattern", "target_class", "max_distance_native_m"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "schema_version",
            "bundle_sha256",
            "source_sha256",
            "inventory_sha256",
            "crs_candidate_id",
            "route_layers",
            "homepass_layers",
            "span_dimension_layers",
            "sling_wire_layers",
            "zpm_boundary_layers",
            "block_families",
            "insert_layer_families",
            "confidence",
            "rationale",
        ],
        "additionalProperties": False,
    }


def _string_set(value: Any, name: str, allowed: set[str]) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or item not in allowed for item in value
    ):
        raise OnboardingError(f"{name} must select observed identifiers only")
    if len(value) != len(set(value)):
        raise OnboardingError(f"{name} contains duplicate identifiers")
    return sorted(value, key=str.casefold)


def validate_onboarding_proposal(
    bundle: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a model proposal without trusting provider JSON-schema support."""

    schema = onboarding_proposal_json_schema(bundle)
    required = set(schema["required"])
    allowed = required | set(schema["properties"])
    if required - set(proposal):
        raise OnboardingError(
            "Invalid onboarding proposal keys; "
            f"missing={sorted(required - set(proposal))}"
        )
    # Strip unknown extra fields — json_object providers may add noise.
    proposal = {k: v for k, v in proposal.items() if k in allowed}
    exact = {
        "schema_version": ONBOARDING_PROPOSAL_SCHEMA,
        "bundle_sha256": bundle.get("bundle_sha256"),
        "source_sha256": bundle.get("source", {}).get("sha256"),
        "inventory_sha256": bundle.get("inventory_sha256"),
    }
    for key, value in exact.items():
        if proposal.get(key) != value:
            raise OnboardingError(f"Onboarding proposal {key} binding mismatch")
    candidates = {
        str(item["candidate_id"]): dict(item)
        for item in bundle.get("crs_candidates", ())
        if isinstance(item, Mapping) and item.get("candidate_id")
    }
    candidate_id = str(proposal.get("crs_candidate_id", ""))
    if candidate_id not in candidates:
        raise OnboardingError("Proposal must select a deterministic CRS candidate")

    layers = set(str(item) for item in bundle.get("layers", {}))
    blocks = set(str(item) for item in bundle.get("named_blocks", {}))
    normalized: dict[str, Any] = {
        **exact,
        "crs_candidate_id": candidate_id,
    }
    for field in (
        "route_layers",
        "homepass_layers",
        "span_dimension_layers",
        "sling_wire_layers",
        "zpm_boundary_layers",
    ):
        normalized[field] = _string_set(proposal.get(field), field, layers)
    for field, allowed in (
        ("block_families", blocks),
        ("insert_layer_families", layers),
    ):
        raw = proposal.get(field)
        if not isinstance(raw, Mapping) or set(raw) != set(_FEATURE_CLASSES):
            raise OnboardingError(
                f"{field} must contain exactly {list(_FEATURE_CLASSES)}"
            )
        normalized[field] = {
            feature_class: _string_set(
                raw[feature_class],
                f"{field}.{feature_class}",
                allowed,
            )
            for feature_class in _FEATURE_CLASSES
        }
    assigned_insert_layers = [
        layer
        for values in normalized["insert_layer_families"].values()
        for layer in values
    ]
    if len(assigned_insert_layers) != len(set(assigned_insert_layers)):
        raise OnboardingError(
            "One INSERT layer cannot map to multiple feature classes"
        )
    confidence = proposal.get("confidence")
    if not isinstance(confidence, Mapping) or set(confidence) != {
        "semantics", "crs"
    }:
        raise OnboardingError("confidence must contain semantics and crs")
    normalized_confidence: dict[str, float] = {}
    for key in ("semantics", "crs"):
        raw = confidence[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise OnboardingError(f"confidence.{key} must be numeric")
        value = float(raw)
        if not 0.0 <= value <= 1.0:
            raise OnboardingError(f"confidence.{key} must be between 0 and 1")
        normalized_confidence[key] = value
    crs_threshold = float(
        os.environ.get("CAD2GIS_ONBOARDING_CRS_CONFIDENCE", "0.95")
    )
    semantics_threshold = float(
        os.environ.get("CAD2GIS_ONBOARDING_SEMANTICS_CONFIDENCE", "0.75")
    )
    if normalized_confidence["crs"] < crs_threshold:
        raise OnboardingError(
            f"CRS auto-acceptance requires confidence >= {crs_threshold}"
        )
    if normalized_confidence["semantics"] < semantics_threshold:
        raise OnboardingError(
            f"Semantic auto-acceptance requires confidence >= {semantics_threshold}"
        )
    rationale = proposal.get("rationale")
    if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 4000:
        raise OnboardingError("rationale must be 1-4000 characters")
    normalized["confidence"] = normalized_confidence
    normalized["rationale"] = rationale.strip()
    raw_annotation_families = proposal.get("annotation_families")
    if isinstance(raw_annotation_families, list):
        families = []
        for entry in raw_annotation_families:
            if not isinstance(entry, dict):
                continue
            fid = str(entry.get("family_id", "")).strip()
            pat = str(entry.get("text_pattern", "")).strip()
            tc = str(entry.get("target_class", "")).strip()
            dist = entry.get("max_distance_native_m")
            if not fid or not pat or tc not in _FEATURE_CLASSES:
                continue
            if isinstance(dist, bool) or not isinstance(dist, (int, float)):
                continue
            dist = float(dist)
            if not 0.1 <= dist <= 100.0:
                continue
            family: dict[str, Any] = {
                "family_id": fid,
                "text_pattern": pat,
                "target_class": tc,
                "max_distance_native_m": dist,
            }
            raw_color = entry.get("aci_color")
            if isinstance(raw_color, int) and not isinstance(raw_color, bool) and 1 <= raw_color <= 255:
                family["aci_color"] = raw_color
            raw_layer = entry.get("source_layer")
            if isinstance(raw_layer, str) and raw_layer.strip():
                family["source_layer"] = raw_layer.strip()
            families.append(family)
        normalized["annotation_families"] = families
    else:
        normalized["annotation_families"] = []
    normalized["proposal_sha256"] = _canonical_sha256(normalized)
    normalized["crs_candidate"] = candidates[candidate_id]
    return normalized


def _review_record(
    proposal: Mapping[str, Any],
    proposer: Mapping[str, Any],
) -> dict[str, str]:
    provider = str(proposer.get("provider") or "host-agent").strip()
    model = str(proposer.get("model") or "unknown-model").strip()
    reviewed_at = str(proposer.get("reviewed_at") or "").strip()
    if not reviewed_at:
        reviewed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "status": "auto_accepted",
        "reviewed_by": f"{provider}/{model}",
        "reviewed_at": reviewed_at,
        "provenance": (
            f"{ONBOARDING_PROPOSAL_SCHEMA}:"
            f"{proposal['proposal_sha256']}"
        ),
    }


def _exact_layer_regex(layers: list[str]) -> str:
    if not layers:
        return "(?!)"
    return "(?i)^(?:" + "|".join(re.escape(layer) for layer in layers) + ")$"


def _extend_route_regex(current: str, added_layers: list[str]) -> str:
    """Append exact-match alternatives for newly observed cable layers."""
    if not added_layers:
        return current
    alternatives = [re.escape(layer) for layer in sorted(set(added_layers))]
    if not current or current == "(?!)":
        return "(?i)^(?:" + "|".join(alternatives) + ")$"
    # Remove the trailing ")$" and insert new alternatives before it.
    if current.endswith(")$") and current.startswith("(?i)^(?:"):
        body = current[len("(?i)^(?:"):-len(")$")]
        return "(?i)^(?:" + body + "|" + "|".join(alternatives) + ")$"
    return current + "|" + "(?i)^(?:" + "|".join(alternatives) + ")$"


def _compile_annotation_families(
    proposal_families: list[dict[str, Any]],
    insert_layer_families: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Expand AI-onboarding annotation families to the full registry format.

    The model provides the core fields (family_id, text_pattern, target_class,
    max_distance_native_m, optional source_layer).  ``source_layer`` is the
    layer that carries the annotation text; ``target_layer_pattern`` must be
    derived from the reviewed INSERT-layer mapping for ``target_class``, never
    from the label layer.  ``require_same_layer`` is only true when the label
    layer is itself a reviewed target layer (e.g. ``EXISTING POLE`` text and
    INSERT share that layer).
    """
    from .family_validation import infer_target_class

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in proposal_families:
        fid = str(entry.get("family_id", "")).strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        raw_layer = str(entry.get("source_layer", "")).strip()
        proposed_class = str(entry.get("target_class", "")).strip()
        target_class = proposed_class
        class_repaired = False
        if raw_layer:
            # The label layer name is deterministic DWG evidence; the model's
            # target_class is a semantic guess.  Reconcile mismatches such as
            # ``POLE ID FDT 2 73`` → SITE (the FDT qualifier must not beat the
            # POLE device semantic) before any target-layer pattern is built.
            inferred_class = infer_target_class(raw_layer)
            if inferred_class in _FEATURE_CLASSES:
                class_repaired = inferred_class != proposed_class
                target_class = inferred_class
        text_pattern = str(entry.get("text_pattern", "")).strip()
        distance = float(entry.get("max_distance_native_m", 15.0))
        target_layers = [
            str(layer).strip()
            for layer in insert_layer_families.get(target_class, ())
            if str(layer).strip()
        ]
        target_pattern = _exact_layer_regex(target_layers)
        source_is_target = bool(
            raw_layer
            and any(
                raw_layer.casefold() == layer.casefold()
                for layer in target_layers
            )
        )
        family: dict[str, Any] = {
            "family_id": fid,
            "target_class": target_class,
            "text_pattern": text_pattern,
            "source_layer_pattern": (
                r"(?i)^" + re.escape(raw_layer) + r"$"
                if raw_layer
                else r"(?i).+"
            ),
            "target_layer_pattern": (
                r"(?i)^" + re.escape(raw_layer) + r"$"
                if source_is_target
                else target_pattern
            ),
            "require_same_layer": source_is_target,
            "max_distance_native_m": distance,
            "rule_id": f"AI-ANNOTATION-{fid.upper()}-001",
            "provenance": (
                f"DWG_DERIVED:AI-ANNOTATION-{fid.upper()}-001"
                + (
                    f"|LAYER-TARGET-CLASS-REPAIR:{target_class}"
                    if class_repaired else ""
                )
            ),
        }
        if entry.get("aci_color") is not None:
            family["aci_color"] = int(entry["aci_color"])
        result.append(family)
    return result


def _family_matches_samples(
    pattern: str, samples: Sequence[Any], *, min_fraction: float = 0.8,
) -> bool:
    """True when the pattern full-matches most of the layer's text samples."""
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    texts = [
        str(sample.get("text", "")).strip()
        for sample in samples
        if str(sample.get("text", "")).strip()
    ]
    if not texts:
        return False
    matched = sum(1 for text in texts if compiled.fullmatch(text))
    return matched / len(texts) >= min_fraction


def _repair_annotation_families(
    *,
    project_dir: str | Path,
    failing_families: list[dict[str, Any]],
    provider_id: str | None = None,
    provider: ReviewProvider | None = None,
    missing_only: bool = False,
) -> list[dict[str, Any]]:
    """Ask the model to fix structurally failing — or propose missing — families.

    With ``missing_only``, entries carry ``layer`` + ``samples`` and the model
    proposes brand-new families (target_class inferred from the layer); with
    ``missing_only=False`` the entries carry an existing family to repair.
    """
    selected_provider = provider
    if selected_provider is None:
        selected_provider = OpenAICompatibleProvider(
            load_provider_config(provider=provider_id)
        )
    if missing_only:
        system_prompt = (
            "You are proposing annotation label families for a CAD2GIS project. "
            "Each entry lists an asset label LAYER whose text samples have no "
            "family yet. Propose ONE family per layer: choose family_id "
            "(lowercase snake), text_pattern that preserves EVERY observed field "
            "token of the samples (separator/case variants tolerated), "
            "target_class (BOITE/PTECH/SITE — POLE layers→PTECH, FAT/closure "
            "layers→BOITE, FDT/splitter→SITE), and max_distance_native_m (15.0). "
            "Return a JSON object with a 'decisions' array. Respond in JSON."
        )
    else:
        # OVERFIT-RISK (prompt example): ``EXT, MR, MF, LBB, S\\d{2}, P\\d+``
        # is a baseline label hierarchy shown only as an example.  The model
        # must follow the actual samples in ``failing_families``, not copy
        # these tokens into new projects.
        system_prompt = (
            "You are repairing annotation label patterns for a CAD2GIS project. "
            "Each entry lists a family whose text_pattern failed structural "
            "validation, along with the samples it must cover. Rewrite the "
            "text_pattern so that it preserves EVERY observed field token of the "
            "label hierarchy (e.g. EXT, MR, MF, LBB, S\\d{2}, P\\d+) — never "
            "collapse middle fields with .*, never invent placeholder tokens. "
            "Separator variants (space vs dot) and case differences are "
            "tolerated. Return a JSON object with a 'decisions' array, one item "
            "per failing family: {\"family_id\", \"text_pattern\", "
            "\"target_class\", \"max_distance_native_m\"}. Keep the family_id "
            "and target_class unchanged. Respond in JSON."
        )
    request = ReviewRequest(
        system_prompt=system_prompt,
        context={
            "schema_version": (
                "cad2gis-family-propose-v1"
                if missing_only
                else "cad2gis-family-repair-v1"
            ),
            "failing_families": failing_families,
        },
        json_schema={
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "family_id": {"type": "string"},
                            "text_pattern": {"type": "string", "minLength": 1},
                            "target_class": {
                                "type": "string",
                                "enum": list(_FEATURE_CLASSES),
                            },
                            "max_distance_native_m": {
                                "type": "number", "minimum": 0.1, "maximum": 100.0,
                            },
                            "layer": {"type": "string"},
                        },
                        "required": ["family_id", "text_pattern", "target_class", "max_distance_native_m"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["decisions"],
            "additionalProperties": False,
        },
        schema_name="cad2gis_annotation_family_repair",
    )
    try:
        response: ReviewResponse = selected_provider.review(request)
        payload = json.loads(response.content)
        decisions = list(payload.get("decisions", ()))
    except Exception:
        return []
    repaired: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        fid = str(decision.get("family_id", "")).strip()
        pat = str(decision.get("text_pattern", "")).strip()
        tc = str(decision.get("target_class", "")).strip()
        dist = decision.get("max_distance_native_m")
        if not fid or not pat or tc not in _FEATURE_CLASSES:
            continue
        if isinstance(dist, bool) or not isinstance(dist, (int, float)):
            continue
        dist = float(dist)
        if not 0.1 <= dist <= 100.0:
            continue
        repaired_entry: dict[str, Any] = {
            "family_id": fid,
            "text_pattern": pat,
            "target_class": tc,
            "max_distance_native_m": dist,
        }
        raw_layer = decision.get("layer")
        if isinstance(raw_layer, str) and raw_layer.strip():
            repaired_entry["source_layer"] = raw_layer.strip()
        repaired.append(repaired_entry)
    return repaired


def _merge_reviewed_rules(
    existing: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep reviewed feature-class rules, filling deterministic defaults only
    for classes that have no reviewed rule yet.

    ``field_rules`` is two-level (``{class: {field: rule}}``) while
    ``display_label_rules`` is one-level (``{class: rule}``); the merge is
    structure-aware so onboarding never overwrites a reviewed decision and
    still applies the deterministic IMB defaults to fresh projects.
    """
    existing = existing or {}
    defaults = defaults or {}
    merged: dict[str, Any] = {}
    for feature_class, rules in existing.items():
        if isinstance(rules, Mapping) and "rule_id" in rules:
            merged[str(feature_class)] = dict(rules)
        else:
            merged[str(feature_class)] = {
                str(field): dict(rule)
                for field, rule in (rules or {}).items()
            }
    for feature_class, rules in defaults.items():
        if feature_class not in merged:
            merged[str(feature_class)] = (
                dict(rules)
                if isinstance(rules, Mapping) and "rule_id" in rules
                else {
                    str(field): dict(rule)
                    for field, rule in (rules or {}).items()
                }
            )
    return merged


def _compile_registry(
    draft: Mapping[str, Any],
    proposal: Mapping[str, Any],
    review: Mapping[str, str],
) -> dict[str, Any]:
    return {
        **dict(draft),
        "review": dict(review),
        "block_families": dict(proposal["block_families"]),
        "insert_layer_families": dict(proposal["insert_layer_families"]),
        "layers": {
            "homepass": list(proposal["homepass_layers"]),
            "span_dimension": list(proposal["span_dimension_layers"]),
            "sling_wire": list(proposal["sling_wire_layers"]),
            "zpm_boundary": list(proposal["zpm_boundary_layers"]),
        },
        "positive_route_layer_regex": _exact_layer_regex(
            list(proposal["route_layers"])
        ),
        "field_rules": _merge_reviewed_rules(
            dict(draft).get("field_rules", {}) or {},
            {
                "IMB": {
                    "CODE": {
                        "rule_id": "AI-IMB-CODE-001",
                        "kind": "entity-text",
                        "provenance": "DWG_DIRECT:text|RULE:AI-IMB-CODE-001",
                    }
                }
            },
        ),
        "display_label_rules": _merge_reviewed_rules(
            dict(draft).get("display_label_rules", {}) or {},
            {
                "IMB": {
                    "rule_id": "AI-IMB-LABEL-001",
                    "kind": "attribute-field",
                    "field": "CODE",
                    "provenance": "DWG_DIRECT:text|RULE:AI-IMB-LABEL-001",
                }
            },
        ),
        "annotation_families": _compile_annotation_families(
            proposal.get("annotation_families", []),
            proposal.get("insert_layer_families", {}),
        ),
        "decision_rules": {
            "annotation_assignment": {
                "rule_id": "AI-ANNOTATION-ASSIGN-001",
                "method": "global-minimum-cost-family-assignment",
                "provenance": "DWG_DERIVED:AI-ANNOTATION-ASSIGN-001",
            },
            "span_segment_measurement": {
                "rule_id": "AI-SPAN-MEASUREMENT-001",
                "method": "exact-segment-or-unique-support-pair",
                "provenance": (
                    "DWG_DIRECT:DIMENSION|"
                    "RULE:AI-SPAN-MEASUREMENT-001"
                ),
            }
        },
        "labels": {},
        "thresholds_native_m": {
            "exact": 0.000001,
            "dimension_to_support": 2.0,
            "device_to_support_candidate": 8.0,
            "device_collocation_to_support_m": 15.0,
            "route_to_asset": 8.0,
        },
        "coverage": {
            "semantics": {"policy": "abstain", "allowlist": []},
            "styles": {"policy": "abstain", "allowlist": []},
        },
    }


def _compile_profile_draft(
    draft: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = proposal["crs_candidate"]
    result = json.loads(json.dumps(draft))
    result["drawing"].update({
        "drawing_units": candidate["drawing_units"],
        "source_coordinate_scale_to_m": candidate[
            "source_coordinate_scale_to_m"
        ],
        "source_coordinate_scale_reviewed": candidate[
            "source_coordinate_scale_reviewed"
        ],
    })
    result["crs"].update({
        "source_crs": candidate["source_crs"],
        "target_crs": candidate["target_crs"],
        "local_registration_strategy": None,
        "local_registration_reviewed": False,
    })
    return result


def compile_onboarding_proposal(
    *,
    source: str | Path,
    project_dir: str | Path,
    proposal: Mapping[str, Any],
    proposer: Mapping[str, Any],
    llm_mode: str = "off",
) -> dict[str, Any]:
    """Compile one validated proposal and derive exact source/feature gates."""

    source_path = Path(source).expanduser().resolve()
    root = Path(project_dir).expanduser().resolve()
    bundle = prepare_onboarding_bundle(root)
    validated = validate_onboarding_proposal(bundle, proposal)

    # ── L1/L2 annotation-family validation with structural repair loop ──
    from .family_validation import (
        derive_family_from_samples,
        l1_validate_family,
        l2_validate_family_group,
        structural_failure_evidence,
    )

    samples_by_layer = dict(bundle.get("text_samples_by_layer", {}))
    family_validation: dict[str, Any] = {
        "schema_version": "cad2gis-family-validation-v1",
        "repair_attempts": 0,
        "max_repair_attempts": 2,
        "results": [],
        "repaired_families": [],
        "dropped_families": [],
    }
    raw_families = list(validated.get("annotation_families", ()))
    if raw_families and samples_by_layer:
        _ASSET_LABEL_LAYER_HINTS = re.compile(
            r"(?i)(pole|fat|fdt|closure|splice|splitter|odp|otb|sling)"
        )
        for attempt in range(family_validation["max_repair_attempts"] + 1):
            family_validation["repair_attempts"] = attempt
            kept: list[dict[str, Any]] = []
            dropped: list[dict[str, Any]] = []
            repaired: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            need_repair: list[dict[str, Any]] = []
            grouped_by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for family in raw_families:
                source_layer = str(family.get("source_layer", "")).strip()
                if source_layer:
                    grouped_by_layer[source_layer].append(family)

            # Missing-family detection: asset label layers whose text samples
            # are structured but have no annotation family.  The model often
            # proposes only one family (e.g. SLING WIRE) and skips POLE ID /
            # FAT CODE — those must be enumerated, not silently dropped.
            covered_layers = set(grouped_by_layer)
            missing_layers = [
                layer
                for layer, samples in samples_by_layer.items()
                if (
                    _ASSET_LABEL_LAYER_HINTS.search(layer)
                    and samples
                    and layer not in covered_layers
                )
            ]
            if missing_layers and attempt < family_validation["max_repair_attempts"]:
                missing_evidence = [
                    {
                        "layer": layer,
                        "samples": [
                            str(s.get("text", "")) for s in samples_by_layer[layer]
                        ][:20],
                        "instruction": (
                            "This asset label layer has structured text samples but "
                            "no annotation family was proposed.  Propose one family "
                            "with a text_pattern that preserves every observed field "
                            "token (separator/case variants tolerated)."
                        ),
                    }
                    for layer in missing_layers
                ]
                repaired_payload = _repair_annotation_families(
                    project_dir=root,
                    failing_families=missing_evidence,
                    provider_id=os.environ.get("CAD2GIS_LLM_PROVIDER") or None,
                    missing_only=True,
                )
                llm_accepted: list[dict[str, Any]] = []
                for repaired_family in repaired_payload:
                    if not repaired_family.get("family_id"):
                        continue
                    layer = str(repaired_family.get("source_layer", "")).strip()
                    own = samples_by_layer.get(layer, ())
                    if own and _family_matches_samples(
                        str(repaired_family.get("text_pattern", "")), own
                    ):
                        llm_accepted.append(repaired_family)
                for family in llm_accepted:
                    repaired.append(family)
                # Deterministic fallback for layers the LLM could not cover.
                for layer in missing_layers:
                    if any(
                        str(f.get("source_layer", "")).strip() == layer
                        for f in llm_accepted
                    ):
                        continue
                    derived = derive_family_from_samples(
                        layer, samples_by_layer.get(layer, ())
                    )
                    repaired.extend(derived)
                family_validation["missing_layers"] = missing_layers
                family_validation["repaired_families"].append(
                    [f.get("family_id", "") for f in repaired]
                )
                raw_families = kept + repaired
                continue

            for family in raw_families:
                source_layer = str(family.get("source_layer", "")).strip()
                l1 = l1_validate_family(family, samples_by_layer)
                l2 = {"passed": True, "sample_count": 0, "ambiguous_count": 0, "unassigned_count": 0}
                if source_layer:
                    siblings = [
                        other for other in raw_families
                        if str(other.get("source_layer", "")).strip() == source_layer
                    ]
                    if len(siblings) > 1:
                        l2 = l2_validate_family_group(
                            siblings,
                            samples_by_layer.get(source_layer, ()),
                        )
                results.append({"l1": l1, "l2": l2})
                if l1["passed"] and l2["passed"]:
                    kept.append(family)
                else:
                    if attempt < family_validation["max_repair_attempts"]:
                        need_repair.append(family)
                    else:
                        dropped.append(family)

            if not need_repair:
                family_validation["results"] = results
                family_validation["dropped_families"] = [
                    {"family_id": f.get("family_id", ""), "reason": "structural_validation_failed"}
                    for f in dropped
                ]
                raw_families = kept
                break

            # Repair attempt: send evidence back to the model
            evidence_items = [
                structural_failure_evidence(
                    family,
                    samples_by_layer.get(str(family.get("source_layer", "")), ()),
                    results[len(raw_families) - len(need_repair) + i]["l1"]
                    if len(results) >= len(raw_families) else {},
                    results[len(raw_families) - len(need_repair) + i]["l2"]
                    if len(results) >= len(raw_families) else {},
                )
                for i, family in enumerate(need_repair)
            ]
            repaired_payload = _repair_annotation_families(
                project_dir=root,
                failing_families=evidence_items,
                provider_id=os.environ.get("CAD2GIS_LLM_PROVIDER") or None,
            )
            for repaired_family in repaired_payload:
                if repaired_family.get("family_id"):
                    repaired.append(repaired_family)
            family_validation["repaired_families"].append(
                [f.get("family_id", "") for f in repaired]
            )
            raw_families = kept + repaired

        # Deterministic cover for asset-label layers that still have no
        # surviving family.  This catches both layers the model never
        # proposed and layers whose proposed/repair families all failed
        # validation and were dropped by ``raw_families = kept + repaired``.
        surviving_layers = {
            str(family.get("source_layer", "")).strip()
            for family in raw_families
            if str(family.get("source_layer", "")).strip()
        }
        fallback_families: list[dict[str, Any]] = []
        for layer, samples in samples_by_layer.items():
            if (
                _ASSET_LABEL_LAYER_HINTS.search(layer)
                and samples
                and layer not in surviving_layers
            ):
                derived = derive_family_from_samples(layer, samples)
                if derived:
                    family_validation.setdefault(
                        "deterministic_fallback_layers", []
                    ).append(layer)
                    fallback_families.extend(derived)
        if fallback_families:
            raw_families.extend(fallback_families)
            family_validation["deterministic_fallback_families"] = [
                family.get("family_id", "") for family in fallback_families
            ]

        # Apply validated families back into the proposal
        validated = {
            **dict(validated),
            "annotation_families": raw_families,
        }

    profile_path = root / "config" / "source_profile.json"
    registry_path = root / "config" / "mapping_registry.json"
    draft_profile, draft_registry, _ = _read_project(root)
    review = _review_record(validated, proposer)
    profile_payload = _compile_profile_draft(draft_profile, validated)
    registry_payload = _compile_registry(
        draft_registry,
        validated,
        review,
    )
    try:
        _atomic_write_json(profile_path, profile_payload)
        _atomic_write_json(registry_path, registry_payload)

        profile = SourceProfile.load(profile_path)
        registry = MappingRegistry.load(
            registry_path,
            profile.source_sha256,
            require_reviewed=False,
        )
        entities, diagnostics = ingest(source_path, profile, skip_census_check=True)
        plan_domain = build_plan_domain(entities)
        semantic_base = list(plan_domain.entities)

        # ── Dry-run classification with route-regex check chain ─────────
        # Loop: classify → inspect unmatched_route_layer for cable-looking
        # layers → extend regex → re-classify.  Feature counts are taken from
        # the FINAL pass so admission matches what convert will deliver.
        from .spatial_filter import apply_spatial_denoising

        feature_counts: dict[str, int] = {}
        annotation_expectations: dict[str, dict[str, int]] = {}
        semantic_diagnostics: dict[str, Any] = {}
        route_regex_check: dict[str, Any] = {"status": "not_run"}
        route_regex_rounds: list[dict[str, Any]] = []
        for _round in range(3):
            semantic = list(semantic_base)
            spatial_result = apply_spatial_denoising(
                entities=semantic,
                catalog_roots=getattr(plan_domain, "catalog_roots", frozenset()),
                plan_domain=plan_domain,
                project_config_dir=root / "config",
                llm_mode=llm_mode,
                route_regex=registry.positive_route_layer_regex,
                boundary_exempt_layers=(
                    registry.layers.get("zpm_boundary", ())
                    + registry.layers.get("sling_wire", ())
                    + registry.layers.get("homepass", ())
                ),
                label_text_patterns=[
                    str(family.text_pattern)
                    for family in getattr(registry, "annotation_families", ())
                ],
                reviewed_insert_layers=[
                    str(layer)
                    for layers in getattr(
                        registry, "insert_layer_families", {}
                    ).values()
                    for layer in layers
                ],
                cable_protect_layers=registry.layers.get("sling_wire", ()),
                dimension_protect_layers=registry.layers.get(
                    "span_dimension", ()
                ),
            )
            semantic = list(spatial_result["entities"])

            features, _, _, semantic_diagnostics = classify_entities(
                semantic,
                registry,
                coverage_policy=registry.semantic_coverage_policy,
                coverage_allowlist=list(registry.semantic_coverage_allowlist),
                catalog_roots=getattr(plan_domain, "catalog_roots", frozenset()),
            )
            feature_counts = dict(
                sorted(Counter(item.feature_class for item in features).items())
            )
            _dry_assignments = semantic_diagnostics.get(
                "annotation_assignments_by_family", {}
            )
            annotation_expectations = {
                _fid: {"assigned": int(_fam_diag.get("assigned", 0))}
                for _fid, _fam_diag in sorted(_dry_assignments.items())
            }

            # Route regex check chain: find unmatched cable-looking layers.
            coverage_records = semantic_diagnostics.get(
                "coverage", {}
            ).get("records", [])
            unmatched_layers: dict[str, int] = defaultdict(int)
            for record in coverage_records:
                if not isinstance(record, Mapping):
                    continue
                if str(record.get("reason", "")) != "unmatched_route_layer":
                    continue
                layer = str(record.get("source_layer", "")).strip()
                if layer:
                    unmatched_layers[layer] += 1
            suspected_cable_layers = [
                layer
                for layer, count in unmatched_layers.items()
                if re.search(r"(?i)(cable|feeder|\bfo\b|grt)", layer)
            ]
            known = str(
                registry_payload.get("positive_route_layer_regex", "")
                or "(?!)"
            )
            extended = _extend_route_regex(known, suspected_cable_layers)
            if extended != known:
                registry_payload["positive_route_layer_regex"] = extended
                _atomic_write_json(registry_path, registry_payload)
                registry = MappingRegistry.load(
                    registry_path, profile.source_sha256, require_reviewed=False,
                )
                round_record = {
                    "status": "extended",
                    "round": _round + 1,
                    "added_layers": suspected_cable_layers,
                    "previous": known,
                    "extended": extended,
                }
                route_regex_rounds.append(round_record)
                route_regex_check = round_record
                continue  # re-classify with the extended regex
            route_regex_check = {
                "status": "unchanged",
                "round": _round + 1,
                "suspected_layers": suspected_cable_layers,
            }
            break
        if route_regex_rounds:
            route_regex_check = {
                **route_regex_check,
                "status": "extended",
                "extended_rounds": len(route_regex_rounds),
                "all_added_layers": sorted({
                    layer
                    for round_record in route_regex_rounds
                    for layer in round_record.get("added_layers", ())
                }),
            }

        if not feature_counts:
            raise OnboardingError(
                "AI proposal produced no semantic features; project remains draft"
            )
        family_validation["route_regex_check"] = route_regex_check
        profile_payload["expectations"]["source_inventory"] = {
            key: int(diagnostics["census"][key])
            for key in ("model_entities", "model_inserts", "model_dimensions")
        }
        profile_payload["expectations"]["feature_counts"] = feature_counts
        profile_payload["expectations"]["annotation_families"] = {}
        profile_payload["review"] = dict(review)
        _atomic_write_json(profile_path, profile_payload)
        validation = validate_project(project_dir=root)
        if validation.get("conversion_allowed") is not True:
            if validation.get("status") != "registration_required":
                raise OnboardingError(
                    f"Compiled onboarding pack failed admission: {validation}"
                )
            # registration_required: local engineering coordinates detected;
            # GCP registration is needed before geographic delivery, but the
            # onboarded config is valid.
    except Exception:
        # Admission is transactional. A failed model proposal or failed dry
        # run must not leave an apparently reviewed, runnable project pack.
        _atomic_write_json(profile_path, draft_profile)
        _atomic_write_json(registry_path, draft_registry)
        raise
    result = {
        "schema_version": "cad2gis.ai_onboarding_compile_result.v1",
        "status": "auto_accepted",
        "project_dir": str(root),
        "source_sha256": validated["source_sha256"],
        "inventory_sha256": validated["inventory_sha256"],
        "bundle_sha256": validated["bundle_sha256"],
        "proposal_sha256": validated["proposal_sha256"],
        "proposer": {
            str(key): value
            for key, value in proposer.items()
            if key not in {"api_key", "authorization", "token"}
        },
        "feature_counts": feature_counts,
        "family_validation": family_validation,
        "semantic_coverage": semantic_diagnostics.get("coverage"),
        "plan_domain": plan_domain.diagnostics,
        "validation": validation,
    }
    _atomic_write_json(root / "review" / "ai_onboarding_result.json", result)
    return result


def request_onboarding_proposal(
    project_dir: str | Path,
    *,
    provider_id: str | None = None,
    provider: ReviewProvider | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask one configured model provider for a strict onboarding proposal."""

    bundle = prepare_onboarding_bundle(project_dir)
    selected_provider = provider
    if selected_provider is None:
        selected_provider = OpenAICompatibleProvider(
            load_provider_config(provider=provider_id)
        )
    # OVERFIT-RISK (prompt examples): ``EXT.MR.MF.LBB.S02.P001``, ``DMPH``,
    # ``FAT CODE``/``FDT-Info`` and the layer-name examples are illustrative
    # tokens from the four baseline drawings.  The JSON schema enum and the
    # bundled text samples remain authoritative for every new project.
    request = ReviewRequest(
        system_prompt=(
            "You are the CAD2GIS onboarding planner. Return a JSON object matching "
            "the proposal_schema EXACTLY — every required field, no extra fields, "
            "no invented values. Select only identifiers listed in the schema enums. "
            "Map telecom route layers, device INSERT layers/blocks, home labels, "
            "span dimensions, sling wire, and ZPM boundary layers (zone outlines "
            "such as FAT AREA / BOUNDARY FAT, drawn as closed-area polylines). "
            "Select the supplied deterministic CRS "
            "candidate. The CRS candidate is derived from DWG metadata (CGEOCS + "
            "INSUNITS), not inferred — when the crs_candidates list has exactly one "
            "entry, set confidence.crs to 1.0 and select that candidate_id. "
            "Derive annotation_families STRICTLY from the observed text samples in "
            "text_samples_by_layer — never invent formats that are not present in "
            "the samples. Each sample entry includes text and, when available, "
            "aci_color. "
            "For every layer whose text values carry structure (e.g. identifiers "
            "with a field hierarchy such as EXT.MR.MF.LBB.S02.P001), propose ONE "
            "annotation_family per distinct field structure with a text_pattern "
            "that preserves EVERY observed field token (no .* collapsing of "
            "middle fields; keep the full hierarchy like EXT, MR, MF, LBB, S\\d{2}, "
            "P\\d+). Treat separator variants (space vs dot vs slash) and case "
            "differences as equivalent — the pattern must capture the field "
            "structure, not a literal separator. "
            "When a layer contains MULTIPLE distinct label families (e.g. DMPH "
            "identifiers alongside plain numeric codes), propose SEPARATE families "
            "per group, distinguishing them by their characteristic tokens. "
            "When samples on the same layer share the same text pattern but carry "
            "different aci_color values, add the aci_color field to the family so "
            "the runtime can filter by colour. "
            "COVERAGE: every layer whose text samples follow a structure must get "
            "at least one annotation family — pole identifier layers (e.g. POLE ID, "
            "EXISTING POLE, NEW POLE), closure layers (FAT CODE, FAT, FAT Info), "
            "and splitter layers (FDT-Info) are all asset label layers.  Do not "
            "stop after one family; enumerate every structured label layer. "
            "Set target_class to BOITE/PTECH/SITE and max_distance_native_m to "
            "15.0 unless the sample layout justifies otherwise. "
            "Do not invent coordinates, lengths, CRS identifiers, GCPs, "
            "layers, blocks, or expected counts. Prefer abstention over a weak "
            "semantic mapping."
        ),
        context={
            key: value
            for key, value in bundle.items()
        },
        json_schema=bundle["proposal_schema"],
        schema_name="cad2gis_ai_onboarding_proposal",
    )
    response: ReviewResponse = selected_provider.review(request)
    try:
        payload = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise OnboardingError("Provider onboarding response is not JSON") from exc
    if not isinstance(payload, dict):
        raise OnboardingError("Provider onboarding response root must be an object")
    proposal = validate_onboarding_proposal(bundle, payload)
    provenance = {
        "provider": response.provider,
        "protocol": response.protocol,
        "model": response.model,
        "capability": response.capability,
        "base_url_profile_sha256": response.base_url_profile_sha256,
        "request_sha256": response.request_sha256,
        "response_sha256": response.response_sha256,
        "response_id": response.response_id,
    }
    return proposal, provenance


def auto_onboard_with_provider(
    *,
    source: str | Path,
    project_dir: str | Path,
    provider_id: str | None = None,
    force_bootstrap: bool = False,
    llm_mode: str = "off",
) -> dict[str, Any]:
    """Bootstrap, ask the configured AI, compile, and validate one new source."""

    root = Path(project_dir).expanduser().resolve()
    managed = root / "review" / "source_inventory.json"
    if force_bootstrap or not managed.is_file():
        bootstrap_project(
            source=source,
            project_dir=root,
            force=force_bootstrap,
        )
    proposal, provenance = request_onboarding_proposal(
        root,
        provider_id=provider_id,
    )
    return compile_onboarding_proposal(
        source=source,
        project_dir=root,
        proposal=proposal,
        proposer=provenance,
        llm_mode=llm_mode,
    )


__all__ = [
    "ONBOARDING_BUNDLE_SCHEMA",
    "ONBOARDING_PROPOSAL_SCHEMA",
    "OnboardingError",
    "auto_onboard_with_provider",
    "compile_onboarding_proposal",
    "onboarding_proposal_json_schema",
    "prepare_onboarding_bundle",
    "request_onboarding_proposal",
    "validate_onboarding_proposal",
]
