"""Replay reviewed, source-bound canonical conversions using an installed runtime."""
from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def migration(args):
    """Prepare a reviewable source-bound proposal, or apply a reviewed proposal."""
    import cad2gis
    from cad2gis.native_runtime import ensure_osgeo_runtime
    from cad2gis.pipeline import prepare_ai_onboarding, apply_ai_onboarding, convert_project
    from cad2gis.cad2gis_v3.project_profile import _draft_profile
    ensure_osgeo_runtime()
    if args.preparation_from_checkout:
        assert args.prepare_migration and not args.apply_migration, "Checkout mode only assembles review proposals"
    else:
        assert "site-packages" in str(cad2gis.__file__).lower(), "An installed package is required"
    item = next(x for x in read(args.corpus) if x["id"] == args.drawing)
    output = args.output / args.drawing
    project = output / "project"
    if args.prepare_migration:
        previous = (args.previous_output or args.output.parent / "v1") / args.drawing
        old_report = read(previous / "report.json")
        inventory = read(previous / "project/review/source_inventory.json")
        output.mkdir(parents=True, exist_ok=False)
        shutil.copytree(previous / "project", project)
        shutil.copytree(project / "config", output / "previous-reviewed-config")
        profile = read(project / "config/source_profile.json")
        registry = read(project / "config/mapping_registry.json")
        fresh = _draft_profile(inventory)
        # Reopen a NEW copied contract for review. Old reviewed baselines and
        # exact source facts stay unchanged; preserve spatial partitions/rules.
        for key in ("review", "source_binding", "drawing", "crs"):
            profile[key] = fresh[key]
        registry["source_binding"]["inventory_sha256"] = inventory["inventory_sha256"]
        registry["review"] = {"status": "draft"}
        write(project / "config/source_profile.json", profile)
        write(project / "config/mapping_registry.json", registry)
        bundle = prepare_ai_onboarding(project_dir=project)
        write(output / "onboarding-bundle.json", bundle)
        layers, blocks = set(bundle["layers"]), set(bundle["named_blocks"])
        def observed(values, allowed):
            return sorted(set(values) & allowed)
        proposal = {
            "schema_version": "cad2gis.ai_onboarding_proposal.v2", "bundle_sha256": bundle["bundle_sha256"],
            "source_sha256": item["sha256"], "inventory_sha256": inventory["inventory_sha256"],
            "crs_candidate_id": bundle["crs_candidates"][0]["candidate_id"] if len(bundle["crs_candidates"]) == 1 else "",
            "route_layers": sorted(layer for layer in layers if re.search(registry["positive_route_layer_regex"], layer)),
            "block_families": {key: observed(registry.get("block_families", {}).get(key, []), blocks) for key in ("BOITE", "PTECH", "SITE")},
            "insert_layer_families": {key: observed(registry.get("insert_layer_families", {}).get(key, []), layers) for key in ("BOITE", "PTECH", "SITE")},
            "confidence": {"semantics": 0.90, "crs": 0.99},
            "rationale": "Review candidate for this unchanged DWG: select its deterministic DWG_DIRECT:GEODATA CRS candidate; carry forward only observed layer/block identifiers from its exact-SHA reviewed baseline. Preserve original native coordinates and detailed field rules/partitions. CRS confidence describes metadata-backed nominal registration only, not independent positional accuracy. Annotation choices require agreement with the source-bound historical class and layer evidence. Human GCP/visual review remains separate.",
        }
        for key, role in (("homepass_layers", "homepass"), ("span_dimension_layers", "span_dimension"), ("sling_wire_layers", "sling_wire"), ("zpm_boundary_layers", "zpm_boundary")):
            proposal[key] = observed(registry.get("layers", {}).get(role, []), layers)
        selected = []
        for candidate in bundle["annotation_family_candidates"]:
            family = candidate["family"]
            prior_matches = [old for old in registry.get("annotation_families", [])
                             if old.get("target_class") == family.get("target_class")
                             and re.search(old.get("source_layer_pattern", "(?!)"), family.get("source_layer", ""))
                             and old.get("text_pattern") == family.get("text_pattern")]
            if prior_matches:
                selected.append({"candidate_id": candidate["candidate_id"], "policy_id": candidate["policy_ids"][0]})
        proposal["annotation_family_selections"] = selected
        write(output / "proposal-review-candidate.json", proposal)
        selected_ids = {x["candidate_id"] for x in selected}
        review = {"id": args.drawing, "status": "AWAITING_AI_REVIEW", "baseline": old_report["baseline"],
                  "proposal_preparation": {"package_file": cad2gis.__file__, "python": sys.executable,
                                           "mode": "checkout_review_assembly" if args.preparation_from_checkout else "installed_runtime"},
                  "source": item, "project_dir": str(project), "crs_candidates": bundle["crs_candidates"],
                  "proposal": proposal, "selected_annotation_candidates": [c for c in bundle["annotation_family_candidates"] if c["candidate_id"] in selected_ids],
                  "annotation_candidate_count": len(bundle["annotation_family_candidates"]),
                  "previous_config_sha256": {str(p.relative_to(output / "previous-reviewed-config")): sha(p) for p in (output / "previous-reviewed-config").rglob("*") if p.is_file()}}
        write(output / "proposal-review.json", review)
        print(json.dumps({"id": args.drawing, "status": review["status"], "crs_candidates": bundle["crs_candidates"], "annotation_selected": len(selected), "annotation_available": len(bundle["annotation_family_candidates"]), "route_layers": proposal["route_layers"]}), flush=True)
        return 0
    assert args.apply_migration
    proposal = read(output / "proposal-reviewed.json")
    source = next(project.glob("*.dwg"))
    started = time.perf_counter()
    report = {"id": args.drawing, "status": "RUNNING", "source_sha256_before": sha(item["source"]), "claim_scope": "AI-reviewed metadata migration and source-bound canonical candidate; no surveyed absolute accuracy", "python": sys.executable, "package_file": cad2gis.__file__, "package_version": importlib.metadata.version("cad2gis")}
    stage = "apply_ai_onboarding"
    try:
        print(json.dumps({"id": args.drawing, "stage": stage, "status": "STARTED"}), flush=True)
        applied = apply_ai_onboarding(source=source, project_dir=project, proposal=proposal,
                                     proposer={"provider": "codex-host-agent", "model": "onsite-architecture-review"})
        write(output / "onboarding-apply.json", applied)
        report["onboarding"] = applied
        stage = "convert_project"
        print(json.dumps({"id": args.drawing, "stage": stage, "status": "STARTED"}), flush=True)
        result = convert_project(source=source, project_dir=project, run_dir=output / "run", llm="off")
        write(output / "conversion-result.json", dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result)
        run = read(output / "run/run_manifest.json")
        report.update(status="CONVERTED", **{key: run.get(key) for key in ("run_status", "delivery_counts", "validation", "terminal_accounting", "unresolved_count", "source_entity_count", "crs", "delivery_partitions", "delivery_contract_gate", "artifacts")})
    except Exception as exc:
        report.update(status="FAILED", failed_stage=stage, error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    finally:
        report["source_sha256_after"] = sha(item["source"])
        report["source_unchanged"] = report["source_sha256_before"] == report["source_sha256_after"]
        report["elapsed_seconds"] = time.perf_counter() - started
        write(output / "report.json", report)
        print(json.dumps({key: report.get(key) for key in ("id", "status", "failed_stage", "error", "delivery_counts", "elapsed_seconds")}), flush=True)
    return 0 if report["status"] == "CONVERTED" else 1


def replay_project(args):
    """Copy an already reviewed project into a new run; retain native hash gates."""
    import cad2gis
    from cad2gis.native_runtime import ensure_osgeo_runtime
    from cad2gis.pipeline import convert_project

    assert "site-packages" in str(cad2gis.__file__).lower(), "An installed package is required"
    assert args.previous_output is not None and args.drawing
    previous = args.previous_output / args.drawing
    output = args.output / args.drawing
    item = next(x for x in read(args.corpus) if x["id"] == args.drawing)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(previous / "project", output / "project")
    for name in ("proposal-reviewed.json", "ai-review-receipt.json", "onboarding-apply.json"):
        if (previous / name).is_file():
            shutil.copy2(previous / name, output / name)
    project = output / "project"
    source = next(project.glob("*.dwg"))
    report = {
        "id": args.drawing, "status": "RUNNING", "python": sys.executable,
        "package_file": cad2gis.__file__, "package_version": importlib.metadata.version("cad2gis"),
        "source_sha256_before": sha(item["source"]), "previous_project": str(previous / "project"),
        "claim_scope": "Reviewed canonical project replay with current installed runtime; no independently surveyed accuracy",
        "copied_config_sha256": {str(p.relative_to(project / "config")): sha(p)
                                 for p in (project / "config").rglob("*") if p.is_file()},
    }
    started = time.perf_counter()
    try:
        assert sha(source) == item["sha256"] == report["source_sha256_before"]
        report["runtime"] = ensure_osgeo_runtime()
        result = convert_project(source=source, project_dir=project, run_dir=output / "run", llm="off")
        write(output / "conversion-result.json", dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result)
        manifest = read(output / "run/run_manifest.json")
        report.update(status="CONVERTED", **{key: manifest.get(key) for key in (
            "run_status", "delivery_counts", "validation", "terminal_accounting", "unresolved_count",
            "source_entity_count", "crs", "delivery_partitions", "delivery_contract_gate", "artifacts")})
    except Exception as exc:
        report.update(status="FAILED", failed_stage="convert_project", error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc())
    finally:
        report["source_sha256_after"] = sha(item["source"])
        report["source_unchanged"] = report["source_sha256_before"] == report["source_sha256_after"]
        report["elapsed_seconds"] = time.perf_counter() - started
        write(output / "report.json", report)
        print(json.dumps({key: report.get(key) for key in ("id", "status", "error", "delivery_counts", "elapsed_seconds")}), flush=True)
    return 0 if report["status"] == "CONVERTED" else 1


def worker(args):
    import cad2gis
    from cad2gis.native_runtime import ensure_osgeo_runtime
    from cad2gis.pipeline import convert_project, validate_project
    from cad2gis.cad2gis_v3.project_profile import (
        UNSUPPORTED_SCHEMA_VERSION, _reader_protocol_contract, inventory_sha256,
    )

    item = next(x for x in read(args.corpus) if x["id"] == args.drawing)
    output = args.output / item["id"]
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    project.mkdir()
    started = time.perf_counter()
    report = {"status": "RUNNING", "id": item["id"], "source": item["source"],
              "source_sha256_before": sha(item["source"]), "python": sys.executable,
              "package_file": cad2gis.__file__, "package_version": importlib.metadata.version("cad2gis"),
              "claim_scope": "source-bound existing reviewed mapping replay; no independently surveyed absolute accuracy or human semantic truth", "timings_seconds": {}}
    stage = "runtime"
    try:
        assert "site-packages" in str(cad2gis.__file__).lower(), "An installed package is required"
        assert report["source_sha256_before"] == item["sha256"]
        report["runtime"] = ensure_osgeo_runtime()
        stage = "resolve_reviewed_configuration"
        matches = [p for p in args.baselines.rglob("*source_profile.json")
                   if read(p).get("source_binding", {}).get("source_sha256") == item["sha256"]]
        assert len(matches) == 1, f"Expected exactly one reviewed source binding, got {matches}"
        profile_path = matches[0]
        profile = read(profile_path)
        report["baseline"] = profile_path.parent.parent.name
        report["expected_feature_counts"] = profile.get("expectations", {}).get("feature_counts", {})
        report["expected_source_counts"] = profile.get("expectations", {}).get("source_inventory", {})
        shutil.copytree(profile_path.parent, project / "config")
        report["copied_config_sha256"] = {str(p.relative_to(project / "config")): sha(p)
                                         for p in (project / "config").rglob("*") if p.is_file()}
        # The basename is part of the legacy inventory digest. This known baseline
        # name is restored only in a same-byte local copy; the input is untouched.
        source_name = "APD - KLETEK RW 05 SIDOARJO.dwg" if report["baseline"] == "kletek" else Path(item["source"]).name
        local_source = project / source_name
        shutil.copy2(item["source"], local_source)
        assert sha(local_source) == item["sha256"]
        report["conversion_source_copy"] = str(local_source)
        stage = "reconstruct_inventory_sidecars"
        source_run = (args.source_root or args.corpus.parent) / item["id"] / "source"
        manifest_path = source_run / "source_manifest.json"
        assert manifest_path.is_file(), f"Root source export is not complete: {manifest_path}"
        manifest = read(manifest_path)
        inv = read(source_run / "review" / "source_inventory.json")
        original_inventory_hash = inv["inventory_sha256"]
        # Normalize the recorded completeness contract from this exact snapshot.
        # This also supports retained pre-fix exports that dropped diagnostics;
        # current exports already carry the same contract. No entity is reread
        # or edited while constructing the review sidecar.
        inv["reader_protocol"] = _reader_protocol_contract(manifest["reader_protocol"])
        inv["source"]["name"] = source_name
        inv["inventory_sha256"] = inventory_sha256(inv)
        review = project / "review"
        review.mkdir()
        write(review / "source_inventory.json", inv)
        write(review / "unsupported_inventory.json", {
            "schema_version": UNSUPPORTED_SCHEMA_VERSION, "source": dict(inv["source"]),
            "inventory_sha256": inv["inventory_sha256"], **dict(inv["unsupported"]), "review_required": True,
        })
        report["inventory_reconstruction"] = {
            "source_manifest": str(manifest_path), "source_manifest_sha256": sha(manifest_path),
            "export_inventory_sha256": original_inventory_hash,
            "restored_inventory_sha256": inv["inventory_sha256"],
            "reviewed_inventory_sha256": profile["source_binding"]["inventory_sha256"],
            "restored_reader_protocol": inv["reader_protocol"],
            "source_copy_name": source_name,
            "geometry_style_label_facts_modified": False,
            "config_files_modified": False,
        }
        if args.prepare_only:
            report["status"] = "PREPARED"
            return
        stage = "validate_project"
        then = time.perf_counter()
        validation = validate_project(project_dir=project)
        report["timings_seconds"][stage] = time.perf_counter() - then
        report["project_validation"] = validation
        write(output / "project-validation.json", validation)
        if validation.get("conversion_allowed") is not True:
            raise RuntimeError(f"Project validation does not authorize conversion: {validation.get('status')}")
        stage = "convert_project"
        then = time.perf_counter()
        result = convert_project(source=local_source, project_dir=project, run_dir=output / "run", llm="off")
        report["timings_seconds"][stage] = time.perf_counter() - then
        write(output / "conversion-result.json", dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result)
        run = read(output / "run" / "run_manifest.json")
        report.update(status="CONVERTED", run_status=run["run_status"],
                      delivery_counts=run["delivery_counts"], validation=run["validation"],
                      terminal_accounting=run["terminal_accounting"], unresolved_count=run["unresolved_count"],
                      source_entity_count=run["source_entity_count"], crs=run["crs"],
                      delivery_partitions=run.get("delivery_partitions", {}),
                      delivery_contract_gate=run["delivery_contract_gate"],
                      artifacts=run["artifacts"])
        stage = "inspect_geopackage"
        delivery = Path(run["artifacts"]["delivery"]["path"])
        with sqlite3.connect(delivery.as_uri() + "?mode=ro", uri=True) as con:
            report["gpkg_integrity_check"] = con.execute("PRAGMA integrity_check").fetchone()[0]
            layers = con.execute("SELECT table_name,srs_id FROM gpkg_geometry_columns").fetchall()
            report["gpkg_layers"] = [{"table": name, "srs_id": srs,
                                     "rows": con.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]}
                                    for name, srs in layers]
        assert report["gpkg_integrity_check"] == "ok"
    except Exception as exc:
        report.update(status="FAILED", failed_stage=stage, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc())
    finally:
        report["source_sha256_after"] = sha(item["source"])
        report["source_unchanged"] = report["source_sha256_before"] == report["source_sha256_after"]
        report["elapsed_seconds"] = time.perf_counter() - started
        write(output / "report.json", report)
        print(json.dumps({key: report.get(key) for key in ("id", "status", "failed_stage", "error", "run_status", "delivery_counts", "elapsed_seconds")}), flush=True)
    return 0 if report["status"] in {"CONVERTED", "PREPARED"} else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--drawing")
    parser.add_argument("--prepare-migration", action="store_true")
    parser.add_argument("--preparation-from-checkout", action="store_true",
                        help="Allow metadata-only review proposal assembly from checkout; never extraction or conversion.")
    parser.add_argument("--apply-migration", action="store_true")
    parser.add_argument("--replay-project", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--previous-output", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.replay_project:
        return replay_project(args)
    if args.prepare_migration or args.apply_migration:
        assert args.drawing
        return migration(args)
    if args.drawing:
        return worker(args)
    args.output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("CAD2GIS_BACKEND_PATH", None)
    def run(item):
        command = [sys.executable, str(Path(__file__).resolve()), "--corpus", str(args.corpus),
                   "--baselines", str(args.baselines), "--output", str(args.output), "--drawing", item["id"]]
        with (args.output / (item["id"] + ".stdout.log")).open("w", encoding="utf-8") as stdout, (args.output / (item["id"] + ".stderr.log")).open("w", encoding="utf-8") as stderr:
            result = subprocess.run(command, env=env, stdout=stdout, stderr=stderr,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        path = args.output / item["id"] / "report.json"
        report = read(path) if path.exists() else {"id": item["id"], "status": "PROCESS_FAILED", "exit_code": result.returncode}
        print(json.dumps({k: report.get(k) for k in ("id", "status", "run_status", "failed_stage", "error", "delivery_counts", "elapsed_seconds")}), flush=True)
        return report
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(run, read(args.corpus)))
    write(args.output / "report.json", {"reports": reports, "converted": sum(r["status"] == "CONVERTED" for r in reports), "count": len(reports)})
    return 0 if all(r["status"] == "CONVERTED" for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
