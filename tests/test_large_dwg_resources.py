"""Keep graph identity and database publication stable under resource pressure."""
import hashlib
import gzip
import io
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tracemalloc

import pytest

from cad2gis.cad2gis_v3 import cad_scene_graph as scene
from cad2gis.cad2gis_v3 import gpkg_metadata
from cad2gis.cad2gis_v3 import evidence_graph as evidence
from cad2gis.cad2gis_v3.artifact_io import write_json_object


@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
def test_streamed_artifact_preserves_canonical_bytes_and_bounds_memory(tmp_path, suffix):
    payload = {"records": [{"name": "主图", "payload": "x" * 4096} for _ in range(1000)]}
    expected = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if suffix.endswith("gz"):
        buffer = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
            compressed.write(expected)
        expected = buffer.getvalue()
    tracemalloc.start()
    try:
        path = tmp_path / ("manifest" + suffix)
        write_json_object(path, payload)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert path.read_bytes() == expected
    assert peak < 4096 * 1000


@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
def test_failed_streaming_preserves_previous_artifact(tmp_path, suffix):
    path = tmp_path / ("manifest" + suffix)
    path.write_bytes(b"previous artifact")
    with pytest.raises(TypeError):
        write_json_object(path, {"partial": [1, 2, object()]})
    assert path.read_bytes() == b"previous artifact"
    assert list(tmp_path.iterdir()) == [path]


def test_streamed_graph_hash_matches_existing_canonical_identity():
    nodes = [scene.CadSceneNode.create(source_sha256="a" * 64, kind="layout", logical_id=name,
                                      facts={"name": name, "nested": {"z": -0.0, "a": [1, True, None]}})
             for name in ("second", "主图")]
    graph = scene.CadSceneGraph.create(source_sha256="a" * 64, nodes=reversed(nodes), edges=[], diagnostics={"order": [2, 1]})
    identity = graph.to_dict()
    identity.pop("graph_sha256")
    expected = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert graph.graph_sha256 == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_graph_hash_peak_memory_is_below_full_fact_payload():
    nodes = [scene.CadSceneNode.create(source_sha256="a" * 64, kind="layout", logical_id=f"layout-{i}",
                                      facts={"payload": "x" * 4096}) for i in range(1000)]
    tracemalloc.start()
    try:
        graph = scene.CadSceneGraph.create(source_sha256="a" * 64, nodes=nodes, edges=[], diagnostics={})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(graph.nodes) == 1000
    assert peak < 4096 * 1000


def test_evidence_graph_hash_preserves_identity_with_bounded_memory():
    nodes = [evidence.EvidenceNode.create(source_sha256="a" * 64, kind="source_entity",
                                          logical_id=f"entity-{i}", facts={"payload": "主图" * 2048})
             for i in range(1000)]
    edge = evidence.EvidenceEdge.create(source_sha256="a" * 64, kind="derived_from",
                                        source_node_id=nodes[0].node_id, target_node_id=nodes[1].node_id,
                                        facts={"nested": {"a": [True, None, -0.0]}})
    tracemalloc.start()
    try:
        graph = evidence.EvidenceGraph.create(source_sha256="a" * 64, nodes=reversed(nodes), edges=[edge])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    identity = graph.to_dict()
    identity.pop("graph_sha256")
    expected = evidence._canonical_json(identity).encode("utf-8")
    assert graph.graph_sha256 == hashlib.sha256(expected).hexdigest()
    assert peak < 4096 * 1000


def database(path: Path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""CREATE TABLE gpkg_contents(table_name TEXT PRIMARY KEY,last_change TEXT);
            INSERT INTO gpkg_contents VALUES('features','2026-09-10T00:00:00.000Z');
            CREATE TABLE features(id INTEGER PRIMARY KEY, value TEXT);
            INSERT INTO features VALUES(42,'unchanged source attributes');""")
        db.commit()
        gpkg_metadata.normalize_geopackage_metadata(db, vacuum=False)


def test_compaction_preserves_values_and_uses_destination_drive(tmp_path, monkeypatch):
    path = tmp_path / "generated.gpkg"
    database(path)
    original = gpkg_metadata.tempfile.mkstemp
    observed = []

    def allocate(*args, **kwargs):
        observed.append(Path(kwargs["dir"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(gpkg_metadata.tempfile, "mkstemp", allocate)
    gpkg_metadata.compact_geopackage(path)
    assert observed == [tmp_path]
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT * FROM features").fetchall() == [(42, "unchanged source attributes")]
        assert db.execute("SELECT last_change FROM gpkg_contents").fetchone()[0] == gpkg_metadata.CANONICAL_GPKG_TIMESTAMP
    assert list(tmp_path.iterdir()) == [path]
    other = tmp_path / "same-data.gpkg"
    database(other)
    gpkg_metadata.compact_geopackage(other)
    assert path.read_bytes() == other.read_bytes()


def test_failed_compaction_keeps_original_and_removes_partial_file(tmp_path, monkeypatch):
    path = tmp_path / "generated.gpkg"
    database(path)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("simulated destination out of space")

    monkeypatch.setattr(gpkg_metadata.os, "replace", fail)
    with pytest.raises(OSError, match="out of space"):
        gpkg_metadata.compact_geopackage(path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["evidence", "scene"])
@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
def test_graph_record_stream_preserves_bytes_without_materializing_facts(tmp_path, kind, suffix):
    from cad2gis.cad2gis_v3.artifact_io import write_graph_object
    node_cls = evidence.EvidenceNode if kind == "evidence" else scene.CadSceneNode
    graph_cls = evidence.EvidenceGraph if kind == "evidence" else scene.CadSceneGraph
    nodes = [node_cls.create(source_sha256="a" * 64, kind="source_entity" if kind == "evidence" else "layout", logical_id=str(i),
                            facts={"text": "图纸", "data": "x" * 4096}) for i in range(1000)]
    kwargs = {} if kind == "evidence" else {"diagnostics": {"status": "ok"}}
    graph = graph_cls.create(source_sha256="a" * 64, nodes=nodes, edges=[], **kwargs)
    reference = tmp_path / ("reference" + suffix)
    write_json_object(reference, graph.to_dict())
    output = tmp_path / ("streamed" + suffix)
    tracemalloc.start()
    try:
        write_graph_object(output, graph)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert output.read_bytes() == reference.read_bytes()
    assert peak < 2_000_000
