"""Replay a local DWG corpus with the installed package, preserving every failure."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def worker(source: Path, output: Path, bootstrap: bool = False):
    import cad2gis
    from cad2gis.native_runtime import ensure_osgeo_runtime
    ensure_osgeo_runtime()
    from cad2gis import pipeline
    output.mkdir(parents=True, exist_ok=False)
    report = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "package_version": importlib.metadata.version("cad2gis"), "package_path": cad2gis.__file__,
              "stages": {}, "status": "RUNNING"}
    stages = [("source", lambda: pipeline.export_source(source=source, run_dir=output / "source"))]
    if bootstrap:
        stages.extend([
            ("bootstrap", lambda: pipeline.bootstrap_project(source=source, project_dir=output / "project")),
            ("onboarding_bundle", lambda: pipeline.prepare_ai_onboarding(project_dir=output / "project")),
        ])
    for name, call in stages:
        start = time.perf_counter()
        try:
            value = call()
            write(output / f"{name}.json", value)
            report["stages"][name] = {"status": "PASS", "seconds": time.perf_counter() - start}
        except Exception as exc:
            report["stages"][name] = {"status": "FAIL", "seconds": time.perf_counter() - start,
                                     "error": str(exc), "type": type(exc).__name__, "traceback": traceback.format_exc()}
        write(output / "inspection_report.json", report)
        print(json.dumps({"drawing": source.name, "stage": name, **report["stages"][name]}, ensure_ascii=True), flush=True)
    report["source_sha256_after"] = hashlib.sha256(source.read_bytes()).hexdigest()
    report["status"] = "PASS" if all(s["status"] == "PASS" for s in report["stages"].values()) else "FAIL"
    write(output / "inspection_report.json", report)
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--bootstrap", action="store_true", help="Also build a draft and onboarding bundle; omit for already source-bound baselines.")
    args = parser.parse_args()
    if args.worker:
        return worker(args.input.resolve(), args.output.resolve(), args.bootstrap)
    args.output.mkdir(parents=True, exist_ok=False)
    sources = sorted(args.input.glob("*.dwg"))
    manifest = [{"id": f"drawing-{i:02d}", "source": str(s.resolve()), "bytes": s.stat().st_size,
                 "sha256": hashlib.sha256(s.read_bytes()).hexdigest()} for i, s in enumerate(sources, 1)]
    write(args.output / "corpus.json", manifest)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    def launch(item):
        with (args.output / (item["id"] + ".log")).open("w", encoding="utf-8") as log:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--input", item["source"],
                                      "--output", str((args.output / item["id"]).resolve()), *(["--bootstrap"] if args.bootstrap else [])], env=environment,
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        print(json.dumps({"id": item["id"], "exit_code": process.returncode}), flush=True)
        return {"id": item["id"], "exit_code": process.returncode}
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(launch, manifest))
    failed = [item for item in results if item["exit_code"] != 0]
    write(args.output / "batch_report.json", {"status": "FAIL" if failed else "PASS", "results": results})
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
