"""AI-assisted, source-bound onboarding for previously unseen CAD drawings.

The model selects only observed CAD layer/block identifiers and one
deterministically derived CRS/unit candidate.  Compilation, census derivation,
coverage policy, hashing, and conversion admission remain deterministic.
"""

from __future__ import annotations

import hashlib
import json
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
from .units import NOMINAL_LOCAL_DRAWING_UNITS, resolve_insunits


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


def _local_fallback_crs_candidate(
    cgeocs: str,
    insunits: int | None,
) -> dict[str, Any]:
    """Nominal local placeholder for drawings without usable CRS metadata.

    The placeholder is deliberately honest: the scale is an unreviewed 1.0
    and the evidence marks the CRS as requiring GCP registration.  Selecting
    it lets onboarding proceed; conversion stays fail-closed until a
    reviewed GCP registration profile is supplied.
    """

    candidate = {
        "candidate_id": (
            "CAD-LOCAL:NO-CGEOCS:INSUNITS-"
            + (str(insunits) if insunits is not None else "NA")
        ),
        "source_crs": "EPSG:3857",
        "target_crs": "EPSG:3857",
        "drawing_units": NOMINAL_LOCAL_DRAWING_UNITS,
        "source_coordinate_scale_to_m": 1.0,
        "source_coordinate_scale_reviewed": False,
        "evidence": {
            "dwg_cgecs": cgeocs,
            "dwg_insunits": insunits,
            "authority": "LOCAL_FALLBACK_NO_CGEOCS",
            "absolute_accuracy": "requires_gcp_registration",
            "nominal": True,
        },
    }
    candidate["candidate_sha256"] = _canonical_sha256(candidate)
    return candidate


