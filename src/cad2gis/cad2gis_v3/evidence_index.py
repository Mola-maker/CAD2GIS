"""Indexed, source-bound access to large evidence graph snapshots.

The canonical JSON graph remains the immutable interchange artifact.  This
module writes a derived SQLite sidecar in the same staged run so MCP paging can
seek to one node without parsing and re-validating hundreds of megabytes on
every request.  Every returned row is still checked against its content
address, and official run indexes are bound by the run manifest artifact hash.
"""

from __future__ import annotations

import hashlib
import gzip
import json
import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .evidence_graph import EvidenceGraph, EvidenceNode


EVIDENCE_INDEX_SCHEMA = "cad2gis.evidence_index.v1"
EVIDENCE_INDEX_FILENAME = "evidence_index.sqlite3"


class EvidenceIndexError(ValueError):
    """An evidence index is missing, stale, or violates its graph binding."""


@dataclass(frozen=True)
class EvidenceIndexBinding:
    """Validated query identity; standalone data carries no run authority."""

    index_path: Path
    graph_path: Path
    graph_sha256: str
    source_sha256: str
    binding_status: str
    manifest_path: Path | None


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


def _file_identity(path: Path) -> tuple[int, ...]:
    from .artifact_io import file_cache_identity
    return file_cache_identity(path)


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise EvidenceIndexError(f"Invalid {name} digest")
    return value


def _read_manifest(manifest_path: Path) -> tuple[dict[str, Any], str]:
    try:
        # A manifest is bounded control data, never a replacement for the graph.
        if manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise EvidenceIndexError("Run manifest exceeds the metadata byte limit")
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceIndexError("Run manifest is missing or unreadable") from exc
    if not isinstance(manifest, dict):
        raise EvidenceIndexError("Run manifest must be an object")
    return manifest, hashlib.sha256(raw).hexdigest()


def _artifact_record(manifest: dict[str, Any], manifest_path: Path, name: str) -> dict[str, Any]:
    try:
        record = manifest["artifacts"][name]
        if not isinstance(record, dict) or not isinstance(record["path"], str) or not record["path"]:
            raise TypeError("invalid artifact record")
        declared_path = Path(record["path"]).expanduser()
        if not declared_path.is_absolute():
            declared_path = manifest_path.parent / declared_path
        return {
            "path": declared_path.resolve(),
            "sha256": _digest(record["sha256"], f"manifest {name}"),
            "graph_sha256": _digest(record["graph_sha256"], f"manifest {name} graph"),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceIndexError(f"Run manifest has missing or invalid {name} binding") from exc


@lru_cache(maxsize=32)
def _resolve_evidence_index_once(
    graph_path: Path,
    index_path: Path,
    graph_identity: tuple[int, ...],
    index_identity: tuple[int, ...],
    manifest_path: Path | None,
    manifest_digest: str,
    expected_source_sha256: str | None,
    expected_graph_sha256: str | None,
) -> EvidenceIndexBinding:
    """Cold verification only; every identity is part of the cache key."""

    try:
        metadata = read_index_metadata(index_path)
        source_hash = _digest(metadata["source_sha256"], "index source")
        graph_hash = _digest(metadata["graph_sha256"], "index graph")
        if expected_source_sha256 is not None and source_hash != expected_source_sha256:
            raise EvidenceIndexError("Evidence index does not match the requested source")
        if expected_graph_sha256 is not None and graph_hash != expected_graph_sha256:
            raise EvidenceIndexError("Evidence index does not match the requested graph")
        if manifest_path is not None:
            manifest, observed_manifest_digest = _read_manifest(manifest_path)
            if observed_manifest_digest != manifest_digest:
                raise EvidenceIndexError("Run manifest changed during index validation")
            index_record = _artifact_record(manifest, manifest_path, "evidence_index")
            graph_record = _artifact_record(manifest, manifest_path, "evidence_graph")
            if index_record["path"] != index_path:
                raise EvidenceIndexError("Run manifest points to another evidence index")
            if graph_record["path"] != graph_path:
                raise EvidenceIndexError("Run manifest points to another requested evidence graph")
            if index_record["sha256"] != _sha256_file(index_path):
                raise EvidenceIndexError("Evidence index does not match the run manifest")
            if graph_record["sha256"] != _sha256_file(graph_path):
                raise EvidenceIndexError("Evidence graph does not match the run manifest")
            if graph_hash != index_record["graph_sha256"] or graph_hash != graph_record["graph_sha256"]:
                raise EvidenceIndexError("Evidence index graph binding does not match the run manifest")
            if source_hash != _digest(manifest["source"]["sha256"], "manifest source"):
                raise EvidenceIndexError("Evidence index source binding does not match the run manifest")

        # Binding only to the index's self-declared graph hash is insufficient:
        # validate the requested artifact's semantic identity on the cold path.
        opener = gzip.open if graph_path.suffix.casefold() == ".gz" else open
        with opener(graph_path, "rt", encoding="utf-8") as handle:
            graph = EvidenceGraph.from_dict(json.load(handle))
        if graph.graph_sha256 != graph_hash or graph.source_sha256 != source_hash:
            raise EvidenceIndexError("Evidence index does not match the requested graph/source")
        if graph_identity != _file_identity(graph_path) or index_identity != _file_identity(index_path):
            raise EvidenceIndexError("Evidence artifacts changed during index validation")
        if manifest_path is not None and _read_manifest(manifest_path)[1] != manifest_digest:
            raise EvidenceIndexError("Run manifest changed during index validation")
    except EvidenceIndexError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.DatabaseError) as exc:
        raise EvidenceIndexError("Evidence graph/index/manifest binding is unreadable or invalid") from exc
    return EvidenceIndexBinding(
        index_path=index_path,
        graph_path=graph_path,
        graph_sha256=graph_hash,
        source_sha256=source_hash,
        binding_status="manifest_bound" if manifest_path else "standalone_unbound",
        manifest_path=manifest_path,
    )


