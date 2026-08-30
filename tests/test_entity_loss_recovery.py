"""Integration tests for entity-loss accounting and reviewed recovery.

Synthetic fixtures (always run) cover the logic assertions.  The real-DWG
acceptance case is opt-in via ``CAD2GIS_FULL_DWG_TESTS`` (same convention as
``tests/test_apd_test_compatibility.py``) and reads the user-supplied
``APD_test`` corpus directly; it is skipped by default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.plan_domain import build_plan_domain
from cad2gis.cad2gis_v3.semantics import classify_entities


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = ROOT.parent / "APD_test"
SF_DWG_NAME = "APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg"
SF_ROUTE_LAYER_REGEX = (
    r"(?i)^(?:DROP\ DUCT|FEEDER|FO\ 12\ CORE|FO\ 144\ CORE|FO\ 24\ CORE"
    r"|FO\ 24\ CORE\ LINE\ C\ \-\ FDT\ 1|FO\ 24\ CORE\ LINE\ C\ \-\ FDT\ 2"
    r"|FO\ 288\ CORE|FO\ 36\ CORE|FO\ 36\ CORE\.|FO\ 48\ CORE|FO\ 72\ CORE"
    r"|FO\ 96\ CORE)$"
)
FULL_READER_ENV = "CAD2GIS_FULL_DWG_TESTS"
SOURCE_HASH = "c" * 64
ROUTE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE"}


def _dataset_root() -> Path:
    configured = os.environ.get("CAD2GIS_TEST_DATASET_ROOT")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else DEFAULT_DATASET_ROOT.resolve()
    )


def _full_reader_enabled() -> bool:
    return os.environ.get(FULL_READER_ENV, "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _registry(route_regex: str) -> SimpleNamespace:
    return SimpleNamespace(
        positive_route_layer_regex=route_regex,
        block_families={},
        insert_layer_families={},
        layers={},
        field_rules={},
        display_label_rules={},
    )


def _entity(
    key: str,
    *,
    kind: str,
    layout: str = "Model",
    layout_role: str = "model",
    cad_role: str = "model",
    block_name: str = "",
    layer: str = "0",
    points: tuple[tuple[float, float], ...] = ((0.0, 0.0), (1.0, 0.0)),
    raw_properties: dict | None = None,
    native_length: float | None = None,
) -> SourceEntity:
    return SourceEntity.from_record({
        "entity_key": key,
        "source_sha256": SOURCE_HASH,
        "source_file": "fixture.dwg",
        "handle": key,
        "layout": layout,
        "layout_role": layout_role,
        "cad_role": cad_role,
        "layer": layer,
        "object_name": f"ACDB{kind}",
        "dwg_type_name": kind,
        "points": points,
        "centroid": points[0] if points else (0.0, 0.0),
        "closed": False,
        "text": "",
        "block_name": block_name,
        "block_attributes": {},
        "raw_properties": raw_properties or {},
        "native_length": native_length,
    })


def _transform(insertion: tuple[float, float, float]) -> dict:
    return {
        "transform_facts": {
            "insertion_point": insertion,
            "block_base_point": (0.0, 0.0, 0.0),
            "scale": (1.0, 1.0, 1.0),
            "rotation": 0.0,
            "normal": (0.0, 0.0, 1.0),
            "extrusion": (0.0, 0.0, 1.0),
        }
    }


def _base_point() -> dict:
    return {"transform_facts": {"block_base_point": (0.0, 0.0, 0.0)}}


def _inventory() -> list[SourceEntity]:
    return [
        _entity("model-anchor", kind="LINE"),
        _entity(
            "orphan-cable",
            kind="LWPOLYLINE",
            layout="BLOCKDEF:OUTER",
            layout_role="block_definition",
            cad_role="block_definition",
            layer="CABLE ROUTE",
            points=((0.0, 0.0), (3.0, 0.0), (3.0, 4.0)),
            raw_properties=_base_point(),
            native_length=7.0,
        ),
        _entity(
            "orphan-insert",
            kind="INSERT",
            layout="BLOCKDEF:OUTER",
            layout_role="block_definition",
            cad_role="block_definition",
            block_name="INNER",
            points=((10.0, 0.0),),
            raw_properties=_transform((10.0, 0.0, 0.0)),
        ),
        _entity(
            "inner-cable",
            kind="LWPOLYLINE",
            layout="BLOCKDEF:INNER",
            layout_role="block_definition",
            cad_role="block_definition",
            layer="CABLE ROUTE",
            points=((0.0, 0.0), (2.0, 0.0)),
            raw_properties=_base_point(),
            native_length=2.0,
        ),
        _entity(
            "rescued-cable",
            kind="LWPOLYLINE",
            layer="CABLE ROUTE",
            cad_role="style_legend",
            points=((20.0, 0.0), (25.0, 0.0)),
            raw_properties={"cad_role_original": "model"},
            native_length=5.0,
        ),
        _entity(
            "paper-cable",
            kind="LWPOLYLINE",
            layout="APD - SF",
            layout_role="layout",
            cad_role="layout",
            layer="CABLE ROUTE",
            points=((30.0, 0.0), (35.0, 0.0)),
            native_length=5.0,
        ),
    ]


def _cable_features(view, route_regex: str):
    features, _, _, _ = classify_entities(
        list(view.entities),
        _registry(route_regex),
        coverage_policy="warn",
        coverage_allowlist=[],
    )
    return [feature for feature in features if feature.feature_class == "CABLE"]


def test_reviewed_recovery_restores_all_three_loss_buckets() -> None:
    view = build_plan_domain(
        _inventory(),
        route_layer_pattern=re.compile(r"(?i)CABLE"),
        plan_layouts=("APD - SF",),
        include_orphan_blocks="*",
    )

    cables = _cable_features(view, r"(?i)CABLE")
    cable_keys = {feature.source_entity_key for feature in cables}

    assert len(cables) == 4
    assert "rescued-cable" in cable_keys
    assert "paper-cable" in cable_keys
    derived_keys = {key for key in cable_keys if key.startswith("plan:")}
    assert len(derived_keys) == 2
    entity_by_key = {entity.entity_key: entity for entity in view.entities}
    for key in derived_keys:
        assert entity_by_key[key].raw_properties["provenance"] == {
            "orphan_block_recovery": "OUTER",
        }
    assert view.diagnostics["orphan_recovery"]["recovered"] == ["OUTER"]
    assert view.diagnostics["orphan_member_entity_keys"] == []
    assert view.diagnostics["plan_layouts"]["admitted"] == ["APD - SF"]
    assert view.diagnostics["route_layer_exemption"]["exempted_count"] == 1


def test_default_configuration_drops_the_same_entities_silently() -> None:
    view = build_plan_domain(_inventory())

    assert {entity.entity_key for entity in view.entities} == {"model-anchor"}
    assert _cable_features(view, r"(?i)CABLE") == []
    assert view.diagnostics["orphan_blocks"][0]["block_name"] == "OUTER"
    assert "APD - SF" in view.diagnostics["plan_layouts"]["undeclared"]


@pytest.mark.skipif(
    not _full_reader_enabled(),
    reason=(
        "Real-DWG recovery acceptance is opt-in; set "
        "CAD2GIS_FULL_DWG_TESTS=1 (requires AutoCAD)"
    ),
)
def test_sf_real_drawing_orphan_recovery_acceptance() -> None:
    """Real -SF acceptance through build_plan_domain -> classify_entities.

    pipeline.convert is not used here: the acceptance targets plan-domain and
    semantic classification without run-directory/publication side effects.

    Pre-fix reconciliation baseline (docs/specs entity-loss design): exactly
    one route-layer entity survived to delivery; 38 orphan block members
    (+16 TEXT), 28 legend-reclassified, 7 title_block-reclassified, and 7
    paper-layout route entities were silently dropped.
    """

    from cad2gis.reader.autocad import extract_dwg_records

    source = _dataset_root() / SF_DWG_NAME
    if not source.is_file():
        pytest.skip(f"APD_test dataset not available: {source}")
    inventory = extract_dwg_records(source)
    entities = [
        SourceEntity.from_record(dict(record)) for record in inventory
    ]

    view = build_plan_domain(
        entities,
        route_layer_pattern=re.compile(SF_ROUTE_LAYER_REGEX),
        plan_layouts=("APD - SF",),
        include_orphan_blocks="*",
    )
    diagnostics = view.diagnostics

    assert diagnostics["orphan_recovery"]["recovered"]
    recovered_names = {
        name.casefold() for name in diagnostics["orphan_recovery"]["recovered"]
    }
    # The cable-bearing orphan roots identified by the pre-fix diagnosis must
    # all be recovered; anonymous dimension blocks (*D*) legitimately skip
    # fail-closed because no member carries a block base point.
    assert {"sfsfsfs", "xvcxvxvxcv", "zcczczc", "fsfsfsfsfsf"} <= recovered_names
    assert {
        item["reason"] for item in diagnostics["orphan_recovery"]["skipped"]
    } <= {
        "unknown_block_definition",
        "not_orphan_block_definition",
        "covered_by_recovered_root",
        "block_base_point_unavailable",
        "block_base_point_not_origin",
        "unsupported_block_geometry_transform",
    }
    route_entities = [
        entity
        for entity in view.entities
        if re.search(SF_ROUTE_LAYER_REGEX, entity.layer)
        and entity.dwg_type.upper() in ROUTE_TYPES
    ]
    # Observed on the real drawing: 68 route entities enter the semantic
    # domain with recovery configured, versus exactly 1 without it.
    assert len(route_entities) > 40
    assert "APD - SF" not in diagnostics["plan_layouts"]["undeclared"]

    cables = _cable_features(view, SF_ROUTE_LAYER_REGEX)
    assert len(cables) > 40

    default_view = build_plan_domain(entities)
    default_cables = _cable_features(default_view, SF_ROUTE_LAYER_REGEX)
    # Pre-fix baseline behaviour is preserved when nothing is configured.
    assert len(default_cables) == 1
