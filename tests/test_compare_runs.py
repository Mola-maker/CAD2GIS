from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "diagnostics"))

from compare_runs import compare_runs  # noqa: E402


def _write_delivery(path: Path, layers: dict[str, list[tuple[str, str]]]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE gpkg_contents (
                table_name TEXT PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT,
                description TEXT,
                last_change TEXT,
                min_x REAL, min_y REAL, max_x REAL, max_y REAL,
                srs_id INTEGER
            )
            """
        )
        for layer, rows in layers.items():
            connection.execute(
                f'CREATE TABLE "{layer}" '
                "(source_entity_key TEXT, source_handle TEXT)"
            )
            connection.executemany(
                f'INSERT INTO "{layer}" VALUES (?, ?)', rows,
            )
            connection.execute(
                "INSERT INTO gpkg_contents(table_name, data_type, identifier) "
                "VALUES (?, 'features', ?)",
                (layer, layer),
            )
        connection.commit()
    finally:
        connection.close()


def _write_run(path: Path, layers: dict[str, list[tuple[str, str]]]) -> None:
    path.mkdir(parents=True)
    _write_delivery(path / "delivery.gpkg", layers)
    (path / "run_manifest.json").write_text(json.dumps({
        "schema_version": "cad2gis-run-manifest-v4",
        "pipeline": "cad2gis-reviewed-project-evidence-first-v4",
        "run_status": "CONDITIONAL",
        "modes": {"domain": "auto", "llm": "assist"},
        "source": {"path": "fixture.dwg", "sha256": "a" * 64},
        "source_entity_count": 10,
        "unresolved_count": 1,
        "delivery_counts": {"PTECH": 2, "CABLE": 1},
        "legend_spatial": {
            "llm_mode": "assist",
            "status": "denoised",
            "flagged_count": 1,
            "auto_excluded_count": 2,
        },
        "semantics": {"status": "WATCH", "passed": False, "conversion_allowed": True},
    }), encoding="utf-8")


def test_compare_runs_reports_identical_inventories(tmp_path: Path) -> None:
    layers = {
        "PTECH": [("p1", "A1"), ("p2", "A2")],
        "CABLE": [("c1", "B1")],
    }
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_run(left, layers)
    _write_run(right, layers)

    result = compare_runs(left, right)

    assert result["identical"] is True
    assert result["manifest_differences"] == []
    assert result["layer_differences"] == []


def test_compare_runs_reports_missing_feature(tmp_path: Path) -> None:
    _write_run(
        tmp_path / "left",
        {
            "PTECH": [("p1", "A1"), ("p2", "A2")],
            "CABLE": [("c1", "B1")],
        },
    )
    _write_run(
        tmp_path / "right",
        {
            "PTECH": [("p1", "A1")],
            "CABLE": [("c1", "B1")],
        },
    )

    result = compare_runs(tmp_path / "left", tmp_path / "right")

    assert result["identical"] is False
    assert result["layer_differences"][0]["layer"] == "PTECH"
    assert result["layer_differences"][0]["missing_in_candidate"] == [
        {"source_entity_key": "p2", "source_handle": "A2"}
    ]
