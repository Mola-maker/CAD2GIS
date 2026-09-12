"""Verify supplied DWGs against unchanged, SHA-bound project configurations.

Each drawing runs in its own process. No approval flags, accuracy thresholds,
source files or baseline configurations are changed by this verifier.
"""
from __future__ import annotations

import argparse
import cProfile
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def worker(args):
    import cad2gis
    from cad2gis import pipeline
    from cad2gis.native_runtime import ensure_osgeo_runtime
    from cad2gis.cad2gis_v3.project_profile import UNSUPPORTED_SCHEMA_VERSION, inspect_source
    from cad2gis.cad2gis_v3.source_replay import load_native_snapshot

    source = args.source.resolve()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"id": args.drawing, "status": "RUNNING", "source": str(source),
              "source_sha256_before": sha(source), "source_size_bytes": source.stat().st_size,
              "package_version": cad2gis.__version__, "package_path": cad2gis.__file__,
              "python": sys.executable, "matching_requested": args.matching,
              "absolute_accuracy_verified": False, "stages": {}}
    stage = "runtime"
    started = time.perf_counter()
    profiler = cProfile.Profile() if args.profile_worker else None
    if profiler:
        profiler.enable()
    try:
        report["runtime"] = ensure_osgeo_runtime()
        # The reviewed baseline slug participates in source-bound SF rules.
        # Preserve it in the project path as well as in the corpus manifest.
        project = root
        shutil.copytree(args.baselines / args.drawing / "config", project / "config")
        profile = read(project / "config/source_profile.json")
        assert profile["source_binding"]["source_sha256"] == report["source_sha256_before"]
        assert profile["source_binding"]["source_size_bytes"] == report["source_size_bytes"]
        name = args.source_name or source.name
        if Path(name).name != name or not name.lower().endswith(".dwg"):
            raise ValueError("Binding source name must be a DWG basename")
        local_source = project / name
        shutil.copy2(source, local_source)
        report["binding_source_name"] = name
        report["configuration_sha256"] = {p.name: sha(p) for p in (project / "config").glob("*.json")}
        selected = pipeline.resolve_project_configuration(
            project_dir=project, discover_gcp=args.matching == "nominal",
            discover_match=args.matching == "osm",
        ).match_profile
        report["matching_profile_selected"] = (
            {"path": str(selected), "sha256": sha(selected)} if selected is not None else None
        )
        if selected is not None and args.osm_data_dir:
            raise ValueError("Project pins a frozen match profile; omit --osm-data-dir when replaying reviewed baselines")
        stage = "export_source"
        print(f"START {stage}: {args.drawing}", flush=True)
        if args.source_snapshots:
            snapshots = read(args.source_snapshots)
            if not isinstance(snapshots, dict) or not isinstance(snapshots.get(args.drawing), str):
                raise ValueError(f"No source snapshot path for {args.drawing}")
            reused = Path(snapshots[args.drawing]).expanduser().resolve()
            if not (reused / "source_manifest.json").is_file():
                raise ValueError(f"Missing declared native source snapshot: {reused}")
        else:
            reused = args.reuse_exports / args.drawing / "source" if args.reuse_exports else None
        source_run = reused if reused and (reused / "source_manifest.json").is_file() else root / "source"
        if source_run == reused:
            load_native_snapshot(source_run, local_source)
            snapshot = read(source_run / "source_manifest.json")
        else:
            snapshot = pipeline.export_source(source=local_source, run_dir=source_run)
        report["source_snapshot_path"] = str(source_run)
        report["stages"][stage] = "PASS"
        report["source_entity_count"] = snapshot.get("entity_count")
        report["source_conservation"] = snapshot.get("conservation")
        shutil.copytree(source_run / "review", project / "review")
        # Inventories also bind the project filename. A byte-identical snapshot
        # can have been extracted under the supplier's longer DWG filename.
        # Rebuild from verified native records instead of editing its digest.
        records, _ = load_native_snapshot(source_run, local_source)
        inventory = inspect_source(source=local_source, records=records)
        write(project / "review/source_inventory.json", inventory)
        del records
        write(project / "review/unsupported_inventory.json", {
            "schema_version": UNSUPPORTED_SCHEMA_VERSION, "source": inventory["source"],
            "inventory_sha256": inventory["inventory_sha256"],
            **inventory["unsupported"], "review_required": True,
        })
        stage = "validate_project"
        print(f"START {stage}: {args.drawing}", flush=True)
        validation = pipeline.validate_project(project_dir=project)
        write(root / "project-validation.json", validation)
        report["project_validation"] = validation
        if validation.get("conversion_allowed") is not True:
            raise ValueError("Existing project binding does not authorize conversion; inspect project-validation.json")
        report["stages"][stage] = "PASS"
        stage = "convert_project"
        print(f"START {stage}: {args.drawing}", flush=True)
        # A long native conversion should leave useful diagnostic evidence.
        import faulthandler
        if args.trace_workers:
            faulthandler.dump_traceback_later(120, repeat=True)
        osm_data = args.osm_data_dir / (args.drawing + ".json") if args.osm_data_dir else None
        if osm_data is not None and not osm_data.is_file():
            raise ValueError(f"Missing frozen OSM snapshot: {osm_data}")
        result = pipeline.convert_project(source=local_source, project_dir=project,
                    run_dir=root / "run", source_run=source_run, llm="off",
                    matching=args.matching, osm_data=osm_data)
        write(root / "conversion-result.json", dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result)
        manifest = read(root / "run/run_manifest.json")
        if selected is not None:
            applied = manifest.get("profiles", {}).get("match_profile", {})
            if applied.get("sha256") != sha(selected):
                raise ValueError("Conversion did not preserve the selected frozen match profile")
        report["osm_matching"] = {key: (manifest.get("osm_matching") or {}).get(key)
                                  for key in ("status", "revision", "match_sha256", "needs_review")}
        report["stages"][stage] = "PASS"
        report.update({key: manifest.get(key) for key in (
            "run_status", "delivery_counts", "validation", "terminal_accounting", "unresolved_count",
            "source_entity_count", "crs", "matching", "delivery_contract_gate", "implementation_sha256")})
        stage = "artifact_integrity"
        print(f"START {stage}: {args.drawing}", flush=True)
        gpkg = root / "run/delivery.gpkg"
        with sqlite3.connect(gpkg.as_uri() + "?mode=ro", uri=True) as db:
            report["gpkg_integrity_check"] = db.execute("PRAGMA integrity_check").fetchone()[0]
            assert report["gpkg_integrity_check"] == "ok"
        report["delivery_sha256"] = sha(gpkg)
        for entry in manifest.get("artifacts", {}).values():
            if isinstance(entry, dict) and entry.get("path") and entry.get("sha256"):
                assert sha(entry["path"]) == entry["sha256"], entry["path"]
        report["stages"][stage] = "PASS"
        stage = "source_delivery_audit"
        print(f"START {stage}: {args.drawing}", flush=True)
        from cad2gis.visual_audit import audit
        audit(source_run, root / "run", root / "audit")
        audit_report = read(root / "audit/report.json")
        report["audit_summary"] = {key: audit_report.get(key) for key in (
            "geometry_status_counts", "unexpected_field_value_mismatches",
            "explicit_relocation_count", "derived_without_source_count",
            "cable_max_hausdorff_native_m", "cable_max_length_difference_native_m",
            "immutable_artifacts_unchanged", "source_artifacts_unchanged",
            "transform_numeric_roundtrip", "labels_without_verified_direct_or_selected_text",
            "labels_with_verified_heuristic_relation_requiring_review",
        )}
        report["unexplained_geometry_change_count"] = len(audit_report.get("unexplained_geometry_changes", []))
        report["stages"][stage] = "COMPLETED"
        report["status"] = "CONVERTED"
    except Exception as exc:
        report.update(status="FAILED", failed_stage=stage, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc())
        if stage == "validate_project" and isinstance(exc, ValueError):
            report.update(status="BLOCKED", requires_project_review=True)
    finally:
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
        if profiler:
            profiler.disable()
            profiler.dump_stats(root / "worker.prof")
        report["configuration_sha256_after"] = {
            p.name: sha(p) for p in (root / "config").glob("*.json")
        }
        report["source_sha256_after"] = sha(source)
        report["conversion_completed"] = report["stages"].get("convert_project") == "PASS"
        report["source_unchanged"] = report["source_sha256_before"] == report["source_sha256_after"]
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        write(root / "report.json", report)
        print(json.dumps({k: report.get(k) for k in ("id", "status", "failed_stage", "error", "run_status", "delivery_counts", "elapsed_seconds")}), flush=True)
    return 0 if report["status"] == "CONVERTED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-names", type=Path, help="JSON baseline-id to original binding basename map")
    snapshots = parser.add_mutually_exclusive_group()
    snapshots.add_argument("--reuse-exports", type=Path, help="Reuse existing snapshots after full hash/reader identity validation")
    snapshots.add_argument("--source-snapshots", type=Path, help="JSON baseline-id to existing native snapshot path map; missing entries fail closed")
    parser.add_argument("--matching", choices=("osm", "nominal"), default="osm")
    parser.add_argument("--osm-data-dir", type=Path, help="Frozen OSM snapshots named <baseline-id>.json; missing files fail closed")
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent drawings; large source graphs can need substantial memory")
    parser.add_argument("--drawing-id", action="append", help="Verify only these baseline IDs (repeatable)")
    parser.add_argument("--profile-worker", action="store_true", help="Record a cProfile file per drawing")
    parser.add_argument("--trace-workers", action="store_true", help="Print a worker traceback every 120 seconds")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-name")
    parser.add_argument("--drawing")
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    if not args.input or args.jobs < 1:
        parser.error("--input and a positive --jobs are required")
    args.output.mkdir(parents=True, exist_ok=False)
    profiles = {}
    for path in args.baselines.glob("*/config/source_profile.json"):
        profiles.setdefault(read(path)["source_binding"]["source_sha256"], []).append(path.parent.parent.name)
    names = read(args.source_names) if args.source_names else {}
    corpus = []
    for source in sorted(args.input.rglob("*")):
        if not source.is_file() or source.suffix.lower() != ".dwg":
            continue
        digest = sha(source)
        matches = profiles.get(digest, [])
        if len(matches) != 1:
            raise ValueError(f"Expected one source-bound baseline for {source.name}; got {matches}")
        if args.drawing_id and matches[0] not in args.drawing_id:
            continue
        corpus.append({"id": matches[0], "source": str(source.resolve()), "sha256": digest,
                       "source_name": names.get(matches[0], source.name)})
    if not corpus:
        raise ValueError("No DWG inputs found")
    write(args.output / "corpus.json", corpus)
    def launch(item):
        cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--source", item["source"],
               "--source-name", item["source_name"], "--drawing", item["id"], "--baselines", str(args.baselines.resolve()),
               "--output", str((args.output / item["id"]).resolve()), "--matching", args.matching]
        if args.reuse_exports:
            cmd.extend(["--reuse-exports", str(args.reuse_exports.resolve())])
        if args.source_snapshots:
            cmd.extend(["--source-snapshots", str(args.source_snapshots.resolve())])
        if args.osm_data_dir:
            cmd.extend(["--osm-data-dir", str(args.osm_data_dir.resolve())])
        if args.profile_worker:
            cmd.append("--profile-worker")
        if args.trace_workers:
            cmd.append("--trace-workers")
        with (args.output / f"{item['id']}.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(cmd, env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT)
        path = args.output / item["id"] / "report.json"
        report = read(path) if path.exists() else {"id": item["id"], "status": "PROCESS_FAILED", "exit_code": result.returncode}
        if not path.exists():
            write(path, report)
        print(json.dumps({k: report.get(k) for k in ("id", "status", "exit_code", "failed_stage", "error", "run_status", "delivery_counts")}), flush=True)
        return report
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        reports = list(pool.map(launch, corpus))
    success = all(r["status"] == "CONVERTED" and r["source_unchanged"] for r in reports)
    write(args.output / "summary.json", {"status": "PASS" if success else "FAIL", "drawing_count": len(reports),
          "converted": sum(r.get("conversion_completed", False) for r in reports),
          "blocked": sum(r["status"] == "BLOCKED" for r in reports),
          "audited": sum(r["status"] == "CONVERTED" for r in reports),
          "absolute_accuracy_verified": False, "reports": reports})
    from cad2gis.batch import report_runs, write_index
    completed = [args.output / r["id"] / "run" for r in reports
                 if (args.output / r["id"] / "run/run_manifest.json").is_file()]
    review = args.output / "review"
    if completed:
        batch_report = report_runs(completed, review)
    else:
        review.mkdir()
        batch_report = {"schema_version": "cad2gis.batch-report.v1", "drawings": []}
    for report in reports:
        if not (args.output / report["id"] / "run/run_manifest.json").is_file():
            batch_report["drawings"].append({"id": report["id"], "status": report["status"],
                "error": report.get("error", "Conversion did not publish a run"), "links": {}})
    batch_report["status"] = "COMPLETED" if success else "FAILED"
    write(review / "batch-report.json", batch_report)
    write_index(review, batch_report)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
