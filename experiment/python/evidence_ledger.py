#!/usr/bin/env python3
"""
Evidence Ledger — D-component provenance tables for the FTTH GPKG pipeline
===========================================================================

Three non-spatial GeoPackage tables (spec component D):

  conservation_ledger
      Per-(disposition, dwg_layer) entity counts covering ALL model-space
      entities. A reserved meta row (disposition="__expected_total__")
      stores the expected entity total so the SUM==total invariant can be
      re-verified by any later reader (evaluator rule 8.1).
      Dispositions: mapped / legend / annotation_consumed / out_of_scope /
      block_definition / paper_space / ... (open vocabulary, converter-fed).

  annotation_assignment_candidates
      Every candidate edge produced by the label-binding stage:
      text / family / target_fc / target_code / distance_m / selected /
      status in {selected, lost, abstained_multiple_optima}.

  field_provenance
      Aggregated origin of non-empty business field values:
      (fc, field, provenance, count) with provenance in
      {annotation-assigned, synthetic, computed, dwg-attribute}.

Design notes
  - Pure data-in functions: callers pass plain dict/list structures; this
    module owns only the OGR I/O. Converter integration happens separately.
  - Writers REPLACE an existing table of the same name (idempotent reruns).
  - Conservation validation is advisory by default (mismatch is recorded
    and returned); pass strict=True to fail-closed (v3 style). The
    evaluator applies the warning/strict gate policy (rule group 8.x,
    --strict-provenance).

CLI:
  python3 evidence_ledger.py --self-test
      Writes synthetic data to a temporary GPKG, reads it back and checks
      round-trip equality, the SUM invariant (pass + fail paths) and the
      strict-mode ValueError path.
  python3 evidence_ledger.py --inspect <path.gpkg>
      Prints a summary of the three evidence tables of an existing GPKG.
"""

import argparse
import os
import sys
import tempfile
from collections import Counter

from osgeo import ogr

# ── Public constants ──────────────────────────────────────────────────────────

CONSERVATION_TABLE = "conservation_ledger"
CANDIDATES_TABLE = "annotation_assignment_candidates"
PROVENANCE_TABLE = "field_provenance"

# Reserved disposition of the meta row carrying the expected entity total.
META_TOTAL_DISPOSITION = "__expected_total__"

CANDIDATE_STATUSES = frozenset({"selected", "lost", "abstained_multiple_optima"})
PROVENANCE_KINDS = frozenset({"annotation-assigned", "synthetic", "computed", "dwg-attribute"})

_CONSERVATION_FIELDS = (
    ("disposition", ogr.OFTString),
    ("dwg_layer", ogr.OFTString),
    ("entity_count", ogr.OFTInteger),
)
_CANDIDATE_FIELDS = (
    ("annotation_key", ogr.OFTString),
    ("text", ogr.OFTString),
    ("family", ogr.OFTString),
    ("target_fc", ogr.OFTString),
    ("target_code", ogr.OFTString),
    ("distance_m", ogr.OFTReal),
    ("selected", ogr.OFTInteger),
    ("status", ogr.OFTString),
)
_PROVENANCE_FIELDS = (
    ("fc", ogr.OFTString),
    ("field", ogr.OFTString),
    ("provenance", ogr.OFTString),
    ("count", ogr.OFTInteger),
)


# ── Data-shape helpers (pure functions, no I/O) ──────────────────────────────

def entries_from_nested_counts(nested):
    """Flatten {disposition: {dwg_layer: count}} into conservation entries."""
    return [
        {"disposition": disposition, "dwg_layer": dwg_layer, "count": int(count)}
        for disposition in sorted(nested)
        for dwg_layer, count in sorted(nested[disposition].items())
    ]


def aggregate_provenance(occurrences):
    """Aggregate an iterable of (fc, field, provenance) into count records."""
    counter = Counter(occurrences)
    return [
        {"fc": fc, "field": field, "provenance": provenance, "count": count}
        for (fc, field, provenance), count in sorted(counter.items())
    ]


