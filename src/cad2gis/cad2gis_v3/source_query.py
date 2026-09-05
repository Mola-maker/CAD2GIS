"""Bounded, typed SQL retrieval over a rebuildable immutable-source index.

The sidecar lives beside (never inside) the published snapshot. RTree and FTS
produce candidates only: native double bounds and literal text decide matches.
No public method accepts SQL. Cold hash validation/index construction is kept
separate from warm query timing; cached hashes are invalidated by file stat.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any
from threading import Event

from .source_export import SOURCE_EXPORT_SCHEMA_VERSION, _sha256, snapshot_digest
from .source_gpkg import MATERIALIZATION_LAYERS, _ENTITY_FIELDS, _entity_values, _route_entity
from .accounting import account_entities
from .model import CadStyle, SourceEntity
from .artifact_io import file_cache_identity

SOURCE_INDEX_SCHEMA = "cad2gis.source_index.v2"
MAX_BYTES = 65536
FACT_FIELDS = tuple(name for name, _ in _ENTITY_FIELDS if name != "entity_key") + ("lineage", "relationships")
SUMMARY_FIELDS = frozenset({
    "entity_key", "dwg_layer", "dwg_type", "cad_layout", "cad_role", "terminal_state",
    "materialization_layer", "native_centroid", "native_length", "curve_fingerprint",
    "text_preview", "text_characters", "view", "bounds_quality",
})
DEFAULT_PROJECTION = ("entity_key", "dwg_layer", "dwg_type", "cad_layout",
                      "terminal_state", "text_preview", "native_centroid")
DEFAULT_FACTS = ("dwg_layer", "dwg_type", "cad_layout", "text", "native_points",
                 "native_length", "curve_facts", "raw_properties", "lineage")
JSON_FACTS = frozenset({"native_points", "native_centroid", "block_attributes",
                        "raw_properties", "curve_facts", "terminal_reasons"})


class SourceQueryError(ValueError):
    """A source binding, cursor, projection or resource budget is invalid."""

    def __init__(self, message: str, *, code: str = "INVALID_QUERY") -> None:
        super().__init__(message)
        self.code = code


_cancellation: ContextVar[Event | None] = ContextVar("cad2gis_source_query_cancellation", default=None)


@contextmanager
def query_cancellation(event: Event):
    """Internal worker context; the event never becomes an AI tool argument."""
    token = _cancellation.set(event)
    try:
        yield
    finally:
        _cancellation.reset(token)


def _check_cancelled() -> None:
    event = _cancellation.get()
    if event is not None and event.is_set():
        raise SourceQueryError("Source query cancelled", code="CANCELLED")


def _check_budget(started: float, timeout_ms: int) -> None:
    _check_cancelled()
    if (time.perf_counter() - started) * 1000 >= timeout_ms:
        raise SourceQueryError("Bounded source query timed out", code="QUERY_BUDGET_EXCEEDED")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _native_envelope(points: Any, curve: dict, kind: str, instance_affine: Any = None) -> tuple[tuple, str]:
    """Conservative native-double envelope; unknown curves must not be excluded.

    Bulge extrema use the analytic circular segment. ARC/CIRCLE/ELLIPSE use
    full primitive envelopes, which may deliberately return extra candidates.
    Unsupported/non-planar curves remain explicit unbounded candidates.
    """
    vertices = [(float(p[0]), float(p[1])) for p in points
                if len(p) >= 2 and all(math.isfinite(float(v)) for v in p[:2])]
    primitive = str(curve.get("primitive_type") or kind).upper()
    quality = "native_vertex_envelope"
    try:
        bulges = curve.get("bulges") or []
        if any(float(b) != 0 for b in bulges):
            normal = curve.get("normal")
            wcs = curve.get("vertices_wcs") or []
            if ((normal is not None and tuple(normal) != (0, 0, 1))
                    or len({p[2] for p in wcs}) > 1 or len(wcs) != len(bulges)):
                return (None,) * 4, "unbounded_curve_candidate"
            sequence = [(float(p[0]), float(p[1])) for p in wcs]
            vertices.extend(sequence)
            segments = len(sequence) if curve.get("closed") else len(sequence) - 1
            for i in range(segments):
                start, end, bulge = sequence[i], sequence[(i+1) % len(sequence)], float(bulges[i])
                if bulge == 0:
                    continue
                chord = math.dist(start, end)
                if chord == 0:
                    return (None,) * 4, "unbounded_curve_candidate"
                offset = chord * (1 - bulge * bulge) / (4 * bulge)
                center = ((start[0]+end[0])/2 - (end[1]-start[1])/chord*offset,
                          (start[1]+end[1])/2 + (end[0]-start[0])/chord*offset)
                radius = chord * (1 + bulge * bulge) / (4 * abs(bulge))
                angle = math.atan2(start[1]-center[1], start[0]-center[0])
                sweep = 4 * math.atan(bulge)
                for cardinal in (0, math.pi/2, math.pi, 3*math.pi/2):
                    delta = ((cardinal-angle) if sweep > 0 else (angle-cardinal)) % math.tau
                    if delta <= abs(sweep) + 1e-14:
                        vertices.append((center[0]+radius*math.cos(cardinal), center[1]+radius*math.sin(cardinal)))
            quality = "analytic_bulge_envelope_candidate"
        elif primitive in {"ARC", "CIRCLE", "ELLIPSE"}:
            parameters = curve.get("primitive_parameters") or {}
            center = parameters.get("center_wcs", parameters.get("center"))
            if center is None:
                return (None,) * 4, "unbounded_curve_candidate"
            if primitive == "ELLIPSE":
                radius = math.sqrt(sum(float(v)**2 for v in parameters["major_axis"])) * max(1, abs(float(parameters["radius_ratio"])))
            else:
                radius = abs(float(parameters["radius"]))
            primitive_corners = [(float(center[0])+dx*radius, float(center[1])+dy*radius)
                                 for dx in (-1, 1) for dy in (-1, 1)]
            if instance_affine is not None:
                # plan_domain preserves primitive parameters in definition
                # coordinates while transforming vertices; use its explicit
                # composed matrix for this envelope only, never mutate facts.
                a, b, c, d, tx, ty = (float(instance_affine[key]) for key in ("m11", "m12", "m21", "m22", "tx", "ty"))
                if not all(math.isfinite(v) for v in (a, b, c, d, tx, ty)):
                    return (None,) * 4, "unbounded_curve_candidate"
                primitive_corners = [(a*x+b*y+tx, c*x+d*y+ty) for x, y in primitive_corners]
            vertices.extend(primitive_corners)
            quality = "conservative_primitive_envelope_candidate"
        elif primitive in {"SPLINE", "HELIX", "RAY", "XLINE", "MLINE"} or (
                primitive in {"LWPOLYLINE", "POLYLINE", "POLYLINE_2D", "POLYLINE_3D"} and not curve):
            return (None,) * 4, "unbounded_curve_candidate"
        if not vertices:
            return (None,) * 4, "no_geometry"
        bounds = (min(p[0] for p in vertices), max(p[0] for p in vertices),
                  min(p[1] for p in vertices), max(p[1] for p in vertices))
        if not all(math.isfinite(v) for v in bounds):
            return (None,) * 4, "unbounded_curve_candidate"
        return tuple(math.nextafter(v, -math.inf if i % 2 == 0 else math.inf)
                     for i, v in enumerate(bounds)), quality
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return (None,) * 4, "unbounded_curve_candidate"


@lru_cache(maxsize=64)
def _cached_sha(path: str, signature: tuple[int, ...]) -> str:
    del signature
    return _sha256(Path(path))


def _file_hash(path: Path) -> str:
    return _cached_sha(str(path), file_cache_identity(path))


def _read_json(path: Path, max_bytes: int = 1048576) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise SourceQueryError(f"Metadata exceeds size limit: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SourceQueryError("Metadata must be a JSON object")
    return value


def validate_source_snapshot(run_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(run_dir).expanduser().resolve()
    try:
        manifest = _read_json(root / "source_manifest.json")
        if (manifest.get("schema_version") != SOURCE_EXPORT_SCHEMA_VERSION
                or manifest.get("status") != "SOURCE_EXPORTED"
                or manifest.get("snapshot_sha256") != snapshot_digest(manifest)):
            raise SourceQueryError("Invalid or stale source snapshot manifest", code="ARTIFACT_BINDING_INVALID")
        if not {"source_gpkg", "reader_records", "source_inventory", "cad_scene_graph", "plan_entities"} <= set(manifest["artifacts"]):
            raise SourceQueryError("Source snapshot artifact binding is incomplete", code="ARTIFACT_BINDING_INVALID")
        for artifact in manifest["artifacts"].values():
            path = Path(artifact["path"]).resolve()
            if not path.is_relative_to(root) or _file_hash(path) != artifact["sha256"]:
                raise SourceQueryError("Source snapshot artifact hash/path mismatch", code="ARTIFACT_BINDING_INVALID")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SourceQueryError(f"Source snapshot is missing or invalid: {exc}", code="ARTIFACT_BINDING_INVALID") from exc
    return root, manifest


def source_index_path(run_dir: str | Path) -> Path:
    root, manifest = validate_source_snapshot(run_dir)
    # Include the run location so separately owned snapshot directories do not
    # contend for one mutable cache publication directory.
    location = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return root.parent / ".source-query-cache" / f"{manifest['snapshot_sha256']}-{location}" / "source_index.sqlite3"


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.enable_load_extension(False)
    return connection


def _index_metadata(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        descriptor = _read_json(path.with_suffix(".json"))
        if descriptor.get("index_sha256") != _file_hash(path):
            raise SourceQueryError("Source index byte hash mismatch; rebuild explicitly", code="ARTIFACT_BINDING_INVALID")
        db = _readonly(path)
        try:
            metadata = dict(db.execute("SELECT key,value FROM metadata"))
        finally:
            db.close()
        expected = {"schema_version": SOURCE_INDEX_SCHEMA,
                    "source_sha256": manifest["source"]["sha256"],
                    "snapshot_sha256": manifest["snapshot_sha256"],
                    "source_gpkg_sha256": manifest["artifacts"]["source_gpkg"]["sha256"]}
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise SourceQueryError("Source/index binding mismatch; rebuild explicitly", code="ARTIFACT_BINDING_INVALID")
        if descriptor.get("snapshot_sha256") != manifest["snapshot_sha256"]:
            raise SourceQueryError("Source index descriptor belongs to another snapshot", code="ARTIFACT_BINDING_INVALID")
        return {**expected, "index_sha256": descriptor["index_sha256"],
                "entity_count": int(metadata["entity_count"]),
                "plan_entity_count": int(metadata["plan_entity_count"])}
    except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SourceQueryError(f"Source index is missing or invalid: {exc}", code="ARTIFACT_BINDING_INVALID") from exc


def build_source_index(run_dir: str | Path, *, rebuild: bool = False) -> dict[str, Any]:
    """Build once using streaming source rows; explicit rebuild repairs a stale cache."""
    _check_cancelled()
    root, manifest = validate_source_snapshot(run_dir)
    path = source_index_path(root)
    if path.exists() and not rebuild:
        return {"path": str(path), **_index_metadata(path, manifest)}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path = path.with_suffix(".json")
    fd, temporary = tempfile.mkstemp(prefix=".source-index-", suffix=".sqlite3", dir=path.parent)
    os.close(fd)
    staged = Path(temporary)
    db = sqlite3.connect(staged)
    source = _readonly(Path(manifest["artifacts"]["source_gpkg"]["path"]))
    try:
        db.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE entities(
                id INTEGER PRIMARY KEY, view TEXT NOT NULL, entity_key TEXT NOT NULL,
                dwg_layer TEXT, dwg_type TEXT, cad_layout TEXT, cad_role TEXT,
                terminal_state TEXT, materialization_layer TEXT,
                native_centroid TEXT, native_length REAL, curve_fingerprint TEXT,
                text TEXT, min_x REAL, max_x REAL, min_y REAL, max_y REAL, bounds_quality TEXT,
                UNIQUE(view,entity_key));
            CREATE TABLE facts(entity_id INTEGER NOT NULL,field TEXT NOT NULL,value_json TEXT NOT NULL,
                PRIMARY KEY(entity_id,field)) WITHOUT ROWID;
            CREATE VIRTUAL TABLE bounds USING rtree(id,min_x,max_x,min_y,max_y);
            CREATE VIRTUAL TABLE text_search USING fts5(text,tokenize='trigram');
            CREATE TABLE text_bigrams(gram TEXT NOT NULL,entity_id INTEGER NOT NULL,
                PRIMARY KEY(gram,entity_id)) WITHOUT ROWID;
            CREATE TABLE scene_nodes(node_id TEXT PRIMARY KEY,logical_id TEXT,kind TEXT,facts_json TEXT);
            CREATE INDEX scene_logical ON scene_nodes(logical_id);
            CREATE TABLE scene_edges(edge_id TEXT PRIMARY KEY,source_node_id TEXT,target_node_id TEXT,kind TEXT,facts_json TEXT);
            CREATE INDEX edges_source ON scene_edges(source_node_id);
            CREATE INDEX edges_target ON scene_edges(target_node_id);
        """)
        fields = [name for name, _ in _ENTITY_FIELDS]
        count = 0
        for layer in MATERIALIZATION_LAYERS:
            sql = 'SELECT ' + ','.join('"' + name + '"' for name in fields) + f' FROM "{layer}" ORDER BY entity_key'
            for row in source.execute(sql):
                _check_cancelled()
                if row["source_sha256"] != manifest["source"]["sha256"]:
                    raise SourceQueryError("Source row SHA-256 differs from manifest")
                points = json.loads(row["native_points"] or "[]")
                bounds, bounds_quality = _native_envelope(points, json.loads(row["curve_facts"] or "{}"), row["dwg_type"])
                count += 1
                values = (count, "source", row["entity_key"], *(row[name] for name in (
                    "dwg_layer", "dwg_type", "cad_layout", "cad_role", "terminal_state",
                    "materialization_layer", "native_centroid", "native_length", "curve_fingerprint", "text")), *bounds, bounds_quality)
                db.execute("INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
                facts = []
                for field in fields:
                    if field == "entity_key":
                        continue
                    value = row[field]
                    if field in JSON_FACTS:
                        value = json.loads(value) if value is not None else None
                    facts.append((count, field, _json(value)))
                raw = json.loads(row["raw_properties"] or "{}")
                lineage = {key: value for key, value in raw.items()
                           if "lineage" in key or "transform" in key or key.startswith("plan_domain")}
                facts.append((count, "lineage", _json(lineage)))
                db.executemany("INSERT INTO facts VALUES (?,?,?)", facts)
                if bounds[0] is not None:
                    db.execute("INSERT INTO bounds VALUES (?,?,?,?,?)", (count, *bounds))
                text = row["text"] or ""
                db.execute("INSERT INTO text_search(rowid,text) VALUES (?,?)", (count, text))
                db.executemany("INSERT INTO text_bigrams VALUES (?,?)",
                               ((gram, count) for gram in set(text[i:i+2] for i in range(len(text)-1))))
        if count != manifest["entity_count"]:
            raise SourceQueryError("Source inventory/index conservation mismatch")
        source_count = count
        # Instances are a distinct namespace and retain their exact derived
        # curve contract and INSERT affine/lineage. Never overwrite raw rows.
        with Path(manifest["artifacts"]["plan_entities"]["path"]).open(encoding="utf-8") as stream:
            for line in stream:
                _check_cancelled()
                encoded = json.loads(line)
                encoded["style"] = CadStyle(**encoded["style"])
                entity = SourceEntity(**encoded)
                if entity.source_sha256 != manifest["source"]["sha256"]:
                    raise SourceQueryError("Plan entity belongs to another source")
                row = _entity_values(_route_entity(entity, account_entities((entity,))[0]))
                points = entity.points
                materialization = entity.raw_properties.get("plan_domain") or {}
                bounds, bounds_quality = _native_envelope(points, entity.curve_facts, entity.dwg_type,
                                                          materialization.get("affine"))
                count += 1
                values = (count, "plan", row["entity_key"], *(row[name] for name in (
                    "dwg_layer", "dwg_type", "cad_layout", "cad_role", "terminal_state",
                    "materialization_layer", "native_centroid", "native_length", "curve_fingerprint", "text")), *bounds, bounds_quality)
                db.execute("INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
                facts = []
                for field in fields:
                    if field != "entity_key":
                        value = row[field]
                        facts.append((count, field, _json(json.loads(value) if field in JSON_FACTS and value is not None else value)))
                facts.append((count, "lineage", _json(entity.raw_properties.get("plan_domain") or {})))
                db.executemany("INSERT INTO facts VALUES (?,?,?)", facts)
                if bounds[0] is not None and all(math.isfinite(v) for v in bounds):
                    db.execute("INSERT INTO bounds VALUES (?,?,?,?,?)", (count, *bounds))
                text = row["text"] or ""
                db.execute("INSERT INTO text_search(rowid,text) VALUES (?,?)", (count, text))
                db.executemany("INSERT INTO text_bigrams VALUES (?,?)",
                               ((gram, count) for gram in set(text[i:i+2] for i in range(len(text)-1))))
        if count - source_count != manifest["scene"]["plan_entity_count"]:
            raise SourceQueryError("Plan instance/index conservation mismatch")
        graph = json.loads(Path(manifest["artifacts"]["cad_scene_graph"]["path"]).read_text(encoding="utf-8"))
        db.executemany("INSERT INTO scene_nodes VALUES (?,?,?,?)", (
            (node["node_id"], node["logical_id"], node["kind"], _json(node["facts"]))
            for node in graph["nodes"]))
        db.executemany("INSERT INTO scene_edges VALUES (?,?,?,?,?)", (
            (edge["edge_id"], edge["source_node_id"], edge["target_node_id"], edge["kind"], _json(edge["facts"]))
            for edge in graph["edges"]))
        logical_by_node = {node["node_id"]: node["logical_id"] for node in graph["nodes"]}
        adjacency: dict[str, list[dict[str, Any]]] = {}
        for edge in graph["edges"]:
            _check_cancelled()
            relation = {"edge_id": edge["edge_id"], "kind": edge["kind"],
                        "source": logical_by_node[edge["source_node_id"]],
                        "target": logical_by_node[edge["target_node_id"]], "facts": edge["facts"]}
            for endpoint in (edge["source_node_id"], edge["target_node_id"]):
                adjacency.setdefault(logical_by_node[endpoint], []).append(relation)
        for entity_id, view, entity_key in db.execute("SELECT id,view,entity_key FROM entities"):
            _check_cancelled()
            db.execute("INSERT INTO facts VALUES (?,?,?)", (entity_id, "relationships",
                       _json(adjacency.get(f"{view}:{entity_key}", []))))
        for name in ("dwg_layer", "dwg_type", "cad_layout", "terminal_state"):
            db.execute(f"CREATE INDEX entities_{name} ON entities(view,{name},entity_key)")
        metadata = {"schema_version": SOURCE_INDEX_SCHEMA, "source_sha256": manifest["source"]["sha256"],
                    "snapshot_sha256": manifest["snapshot_sha256"],
                    "source_gpkg_sha256": manifest["artifacts"]["source_gpkg"]["sha256"],
                    "entity_count": str(source_count), "plan_entity_count": str(count-source_count)}
        db.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SourceQueryError("Source index integrity check failed")
    finally:
        source.close()
        db.close()
    try:
        # Revalidate source signatures before publishing a derived index.
        validate_source_snapshot(root)
        byte_hash = _sha256(staged)
        descriptor_tmp = staged.with_suffix(".json")
        descriptor_tmp.write_text(_json({"index_sha256": byte_hash,
                                         "snapshot_sha256": manifest["snapshot_sha256"]}), encoding="utf-8")
        os.replace(staged, path)
        os.replace(descriptor_tmp, descriptor_path)
    finally:
        staged.unlink(missing_ok=True)
    return {"path": str(path), **_index_metadata(path, manifest)}


def _open_query(run_dir: str | Path, timeout_ms: int) -> tuple[sqlite3.Connection, dict[str, Any], float]:
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1 <= timeout_ms <= 10000:
        raise SourceQueryError("timeout_ms must be 1..10000")
    metadata = build_source_index(run_dir)
    path = Path(metadata.pop("path"))
    db = _readonly(path)
    started = time.perf_counter()
    deadline = started + timeout_ms / 1000
    event = _cancellation.get()
    db.set_progress_handler(lambda: int(time.perf_counter() >= deadline or (event is not None and event.is_set())), 1000)
    return db, metadata, started


def _cursor(binding: dict[str, Any], position: Any) -> str:
    return base64.urlsafe_b64encode(_json({**binding, "position": position}).encode("utf-8")).decode("ascii")


def _position(cursor: str | None, binding: dict[str, Any], initial: Any) -> Any:
    if cursor is None:
        return initial
    try:
        if not isinstance(cursor, str) or len(cursor) > 8192:
            raise ValueError("cursor too large")
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if {key: value.get(key) for key in binding} != binding:
            raise ValueError("cursor source, index or filters changed")
        return value["position"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise SourceQueryError(f"Invalid or stale cursor: {exc}", code="STALE_CURSOR") from exc


def _budget(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 2048 <= value <= MAX_BYTES:
        raise SourceQueryError("max_bytes must be 2048..65536")
    return value


def _response(items: list[dict[str, Any]], metadata: dict[str, Any], next_cursor: str | None,
              started: float) -> dict[str, Any]:
    response = {"items": items, "next_cursor": next_cursor, "metadata": metadata,
                "returned_count": len(items), "elapsed_ms": round((time.perf_counter()-started)*1000, 3),
                "response_bytes": 0}
    for _ in range(4):
        size = len(_json(response).encode("utf-8"))
        if response["response_bytes"] == size:
            break
        response["response_bytes"] = size
    return response


def query_source_entities(
    *, run_dir: str | Path, view: str = "source", layer: str | None = None, dwg_type: str | None = None,
    layout: str | None = None, terminal_state: str | None = None,
    text_query: str | None = None, bbox: Sequence[float] | None = None,
    projection: Sequence[str] | None = None, limit: int = 50, cursor: str | None = None,
    timeout_ms: int = 2000, max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Seek a compact page; bbox is [min_x,min_y,max_x,max_y] in native CAD space."""
    max_bytes = _budget(max_bytes)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise SourceQueryError("limit must be 1..200")
    if view not in {"source", "plan"}:
        raise SourceQueryError("view must be source or plan")
    fields = tuple(dict.fromkeys(("entity_key", "view", *(projection or DEFAULT_PROJECTION))))
    if not set(fields) <= SUMMARY_FIELDS:
        raise SourceQueryError("Unsupported projection; use context batch for full facts")
    filters = {"dwg_layer": layer, "dwg_type": dwg_type, "cad_layout": layout,
               "terminal_state": terminal_state, "text_query": text_query}
    if any(value is not None and (not isinstance(value, str) or len(value) > 1024) for value in filters.values()):
        raise SourceQueryError("Filters must be strings of at most 1024 characters")
    box = None
    if bbox is not None:
        try:
            box = tuple(float(v) for v in bbox)
            if len(box) != 4 or not all(math.isfinite(v) for v in box) or box[0] > box[2] or box[1] > box[3]:
                raise ValueError("invalid native bbox")
        except (TypeError, ValueError) as exc:
            raise SourceQueryError("bbox must contain ordered finite native bounds") from exc
    db, metadata, started = _open_query(run_dir, timeout_ms)
    binding = {"kind": "source_entities", "snapshot": metadata["snapshot_sha256"],
               "index": metadata["index_sha256"], "filter": _digest([view, filters, box, fields])}
    try:
        last = _position(cursor, binding, "")
        if not isinstance(last, str):
            raise SourceQueryError("Invalid entity cursor position", code="STALE_CURSOR")
        clauses, parameters = ["e.entity_key > ?", "e.view = ?"], [last, view]
        for name, value in filters.items():
            if name != "text_query" and value is not None:
                clauses.append(f"e.{name} = ?")
                parameters.append(value)
        if text_query:
            if len(text_query) >= 3:
                clauses.append("e.id IN (SELECT rowid FROM text_search WHERE text_search MATCH ?)")
                parameters.append('"' + text_query.replace('"', '""') + '"')
            elif len(text_query) == 2:
                clauses.append("e.id IN (SELECT entity_id FROM text_bigrams WHERE gram = ?)")
                parameters.append(text_query)
            clauses.append("instr(e.text, ?) > 0")
            parameters.append(text_query)
        if box is not None:
            spatial_clauses = []
            for table in ("bounds", "e"):
                expression = f"{table}.max_x >= ? AND {table}.min_x <= ? AND {table}.max_y >= ? AND {table}.min_y <= ?"
                spatial_clauses.append(f"e.id IN (SELECT id FROM bounds WHERE {expression})" if table == "bounds" else expression)
                parameters.extend((box[0], box[2], box[1], box[3]))
            clauses.append("(e.bounds_quality='unbounded_curve_candidate' OR (" + " AND ".join(spatial_clauses) + "))")
            fields = tuple(dict.fromkeys((*fields, "bounds_quality")))
        select = ["substr(e.text,1,256) AS text_preview" if field == "text_preview" else
                  "length(e.text) AS text_characters" if field == "text_characters" else f"e.{field}" for field in fields]
        sql = "SELECT " + ",".join(select) + " FROM entities e WHERE " + " AND ".join(clauses) + " ORDER BY e.entity_key LIMIT ?"
        rows = db.execute(sql, (*parameters, limit + 1))
        items = []
        next_cursor = None
        for row in rows:
            _check_budget(started, timeout_ms)
            if len(items) == limit:
                next_cursor = _cursor(binding, items[-1]["entity_key"])
                break
            item = dict(row)
            if "native_centroid" in item and item["native_centroid"] is not None:
                item["native_centroid"] = json.loads(item["native_centroid"])
            candidate_cursor = _cursor(binding, item["entity_key"])
            if _response([*items, item], metadata, candidate_cursor, started)["response_bytes"] > max_bytes - 32:
                if not items:
                    raise SourceQueryError("Entity summary exceeds budget; request a smaller projection")
                next_cursor = _cursor(binding, items[-1]["entity_key"])
                break
            items.append(item)
        _check_budget(started, timeout_ms)
        return _response(items, metadata, next_cursor, started)
    except sqlite3.OperationalError as exc:
        _check_cancelled()
        raise SourceQueryError(f"Bounded source query failed or timed out: {exc}",
                               code="QUERY_BUDGET_EXCEEDED" if "interrupted" in str(exc) else "INVALID_QUERY") from exc
    finally:
        db.close()


def get_entity_context_batch(
    *, run_dir: str | Path, entity_keys: Sequence[str], view: str = "source", fields: Sequence[str] | None = None,
    cursor: str | None = None, max_bytes: int = MAX_BYTES, timeout_ms: int = 2000,
) -> dict[str, Any]:
    """Read observed IDs in bounded field groups, with lossless large-field chunks.

    A ``field_chunks`` value is a slice of canonical JSON text: concatenate in
    offset order then JSON-decode. ``complete`` means the entity's requested
    fields end in this group; chunks explicitly remain partial until all arrive.
    """
    max_bytes = _budget(max_bytes)
    if view not in {"source", "plan"}:
        raise SourceQueryError("view must be source or plan")
    keys = tuple(entity_keys)
    selected = tuple(dict.fromkeys(fields or DEFAULT_FACTS))
    if (not 1 <= len(keys) <= 200 or len(set(keys)) != len(keys)
            or any(not isinstance(key, str) or not key or len(key) > 512 for key in keys)):
        raise SourceQueryError("entity_keys must contain 1..200 unique stable keys")
    if not selected or not set(selected) <= set(FACT_FIELDS):
        raise SourceQueryError("Unsupported context field")
    db, metadata, started = _open_query(run_dir, timeout_ms)
    binding = {"kind": "entity_context", "snapshot": metadata["snapshot_sha256"],
               "index": metadata["index_sha256"], "filter": _digest([view, keys, selected])}
    try:
        position = _position(cursor, binding, [0, 0, 0])
        if (not isinstance(position, list) or len(position) != 3
                or any(type(v) is not int or v < 0 for v in position)
                or position[0] >= len(keys) or position[1] >= len(selected)):
            raise SourceQueryError("Invalid context cursor position", code="STALE_CURSOR")
        ids = dict(db.execute("SELECT entity_key,id FROM entities WHERE view=? AND entity_key IN (" + ",".join("?" for _ in keys) + ")", (view,*keys)))
        if len(ids) != len(keys):
            raise SourceQueryError("Unknown source entity key")
        items: list[dict[str, Any]] = []
        while position[0] < len(keys):
            _check_budget(started, timeout_ms)
            entity_offset, field_offset, char_offset = position
            key, field = keys[entity_offset], selected[field_offset]
            length_row = db.execute("SELECT length(value_json) FROM facts WHERE entity_id=? AND field=?", (ids[key], field)).fetchone()
            if length_row is None:
                raise SourceQueryError("Source index fact is missing")
            total = int(length_row[0])
            if char_offset >= total:
                raise SourceQueryError("Invalid fact chunk offset", code="STALE_CURSOR")
            # SQLite substr bounds Python decoding work even for multi-megabyte
            # vertex arrays. A smaller max_bytes yields smaller chunk groups.
            chunk_size = min(2048, max(32, (max_bytes - 1600) // 12))
            value = db.execute("SELECT substr(value_json,?,?) FROM facts WHERE entity_id=? AND field=?",
                               (char_offset + 1, chunk_size, ids[key], field)).fetchone()[0]
            field_done = char_offset + len(value) == total
            next_position = ([entity_offset, field_offset + 1, 0] if field_done else
                             [entity_offset, field_offset, char_offset + len(value)])
            entity_done = next_position[1] == len(selected)
            if entity_done:
                next_position = [entity_offset + 1, 0, 0]
            item: dict[str, Any] = {"entity_key": key, "view": view, "facts": {}, "complete": entity_done}
            if char_offset == 0 and field_done:
                item["facts"][field] = json.loads(value)
            else:
                item["field_chunks"] = [{"field": field, "offset": char_offset,
                    "total_characters": total, "encoding": "canonical_json_text",
                    "value": value, "field_complete": field_done}]
            continuation = _cursor(binding, next_position) if next_position[0] < len(keys) else None
            candidate_items = [*items, item]
            # Merge adjacent small fields into an ordinary per-entity group.
            if (items and items[-1]["entity_key"] == key and "field_chunks" not in item
                    and "field_chunks" not in items[-1]):
                merged = {**items[-1], "facts": {**items[-1]["facts"], **item["facts"]}, "complete": entity_done}
                candidate_items = [*items[:-1], merged]
            if _response(candidate_items, metadata, continuation, started)["response_bytes"] > max_bytes - 32:
                if not items:
                    raise SourceQueryError("Context budget is too small for the requested key/field group")
                break
            items = candidate_items
            position = next_position
        next_cursor = _cursor(binding, position) if position[0] < len(keys) else None
        _check_budget(started, timeout_ms)
        return _response(items, metadata, next_cursor, started)
    except sqlite3.OperationalError as exc:
        _check_cancelled()
        raise SourceQueryError(f"Bounded context query failed or timed out: {exc}",
                               code="QUERY_BUDGET_EXCEEDED" if "interrupted" in str(exc) else "INVALID_QUERY") from exc
    finally:
        db.close()
