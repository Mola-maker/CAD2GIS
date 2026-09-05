from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.source_export import export_source
from cad2gis.cad2gis_v3.semantic_stage import (
    SemanticContractError, _sha256_path, prepare_semantics,
    query_relationship_candidates, validate_semantics,
)
from cad2gis.cad2gis_v3.semantic_store import (
    SemanticConflictError, cancel_compile_job, commit_semantic_patch,
    compile_semantic_revision, initialize_semantic_store, inspect_semantic_store, preview_semantic_patch,
    reconcile_compile_jobs,
)


@pytest.fixture
def semantic_fixture(tmp_path):
    source = tmp_path / "source"
    common = {"source_file": "fixture.dwg", "layout": "Model", "layout_role": "model", "cad_role": "model", "layer": "NETWORK", "raw_properties": {"extraction_backend": "fixture"}}

    def entity(key, kind, points, **extra):
        return SourceEntity.from_record({**common, "entity_key": key, "handle": key, "object_name": "AcDb" + kind, "dwg_type_name": kind, "points": points, "centroid": (5, 0), **extra})

    entities = [
        entity("line-1", "LINE", [(0, 0), (10, 0)], native_length=10.0, raw_properties={"extraction_backend": "fixture", "immutable_transform_chain": [[1, 0, 0], [0, 1, 0]]}),
        entity("line-2", "LINE", [(0, 1), (10, 1)], native_length=10.0),
        entity("text-1", "TEXT", [(5, .5)], text="管线-Ａ１２", centroid=(5, .5)),
        entity("dimension-1", "DIMENSION", [(0, 0), (10, 0)], dimension_value=10.125, native_length=8.5),
        entity("dimension-invalid", "DIMENSION", [(0, 2), (10, 2)], dimension_value=33.0),
        entity("unknown-1", "IMAGE", []),
    ]
    drawing = tmp_path / "fixture.dwg"
    drawing.write_bytes(b"AC1032-semantic-simulation-injected-records")
    export_source(source=drawing, run_dir=source, source_crs=None, records=entities)
    prepared = prepare_semantics(source_run=source)
    context = {"source_run": source, "prepare_manifest": prepared["manifest_path"], "semantic_store": tmp_path / "semantic.sqlite3"}
    initialized = initialize_semantic_store(**context)
    return {"context": context, "prepared": prepared, "initialized": initialized, "output": tmp_path / "candidates", "source": source, "tmp_path": tmp_path}


def candidate(fixture, kind="label", entity="line-1", target=None):
    page = query_relationship_candidates(prepare_manifest=fixture["context"]["prepare_manifest"], entity_ids=[entity], relation_kind=kind)
    return next(item for item in page["items"] if target is None or item["target_id"] == target)


def operation(item):
    kind = item["relation_kind"]
    name, field = {"label": ("attach_existing_label", "label_entity_key"), "class": ("select_semantic_class", "class_id"), "dimension": ("bind_existing_dimension", "dimension_entity_key"), "terminal": ("set_terminal_state", "terminal_state")}[kind]
    return {"operation": name, "entity_key": item["entity_key"], "candidate_id": item["candidate_id"], "policy_id": item["policy_id"], field: item["target_id"]}


def patch(fixture, operations=None, revision=0):
    values = {key: fixture["initialized"][key] for key in ("source_sha256", "snapshot_sha256", "candidates_sha256", "policy_sha256", "ontology_sha256")}
    return {**values, "base_revision": revision, "operations": operations or [operation(candidate(fixture))]}


def commit(fixture, proposed=None, key="patch-1"):
    proposed = proposed or patch(fixture)
    preview = preview_semantic_patch(**fixture["context"], patch=proposed)
    return commit_semantic_patch(**fixture["context"], patch=proposed, preview_hash=preview["preview_hash"], idempotency_key=key)


def compile_revision(fixture, **extra):
    return compile_semantic_revision(**fixture["context"], revision=1, output_dir=fixture["output"], idempotency_key="compile-1", **extra)


def test_full_label_class_dimension_compile_preserves_every_source_field(semantic_fixture):
    f = semantic_fixture
    original_hash = _sha256_path(f["source"] / "source.gpkg")
    operations = [operation(candidate(f)), operation(candidate(f, "class", target="NETWORK_SEGMENT")), operation(candidate(f, "dimension", target="dimension-1"))]
    result = commit(f, patch(f, operations))
    assert result["revision"] == 1
    compiled = compile_revision(f)
    assert compiled["validation"]["valid"]
    assert compiled["validation"]["source_facts_unchanged"] is True
    assert compiled["validation"]["conservation_difference"] == 0
    assert compiled["accepted_run_id"] is None
    assert compiled["delivery_status"] == "candidate_only_not_canonical_delivery"
    assert original_hash == _sha256_path(f["source"] / "source.gpkg")
    with sqlite3.connect(compiled["semantic_gpkg"]) as db:
        assert db.execute("SELECT primary_entity_key,semantic_class,display_label,source_dimension_value FROM semantic_features").fetchone() == ("line-1", "NETWORK_SEGMENT", "管线-Ａ１２", 10.125)
        assert db.execute("SELECT native_length FROM source_lines WHERE entity_key='line-1'").fetchone()[0] == 10.0
        assert db.execute("SELECT terminal_state FROM semantic_entity_ledger WHERE entity_key='unknown-1'").fetchone()[0] == "UNRESOLVED"
    assert compile_revision(f) == compiled
    assert len(list(f["output"].glob("semantic-*"))) == 1