def validate_conservation(entries, expected_total):
    """Check SUM(entries.count) == expected_total. Returns (ok, actual_sum)."""
    total = sum(int(entry["count"]) for entry in entries)
    return total == int(expected_total), total


# ── OGR plumbing ──────────────────────────────────────────────────────────────

def _open_update(gpkg_path):
    gpkg_path = str(gpkg_path)
    if os.path.exists(gpkg_path):
        ds = ogr.Open(gpkg_path, 1)
    else:
        ds = ogr.GetDriverByName("GPKG").CreateDataSource(gpkg_path)
    if ds is None:
        raise RuntimeError(f"Cannot open/create GeoPackage for update: {gpkg_path}")
    return ds


def _replace_table(ds, name, fields):
    for i in range(ds.GetLayerCount()):
        if ds.GetLayerByIndex(i).GetName() == name:
            ds.DeleteLayer(i)
            break
    layer = ds.CreateLayer(name, None, ogr.wkbNone)
    if layer is None:
        raise RuntimeError(f"Cannot create table: {name}")
    for field_name, field_type in fields:
        layer.CreateField(ogr.FieldDefn(field_name, field_type))
    return layer


def _insert(layer, values):
    row = ogr.Feature(layer.GetLayerDefn())
    for key, value in values.items():
        if value is not None:
            row.SetField(key, value)
    layer.CreateFeature(row)


# ── Table writers (take an open update dataset) ───────────────────────────────

def _write_conservation(ds, entries, expected_total=None, strict=False):
    result = {"rows": len(entries), "sum": sum(int(e["count"]) for e in entries),
              "expected_total": expected_total, "ok": None}
    if expected_total is not None:
        ok, total = validate_conservation(entries, expected_total)
        result["ok"] = ok
        if not ok and strict:
            raise ValueError(
                f"conservation_ledger SUM mismatch: sum={total} != expected={expected_total}")
    layer = _replace_table(ds, CONSERVATION_TABLE, _CONSERVATION_FIELDS)
    # Counting basis: rows are SOURCE-ENTITY weighted, not output-feature
    # counts (aggregates expand to their source_entity_count; derived copies
    # weigh 0). E.g. a legend cluster of 67 output features may account for
    # only 43 source entities. Recorded on the table so gpkg consumers see it.
    layer.SetMetadataItem(
        "DESCRIPTION",
        "entity_count basis: source DWG model-space entities (entity-weighted;"
        " aggregates expand to source_entity_count, annotation-derived copies"
        " weigh 0) — NOT delivered/output feature counts. SUM over dispositions"
        f" equals the {META_TOTAL_DISPOSITION} row.")
    for entry in entries:
        _insert(layer, {
            "disposition": str(entry["disposition"]),
            "dwg_layer": str(entry.get("dwg_layer", "")),
            "entity_count": int(entry["count"]),
        })
    if expected_total is not None:
        _insert(layer, {
            "disposition": META_TOTAL_DISPOSITION,
            "dwg_layer": "*",
            "entity_count": int(expected_total),
        })
    return result


def _write_candidates(ds, candidates):
    layer = _replace_table(ds, CANDIDATES_TABLE, _CANDIDATE_FIELDS)
    for cand in candidates:
        _insert(layer, {
            "annotation_key": cand.get("annotation_key"),
            "text": cand.get("text"),
            "family": cand.get("family"),
            "target_fc": cand.get("target_fc"),
            "target_code": cand.get("target_code"),
            "distance_m": (None if cand.get("distance_m") is None
                           else float(cand["distance_m"])),
            "selected": int(bool(cand.get("selected"))),
            "status": cand.get("status"),
        })
    return {"rows": len(candidates)}


def _write_provenance(ds, records):
    layer = _replace_table(ds, PROVENANCE_TABLE, _PROVENANCE_FIELDS)
    for rec in records:
        _insert(layer, {
            "fc": str(rec["fc"]),
            "field": str(rec["field"]),
            "provenance": str(rec["provenance"]),
            "count": int(rec["count"]),
        })
    return {"rows": len(records)}


