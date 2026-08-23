"""Architecture-level contracts that keep conversion source-bound and loss-aware.

These tests intentionally use small in-memory records and the public stage
boundaries.  They do not start AutoCAD, QGIS, GDAL, or an external model.  A
different DWG must receive a different reviewed profile; unsupported CAD facts
must remain visible as evidence; and ambiguous spatial/semantic information
must stop publication rather than being silently guessed.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "src"
SRC = ROOT / "src"
APD_SOURCE_PROFILE = ROOT / "baselines" / "hutabohu" / "config" / "source_profile.json"
APD_MAPPING = ROOT / "baselines" / "hutabohu" / "config" / "mapping_registry.json"
LEGACY_READCAD_DWG_NAME = "APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"
LEGACY_READCAD_DWG_SHA = "557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557"


def _canonical_module(name: str):
    """Prefer this checkout over an unrelated globally installed cad2gis."""

    src_text = str(SRC)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
    else:
        sys.path.remove(src_text)
        sys.path.insert(0, src_text)
    package = sys.modules.get("cad2gis")
    if package is not None:
        package_file = getattr(package, "__file__", None)
        if package_file is None or not Path(package_file).resolve().as_posix().startswith(
            SRC.resolve().as_posix()
        ):
            for loaded in list(sys.modules):
                if loaded == "cad2gis" or loaded.startswith("cad2gis."):
                    sys.modules.pop(loaded, None)
    return importlib.import_module(name)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_entity(
    model,
    key: str,
    *,
    kind: str,
    layer: str = "UNKNOWN",
    points=((0.0, 0.0), (1.0, 0.0)),
    block_name: str = "",
    layout: str = "Model",
    raw_properties: dict | None = None,
    style=None,
):
    points = tuple(tuple(point) for point in points)
    return model.SourceEntity.from_record(
        {
            "entity_key": key,
            "source_sha256": "a" * 64,
            "source_file": "fixture.dwg",
            "handle": key,
            "layout": layout,
            "layout_role": "model" if layout.casefold() == "model" else "block_definition",
            "cad_role": "model" if layout.casefold() == "model" else "block_definition",
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
            "style": style,
        }
    )


def _draft_profile(source: Path) -> dict:
    payload = source.read_bytes()
    digest = _sha256_bytes(payload)
    return {
        "schema_version": "cad2gis-project-profile-v1",
        "project_id": "draft-fixture",
        "review": {
            "status": "draft",
            "reviewed_by": "",
            "reviewed_at": "",
            "provenance": "",
        },
        "source_binding": {
            "source_sha256": digest,
            "source_size_bytes": len(payload),
            "inventory_sha256": _sha256_bytes(b"inventory"),
        },
        "drawing": {
            "dwg_cgeocs": None,
            "dwg_insunits": None,
            "drawing_units": None,
        },
        "crs": {"source_crs": None, "target_crs": None},
        "spatial_coverage_policy": None,
        "expectations": {
            "source_inventory": {},
            "feature_counts": {},
            "annotation_families": {},
            "source_geometry_gates": {},
            "topology_gates": {},
            "segment_gates": {},
            "delivery_counts": {},
        },
    }


def test_reviewed_profile_and_mapping_registry_are_bound_to_one_source_hash(tmp_path: Path):
    """A reviewed APD pack cannot be reused for a second DWG byte stream."""

    config = _canonical_module("cad2gis.cad2gis_v3.config")
    profile_payload = _json(APD_SOURCE_PROFILE)
    profile = config.SourceProfile.load(APD_SOURCE_PROFILE)
    registry_payload = _json(APD_MAPPING)
    assert profile.source_sha256 == registry_payload["source_binding"]["source_sha256"]
    registry = config.MappingRegistry.load(APD_MAPPING, profile.source_sha256)
    assert registry.source_sha256 == profile.source_sha256

    # The archived readcad review bundle belongs to the same historical DWG
    # family; its identity metadata must stay parseable, and the real binding
    # guarantee is exercised below by mutating the profile hash.
    readcad_bundle = {
        "source": {
            "dwg_name": LEGACY_READCAD_DWG_NAME,
            "dwg_sha256": LEGACY_READCAD_DWG_SHA,
        }
    }
    assert readcad_bundle["source"]["dwg_sha256"] == profile.source_sha256

    first_source = tmp_path / "synthetic-reviewed-source.dwg"
    first_source.write_bytes(b"synthetic reviewed source fixture")
    profile_payload["source_binding"]["source_sha256"] = _sha256_bytes(first_source.read_bytes())
    profile_payload["source_binding"]["source_size_bytes"] = len(first_source.read_bytes())
    synthetic_profile_path = _write_json(tmp_path / "synthetic_profile.json", profile_payload)
    synthetic_profile = config.SourceProfile.load(synthetic_profile_path)
    assert synthetic_profile.validate_source(first_source) == synthetic_profile.source_sha256

    second_source = tmp_path / "same-layout-different-hash.dwg"
    second_source.write_bytes(first_source.read_bytes() + b"\nCAD2GIS-SECOND-SOURCE")
    with pytest.raises(ValueError, match="Source hash mismatch"):
        synthetic_profile.validate_source(second_source)

    stale_mapping = _json(APD_MAPPING)
    stale_mapping["source_binding"]["source_sha256"] = _sha256_bytes(b"different-reviewed-dwg")
    stale_mapping_path = _write_json(tmp_path / "stale_mapping.json", stale_mapping)
    with pytest.raises(ValueError, match="stale|different DWG"):
        config.MappingRegistry.load(stale_mapping_path, profile.source_sha256)


def test_draft_profile_is_rejected_before_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bootstrap output is evidence-only until a human review changes its state."""

    config = _canonical_module("cad2gis.cad2gis_v3.config")
    pipeline = _canonical_module("cad2gis.cad2gis_v3.pipeline")
    source = tmp_path / "draft.dwg"
    source.write_bytes(b"not-a-real-dwg-but-a-source-bound-fixture")
    profile_path = _write_json(tmp_path / "source_profile.json", _draft_profile(source))
    profile = config.SourceProfile.load(profile_path)
    with pytest.raises(ValueError, match="reviewed state|draft"):
        profile.require_reviewed()

    # The gate must run before the expensive reader.  If a future refactor
    # moves it, this sentinel turns silent/partial conversion into a test
    # failure rather than launching AutoCAD in CI.
    monkeypatch.setattr(
        pipeline.MappingRegistry,
        "load",
        staticmethod(lambda *_args, **_kwargs: SimpleNamespace()),
    )
    monkeypatch.setattr(
        pipeline,
        "ingest",
        lambda *_args, **_kwargs: pytest.fail("draft profile reached CAD ingest"),
    )
    request = pipeline.ConversionRequest(
        source=source,
        run_dir=tmp_path / "run",
        source_profile=profile_path,
        mapping_registry=tmp_path / "mapping.json",
    )
    with pytest.raises(ValueError, match="reviewed state|draft"):
        pipeline.convert(request)