def test_two_writers_same_revision_accept_exactly_one(semantic_fixture):
    f = semantic_fixture
    proposed = patch(f)
    preview = preview_semantic_patch(**f["context"], patch=proposed)
    barrier = Barrier(2)

    def writer(key):
        barrier.wait()
        try:
            return commit_semantic_patch(**f["context"], patch=proposed, preview_hash=preview["preview_hash"], idempotency_key=key)["revision"]
        except SemanticConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, ["writer-a", "writer-b"]))
    assert sorted(map(str, results)) == ["1", "conflict"]
    with sqlite3.connect(f["context"]["semantic_store"]) as db:
        assert db.execute("SELECT COUNT(*) FROM semantic_patches").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM operation_events").fetchone()[0] == 1


def test_idempotency_retry_returns_original_without_event(semantic_fixture):
    f = semantic_fixture
    proposed = patch(f)
    preview = preview_semantic_patch(**f["context"], patch=proposed)
    first = commit(f, proposed)
    second = commit_semantic_patch(**f["context"], patch=proposed, preview_hash=preview["preview_hash"], idempotency_key="patch-1")
    assert first == second
    changed = patch(f, [operation(candidate(f, "class", target="GENERIC_ASSET"))], revision=1)
    changed_preview = preview_semantic_patch(**f["context"], patch=changed)
    with pytest.raises(SemanticConflictError, match="different patch") as conflict:
        commit_semantic_patch(**f["context"], patch=changed, preview_hash=changed_preview["preview_hash"], idempotency_key="patch-1")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    with sqlite3.connect(f["context"]["semantic_store"]) as db:
        assert db.execute("SELECT COUNT(*) FROM operation_events").fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [("coordinates", [4.2, 8.9]), ("native_length", 1.1), ("display_label", "invented"), ("dimension_value", 10.25), ("confidence", .9)])
def test_numeric_or_text_writes_reject_entire_batch(semantic_fixture, field, value):
    f = semantic_fixture
    first = operation(candidate(f))
    second = {**operation(candidate(f, "class", "line-2", "GENERIC_ASSET")), field: value}
    with pytest.raises(SemanticContractError, match="only observed"):
        preview_semantic_patch(**f["context"], patch=patch(f, [first, second]))
    with sqlite3.connect(f["context"]["semantic_store"]) as db:
        assert db.execute("SELECT revision FROM source_bindings").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM entity_decisions").fetchone()[0] == 0


def test_duplicate_label_claim_rolls_back_entire_batch(semantic_fixture):
    f = semantic_fixture
    operations = [operation(candidate(f)), operation(candidate(f, entity="line-2"))]
    with pytest.raises(SemanticConflictError, match="multiple"):
        preview_semantic_patch(**f["context"], patch=patch(f, operations))
    assert commit(f)["revision"] == 1
    with pytest.raises(SemanticConflictError, match="already bound"):
        preview_semantic_patch(**f["context"], patch=patch(f, [operation(candidate(f, entity="line-2"))], revision=1))


def test_query_keyset_bound_to_filters_and_index(semantic_fixture):
    f = semantic_fixture
    manifest = f["context"]["prepare_manifest"]
    page = query_relationship_candidates(prepare_manifest=manifest, relation_kind="class", limit=1)
    all_ids = []
    while True:
        assert page["row_count"] <= 1
        assert page["response_bytes"] <= 65536
        all_ids.extend(i["candidate_id"] for i in page["items"])
        if not page["next_cursor"]:
            break
        with pytest.raises(SemanticContractError, match="cursor"):
            query_relationship_candidates(prepare_manifest=manifest, relation_kind="label", cursor=page["next_cursor"])
        page = query_relationship_candidates(prepare_manifest=manifest, relation_kind="class", limit=1, cursor=page["next_cursor"])
    assert all_ids == sorted(set(all_ids))
    assert not query_relationship_candidates(prepare_manifest=manifest, entity_ids=["line-2"], relation_kind="dimension")["items"]
    with pytest.raises(SemanticContractError, match="unknown source"):
        query_relationship_candidates(prepare_manifest=manifest, entity_ids=["invented"])
    index = Path(manifest).parent / "candidate_index.sqlite3"
    with sqlite3.connect(index) as db:
        query_plan = db.execute("EXPLAIN QUERY PLAN SELECT payload FROM candidates WHERE relation_kind=? AND candidate_id>? ORDER BY candidate_id LIMIT ?", ("label", "", 51)).fetchall()
        assert not any("TEMP B-TREE" in str(row) for row in query_plan)
        assert any("candidate_kind_cursor" in str(row) for row in query_plan)
        db.execute("DELETE FROM candidates WHERE relation_kind='label'")
    with pytest.raises(SemanticContractError, match="digest mismatch"):
        query_relationship_candidates(prepare_manifest=manifest)


