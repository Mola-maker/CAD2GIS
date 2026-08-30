from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from cad2gis.cad2gis_v3.source_export import export_source


class _Inventory(list[dict]):
    def __init__(self, values: list[dict], diagnostics: dict) -> None:
        super().__init__(values)
        self.diagnostics = diagnostics


def test_export_source_stops_before_semantics_and_preserves_records(tmp_path: Path):
    source = tmp_path / "input.dwg"
    source.write_bytes(b"fixture")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    records = _Inventory(
        [
            {
                "entity_key": "line:1",
                "source_sha256": digest,
                "source_file": source.name,
                "handle": "1",
                "layout": "Model",
                "layout_role": "model",
                "cad_role": "model",
                "layer": "ANY",
                "object_name": "AcDbLine",
                "dwg_type_name": "LINE",
                "points": ((0.0, 0.0), (1.0, 1.0)),
                "centroid": (0.5, 0.5),
                "closed": False,
                "text": "",
                "block_name": "",
                "block_attributes": {},
                "native_length": 2 ** 0.5,
                "raw_properties": {},
            }
        ],
        {"inventory_complete": True, "skipped_rows": 0},
    )

    result = export_source(source=source, run_dir=tmp_path / "run", records=records)

    assert result["status"] == "SOURCE_EXPORTED"
    assert result["coordinate_reference"]["state"] == "native_cad_unregistered"
    assert result["conservation"] == {
        "reader_records": 1,
        "gpkg_entities": 1,
        "difference": 0,
        "passed": True,
    }
    assert "semantic_mapping" in result["excluded_stages"]
    assert not (tmp_path / "run" / "delivery.gpkg").exists()
    with sqlite3.connect(tmp_path / "run" / "source.gpkg") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM source_entity_accounting"
        ).fetchone()[0] == 1
