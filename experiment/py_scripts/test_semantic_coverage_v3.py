"""Semantic/style observability must be deterministic and fail closed."""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis_v3.evidence import write_evidence
from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.model import CadStyle, SourceEntity
from cad2gis_v3.semantics import (
    CoverageGateError,
    build_coverage_report,
    classify_entities,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"
REGISTRY = ROOT / "config" / "apd_mapping_registry.json"


def _registry():
    profile = SourceProfile.load(PROFILE)
    return MappingRegistry.load(REGISTRY, profile.source_sha256)


def _entity(
    key: str,
    dwg_type: str,
    layer: str,
    points=(),
    *,
    block_name: str = "",
    text: str = "",
) -> SourceEntity:
    points = tuple(points)
    centroid = points[0] if points else (0.0, 0.0)
    return SourceEntity(
        entity_key=key,
        source_sha256="x",
        source_file="coverage.dwg",
        handle=key,
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer=layer,
        object_name="",
        dwg_type=dwg_type,
        points=points,
        centroid=centroid,
        closed=False,
        text=text,
        block_name=block_name,
        block_attributes={},
        style=CadStyle(aci_color=7, linetype="Continuous"),
    )


def test_reviewed_route_line_is_cable_but_other_line_is_only_coverage():
    route_layer = "Cable Line A (FO Cable 24C_2T)"
    entities = [
        _entity("ROUTE", "LINE", route_layer, ((0.0, 0.0), (2.0, 0.0))),
        _entity("OTHER", "LINE", "ROAD CENTER", ((0.0, 1.0), (2.0, 1.0))),
    ]

    features, _, _, diagnostics = classify_entities(
        entities, _registry(), coverage_policy="abstain",
    )

    assert [(item.source_entity_key, item.feature_class) for item in features] == [
        ("ROUTE", "CABLE"),
    ]
    coverage = diagnostics["coverage"]
    assert coverage["schema_version"] == "cad2gis-semantic-coverage-v1"
    assert coverage["status"] == "WATCH"
    assert coverage["conversion_allowed"] is True
    assert coverage["by_reason"] == {"unmatched_route_layer": 1}
    assert coverage["records"][0] == {
        "source_entity_key": "OTHER",
        "reason": "unmatched_route_layer",
        "candidate_class": "CABLE",
        "source_layer": "ROAD CENTER",
        "dwg_type": "LINE",
        "source_handle": "OTHER",
        "block_name": "",
        "allowlisted": False,
        "action": "abstain",
    }


def test_unknown_insert_and_missing_points_fail_closed_with_payload():
    entity = _entity("UNKNOWN", "INSERT", "ASSET", block_name="MYSTERY")

    with pytest.raises(CoverageGateError) as captured:
        classify_entities([entity], _registry(), coverage_policy="fail")

    coverage = captured.value.coverage
    assert coverage["status"] == "FAIL"
    assert coverage["conversion_allowed"] is False
    assert coverage["by_reason"] == {
        "missing_geometry_points": 1,
        "unknown_insert_block": 1,
    }
    assert {item["action"] for item in coverage["records"]} == {"fail"}


def test_structured_allowlist_is_reviewable_and_case_insensitive():
    entity = _entity(
        "UNKNOWN", "INSERT", "LEGACY SYMBOLS", ((1.0, 2.0),),
        block_name="VENDOR_X",
    )
    _, _, _, diagnostics = classify_entities(
        [entity],
        _registry(),
        coverage_policy="fail",
        coverage_allowlist=[{
            "reason": "unknown_insert_block",
            "source_layer": "legacy*",
            "block_name": "vendor_*",
        }],
    )

    coverage = diagnostics["coverage"]
    assert coverage["status"] == "PASS"
    assert coverage["passed"] is True
    assert coverage["records"][0]["action"] == "allowlist"


def test_empty_cross_cad_semantic_configuration_abstains_without_guessing():
    draft_registry = SimpleNamespace(
        block_families={}, layers={}, positive_route_layer_regex="",
        field_rules={}, display_label_rules={}, annotation_families=(),
        decision_rules={}, labels={}, is_reviewed=False,
    )
    entity = _entity(
        "TEXT", "TEXT", "VENDOR LABELS", ((1.0, 2.0),), text="ABC-100",
    )

    features, relations, unresolved, diagnostics = classify_entities(
        [entity], draft_registry, coverage_policy="abstain",
    )

    assert features == []
    assert relations == []
    assert unresolved == []
    assert diagnostics["coverage"]["by_reason"] == {
        "unreviewed_annotation_carrier": 1,
    }


def test_semantic_coverage_contract_is_persisted_in_evidence(tmp_path):
    entity = _entity(
        "UNKNOWN", "INSERT", "LEGACY SYMBOLS", ((1.0, 2.0),),
        block_name="VENDOR_X",
    )
    features, relations, unresolved, semantic_diagnostics = classify_entities(
        [entity], _registry(), coverage_policy="abstain",
    )
    style_coverage = build_coverage_report(
        [{
            "source_entity_key": "UNKNOWN",
            "reason": "unsupported_linetype",
            "candidate_class": "CABLE",
            "source_layer": "LEGACY SYMBOLS",
            "dwg_type": "",
            "source_handle": "UNKNOWN",
            "linetype": "VENDOR_PATTERN",
        }],
        schema_version="cad2gis-style-coverage-v1",
        policy="abstain",
    )
    path = tmp_path / "coverage.gpkg"
    write_evidence(
        path, [entity], features, relations, unresolved,
        {
            "semantics": semantic_diagnostics,
            "styles": {"coverage": style_coverage},
        },
        DirectTransformer("EPSG:3857", "EPSG:9481").source,
    )

    with sqlite3.connect(path) as connection:
        summary = connection.execute(
            "SELECT domain, schema_version, policy, status, "
            "passed, conversion_allowed FROM coverage_summaries "
            "WHERE domain='semantics'"
        ).fetchone()
        record = connection.execute(
            "SELECT source_entity_key, reason, candidate_class, source_layer, "
            "dwg_type, action, allowlisted FROM coverage_records "
            "WHERE domain='semantics'"
        ).fetchone()
        style_record = connection.execute(
            "SELECT reason, action FROM coverage_records WHERE domain='styles'"
        ).fetchone()
    assert summary == (
        "semantics", "cad2gis-semantic-coverage-v1", "abstain", "WATCH", 0, 1,
    )
    assert record == (
        "UNKNOWN", "unknown_insert_block", "UNMAPPED_INSERT",
        "LEGACY SYMBOLS", "INSERT", "abstain", 0,
    )
    assert style_record == ("unsupported_linetype", "abstain")


def test_evidence_validation_error_closes_ogr_handle_and_removes_stage(tmp_path):
    route = _entity(
        "ROUTE", "LINE", "Cable Line A (FO Cable 24C_2T)",
        ((0.0, 0.0), (2.0, 0.0)),
    )
    features, relations, unresolved, diagnostics = classify_entities(
        [route], _registry(), coverage_policy="warn",
    )
    destination = tmp_path / "invalid.gpkg"

    with pytest.raises(
        RuntimeError,
        match="Evidence requires one span metric per CABLE segment",
    ):
        write_evidence(
            destination, [route], features, relations, unresolved,
            {"semantics": diagnostics},
            DirectTransformer("EPSG:3857", "EPSG:9481").source,
        )

    assert not destination.exists()
    assert list(tmp_path.glob(".invalid.gpkg.*")) == []


def test_connection_port_structured_diagnostics_are_persisted_canonically(tmp_path):
    source = _entity(
        "SOURCE", "LINE", "GRAPHIC", ((0.0, 0.0), (1.0, 0.0)),
    )
    provenance = {
        "rotation": "reader_raw_properties",
        "block_base_point": "reader_raw_properties",
    }
    diagnostic = {
        "severity": "blocking",
        "code": "block_footprint_unavailable",
        "blocking": True,
        "reasons": [{
            "missing_facts": ["block_base_point", "normal"],
            "code": "missing_block_transform_facts",
        }],
    }
    candidate = {
        "route_key": "route",
        "route_source_handle": "R1",
        "vertex_index": 0,
        "asset_key": "asset",
        "asset_source_handle": "A1",
        "block_name": "SYMBOL",
        "attachment_kind": "support_port",
        "route_point_native": [1.0, 2.0],
        "port_point_native": None,
        "center_distance_m": 0.25,
        "footprint_distance_m": None,
        "status": "abstain_block_footprint",
        "transform_basis": "explicit_reader_insert_transform_only",
        "transform_fact_provenance": provenance,
        "diagnostic": diagnostic,
    }
    path = tmp_path / "port-diagnostics.gpkg"

    write_evidence(
        path, [source], [], [], [],
        {"topology": {"connection_port_candidates": [candidate]}},
        DirectTransformer("EPSG:3857", "EPSG:9481").source,
    )

    with sqlite3.connect(path) as connection:
        stored_provenance, stored_diagnostic = connection.execute(
            "SELECT transform_fact_provenance, diagnostic "
            "FROM connection_port_candidates"
        ).fetchone()
    assert stored_provenance == json.dumps(
        provenance, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    assert stored_diagnostic == json.dumps(
        diagnostic, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    assert json.loads(stored_provenance) == provenance
    assert json.loads(stored_diagnostic) == diagnostic