def resolve_evidence_index(
    graph_path: str | Path,
    *,
    expected_source_sha256: str | None = None,
    expected_graph_sha256: str | None = None,
    allow_standalone: bool = False,
) -> EvidenceIndexBinding | None:
    """Resolve a graph-bound index, rejecting any incomplete official binding.

    Standalone callers must opt in explicitly and receive ``standalone_unbound``.
    This option never permits a present, malformed manifest to be ignored. Hot
    calls read only bounded manifest control data and stable file identities.
    """

    graph = Path(graph_path).expanduser().resolve()
    index = index_path_for_graph(graph)
    manifest_path = index.parent.parent / "run_manifest.json"
    manifest_digest = ""
    if manifest_path.exists():
        manifest, manifest_digest = _read_manifest(manifest_path)
        _artifact_record(manifest, manifest_path, "evidence_index")
        _artifact_record(manifest, manifest_path, "evidence_graph")
        if not index.is_file():
            raise EvidenceIndexError("Run manifest evidence index is missing")
    else:
        if not index.is_file():
            return None
        if not allow_standalone:
            raise EvidenceIndexError("Run manifest is missing; standalone access requires explicit opt-in")
        manifest_path = None
    if not graph.is_file():
        raise EvidenceIndexError("Requested evidence graph is missing")
    for value, name in ((expected_source_sha256, "requested source"), (expected_graph_sha256, "requested graph")):
        if value is not None:
            _digest(value, name)
    try:
        return _resolve_evidence_index_once(
            graph, index, _file_identity(graph), _file_identity(index),
            manifest_path, manifest_digest, expected_source_sha256, expected_graph_sha256,
        )
    except OSError as exc:
        raise EvidenceIndexError("Evidence artifacts are unreadable") from exc


def validate_index_manifest_binding(index_path: Path) -> bool:
    """Compatibility validator; absent or incomplete official bindings fail closed."""

    index = Path(index_path).expanduser().resolve()
    manifest_path = index.parent.parent / "run_manifest.json"
    manifest, _ = _read_manifest(manifest_path)
    record = _artifact_record(manifest, manifest_path, "evidence_graph")
    binding = resolve_evidence_index(record["path"])
    if binding is None or binding.index_path != index:
        raise EvidenceIndexError("Run manifest points to another evidence index")
    return True


__all__ = [
    "EVIDENCE_INDEX_FILENAME",
    "EVIDENCE_INDEX_SCHEMA",
    "EvidenceIndexError",
    "EvidenceIndexBinding",
    "get_indexed_evidence_node",
    "index_path_for_graph",
    "indexed_nodes_by_kind",
    "page_evidence_nodes",
    "read_index_metadata",
    "resolve_evidence_index",
    "validate_index_manifest_binding",
    "write_evidence_index",
]
