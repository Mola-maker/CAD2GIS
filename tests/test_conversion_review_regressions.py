"""Cross-boundary regressions found in the September conversion review."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cad2gis.native_runtime import ensure_osgeo_runtime

ensure_osgeo_runtime()

from cad2gis.cad2gis_v3.coordinate_domain import assess_coordinate_domain  # noqa: E402
from cad2gis.cad2gis_v3.implementation import (  # noqa: E402
    SnapshotVerificationError, _artifact_record,
    freeze_conversion_snapshot, verify_conversion_snapshot,
)
from cad2gis.cad2gis_v3.model import SourceEntity  # noqa: E402
from cad2gis.cad2gis_v3.pipeline import (  # noqa: E402
    ConversionRequest, _coordinate_domain_view, _denoise_plan_entities,
    _native_inventory_crs, _publish_run_bundle, convert,
)
from cad2gis.cad2gis_v3.run_status import RunStatus, publish_verified_alias  # noqa: E402
from cad2gis.cad2gis_v3.source_gpkg import write_source_gpkg  # noqa: E402
from cad2gis.cad2gis_v3.units import build_unit_crs_contract  # noqa: E402


def _line(points):
    return SourceEntity.from_record({
        "entity_key": "source:1", "source_sha256": "a" * 64,
        "source_file": "fixture.dwg", "handle": "1", "layout": "Model",
        "layout_role": "model", "cad_role": "model", "layer": "route",
        "object_name": "ACDBLINE", "dwg_type_name": "LINE",
        "points": points, "centroid": points[0],
    })


def test_published_run_cannot_be_replaced_or_enter_conversion(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    manifest = run / "run_manifest.json"
    manifest.write_text('{"run_status":"VERIFIED"}', encoding="utf-8")
    delivery = run / "delivery.gpkg"
    delivery.write_bytes(b"last surveyed delivery")
    alias = tmp_path / "latest_verified.json"
    publish_verified_alias(alias, RunStatus.VERIFIED, run, hashlib.sha256(manifest.read_bytes()).hexdigest())
    previous = {p: p.read_bytes() for p in (manifest, delivery, alias)}
    stage = tmp_path / ".stage"
    stage.mkdir()
    (stage / "run_manifest.json").write_text('{"run_status":"CONDITIONAL"}')
    with pytest.raises(FileExistsError, match="immutable"):
        _publish_run_bundle(stage, run)
    with pytest.raises(FileExistsError, match="immutable"):
        convert(ConversionRequest(tmp_path / "missing.dwg", run, tmp_path / "profile", tmp_path / "registry"))
    assert all(path.read_bytes() == data for path, data in previous.items())
    assert stage.is_dir()


@pytest.mark.parametrize("marker", ["source_manifest.json", "semantic_manifest.json"])
def test_published_source_or_semantic_snapshot_cannot_be_conversion_output(tmp_path, marker):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / marker).write_bytes(b'{"snapshot_sha256":"reviewed"}')
    (snapshot / "reader_records.json.gz").write_bytes(b"immutable reader facts")
    previous = {path.name: path.read_bytes() for path in snapshot.iterdir()}
    stage = tmp_path / ".stage"
    stage.mkdir()
    (stage / "run_manifest.json").write_bytes(b"{}")
    with pytest.raises(FileExistsError, match="immutable"):
        _publish_run_bundle(stage, snapshot)
    with pytest.raises(FileExistsError, match="immutable"):
        convert(ConversionRequest(
            tmp_path / "missing.dwg", snapshot, tmp_path / "profile", tmp_path / "registry",
            source_run=snapshot,
        ))
    assert {path.name: path.read_bytes() for path in snapshot.iterdir()} == previous


def test_reviewed_millimetres_use_crs_axis_units_for_domain_admission():
    entity = _line(((760000000.0, 605000000.0), (765000000.0, 610000000.0)))
    original_points = entity.points
    contract = build_unit_crs_contract(
        4, "EPSG:23846", "EPSG:23846",
        source_coordinate_scale_to_m=0.001, source_coordinate_scale_reviewed=True,
    )
    assert not assess_coordinate_domain([entity], "EPSG:23846")["passed"]
    view = _coordinate_domain_view([entity], contract)
    assert assess_coordinate_domain(view, "EPSG:23846")["passed"]
    assert view[0].points == ((760000.0, 605000.0), (765000.0, 610000.0))
    assert entity.points == original_points


@pytest.mark.parametrize("name", ["delivery_partitions", "spatial_regions"])
@pytest.mark.parametrize("change", ["appear", "modify", "disappear"])
def test_auxiliary_configuration_is_bound_including_absence(tmp_path, name, change):
    inputs = [tmp_path / name for name in ("source.dwg", "source_profile.json", "mapping_registry.json")]
    for path in inputs:
        path.write_bytes(b"{}")
    config = tmp_path / f"{name}.json"
    if change != "appear":
        config.write_bytes(b"{}")
    snapshot = freeze_conversion_snapshot(*inputs, code_root=tmp_path, code_paths=(), runtime={})
    assert verify_conversion_snapshot(snapshot)["verified"]
    if change == "disappear":
        config.unlink()
    else:
        config.write_bytes(b'{"changed":true}')
    with pytest.raises(SnapshotVerificationError, match=name):
        verify_conversion_snapshot(snapshot)


def test_raw_engineering_source_preserves_points_with_undefined_srs(tmp_path):
    from osgeo import ogr
    path = tmp_path / "source.gpkg"
    entity = _line(((0.0, 0.0), (100.0, 0.0)))
    write_source_gpkg(path, [entity], None)
    dataset = ogr.Open(str(path))
    layer = dataset.GetLayerByName("source_lines")
    srs = layer.GetSpatialRef()
    assert srs is None or srs.GetAuthorityCode(None) is None
    feature = layer.GetNextFeature()
    assert feature.GetGeometryRef().ExportToWkt() == "LINESTRING (0 0,100 0)"
    dataset = None


@pytest.mark.parametrize("layout,role", [("Layout1", "layout"), ("BLOCKDEF:frame", "block_definition")])
def test_raw_inventory_with_other_coordinate_spaces_has_no_projected_srs(layout, role):
    model = _line(((760000.0, 605000.0), (765000.0, 610000.0)))
    other = replace(_line(((0.0, 0.0), (100.0, 0.0))), layout=layout, cad_role=role)
    contract = SimpleNamespace(source_to_crs_axis_factor=1.0)
    domain = assess_coordinate_domain([model], "EPSG:23846")
    assert domain["passed"]
    assert _native_inventory_crs([model], "EPSG:23846", contract, domain) == "EPSG:23846"
    assert _native_inventory_crs([model, other], "EPSG:23846", contract, domain) is None


@pytest.mark.parametrize("mode", ["observe", "assist"])
@pytest.mark.parametrize("cache_exists", [True, False])
def test_conversion_denoising_keeps_frozen_inputs_and_records_decisions(tmp_path, monkeypatch, mode, cache_exists):
    from cad2gis.cad2gis_v3 import spatial_llm

    inputs = [tmp_path / name for name in ("source.dwg", "source_profile.json", "mapping_registry.json")]
    for path in inputs:
        path.write_bytes(b"{}")
    cache = tmp_path / "spatial_regions.json"
    if cache_exists:
        cache.write_bytes(b'{"clusters": [], "reviewed": true}')
    snapshot = freeze_conversion_snapshot(*inputs, code_root=tmp_path, code_paths=(), runtime={})
    decision = {"cluster_id": "test", "disposition": "legend", "confidence": 1.0, "applied": True}
    monkeypatch.setattr(spatial_llm, "classify_spatial_clusters", lambda **_: {
        "decisions": [decision], "flag_map_entries": {}, "diagnostics": {"llm_model": "fixture"},
    })
    monkeypatch.setattr(spatial_llm, "classify_layer_semantics", lambda **_: {"verdicts": []})
    request = ConversionRequest(*inputs[:1], tmp_path / "run", *inputs[1:], llm=mode)
    _, result = _denoise_plan_entities(
        [_line(((10000.0, 10000.0), (10100.0, 10000.0)))],
        SimpleNamespace(catalog_roots=frozenset()),
        SimpleNamespace(positive_route_layer_regex="route"), request,
    )
    assert result["diagnostics"]["llm_decisions"] == [decision]
    assert verify_conversion_snapshot(snapshot)["verified"]
    assert cache.exists() == cache_exists


def test_snapshot_hash_streams_source_without_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / "drawing.dwg"
    data = b"dwg" * 800000
    path.write_bytes(data)

    def forbidden(_path):
        raise AssertionError("source hashing must stream")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    record = _artifact_record(path, kind="source")
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    assert record["size_bytes"] == len(data)


def test_external_backend_is_the_module_actually_loaded_in_new_process(tmp_path):
    external = tmp_path / "external"
    for relative in ("cad2gis", "cad2gis/cad2gis_v3", "cad2gis/reader"):
        package = external / relative
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    module = external / "cad2gis/cad2gis_v3/pipeline.py"
    module.write_text("MARKER = 'external'\n", encoding="utf-8")
    code = (
        "import json; from cad2gis import runtime; "
        "module=runtime.load_backend_module('cad2gis.cad2gis_v3.pipeline'); "
        "print(json.dumps({'file': module.__file__, 'marker': module.MARKER}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=dict(os.environ, CAD2GIS_BACKEND_PATH=str(external)),
        text=True, encoding="utf-8", capture_output=True, check=True,
    )
    assert json.loads(result.stdout) == {"file": str(module), "marker": "external"}


def test_external_backend_change_after_import_fails_instead_of_mixing_code(tmp_path):
    package = tmp_path / "cad2gis/cad2gis_v3"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    reader = tmp_path / "cad2gis/reader"
    reader.mkdir()
    (reader / "__init__.py").write_text("")
    code = (
        "import os; from cad2gis import runtime; import cad2gis.cad2gis_v3; "
        "os.environ['CAD2GIS_BACKEND_PATH']=os.environ['TEST_EXTERNAL']; "
        "runtime._prepare_backend_import()"
    )
    env = dict(os.environ, TEST_EXTERNAL=str(tmp_path))
    env.pop("CAD2GIS_BACKEND_PATH", None)
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "different CAD2GIS backend is already loaded" in result.stderr


def test_partial_external_backend_is_rejected_before_executing_backend_code(tmp_path):
    package = tmp_path / "cad2gis/cad2gis_v3"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise AssertionError('backend must not execute')")
    code = "from cad2gis import runtime; runtime.load_backend_module('cad2gis.cad2gis_v3.pipeline')"
    result = subprocess.run(
        [sys.executable, "-c", code], env=dict(os.environ, CAD2GIS_BACKEND_PATH=str(tmp_path)),
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0
    assert "does not contain a valid cad2gis_v3 deployment" in result.stderr
    assert "AssertionError" not in result.stderr


def test_external_namespace_reader_remains_in_selected_deployment(tmp_path):
    backend = tmp_path / "cad2gis/cad2gis_v3"
    reader = tmp_path / "cad2gis/reader"
    backend.mkdir(parents=True)
    reader.mkdir()
    (backend / "__init__.py").write_text("")
    (backend / "pipeline.py").write_text("from ..reader import resolver")
    (reader / "resolver.py").write_text("MARKER = 'external'")
    code = (
        "import json; from cad2gis import runtime; "
        "m=runtime.load_backend_module('cad2gis.cad2gis_v3.pipeline'); "
        "again=runtime.load_backend_module('cad2gis.cad2gis_v3.pipeline'); "
        "print(json.dumps([m.resolver.__file__, m is again])); "
        "import cad2gis.reader.autocad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=dict(os.environ, CAD2GIS_BACKEND_PATH=str(tmp_path)),
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.stdout, result.stderr
    assert json.loads(result.stdout) == [str(reader / "resolver.py"), True]
    assert result.returncode != 0
    assert "No module named 'cad2gis.reader.autocad'" in result.stderr
