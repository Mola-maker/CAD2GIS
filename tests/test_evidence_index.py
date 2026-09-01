from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cad2gis import agent_mcp
from cad2gis.cad2gis_v3.evidence_graph import EvidenceGraph, EvidenceNode
from cad2gis.cad2gis_v3.evidence_index import (
    EVIDENCE_INDEX_FILENAME,
    EvidenceIndexError,
    get_indexed_evidence_node,
    page_evidence_nodes,
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
    graph = _graph()
    reasoning = tmp_path / "reasoning"
    path = write_evidence_index(reasoning / EVIDENCE_INDEX_FILENAME, graph)
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({
            "artifacts": {
                "evidence_index": {
                    "path": str(path),
                    "sha256": _sha256(path),
                    "graph_sha256": graph.graph_sha256,
                }
            }
        }),
        encoding="utf-8",
    )
    assert validate_index_manifest_binding(path) is True

    with path.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(EvidenceIndexError, match="does not match"):
        validate_index_manifest_binding(path)


def test_mcp_prefers_index_without_loading_large_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _graph()
    reasoning = tmp_path / "reasoning"
    reasoning.mkdir()
    graph_path = reasoning / "evidence_graph.json"
    graph_path.write_text("not parsed when an index is present", encoding="utf-8")
    write_evidence_index(reasoning / EVIDENCE_INDEX_FILENAME, graph)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        agent_mcp,
        "_load_graph",
        lambda _path: (_ for _ in ()).throw(AssertionError("JSON graph loaded")),
    )

    page = agent_mcp.list_evidence_nodes(str(graph_path), limit=2)
    assert page["query_backend"] == "sqlite-index"
    assert len(page["nodes"]) == 2
    node = agent_mcp.get_evidence_node(
        str(graph_path), page["nodes"][0]["node_id"],
    )
    assert node["node_id"] == page["nodes"][0]["node_id"]
