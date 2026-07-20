"""Focused contracts for the operator-only GCP workflow."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from osgeo import ogr, osr

from apd_rules import set_traditional_axis_order
from cad2gis_v3.calibration import GCPProfile
from cad2gis_v3.georef import DirectTransformer
from gcp_tool import diagnose_capture, export_profile, prepare_capture


ROOT = Path(__file__).resolve().parents[1]
PROFILE_TEMPLATE = ROOT / "config" / "apd_gcp_profile.json"
SOURCE_SHA256 = "557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557"
SOURCE_CRS = "EPSG:3857"
TARGET_CRS = "EPSG:9481"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_points():
    return (
        (13_682_000.0, 69_000.0),
        (13_682_100.0, 69_000.0),
        (13_682_000.0, 69_100.0),
        (13_682_100.0, 69_100.0),
        (13_682_050.0, 69_050.0),
        (13_682_025.0, 69_075.0),
    )


def _create_delivery(path: Path) -> None:
    driver = ogr.GetDriverByName("GPKG")
    dataset = driver.CreateDataSource(str(path))
    reference = osr.SpatialReference()
    assert reference.SetFromUserInput(TARGET_CRS) == 0
    set_traditional_axis_order(reference, osr)
    layer = dataset.CreateLayer("PTECH", srs=reference, geom_type=ogr.wkbPoint)
    for name in ("source_entity_key", "source_handle", "display_label", "CODE"):
        assert layer.CreateField(ogr.FieldDefn(name, ogr.OFTString)) == ogr.OGRERR_NONE
    definition = layer.GetLayerDefn()
    transformer = DirectTransformer(SOURCE_CRS, TARGET_CRS)
    for index, source_point in enumerate(_source_points()):
        feature = ogr.Feature(definition)
        feature.SetField("source_entity_key", f"ENTITY-{index}")
        feature.SetField("source_handle", f"H{index}")
        feature.SetField("display_label", f"CONTROL CANDIDATE {index}")
        feature.SetField("CODE", f"P-{index}")
        geometry = ogr.Geometry(ogr.wkbPoint)
        geometry.AddPoint_2D(*transformer.point(source_point))
        feature.SetGeometry(geometry)
        assert layer.CreateFeature(feature) == ogr.OGRERR_NONE
    dataset = None


def _create_evidence(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE feature_candidates (
                feature_key TEXT, feature_class TEXT, geometry_kind TEXT,
                source_entity_key TEXT, source_handle TEXT, source_layer TEXT,
                display_label TEXT
            )"""
        )
        connection.execute(
            "CREATE TABLE cad_entities (entity_key TEXT, native_points TEXT)"
        )
        for index, source_point in enumerate(_source_points()):
            connection.execute(
                "INSERT INTO feature_candidates VALUES (?, 'PTECH', 'Point', ?, ?, 'POLE', ?)",
                (f"FEATURE-{index}", f"ENTITY-{index}", f"H{index}", f"EVIDENCE {index}"),
            )
            connection.execute(
                "INSERT INTO cad_entities VALUES (?, ?)",
                (f"ENTITY-{index}", json.dumps([source_point])),
            )


