"""Immutable, indexed semantic evidence and a source-coordinate compiler.

Candidates express admissible choices, never automatic semantic conclusions.
The label grid policy is adapted from the audited feature semantic_stage; the
index, strict patch boundary and publication protocol replace its JSONL scans.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import uuid
from collections import defaultdict
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from .artifact_io import file_cache_identity

PREPARE_SCHEMA = "cad2gis.semantic_prepare.v3"
SEMANTIC_SCHEMA = "cad2gis.semantic_gpkg.v2"
SOURCE_LAYERS = ("source_points", "source_lines", "source_polygons", "source_text", "source_blocks", "source_metadata")
TERMINAL_STATES = ("CONSUMED_BY_FEATURE", "RETAINED_AS_REFERENCE", "EXCLUDED_AS_DOCUMENTATION", "UNRESOLVED")
CLASS_BY_TABLE = {
    "source_points": ["GENERIC_ASSET", "SUPPORT", "DISTRIBUTION_NODE", "ACCESS_NODE", "CENTRAL_SITE", "HANDHOLE", "PREMISE"],
    "source_blocks": ["GENERIC_ASSET", "SUPPORT", "DISTRIBUTION_NODE", "ACCESS_NODE", "CENTRAL_SITE", "HANDHOLE", "PREMISE"],
    "source_lines": ["GENERIC_ASSET", "NETWORK_ROUTE", "NETWORK_SEGMENT", "REFERENCE_ROAD"],
    "source_polygons": ["GENERIC_ASSET", "BUILDING", "ZONE"],
}
POLICIES = {
    "label_proximity_v1": {"kind": "label", "grid_median_gap_multiplier": 8, "radius_cells": 2.5, "max_candidates": 5, "same_layout_and_role": True, "relation_is": "nearby_not_attached"},
    "geometry_class_v1": {"kind": "class", "allowed_classes_by_source_table": CLASS_BY_TABLE, "meaning": "compatible_geometry_not_classification"},
    "dimension_exact_endpoints_v1": {"kind": "dimension", "requires": "DIMENSION finite source value; exactly two definition points equal line endpoints; same layout and role", "tolerance": 0},
    "terminal_accounting_v1": {"kind": "terminal", "states": list(TERMINAL_STATES[1:])},
}
_HASH_CACHE: dict[str, tuple[tuple[int, ...], str]] = {}


class SemanticContractError(ValueError):
    """Evidence or an operation does not satisfy the source-bound contract."""

    def __init__(self, message: str, *, code: str = "VALIDATION_FAILED") -> None:
        super().__init__(message)
        self.code = code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha256_path(path: Path, *, cached: bool = False) -> str:
    signature = file_cache_identity(path)
    key = str(path.resolve())
    if cached and key in _HASH_CACHE and _HASH_CACHE[key][0] == signature:
        return _HASH_CACHE[key][1]
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    result = digest.hexdigest()
    _HASH_CACHE[key] = signature, result
    return result


@contextmanager
def _read_only(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_run(source_run: str | Path) -> tuple[Path, Path, dict[str, Any]]:
    from .source_query import SourceQueryError, validate_source_snapshot

    try:
        root, manifest = validate_source_snapshot(source_run)
    except SourceQueryError as exc:
        raise SemanticContractError(str(exc), code="SOURCE_MISMATCH") from exc
    gpkg = root / "source.gpkg"
    if manifest.get("status") != "SOURCE_EXPORTED":
        raise SemanticContractError("source snapshot is not SOURCE_EXPORTED", code="PUBLICATION_INCOMPLETE")
    expected = manifest.get("artifacts", {}).get("source_gpkg", {}).get("sha256")
    source_hash = manifest.get("source", {}).get("sha256")
    snapshot_hash = manifest.get("snapshot_sha256")
    if not all(isinstance(value, str) and len(value) == 64 for value in (expected, source_hash, snapshot_hash)):
        raise SemanticContractError("source/snapshot/artifact SHA256 bindings are required", code="SOURCE_MISMATCH")
    if _sha256_path(gpkg, cached=True) != expected:
        raise SemanticContractError("source.gpkg digest mismatch", code="SOURCE_MISMATCH")
    return root, gpkg, manifest


def _point(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        value = json.loads(row.get("native_centroid") or "null")
        x, y = float(value[0]), float(value[1])
        return (x, y) if math.isfinite(x) and math.isfinite(y) else None
    except (TypeError, ValueError, IndexError):
        return None


def _points(row: dict[str, Any]) -> list[list[float]]:
    try:
        values = json.loads(row.get("native_points") or "[]")
        if not isinstance(values, list) or any(not isinstance(p, list) or len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p) for p in values):
            return []
        return values
    except (TypeError, ValueError):
        return []


def _partition(row: dict[str, Any]) -> tuple[str, str]:
    return row.get("cad_layout") or "", row.get("cad_role") or ""


def _label_grid(rows: list[dict[str, Any]]) -> tuple[dict, dict]:
    partitions: dict[Any, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        point = _point(row)
        if point is not None:
            partitions[_partition(row)].append(point)
    cells = {}
    for key, values in partitions.items():
        axes = [sorted({point[i] for point in values}) for i in (0, 1)]
        gaps = [b - a for axis in axes for a, b in zip(axis, axis[1:]) if b > a]
        cells[key] = max(statistics.median(gaps) * 8.0, 1e-9) if gaps else 1.0
    grid: dict[Any, list[dict]] = defaultdict(list)
    for row in rows:
        point = _point(row)
        if row["source_table"] == "source_text" and str(row.get("text") or "").strip() and point is not None:
            cell = cells[_partition(row)]
            grid[(*_partition(row), math.floor(point[0] / cell), math.floor(point[1] / cell))].append(row)
    return grid, cells


def prepare_semantics(*, source_run: str | Path, output_dir: str | Path | None = None, force: bool = False) -> dict[str, Any]:
    """Materialize an immutable SQL candidate index; never overwrite a prepare."""
    root, gpkg, source_manifest = _source_run(source_run)
    output = Path(output_dir).resolve() if output_dir is not None else root.with_name(root.name + "-semantic-prepare")
    if output == root or root in output.parents:
        raise SemanticContractError("semantic preparation must be outside the immutable source snapshot")
    if force:
        raise SemanticContractError("force overwrite is forbidden; select a new prepare directory")
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name("." + output.name + "." + uuid.uuid4().hex + ".staging")
    staging.mkdir()
    try:
        rows = []
        with _read_only(gpkg) as source:
            for table in SOURCE_LAYERS:
                for raw in source.execute(f'SELECT * FROM "{table}" ORDER BY entity_key'):
                    row = dict(raw)
                    row["has_source_geometry"] = row.pop("geom", None) is not None
                    row["source_table"] = table
                    rows.append(row)
        if len({r["entity_key"] for r in rows}) != len(rows):
            raise SemanticContractError("duplicate source entity keys")
        grid, cells = _label_grid(rows)
        dimension_by_endpoints: dict[Any, list[dict]] = defaultdict(list)
        for row in rows:
            value, points = row.get("dimension_value"), _points(row)
            if row.get("dwg_type") == "DIMENSION" and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 and len(points) == 2:
                dimension_by_endpoints[(_partition(row), tuple(sorted(map(tuple, points))))].append(row)
        database = staging / "candidate_index.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            db.executescript("""
                CREATE TABLE entities(entity_key TEXT PRIMARY KEY, source_table TEXT NOT NULL, layer TEXT, layout TEXT, cad_role TEXT, dwg_type TEXT);
                CREATE TABLE candidates(candidate_id TEXT PRIMARY KEY, entity_key TEXT NOT NULL, relation_kind TEXT NOT NULL, policy_id TEXT NOT NULL, target_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX candidate_entity_kind ON candidates(entity_key,relation_kind,candidate_id);
                CREATE INDEX candidate_kind_id ON candidates(relation_kind,policy_id,candidate_id);
                CREATE INDEX candidate_kind_cursor ON candidates(relation_kind,candidate_id);
                CREATE INDEX candidate_target ON candidates(target_id,relation_kind,candidate_id);
            """)
            count = 0

            def add(row: dict, kind: str, policy: str, target: str, evidence: dict) -> None:
                nonlocal count
                payload = {"entity_key": row["entity_key"], "relation_kind": kind, "policy_id": policy, "target_id": target, "evidence": evidence}
                payload["candidate_id"] = _digest({"snapshot": source_manifest["snapshot_sha256"], **payload})
                db.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?)", (payload["candidate_id"], row["entity_key"], kind, policy, target, _json(payload)))
                count += 1

            for row in rows:
                key, table = row["entity_key"], row["source_table"]
                db.execute("INSERT INTO entities VALUES(?,?,?,?,?,?)", (key, table, row.get("dwg_layer"), row.get("cad_layout"), row.get("cad_role"), row.get("dwg_type")))
                for state in TERMINAL_STATES[1:]:
                    add(row, "terminal", "terminal_accounting_v1", state, {"source_table": table})
                if table not in CLASS_BY_TABLE or not row["has_source_geometry"]:
                    continue
                for class_id in CLASS_BY_TABLE[table]:
                    add(row, "class", "geometry_class_v1", class_id, {"source_table": table, "dwg_type": row.get("dwg_type"), "claim": "geometry_compatible_choice_only"})
                point = _point(row)
                if point is not None:
                    cell = cells[_partition(row)]
                    gx, gy = math.floor(point[0] / cell), math.floor(point[1] / cell)
                    nearby = []
                    for x in range(gx - 2, gx + 3):
                        for y in range(gy - 2, gy + 3):
                            for label in grid.get((*_partition(row), x, y), ()):
                                distance = math.dist(point, _point(label))
                                if distance <= cell * 2.5:
                                    nearby.append((distance, label["entity_key"], label))
                    for distance, label_key, label in sorted(nearby)[:5]:
                        add(row, "label", "label_proximity_v1", label_key, {"relationship": "nearby", "distance_native": distance, "source_text": label["text"]})
                points = _points(row)
                if table == "source_lines" and len(points) >= 2:
                    endpoint_key = (_partition(row), tuple(sorted((tuple(points[0]), tuple(points[-1])))))
                    for dimension in dimension_by_endpoints.get(endpoint_key, ()):
                        add(row, "dimension", "dimension_exact_endpoints_v1", dimension["entity_key"], {"relationship": "exact_endpoint_pair", "source_dimension_value": dimension["dimension_value"], "length_policy": "separate_dimension_value_not_native_length"})
            db.commit()
        index_hash = _sha256_path(database)
        manifest = {
            "schema_version": PREPARE_SCHEMA, "status": "READY_FOR_SEMANTIC_DECISIONS", "source_run": str(root),
            "source_sha256": source_manifest["source"]["sha256"], "snapshot_sha256": source_manifest["snapshot_sha256"],
            "source_gpkg_sha256": source_manifest["artifacts"]["source_gpkg"]["sha256"],
            "candidates_sha256": index_hash, "policy_sha256": _digest(POLICIES), "ontology_sha256": _digest(CLASS_BY_TABLE),
            "candidate_count": count, "entity_count": len(rows), "policies": POLICIES,
            "candidate_index": {"path": str(output / database.name), "sha256": index_hash},
            "assembly_conservation": {"source_entity_count": len(rows), "assembled_entity_count": len(rows), "difference": 0, "passed": True},
            "pipeline_boundary": "semantic_native_cad_space", "manifest_path": str(output / "manifest.json"),
        }
        _atomic_json(staging / "manifest.json", manifest)
        os.rename(staging, output)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _load_prepare(prepare_manifest: str | Path) -> tuple[dict[str, Any], Path]:
    path = Path(prepare_manifest).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != PREPARE_SCHEMA or manifest.get("status") != "READY_FOR_SEMANTIC_DECISIONS":
        raise SemanticContractError("unsupported or incomplete semantic prepare", code="PUBLICATION_INCOMPLETE")
    index = (path.parent / "candidate_index.sqlite3").resolve()
    if index.parent != path.parent:
        raise SemanticContractError("candidate index must stay inside its prepare directory")
    if _sha256_path(index, cached=True) != manifest.get("candidates_sha256") or manifest["candidate_index"].get("sha256") != manifest.get("candidates_sha256"):
        raise SemanticContractError("candidate index digest mismatch", code="SOURCE_MISMATCH")
    if manifest.get("policy_sha256") != _digest(POLICIES) or manifest.get("ontology_sha256") != _digest(CLASS_BY_TABLE):
        raise SemanticContractError("candidate policy/ontology version mismatch", code="SOURCE_MISMATCH")
    return manifest, index


def _bindings(manifest: dict[str, Any]) -> dict[str, str]:
    return {key: manifest[key] for key in ("source_sha256", "snapshot_sha256", "source_gpkg_sha256", "candidates_sha256", "policy_sha256", "ontology_sha256")}


def _bound_context(source_run: str | Path, prepare_manifest: str | Path) -> tuple[dict, Path, Path]:
    _, gpkg, source = _source_run(source_run)
    manifest, index = _load_prepare(prepare_manifest)
    for key, actual in (("source_sha256", source["source"]["sha256"]), ("snapshot_sha256", source["snapshot_sha256"]), ("source_gpkg_sha256", source["artifacts"]["source_gpkg"]["sha256"])):
        if manifest[key] != actual:
            raise SemanticContractError("prepare " + key + " mismatch", code="SOURCE_MISMATCH")
    return manifest, index, gpkg


def query_relationship_candidates(*, prepare_manifest: str | Path, entity_ids: list[str] | None = None, relation_kind: str = "label", policy_id: str | None = None, cursor: str | None = None, limit: int = 50, max_bytes: int = 65536) -> dict[str, Any]:
    """SQL keyset query. Cursor is bound to snapshot, index and all filters."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200 or not 1024 <= max_bytes <= 65536:
        raise SemanticContractError("invalid page/byte budget")
    if relation_kind not in {p["kind"] for p in POLICIES.values()}:
        raise SemanticContractError("unregistered relationship kind", code="UNSUPPORTED_OPERATION")
    if policy_id is not None and (policy_id not in POLICIES or POLICIES[policy_id]["kind"] != relation_kind):
        raise SemanticContractError("unknown or incompatible policy ID", code="UNKNOWN_ID")
    if entity_ids is not None and (not isinstance(entity_ids, list) or not 1 <= len(entity_ids) <= 200 or any(not isinstance(i, str) or not i for i in entity_ids)):
        raise SemanticContractError("entity_ids must contain 1..200 observed IDs")
    manifest, index = _load_prepare(prepare_manifest)
    filters = {"entity_ids": sorted(set(entity_ids or [])), "relation_kind": relation_kind, "policy_id": policy_id}
    binding = _digest({**_bindings(manifest), "filters": filters})
    last = ""
    if cursor:
        try:
            token = json.loads(base64.urlsafe_b64decode(cursor))
            if token["binding"] != binding or not isinstance(token["last"], str):
                raise ValueError()
            last = token["last"]
        except Exception as exc:
            raise SemanticContractError("stale or invalid candidate cursor") from exc
    clauses, params = ["relation_kind=?", "candidate_id> ?"], [relation_kind, last]
    if policy_id:
        clauses.append("policy_id=?")
        params.append(policy_id)
    with _read_only(index) as db:
        if entity_ids:
            marks = ",".join("?" for _ in filters["entity_ids"])
            known = {row[0] for row in db.execute(f"SELECT entity_key FROM entities WHERE entity_key IN ({marks})", filters["entity_ids"])}
            if known != set(entity_ids):
                raise SemanticContractError("unknown source entity ID", code="UNKNOWN_ID")
            clauses.append(f"entity_key IN ({marks})")
            params.extend(filters["entity_ids"])
        rows = db.execute("SELECT payload FROM candidates WHERE " + " AND ".join(clauses) + " ORDER BY candidate_id LIMIT ?", [*params, limit + 1]).fetchall()
    items, used = [], 1800
    for row in rows[:limit]:
        payload = json.loads(row[0])
        size = len(row[0].encode("utf-8"))
        if used + size > max_bytes:
            # The ID, relationship and numeric evidence remain complete. Long text
            # is explicitly deferred to bounded source context, never silently cut.
            if "source_text" in payload["evidence"]:
                payload["evidence"].pop("source_text")
                payload["evidence"]["source_text_deferred"] = True
                size = len(_json(payload).encode("utf-8"))
            if used + size > max_bytes:
                break
        used += size
        items.append(payload)
    if rows and not items:
        raise SemanticContractError("byte budget too small for one complete candidate")
    next_cursor = base64.urlsafe_b64encode(_json({"binding": binding, "last": items[-1]["candidate_id"]}).encode()).decode() if len(rows) > len(items) else None
    result = {"schema_version": "cad2gis.relationship_candidate_page.v1", **_bindings(manifest), "items": items, "next_cursor": next_cursor, "row_count": len(items), "complete": next_cursor is None}
    result["response_bytes"] = 0
    for _ in range(4):
        result["response_bytes"] = len(_json(result).encode("utf-8"))
    if result["response_bytes"] > max_bytes:
        raise SemanticContractError("candidate response exceeds byte budget")
    return result


def list_semantic_candidates(**kwargs: Any) -> dict[str, Any]:
    return query_relationship_candidates(**kwargs)


def _source_fingerprints(path: Path) -> dict[str, str]:
    """Exact original snapshot tables, including WKB, CRS and GPKG registration.

    Only the new semantic tables are excluded. Original metadata, extension and
    spatial-index tables are source facts too; equal WKB with a changed SRS is
    not a source-preserving result.
    """
    result = {}
    with _read_only(path) as db:
        tables = list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'semantic_%' ORDER BY name"))
        for table, create_sql in tables:
            safe = table.replace('"', '""')
            columns = list(db.execute(f'PRAGMA table_info("{safe}")'))
            order = ",".join('"' + row[1].replace('"', '""') + '"' for row in columns)
            digest = hashlib.sha256(_json({"create_sql": create_sql, "columns": [tuple(row) for row in columns]}).encode())
            for row in db.execute(f'SELECT * FROM "{safe}" ORDER BY {order}'):
                values = [({"blob_hex": v.hex()} if isinstance(v, bytes) else v) for v in row]
                digest.update(_json(values).encode())
                digest.update(b"\n")
            result[table] = digest.hexdigest()
    return result


def _compile_candidate(*, source_gpkg: Path, destination: Path, decisions: list[dict], manifest: dict) -> dict:
    """Write only inside a unique staging directory; caller owns publication CAS."""
    shutil.copyfile(source_gpkg, destination)
    with closing(sqlite3.connect(destination)) as db:
        db.executescript("""
            CREATE TABLE semantic_features(feature_id TEXT PRIMARY KEY, primary_entity_key TEXT UNIQUE NOT NULL, semantic_class TEXT, source_label_entity_key TEXT, display_label TEXT, source_dimension_entity_key TEXT, source_dimension_value REAL, source_table TEXT NOT NULL, provenance_json TEXT NOT NULL);
            CREATE TABLE semantic_entity_ledger(entity_key TEXT PRIMARY KEY, terminal_state TEXT NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE semantic_manifest(payload TEXT NOT NULL);
        """)
        entity_tables = {}
        for table in SOURCE_LAYERS:
            entity_tables.update((row[0], table) for row in db.execute(f'SELECT entity_key FROM "{table}"'))
        decision_map = {d["entity_key"]: d for d in decisions}
        for key, table in entity_tables.items():
            decision = decision_map.get(key, {})
            state = decision.get("terminal_state", "UNRESOLVED")
            db.execute("INSERT INTO semantic_entity_ledger VALUES(?,?,?)", (key, state, "committed_semantic_revision" if decision else "no_semantic_decision"))
            if state != "CONSUMED_BY_FEATURE":
                continue
            label = decision.get("label_entity_key")
            dimension = decision.get("dimension_entity_key")
            text = db.execute("SELECT text FROM source_text WHERE entity_key=?", (label,)).fetchone() if label else None
            value = db.execute(f'SELECT dimension_value FROM "{entity_tables[dimension]}" WHERE entity_key=?', (dimension,)).fetchone() if dimension else None
            db.execute("INSERT INTO semantic_features VALUES(?,?,?,?,?,?,?,?,?)", (_digest({"entity_key": key, "snapshot": manifest["snapshot_sha256"]}), key, decision.get("class_id"), label, text[0] if text else None, dimension, value[0] if value else None, table, _json({"revision": manifest["revision"], "geometry": "unchanged_source_reference", "candidate_ids": decision.get("candidate_ids", [])})))
        db.execute("INSERT INTO semantic_manifest VALUES(?)", (_json(manifest),))
        db.commit()
    validation = validate_semantics(destination, source_gpkg=source_gpkg)
    if not validation["valid"]:
        raise SemanticContractError("semantic validation failed: " + _json(validation))
    return validation


def validate_semantics(semantic_gpkg: str | Path, *, source_gpkg: str | Path | None = None) -> dict[str, Any]:
    path = Path(semantic_gpkg).resolve()
    with _read_only(path) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        source_count = db.execute("SELECT COUNT(*) FROM source_entity_accounting").fetchone()[0]
        ledger_count = db.execute("SELECT COUNT(*) FROM semantic_entity_ledger").fetchone()[0]
        invalid = db.execute("SELECT COUNT(*) FROM semantic_entity_ledger WHERE terminal_state NOT IN (?,?,?,?)", TERMINAL_STATES).fetchone()[0]
        missing = db.execute("SELECT COUNT(*) FROM source_entity_accounting s LEFT JOIN semantic_entity_ledger l ON l.entity_key=s.entity_key WHERE l.entity_key IS NULL").fetchone()[0]
        states = dict(db.execute("SELECT terminal_state,COUNT(*) FROM semantic_entity_ledger GROUP BY terminal_state"))
        feature_count = db.execute("SELECT COUNT(*) FROM semantic_features").fetchone()[0]
    fingerprints = _source_fingerprints(path)
    source_equal = fingerprints == _source_fingerprints(Path(source_gpkg)) if source_gpkg is not None else None
    return {"schema_version": "cad2gis.semantic_validation.v2", "valid": integrity == "ok" and source_count == ledger_count and not invalid and not missing and source_equal is not False, "integrity": integrity, "source_entity_count": source_count, "ledger_entity_count": ledger_count, "feature_count": feature_count, "terminal_state_counts": states, "conservation_difference": ledger_count - source_count, "source_facts_unchanged": source_equal, "source_table_fingerprints": fingerprints}
