"""Reproducible synthetic source-export/query scale simulation (no DWG reader)."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from cad2gis.cad2gis_v3.source_export import export_source
from cad2gis.cad2gis_v3.source_query import (
    SourceQueryError, build_source_index, get_entity_context_batch, query_source_entities,
    validate_source_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100000)
    parser.add_argument("--reuse-snapshot", action="store_true", help="Validate and reuse an existing export; rebuild only its derived index")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / "synthetic.dwg"
    root = args.output / "snapshot"
    if args.reuse_snapshot:
        _, manifest = validate_source_snapshot(root)
        assert manifest["entity_count"] == args.count
        previous = json.loads((args.output / "results.json").read_text(encoding="utf-8"))
        export_seconds = previous["export_seconds"]
    else:
        source.write_bytes(f"CAD2GIS synthetic reader injection, count={args.count}".encode())
        records = [{"entity_key": f"E{i:09d}", "handle": str(i), "source_file": source.name,
                "dwg_type_name": "POINT", "layout": "Sheet1", "layout_role": "paper",
                "cad_role": "documentation", "layer": f"L{i%20:02}",
                "points": [[100000000.0 + i * 0.125, float(i % 1000)]],
                "text": f"光缆-{i:09d}" if i % 10 == 0 else "",
                "raw_properties": {"extraction_backend": "synthetic_injection"}}
                   for i in range(args.count)]
        start = time.perf_counter()
        manifest = export_source(source=source, run_dir=root, records=records)
        export_seconds = time.perf_counter() - start
    print(json.dumps({"phase": "exported", "seconds": export_seconds, "count": args.count}), flush=True)
    start = time.perf_counter()
    index = build_source_index(root, rebuild=args.reuse_snapshot)
    index_seconds = time.perf_counter() - start
    print(json.dumps({"phase": "index_ready", "seconds": index_seconds}), flush=True)
    timings = []
    page = None
    # Seek every 50 rows, including a page in the middle and the final page.
    cursor = None
    pages = 0
    max_response = 0
    returned = 0
    while True:
        start = time.perf_counter()
        page = query_source_entities(run_dir=root, limit=50, cursor=cursor,
                                     projection=["entity_key", "dwg_layer", "native_centroid"])
        timings.append((time.perf_counter() - start) * 1000)
        max_response = max(max_response, page["response_bytes"])
        returned += len(page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert returned == args.count
    labels = query_source_entities(run_dir=root, text_query="光缆", limit=50)
    context = get_entity_context_batch(run_dir=root, entity_keys=[item["entity_key"] for item in labels["items"]],
                                       fields=["text", "native_points", "raw_properties"])
    assert context["response_bytes"] <= 65536
    timed_out = False
    try:
        query_source_entities(run_dir=root, text_query="☃", timeout_ms=1)
    except SourceQueryError as exc:
        timed_out = "timed out" in str(exc)
    result = {"simulation_kind": "injected synthetic CAD facts; real export, GeoPackage and SQLite query code",
              "source_reader_executed": False, "absolute_position_accuracy": "not_evaluated",
              "export_reused_for_final_query_verification": args.reuse_snapshot,
              "entity_count": args.count, "snapshot_sha256": manifest["snapshot_sha256"],
              "source_provenance": manifest["reader_provenance"]["mode"],
              "export_seconds": export_seconds, "cold_index_seconds": index_seconds,
              "index_bytes": Path(index["path"]).stat().st_size, "pages": pages,
              "returned_entities": returned, "max_page_response_bytes": max_response,
              "warm_p50_ms": statistics.median(timings),
              "warm_p95_ms": sorted(timings)[int((len(timings)-1)*0.95)],
              "warm_max_ms": max(timings), "last_page_ms": timings[-1],
              "chinese_two_character_matches_in_first_page": len(labels["items"]),
              "context_response_bytes": context["response_bytes"], "timeout_exercised": timed_out}
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
