"""Real-reader source/SQL/semantic transaction replay, without semantic truth claims."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from cad2gis.cad2gis_v3.source_export import _sha256, export_source
from cad2gis.cad2gis_v3.source_query import query_source_entities, get_entity_context_batch
from cad2gis.cad2gis_v3.semantic_stage import prepare_semantics, query_relationship_candidates, _source_fingerprints
from cad2gis.cad2gis_v3.semantic_store import initialize_semantic_store, preview_semantic_patch, commit_semantic_patch, compile_semantic_revision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, args.output / "replay.py")
    timings = {}
    report = {"status": "RUNNING", "source": str(args.source.resolve()),
              "source_reader_executed": True, "python": platform.python_version(),
              "claim_scope": "actual native-reader and bounded SQL/ID patch replay; GENERIC_ASSET samples exercise protocol only",
              "industry_semantic_accuracy": "not_evaluated_against_human_truth", "absolute_position_accuracy": "not_evaluated",
              "timings_seconds": timings}

    def write(name, value):
        (args.output / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def step(name, call):
        start = time.perf_counter()
        try:
            result = call()
        finally:
            timings[name] = time.perf_counter() - start
        write(name, result)
        print(json.dumps({"stage": name, "seconds": timings[name]}, ensure_ascii=True), flush=True)
        return result

    original_hash = _sha256(args.source)
    try:
        source_run = args.output / "source"
        manifest = step("export_source", lambda: export_source(source=args.source, run_dir=source_run, source_crs=None))
        before_facts = _source_fingerprints(source_run / "source.gpkg")
        before_artifacts = {key: _sha256(Path(value["path"])) for key, value in manifest["artifacts"].items()}
        report["native_reader"] = manifest["reader_protocol"]
        binary = manifest["reader_protocol"].get("libredwg_cli")
        if binary:
            version = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                     timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            report["native_reader_version"] = version.stdout.strip()
        raw = step("source_query", lambda: query_source_entities(run_dir=source_run, limit=50))
        plan = step("plan_query", lambda: query_source_entities(run_dir=source_run, view="plan", limit=50))
        prepared = step("prepare_semantics", lambda: prepare_semantics(source_run=source_run, output_dir=args.output / "prepared"))
        prepared_path = prepared["manifest_path"]
        candidates = step("class_candidates", lambda: query_relationship_candidates(prepare_manifest=prepared_path, relation_kind="class", limit=100))
        chosen = []
        for candidate in candidates["items"]:
            if candidate["target_id"] == "GENERIC_ASSET" and candidate["entity_key"] not in {item["entity_key"] for item in chosen}:
                chosen.append(candidate)
            if len(chosen) == 3:
                break
        if not chosen:
            raise RuntimeError("No geometry-compatible GENERIC_ASSET candidate in the bounded sample")
        keys = [candidate["entity_key"] for candidate in chosen]
        step("selected_context", lambda: get_entity_context_batch(run_dir=source_run, entity_keys=keys,
             fields=["cad_layout", "cad_role", "native_points", "native_length", "curve_facts", "lineage"]))
        labels = step("label_candidates_inspected", lambda: query_relationship_candidates(prepare_manifest=prepared_path, entity_ids=keys, relation_kind="label", limit=20))
        report["label_choice"] = {"nearby_candidates_inspected": len(labels["items"]), "bound": 0,
                                   "reason": "proximity alone is not a verified label binding"}
        store = args.output / "semantic.sqlite3"
        step("initialize_store", lambda: initialize_semantic_store(source_run=source_run, prepare_manifest=prepared_path, semantic_store=store))
        patch = {key: prepared[key] for key in ("source_sha256", "snapshot_sha256", "candidates_sha256", "policy_sha256", "ontology_sha256")}
        patch.update(base_revision=0, operations=[{"operation": "select_semantic_class", "entity_key": candidate["entity_key"],
                     "candidate_id": candidate["candidate_id"], "policy_id": candidate["policy_id"], "class_id": candidate["target_id"]}
                    for candidate in chosen])
        write("patch", patch)
        preview = step("preview_patch", lambda: preview_semantic_patch(source_run=source_run, prepare_manifest=prepared_path, semantic_store=store, patch=patch))
        commit_call = lambda: commit_semantic_patch(source_run=source_run, prepare_manifest=prepared_path,
             semantic_store=store, patch=patch, preview_hash=preview["preview_hash"], idempotency_key="real-dwg-replay-patch-v1")
        committed = step("commit_patch", commit_call)
        replay = step("commit_idempotent_replay", commit_call)
        assert replay == committed
        compiled = step("compile_revision", lambda: compile_semantic_revision(source_run=source_run, prepare_manifest=prepared_path,
             semantic_store=store, revision=committed["revision"], output_dir=args.output / "candidates", idempotency_key="real-dwg-replay-compile-v1"))
        after_facts = _source_fingerprints(Path(compiled["semantic_gpkg"]))
        after_artifacts = {key: _sha256(Path(value["path"])) for key, value in manifest["artifacts"].items()}
        report.update(status="PASS", source_sha256_before=original_hash, source_sha256_after=_sha256(args.source),
                      source_artifact_hashes_before=before_artifacts, source_artifact_hashes_after=after_artifacts,
                      source_table_fingerprints_before=before_facts, compiled_source_table_fingerprints=after_facts,
                      source_tables_exactly_unchanged=before_facts == after_facts,
                      source_artifacts_exactly_unchanged=before_artifacts == after_artifacts,
                      source_count=manifest["entity_count"], plan_count=plan["metadata"]["plan_entity_count"],
                      source_conservation=manifest["conservation"], coordinate_reference=manifest["coordinate_reference"],
                      semantic_validation=compiled["validation"], semantic_revision=committed["revision"],
                      sample_class_assignments=len(chosen), accepted_run_id=compiled["accepted_run_id"],
                      source_query_response_bytes=raw["response_bytes"], plan_query_response_bytes=plan["response_bytes"],
                      semantic_candidate_directory=str(Path(compiled["manifest_path"]).parent))
        assert report["source_sha256_before"] == report["source_sha256_after"]
        assert report["source_tables_exactly_unchanged"] and report["source_artifacts_exactly_unchanged"]
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc(),
                      source_sha256_after=_sha256(args.source), source_sha256_before=original_hash)
        print(report["traceback"], flush=True)
    finally:
        write("report", report)
        print(json.dumps({"status": report["status"], "report": str(args.output / "report.json")}), flush=True)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
