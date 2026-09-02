"""Deterministic source inspection and project-profile onboarding.

The onboarding boundary records CAD facts only.  It deliberately does not
classify telecom assets, select a CRS, infer drawing units, or manufacture
ground-control points.  A bootstrapped pack is therefore useful review input,
but cannot authorize conversion until both project contracts carry explicit
``reviewed`` records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .geodata import normalize_geodata_registration

from ..reader.contracts import DWGRecordInventory
from .config import (
    MAPPING_REGISTRY_SCHEMA_VERSION,
    PROJECT_PROFILE_SCHEMA_VERSION,
    MappingRegistry,
    SourceProfile,
)
from .cad_scene_graph import CadSceneGraph, build_cad_scene_graph
from .model import SourceEntity
from .plan_domain import PlanDomainError, build_plan_domain


INVENTORY_SCHEMA_VERSION = "cad2gis-source-inventory-v1"
UNSUPPORTED_SCHEMA_VERSION = "cad2gis-unsupported-inventory-v1"


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _string(record: Any, name: str) -> str:
    value = _record_value(record, name, "")
    return "" if value is None else str(value).strip()


def _reason_values(record: Any) -> tuple[str, ...]:
    raw = _record_value(record, "raw_properties", {}) or {}
    reasons: Any = raw.get("unsupported_reasons", ()) if isinstance(raw, Mapping) else ()
    if not reasons:
        singular = raw.get("unsupported_reason", "") if isinstance(raw, Mapping) else ""
        reasons = str(singular).split(";") if singular else ()
    if isinstance(reasons, str):
        reasons = reasons.split(";")
    return tuple(sorted({str(item).strip() for item in reasons if str(item).strip()}))


def _reader_protocol_contract(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep completeness evidence, excluding machine/runtime path identity."""
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "backend", "compatibility_policy", "completion_rows",
        "completion_schema", "total_rows", "parsed_rows", "returned_records",
        "skipped_rows", "skipped_row_errors", "inventory_complete",
    }
    return {
        key: value[key]
        for key in sorted(allowed & set(value))
    }


def _metadata_values(texts: Iterable[str], key: str) -> list[str]:
    pattern = re.compile(rf"(?:^|[;|,\s]){re.escape(key)}\s*=\s*([^;|,\s]+)", re.I)
    return sorted({match.group(1).strip() for text in texts for match in pattern.finditer(text)})


def _style_fact(record: Any, name: str, default: Any) -> Any:
    style = _record_value(record, "style")
    if style is not None:
        return getattr(style, name, default)
    return _record_value(record, name, default)


