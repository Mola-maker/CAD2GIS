"""Durable semantic revisions, strict ID patches and fenced local compile jobs.

SQLite is the authority. The outbox is durable local scheduling evidence; a
future Redis adapter may deliver its job IDs but cannot bypass this CAS.
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from .semantic_stage import (
    SEMANTIC_SCHEMA, SemanticContractError, _atomic_json, _bindings,
    _bound_context, _compile_candidate, _digest, _json, _read_only,
    _sha256_path, validate_semantics,
)

PATCH_SCHEMA = "cad2gis.semantic_patch.v1"
COMPILER_VERSION = "source-preserving-semantic-v2"
_PATCH_BINDINGS = ("source_sha256", "snapshot_sha256", "candidates_sha256", "policy_sha256", "ontology_sha256")
_OPERATIONS = {
    "attach_existing_label": ("label", "label_entity_key"),
    "select_semantic_class": ("class", "class_id"),
    "bind_existing_dimension": ("dimension", "dimension_entity_key"),
    "set_terminal_state": ("terminal", "terminal_state"),
}


class SemanticConflictError(SemanticContractError):
    """A revision, idempotency key or worker generation is no longer current."""


def _connect(path: str | Path) -> sqlite3.Connection:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size:
        with _read_only(destination) as existing:
            tables = {row[0] for row in existing.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "source_bindings" not in tables:
                raise SemanticContractError("refusing to modify a non-semantic database")
    db = sqlite3.connect(destination, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def _initialize(path: str | Path, bindings: dict) -> None:
    db = _connect(path)
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS source_bindings(id INTEGER PRIMARY KEY CHECK(id=1), binding_json TEXT NOT NULL, revision INTEGER NOT NULL, accepted_run_id TEXT);
            CREATE TABLE IF NOT EXISTS semantic_revisions(revision INTEGER PRIMARY KEY, parent_revision INTEGER, patch_hash TEXT, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS semantic_patches(patch_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL, preview_hash TEXT NOT NULL, revision INTEGER NOT NULL REFERENCES semantic_revisions(revision), payload TEXT NOT NULL, result_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entity_decisions(entity_key TEXT PRIMARY KEY, revision INTEGER NOT NULL REFERENCES semantic_revisions(revision), decision_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decision_history(entity_key TEXT NOT NULL, revision INTEGER NOT NULL REFERENCES semantic_revisions(revision), decision_json TEXT NOT NULL, PRIMARY KEY(entity_key,revision));
            CREATE INDEX IF NOT EXISTS history_revision ON decision_history(revision,entity_key);
            CREATE TABLE IF NOT EXISTS label_bindings(entity_key TEXT PRIMARY KEY, label_entity_key TEXT UNIQUE NOT NULL, revision INTEGER NOT NULL REFERENCES semantic_revisions(revision));
            CREATE TABLE IF NOT EXISTS derived_relations(entity_key TEXT PRIMARY KEY, dimension_entity_key TEXT UNIQUE NOT NULL, revision INTEGER NOT NULL REFERENCES semantic_revisions(revision));
            CREATE TABLE IF NOT EXISTS operation_events(event_id TEXT PRIMARY KEY, revision INTEGER, event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS compile_jobs(job_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL REFERENCES semantic_revisions(revision), state TEXT NOT NULL CHECK(state IN ('queued','running','validated','published','failed','cancelled')), generation INTEGER NOT NULL, result_run_id TEXT UNIQUE, result_manifest TEXT, error TEXT, updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS compile_state ON compile_jobs(state,updated_at);
            CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY REFERENCES operation_events(event_id), job_id TEXT REFERENCES compile_jobs(job_id), payload TEXT NOT NULL, dispatched_at REAL);
        """)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT binding_json FROM source_bindings WHERE id=1").fetchone()
        if row is None:
            db.execute("INSERT INTO source_bindings(id,binding_json,revision) VALUES(1,?,0)", (_json(bindings),))
            db.execute("INSERT INTO semantic_revisions VALUES(0,NULL,NULL,?)", (time.time(),))
        elif row[0] != _json(bindings):
            raise SemanticConflictError("semantic store source/snapshot/candidate/policy binding mismatch", code="SOURCE_MISMATCH")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _store_outside_inputs(path: str | Path, source_run: str | Path, prepare_manifest: str | Path) -> Path:
    destination = Path(path).resolve()
    for immutable in (Path(source_run).resolve(), Path(prepare_manifest).resolve().parent):
        if destination == immutable or immutable in destination.parents:
            raise SemanticContractError("semantic store must be outside immutable source and prepare directories")
    return destination


