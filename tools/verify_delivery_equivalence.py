"""Read-only, exact logical comparison of delivery feature schemas and rows.

Geometry BLOBs, business labels, length metrics and lineage are all included.
Run timestamps, audit files and non-feature GeoPackage metadata are excluded;
this is a regression check, not proof of surveyed positional accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")).hexdigest()


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def snapshot_delivery(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        layers = {}
        for (name,) in connection.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type='features' ORDER BY table_name"
        ):
            table = _identifier(name)
            schema = list(connection.execute(f"PRAGMA table_info({table})"))
            geometry_contract = list(connection.execute(
                "SELECT column_name, geometry_type_name, srs_id, z, m "
                "FROM gpkg_geometry_columns WHERE table_name=? ORDER BY column_name", (name,),
            ))
            columns = [item[1] for item in schema]
            rows = []
            nonempty_labels = diagnostic_labels = unproven_labels = 0
            for row in connection.execute(f"SELECT * FROM {table}"):
                # Include IDs and exact binary geometries. Sorting the row
                # digests ignores SQLite's incidental physical storage order.
                rows.append(_digest([
                    {"blob_hex": value.hex()} if isinstance(value, bytes) else value
                    for value in row
                ]))
                values = dict(zip(columns, row))
                label = str(values.get("display_label") or "")
                provenance = str(values.get("label_provenance") or "")
                nonempty_labels += bool(label)
                diagnostic_labels += "CAD geometry" in label or "no DIMENSION" in label
                unproven_labels += bool(label) and provenance in {"", "UNAVAILABLE"}
            layers[name] = {
                "schema_sha256": _digest([schema, geometry_contract]),
                "rows_sha256": _digest(sorted(rows)),
                "row_count": len(rows),
                "nonempty_business_labels": nonempty_labels,
                "diagnostic_labels": diagnostic_labels,
                "unproven_business_labels": unproven_labels,
            }
        return layers
    finally:
        connection.close()


def compare_deliveries(baseline, candidate):
    left, right = snapshot_delivery(baseline), snapshot_delivery(candidate)
    differences = [
        name for name in sorted(left.keys() | right.keys())
        if left.get(name) != right.get(name)
    ]
    return {
        "schema_version": "cad2gis.delivery_equivalence.v1",
        "equivalent": not differences,
        "different_layers": differences,
        "comparison": "exact_feature_schemas_and_rows_including_geometry_labels_lengths_lineage",
        "limitation": "Does not establish ground-truth accuracy or external metadata equivalence",
        "baseline": left, "candidate": right,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = compare_deliveries(args.baseline, args.candidate)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