def build_source_inventory(
    source: str | Path,
    records: Iterable[Any],
    *,
    reader_protocol: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a path-independent, hashable inventory from authoritative records."""

    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"DWG source does not exist: {source_path}")
    if source_path.suffix.casefold() != ".dwg":
        raise ValueError(f"Project inspection requires a DWG source: {source_path}")

    reader_protocol = _reader_protocol_contract(
        getattr(records, "diagnostics", {}) or {}
        if reader_protocol is None else reader_protocol
    )
    materialized = list(records)
    source_hash = _file_sha256(source_path)
    for index, record in enumerate(materialized):
        bound_hash = _string(record, "source_sha256")
        if bound_hash and bound_hash.casefold() != source_hash:
            raise ValueError(
                f"Reader record {index} is bound to a different source SHA-256"
            )

    layouts = Counter(_string(record, "layout") or "UNAVAILABLE" for record in materialized)
    roles = Counter(_string(record, "cad_role") or "UNAVAILABLE" for record in materialized)
    entity_types = Counter(
        (_string(record, "dwg_type") or _string(record, "dwg_type_name") or "UNAVAILABLE").upper()
        for record in materialized
    )
    layers = Counter(_string(record, "layer") or "UNAVAILABLE" for record in materialized)
    blocks = Counter(
        _string(record, "block_name")
        for record in materialized
        if _string(record, "block_name")
    )
    # Keep the source-inventory census identical to the admission census in
    # ``ingest``.  A record can physically live in the Model layout while the
    # plan-domain classifier correctly assigns it a non-model role (title
    # block, design summary, catalogue, and so on).  Counting the union made
    # freshly bootstrapped profiles fail their own conversion gate.
    model_records = [
        record for record in materialized
        if _string(record, "cad_role").casefold() == "model"
    ]
    metadata_texts = sorted(
        _string(record, "text")
        for record in materialized
        if (_string(record, "dwg_type") or _string(record, "dwg_type_name")).upper()
        == "DOCUMENT_METADATA"
    )
    geodata_registrations: list[dict[str, Any]] = []
    for record in materialized:
        entity_type = (
            _string(record, "dwg_type")
            or _string(record, "dwg_type_name")
            or ""
        ).upper()
        if entity_type != "DOCUMENT_METADATA":
            continue
        raw_properties = _record_value(record, "raw_properties", {}) or {}
        if not isinstance(raw_properties, Mapping):
            continue
        value = raw_properties.get("geodata_registration")
        if value is None:
            continue
        geodata_registrations.append(normalize_geodata_registration(value))
    unique_geodata = {
        json.dumps(value, sort_keys=True, separators=(",", ":")): value
        for value in geodata_registrations
    }
    if len(unique_geodata) > 1:
        raise ValueError("Reader returned conflicting GEODATA registrations")
    geodata_registration = next(iter(unique_geodata.values()), None)
    annotation_types = {
        "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "MLEADER", "MULTILEADER",
        "TABLE", "TABLE_CELL",
    }
    annotation_carriers = []
    block_instances = []
    style_facts: Counter[str] = Counter()
    curve_primitives: Counter[str] = Counter()
    for index, record in enumerate(materialized):
        entity_type = (
            _string(record, "dwg_type") or _string(record, "dwg_type_name") or "UNAVAILABLE"
        ).upper()
        entity_key = _string(record, "entity_key") or f"record:{index}"
        if entity_type in annotation_types:
            carrier: dict[str, Any] = {
                "entity_key": entity_key,
                "dwg_type": entity_type,
                "layout": _string(record, "layout"),
                "layer": _string(record, "layer"),
                "text": _string(record, "text"),
            }
            aci_color = _record_value(record, "aci_color", None)
            if aci_color is None and hasattr(record, "style"):
                # SourceEntity carries colour on the style; keep the inventory
                # hash identical between the record-dict and entity paths.
                aci_color = getattr(record.style, "aci_color", None)
            if isinstance(aci_color, int) and not isinstance(aci_color, bool):
                carrier["aci_color"] = aci_color
            annotation_carriers.append(carrier)
        if entity_type == "INSERT":
            attributes = _record_value(record, "block_attributes", {}) or {}
            if not isinstance(attributes, Mapping):
                attributes = {}
            block_instances.append({
                "entity_key": entity_key,
                "layout": _string(record, "layout"),
                "layer": _string(record, "layer"),
                "block_name": _string(record, "block_name"),
                "attributes": {
                    str(key): str(value)
                    for key, value in sorted(attributes.items(), key=lambda item: str(item[0]))
                },
            })
        style_key = "|".join((
            f"ACI:{_style_fact(record, 'aci_color', 256)}",
            f"TRUECOLOR:{str(_style_fact(record, 'true_color', '')).upper()}",
            f"LINETYPE:{_style_fact(record, 'linetype', 'ByLayer')}",
            f"LINEWEIGHT:{_style_fact(record, 'lineweight', -1)}",
        ))
        style_facts[style_key] += 1
        curve_facts = _record_value(record, "curve_facts", {}) or {}
        if isinstance(curve_facts, Mapping) and curve_facts:
            primitive = str(curve_facts.get("primitive_type", "UNAVAILABLE")).upper()
            has_bulge = any(
                abs(float(value)) > 0.0 for value in curve_facts.get("bulges", ())
            )
            curve_primitives[f"{primitive}|NONZERO_BULGE:{has_bulge}"] += 1

    reason_counts: Counter[str] = Counter()
    reason_entity_keys: dict[str, list[str]] = {}
    unsupported_entity_types: Counter[str] = Counter()
    for index, record in enumerate(materialized):
        reasons = _reason_values(record)
        if not reasons:
            continue
        entity_type = (
            _string(record, "dwg_type") or _string(record, "dwg_type_name") or "UNAVAILABLE"
        ).upper()
        entity_key = _string(record, "entity_key") or f"record:{index}"
        unsupported_entity_types[entity_type] += 1
        for reason in reasons:
            reason_counts[reason] += 1
            reason_entity_keys.setdefault(reason, []).append(entity_key)

    inventory: dict[str, Any] = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source": {
            "name": source_path.name,
            "sha256": source_hash,
            "size_bytes": source_path.stat().st_size,
        },
        "counts": {
            "records": len(materialized),
            "model_entities": len(model_records),
            "model_inserts": sum(
                (_string(record, "dwg_type") or _string(record, "dwg_type_name")).upper()
                == "INSERT"
                for record in model_records
            ),
            "model_dimensions": sum(
                (_string(record, "dwg_type") or _string(record, "dwg_type_name")).upper()
                == "DIMENSION"
                for record in model_records
            ),
            "unsupported_records": sum(unsupported_entity_types.values()),
            "block_instances": len(block_instances),
            "annotation_entities": len(annotation_carriers),
            "annotation_entities_with_text": sum(
                bool(item["text"]) for item in annotation_carriers
            ),
            "native_length_entities": sum(
                _record_value(record, "native_length") is not None
                for record in materialized
            ),
            "curve_facts_entities": sum(
                bool(_record_value(record, "curve_facts", {}) or {})
                for record in materialized
            ),
            "style_variants": len(style_facts),
        },
        "layouts": dict(sorted(layouts.items())),
        "cad_roles": dict(sorted(roles.items())),
        "entity_types": dict(sorted(entity_types.items())),
        "layers": dict(sorted(layers.items())),
        "block_names": dict(sorted(blocks.items())),
        "block_instances": sorted(block_instances, key=lambda item: item["entity_key"]),
        "annotation_carriers": sorted(
            annotation_carriers, key=lambda item: item["entity_key"]
        ),
        "style_facts": dict(sorted(style_facts.items())),
        "curve_capabilities": dict(sorted(curve_primitives.items())),
        "document_metadata": {
            "records": metadata_texts,
            "dwg_cgeocs_values": _metadata_values(metadata_texts, "CGEOCS"),
            "dwg_insunits_values": _metadata_values(metadata_texts, "INSUNITS"),
            "geodata_registration": geodata_registration,
        },
        "unsupported": {
            "by_reason": dict(sorted(reason_counts.items())),
            "by_entity_type": dict(sorted(unsupported_entity_types.items())),
            "entity_keys_by_reason": {
                reason: sorted(keys) for reason, keys in sorted(reason_entity_keys.items())
            },
        },
        "reader_protocol": reader_protocol,
    }
    inventory["inventory_sha256"] = inventory_sha256(inventory)
    return inventory


def inventory_sha256(inventory: Mapping[str, Any]) -> str:
    payload = dict(inventory)
    payload.pop("inventory_sha256", None)
    # These fields are derived after the immutable reader inventory is built.
    # They remain useful review evidence but are not part of the source census
    # binding reconstructed during conversion.
    payload.pop("plan_domain", None)
    payload.pop("inspection_status", None)
    payload.pop("onboarding", None)
    # The scene graph is deterministically derived after the immutable reader
    # census is built.  Excluding it keeps the project binding reproducible
    # when conversion reconstructs the authoritative reader inventory.
    payload.pop("cad_scene_graph", None)
    return _canonical_json_sha256(payload)


def _extract_records(source: Path) -> DWGRecordInventory:
    # Reader import is delayed so project/CLI help remains usable without GIS
    # and platform-specific reader dependencies.
    from .ingest import extract_records

    return extract_records(source)


def _plan_domain_summary(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    issues = list(diagnostics.get("issues", ()))
    return {
        key: value
        for key, value in diagnostics.items()
        if key != "issues"
    } | {
        "issue_count": len(issues),
        "issue_codes": dict(sorted(Counter(
            str(issue.get("code", "unknown"))
            for issue in issues
            if isinstance(issue, Mapping)
        ).items())),
        "issue_sample": issues[:10],
    }


def _inspect_source_products(
    *,
    source: str | Path,
    records: Iterable[Any] | None = None,
) -> tuple[dict[str, Any], CadSceneGraph]:
    """Return inventory plus a pre-semantic, source-bound structural graph."""

    source_path = Path(source).expanduser().resolve()
    authoritative_records = _extract_records(source_path) if records is None else records
    reader_protocol = dict(
        getattr(authoritative_records, "diagnostics", {}) or {}
    )
    if records is None or reader_protocol:
        skipped_rows = int(reader_protocol.get("skipped_rows", 0) or 0)
        if (
            skipped_rows != 0
            or reader_protocol.get("inventory_complete") is not True
        ):
            raise ValueError(
                "Reader inventory is not authoritative: "
                f"skipped_rows={skipped_rows}, "
                "inventory_complete="
                f"{reader_protocol.get('inventory_complete')!r}"
            )
    materialized = list(authoritative_records)
    inventory = build_source_inventory(
        source_path,
        materialized,
        reader_protocol=reader_protocol,
    )
    entities = [
        record
        if isinstance(record, SourceEntity)
        else SourceEntity.from_record(dict(record))
        for record in materialized
    ]
    try:
        plan_domain = build_plan_domain(entities)
    except PlanDomainError as exc:
        inventory["plan_domain"] = _plan_domain_summary(exc.diagnostics)
        inventory["inspection_status"] = "FAIL"
        plan_entities: tuple[SourceEntity, ...] = ()
    else:
        inventory["plan_domain"] = _plan_domain_summary(plan_domain.diagnostics)
        inventory["inspection_status"] = (
            "PASS"
            if plan_domain.diagnostics.get("status") == "PASS"
            else "WATCH"
        )
        plan_entities = plan_domain.entities
    scene_graph = build_cad_scene_graph(
        source_sha256=str(inventory["source"]["sha256"]),
        source_entities=entities,
        plan_entities=plan_entities,
    )
    inventory["cad_scene_graph"] = {
        "schema_version": "cad2gis.cad_scene_graph.v1",
        "graph_sha256": scene_graph.graph_sha256,
        "node_count": len(scene_graph.nodes),
        "edge_count": len(scene_graph.edges),
        "relative_path": "review/cad_scene_graph.json",
        "authority": scene_graph.diagnostics["authority"],
    }
    inventory["onboarding"] = {
        "reviewed_project_pack_present": False,
        "conversion_allowed": False,
        "semantic_accuracy": "not_evaluated_without_reviewed_mapping",
        "coordinate_accuracy": (
            "not_evaluated_without_reviewed_crs_and_independent_gcp"
        ),
        "next_action": "bootstrap_source_bound_project_pack",
    }
    inventory["inventory_sha256"] = inventory_sha256(inventory)
    return inventory, scene_graph


def inspect_source(
    *,
    source: str | Path,
    project_dir: str | Path | None = None,
    records: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Inspect one DWG without assigning GIS meaning or writing project files."""

    del project_dir  # Accepted as a stable public-port argument; inspection is read-only.
    inventory, _ = _inspect_source_products(source=source, records=records)
    return inventory


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _single_observed(values: Any) -> Any:
    return values[0] if isinstance(values, list) and len(values) == 1 else None


def _draft_profile(inventory: Mapping[str, Any]) -> dict[str, Any]:
    source = inventory["source"]
    metadata = inventory["document_metadata"]
    raw_insunits = _single_observed(metadata["dwg_insunits_values"])
    try:
        insunits = None if raw_insunits is None else int(raw_insunits)
    except (TypeError, ValueError):
        insunits = None
    project_id = f"source-{str(source['sha256'])[:12]}"
    counts = inventory["counts"]
    return {
        "schema_version": PROJECT_PROFILE_SCHEMA_VERSION,
        "project_id": project_id,
        "review": {"status": "draft"},
        "source_binding": {
            "source_sha256": source["sha256"],
            "source_size_bytes": source["size_bytes"],
            "inventory_sha256": inventory["inventory_sha256"],
        },
        "drawing": {
            "dwg_cgeocs": _single_observed(metadata["dwg_cgeocs_values"]),
            "dwg_insunits": insunits,
            "drawing_units": None,
            "source_coordinate_scale_to_m": None,
            "source_coordinate_scale_reviewed": False,
        },
        "crs": {
            "source_crs": None,
            "target_crs": None,
            "local_registration_strategy": None,
            "local_registration_reviewed": False,
            "geodata_registration": metadata.get("geodata_registration"),
        },
        "spatial_coverage_policy": None,
        "expectations": {
            "source_inventory": {
                key: int(counts[key])
                for key in ("model_entities", "model_inserts", "model_dimensions")
            },
            "feature_counts": {},
            "annotation_families": {},
            "source_geometry_gates": {},
            "topology_gates": {},
            "segment_gates": {},
            "delivery_counts": {},
        },
    }


def _draft_registry(inventory: Mapping[str, Any]) -> dict[str, Any]:
    source = inventory["source"]
    project_id = f"source-{str(source['sha256'])[:12]}"
    return {
        "schema_version": MAPPING_REGISTRY_SCHEMA_VERSION,
        "project_id": project_id,
        "review": {"status": "draft"},
        "source_binding": {
            "source_sha256": source["sha256"],
            "inventory_sha256": inventory["inventory_sha256"],
        },
        "block_families": {},
        "layers": {},
        "positive_route_layer_regex": "(?!)",
        "field_rules": {},
        "display_label_rules": {},
        "annotation_families": [],
        "decision_rules": {},
        "labels": {},
        "thresholds_native_m": {},
        "policy": {
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        },
        "coverage": {
            "semantics": {"policy": "fail", "allowlist": []},
            "styles": {"policy": "fail", "allowlist": []},
        },
    }


def bootstrap_project(
    *,
    source: str | Path,
    project_dir: str | Path,
    force: bool = False,
    records: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Write a deterministic draft review pack; never mark it reviewed."""

    root = Path(project_dir).expanduser().resolve()
    inventory, scene_graph = _inspect_source_products(source=source, records=records)
    paths = {
        "source_profile": root / "config" / "source_profile.json",
        "mapping_registry": root / "config" / "mapping_registry.json",
        "inventory": root / "review" / "source_inventory.json",
        "unsupported": root / "review" / "unsupported_inventory.json",
        "cad_scene_graph": root / "review" / "cad_scene_graph.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "Project pack already contains managed files; pass force=True to replace only "
            + ", ".join(str(path) for path in existing)
        )

    unsupported = {
        "schema_version": UNSUPPORTED_SCHEMA_VERSION,
        "source": dict(inventory["source"]),
        "inventory_sha256": inventory["inventory_sha256"],
        **dict(inventory["unsupported"]),
        "review_required": True,
    }
    payloads = {
        "source_profile": _draft_profile(inventory),
        "mapping_registry": _draft_registry(inventory),
        "inventory": inventory,
        "unsupported": unsupported,
        "cad_scene_graph": scene_graph.to_dict(),
    }
    for name, path in paths.items():
        _atomic_write_json(path, payloads[name])
    return {
        "schema_version": "cad2gis-project-bootstrap-result-v1",
        "status": "draft",
        "conversion_allowed": False,
        "project_id": payloads["source_profile"]["project_id"],
        "inventory_sha256": inventory["inventory_sha256"],
        "paths": {name: str(path) for name, path in paths.items()},
        "next_actions": [
            "Review source_inventory.json and unsupported_inventory.json.",
            "Declare drawing units and CRS from authoritative evidence.",
            "Create and review deterministic semantic mappings and all expected gates.",
            "Add surveyed GCPs separately when absolute map accuracy is required.",
            "Set both review records to reviewed only with reviewer/provenance metadata.",
        ],
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Required project file is missing: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _resolve_project_config(root: Path, canonical_name: str, suffix: str) -> Path:
    config_dir = root / "config"
    candidates = {
        path.resolve()
        for path in (
            [config_dir / canonical_name]
            + list(config_dir.glob(f"*_{suffix}.json"))
        )
        if path.is_file()
    }
    if not candidates:
        raise ValueError(
            f"Required project config is missing under {config_dir}: {canonical_name}"
        )
    if len(candidates) != 1:
        rendered = ", ".join(str(path) for path in sorted(candidates))
        raise ValueError(f"Project config is ambiguous for {suffix}: {rendered}")
    return next(iter(candidates))


def _reviewed_contract_state(
    profile: SourceProfile,
    registry: MappingRegistry,
) -> tuple[str, bool, Any | None, list[str]]:
    contracts_reviewed = profile.is_reviewed and registry.is_reviewed
    if not contracts_reviewed:
        return (
            "unreviewed",
            False,
            None,
            [
                "Complete the source, unit/CRS, semantic, gate, and unsupported review.",
                "Set both review records to reviewed with reviewer, timestamp, and provenance.",
            ],
        )

    profile.require_reviewed()
    registry.require_reviewed()
    expected_classes = {
        feature_class
        for feature_class, count in profile.expectations.feature_counts.items()
        if count > 0
    }
    configured_classes = (
        set(registry.block_families)
        | set(registry.field_rules)
        | set(registry.display_label_rules)
        | {family.target_class for family in registry.annotation_families}
    )
    if registry.positive_route_layer_regex not in {"", "(?!)"}:
        configured_classes.add("CABLE")
    if registry.layers.get("zpm_boundary"):
        configured_classes.add("ZPM")
    # INFRASTRUCTURE and ZNRO are deterministic delivery derivatives: the
    # total set of reviewed CABLE geometry and the parent zone of reviewed
    # ZPM polygons.  They have no DWG mapping rules by design.
    generated_delivery_classes = {"INFRASTRUCTURE", "ZNRO"}
    unconfigured_classes = (
        expected_classes - configured_classes - generated_delivery_classes
    )
    if unconfigured_classes:
        raise ValueError(
            "Reviewed expectations contain feature classes with no mapping rules: "
            f"{sorted(unconfigured_classes)}"
        )

    from .units import build_unit_crs_contract

    unit_crs_contract = build_unit_crs_contract(
        dwg_insunits=profile.dwg_insunits,
        source_crs=profile.source_crs,
        target_crs=profile.target_crs,
        source_coordinate_scale_to_m=profile.source_coordinate_scale_to_m,
        source_coordinate_scale_reviewed=profile.source_coordinate_scale_reviewed,
        local_registration_strategy=profile.local_registration_strategy,
        local_registration_reviewed=profile.local_registration_reviewed,
    )
    if unit_crs_contract.can_direct_transform:
        return "reviewed_ready", True, unit_crs_contract, []
    return (
        "registration_required",
        False,
        unit_crs_contract,
        ["Run the reviewed authoritative local-registration stage before conversion."],
    )


def _model_space_entities(
    root: Path, inventory: Mapping[str, Any],
) -> list[Any]:
    """Return lightweight entity-like objects with model-space coordinates.

    Reads the DWG via the canonical reader when one is co-located with the
    project root, then filters to ``layout_role == "model"`` records.
    """

    source_sha = inventory.get("source", {}).get("sha256", "")
    search_roots = [root, root.parent, root.parent.parent / "raw"]
    candidates = []
    for search_root in search_roots:
        if not search_root.is_dir():
            continue
        candidates.extend(
            source.resolve()
            for source in sorted(search_root.glob("*.dwg"))
            if _file_sha256(source) == source_sha
        )
    if not candidates:
        return []
    from cad2gis.reader.resolver import extract_records

    records = extract_records(str(candidates[0]))

    class _LightEntity:
        __slots__ = ("points", "centroid")
        def __init__(self, pts, ctr):
            self.points = pts
            self.centroid = ctr

    entities: list[Any] = []
    for rec in records:
        if rec.get("layout_role") != "model":
            continue
        pts = rec.get("points") or []
        ctr = rec.get("centroid") or (0.0, 0.0)
        if pts:
            entities.append(_LightEntity(pts, ctr))
    return entities


def _patch_source_profile_local(profile_path: Path) -> None:
    """Set a source profile to local-engineering-coordinate mode in place."""
    data = _read_json(profile_path)
    crs = data.setdefault("crs", {})
    crs["source_crs"] = None
    crs["local_registration_strategy"] = "gcp_required"
    crs["local_registration_reviewed"] = False
    data["review"] = data.get("review", {})
    data["review"]["status"] = "draft"
    with open(profile_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def validate_project(*, project_dir: str | Path) -> dict[str, Any]:
    """Validate a pack without changing review state or authorizing facts."""

    root = Path(project_dir).expanduser().resolve()
    profile_path = _resolve_project_config(root, "source_profile.json", "source_profile")
    registry_path = _resolve_project_config(
        root, "mapping_registry.json", "mapping_registry"
    )
    inventory_path = root / "review" / "source_inventory.json"
    unsupported_path = root / "review" / "unsupported_inventory.json"

    profile = SourceProfile.load(profile_path)
    registry = MappingRegistry.load(
        registry_path,
        profile.source_sha256,
        require_reviewed=False,
    )

    inventory_exists = inventory_path.is_file()
    unsupported_exists = unsupported_path.is_file()
    if inventory_exists != unsupported_exists:
        missing = unsupported_path if inventory_exists else inventory_path
        raise ValueError(f"Required project file is missing: {missing}")
    if not inventory_exists:
        if not (profile.is_legacy and registry.is_legacy):
            raise ValueError(f"Required project file is missing: {inventory_path}")
        matching_sources = [
            source.resolve()
            for source in sorted(root.glob("*.dwg"))
            if _file_sha256(source) == profile.source_sha256
        ]
        if not matching_sources:
            raise ValueError(
                "Legacy project validation requires a project-root DWG matching "
                f"the reviewed source SHA-256 {profile.source_sha256}"
            )
        if profile.source_size_bytes is not None and any(
            source.stat().st_size != profile.source_size_bytes
            for source in matching_sources
        ):
            raise ValueError("Legacy project DWG size differs from its reviewed profile")
        status, conversion_allowed, unit_crs_contract, next_actions = (
            _reviewed_contract_state(profile, registry)
        )
        if status == "reviewed_ready":
            status = "reviewed_ready_legacy_compatibility"
        return {
            "schema_version": "cad2gis-project-validation-result-v1",
            "status": status,
            "valid": True,
            "conversion_allowed": conversion_allowed,
            "compatibility_mode": "legacy-source-bound",
            "project_id": profile.project_id or None,
            "source_sha256": profile.source_sha256,
            "source_paths": [str(path) for path in matching_sources],
            "inventory_sha256": None,
            "review": {
                "source_profile": profile.review.status,
                "mapping_registry": registry.review.status,
            },
            "unit_crs_contract": (
                None
                if unit_crs_contract is None
                else unit_crs_contract.to_manifest_dict()
            ),
            "unsupported_record_count": None,
            "warnings": [
                "Legacy reviewed compatibility pack has no onboarding inventory "
                "sidecars; bootstrap a new project pack for every other CAD source."
            ],
            "next_actions": next_actions,
        }

    inventory = _read_json(inventory_path)
    if inventory.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise ValueError("Unsupported source inventory schema")
    stored_inventory_hash = str(inventory.get("inventory_sha256", ""))
    actual_inventory_hash = inventory_sha256(inventory)
    if stored_inventory_hash != actual_inventory_hash:
        raise ValueError(
            "Source inventory hash mismatch: expected "
            f"{stored_inventory_hash}, got {actual_inventory_hash}"
        )
    unsupported = _read_json(unsupported_path)
    if unsupported.get("schema_version") != UNSUPPORTED_SCHEMA_VERSION:
        raise ValueError("Unsupported unsupported-inventory schema")
    if unsupported.get("inventory_sha256") != actual_inventory_hash:
        raise ValueError("Unsupported inventory is stale or bound to another source inventory")
    unsupported_facts = inventory.get("unsupported", {})
    for key in ("by_reason", "by_entity_type", "entity_keys_by_reason"):
        if unsupported.get(key) != unsupported_facts.get(key):
            raise ValueError(
                f"Unsupported inventory {key} differs from the source inventory"
            )
    if unsupported.get("source") != inventory.get("source"):
        raise ValueError("Unsupported inventory source binding is stale")

    source_binding = inventory.get("source", {})
    failures: list[str] = []
    if profile.source_sha256 != source_binding.get("sha256"):
        failures.append("project profile source hash differs from source inventory")
    if profile.source_size_bytes != source_binding.get("size_bytes"):
        failures.append("project profile source size differs from source inventory")
    if profile.inventory_sha256 != actual_inventory_hash:
        failures.append("project profile inventory binding is stale")
    if registry.inventory_sha256 != actual_inventory_hash:
        failures.append("mapping registry inventory binding is stale")
    if profile.project_id != registry.project_id:
        failures.append("project profile and mapping registry project_id values differ")
    if not profile.is_legacy and profile.plan_layouts:
        inventory_layouts = {
            str(name).casefold() for name in inventory.get("layouts", {})
        }
        missing_layouts = [
            name
            for name in profile.plan_layouts
            if name.casefold() not in inventory_layouts
        ]
        if missing_layouts:
            failures.append(
                "plan_layouts declares layouts missing from the source "
                f"inventory: {missing_layouts}"
            )
    if failures:
        raise ValueError("Invalid project bindings: " + "; ".join(failures))

    warnings: list[str] = []
    plan_domain_summary = inventory.get("plan_domain")
    if isinstance(plan_domain_summary, Mapping):
        issue_codes = plan_domain_summary.get("issue_codes")
        if isinstance(issue_codes, Mapping):
            orphan_count = int(
                issue_codes.get("orphan_block_definition", 0) or 0
            )
            if orphan_count:
                warnings.append(
                    f"{orphan_count} orphan block definition(s) hold entities "
                    "unreachable from selected roots; explicitly review "
                    "plan_domain.include_orphan_blocks."
                )
            undeclared_count = int(
                issue_codes.get("undeclared_layout_entities", 0) or 0
            )
            if undeclared_count:
                warnings.append(
                    "Paper-space layouts outside plan_layouts remain "
                    "evidence-only and are not converted."
                )

    # ── Coordinate-domain plausibility gate ───────────────────────────────
    # A DWG ``CGEOCS`` declaration does not prove that entity WCS coordinates
    # already occupy that CRS.  When coordinates fall outside the declared CRS
    # area of use, the profile is patched to local-engineering mode and must
    # be re-validated after GCP registration.
    if profile.source_crs and profile.source_crs not in {"local", "LOCAL"}:
        from .coordinate_domain import assess_coordinate_domain

        entities = _model_space_entities(root, inventory)
        if profile.geodata_registration is not None:
            from .geodata import local_to_crs_point

            class _RegisteredEntity:
                __slots__ = ("centroid", "points")

                def __init__(self, entity: Any):
                    self.points = tuple(
                        local_to_crs_point(
                            point, profile.geodata_registration,
                        )
                        for point in entity.points
                    )
                    self.centroid = local_to_crs_point(
                        entity.centroid, profile.geodata_registration,
                    )

            entities = [_RegisteredEntity(entity) for entity in entities]
        domain = assess_coordinate_domain(entities, profile.source_crs)

        # EPSG:3857 is global — any local coordinates pass the area-of-use
        # check.  Fall back to a magnitude heuristic: real Web Mercator
        # coordinates for inhabited land are never within ±50 000 m of the
        # origin.
        if (
            domain["status"] == "PLAUSIBLE_DECLARED_CRS_DOMAIN"
            and domain["point_count"] > 0
        ):
            extent = domain.get("observed_extent", {})
            max_abs = max(
                abs(extent.get("min_x", 0)), abs(extent.get("max_x", 0)),
                abs(extent.get("min_y", 0)), abs(extent.get("max_y", 0)),
            )
            if max_abs < 100_000:
                domain["status"] = "LOCAL_OR_MISREGISTERED_COORDINATES"
                domain["passed"] = False
                domain["inside_fraction"] = 0.0
                domain["failures"].append(
                    "Drawing coordinate magnitude is implausible for the "
                    f"declared CRS (max absolute coordinate {max_abs:.0f} m)."
                )

        if domain["status"] == "LOCAL_OR_MISREGISTERED_COORDINATES":
            status = "registration_required"
            conversion_allowed = False
            return {
                "schema_version": "cad2gis-project-validation-result-v1",
                "status": status,
                "valid": True,
                "conversion_allowed": conversion_allowed,
                "project_id": profile.project_id,
                "source_sha256": profile.source_sha256,
                "inventory_sha256": actual_inventory_hash,
                "review": {
                    "source_profile": profile.review.status,
                    "mapping_registry": registry.review.status,
                },
                "coordinate_domain": domain,
                "warnings": warnings,
                "next_actions": [
                    "Drawing coordinates do not occupy the declared CRS domain.",
                    "Supply reviewed DWG GEODATA or an accepted surveyed GCP profile.",
                    "OSM place-name results are relative candidates and cannot authorize delivery.",
                ],
            }

    status, conversion_allowed, unit_crs_contract, next_actions = (
        _reviewed_contract_state(profile, registry)
    )
    return {
        "schema_version": "cad2gis-project-validation-result-v1",
        "status": status,
        "valid": True,
        "conversion_allowed": conversion_allowed,
        "project_id": profile.project_id,
        "source_sha256": profile.source_sha256,
        "inventory_sha256": actual_inventory_hash,
        "review": {
            "source_profile": profile.review.status,
            "mapping_registry": registry.review.status,
        },
        "unit_crs_contract": (
            None if unit_crs_contract is None
            else unit_crs_contract.to_manifest_dict()
        ),
        "unsupported_record_count": int(
            inventory.get("counts", {}).get("unsupported_records", 0)
        ),
        "warnings": warnings,
        "next_actions": next_actions,
    }


__all__ = [
    "INVENTORY_SCHEMA_VERSION",
    "UNSUPPORTED_SCHEMA_VERSION",
    "bootstrap_project",
    "build_source_inventory",
    "inspect_source",
    "inventory_sha256",
    "validate_project",
]
