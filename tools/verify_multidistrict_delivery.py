"""Audit each manifest-declared district against its native source snapshot.

Runs must be under <runs>/<drawing-id>/run and complete native snapshots under
<sources>/<drawing-id>/source. Missing declared districts are errors, not skips.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sqlite3

from cad2gis.delivery_scopes import delivery_scopes
from cad2gis.native_runtime import ensure_osgeo_runtime
from cad2gis.visual_audit import audit


def verify(runs: Path, sources: Path, output: Path, drawings: list[str]) -> dict:
    ensure_osgeo_runtime()
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for drawing in drawings:
        if Path(drawing).name != drawing or drawing in (".", ".."):
            raise ValueError("Drawing ID must be a directory basename")
        run = (runs / drawing / "run").resolve()
        catalog = delivery_scopes(run)
        if not catalog["scopes"]:
            raise ValueError(f"No delivery manifest found: {drawing}")
        for scope in catalog["scopes"]:
            if not scope["available"]:
                raise ValueError(f"Unavailable district {drawing}/{scope['region_id']}: {scope['error']}")
            region = scope["region_id"]
            gpkg = Path(scope["run_dir"]) / "delivery.gpkg"
            with sqlite3.connect(gpkg.as_uri() + "?mode=ro", uri=True) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError(f"GeoPackage integrity failure: {drawing}/{region}")
                actual = {}
                for name, in db.execute("SELECT table_name FROM gpkg_geometry_columns"):
                    quoted = '"' + name.replace('"', '""') + '"'
                    actual[name] = db.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                if actual != scope["delivery_counts"]:
                    raise ValueError(f"District counts disagree: {drawing}/{region}: {actual}")
            destination = output / drawing / region
            destination.mkdir(parents=True)
            with (destination / "audit.log").open("w", encoding="utf-8") as log, redirect_stdout(log):
                audit(sources / drawing / "source", run, destination, partition=None if region == "MAIN" else region)
            report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
            result = {"drawing": drawing, "district": region, "delivery_counts": actual,
                      "run_status": scope["run_status"], "report": str(destination / "report.json"),
                      "unexplained_geometry_changes": len(report["unexplained_geometry_changes"]),
                      "unexpected_field_value_mismatches": report["unexpected_field_value_mismatches"],
                      "labels_without_verified_source_text": report["labels_without_verified_direct_or_selected_text"],
                      "geometry_review_required_count": report["geometry_review_required_count"],
                      "immutable_artifacts_unchanged": report.get("immutable_artifacts_unchanged"),
                      "absolute_accuracy_verified": False}
            results.append(result)
            print(json.dumps(result), flush=True)
    summary = {"schema_version": "cad2gis.multidistrict-verification.v1", "drawing_count": len(drawings),
               "district_count": len(results), "districts": results, "absolute_accuracy_verified": False}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runs", "sources", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--drawing", action="append", required=True)
    args = parser.parse_args()
    summary = verify(args.runs, args.sources, args.output, args.drawing)
    return int(any(r["unexplained_geometry_changes"] or r["unexpected_field_value_mismatches"]
                   or r["labels_without_verified_source_text"] or not r["immutable_artifacts_unchanged"]
                   for r in summary["districts"]))


if __name__ == "__main__":
    raise SystemExit(main())