def _crs_candidates(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    drawing = profile.get("drawing")
    if not isinstance(drawing, Mapping):
        return []
    cgeocs = str(drawing.get("dwg_cgeocs") or "").strip()
    raw_insunits = drawing.get("dwg_insunits")
    insunits = (
        raw_insunits
        if isinstance(raw_insunits, int) and not isinstance(raw_insunits, bool)
        else None
    )
    crs_token = _CGEOCS_CRS.get(cgeocs.upper()) if cgeocs else None
    if crs_token is None:
        return [_local_fallback_crs_candidate(cgeocs, insunits)]
    if insunits is None:
        return []
    unit = resolve_insunits(insunits)
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
    result: dict[str, Any] = {
        "route_layers": [],
        "homepass_layers": [],
        "span_dimension_layers": [],
        "sling_wire_layers": [],
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
            or re.fullmatch(r"HP(?:\s*\([A-Z]\)|\s+REDUCE)?", upper)
        ):
            result["homepass_layers"].append(str(layer))
        if "SPAN" in upper or "DIMENSI" in upper:
            result["span_dimension_layers"].append(str(layer))
        if "SLING WIRE" in upper:
            result["sling_wire_layers"].append(str(layer))
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
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    if not isinstance(carriers, list):
        return {}
    for raw in carriers:
        if not isinstance(raw, Mapping):
            continue
        text = str(raw.get("text") or "").strip()
        layer = str(raw.get("layer") or "").strip()
        if (
            not text
            or not layer
            or text in grouped[layer]
            or len(grouped[layer]) >= limit_per_layer
        ):
            continue
        grouped[layer].append(text[:160])
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


def _scene_understanding_context(
    root: Path,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    summary = inventory.get("scene_visual")
    if not isinstance(summary, Mapping):
        return {"status": "not_available"}
    relative = Path(str(summary.get("relative_path", "")))
    if relative.is_absolute() or ".." in relative.parts:
        raise OnboardingError("Scene visual manifest path is not project-relative")
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise OnboardingError("Scene visual manifest path escapes the project root")
    manifest = _read_json(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != summary.get("manifest_sha256"):
        raise OnboardingError("Scene visual manifest digest is stale")
    layouts = manifest.get("layouts")
    regions = manifest.get("regions")
    if not isinstance(layouts, list) or not isinstance(regions, list):
        raise OnboardingError("Scene visual manifest is missing layouts or regions")
    region_index = [
        {
            key: item.get(key)
            for key in (
                "region_id", "layout", "native_bounds", "pixel_width",
                "pixel_height", "visible_entity_count", "render_path",
                "render_sha256", "context_path", "context_sha256",
            )
        }
        for item in regions if isinstance(item, Mapping)
    ]
    overview = [
        item for item in region_index
        if str(item.get("region_id", "")).endswith(":overview")
    ]
    dense_details = sorted(
        (
            item for item in region_index
            if not str(item.get("region_id", "")).endswith(":overview")
        ),
        key=lambda item: (
            -int(item.get("visible_entity_count") or 0),
            str(item.get("region_id", "")),
        ),
    )
    prompt_regions = (overview + dense_details)[:64]
    return {
        "status": "available",
        "schema_version": manifest.get("schema_version"),
        "manifest_sha256": digest,
        "cad_scene_graph_sha256": manifest.get("cad_scene_graph_sha256"),
        "layout_count": manifest.get("layout_count"),
        "region_count": manifest.get("region_count"),
        "render_conserved": manifest.get("render_conserved"),
        "layouts": layouts,
        "prompt_region_index": prompt_regions,
        "prompt_region_index_is_complete": len(prompt_regions) == len(region_index),
        "complete_region_index_sha256": _canonical_sha256(region_index),
        "retrieval": {
            "list_tool": "list_scene_visual_regions",
            "context_tool": "get_scene_visual_region_context",
        },
        "authority": manifest.get("model_contract"),
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
        "cad_scene_graph": dict(inventory.get("cad_scene_graph", {})),
        "scene_visual": dict(inventory.get("scene_visual", {})),
        "scene_understanding": _scene_understanding_context(root, inventory),
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

    expected = set(onboarding_proposal_json_schema(bundle)["required"])
    if set(proposal) != expected:
        raise OnboardingError(
            "Invalid onboarding proposal keys; "
            f"missing={sorted(expected - set(proposal))}, "
            f"unknown={sorted(set(proposal) - expected)}"
        )
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
    if normalized_confidence["crs"] < 0.95:
        raise OnboardingError(
            "CRS auto-acceptance requires confidence >= 0.95"
        )
    if normalized_confidence["semantics"] < 0.75:
        raise OnboardingError(
            "Semantic auto-acceptance requires confidence >= 0.75"
        )
    rationale = proposal.get("rationale")
    if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 4000:
        raise OnboardingError("rationale must be 1-4000 characters")
    normalized["confidence"] = normalized_confidence
    normalized["rationale"] = rationale.strip()
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
        },
        "positive_route_layer_regex": _exact_layer_regex(
            list(proposal["route_layers"])
        ),
        "field_rules": {
            "IMB": {
                "CODE": {
                    "rule_id": "AI-IMB-CODE-001",
                    "kind": "entity-text",
                    "provenance": "DWG_DIRECT:text|RULE:AI-IMB-CODE-001",
                }
            }
        },
        "display_label_rules": {
            "IMB": {
                "rule_id": "AI-IMB-LABEL-001",
                "kind": "attribute-field",
                "field": "CODE",
                "provenance": "DWG_DIRECT:text|RULE:AI-IMB-LABEL-001",
            }
        },
        "annotation_families": [],
        "decision_rules": {
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
) -> dict[str, Any]:
    """Compile one validated proposal and derive exact source/feature gates."""

    source_path = Path(source).expanduser().resolve()
    root = Path(project_dir).expanduser().resolve()
    bundle = prepare_onboarding_bundle(root)
    validated = validate_onboarding_proposal(bundle, proposal)
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
        entities, diagnostics = ingest(source_path, profile)
        # Compile must derive expectations from the exact plan-domain view the
        # conversion pipeline will build: same route-layer pattern from the
        # reviewed registry and the same plan-domain declarations from the
        # profile.  Otherwise the recorded feature census diverges from the
        # conversion-time census (e.g. route-layer exemption rescues cables
        # only on one side) and the exact-count gate fails at convert time.
        route_regex = getattr(registry, "positive_route_layer_regex", None)
        route_layer_pattern = (
            None
            if not route_regex or route_regex == "(?!)"
            else re.compile(route_regex)
        )
        plan_domain = build_plan_domain(
            entities,
            route_layer_pattern=route_layer_pattern,
            plan_layouts=profile.plan_layouts,
            include_orphan_blocks=(
                "*" if profile.include_orphan_blocks == ("*",)
                else profile.include_orphan_blocks or None
            ),
            excluded_legend_entity_keys=profile.excluded_legend_entity_keys,
        )
        features, _, _, semantic_diagnostics = classify_entities(
            list(plan_domain.entities),
            registry,
            coverage_policy=registry.semantic_coverage_policy,
            coverage_allowlist=list(registry.semantic_coverage_allowlist),
        )
        feature_counts = dict(
            sorted(Counter(item.feature_class for item in features).items())
        )
        if not feature_counts:
            raise OnboardingError(
                "AI proposal produced no semantic features; project remains draft"
            )
        profile_payload["expectations"]["source_inventory"] = {
            key: int(diagnostics["census"][key])
            for key in ("model_entities", "model_inserts", "model_dimensions")
        }
        profile_payload["expectations"]["feature_counts"] = feature_counts
        profile_payload["review"] = dict(review)
        _atomic_write_json(profile_path, profile_payload)
        validation = validate_project(project_dir=root)
        if validation.get("conversion_allowed") is not True:
            # The local fallback CRS candidate is a nominal placeholder:
            # admission accepts a reviewed pack whose only remaining gate is
            # the reviewed GCP registration profile at conversion time.
            crs_evidence = validated["crs_candidate"].get("evidence") or {}
            nominal_fallback_pending_gcp = (
                isinstance(crs_evidence, Mapping)
                and crs_evidence.get("authority") == "LOCAL_FALLBACK_NO_CGEOCS"
                and validation.get("status") == "registration_required"
            )
            if not nominal_fallback_pending_gcp:
                raise OnboardingError(
                    f"Compiled onboarding pack failed admission: {validation}"
                )
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
    request = ReviewRequest(
        system_prompt=(
            "You are the CAD2GIS onboarding planner. First use the supplied "
            "layout and scene-region structural index to separate plan content "
            "from legend, title, schedule, overview, annotation, and unknown "
            "content. Do not claim pixel-level visual evidence unless images "
            "were actually supplied by the host. Select only identifiers listed "
            "in the task-bound schema. Map route layers, device INSERT layers/"
            "blocks, labels, dimensions, and other observed drawing-local roles. "
            "Select the supplied deterministic CRS candidate. Do not invent "
            "coordinates, lengths, CRS identifiers, GCPs, layers, blocks, or "
            "expected counts. Prefer abstention over a weak semantic mapping."
        ),
        context={
            key: value
            for key, value in bundle.items()
            if key != "proposal_schema"
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
