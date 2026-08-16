#!/usr/bin/env python3
"""Deterministic comparison of two immutable CAD2GIS run directories.

Use this to answer "why does the GCP-registered delivery lose PTECH/CABLE
rows?" without trusting a web preview (the preview always renders the
pre-registration run).

The tool compares:

* run manifest modes, source hash, delivery counts, entity counts and
  spatial-denoising summary;
* per-layer GeoPackage feature counts;
* per-layer ``source_entity_key + source_handle`` sets, reporting exactly
  which features are missing from / extra in run B.

Exit status: 0 = identical feature inventories, 1 = differences found,
2 = invocation/read error.

Examples:

    python tools/diagnostics/compare_runs.py \\
        baselines/hutabohu/run \\
        baselines/hutabohu/run.review/registered-run

    python tools/diagnostics/compare_runs.py --json A B
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_LAYER_ORDER = (
    "BOITE", "CABLE", "CABLE_SEGMENT", "PTECH", "INFRASTRUCTURE",
    "SITE", "ZNRO", "ZPM", "IMB",
)
_IDENTITY_FIELDS = ("source_entity_key", "source_handle")


def _read_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Run manifest does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Run manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Run manifest root must be an object: {path}")
    return payload


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    modes = manifest.get("modes")
    legend = manifest.get("legend_spatial") or {}
    semantics = manifest.get("semantics") or {}
    return {
        "pipeline": manifest.get("pipeline"),
        "run_status": manifest.get("run_status"),
        "modes": dict(modes) if isinstance(modes, dict) else modes,
        "source_sha256": (
            (manifest.get("source") or {}).get("sha256")
            if isinstance(manifest.get("source"), dict) else None
        ),
        "source_entity_count": manifest.get("source_entity_count"),
        "delivery_counts": dict(manifest.get("delivery_counts") or {}),
        "unresolved_count": manifest.get("unresolved_count"),
        "legend_spatial": {
            key: legend.get(key)
            for key in (
                "llm_mode", "status", "flagged_count",
                "auto_excluded_count", "cached_regions_count",
            )
            if key in legend
        },
        "semantic_coverage": {
            key: semantics.get(key)
            for key in ("status", "passed", "conversion_allowed", "counts")
            if key in semantics
        },
        "osm_anchor_status": (
            (manifest.get("osm_anchor") or {}).get("status")
            if isinstance(manifest.get("osm_anchor"), dict) else None
        ),
    }


def _delivery_tables(delivery_path: Path) -> list[str]:
    if not delivery_path.is_file():
        raise FileNotFoundError(f"Delivery GeoPackage does not exist: {delivery_path}")
    connection = sqlite3.connect(str(delivery_path))
    try:
        rows = connection.execute(
            """
            SELECT table_name
            FROM gpkg_contents
            WHERE data_type = 'features'
            ORDER BY table_name
            """
        ).fetchall()
        tables = [str(row[0]) for row in rows]
        if not tables:
            raise ValueError(f"No feature tables found in {delivery_path}")
        return tables
    finally:
        connection.close()


def _feature_inventory(
    delivery_path: Path,
    tables: list[str],
) -> dict[str, dict[str, Any]]:
    """Return per-layer counts and identity sets from a delivery GeoPackage."""
    connection = sqlite3.connect(str(delivery_path))
    try:
        inventory: dict[str, dict[str, Any]] = {}
        for table in tables:
            columns = [
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            column_set = set(columns)
            identity_fields = tuple(
                field for field in _IDENTITY_FIELDS if field in column_set
            )
            rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
            count = len(rows)
            identities = set()
            for row in rows:
                values = tuple(row[columns.index(field)] for field in identity_fields)
                identities.add(values)
            inventory[table] = {
                "count": count,
                "identity_fields": identity_fields,
                "identity_set": identities,
            }
        return inventory
    finally:
        connection.close()


def _diff_inventories(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for layer in _LAYER_ORDER:
        left = baseline.get(layer)
        right = candidate.get(layer)
        if left is None and right is None:
            continue
        if left is None or right is None:
            differences.append({
                "kind": "layer_presence",
                "layer": layer,
                "baseline_count": left.get("count") if left else None,
                "candidate_count": right.get("count") if right else None,
            })
            continue
        left_keys = left["identity_set"]
        right_keys = right["identity_set"]
        missing_in_candidate = sorted(left_keys - right_keys)
        extra_in_candidate = sorted(right_keys - left_keys)
        if (
            left["count"] == right["count"]
            and not missing_in_candidate
            and not extra_in_candidate
        ):
            continue
        differences.append({
            "kind": "layer_feature_inventory",
            "layer": layer,
            "baseline_count": left["count"],
            "candidate_count": right["count"],
            "identity_fields": list(left["identity_fields"]),
            "missing_in_candidate_count": len(missing_in_candidate),
            "missing_in_candidate": [
                dict(zip(left["identity_fields"], values))
                for values in missing_in_candidate[:20]
            ],
            "extra_in_candidate_count": len(extra_in_candidate),
            "extra_in_candidate": [
                dict(zip(left["identity_fields"], values))
                for values in extra_in_candidate[:20]
            ],
        })
    # Tables present in either delivery but outside the canonical layer order.
    extra_tables = set(candidate) - set(baseline) - set(_LAYER_ORDER)
    missing_tables = set(baseline) - set(candidate) - set(_LAYER_ORDER)
    if extra_tables or missing_tables:
        differences.append({
            "kind": "non_canonical_tables",
            "missing_in_candidate": sorted(missing_tables),
            "extra_in_candidate": sorted(extra_tables),
        })
    return differences


def compare_runs(
    baseline_dir: Path,
    candidate_dir: Path,
) -> dict[str, Any]:
    baseline_manifest = _read_manifest(baseline_dir)
    candidate_manifest = _read_manifest(candidate_dir)
    baseline_summary = _manifest_summary(baseline_manifest)
    candidate_summary = _manifest_summary(candidate_manifest)

    manifest_differences = []
    for key in ("pipeline", "run_status", "modes", "source_sha256"):
        if baseline_summary.get(key) != candidate_summary.get(key):
            manifest_differences.append({
                "field": key,
                "baseline": baseline_summary.get(key),
                "candidate": candidate_summary.get(key),
            })
    for key in ("source_entity_count", "unresolved_count"):
        if baseline_summary.get(key) != candidate_summary.get(key):
            manifest_differences.append({
                "field": key,
                "baseline": baseline_summary.get(key),
                "candidate": candidate_summary.get(key),
            })
    for key in ("legend_spatial", "semantic_coverage"):
        if baseline_summary.get(key) != candidate_summary.get(key):
            manifest_differences.append({
                "field": key,
                "baseline": baseline_summary.get(key),
                "candidate": candidate_summary.get(key),
            })
    for key in ("llm_mode", "status", "flagged_count", "auto_excluded_count"):
        left = baseline_summary["legend_spatial"].get(key)
        right = candidate_summary["legend_spatial"].get(key)
        if left != right:
            manifest_differences.append({
                "field": f"legend_spatial.{key}",
                "baseline": left,
                "candidate": right,
            })

    baseline_delivery = baseline_dir / "delivery.gpkg"
    candidate_delivery = candidate_dir / "delivery.gpkg"
    tables = _delivery_tables(baseline_delivery)
    candidate_tables = _delivery_tables(candidate_delivery)
    all_tables = sorted(set(tables) | set(candidate_tables), key=lambda name: (
        _LAYER_ORDER.index(name) if name in _LAYER_ORDER else len(_LAYER_ORDER),
        name,
    ))
    baseline_inventory = _feature_inventory(baseline_delivery, all_tables)
    candidate_inventory = _feature_inventory(candidate_delivery, all_tables)
    layer_differences = _diff_inventories(baseline_inventory, candidate_inventory)

    return {
        "baseline_run_dir": str(baseline_dir.resolve()),
        "candidate_run_dir": str(candidate_dir.resolve()),
        "baseline_manifest": baseline_summary,
        "candidate_manifest": candidate_summary,
        "manifest_differences": manifest_differences,
        "layer_differences": layer_differences,
        "identical": not manifest_differences and not layer_differences,
    }


def _print_human(result: dict[str, Any]) -> None:
    print(f"baseline: {result['baseline_run_dir']}")
    print(f"candidate: {result['candidate_run_dir']}")
    print()
    for side in ("baseline", "candidate"):
        summary = result[f"{side}_manifest"]
        modes = summary.get("modes")
        print(
            f"{side}: pipeline={summary.get('pipeline')} "
            f"run_status={summary.get('run_status')} modes={modes} "
            f"source_sha256={summary.get('source_sha256', '')[:16]}"
        )
        print(
            f"  source_entities={summary.get('source_entity_count')} "
            f"unresolved={summary.get('unresolved_count')} "
            f"delivery_counts={summary.get('delivery_counts')}"
        )
        print(f"  legend_spatial={summary.get('legend_spatial')}")
    print()
    if not result["manifest_differences"]:
        print("Manifest comparison: identical")
    else:
        print(f"Manifest differences: {len(result['manifest_differences'])}")
        for item in result["manifest_differences"]:
            print(f"  {item['field']}: baseline={item['baseline']!r} candidate={item['candidate']!r}")
    print()
    if not result["layer_differences"]:
        print("Delivery feature inventories: identical (counts + source_entity_key/source_handle sets)")
    else:
        print(f"Delivery feature differences: {len(result['layer_differences'])}")
        for item in result["layer_differences"]:
            if item["kind"] == "layer_feature_inventory":
                print(
                    f"  {item['layer']}: baseline={item['baseline_count']} "
                    f"candidate={item['candidate_count']} "
                    f"missing={item['missing_in_candidate_count']} "
                    f"extra={item['extra_in_candidate_count']}"
                )
                for record in item["missing_in_candidate"][:10]:
                    print(f"    missing: {record}")
                if item["missing_in_candidate_count"] > 10:
                    print("    ...")
                for record in item["extra_in_candidate"][:10]:
                    print(f"    extra:   {record}")
                if item["extra_in_candidate_count"] > 10:
                    print("    ...")
            else:
                print(f"  {item}")
    print()
    print("RESULT:", "IDENTICAL" if result["identical"] else "DIFFERENCES FOUND")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two CAD2GIS run directories deterministically."
    )
    parser.add_argument("baseline_run_dir", type=Path)
    parser.add_argument("candidate_run_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        result = compare_runs(args.baseline_run_dir, args.candidate_run_dir)
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"compare_runs error: {exc}\n")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(result)
    return 0 if result["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
