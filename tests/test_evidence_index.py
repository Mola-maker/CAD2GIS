from __future__ import annotations

import hashlib
import gzip
import json
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.evidence_graph import EvidenceGraph, EvidenceNode
from cad2gis.cad2gis_v3.evidence_index import (
    EVIDENCE_INDEX_FILENAME,
    EvidenceIndexError,
    get_indexed_evidence_node,
    page_evidence_nodes,
    resolve_evidence_index,
    validate_index_manifest_binding,
    write_evidence_index,
)


SOURCE_SHA = "1" * 64


def _graph(count: int = 7) -> EvidenceGraph:
    return EvidenceGraph.create(
        source_sha256=SOURCE_SHA,
        nodes=(
            EvidenceNode.create(
                source_sha256=SOURCE_SHA,
                logical_id=f"entity:{index}",
                kind="source_entity" if index % 2 == 0 else "feature",
                facts={"index": index, "label": f"L-{index}"},
            )
            for index in range(count)
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _official_run(tmp_path: Path, graph: EvidenceGraph | None = None) -> tuple[Path, Path, Path]:
    graph = graph or _graph()
    reasoning = tmp_path / "reasoning"
    reasoning.mkdir(parents=True, exist_ok=True)
    graph_path = reasoning / "evidence_graph.json.gz"
    with gzip.open(graph_path, "wt", encoding="utf-8") as handle:
        json.dump(graph.to_dict(), handle)
    index = write_evidence_index(reasoning / EVIDENCE_INDEX_FILENAME, graph)
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(json.dumps({
        "source": {"sha256": graph.source_sha256},
        "artifacts": {
            "evidence_graph": {
                "path": str(graph_path.relative_to(tmp_path)),
                "sha256": _sha256(graph_path),
                "graph_sha256": graph.graph_sha256,
            },
            "evidence_index": {
                "path": str(index.relative_to(tmp_path)),
                "sha256": _sha256(index),
                "graph_sha256": graph.graph_sha256,
            },
        },
    }), encoding="utf-8")
    return graph_path, index, manifest_path


def test_index_pages_and_reads_content_addressed_nodes(tmp_path: Path) -> None:
    graph = _graph()
    path = write_evidence_index(tmp_path / EVIDENCE_INDEX_FILENAME, graph)

    page = page_evidence_nodes(
        path, kind="source_entity", cursor=1, limit=2,
    )
    assert page["query_backend"] == "sqlite-index"
    assert page["graph_sha256"] == graph.graph_sha256
    assert page["total"] == 4
    assert page["cursor"] == 1
    assert page["next_cursor"] == 3
    assert [item["logical_id"] for item in page["nodes"]] == [
        graph.nodes[index].logical_id
        for index in range(len(graph.nodes))
        if graph.nodes[index].kind == "source_entity"
    ][1:3]

    selected = get_indexed_evidence_node(path, page["nodes"][0]["node_id"])
    assert selected is not None
    assert selected["facts"]["label"].startswith("L-")


def test_official_index_is_bound_to_run_manifest(tmp_path: Path) -> None:
    _, path, _ = _official_run(tmp_path)
    assert validate_index_manifest_binding(path) is True

    with path.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(EvidenceIndexError, match="does not match"):
        validate_index_manifest_binding(path)


def test_mcp_prefers_index_without_loading_large_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cad2gis import agent_mcp
    graph = _graph()
    reasoning = tmp_path / "reasoning"
    reasoning.mkdir()
    graph_path = reasoning / "evidence_graph.json"
    graph_path.write_text(json.dumps(graph.to_dict()), encoding="utf-8")
    write_evidence_index(reasoning / EVIDENCE_INDEX_FILENAME, graph)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        agent_mcp,
        "_load_graph",
        lambda _path: (_ for _ in ()).throw(AssertionError("JSON graph loaded")),
    )

    page = agent_mcp.list_evidence_nodes(str(graph_path), limit=2, allow_standalone=True)
    assert page["query_backend"] == "sqlite-index"
    assert len(page["nodes"]) == 2
    node = agent_mcp.get_evidence_node(
        str(graph_path), page["nodes"][0]["node_id"], allow_standalone=True,
    )
    assert node["node_id"] == page["nodes"][0]["node_id"]


def test_official_binding_checks_requested_graph_and_source(tmp_path: Path) -> None:
    graph_path, _, _ = _official_run(tmp_path)
    binding = resolve_evidence_index(graph_path, expected_source_sha256=SOURCE_SHA)
    assert binding is not None and binding.binding_status == "manifest_bound"
    wrong_graph = graph_path.with_name("another_graph.json.gz")
    wrong_graph.write_bytes(graph_path.read_bytes())
    with pytest.raises(EvidenceIndexError, match="another requested evidence graph"):
        resolve_evidence_index(wrong_graph)
    with pytest.raises(EvidenceIndexError, match="requested source"):
        resolve_evidence_index(graph_path, expected_source_sha256="f" * 64)
    with pytest.raises(EvidenceIndexError, match="requested graph"):
        resolve_evidence_index(graph_path, expected_graph_sha256="f" * 64)


@pytest.mark.parametrize("manifest", ["{broken", "[]", "{}", '{"artifacts":{}}'])
def test_present_incomplete_manifest_fails_even_with_standalone_opt_in(
    tmp_path: Path, manifest: str,
) -> None:
    graph_path, _, manifest_path = _official_run(tmp_path)
    manifest_path.write_text(manifest, encoding="utf-8")
    with pytest.raises(EvidenceIndexError, match="manifest"):
        resolve_evidence_index(graph_path, allow_standalone=True)


def test_missing_manifest_requires_explicit_standalone_access(tmp_path: Path) -> None:
    graph_path, _, manifest_path = _official_run(tmp_path)
    manifest_path.unlink()
    with pytest.raises(EvidenceIndexError, match="manifest is missing"):
        resolve_evidence_index(graph_path)
    with pytest.raises(EvidenceIndexError, match="manifest is missing"):
        validate_index_manifest_binding(graph_path.with_name(EVIDENCE_INDEX_FILENAME))
    binding = resolve_evidence_index(graph_path, allow_standalone=True)
    assert binding is not None and binding.binding_status == "standalone_unbound"
    assert binding.manifest_path is None


def test_standalone_index_must_match_actual_graph_content(tmp_path: Path) -> None:
    graph_path, _, manifest_path = _official_run(tmp_path)
    manifest_path.unlink()
    with gzip.open(graph_path, "wt", encoding="utf-8") as handle:
        json.dump(_graph(3).to_dict(), handle)
    with pytest.raises(EvidenceIndexError, match="requested graph/source"):
        resolve_evidence_index(graph_path, allow_standalone=True)


@pytest.mark.parametrize("target", ["source", "evidence_graph", "evidence_index"])
def test_manifest_binding_changes_invalidate_cached_validation(
    tmp_path: Path, target: str,
) -> None:
    graph_path, _, manifest_path = _official_run(tmp_path)
    assert resolve_evidence_index(graph_path) is not None
    before = manifest_path.stat()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if target == "source":
        manifest["source"]["sha256"] = "f" * 64
    else:
        manifest["artifacts"][target]["graph_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # Even when a caller restores mtime and retains size, the manifest content
    # digest remains a cache input. This reproduces the old stale-trust window.
    import os
    os.utime(manifest_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(EvidenceIndexError, match="binding does not match"):
        resolve_evidence_index(graph_path)


def test_graph_and_index_changes_invalidate_cache(tmp_path: Path) -> None:
    graph_path, index, _ = _official_run(tmp_path)
    assert resolve_evidence_index(graph_path) is not None
    with graph_path.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(EvidenceIndexError, match="graph does not match"):
        resolve_evidence_index(graph_path)
    graph_path, index, _ = _official_run(tmp_path)
    assert resolve_evidence_index(graph_path) is not None
    write_evidence_index(index, _graph(3))
    with pytest.raises(EvidenceIndexError, match="index does not match"):
        resolve_evidence_index(graph_path)


def test_official_missing_index_does_not_fall_back_to_json(tmp_path: Path) -> None:
    graph_path, index, _ = _official_run(tmp_path)
    index.unlink()
    with pytest.raises(EvidenceIndexError, match="index is missing"):
        resolve_evidence_index(graph_path)


def test_warm_binding_never_reparses_or_rehashes_large_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cad2gis.cad2gis_v3 import evidence_index
    graph_path, _, _ = _official_run(tmp_path)
    binding = resolve_evidence_index(graph_path)
    def unexpected(*_args, **_kwargs):
        raise AssertionError("warm lookup read full graph/index")
    monkeypatch.setattr(EvidenceGraph, "from_dict", unexpected)
    monkeypatch.setattr(evidence_index, "_sha256_file", unexpected)
    assert resolve_evidence_index(graph_path) == binding


@pytest.mark.parametrize("target", ["graph", "index"])
def test_same_size_artifact_change_with_restored_mtime_invalidates_cache(tmp_path, target):
    import os
    from cad2gis.cad2gis_v3.artifact_io import file_cache_identity

    graph_path, index_path, _ = _official_run(tmp_path)
    assert resolve_evidence_index(graph_path) is not None
    artifact = graph_path if target == "graph" else index_path
    before = artifact.stat()
    identity = file_cache_identity(artifact)
    data = bytearray(artifact.read_bytes())
    data[len(data) // 2] ^= 1
    artifact.write_bytes(data)
    os.utime(artifact, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert artifact.stat().st_size == before.st_size
    assert artifact.stat().st_mtime_ns == before.st_mtime_ns
    assert file_cache_identity(artifact) != identity
    with pytest.raises(EvidenceIndexError):
        resolve_evidence_index(graph_path)