def initialize_semantic_store(*, source_run: str | Path, prepare_manifest: str | Path, semantic_store: str | Path) -> dict:
    manifest, _, _ = _bound_context(source_run, prepare_manifest)
    destination = _store_outside_inputs(semantic_store, source_run, prepare_manifest)
    _initialize(destination, _bindings(manifest))
    with _read_only(destination) as db:
        revision = db.execute("SELECT revision FROM source_bindings WHERE id=1").fetchone()[0]
    return {"schema_version": "cad2gis.semantic_store.v1", "semantic_store": str(destination), "revision": revision, **_bindings(manifest), "accepted_run_id": None}


def inspect_semantic_store(*, semantic_store: str | Path, job_id: str | None = None, idempotency_key: str | None = None) -> dict:
    """Read durable status after a timeout/cancellation without creating a DB."""
    if job_id is not None and idempotency_key is not None:
        raise SemanticContractError("job_id and idempotency_key are mutually exclusive")
    with _read_only(Path(semantic_store).resolve()) as db:
        head = db.execute("SELECT * FROM source_bindings WHERE id=1").fetchone()
        patches = [dict(row) for row in db.execute("SELECT patch_id,revision,payload_hash AS patch_hash FROM semantic_patches ORDER BY revision DESC LIMIT 20")]
        job = None
        if job_id is not None:
            row = db.execute("SELECT job_id,revision,state,generation,result_run_id,result_manifest,error,updated_at FROM compile_jobs WHERE job_id=?", (_key(job_id, "job_id"),)).fetchone()
            if row is None:
                raise SemanticContractError("unknown compile job ID", code="UNKNOWN_ID")
            job = dict(row)
        committed = None
        if idempotency_key is not None:
            _key(idempotency_key, "idempotency_key")
            row = db.execute("SELECT job_id,revision,state,generation,result_run_id,result_manifest,error,updated_at FROM compile_jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            job = dict(row) if row else None
            row = db.execute("SELECT result_json FROM semantic_patches WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            committed = json.loads(row[0]) if row else None
        pending = db.execute("SELECT COUNT(*) FROM outbox WHERE dispatched_at IS NULL").fetchone()[0]
    return {"schema_version": "cad2gis.semantic_store_status.v1", **json.loads(head["binding_json"]), "revision": head["revision"], "accepted_run_id": head["accepted_run_id"], "recent_committed_patches": patches, "job": job, "idempotency_key": idempotency_key, "committed_patch": committed, "key_found": job is not None or committed is not None if idempotency_key is not None else None, "pending_outbox_events": pending}


def _compiler_identity() -> dict:
    """Actual implementation and execution runtime, not just a release label."""
    from .implementation import production_conversion_provenance

    modules = [Path(__file__).resolve(), Path(__file__).with_name("semantic_stage.py").resolve()]
    identity = {"version": COMPILER_VERSION, "production_conversion": production_conversion_provenance(), "implementation_sha256": {path.name: _sha256_path(path, cached=True) for path in modules}, "python": sys.version, "sqlite": sqlite3.sqlite_version, "platform": platform.system(), "machine": platform.machine()}
    return {**identity, "fingerprint": _digest(identity)}


def _key(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise SemanticContractError(label + " must be a nonempty ID of at most 512 characters")
    return value


def _validate_patch(patch: dict, manifest: dict, index: Path) -> tuple[dict, list[dict]]:
    allowed = {*_PATCH_BINDINGS, "base_revision", "operations"}
    if not isinstance(patch, dict) or set(patch) != allowed:
        raise SemanticContractError("patch must contain exactly binding hashes, base_revision and operations; numeric geometry/text writes are forbidden")
    if type(patch["base_revision"]) is not int or patch["base_revision"] < 0:
        raise SemanticContractError("base_revision must be a nonnegative integer")
    for name in _PATCH_BINDINGS:
        if patch[name] != manifest[name]:
            raise SemanticContractError("patch " + name + " mismatch", code="SOURCE_MISMATCH")
    operations = patch["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
        raise SemanticContractError("operations must contain 1..200 ID operations")
    normalized, seen = [], set()
    # One batch lookup, independent of source/candidate corpus size.
    ids = []
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("operation") not in _OPERATIONS:
            raise SemanticContractError("unknown or unimplemented semantic operation", code="UNSUPPORTED_OPERATION")
        kind, target_field = _OPERATIONS[operation["operation"]]
        if set(operation) != {"operation", "entity_key", "candidate_id", "policy_id", target_field}:
            raise SemanticContractError("operation permits only observed entity/candidate/policy/target IDs")
        for field, value in operation.items():
            _key(value, field)
        identity = (operation["entity_key"], kind)
        if identity in seen:
            raise SemanticContractError("duplicate operation kind for one entity in patch")
        seen.add(identity)
        ids.append(operation["candidate_id"])
    with _read_only(index) as db:
        placeholders = ",".join("?" for _ in ids)
        candidates = {row["candidate_id"]: row for row in db.execute(f"SELECT * FROM candidates WHERE candidate_id IN ({placeholders})", ids)}
    for operation in operations:
        kind, field = _OPERATIONS[operation["operation"]]
        candidate = candidates.get(operation["candidate_id"])
        if candidate is None:
            raise SemanticContractError("unknown semantic candidate ID", code="UNKNOWN_ID")
        if any((candidate["entity_key"] != operation["entity_key"], candidate["relation_kind"] != kind, candidate["policy_id"] != operation["policy_id"], candidate["target_id"] != operation[field])):
            raise SemanticContractError("operation does not match an observed candidate and policy")
        normalized.append(dict(operation))
    # A terminal override may not hide a simultaneous binding/class assertion.
    terminal_entities = {o["entity_key"] for o in normalized if o["operation"] == "set_terminal_state"}
    if terminal_entities & {o["entity_key"] for o in normalized if o["operation"] != "set_terminal_state"}:
        raise SemanticContractError("terminal override and semantic binding cannot target the same entity in one patch")
    return {**patch, "operations": normalized}, normalized


def _fold(db: sqlite3.Connection, operations: list[dict]) -> list[dict]:
    keys = sorted({o["entity_key"] for o in operations})
    placeholders = ",".join("?" for _ in keys)
    states = {row["entity_key"]: json.loads(row["decision_json"]) for row in db.execute(f"SELECT * FROM entity_decisions WHERE entity_key IN ({placeholders})", keys)}
    for key in keys:
        states.setdefault(key, {"entity_key": key, "terminal_state": "UNRESOLVED", "class_id": None, "label_entity_key": None, "dimension_entity_key": None, "candidate_ids": []})
    for operation in operations:
        state = states[operation["entity_key"]]
        _, field = _OPERATIONS[operation["operation"]]
        if field == "terminal_state":
            state.update(class_id=None, label_entity_key=None, dimension_entity_key=None, candidate_ids=[])
        else:
            state["terminal_state"] = "CONSUMED_BY_FEATURE"
        state[field] = operation[field]
        state["candidate_ids"] = sorted(set([*state["candidate_ids"], operation["candidate_id"]]))
    # A label or source DIMENSION binds to at most one semantic asset. Permit a
    # same-patch transfer only when the previous owner is explicitly cleared.
    for field, table in (("label_entity_key", "label_bindings"), ("dimension_entity_key", "derived_relations")):
        claims: dict[str, str] = {}
        for state in states.values():
            target = state.get(field)
            if target:
                if target in claims and claims[target] != state["entity_key"]:
                    raise SemanticConflictError("multiple semantic entities claim " + field)
                claims[target] = state["entity_key"]
        if claims:
            marks = ",".join("?" for _ in claims)
            for row in db.execute(f"SELECT entity_key,{field} FROM {table} WHERE {field} IN ({marks})", list(claims)):
                owner, target = row[0], row[1]
                if owner != claims[target] and (owner not in states or states[owner].get(field) == target):
                    raise SemanticConflictError(field + " is already bound to another entity")
    return [states[key] for key in keys]


def _head(db: sqlite3.Connection, manifest: dict, base_revision: int) -> None:
    row = db.execute("SELECT binding_json,revision FROM source_bindings WHERE id=1").fetchone()
    if row["binding_json"] != _json(_bindings(manifest)):
        raise SemanticConflictError("store evidence binding changed", code="SOURCE_MISMATCH")
    if row["revision"] != base_revision:
        raise SemanticConflictError(f"stale base revision {base_revision}; current revision is {row['revision']}", code="STALE_REVISION")


def preview_semantic_patch(*, source_run: str | Path, prepare_manifest: str | Path, semantic_store: str | Path, patch: dict) -> dict[str, Any]:
    manifest, index, _ = _bound_context(source_run, prepare_manifest)
    normalized, operations = _validate_patch(patch, manifest, index)
    destination = _store_outside_inputs(semantic_store, source_run, prepare_manifest)
    if not destination.is_file():
        raise SemanticContractError("initialize_semantic_store is required before a read-only preview")
    with _read_only(destination) as db:
        db.execute("BEGIN")
        _head(db, manifest, normalized["base_revision"])
        after = _fold(db, operations)
        db.commit()
    return {"schema_version": "cad2gis.semantic_patch_preview.v1", "valid": True, "preview_hash": _digest({"schema": PATCH_SCHEMA, "patch": normalized}), "patch_hash": _digest(normalized), "base_revision": normalized["base_revision"], "affected_entity_ids": [s["entity_key"] for s in after], "after": after, "required_validators": ["source_table_exact_fingerprints", "semantic_terminal_conservation", "reader_accounting_unchanged"]}


def commit_semantic_patch(*, source_run: str | Path, prepare_manifest: str | Path, semantic_store: str | Path, patch: dict, preview_hash: str, idempotency_key: str) -> dict[str, Any]:
    _key(idempotency_key, "idempotency_key")
    manifest, index, _ = _bound_context(source_run, prepare_manifest)
    normalized, operations = _validate_patch(patch, manifest, index)
    expected_preview = _digest({"schema": PATCH_SCHEMA, "patch": normalized})
    if preview_hash != expected_preview:
        raise SemanticContractError("preview_hash mismatch; preview the exact patch before committing")
    payload_hash = _digest(normalized)
    _store_outside_inputs(semantic_store, source_run, prepare_manifest)
    _initialize(semantic_store, _bindings(manifest))
    db = _connect(semantic_store)
    try:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT payload_hash,result_json FROM semantic_patches WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if old:
            if old["payload_hash"] != payload_hash:
                raise SemanticConflictError("idempotency key already used for a different patch", code="IDEMPOTENCY_CONFLICT")
            db.commit()
            return json.loads(old["result_json"])
        _head(db, manifest, normalized["base_revision"])
        after = _fold(db, operations)
        revision = normalized["base_revision"] + 1
        patch_id, event_id = uuid.uuid4().hex, uuid.uuid4().hex
        result = {"schema_version": "cad2gis.semantic_patch_commit.v1", "revision": revision, "event_id": event_id, "patch_id": patch_id, "patch_hash": payload_hash, "affected_row_count": len(after), **_bindings(manifest)}
        db.execute("INSERT INTO semantic_revisions VALUES(?,?,?,?)", (revision, normalized["base_revision"], payload_hash, time.time()))
        # Delete changed claims first to make an explicit whole-patch transfer atomic.
        for state in after:
            db.execute("DELETE FROM label_bindings WHERE entity_key=?", (state["entity_key"],))
            db.execute("DELETE FROM derived_relations WHERE entity_key=?", (state["entity_key"],))
        for state in after:
            db.execute("INSERT INTO entity_decisions VALUES(?,?,?) ON CONFLICT(entity_key) DO UPDATE SET revision=excluded.revision,decision_json=excluded.decision_json", (state["entity_key"], revision, _json(state)))
            db.execute("INSERT INTO decision_history VALUES(?,?,?)", (state["entity_key"], revision, _json(state)))
            if state.get("label_entity_key"):
                db.execute("INSERT INTO label_bindings VALUES(?,?,?)", (state["entity_key"], state["label_entity_key"], revision))
            if state.get("dimension_entity_key"):
                db.execute("INSERT INTO derived_relations VALUES(?,?,?)", (state["entity_key"], state["dimension_entity_key"], revision))
        db.execute("INSERT INTO semantic_patches VALUES(?,?,?,?,?,?,?)", (patch_id, idempotency_key, payload_hash, preview_hash, revision, _json(normalized), _json(result)))
        db.execute("INSERT INTO operation_events VALUES(?,?,?,?,?)", (event_id, revision, "semantic_patch_committed", _json(result), time.time()))
        db.execute("INSERT INTO outbox(event_id,payload) VALUES(?,?)", (event_id, _json({"revision": revision, "patch_id": patch_id})))
        changed = db.execute("UPDATE source_bindings SET revision=? WHERE id=1 AND revision=?", (revision, normalized["base_revision"])).rowcount
        if changed != 1:
            raise SemanticConflictError("revision CAS failed", code="STALE_REVISION")
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _event(db: sqlite3.Connection, job_id: str, revision: int, event_type: str, payload: dict) -> None:
    event_id = uuid.uuid4().hex
    db.execute("INSERT INTO operation_events VALUES(?,?,?,?,?)", (event_id, revision, event_type, _json(payload), time.time()))
    db.execute("INSERT INTO outbox(event_id,job_id,payload) VALUES(?,?,?)", (event_id, job_id, _json(payload)))


def _job_state(path: str | Path, job_id: str) -> dict:
    with closing(_connect(path)) as db:
        row = db.execute("SELECT * FROM compile_jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise SemanticContractError("unknown compile job ID", code="UNKNOWN_ID")
    return dict(row)


def cancel_compile_job(*, semantic_store: str | Path, job_id: str) -> dict:
    inspect_semantic_store(semantic_store=semantic_store, job_id=job_id)
    db = _connect(semantic_store)
    try:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM compile_jobs WHERE job_id=?", (job_id,)).fetchone()
        if job is None:
            raise SemanticContractError("unknown compile job ID", code="UNKNOWN_ID")
        if job["state"] == "published":
            raise SemanticConflictError("published candidate cannot be cancelled; it remains unaccepted")
        if job["state"] != "cancelled":
            db.execute("UPDATE compile_jobs SET state='cancelled',generation=generation+1,updated_at=? WHERE job_id=?", (time.time(), job_id))
            _event(db, job_id, job["revision"], "compile_cancelled", {"job_id": job_id})
        db.commit()
        return {"job_id": job_id, "state": "cancelled", "accepted_run_id": None}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _transition(path: str | Path, job_id: str, generation: int, from_state: str, to_state: str, **fields: Any) -> None:
    allowed = {"result_run_id", "result_manifest", "error"}
    if set(fields) - allowed:
        raise ValueError("invalid internal job field")
    db = _connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        values = {"state": to_state, "updated_at": time.time(), **fields}
        assignments = ",".join(key + "=?" for key in values)
        changed = db.execute(f"UPDATE compile_jobs SET {assignments} WHERE job_id=? AND generation=? AND state=?", [*values.values(), job_id, generation, from_state]).rowcount
        if changed != 1:
            current = db.execute("SELECT state FROM compile_jobs WHERE job_id=?", (job_id,)).fetchone()
            code = "CANCELLED" if current is not None and current["state"] == "cancelled" else "STALE_REVISION"
            raise SemanticConflictError("stale/cancelled worker generation rejected during result CAS", code=code)
        revision = db.execute("SELECT revision FROM compile_jobs WHERE job_id=?", (job_id,)).fetchone()[0]
        _event(db, job_id, revision, "compile_" + to_state, {"job_id": job_id, "generation": generation, "state": to_state})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _verified_result(job: dict) -> dict:
    path = Path(job["result_manifest"])
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SemanticContractError("published candidate manifest is missing or unreadable", code="PUBLICATION_INCOMPLETE") from exc
    payload = json.loads(job["payload"])
    expected_path = Path(payload["output_dir"]) / str(job["result_run_id"]) / "semantic_manifest.json"
    if path.resolve() != expected_path.resolve():
        raise SemanticContractError("published candidate result path mismatch", code="PUBLICATION_INCOMPLETE")
    if manifest.get("job_id") != job["job_id"] or manifest.get("generation") != job["generation"] or manifest.get("job_payload_hash") != job["payload_hash"] or manifest.get("run_id") != job["result_run_id"]:
        raise SemanticContractError("published candidate job binding mismatch", code="PUBLICATION_INCOMPLETE")
    if any(manifest.get(key) != payload.get(key) for key in (*_PATCH_BINDINGS, "source_gpkg_sha256", "revision")) or manifest.get("compiler_identity") != payload.get("compiler"):
        raise SemanticContractError("published candidate source/compiler binding mismatch", code="SOURCE_MISMATCH")
    if manifest.get("status") != "SEMANTIC_COMPILED" or manifest.get("validation", {}).get("valid") is not True or manifest.get("validation", {}).get("source_facts_unchanged") is not True:
        raise SemanticContractError("published candidate has no successful precision validation", code="PUBLICATION_INCOMPLETE")
    try:
        actual_hash = _sha256_path(path.parent / "semantic.gpkg")
    except OSError as exc:
        raise SemanticContractError("published candidate artifact is missing or unreadable", code="PUBLICATION_INCOMPLETE") from exc
    if actual_hash != manifest.get("semantic_gpkg_sha256"):
        raise SemanticContractError("published candidate artifact digest mismatch", code="PUBLICATION_INCOMPLETE")
    return manifest


def compile_semantic_revision(*, source_run: str | Path, prepare_manifest: str | Path, semantic_store: str | Path, revision: int, output_dir: str | Path, idempotency_key: str, retry_failed: bool = False, _fault: Callable[[str, dict], None] | None = None) -> dict[str, Any]:
    """Compile a committed historical revision into a unique unaccepted candidate.

    File generation and verification run outside write transactions. The job
    generation is checked again after publication before recording its pointer.
    """
    _key(idempotency_key, "idempotency_key")
    if type(revision) is not int or revision < 1:
        raise SemanticContractError("compile requires a committed revision >= 1")
    manifest, _, gpkg = _bound_context(source_run, prepare_manifest)
    _store_outside_inputs(semantic_store, source_run, prepare_manifest)
    _initialize(semantic_store, _bindings(manifest))
    output = Path(output_dir).resolve()
    for immutable in (Path(source_run).resolve(), Path(prepare_manifest).resolve().parent):
        if output == immutable or immutable in output.parents:
            raise SemanticContractError("compile output must be outside immutable source/prepare directories")
    output.mkdir(parents=True, exist_ok=True)
    compiler_identity = _compiler_identity()
    payload = {**_bindings(manifest), "revision": revision, "compiler": compiler_identity, "output_dir": str(output)}
    payload_hash = _digest(payload)
    db = _connect(semantic_store)
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM semantic_revisions WHERE revision=?", (revision,)).fetchone() is None:
            raise SemanticContractError("unknown semantic revision", code="UNKNOWN_ID")
        old = db.execute("SELECT * FROM compile_jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if old:
            job = dict(old)
            if job["payload_hash"] != payload_hash:
                raise SemanticConflictError("compile idempotency key already used for a different payload", code="IDEMPOTENCY_CONFLICT")
            if job["state"] == "published":
                db.commit()
                return _verified_result(job)
            if job["state"] == "cancelled":
                raise SemanticConflictError("cancelled job requires a new idempotency key", code="CANCELLED")
            if job["state"] in {"running", "validated"}:
                db.commit()
                return {"job_id": job["job_id"], "state": job["state"], "generation": job["generation"], "result_run_id": None, "accepted_run_id": None}
            if job["state"] == "failed" and not retry_failed:
                raise SemanticConflictError("job failed; explicitly retry_failed after inspecting its error", code="PUBLICATION_INCOMPLETE")
            generation = job["generation"] + (job["state"] == "failed")
            job_id = job["job_id"]
            db.execute("UPDATE compile_jobs SET generation=?,state='queued',error=NULL,updated_at=? WHERE job_id=?", (generation, time.time(), job_id))
        else:
            job_id, generation = uuid.uuid4().hex, 1
            db.execute("INSERT INTO compile_jobs(job_id,idempotency_key,payload_hash,payload,revision,state,generation,updated_at) VALUES(?,?,?,?,?,'queued',?,?)", (job_id, idempotency_key, payload_hash, _json(payload), revision, generation, time.time()))
        _event(db, job_id, revision, "compile_queued", {"job_id": job_id, "generation": generation})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    _transition(semantic_store, job_id, generation, "queued", "running")
    run_id = f"semantic-{job_id}-g{generation}"
    staging, destination = output / ("." + run_id + ".staging"), output / run_id
    context = {"job_id": job_id, "generation": generation, "run_id": run_id}
    try:
        staging.mkdir(exist_ok=False)
        with closing(_connect(semantic_store)) as db:
            decisions = [json.loads(row[0]) for row in db.execute("SELECT h.decision_json FROM decision_history h JOIN (SELECT entity_key,MAX(revision) AS revision FROM decision_history WHERE revision<=? GROUP BY entity_key) latest ON h.entity_key=latest.entity_key AND h.revision=latest.revision ORDER BY h.entity_key", (revision,))]
        compiled = {"schema_version": SEMANTIC_SCHEMA, "status": "SEMANTIC_COMPILED", "state": "published", **_bindings(manifest), "revision": revision, **context, "job_payload_hash": payload_hash, "compiler_version": COMPILER_VERSION, "compiler_identity": compiler_identity, "pipeline_boundary": "semantic_native_cad_space", "coordinate_status": "source_coordinates_preserved_not_new_registration", "accepted_run_id": None, "delivery_status": "candidate_only_not_canonical_delivery", "excluded_stages": ["topology_repair", "length_inference", "crs_transformation", "gcp_registration", "delivery_publication"]}
        validation = _compile_candidate(source_gpkg=gpkg, destination=staging / "semantic.gpkg", decisions=decisions, manifest=compiled)
        if _fault:
            _fault("after_compile", context)
        compiled.update(validation=validation, semantic_gpkg=str(destination / "semantic.gpkg"), semantic_gpkg_sha256=_sha256_path(staging / "semantic.gpkg"), manifest_path=str(destination / "semantic_manifest.json"))
        # fsync the closed artifact before its manifest and directory are exposed.
        with (staging / "semantic.gpkg").open("r+b") as stream:
            os.fsync(stream.fileno())
        _atomic_json(staging / "semantic_manifest.json", compiled)
        _transition(semantic_store, job_id, generation, "running", "validated")
        if _fault:
            _fault("after_validate", context)
        os.rename(staging, destination)
        if _fault:
            _fault("after_publish", context)
        _transition(semantic_store, job_id, generation, "validated", "published", result_run_id=run_id, result_manifest=str(destination / "semantic_manifest.json"))
        return compiled
    except Exception as exc:
        job = _job_state(semantic_store, job_id)
        if job["generation"] == generation and job["state"] in {"running", "validated"}:
            # A complete orphan remains validated for reconciliation; a failed
            # staging artifact is preserved as audit evidence, never a result.
            if not destination.is_dir():
                _transition(semantic_store, job_id, generation, job["state"], "failed", error=str(exc)[:2000])
        raise


def reconcile_compile_jobs(*, semantic_store: str | Path, source_run: str | Path, prepare_manifest: str | Path) -> dict:
    """Recover complete orphan candidates and fence incomplete interrupted jobs.

    Run when the local worker is known stopped. Active workers interrupted by
    this call are fenced and cannot register a result. No run is promoted.
    """
    manifest, _, gpkg = _bound_context(source_run, prepare_manifest)
    _store_outside_inputs(semantic_store, source_run, prepare_manifest)
    _initialize(semantic_store, _bindings(manifest))
    with closing(_connect(semantic_store)) as db:
        jobs = [dict(row) for row in db.execute("SELECT * FROM compile_jobs WHERE state IN ('queued','running','validated') ORDER BY job_id")]
    results = []
    for job in jobs:
        payload = json.loads(job["payload"])
        run_id = f"semantic-{job['job_id']}-g{job['generation']}"
        candidate = Path(payload["output_dir"]) / run_id
        path = candidate / "semantic_manifest.json"
        if job["state"] == "validated" and path.is_file():
            evidence = {**job, "result_manifest": str(path), "result_run_id": run_id}
            compiled = _verified_result(evidence)
            if any(compiled.get(k) != v for k, v in _bindings(manifest).items()) or compiled.get("revision") != job["revision"]:
                raise SemanticContractError("orphan manifest evidence/revision mismatch", code="SOURCE_MISMATCH")
            validation = validate_semantics(candidate / "semantic.gpkg", source_gpkg=gpkg)
            if not validation["valid"]:
                raise SemanticContractError("orphan candidate failed recovery validation", code="PUBLICATION_INCOMPLETE")
            _transition(semantic_store, job["job_id"], job["generation"], "validated", "published", result_run_id=run_id, result_manifest=str(path))
            results.append({"job_id": job["job_id"], "state": "published", "accepted_run_id": None})
        else:
            _transition(semantic_store, job["job_id"], job["generation"], job["state"], "failed", error="recovery fenced interrupted worker; retry from durable revision")
            results.append({"job_id": job["job_id"], "state": "failed", "retryable": True})
    return {"jobs": results, "accepted_run_id": None, "backend": "durable_local_sqlite_outbox"}