# ── Public API (path-based, open/close per call) ─────────────────────────────

def write_conservation_ledger(gpkg_path, entries, expected_total=None, strict=False):
    """Write (replace) conservation_ledger. Returns {rows, sum, expected_total, ok}."""
    ds = _open_update(gpkg_path)
    try:
        return _write_conservation(ds, entries, expected_total, strict)
    finally:
        ds = None


def write_annotation_candidates(gpkg_path, candidates):
    """Write (replace) annotation_assignment_candidates. Returns {rows}."""
    ds = _open_update(gpkg_path)
    try:
        return _write_candidates(ds, candidates)
    finally:
        ds = None


def write_field_provenance(gpkg_path, records):
    """Write (replace) field_provenance. Returns {rows}."""
    ds = _open_update(gpkg_path)
    try:
        return _write_provenance(ds, records)
    finally:
        ds = None


def write_evidence_tables(gpkg_path, conservation_entries=None, expected_total=None,
                          candidates=None, provenance_records=None, strict=False):
    """Write all provided evidence tables in a single dataset session."""
    summary = {}
    ds = _open_update(gpkg_path)
    try:
        if conservation_entries is not None:
            summary[CONSERVATION_TABLE] = _write_conservation(
                ds, conservation_entries, expected_total, strict)
        if candidates is not None:
            summary[CANDIDATES_TABLE] = _write_candidates(ds, candidates)
        if provenance_records is not None:
            summary[PROVENANCE_TABLE] = _write_provenance(ds, provenance_records)
    finally:
        ds = None
    return summary


def read_table(gpkg_path, table_name):
    """Read a table into a list of dicts; None if the table does not exist."""
    ds = ogr.Open(str(gpkg_path), 0)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage: {gpkg_path}")
    try:
        layer = ds.GetLayerByName(table_name)
        if layer is None:
            return None
        defn = layer.GetLayerDefn()
        names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
        layer.ResetReading()
        return [{name: feat.GetField(name) for name in names} for feat in layer]
    finally:
        ds = None