def test_bootstrap_project_pack_reports_draft_and_cannot_convert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The real onboarding pack remains review input, never a runnable profile."""

    onboarding = _canonical_module("cad2gis.cad2gis_v3.project_profile")
    pipeline = _canonical_module("cad2gis.cad2gis_v3.pipeline")
    source = tmp_path / "onboarding.dwg"
    source.write_bytes(b"source inventory fixture")
    project_dir = tmp_path / "project"
    result = onboarding.bootstrap_project(
        source=source, project_dir=project_dir, records=[]
    )
    assert result["status"] == "draft"
    assert result["conversion_allowed"] is False
    validation = onboarding.validate_project(project_dir=project_dir)
    assert validation["status"] == "unreviewed"
    assert validation["conversion_allowed"] is False
    assert validation["review"] == {"source_profile": "draft", "mapping_registry": "draft"}

    monkeypatch.setattr(
        pipeline,
        "ingest",
        lambda *_args, **_kwargs: pytest.fail("draft bootstrap pack reached CAD ingest"),
    )
    with pytest.raises(ValueError, match="reviewed|draft"):
        pipeline.convert(
            pipeline.ConversionRequest(
                source=source,
                run_dir=tmp_path / "run",
                source_profile=project_dir / "config" / "source_profile.json",
                mapping_registry=project_dir / "config" / "mapping_registry.json",
            )
        )


def test_unknown_semantics_are_structured_coverage_not_silent_drop():
    semantics = _canonical_module("cad2gis.cad2gis_v3.semantics")
    model = _canonical_module("cad2gis.cad2gis_v3.model")
    config = _canonical_module("cad2gis.cad2gis_v3.config")
    profile = config.SourceProfile.load(APD_SOURCE_PROFILE)
    registry = config.MappingRegistry.load(APD_MAPPING, profile.source_sha256)

    unknown_block = _source_entity(
        model, "unknown-insert", kind="INSERT", block_name="VENDOR_UNKNOWN_SYMBOL",
        layer="PTECH",
    )
    unmatched_line = _source_entity(
        model, "unmatched-line", kind="LINE", layer="VENDOR_LINEWORK",
    )
    features, _relations, unresolved, diagnostics = semantics.classify_entities(
        [unknown_block, unmatched_line], registry, coverage_policy="abstain",
    )
    assert features == []
    coverage = diagnostics["coverage"]
    assert coverage["status"] == "WATCH"
    assert coverage["conversion_allowed"] is True
    reasons = {record["reason"] for record in coverage["records"]}
    assert "unknown_insert_block" in reasons
    assert "unmatched_route_layer" in reasons
    assert unresolved or coverage["records"]

    with pytest.raises(semantics.CoverageGateError) as error:
        semantics.classify_entities(
            [unknown_block], registry, coverage_policy="fail",
        )
    assert error.value.coverage["status"] == "FAIL"
    assert error.value.coverage["records"]


def test_closed_route_on_zpm_boundary_layer_becomes_polygon_feature():
    semantics = _canonical_module("cad2gis.cad2gis_v3.semantics")
    model = _canonical_module("cad2gis.cad2gis_v3.model")
    config = _canonical_module("cad2gis.cad2gis_v3.config")
    profile = config.SourceProfile.load(
        ROOT / "baselines" / "lamteh_main" / "config" / "source_profile.json"
    )
    registry = config.MappingRegistry.load(
        ROOT / "baselines" / "lamteh_main" / "config" / "mapping_registry.json",
        profile.source_sha256,
    )
    assert "FAT AREA" in registry.layers.get("zpm_boundary", ())

    ring = _source_entity(
        model, "zpm-ring", kind="LWPOLYLINE", layer="FAT AREA",
        points=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)),
        style=model.CadStyle(aci_color=134, true_color="#009999"),
    )
    # Reviewed boundary outlines are polygon-closed at the warehouse even
    # when the DWG does not flag them closed (reference converter parity).
    open_line = _source_entity(
        model, "zpm-open", kind="LWPOLYLINE", layer="FAT AREA",
        points=((0.0, 0.0), (5.0, 5.0), (8.0, 2.0)),
        style=model.CadStyle(aci_color=134, true_color="#009999"),
    )
    short_line = _source_entity(
        model, "zpm-short", kind="LWPOLYLINE", layer="FAT AREA",
        points=((0.0, 0.0), (5.0, 5.0)),
        style=model.CadStyle(aci_color=134, true_color="#009999"),
    )
    features, _relations, unresolved, diagnostics = semantics.classify_entities(
        [ring, open_line, short_line], registry, coverage_policy="abstain",
    )
    zpm = [f for f in features if f.feature_class == "ZPM"]
    assert len(zpm) == 2
    for feature in zpm:
        assert feature.geometry_kind == "Polygon"
        assert feature.geometry_role == "SOURCE_BOUNDARY"
        assert feature.source_layer == "FAT AREA"
    assert len([f for f in zpm if f.native_points[0] == f.native_points[-1]]) == 1
    reasons = {record["reason"] for record in diagnostics["coverage"]["records"]}
    assert "unmatched_route_layer" in reasons  # 2-point FAT AREA line abstains


def test_unknown_linetype_is_visible_style_coverage(tmp_path: Path):
    styles = _canonical_module("cad2gis.cad2gis_v3.styles")
    model = _canonical_module("cad2gis.cad2gis_v3.model")
    feature = model.Feature(
        feature_key="style-fixture",
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=[(0.0, 0.0), (1.0, 0.0)],
        source_entity_key="style-fixture",
        source_handle="style-fixture",
        source_layer="FIBER",
        geometry_role="SOURCE_ROUTE",
        style=model.CadStyle(
            aci_color=7,
            linetype="VENDOR_PATTERN_X",
            entity_linetype="VENDOR_PATTERN_X",
        ),
    )
    manifest_path = styles.write_styles(tmp_path, [feature], coverage_policy="abstain")
    manifest = _json(manifest_path)
    assert manifest["coverage"]["status"] == "WATCH"
    assert manifest["unsupported_records"] == manifest["coverage"]["records"]
    assert any(
        item["reason"] == "unsupported_linetype"
        for item in manifest["coverage"]["records"]
    )
    with pytest.raises(styles.CoverageGateError) as error:
        styles.write_styles(tmp_path / "strict", [feature], coverage_policy="fail")
    assert error.value.coverage["status"] == "FAIL"


def test_mm_and_ft_require_reviewed_scaling_and_preserve_unit_provenance():
    units = _canonical_module("cad2gis.cad2gis_v3.units")
    assert units.resolve_insunits(4).metres_per_unit == pytest.approx(0.001)
    assert units.resolve_insunits(2).metres_per_unit == pytest.approx(0.3048)

    for code, scale in ((4, 0.001), (2, 0.3048)):
        contract = units.build_unit_crs_contract(
            code,
            "EPSG:3857",
            "EPSG:3857",
            source_coordinate_scale_to_m=scale,
            source_coordinate_scale_reviewed=True,
        )
        assert contract.coordinate_mode == "direct_crs"
        assert contract.source_coordinate_scale_to_m == pytest.approx(scale)
        assert contract.source_coordinate_scale_reviewed is True
        assert contract.to_manifest_dict()["provenance"]["dwg_insunits"] == "DWG_DIRECT:$INSUNITS"

    assert units.resolve_insunits(0).name == "unitless"
    with pytest.raises(units.UnitCrsContractError, match="Unitless"):
        units.build_unit_crs_contract(0, "EPSG:3857", "EPSG:3857")
    with pytest.raises(units.UnitCrsContractError, match="require explicit"):
        units.build_unit_crs_contract(4, "EPSG:3857", "EPSG:3857")
    with pytest.raises(units.UnitCrsContractError, match="reviewed"):
        units.build_unit_crs_contract(
            2,
            "EPSG:3857",
            "EPSG:3857",
            source_coordinate_scale_to_m=0.3048,
            source_coordinate_scale_reviewed=False,
        )
    projected_wcs = units.build_unit_crs_contract(
        4,
        "EPSG:23846",
        "EPSG:23846",
        source_coordinate_scale_to_m=1.0,
        source_coordinate_scale_reviewed=True,
    )
    assert projected_wcs.cad_unit.name == "millimetre"
    assert projected_wcs.source_crs_axis_unit.name == "metre"
    assert projected_wcs.source_to_crs_axis_factor == pytest.approx(1.0)


def test_unknown_or_local_crs_requires_authoritative_registration():
    units = _canonical_module("cad2gis.cad2gis_v3.units")
    with pytest.raises(units.UnitCrsContractError, match="cannot be guessed|registration"):
        units.build_unit_crs_contract(6, None, "EPSG:3857")
    with pytest.raises(units.UnitCrsContractError, match="cannot be guessed|registration"):
        units.build_unit_crs_contract(6, "EPSG:4326", "EPSG:3857")

    reviewed = units.build_unit_crs_contract(
        6,
        None,
        "EPSG:3857",
        local_registration_strategy="surveyed similarity transform",
        local_registration_reviewed=True,
    )
    assert reviewed.coordinate_mode == "reviewed_authoritative_registration"
    assert reviewed.local_registration_reviewed is True
    assert reviewed.can_direct_transform is False


def test_reader_protocol_rejects_malformed_rows_with_location():
    reader = _canonical_module("cad2gis.reader.autocad")
    with pytest.raises(reader.BulkProtocolError, match=r"bulk row 17.*field points"):
        reader._parse_bulk_points("0,1;2", line_number=17)
    with pytest.raises(reader.BulkProtocolError, match=r"bulk row 4.*field column_count"):
        reader._record_from_bulk_row(["LINE"] * 16, line_number=4)
    with pytest.raises(ValueError, match="compatibility policy"):
        reader._validate_bulk_compatibility_policy("silently_skip")


def test_line_and_bulge_route_preserve_source_segments_and_native_length():
    curves = _canonical_module("cad2gis.cad2gis_v3.curve_geometry")
    model = _canonical_module("cad2gis.cad2gis_v3.model")
    chord = 10.0
    bulge = 0.5
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    arc_length = radius * abs(4.0 * math.atan(bulge))
    native_length = chord + arc_length
    facts = {
        "schema_version": "cad2gis-curve-facts-v1",
        "coordinate_system": "WCS",
        "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[0.0, 0.0, 0.0], [chord, 0.0, 0.0], [2.0 * chord, 0.0, 0.0]],
        "bulges": [0.0, bulge, 0.0],
        "elevation": 0.0,
        "normal": [0.0, 0.0, 1.0],
        "extrusion": [0.0, 0.0, 1.0],
        "closed": False,
        "primitive_parameters": {},
        "native_length": native_length,
        "native_length_source": "fixture:analytic-bulge-arc",
    }
    source = model.SourceEntity.from_record(
        {
            "entity_key": "bulge-route-source",
            "source_sha256": "b" * 64,
            "source_file": "fixture.dwg",
            "handle": "BULGE1",
            "layout": "Model",
            "layout_role": "model",
            "cad_role": "model",
            "layer": "FIBER_ROUTE",
            "object_name": "ACDBLWPOLYLINE",
            "dwg_type_name": "LWPOLYLINE",
            "points": [(0.0, 0.0), (chord, 0.0), (2.0 * chord, 0.0)],
            "centroid": (chord, 0.0),
            "closed": False,
            "text": "",
            "block_name": "",
            "block_attributes": {},
            "native_length": native_length,
            "curve_facts": facts,
        }
    )
    feature = model.Feature(
        feature_key="bulge-route",
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=[(0.0, 0.0), (chord, 0.0), (2.0 * chord, 0.0)],
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=model.CadStyle(),
    )
    original = list(feature.native_points)
    diagnostics = curves.materialize_cable_features([source], [feature], strict=True)
    segments = curves.delivery_segments(feature)
    assert diagnostics["line_segments"] == 1
    assert diagnostics["arc_segments"] == 1
    assert diagnostics["source_segments_total"] == 2
    assert segments[0]["source_segment_kind"] == "line"
    assert segments[0]["source_native_length"] == pytest.approx(chord)
    assert segments[1]["source_segment_kind"] == "bulge_arc"
    assert len(segments[1]["delivery_native_points"]) > 2
    assert segments[1]["source_native_length"] == pytest.approx(arc_length)
    assert segments[1]["native_length_source"] == "analytic_bulge_arc"
    assert feature.native_points == original
    assert curves.delivery_points(feature)[0] == (0.0, 0.0)
    assert curves.delivery_points(feature)[-1] == (2.0 * chord, 0.0)


def test_insert_transform_uses_layout_block_base_and_rotation_without_moving_route():
    ports = _canonical_module("cad2gis.cad2gis_v3.ports")
    model = _canonical_module("cad2gis.cad2gis_v3.model")
    definition = _source_entity(
        model, "def-symbol", kind="LINE", layout="BLOCKDEF:SYMBOL",
        points=((10.0, 0.0), (11.0, 0.0)),
    )
    instance = _source_entity(
        model, "insert-symbol", kind="INSERT", block_name="SYMBOL",
        raw_properties={
            "transform_facts": {
                "insertion_point": (100.0, 200.0, 0.0),
                "block_base_point": (10.0, 0.0, 0.0),
                "scale": (1.0, 1.0, 1.0),
                "rotation": math.pi / 2.0,
                "normal": (0.0, 0.0, 1.0),
                "extrusion": (0.0, 0.0, 1.0),
            }
        },
    )
    feature_type = model.Feature
    support = feature_type(
        feature_key="support", feature_class="PTECH", geometry_kind="Point",
        native_points=[(100.0, 200.0)], source_entity_key="insert-symbol",
        source_handle="insert-symbol", source_layer="PTECH", geometry_role="SOURCE_ASSET",
        style=model.CadStyle(),
    )
    route = feature_type(
        feature_key="route", feature_class="CABLE", geometry_kind="LineString",
        native_points=[(100.0, 200.0), (110.0, 200.0)], source_entity_key="route",
        source_handle="route", source_layer="FIBER", geometry_role="SOURCE_ROUTE",
        style=model.CadStyle(),
    )
    original_route = list(route.native_points)
    registry = SimpleNamespace(thresholds={"device_to_support_candidate": 0.5, "exact": 1e-6, "dimension_to_support": 0.5})
    candidates = ports.build_port_candidates(
        [definition, instance], [support, route], registry,
    )
    assert candidates and candidates[0]["status"] == "on_symbol_geometry"
    assert candidates[0]["port_point_native"] == [100.0, 200.0]
    assert route.native_points == original_route

    missing_base = _source_entity(
        model, "insert-no-base", kind="INSERT", block_name="SYMBOL",
        raw_properties={
            "transform_facts": {
                "insertion_point": (100.0, 200.0, 0.0),
                "scale": (1.0, 1.0, 1.0),
                "rotation": 0.0,
                "normal": (0.0, 0.0, 1.0),
                "extrusion": (0.0, 0.0, 1.0),
            }
        },
    )
    support_no_base = feature_type(
        feature_key="support-no-base", feature_class="PTECH", geometry_kind="Point",
        native_points=[(100.0, 200.0)], source_entity_key="insert-no-base",
        source_handle="insert-no-base", source_layer="PTECH", geometry_role="SOURCE_ASSET",
        style=model.CadStyle(),
    )
    candidate = ports.build_port_candidates(
        [definition, missing_base], [support_no_base, route], registry,
    )[0]
    assert candidate["status"].startswith("abstain_")
    assert candidate["port_point_native"] is None


def test_missing_gcp_is_not_an_absolute_accuracy_claim(tmp_path: Path):
    workflow = _canonical_module("cad2gis.gcp_workflow")
    result = workflow.status_project(tmp_path / "project-without-gcp")
    assert result["absolute_accuracy_validation"] == "not_verified"
    assert result["status"] == "blocked"
    assert result["authority"]["absolute_train_and_check_ready"] is False


def test_ambiguous_project_configuration_has_actionable_error(tmp_path: Path):
    pipeline = _canonical_module("cad2gis.pipeline")
    config = tmp_path / "config"
    config.mkdir()
    (config / "source_profile.json").write_text("{}", encoding="utf-8")
    (config / "vendor_source_profile.json").write_text("{}", encoding="utf-8")
    (config / "mapping_registry.json").write_text("{}", encoding="utf-8")
    with pytest.raises(pipeline.ProjectConfigurationError, match="ambiguous") as error:
        pipeline.resolve_project_configuration(project_dir=tmp_path)
    assert "source_profile" in str(error.value)


def _fake_conversion_status(*, reader_protocol=None, georeference=None):
    pipeline = _canonical_module("cad2gis.cad2gis_v3.pipeline")
    return pipeline._derive_conversion_status(
        entities=[object()],
        ingest_diagnostics={
            "reader_protocol": (
                {"inventory_complete": True, "skipped_rows": 0}
                if reader_protocol is None
                else reader_protocol
            ),
            "reader_inventory": {},
        },
        semantic_diagnostics={"coverage": {"counts": {}}},
        style_coverage={"counts": {}},
        unresolved=[],
        terminal_accounting={
            "accepted": 1,
            "unsupported": 0,
            "abstained": 0,
            "errored": 0,
            "total": 1,
        },
        validation_summary={},
        georeference_diagnostics={} if georeference is None else georeference,
        diagnostics={},
    )


def _run_fake_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    alias_error: OSError | None = None,
):
    pipeline = _canonical_module("cad2gis.cad2gis_v3.pipeline")
    model = _canonical_module("cad2gis.cad2gis_v3.model")
    implementation = _canonical_module("cad2gis.cad2gis_v3.implementation")
    runtime_provenance = _canonical_module("cad2gis.cad2gis_v3.runtime_provenance")
    units = _canonical_module("cad2gis.cad2gis_v3.units")
    styles = _canonical_module("cad2gis.cad2gis_v3.styles")

    source = tmp_path / "fixture.dwg"
    source.write_bytes(b"pipeline integration fixture")
    run_dir = tmp_path / "runs" / "fixture"
    source_hash = _sha256_bytes(source.read_bytes())
    (tmp_path / "source_profile.json").write_text("{}", encoding="utf-8")
    (tmp_path / "mapping_registry.json").write_text("{}", encoding="utf-8")
    entity = _source_entity(model, "source-line", kind="LINE", layer="FIBER")

    class FakeProfile:
        path = tmp_path / "source_profile.json"
        source_sha256 = source_hash
        source_crs = "EPSG:3857"
        target_crs = "EPSG:3857"
        dwg_insunits = 6
        source_coordinate_scale_to_m = 1.0
        source_coordinate_scale_reviewed = True
        local_registration_strategy = None
        local_registration_reviewed = False
        inventory_sha256 = ""
        project_id = ""
        is_legacy = False
        spatial_coverage_policy = None
        review = SimpleNamespace(status="reviewed")
        expectations = SimpleNamespace(
            source_inventory={}, feature_counts={}, annotation_families=(),
            source_geometry_gates=(), topology_gates=(), segment_gates=(),
            delivery_counts={},
        )

        def require_reviewed(self):
            return None

        def validate_source(self, path):
            assert Path(path).resolve() == source.resolve()
            return source_hash

    class FakeRegistry:
        path = tmp_path / "mapping_registry.json"
        project_id = ""
        inventory_sha256 = ""
        semantic_coverage_policy = "warn"
        semantic_coverage_allowlist = ()
        style_coverage_policy = "warn"
        style_coverage_allowlist = ()
        annotation_families = ()
        positive_route_layer_regex = ".*"
        policy = {
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        }
        review = SimpleNamespace(status="reviewed")

        def require_reviewed(self):
            return None

    monkeypatch.setattr(
        pipeline.SourceProfile, "load", staticmethod(lambda _: FakeProfile())
    )
    monkeypatch.setattr(
        pipeline.MappingRegistry,
        "load",
        staticmethod(lambda *_args: FakeRegistry()),
    )
    monkeypatch.setattr(
        pipeline,
        "ingest",
        lambda *_args: (
            [entity],
            {
                "census": {},
                "reader_inventory": {},
                "reader_protocol": {"inventory_complete": True, "skipped_rows": 0},
            },
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "classify_entities",
        lambda *_args, **_kwargs: (
            [], [], [],
            {
                "annotation_assignments_by_family": {},
                "coverage": {"conversion_allowed": True},
            },
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_validate_source_geometry",
        lambda *_args, **_kwargs: {
            "cable_sources_checked": 0,
            "curve_facts_checked": 0,
            "source_geometry_immutable": True,
        },
    )
    monkeypatch.setattr(pipeline, "_validate_topology_policy", lambda *_args: {
        "dimension_or_sling_promotions": 0,
        "synthetic_route_vertices": 0,
        "support_optical_promotions": 0,
        "crossing_connections": 0,
    })
    monkeypatch.setattr(pipeline, "_evaluate_diagnostic_gates", lambda domain, *_args: {
        "domain": domain, "passed": True, "gates": [],
    })
    monkeypatch.setattr(
        pipeline,
        "build_topology",
        lambda _entities, _features, _registry, relations, unresolved: (
            relations,
            unresolved,
            {
                "source_route_components": 0,
                "source_routes": 0,
                "source_route_graph": {},
                "source_route_native_lengths": 0,
                "source_route_native_length_max_abs_delta_m": 0.0,
            },
        ),
    )
    curve_geometry = _canonical_module("cad2gis.cad2gis_v3.curve_geometry")
    monkeypatch.setattr(
        curve_geometry,
        "materialize_cable_features",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        curve_geometry,
        "validate_cable_geometry_materialization",
        lambda *_args, **_kwargs: {},
    )

    class FakeTransformer:
        source_crs = "EPSG:3857"
        target_crs = "EPSG:3857"
        source = "EPSG:3857"
        target = "EPSG:3857"

        def roundtrip_error(self, _points):
            return 0.0

        def engine_crosscheck_error(self, _points):
            return 0.0

        def operation_metadata(self, _point):
            return {"absolute_accuracy_validation": "not_verified"}

    monkeypatch.setattr(
        pipeline,
        "DirectTransformer",
        lambda *_args, **_kwargs: FakeTransformer(),
    )
    monkeypatch.setattr(
        units,
        "build_unit_crs_contract",
        lambda *_args, **_kwargs: SimpleNamespace(
            coordinate_mode="direct_crs",
            to_manifest_dict=lambda: {},
        ),
    )
    monkeypatch.setattr(
        pipeline, "feature_adjustment_records", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        pipeline, "enrich_delivery_metrics", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(styles, "analyze_style_coverage", lambda *_args, **_kwargs: {
        "conversion_allowed": True, "status": "PASS",
    })
    monkeypatch.setattr(
        pipeline,
        "_manifest_validation_summary",
        lambda *_args, **_kwargs: {"segment_delivery": {"passed": True}},
    )

    def fake_evidence(path, *_args, **_kwargs):
        Path(path).write_bytes(b"evidence")

    def fake_delivery(path, *_args, **_kwargs):
        Path(path).write_bytes(b"delivery")
        return {}

    def fake_styles(path, *_args, **_kwargs):
        destination = Path(path)
        destination.mkdir(parents=True, exist_ok=True)
        manifest = destination / "style_manifest.json"
        manifest.write_text(
            json.dumps({"coverage": {"conversion_allowed": True}}),
            encoding="utf-8",
        )
        return manifest

    def fake_source(path, *_args, **_kwargs):
        destination = Path(path)
        destination.write_bytes(b"source")
        return SimpleNamespace(path=destination, entity_count=1)

    monkeypatch.setattr(pipeline, "write_evidence", fake_evidence)
    monkeypatch.setattr(pipeline, "write_delivery", fake_delivery)
    monkeypatch.setattr(pipeline, "write_styles", fake_styles)
    monkeypatch.setattr(pipeline, "write_source_gpkg", fake_source, raising=False)
    monkeypatch.setattr(
        pipeline, "account_entities", lambda values: list(values), raising=False
    )
    monkeypatch.setattr(pipeline, "summarize_accounting", lambda values: {
        "accepted": len(list(values)), "unsupported": 0, "abstained": 0,
        "errored": 0, "total": 1,
    }, raising=False)
    monkeypatch.setattr(
        implementation,
        "freeze_conversion_snapshot",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        implementation,
        "conversion_snapshot_manifest_fields",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        implementation,
        "verify_conversion_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runtime_provenance, "collect_runtime_provenance", lambda **_kwargs: {}
    )
    monkeypatch.setattr(
        runtime_provenance,
        "runtime_manifest_fields",
        lambda *_args, **_kwargs: {},
    )

    if alias_error is not None:
        alias_path = run_dir.parent / "latest_verified.json"
        alias_path.parent.mkdir(parents=True, exist_ok=True)
        alias_path.write_bytes(b"previous verified alias\n")

        def fail_alias(*_args, **_kwargs):
            assert run_dir.is_dir()
            assert (run_dir / "run_manifest.json").is_file()
            raise alias_error

        monkeypatch.setattr(pipeline, "publish_verified_alias", fail_alias)

    result = pipeline.convert(
        pipeline.ConversionRequest(
            source=source,
            run_dir=run_dir,
            source_profile=FakeProfile.path,
            mapping_registry=FakeRegistry.path,
        )
    )
    return result, run_dir


def test_conversion_publishes_source_artifact_accounting_status_and_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    result, run_dir = _run_fake_conversion(tmp_path, monkeypatch)
    manifest = _json(run_dir / "run_manifest.json")
    expected_artifacts = {
        "source.gpkg", "delivery.gpkg", "evidence.gpkg", "style_manifest.json",
        "evidence_graph.json", "manifest.json",
    }
    artifact_names = {
        Path(item["path"]).name for item in manifest["artifacts"].values()
    }
    assert artifact_names == expected_artifacts
    for item in manifest["artifacts"].values():
        artifact = Path(item["path"])
        assert item["sha256"] == _sha256_bytes(artifact.read_bytes())
    assert manifest["run_status"] in {"VERIFIED", "CONDITIONAL", "UNSAFE", "FAILED"}
    assert manifest["terminal_accounting"]["total"] == manifest["source_entity_count"]
    assert manifest["modes"] == {"domain": "auto", "llm": "off"}
    assert (
        manifest["plan_domain"]["schema_version"]
        == "cad2gis-plan-domain-v1"
    )
    assert manifest["plan_domain"]["raw_entity_count"] == (
        manifest["source_entity_count"]
    )
    assert manifest["artifacts"]["source"]["sha256"] == _sha256_bytes(
        result.source_path.read_bytes()
    )
    assert manifest["reasoning"]["graph_sha256"] == _json(
        run_dir / "reasoning" / "evidence_graph.json"
    )["graph_sha256"]


def test_alias_failure_preserves_old_alias_after_bundle_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    alias_path = tmp_path / "runs" / "latest_verified.json"
    run_dir = tmp_path / "runs" / "fixture"

    with pytest.raises(OSError, match="simulated alias failure"):
        _run_fake_conversion(
            tmp_path,
            monkeypatch,
            alias_error=OSError("simulated alias failure"),
        )

    assert alias_path.read_bytes() == b"previous verified alias\n"
    assert run_dir.is_dir()
    assert (run_dir / "run_manifest.json").is_file()


def test_conversion_status_preserves_incomplete_reader_as_unsafe():
    status = _fake_conversion_status(
        reader_protocol={"inventory_complete": False, "skipped_rows": 0}
    )
    assert status.value == "UNSAFE"


@pytest.mark.parametrize("value", [math.nan, math.inf, -1.0])
def test_conversion_status_rejects_invalid_roundtrip_metric(value: float):
    status = _fake_conversion_status(
        georeference={"roundtrip_max_source_m": value}
    )
    assert status.value == "UNSAFE"


def test_conversion_status_counts_skipped_row_errors_as_unsafe():
    status = _fake_conversion_status(
        reader_protocol={
            "inventory_complete": True,
            "skipped_rows": 0,
            "skipped_row_errors": ["row 7 malformed"],
        }
    )
    assert status.value == "UNSAFE"


def test_conversion_status_fails_closed_for_malformed_reader_count():
    status = _fake_conversion_status(
        reader_protocol={"inventory_complete": True, "skipped_rows": "unknown"}
    )
    assert status.value == "UNSAFE"


def test_runtime_forwards_conversion_modes_to_backend_request(
    tmp_path: Path, monkeypatch,
):
    runtime = _canonical_module("cad2gis.runtime")
    captured = {}

    class FakeRequest:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.__dict__.update(kwargs)

    class FakeBackend:
        ConversionRequest = FakeRequest

        @staticmethod
        def convert(request):
            return request

    monkeypatch.setattr(runtime, "load_backend_module", lambda _name: FakeBackend)
    result = runtime.call_conversion_backend(
        source=tmp_path / "source.dwg",
        run_dir=tmp_path / "run",
        source_profile=tmp_path / "source_profile.json",
        mapping_registry=tmp_path / "mapping_registry.json",
        gcp_profile=None,
        domain="generic",
        llm="assist",
    )
    assert result.domain == "generic"
    assert result.llm == "assist"
    assert captured["domain"] == "generic"
    assert captured["llm"] == "assist"


@pytest.mark.parametrize(
    ("field", "value"),
    [("domain", "vendor"), ("llm", "provider")],
)
def test_conversion_request_rejects_unknown_modes(field: str, value: str):
    pipeline = _canonical_module("cad2gis.cad2gis_v3.pipeline")
    values = {
        "source": Path("source.dwg"),
        "run_dir": Path("run"),
        "source_profile": Path("source_profile.json"),
        "mapping_registry": Path("mapping_registry.json"),
        field: value,
    }
    with pytest.raises(ValueError, match=field):
        pipeline.ConversionRequest(**values)


def test_gpkg_contents_scan_order_follows_reviewed_layer_order(tmp_path: Path):
    import sqlite3

    gpkg_metadata = _canonical_module("cad2gis.cad2gis_v3.gpkg_metadata")
    database = tmp_path / "order.gpkg"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE gpkg_contents ("
            "table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, "
            "last_change TEXT NOT NULL)"
        )
        # Simulate GDAL deferring zero-feature layer registration: non-empty
        # layers land first, empty layers are appended later.
        for name in (
            "SITE", "PTECH", "INFRASTRUCTURE", "CABLE",
            "BOITE", "IMB", "ZPM", "ZNRO",
        ):
            connection.execute(
                "INSERT INTO gpkg_contents(table_name, data_type, last_change) "
                "VALUES (?, 'features', '2020-01-01T00:00:00.000Z')",
                (name,),
            )
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(database)
    try:
        gpkg_metadata.normalize_geopackage_metadata(
            connection,
            contents_order=("SITE", "BOITE", "PTECH", "IMB", "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO"),
        )
        names = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM gpkg_contents WHERE data_type='features'"
            )
        ]
    finally:
        connection.close()

    assert names == ["SITE", "BOITE", "PTECH", "IMB", "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO"]


def test_optional_emr_layer_is_inserted_before_imb():
    warehouse = _canonical_module("cad2gis.cad2gis_v3.warehouse")
    features = [SimpleNamespace(feature_class="EMR")]
    order = warehouse._active_layer_order(features)
    assert order == (
        "SITE", "BOITE", "PTECH", "EMR", "IMB",
        "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO",
    )