def test_preview_is_read_only_and_requires_explicit_initialization(semantic_fixture):
    f = semantic_fixture
    missing = f["tmp_path"] / "missing.sqlite3"
    with pytest.raises(SemanticContractError, match="initialize_semantic_store"):
        preview_semantic_patch(**{**f["context"], "semantic_store": missing}, patch=patch(f))
    assert not missing.exists()
    original = _sha256_path(f["context"]["semantic_store"])
    preview_semantic_patch(**f["context"], patch=patch(f))
    assert _sha256_path(f["context"]["semantic_store"]) == original


@pytest.mark.parametrize("phase", ["after_compile", "after_validate"])
def test_failed_compile_has_no_result_and_retry_is_new_generation(semantic_fixture, phase):
    f = semantic_fixture
    commit(f)

    def fail(stage, context):
        if stage == phase:
            raise RuntimeError("simulated fault")

    with pytest.raises(RuntimeError, match="simulated"):
        compile_revision(f, _fault=fail)
    with sqlite3.connect(f["context"]["semantic_store"]) as db:
        assert db.execute("SELECT state,result_run_id FROM compile_jobs").fetchone() == ("failed", None)
    assert not list(f["output"].glob("semantic-*"))
    result = compile_revision(f, retry_failed=True)
    assert result["generation"] == 2
    assert result["validation"]["source_facts_unchanged"] is True


def test_cancelled_worker_cannot_register_result(semantic_fixture):
    f = semantic_fixture
    commit(f)

    def cancel(stage, context):
        if stage == "after_validate":
            cancel_compile_job(semantic_store=f["context"]["semantic_store"], job_id=context["job_id"])

    with pytest.raises(SemanticConflictError, match="generation") as conflict:
        compile_revision(f, _fault=cancel)
    assert conflict.value.code == "CANCELLED"
    with sqlite3.connect(f["context"]["semantic_store"]) as db:
        assert db.execute("SELECT state,result_run_id FROM compile_jobs").fetchone() == ("cancelled", None)
        assert db.execute("SELECT accepted_run_id FROM source_bindings").fetchone()[0] is None
    assert reconcile_compile_jobs(**f["context"])["jobs"] == []


def test_crash_after_directory_publish_reconciles_without_promotion(semantic_fixture):
    f = semantic_fixture
    commit(f)

    def crash(stage, context):
        if stage == "after_publish":
            raise RuntimeError("crash before result CAS")

    with pytest.raises(RuntimeError, match="result CAS"):
        compile_revision(f, _fault=crash)
    with sqlite3.connect(f["context"]["semantic_store"]) as db:
        assert db.execute("SELECT state,result_run_id FROM compile_jobs").fetchone() == ("validated", None)
    recovery = reconcile_compile_jobs(**f["context"])
    assert recovery["jobs"][0]["state"] == "published"
    result = compile_revision(f)
    assert result["generation"] == 1
    assert result["accepted_run_id"] is None
    assert len(list(f["output"].glob("semantic-*"))) == 1


def test_immutable_directories_and_force_are_rejected(semantic_fixture):
    f = semantic_fixture
    with pytest.raises(SemanticContractError, match="force"):
        prepare_semantics(source_run=f["source"], output_dir=f["tmp_path"] / "new", force=True)
    with pytest.raises(SemanticContractError, match="outside"):
        prepare_semantics(source_run=f["source"], output_dir=f["source"] / "new")
    with pytest.raises(SemanticContractError, match="outside"):
        initialize_semantic_store(**{**f["context"], "semantic_store": f["source"] / "semantic.sqlite3"})


def test_historical_revision_compiles_committed_state(semantic_fixture):
    f = semantic_fixture
    commit(f)
    terminal = candidate(f, "terminal", target="RETAINED_AS_REFERENCE")
    commit(f, patch(f, [operation(terminal)], revision=1), key="patch-2")
    old = compile_revision(f)
    new = compile_semantic_revision(**f["context"], revision=2, output_dir=f["output"], idempotency_key="compile-2")
    assert old["validation"]["feature_count"] == 1
    assert new["validation"]["feature_count"] == 0
    assert new["validation"]["terminal_state_counts"]["RETAINED_AS_REFERENCE"] == 1


