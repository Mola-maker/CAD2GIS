"""Exercise OSM revisions through the canonical publisher and real GIS writers."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from pyproj import Transformer

from cad2gis.cad2gis_v3 import pipeline, styles, units
from cad2gis.cad2gis_v3 import osm_matching
from cad2gis.cad2gis_v3.implementation import SnapshotVerificationError, freeze_conversion_snapshot, verify_conversion_snapshot
from test_crosscad_contracts import _run_fake_conversion


def test_osm_revision_reaches_gis_and_second_match_preserves_first_run(tmp_path, monkeypatch):
    # Source ingestion/classification are bounded fixtures; transformations,
    # geometry/metric enrichment, source/delivery writers and publication are real.
    originals = {name: getattr(pipeline, name) for name in (
        "DirectTransformer", "feature_adjustment_records", "enrich_delivery_metrics",
        "write_delivery", "write_styles", "write_source_gpkg",
        "_manifest_validation_summary",
    )}
    build_units = units.build_unit_crs_contract
    analyze_styles = styles.analyze_style_coverage
    origin = (12_600_000.0, -800_000.0)
    route_points = [
        [origin, (origin[0] + 100, origin[1]), (origin[0] + 140, origin[1] + 40)],
        [(origin[0] + 100, origin[1]), (origin[0] + 100, origin[1] - 80), (origin[0] + 170, origin[1] - 120)],
    ]
    saved = {}

    def configure(patch, second=False):
        def setup(module, model, source):
            for name, value in originals.items():
                patch.setattr(module, name, value)
            patch.setattr(units, "build_unit_crs_contract", build_units)
            patch.setattr(styles, "analyze_style_coverage", analyze_styles)
            module.SourceProfile.load(source.parent / "source_profile.json").expectations.feature_counts = {"PTECH": 1}
            nominal = originals["DirectTransformer"]("EPSG:3857", "EPSG:3857", unit_contract=build_units(
                6, "EPSG:3857", "EPSG:3857", source_coordinate_scale_to_m=1.0,
                source_coordinate_scale_reviewed=True,
            ))
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            if second:
                controls = [
                    {"point_id": str(i), "cad_x": p[0], "cad_y": p[1],
                     "target_easting": p[0] + 120, "target_northing": p[1] + 190,
                     "target_crs": "EPSG:3857", "role": "train", "enabled": True}
                    for i, p in enumerate([origin, route_points[0][-1]])
                ]
                record = osm_matching.refine_match(
                    saved["record"], nominal, controls, source_sha256=source_hash,
                    parent_run_sha256=saved["manifest_sha"],
                )
            else:
                geographic = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
                roads = []
                for index, points in enumerate(route_points):
                    geometry = [geographic.transform(x + 100, y + 200) for x, y in points]
                    roads.append({"type": "way", "id": index + 1, "tags": {"highway": "residential"},
                                  "geometry": [{"lon": lon, "lat": lat} for lon, lat in geometry]})
                data = source.parent / "osm.json"
                data.write_text(json.dumps({"elements": roads}), encoding="utf-8")
                _, record = osm_matching.match_osm(
                    source=source, source_sha256=source_hash,
                    features=[SimpleNamespace(feature_class="CABLE", native_points=p) for p in route_points],
                    nominal=nominal, coordinate_domain={"passed": True}, osm_data=data,
                )
                assert record["status"] == "matched"
                saved["record"] = record
            profile = source.parent / "match.json"
            profile.write_text(json.dumps(record), encoding="utf-8")
            feature = model.Feature(
                "fixture-device", "PTECH", "Point", [origin], "source-line", "source-line", "FIBER",
                "source_observed", model.CadStyle(aci_color=1), display_label="DEVICE-1",
                label_provenance="DWG_DIRECT:fixture-text",
            )
            patch.setattr(module, "classify_entities", lambda *_args, **_kwargs: (
                [feature], [], [], {"annotation_assignments_by_family": {}, "coverage": {"conversion_allowed": True}},
            ))
            saved["feature"] = feature
            return {"matching": "osm", "match_profile": profile}
        return setup

    first_dir = tmp_path / "first"
    first_dir.mkdir()
    with monkeypatch.context() as patch:
        first, run = _run_fake_conversion(first_dir, patch, configure=configure(patch))
    manifest = json.loads(first.run_manifest_path.read_text(encoding="utf-8"))
    original_delivery = first.delivery_path.read_bytes()
    saved["manifest_sha"] = hashlib.sha256(first.run_manifest_path.read_bytes()).hexdigest()
    assert manifest["osm_matching"]["revision"] == 1
    assert manifest["crs"]["lineage"]["model"] == "osm_relative_translation"
    assert manifest["validation"]["coordinate_accuracy"]["passed"] is None
    assert first.run_status == "CONDITIONAL"
    assert (run / "osm_matching.json").is_file()
    assert saved["feature"].native_points == [origin]
    assert "OSM-relative" in saved["feature"].field_provenance["X"]

    from osgeo import ogr
    def point(path):
        database = ogr.Open(str(path), 0)
        row = database.GetLayerByName("PTECH").GetNextFeature()
        result = row.GetGeometryRef().GetPoint_2D(0)
        assert row.GetField("X") == pytest.approx(result[0])
        assert row.GetField("Y") == pytest.approx(result[1])
        database = None
        return result

    assert point(first.delivery_path) == pytest.approx((origin[0] + 100, origin[1] + 200), abs=0.1)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    with monkeypatch.context() as patch:
        second, _ = _run_fake_conversion(second_dir, patch, configure=configure(patch, second=True))
    assert point(second.delivery_path) == pytest.approx((origin[0] + 120, origin[1] + 190), abs=0.1)
    assert first.delivery_path.read_bytes() == original_delivery
    second_manifest = json.loads(second.run_manifest_path.read_text(encoding="utf-8"))
    assert second_manifest["osm_matching"]["revision"] == 2
    assert second_manifest["osm_matching"]["parent_match_sha256"] == saved["record"]["match_sha256"]


def test_osm_inputs_are_frozen_and_changes_reject_publication(tmp_path):
    files = []
    for name in ("drawing.dwg", "profile.json", "registry.json", "osm.json", "match.json"):
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        files.append(path)
    snapshot = freeze_conversion_snapshot(*files[:3], osm_data=files[3], match_profile=files[4],
                                          code_root=tmp_path, code_paths=(), runtime={})
    verify_conversion_snapshot(snapshot)
    files[3].write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(SnapshotVerificationError, match="osm_data"):
        verify_conversion_snapshot(snapshot)


def test_independent_panels_match_separately_and_refinement_only_publishes_selected_panel(tmp_path, monkeypatch):
    originals = {name: getattr(pipeline, name) for name in (
        "DirectTransformer", "feature_adjustment_records", "enrich_delivery_metrics",
        "write_delivery", "write_styles", "write_source_gpkg", "_manifest_validation_summary",
    )}
    build_units, analyze_styles = units.build_unit_crs_contract, styles.analyze_style_coverage
    real_match = osm_matching.match_osm
    origins = {"MAIN": (12_600_000.0, -800_000.0), "PANEL": (12_605_000.0, -800_000.0)}
    shifts = {"MAIN": (100, 200), "PANEL": (900, -500)}
    records, calls = {}, []

    def configure(patch, second=False):
        def setup(module, model, source):
            for name, value in originals.items():
                patch.setattr(module, name, value)
            patch.setattr(units, "build_unit_crs_contract", build_units)
            patch.setattr(styles, "analyze_style_coverage", analyze_styles)
            module.SourceProfile.load(source.parent / "source_profile.json").expectations.feature_counts = {"PTECH": 2}
            (source.parent / "delivery_partitions.json").write_text(json.dumps({"partitions": [
                {"region_id": "PANEL", "label": "Independent panel", "bbox": [12_604_900, -800_100, 12_605_300, -799_900]},
            ]}), encoding="utf-8")
            nominal = originals["DirectTransformer"]("EPSG:3857", "EPSG:3857", unit_contract=build_units(
                6, "EPSG:3857", "EPSG:3857", source_coordinate_scale_to_m=1.0, source_coordinate_scale_reviewed=True,
            ))
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            features = [model.Feature(
                region, "PTECH", "Point", [point], "source-line", region, "FIBER",
                "source_observed", model.CadStyle(aci_color=1), display_label=region,
                label_provenance="DWG_DIRECT:fixture-text",
            ) for region, point in origins.items()]
            # The selected PANEL is clean; the source MAIN panel still has an
            # engineering derivation requiring review during the second run.
            features[0].lineage.append({"operation": "collocate_with_support"})
            patch.setattr(module, "classify_entities", lambda *_args, **_kwargs: (
                features, [], [], {"annotation_assignments_by_family": {}, "coverage": {"conversion_allowed": True}},
            ))
            if second:
                origin = origins["PANEL"]
                controls = [{"point_id": str(i), "cad_x": x, "cad_y": y,
                    "target_easting": x + 700, "target_northing": y - 300,
                    "target_crs": "EPSG:3857", "role": "train"}
                    for i, (x, y) in enumerate([origin, (origin[0] + 100, origin[1])])]
                record = osm_matching.refine_match(records["PANEL"], nominal, controls,
                    source_sha256=source_hash, parent_run_sha256="a" * 64, region_id="PANEL")
                profile = source.parent / "match.json"
                profile.write_text(json.dumps(record), encoding="utf-8")
                return {"matching": "osm", "match_profile": profile}

            geographic = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
            for region, (x, y) in origins.items():
                dx, dy = shifts[region]
                routes = [[(x, y), (x + 100, y), (x + 140, y + 40)],
                          [(x + 100, y), (x + 100, y - 80), (x + 170, y - 120)]]
                roads = [{"type": "way", "id": index + 1, "tags": {"highway": "residential"},
                    "geometry": [{"lon": lon, "lat": lat} for lon, lat in
                        [geographic.transform(px + dx, py + dy) for px, py in route]]}
                    for index, route in enumerate(routes)]
                data = source.parent / f"{region}.json"
                data.write_text(json.dumps({"elements": roads}), encoding="utf-8")
                _, record = real_match(source=source, source_sha256=source_hash,
                    features=[SimpleNamespace(feature_class="CABLE", native_points=route) for route in routes],
                    nominal=nominal, coordinate_domain={"passed": True}, osm_data=data, region_id=region)
                assert record["status"] == "matched"
                records[region] = record

            def match_scoped(**kwargs):
                region = kwargs["region_id"]
                assert [feature.feature_key for feature in kwargs["features"]] == [region]
                calls.append(region)
                return osm_matching.transformer_from_record(records[region], nominal), records[region]
            patch.setattr(osm_matching, "match_osm", match_scoped)
            return {"matching": "osm"}
        return setup

    first_dir = tmp_path / "first"
    first_dir.mkdir()
    with monkeypatch.context() as patch:
        first, _ = _run_fake_conversion(first_dir, patch, configure=configure(patch))
    assert sorted(calls) == ["MAIN", "PANEL"]
    first_manifest = json.loads(first.run_manifest_path.read_text(encoding="utf-8"))
    panel_path = first.delivery_path.parent / "PANEL" / "delivery.gpkg"
    saved_bytes = (first.delivery_path.read_bytes(), panel_path.read_bytes())

    from osgeo import ogr
    def point(path):
        dataset = ogr.Open(str(path), 0)
        layer = dataset.GetLayerByName("PTECH")
        assert layer.GetFeatureCount() == 1
        row = layer.GetNextFeature()
        return row.GetGeometryRef().GetPoint_2D(0)

    assert point(first.delivery_path) == pytest.approx((12_600_100, -799_800), abs=0.1)
    assert point(panel_path) == pytest.approx((12_605_900, -800_500), abs=0.1)
    assert first_manifest["delivery_partitions"]["PANEL"]["osm_matching"]["region_id"] == "PANEL"
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    with monkeypatch.context() as patch:
        second, _ = _run_fake_conversion(second_dir, patch, configure=configure(patch, second=True))
    second_manifest = json.loads(second.run_manifest_path.read_text(encoding="utf-8"))
    assert second_manifest["delivery_partition"]["region_id"] == "PANEL"
    assert second_manifest["delivery_partitions"] == {}
    engineering = second_manifest["validation"]["engineering_review"]
    assert engineering["passed"] is None
    assert engineering["scope"] == "all_source_regions"
    assert engineering["region_ids"] == ["MAIN", "PANEL"]
    assert engineering["output_region_ids"] == ["PANEL"]
    assert engineering["geometry_change_feature_count"] == 1
    assert engineering["geometry_lineage_operation_counts"] == {"collocate_with_support": 1}
    assert not (second.delivery_path.parent / "PANEL").exists()
    assert point(second.delivery_path) == pytest.approx((12_605_700, -800_300), abs=0.1)
    assert (first.delivery_path.read_bytes(), panel_path.read_bytes()) == saved_bytes


@pytest.mark.parametrize("main_present", [True, False])
def test_local_panel_admission_is_independent_and_empty_main_does_not_fail_run(tmp_path, monkeypatch, main_present):
    originals = {name: getattr(pipeline, name) for name in (
        "DirectTransformer", "feature_adjustment_records", "enrich_delivery_metrics",
        "write_delivery", "write_styles", "write_source_gpkg", "_manifest_validation_summary",
    )}
    build_units, analyze_styles = units.build_unit_crs_contract, styles.analyze_style_coverage

    def setup(module, model, source):
        for name, value in originals.items():
            monkeypatch.setattr(module, name, value)
        monkeypatch.setattr(units, "build_unit_crs_contract", build_units)
        monkeypatch.setattr(styles, "analyze_style_coverage", analyze_styles)
        profile = module.SourceProfile.load(source.parent / "source_profile.json")
        profile.expectations.feature_counts = {"PTECH": 2 if main_present else 1}
        if not main_present:
            profile.source_crs = None
            profile.local_registration_strategy = "osm_relative"
            profile.local_registration_reviewed = True
        (source.parent / "delivery_partitions.json").write_text(json.dumps({"partitions": [
            {"region_id": "LOCAL", "label": "Local panel", "bbox": [-100, -100, 100, 100]},
        ]}), encoding="utf-8")
        positions = [("LOCAL", (10.0, 20.0))]
        if main_present:
            positions.append(("MAIN", (12_600_000.0, -800_000.0)))
        features = [model.Feature(name, "PTECH", "Point", [point], "source-line", name, "FIBER",
            "source_observed", model.CadStyle(aci_color=1), display_label=name,
            label_provenance="DWG_DIRECT:fixture-text") for name, point in positions]
        monkeypatch.setattr(module, "classify_entities", lambda *_args, **_kwargs: (
            features, [], [], {"annotation_assignments_by_family": {}, "coverage": {"conversion_allowed": True}},
        ))
        geographic = Transformer.from_crs(3857, 4326, always_xy=True)
        geometry = [{"lon": lon, "lat": lat} for lon, lat in [
            geographic.transform(12_600_000, -800_000), geographic.transform(12_600_100, -800_000),
        ]]
        data = source.parent / "osm.json"
        data.write_text(json.dumps({"elements": [
            {"type": "way", "id": 1, "tags": {"highway": "residential"}, "geometry": geometry},
        ]}), encoding="utf-8")
        return {"matching": "osm", "osm_data": data}

    result, _ = _run_fake_conversion(tmp_path, monkeypatch, configure=setup)
    manifest = json.loads(result.run_manifest_path.read_text(encoding="utf-8"))
    record = manifest["delivery_partitions"]["LOCAL"]["osm_matching"]
    assert record["status"] == "coarse"
    assert record["base"]["mode"] == "local"
    assert record["quality"]["reason"] == "no_source_routes"
    assert manifest["crs"]["coordinate_domains_by_region"]["LOCAL"]["passed"] is False
    assert manifest["validation"]["coordinate_accuracy"]["passed"] is None
    assert result.run_status == "CONDITIONAL"
    if not main_present:
        assert manifest["osm_matching"]["status"] == "unavailable"
    from osgeo import ogr
    dataset = ogr.Open(str(result.delivery_path.parent / "LOCAL" / "delivery.gpkg"), 0)
    row = dataset.GetLayerByName("PTECH").GetNextFeature()
    assert row.GetGeometryRef().GetPoint_2D(0) == pytest.approx((12_600_050, -800_000), abs=0.01)
