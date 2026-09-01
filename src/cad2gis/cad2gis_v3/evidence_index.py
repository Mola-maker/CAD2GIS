"""Indexed, source-bound access to large evidence graph snapshots.

The canonical JSON graph remains the immutable interchange artifact.  This
module writes a derived SQLite sidecar in the same staged run so MCP paging can
seek to one node without parsing and re-validating hundreds of megabytes on
every request.  Every returned row is still checked against its content
address, and official run indexes are bound by the run manifest artifact hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .evidence_graph import EvidenceGraph, EvidenceNode


EVIDENCE_INDEX_SCHEMA = "cad2gis.evidence_index.v1"
EVIDENCE_INDEX_FILENAME = "evidence_index.sqlite3"


class EvidenceIndexError(ValueError):
    """An evidence index is missing, stale, or violates its graph binding."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index_path_for_graph(graph_path: str | Path) -> Path:
    """Return the conventional sidecar path for one evidence graph JSON."""

    graph = Path(graph_path)
    return graph.with_name(EVIDENCE_INDEX_FILENAME)


def write_evidence_index(path: str | Path, graph: EvidenceGraph) -> Path:
    """Atomically write a deterministic query index for ``graph``."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(str(temporary))
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE nodes (
                position INTEGER PRIMARY KEY,
                node_id TEXT NOT NULL UNIQUE,
                logical_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                facts_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX nodes_kind_position ON nodes(kind, position)"
        )
        connection.execute(
            """
            CREATE TABLE edges (
                position INTEGER PRIMARY KEY,
                edge_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX edges_kind_position ON edges(kind, position)"
        )
        metadata = {
            "schema_version": EVIDENCE_INDEX_SCHEMA,
            "graph_schema_version": "cad2gis.evidence_graph.v1",
            "graph_sha256": graph.graph_sha256,
            "source_sha256": graph.source_sha256,
            "node_count": str(len(graph.nodes)),
            "edge_count": str(len(graph.edges)),
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        connection.executemany(
            """
            INSERT INTO nodes(
                position, node_id, logical_id, kind, facts_sha256, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    position,
                    node.node_id,
                    node.logical_id,
                    node.kind,
                    node.facts_sha256,
                    json.dumps(
                        node.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                )
                for position, node in enumerate(graph.nodes)
            ),
        )
        connection.executemany(
            """
            INSERT INTO edges(
                position, edge_id, kind, source_node_id, target_node_id,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    position,
                    edge.edge_id,
                    edge.kind,
                    edge.source_node_id,
                    edge.target_node_id,
                    json.dumps(
                        edge.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                )
                for position, edge in enumerate(graph.edges)
            ),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise EvidenceIndexError(f"Evidence index integrity check failed: {integrity}")
    finally:
        connection.close()
    os.replace(temporary, destination)
    return destination


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    try:
        values = dict(connection.execute("SELECT key, value FROM metadata"))
    except sqlite3.DatabaseError as exc:
        raise EvidenceIndexError("Evidence index metadata is unreadable") from exc
    if values.get("schema_version") != EVIDENCE_INDEX_SCHEMA:
        raise EvidenceIndexError("Unsupported evidence index schema")
    return values


def read_index_metadata(path: str | Path) -> dict[str, Any]:
    """Read bounded metadata without loading graph nodes."""

    source = Path(path)
    if not source.is_file():
        raise EvidenceIndexError(f"Evidence index does not exist: {source}")
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        values = _metadata(connection)
    finally:
        connection.close()
    return {
        **values,
        "node_count": int(values["node_count"]),
        "edge_count": int(values["edge_count"]),
    }


def page_evidence_nodes(
    path: str | Path,
    *,
    kind: str = "",
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Return one indexed page, validating every selected content address."""

    source = Path(path)
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        metadata = _metadata(connection)
        where = "WHERE kind = ?" if kind else ""
        parameters: tuple[Any, ...] = (kind,) if kind else ()
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM nodes {where}", parameters
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT payload_json FROM nodes {where}
            ORDER BY position LIMIT ? OFFSET ?
            """,
            (*parameters, limit, cursor),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise EvidenceIndexError("Evidence index node page is unreadable") from exc
    finally:
        connection.close()
    nodes = [EvidenceNode.from_dict(json.loads(row[0])) for row in rows]
    next_cursor = cursor + len(nodes)
    return {
        "graph_sha256": metadata["graph_sha256"],
        "source_sha256": metadata["source_sha256"],
        "total": total,
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < total else None,
        "query_backend": "sqlite-index",
        "nodes": [
            {
                "node_id": node.node_id,
                "logical_id": node.logical_id,
                "kind": node.kind,
                "facts_sha256": node.facts_sha256,
            }
            for node in nodes
        ],
    }


def get_indexed_evidence_node(path: str | Path, node_id: str) -> dict[str, Any] | None:
    """Return one validated node by content address, or ``None``."""

    source = Path(path)
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        _metadata(connection)
        row = connection.execute(
            "SELECT payload_json FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise EvidenceIndexError("Evidence index node lookup is unreadable") from exc
    finally:
        connection.close()
    if row is None:
        return None
    return EvidenceNode.from_dict(json.loads(row[0])).to_dict()


def indexed_nodes_by_kind(path: str | Path, kind: str) -> Iterable[EvidenceNode]:
    """Yield all nodes of one kind while validating each row independently."""

    source = Path(path)
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        _metadata(connection)
        rows = connection.execute(
            "SELECT payload_json FROM nodes WHERE kind = ? ORDER BY position", (kind,)
        )
        for row in rows:
            yield EvidenceNode.from_dict(json.loads(row[0]))
    except sqlite3.DatabaseError as exc:
        raise EvidenceIndexError("Evidence index filtered lookup is unreadable") from exc
    finally:
        connection.close()


def validate_index_manifest_binding(index_path: Path) -> bool:
    """Validate an official run sidecar against ``run_manifest.json`` when present.

    Legacy or standalone indexes have no enclosing manifest and return
    ``False``; callers still validate every selected content-addressed row.
    """

    manifest_path = index_path.parent.parent / "run_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["artifacts"]["evidence_index"]
        expected = str(record["sha256"]).casefold()
        declared_path = Path(str(record["path"])).expanduser()
        if not declared_path.is_absolute():
            declared_path = manifest_path.parent / declared_path
        declared_graph_sha256 = str(record["graph_sha256"]).casefold()
    except (KeyError, TypeError, json.JSONDecodeError, OSError):
        return False
    if declared_path.resolve() != index_path.resolve():
        raise EvidenceIndexError("Run manifest points to another evidence index")
    if len(expected) != 64:
        raise EvidenceIndexError("Run manifest evidence index digest is invalid")
    if _sha256_file(index_path) != expected:
        raise EvidenceIndexError("Evidence index does not match the run manifest")
    metadata = read_index_metadata(index_path)
    if metadata["graph_sha256"] != declared_graph_sha256:
        raise EvidenceIndexError("Evidence index graph binding does not match the run manifest")
    return True


__all__ = [
    "EVIDENCE_INDEX_FILENAME",
    "EVIDENCE_INDEX_SCHEMA",
    "EvidenceIndexError",
    "get_indexed_evidence_node",
    "index_path_for_graph",
    "indexed_nodes_by_kind",
    "page_evidence_nodes",
    "read_index_metadata",
    "validate_index_manifest_binding",
    "write_evidence_index",
]
