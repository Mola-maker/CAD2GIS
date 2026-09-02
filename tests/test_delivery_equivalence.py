from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from verify_delivery_equivalence import compare_deliveries  # noqa: E402


def _delivery(path, *, label="FIBER-01", geometry=b"exact-geometry", length=10.0,
              lineage="source-1", srs=3857):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
        connection.execute("INSERT INTO gpkg_contents VALUES ('CABLE', 'features')")
        connection.execute(
            "CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, "
            "geometry_type_name TEXT, srs_id INTEGER, z INTEGER, m INTEGER)"
        )
        connection.execute(
            "INSERT INTO gpkg_geometry_columns VALUES ('CABLE', 'geom', 'LINESTRING', ?, 0, 0)",
            (srs,),
        )
        connection.execute(
            "CREATE TABLE CABLE (fid INTEGER PRIMARY KEY, geom BLOB, display_label TEXT, "
            "label_provenance TEXT, LONGUEUR REAL, lineage_json TEXT)"
        )
        connection.execute(
            "INSERT INTO CABLE VALUES (1, ?, ?, 'DWG_TEXT:h1', ?, ?)",
            (geometry, label, length, lineage),
        )


@pytest.mark.parametrize("change", [
    {"label": "FIBER-02"}, {"geometry": b"moved"}, {"length": 10.000001},
    {"lineage": "source-2"}, {"srs": 32749},
])
def test_equal_counts_do_not_hide_label_geometry_length_lineage_or_crs_regression(tmp_path, change):
    baseline, candidate = tmp_path / "baseline.gpkg", tmp_path / "candidate.gpkg"
    _delivery(baseline)
    _delivery(candidate, **change)
    report = compare_deliveries(baseline, candidate)
    assert not report["equivalent"]
    assert report["different_layers"] == ["CABLE"]


def test_identical_delivery_rows_match_and_comparison_is_read_only(tmp_path):
    baseline, candidate = tmp_path / "baseline.gpkg", tmp_path / "candidate.gpkg"
    _delivery(baseline)
    _delivery(candidate)
    before = (baseline.read_bytes(), candidate.read_bytes())
    assert compare_deliveries(baseline, candidate)["equivalent"]
    assert before == (baseline.read_bytes(), candidate.read_bytes())
