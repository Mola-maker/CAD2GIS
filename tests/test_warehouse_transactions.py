"""The extracted writer boundaries must preserve all-or-nothing publication."""

from __future__ import annotations

import sqlite3

import pytest

from cad2gis.cad2gis_v3 import warehouse
from cad2gis.cad2gis_v3.georef import DirectTransformer
from cad2gis.cad2gis_v3.model import CadStyle, Feature


def test_empty_delivery_keeps_complete_ordered_schema(tmp_path):
    path = tmp_path / "delivery.gpkg"
    counts = warehouse.write_delivery(path, [], DirectTransformer("EPSG:3857", "EPSG:3857"))
    assert list(counts) == list(warehouse.LAYER_ORDER)
    assert all(count == 0 for count in counts.values())
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for layer in counts:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{layer}")')]
            assert len(columns) == len(set(columns))
            assert "display_label" in columns and "label_provenance" in columns


def test_population_failure_does_not_replace_existing_delivery(tmp_path, monkeypatch):
    path = tmp_path / "delivery.gpkg"
    path.write_bytes(b"previous delivery")

    def fail(*_args):
        raise RuntimeError("simulated row failure")

    monkeypatch.setattr(warehouse, "_populate_dataset", fail)
    with pytest.raises(RuntimeError, match="simulated row failure"):
        warehouse.write_delivery(path, [], DirectTransformer("EPSG:3857", "EPSG:3857"))
    assert path.read_bytes() == b"previous delivery"
    assert list(tmp_path.iterdir()) == [path]


def test_asset_coordinate_mismatch_rolls_back_without_publishing(tmp_path):
    feature = Feature(
        "point-1", "PTECH", "Point", [(100.0, 100.0)], "e1", "h1", "POLE",
        "source", CadStyle(), attributes={"X": 200.0, "Y": 100.0},
    )
    path = tmp_path / "delivery.gpkg"
    with pytest.raises(RuntimeError, match="Projected coordinate enrichment mismatch"):
        warehouse.write_delivery(path, [feature], DirectTransformer("EPSG:3857", "EPSG:3857"))
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