def test_compiler_implementation_change_prevents_silent_reuse(semantic_fixture, monkeypatch):
    import cad2gis.cad2gis_v3.semantic_store as store
    f = semantic_fixture
    commit(f)
    original = compile_revision(f)
    assert "semantic_stage.py" in original["compiler_identity"]["implementation_sha256"]
    real_identity = store._compiler_identity
    monkeypatch.setattr(store, "_compiler_identity", lambda: {**real_identity(), "fingerprint": "changed-actual-code-fingerprint"})
    with pytest.raises(SemanticConflictError, match="different payload"):
        compile_revision(f)


def test_inspect_after_cancel_and_read_only_database_guard(semantic_fixture):
    f = semantic_fixture
    commit(f)
    result = compile_revision(f)
    status = inspect_semantic_store(semantic_store=f["context"]["semantic_store"], job_id=result["job_id"])
    assert status["revision"] == 1
    assert status["job"]["state"] == "published"
    assert status["recent_committed_patches"][0]["revision"] == 1
    assert status["pending_outbox_events"] > 0
    assert inspect_semantic_store(semantic_store=f["context"]["semantic_store"], idempotency_key="patch-1")["committed_patch"]["revision"] == 1
    assert inspect_semantic_store(semantic_store=f["context"]["semantic_store"], idempotency_key="compile-1")["job"]["job_id"] == result["job_id"]
    assert inspect_semantic_store(semantic_store=f["context"]["semantic_store"], idempotency_key="not-submitted")["key_found"] is False
    other = f["tmp_path"] / "other.sqlite3"
    with sqlite3.connect(other) as db:
        db.execute("CREATE TABLE source_facts(value)")
    with pytest.raises(SemanticContractError, match="non-semantic"):
        initialize_semantic_store(**{**f["context"], "semantic_store": other})


@pytest.mark.parametrize("mutation", [
    "UPDATE gpkg_geometry_columns SET srs_id=4326 WHERE table_name='source_lines'",
    "UPDATE gpkg_contents SET srs_id=4326 WHERE table_name='source_lines'",
    "UPDATE gpkg_spatial_ref_sys SET definition='tampered CRS definition' WHERE srs_id=-1",
])
def test_geometry_registration_and_crs_metadata_are_precision_facts(semantic_fixture, mutation):
    f = semantic_fixture
    commit(f)
    compiled = compile_revision(f)
    with sqlite3.connect(compiled["semantic_gpkg"]) as db:
        db.execute(mutation)
    validation = validate_semantics(compiled["semantic_gpkg"], source_gpkg=f["source"] / "source.gpkg")
    assert validation["valid"] is False
    assert validation["source_facts_unchanged"] is False
    with pytest.raises(SemanticContractError) as incomplete:
        compile_revision(f)
    assert incomplete.value.code == "PUBLICATION_INCOMPLETE"


def test_candidate_hash_cache_rejects_same_size_restored_mtime_tampering(semantic_fixture):
    f = semantic_fixture
    manifest = f["context"]["prepare_manifest"]
    query_relationship_candidates(prepare_manifest=manifest)
    index = Path(manifest).parent / "candidate_index.sqlite3"
    stat = index.stat()
    db = sqlite3.connect(index)
    try:
        db.execute("UPDATE candidates SET payload=replace(payload,'nearby','forged') WHERE relation_kind='label'")
        db.commit()
    finally:
        db.close()
    assert index.stat().st_size == stat.st_size
    os.utime(index, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(SemanticContractError, match="digest mismatch"):
        query_relationship_candidates(prepare_manifest=manifest)


def test_semantic_errors_have_stable_default_codes():
    assert SemanticContractError("ordinary validation failure").code == "VALIDATION_FAILED"
    assert SemanticConflictError("ordinary cardinality conflict").code == "VALIDATION_FAILED"


@pytest.mark.parametrize("kind,expected", [
    ("wrong_source", "SOURCE_MISMATCH"),
    ("unknown_operation", "UNSUPPORTED_OPERATION"),
    ("unknown_candidate", "UNKNOWN_ID"),
    ("stale_revision", "STALE_REVISION"),
])
def test_patch_error_codes_are_structural(semantic_fixture, kind, expected):
    f = semantic_fixture
    proposed = patch(f)
    if kind == "wrong_source":
        proposed["source_sha256"] = "0" * 64
    elif kind == "unknown_operation":
        proposed["operations"][0]["operation"] = "rewrite_source_geometry"
    elif kind == "unknown_candidate":
        proposed["operations"][0]["candidate_id"] = "0" * 64
    else:
        commit(f, proposed)
    with pytest.raises(SemanticContractError) as rejected:
        preview_semantic_patch(**f["context"], patch=proposed)
    assert rejected.value.code == expected