def verify_conservation_sum(gpkg_path):
    """
    Re-verify the SUM invariant from a written GPKG.
    Returns {present, sum, expected, ok}: ok is True only when the meta row
    exists and SUM(non-meta entity_count) == expected.
    """
    rows = read_table(gpkg_path, CONSERVATION_TABLE)
    if rows is None:
        return {"present": False, "sum": None, "expected": None, "ok": False}
    expected = None
    total = 0
    for row in rows:
        if row.get("disposition") == META_TOTAL_DISPOSITION:
            expected = row.get("entity_count")
        else:
            total += int(row.get("entity_count") or 0)
    return {"present": True, "sum": total, "expected": expected,
            "ok": expected is not None and total == int(expected)}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _self_test():
    failures = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="evidence_ledger_") as tmp:
        gpkg = os.path.join(tmp, "self_test.gpkg")

        conservation = entries_from_nested_counts({
            "mapped": {"FAT": 40, "KU KABEL 12": 25},
            "annotation_consumed": {"TEXT NAMA JALAN": 20},
            "out_of_scope": {"0": 10},
            "legend": {"LEGENDA": 5},
        })
        candidates = [
            {"annotation_key": "ann-001", "text": "DMPH-01.001.A01", "family": "fat",
             "target_fc": "BOITE", "target_code": "DMPH-01.001.A01",
             "distance_m": 3.2, "selected": 1, "status": "selected"},
            {"annotation_key": "ann-001", "text": "DMPH-01.001.A01", "family": "fat",
             "target_fc": "BOITE", "target_code": "DMPH-01.001.A02",
             "distance_m": 9.7, "selected": 0, "status": "lost"},
            {"annotation_key": "ann-002", "text": "MR.DMPH.P10", "family": "pole",
             "target_fc": "PTECH", "target_code": None,
             "distance_m": 7.1, "selected": 0, "status": "abstained_multiple_optima"},
        ]
        provenance = aggregate_provenance(
            [("BOITE", "CODE", "annotation-assigned")] * 43
            + [("PTECH", "CODE", "synthetic")] * 49
            + [("CABLE", "LONGUEUR", "computed")] * 6
        )

        summary = write_evidence_tables(
            gpkg, conservation_entries=conservation, expected_total=100,
            candidates=candidates, provenance_records=provenance)
        check("conservation SUM validation (pass path)",
              summary[CONSERVATION_TABLE]["ok"] is True,
              f"sum={summary[CONSERVATION_TABLE]['sum']} expected=100")

        readback = verify_conservation_sum(gpkg)
        check("conservation readback SUM invariant",
              readback == {"present": True, "sum": 100, "expected": 100, "ok": True},
              str(readback))

        cand_rows = read_table(gpkg, CANDIDATES_TABLE)
        check("candidates round-trip row count", len(cand_rows) == 3)
        check("candidates round-trip content",
              cand_rows[0]["text"] == "DMPH-01.001.A01"
              and cand_rows[0]["selected"] == 1
              and cand_rows[2]["status"] == "abstained_multiple_optima"
              and abs(cand_rows[1]["distance_m"] - 9.7) < 1e-9)

        prov_rows = read_table(gpkg, PROVENANCE_TABLE)
        check("provenance round-trip aggregation",
              {(r["fc"], r["field"], r["provenance"]): r["count"] for r in prov_rows}
              == {("BOITE", "CODE", "annotation-assigned"): 43,
                  ("PTECH", "CODE", "synthetic"): 49,
                  ("CABLE", "LONGUEUR", "computed"): 6})
        check("provenance kinds vocabulary",
              all(r["provenance"] in PROVENANCE_KINDS for r in prov_rows))

        # Fail path: advisory mismatch is recorded, not raised.
        mismatch = write_conservation_ledger(gpkg, conservation, expected_total=101)
        check("conservation SUM validation (fail path, advisory)",
              mismatch["ok"] is False, f"sum={mismatch['sum']} expected=101")
        readback_bad = verify_conservation_sum(gpkg)
        check("conservation readback detects mismatch",
              readback_bad["present"] and readback_bad["ok"] is False,
              str(readback_bad))

        # Strict path: mismatch must raise.
        try:
            write_conservation_ledger(gpkg, conservation, expected_total=101, strict=True)
            check("strict mode raises on SUM mismatch", False, "no exception raised")
        except ValueError as exc:
            check("strict mode raises on SUM mismatch", True, str(exc))

        # Table replacement is idempotent (no duplicate rows on rewrite).
        write_conservation_ledger(gpkg, conservation, expected_total=100)
        rows = read_table(gpkg, CONSERVATION_TABLE)
        check("table replacement idempotency",
              len(rows) == len(conservation) + 1, f"{len(rows)} rows")

        # Missing table reads as None (evaluator relies on this).
        check("missing table reads as None",
              read_table(gpkg, "no_such_table") is None)

    print(f"\nSelf-test: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s))")
    return 0 if not failures else 1


def _inspect(gpkg_path):
    print(f"GeoPackage: {gpkg_path}")
    conservation = verify_conservation_sum(gpkg_path)
    print(f"  {CONSERVATION_TABLE}: present={conservation['present']} "
          f"sum={conservation['sum']} expected={conservation['expected']} "
          f"ok={conservation['ok']}")
    for table in (CANDIDATES_TABLE, PROVENANCE_TABLE):
        rows = read_table(gpkg_path, table)
        print(f"  {table}: " + ("absent" if rows is None else f"{len(rows)} rows"))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Evidence ledger tables (component D)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-test", action="store_true",
                       help="Run synthetic write/readback/validation self-test")
    group.add_argument("--inspect", metavar="GPKG",
                       help="Summarize evidence tables of an existing GeoPackage")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(_self_test())
    sys.exit(_inspect(args.inspect))


if __name__ == "__main__":
    main()
