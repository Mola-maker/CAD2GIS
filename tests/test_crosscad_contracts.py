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
BACKEND = ROOT / "experiment" / "py_scripts"
SRC = ROOT / "src"
APD_SOURCE = ROOT / "experiment" / "APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"
APD_SOURCE_PROFILE = ROOT / "experiment" / "config" / "apd_source_profile.json"
APD_MAPPING = ROOT / "experiment" / "config" / "apd_mapping_registry.json"


def _backend_module(name: str):
    """Load a backend stage without requiring an editable install."""

    backend_text = str(BACKEND)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)
    return importlib.import_module(name)


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

    config = _backend_module("cad2gis_v3.config")
    profile = config.SourceProfile.load(APD_SOURCE_PROFILE)
    assert profile.validate_source(APD_SOURCE) == profile.source_sha256

    second_source = tmp_path / "same-layout-different-hash.dwg"
    second_source.write_bytes(APD_SOURCE.read_bytes() + b"\nCAD2GIS-SECOND-SOURCE")
    with pytest.raises(ValueError, match="Source hash mismatch"):
        profile.validate_source(second_source)

    registry = config.MappingRegistry.load(APD_MAPPING, profile.source_sha256)
    assert registry.source_sha256 == profile.source_sha256
    stale_mapping = _json(APD_MAPPING)
    stale_mapping["source_sha256"] = _sha256_bytes(b"different-reviewed-dwg")
    stale_mapping_path = _write_json(tmp_path / "stale_mapping.json", stale_mapping)
    with pytest.raises(ValueError, match="stale|different DWG"):
        config.MappingRegistry.load(stale_mapping_path, profile.source_sha256)


def test_draft_profile_is_rejected_before_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bootstrap output is evidence-only until a human review changes its state."""

    config = _backend_module("cad2gis_v3.config")
    pipeline = _backend_module("cad2gis_v3.pipeline")
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

    onboarding = _backend_module("cad2gis_v3.project_profile")
    pipeline = _backend_module("cad2gis_v3.pipeline")
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
    semantics = _backend_module("cad2gis_v3.semantics")
    model = _backend_module("cad2gis_v3.model")
    config = _backend_module("cad2gis_v3.config")
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


def test_unknown_linetype_is_visible_style_coverage(tmp_path: Path):
    styles = _backend_module("cad2gis_v3.styles")
    model = _backend_module("cad2gis_v3.model")
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
    units = _backend_module("cad2gis_v3.units")
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

    with pytest.raises(units.UnitCrsContractError, match="Unsupported|unitless"):
        units.resolve_insunits(0)
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


def test_unknown_or_local_crs_requires_authoritative_registration():
    units = _backend_module("cad2gis_v3.units")
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
    reader = _backend_module("autocad_reader")
    with pytest.raises(reader.BulkProtocolError, match=r"bulk row 17.*field points"):
        reader._parse_bulk_points("0,1;2", line_number=17)
    with pytest.raises(reader.BulkProtocolError, match=r"bulk row 4.*field column_count"):
        reader._record_from_bulk_row(["LINE"] * 16, line_number=4)
    with pytest.raises(ValueError, match="compatibility policy"):
        reader._validate_bulk_compatibility_policy("silently_skip")


def test_line_and_bulge_route_preserve_source_segments_and_native_length():
    curves = _backend_module("cad2gis_v3.curve_geometry")
    model = _backend_module("cad2gis_v3.model")
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
    ports = _backend_module("cad2gis_v3.ports")
    model = _backend_module("cad2gis_v3.model")
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