def _bundle(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    delivery = tmp_path / "delivery.gpkg"
    evidence = tmp_path / "evidence.gpkg"
    manifest = tmp_path / "run_manifest.json"
    _create_delivery(delivery)
    _create_evidence(evidence)
    value = {
        "schema_version": "cad2gis-run-manifest-v3",
        "publication": {"status": "complete"},
        "source": {"path": "synthetic.dwg", "sha256": SOURCE_SHA256},
        "crs": {"source_crs": SOURCE_CRS, "target_crs": TARGET_CRS},
        "artifacts": {
            "delivery": {"path": str(delivery), "sha256": _sha256(delivery)},
            "evidence": {"path": str(evidence), "sha256": _sha256(evidence)},
        },
    }
    manifest.write_text(json.dumps(value), encoding="utf-8")
    capture = tmp_path / "capture.gpkg"
    prepare_capture(
        delivery_path=delivery,
        evidence_path=evidence,
        manifest_path=manifest,
        output_path=capture,
        candidate_layers=("PTECH",),
    )
    return delivery, evidence, manifest, capture


def _accept_controls(capture: Path, *, relative_osm_index: int | None = None) -> None:
    dataset = ogr.Open(str(capture), 1)
    assert dataset is not None
    layer = dataset.GetLayerByName("gcp_controls")
    assert layer is not None
    layer.StartTransaction()
    for index, feature in enumerate(layer):
        feature.SetField("role", "train" if index < 3 else "check")
        if index == relative_osm_index:
            feature.SetField("reference_kind", "relative_osm_reference")
            feature.SetField("control_source", "OpenStreetMap snapshot 2026-07-17 road fixture")
        else:
            feature.SetField("reference_kind", "authoritative_control")
            feature.SetField("control_source", "closed-form authoritative test fixture")
        feature.SetField("accuracy_m", 0.1)
        feature.SetField("weight", 1.0)
        feature.SetField("enabled", 1)
        feature.SetField("review_status", "accepted")
        feature.SetField("target_easting", feature.GetField("nominal_easting") + 8.0)
        feature.SetField("target_northing", feature.GetField("nominal_northing") - 6.0)
        assert layer.SetFeature(feature) == ogr.OGRERR_NONE
    layer.CommitTransaction()
    dataset = None


def test_direct_transformer_exposes_nominal_target_to_source_inverse():
    transformer = DirectTransformer(SOURCE_CRS, TARGET_CRS)
    source = (13_682_034.5, 69_087.25)
    target = transformer.point(source)
    assert transformer.target_to_source_point(target) == pytest.approx(source, abs=5e-8)
    inverse_points = transformer.target_to_source_points((target,))
    assert len(inverse_points) == 1
    assert inverse_points[0] == pytest.approx(source, abs=5e-8)


def test_prepare_diagnose_and_enabled_export_are_separate_and_strict(tmp_path):
    delivery, evidence, _, capture = _bundle(tmp_path)
    delivery_before = _sha256(delivery)
    evidence_before = _sha256(evidence)

    dataset = ogr.Open(str(capture))
    layer = dataset.GetLayerByName("gcp_controls")
    assert layer.GetFeatureCount() == 6
    first = layer.GetNextFeature()
    assert first.GetField("review_status") == "candidate"
    assert first.GetField("enabled") == 0
    assert first.IsFieldSetAndNotNull(first.GetFieldIndex("target_easting")) is False
    dataset = None

    _accept_controls(capture)
    report_path = tmp_path / "diagnostic.json"
    diagnostic = diagnose_capture(capture_path=capture, report_path=report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["publication_changed"] is False
    assert diagnostic["available_models"] == ["translation", "similarity", "affine"]
    translation = next(
        item for item in report["candidate_models"] if item["model"] == "translation"
    )
    assert translation["result"]["parameters"]["pivot_shift_e_m"] == pytest.approx(8.0)
    assert translation["result"]["parameters"]["pivot_shift_n_m"] == pytest.approx(-6.0)
    assert translation["result"]["check_metrics"]["max_m"] == pytest.approx(0.0, abs=1e-9)
    assert len(translation["result"]["residuals"]) == 6
    assert report["spatial_coverage"]["training_control_count"] == 3
    assert report["spatial_coverage"]["check_control_count"] == 3
    assert _sha256(delivery) == delivery_before
    assert _sha256(evidence) == evidence_before

    exported = tmp_path / "enabled_profile.json"
    result = export_profile(
        capture_path=capture,
        template_profile_path=PROFILE_TEMPLATE,
        output_path=exported,
        diagnostic_report_path=report_path,
        enable=True,
        requested_model="translation",
        spatial_review_source="closed-form fixture roles reviewed before fitting",
        max_check_error_m=0.01,
        max_pivot_shift_m=100.0,
        max_abs_rotation_deg=1.0,
        max_scale_deviation_ratio=0.01,
        max_affine_condition_number=10.0,
        disable_robust=True,
    )
    profile = GCPProfile.load(exported, expected_source_sha256=SOURCE_SHA256)
    assert profile.enabled is True
    assert len(profile.controls) == 6
    assert result["selected_model"] == "translation"
    assert result["validation_passed"] is True
    assert result["publication_changed"] is False

    dataset = ogr.Open(str(capture), 1)
    layer = dataset.GetLayerByName("gcp_controls")
    first = layer.GetNextFeature()
    first.SetField("role", "check")
    assert layer.SetFeature(first) == ogr.OGRERR_NONE
    dataset = None
    with pytest.raises(ValueError, match="frozen train/check roles changed"):
        export_profile(
            capture_path=capture,
            template_profile_path=PROFILE_TEMPLATE,
            output_path=tmp_path / "stale_role_profile.json",
            diagnostic_report_path=report_path,
            enable=True,
            requested_model="translation",
        )


def test_relative_osm_is_explicit_and_requires_activation_acknowledgement(tmp_path):
    _, _, _, capture = _bundle(tmp_path)
    _accept_controls(capture, relative_osm_index=0)
    report_path = tmp_path / "relative_report.json"
    diagnose_capture(capture_path=capture, report_path=report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "relative_osm" in report["controls"]["reference_scope"]
    assert "not surveyed ground truth" in report["controls"]["relative_osm_warning"]

    draft = tmp_path / "relative_draft.json"
    export_profile(
        capture_path=capture,
        template_profile_path=PROFILE_TEMPLATE,
        output_path=draft,
    )
    value = json.loads(draft.read_text(encoding="utf-8"))
    assert value["enabled"] is False
    assert value["controls"][0]["source"].startswith("RELATIVE_OSM_REFERENCE_ONLY")

    with pytest.raises(ValueError, match="--allow-relative-osm"):
        export_profile(
            capture_path=capture,
            template_profile_path=PROFILE_TEMPLATE,
            output_path=tmp_path / "forbidden_enabled.json",
            enable=True,
            requested_model="translation",
        )


def test_capture_rejects_edited_source_coordinates_and_stale_bound_artifacts(tmp_path):
    _, evidence, _, capture = _bundle(tmp_path)
    _accept_controls(capture)
    dataset = ogr.Open(str(capture), 1)
    layer = dataset.GetLayerByName("gcp_controls")
    feature = layer.GetNextFeature()
    feature.SetField("cad_x", feature.GetField("cad_x") + 1.0)
    assert layer.SetFeature(feature) == ogr.OGRERR_NONE
    dataset = None
    with pytest.raises(ValueError, match="immutable cad_x was edited"):
        diagnose_capture(capture_path=capture, report_path=tmp_path / "edited.json")

    _, evidence, _, capture = _bundle(tmp_path / "stale")
    evidence.write_bytes(evidence.read_bytes() + b"stale")
    with pytest.raises(ValueError, match="evidence SHA-256"):
        diagnose_capture(capture_path=capture, report_path=tmp_path / "stale.json")
