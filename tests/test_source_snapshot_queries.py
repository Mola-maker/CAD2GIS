from __future__ import annotations

import json
import os
import shutil
import sqlite3
from threading import Event
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3 import source_export, source_query


def row(key="A", **overrides):
    return {"entity_key": key, "handle": key, "dwg_type_name": "LINE",
            "layout": "Model", "layout_role": "model", "cad_role": "model",
            "layer": "线路", "points": [[100000000.12345678, 2.25], [100000002.98765433, 4.75]],
            "native_length": 3.801620894453, "text": "光缆-01/中文标注", **overrides}


def snapshot(tmp_path, records=None, name="run"):
    source = tmp_path / (name + ".dwg")
    source.write_bytes(("synthetic-source-" + name).encode())
    root = tmp_path / name
    manifest = source_export.export_source(source=source, run_dir=root,
                                            records=records if records is not None else [row()])
    return root, manifest


def test_unregistered_source_keeps_exact_facts_and_undefined_cartesian_srs(tmp_path):
    root, manifest = snapshot(tmp_path)
    assert manifest["coordinate_reference"] == {"state": "native_cad_unregistered",
                                               "source_crs": None, "units": "not_inferred", "transformed": False}
    assert manifest["reader_provenance"]["mode"] == "injected_records"
    assert manifest["snapshot_sha256"] == source_export.snapshot_digest(manifest)
    with sqlite3.connect(root / "source.gpkg") as db:
        value = db.execute("SELECT native_points,native_length FROM source_lines").fetchone()
        assert json.loads(value[0]) == row()["points"]
        assert value[1] == row()["native_length"]
        assert set(r[0] for r in db.execute("SELECT srs_id FROM gpkg_geometry_columns")) == {-1}
    assert json.loads((root / "reader_records.jsonl").read_text(encoding="utf-8")) == row()
    assert len((root / "plan_entities.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_export_refuses_nonempty_destination_without_deleting_anything(tmp_path):
    root, manifest = snapshot(tmp_path)
    before = (root / "source_manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        source_export.export_source(source=tmp_path / "run.dwg", run_dir=root, records=[row()])
    assert (root / "source_manifest.json").read_bytes() == before
    assert manifest["conservation"]["passed"] is True


def test_native_export_inventory_retains_reader_protocol_and_bootstrap_binding(tmp_path, monkeypatch):
    from cad2gis.reader.libredwg_cli import DWGRecordInventory
    from cad2gis.cad2gis_v3.project_profile import _inspect_source_facts

    source = tmp_path / "native.dwg"
    source.write_bytes(b"native-reader-fixture")
    records = DWGRecordInventory([row(source_sha256=source_export._sha256(source))], diagnostics={
        "backend": "libredwg_cli_dxf", "extraction_backend": "libredwg_cli_dxf",
        "inventory_complete": True, "skipped_rows": 0, "completion_rows": 1,
        "parsed_rows": 1, "total_rows": 1, "returned_records": 1,
    })
    monkeypatch.setattr(source_export, "_extract_records", lambda path: records)
    manifest = source_export.export_source(source=source, run_dir=tmp_path / "native-run")
    inventory = json.loads(Path(manifest["artifacts"]["source_inventory"]["path"]).read_text(encoding="utf-8"))
    expected, _, _, _ = _inspect_source_facts(source=source, records=records)
    assert inventory["reader_protocol"] == expected["reader_protocol"]
    assert inventory["reader_protocol"]["inventory_complete"] is True
    assert inventory["inventory_sha256"] == expected["inventory_sha256"]
    assert manifest["reader_provenance"]["mode"] == "native_reader"


def test_export_failure_never_publishes_partial_snapshot_and_keeps_audit(tmp_path, monkeypatch):
    source = tmp_path / "fail.dwg"
    source.write_bytes(b"failure fixture")
    real_write = source_export._write_json

    def fail_scene(path, payload):
        if path.name == "cad_scene_graph.json":
            raise RuntimeError("injected scene write failure")
        real_write(path, payload)

    monkeypatch.setattr(source_export, "_write_json", fail_scene)
    with pytest.raises(RuntimeError, match="injected scene"):
        source_export.export_source(source=source, run_dir=tmp_path / "failed", records=[row()])
    assert not (tmp_path / "failed").exists()
    audit = list(tmp_path.glob(".failed.staged-*/_failure.json"))
    assert len(audit) == 1
    assert json.loads(audit[0].read_text())["published"] is False


def test_publication_permission_failure_keeps_snapshot_unpublished(tmp_path, monkeypatch):
    source = tmp_path / "private.dwg"
    source.write_bytes(b"permission failure fixture")

    def deny_inheritance(path):
        assert path.parent == tmp_path
        assert (path / "source_manifest.json").is_file()
        raise PermissionError("cannot inherit output ACL")

    monkeypatch.setattr(source_export, "_inherit_output_permissions", deny_inheritance)
    with pytest.raises(PermissionError, match="output ACL"):
        source_export.export_source(source=source, run_dir=tmp_path / "private", records=[row()])
    assert not (tmp_path / "private").exists()
    failure = next(tmp_path.glob(".private.staged-*/_failure.json"))
    assert json.loads(failure.read_text())["published"] is False
    assert source.read_bytes() == b"permission failure fixture"


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL publication regression")
def test_windows_published_files_inherit_explicit_parent_user_acl(tmp_path):
    import ctypes
    import subprocess

    name = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(len(name))
    assert ctypes.windll.advapi32.GetUserNameW(name, ctypes.byref(size))
    # Give only the current owner an explicit inheritable entry on this fixture
    # parent; publication must inherit it instead of keeping mkdtemp's DACL.
    subprocess.run(["icacls.exe", str(tmp_path), "/grant", f"{name.value}:(OI)(CI)(RX)"],
                   capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    root, manifest = snapshot(tmp_path, name="inherited")
    permissions = subprocess.run(["icacls.exe", str(root / "source.gpkg")], capture_output=True,
                                 text=True, encoding="oem", check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert any(name.value.casefold() in line.casefold() and "(I)" in line
               for line in permissions.stdout.splitlines())
    assert manifest["snapshot_sha256"] == source_export.snapshot_digest(manifest)


def test_keyset_binds_filters_view_and_snapshot_and_handles_chinese_two_characters(tmp_path):
    root, _ = snapshot(tmp_path, [row(f"K{i:03}", layer="线路" if i % 2 else "建筑") for i in range(11)])
    first = source_query.query_source_entities(run_dir=root, text_query="光缆", layer="线路", limit=2)
    assert [v["entity_key"] for v in first["items"]] == ["K001", "K003"]
    second = source_query.query_source_entities(run_dir=root, text_query="光缆", layer="线路", limit=2, cursor=first["next_cursor"])
    assert [v["entity_key"] for v in second["items"]] == ["K005", "K007"]
    with pytest.raises(source_query.SourceQueryError, match="cursor"):
        source_query.query_source_entities(run_dir=root, text_query="中文", layer="线路", cursor=first["next_cursor"])
    with pytest.raises(source_query.SourceQueryError, match="cursor"):
        source_query.query_source_entities(run_dir=root, view="plan", text_query="光缆", layer="线路", cursor=first["next_cursor"])
    for query in ("中文标", "-01/", "光缆-01/中文标注"):
        assert source_query.query_source_entities(run_dir=root, text_query=query)["returned_count"] == 11
    index = source_query.source_index_path(root)
    assert not index.is_relative_to(root)
    before = index.read_bytes()
    source_query.build_source_index(root, rebuild=True)
    assert index.read_bytes() == before


def test_rtree_float32_candidates_are_filtered_with_authoritative_double_bounds(tmp_path):
    root, _ = snapshot(tmp_path, [row("far", points=[[100000001.2, 10.0]], dwg_type_name="POINT"),
                                   row("near", points=[[100000001.0, 10.0]], dwg_type_name="POINT")])
    result = source_query.query_source_entities(run_dir=root, bbox=[100000000.99, 9.99, 100000001.01, 10.01])
    assert [item["entity_key"] for item in result["items"]] == ["near"]


@pytest.mark.parametrize("bulge,y", [(1.0, -5.0), (-1.0, 5.0)])
def test_spatial_queries_include_bulged_arc_midpoint_outside_vertex_bbox(tmp_path, bulge, y):
    curve = {"schema_version": "cad2gis-curve-facts-v1", "coordinate_system": "WCS",
             "primitive_type": "LWPOLYLINE", "vertices_wcs": [[0, 0, 0], [10, 0, 0]],
             "bulges": [bulge, 0], "closed": False}
    root, _ = snapshot(tmp_path, [row("arc", dwg_type_name="LWPOLYLINE", points=[[0, 0], [10, 0]], curve_facts=curve)])
    for view in ("source", "plan"):
        result = source_query.query_source_entities(run_dir=root, view=view, bbox=[4.9, y-0.1, 5.1, y+0.1])
        assert [item["entity_key"] for item in result["items"]] == ["arc"]
        assert result["items"][0]["bounds_quality"] == "analytic_bulge_envelope_candidate"
        assert source_query.query_source_entities(run_dir=root, view=view, bbox=[4.9, -y-0.1, 5.1, -y+0.1])["items"] == []


def test_unknown_curve_is_not_excluded_by_narrow_sampled_vertex_bounds(tmp_path):
    root, _ = snapshot(tmp_path, [row("spline", dwg_type_name="SPLINE", points=[[0, 0], [10, 0]])])
    result = source_query.query_source_entities(run_dir=root, bbox=[4, 4, 6, 6])
    assert result["items"][0]["entity_key"] == "spline"
    assert result["items"][0]["bounds_quality"] == "unbounded_curve_candidate"


def test_plan_circle_envelope_maps_definition_parameters_through_instance_affine(tmp_path):
    circle = {"schema_version": "cad2gis-curve-facts-v1", "coordinate_system": "WCS", "primitive_type": "CIRCLE",
              "vertices_wcs": [[0, 0, 0], [10, 0, 0]], "primitive_parameters": {"center_wcs": [5, 0, 0], "radius": 5}}
    root, _ = snapshot(tmp_path, [
        row("insert", dwg_type_name="INSERT", cad_role="style_legend", block_name="CIRCLE_BLOCK", points=[[100, 200]],
            raw_properties={"transform_facts": {"insertion_point": [100, 200, 0], "block_base_point": [0, 0, 0],
                "scale": [2, 2, 1], "rotation": 0, "normal": [0, 0, 1], "extrusion": [0, 0, 1]}}),
        row("definition-circle", dwg_type_name="CIRCLE", layout="BLOCKDEF:CIRCLE_BLOCK", layout_role="block_definition",
            cad_role="block_definition", points=[[0, 0], [10, 0]], curve_facts=circle),
    ])
    result = source_query.query_source_entities(run_dir=root, view="plan", dwg_type="CIRCLE", bbox=[109, 209, 111, 211])
    assert len(result["items"]) == 1
    assert result["items"][0]["bounds_quality"] == "conservative_primitive_envelope_candidate"


@pytest.mark.parametrize("kind,parameters", [("CIRCLE", {"center_wcs": [5, 5, 0], "radius": 5}),
                                              ("ARC", {"center": [5, 5, 0], "radius": 5}),
                                              ("ELLIPSE", {"center_wcs": [5, 5, 0], "major_axis": [5, 0, 0], "radius_ratio": 0.5})])
def test_native_curve_parameters_produce_conservative_double_envelopes(tmp_path, kind, parameters):
    curve = {"schema_version": "cad2gis-curve-facts-v1", "coordinate_system": "WCS", "primitive_type": kind,
             "vertices_wcs": [[5, 5, 0], [6, 5, 0]], "primitive_parameters": parameters}
    root, _ = snapshot(tmp_path, [row("curve", dwg_type_name=kind, points=[[5, 5], [6, 5]], curve_facts=curve)])
    result = source_query.query_source_entities(run_dir=root, bbox=[9.9, 4.9, 10.1, 5.1])
    assert result["items"][0]["bounds_quality"] == "conservative_primitive_envelope_candidate"


def test_source_and_index_mismatch_fail_closed(tmp_path):
    first, _ = snapshot(tmp_path, name="first")
    second, _ = snapshot(tmp_path, name="second")
    a = Path(source_query.build_source_index(first)["path"])
    b = Path(source_query.build_source_index(second)["path"])
    shutil.copyfile(b, a)
    shutil.copyfile(b.with_suffix(".json"), a.with_suffix(".json"))
    with pytest.raises(source_query.SourceQueryError, match="binding mismatch"):
        source_query.query_source_entities(run_dir=first)
    source_query.build_source_index(first, rebuild=True)
    with (first / "source.gpkg").open("ab") as stream:
        stream.write(b"mutation")
    with pytest.raises(source_query.SourceQueryError, match="hash/path mismatch"):
        source_query.query_source_entities(run_dir=first)


def test_same_length_restored_mtime_mutation_invalidates_verified_hash_cache(tmp_path):
    root, _ = snapshot(tmp_path)
    source_query.query_source_entities(run_dir=root)
    path = root / "reader_records.jsonl"
    before = path.stat()
    value = path.read_bytes()
    path.write_bytes(value.replace(b'"A"', b'"B"', 1))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(source_query.SourceQueryError) as raised:
        source_query.query_source_entities(run_dir=root)
    assert raised.value.code == "ARTIFACT_BINDING_INVALID"


def test_context_budget_continues_lossless_large_fields_and_never_claims_partial_complete(tmp_path):
    text = "中文-很长的原始标注\"\\" * 3000
    root, _ = snapshot(tmp_path, [row(text=text)])
    cursor = None
    parts = []
    pages = 0
    while True:
        result = source_query.get_entity_context_batch(run_dir=root, entity_keys=["A"], fields=["text"],
                                                        cursor=cursor, max_bytes=4096)
        assert len(source_query._json(result).encode("utf-8")) == result["response_bytes"] <= 4096
        for item in result["items"]:
            for chunk in item.get("field_chunks", []):
                assert chunk["offset"] == sum(len(part) for part in parts)
                parts.append(chunk["value"])
        pages += 1
        cursor = result["next_cursor"]
        if cursor is None:
            assert result["items"][-1]["complete"] is True
            break
        assert result["items"][-1]["complete"] is False
        assert pages < 400
    assert pages > 1
    assert json.loads("".join(parts)) == text


def test_context_small_facts_are_grouped_and_unknown_keys_rejected(tmp_path):
    root, _ = snapshot(tmp_path)
    result = source_query.get_entity_context_batch(run_dir=root, entity_keys=["A"], fields=["native_points", "native_length"])
    assert result["items"] == [{"entity_key": "A", "view": "source", "facts": {
        "native_points": row()["points"], "native_length": row()["native_length"]}, "complete": True}]
    with pytest.raises(source_query.SourceQueryError, match="Unknown"):
        source_query.get_entity_context_batch(run_dir=root, entity_keys=["not-observed"])


def test_warm_query_reads_only_metadata_and_bounded_sql_rows(tmp_path, monkeypatch):
    root, _ = snapshot(tmp_path, [row(f"K{i:04d}") for i in range(100)])
    source_query.build_source_index(root)
    read_text = Path.read_text

    def metadata_only(path, *args, **kwargs):
        assert path.name in {"source_manifest.json", "source_index.json"}, "Warm query parsed a full artifact"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", metadata_only)
    result = source_query.query_source_entities(run_dir=root, limit=3)
    assert result["returned_count"] == 3
    assert result["next_cursor"]
    context = source_query.get_entity_context_batch(run_dir=root, entity_keys=["K0001", "K0002"], fields=["native_points"])
    assert context["returned_count"] == 2


def test_internal_cancellation_is_typed_and_does_not_leak_between_queries(tmp_path):
    root, _ = snapshot(tmp_path)
    event = Event()
    event.set()
    with source_query.query_cancellation(event):
        with pytest.raises(source_query.SourceQueryError) as raised:
            source_query.query_source_entities(run_dir=root)
    assert raised.value.code == "CANCELLED"
    assert source_query.query_source_entities(run_dir=root)["returned_count"] == 1


def test_sql_budget_and_stale_cursor_have_stable_error_codes(tmp_path, monkeypatch):
    root, _ = snapshot(tmp_path, [row("A"), row("B")])
    first = source_query.query_source_entities(run_dir=root, limit=1)
    with pytest.raises(source_query.SourceQueryError) as raised:
        source_query.query_source_entities(run_dir=root, layer="other", cursor=first["next_cursor"])
    assert raised.value.code == "STALE_CURSOR"
    real_open = source_query._open_query

    def expired(*args, **kwargs):
        db, metadata, started = real_open(*args, **kwargs)
        return db, metadata, started - 10

    monkeypatch.setattr(source_query, "_open_query", expired)
    with pytest.raises(source_query.SourceQueryError) as raised:
        source_query.query_source_entities(run_dir=root)
    assert raised.value.code == "QUERY_BUDGET_EXCEEDED"


def test_plan_instances_have_separate_namespace_and_exact_affine_lineage(tmp_path):
    root, _ = snapshot(tmp_path, [
        row("root", dwg_type_name="INSERT", cad_role="style_legend", block_name="NETWORK",
            points=[[100.0, 200.0]], raw_properties={"transform_facts": {
                "insertion_point": [100.0, 200.0, 0.0], "block_base_point": [0.0, 0.0, 0.0],
                "scale": [2.0, 2.0, 1.0], "rotation": 0.0,
                "normal": [0.0, 0.0, 1.0], "extrusion": [0.0, 0.0, 1.0]}}),
        row("definition", layout="BLOCKDEF:NETWORK", layout_role="block_definition", cad_role="block_definition",
            points=[[0.0, 0.0], [10.0, 0.0]], native_length=10.0),
    ])
    raw = source_query.query_source_entities(run_dir=root)
    plan = source_query.query_source_entities(run_dir=root, view="plan", dwg_type="LINE")
    assert {item["entity_key"] for item in raw["items"]} == {"root", "definition"}
    assert len(plan["items"]) == 1
    key = plan["items"][0]["entity_key"]
    facts = source_query.get_entity_context_batch(run_dir=root, view="plan", entity_keys=[key],
                                                  fields=["native_points", "native_length", "lineage"])["items"][0]["facts"]
    assert facts["native_points"] == [[100.0, 200.0], [120.0, 200.0]]
    assert facts["native_length"] == 20.0
    assert facts["lineage"]["definition_entity_key"] == "definition"
    assert facts["lineage"]["root_entity_key"] == "root"
    assert "affine" in facts["lineage"]
    with pytest.raises(source_query.SourceQueryError, match="Unknown"):
        source_query.get_entity_context_batch(run_dir=root, entity_keys=[key])


@pytest.mark.parametrize("kwargs", [{"limit": 201}, {"limit": 0}, {"projection": ["geom;DROP TABLE entities"]},
                                    {"bbox": [0, 0, float("inf"), 1]}, {"timeout_ms": 0}, {"max_bytes": 100000}])
def test_queries_reject_unbounded_or_untyped_inputs(tmp_path, kwargs):
    root, _ = snapshot(tmp_path)
    with pytest.raises(source_query.SourceQueryError):
        source_query.query_source_entities(run_dir=root, **kwargs)
