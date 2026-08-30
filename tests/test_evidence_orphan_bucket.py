"""Tests for the orphan_block_member disposition bucket in evidence."""

from __future__ import annotations

import sqlite3

from cad2gis.cad2gis_v3.evidence import write_evidence
from cad2gis.cad2gis_v3.model import SourceEntity


def _entity(key: str, *, cad_role: str) -> SourceEntity:
    return SourceEntity.from_record({
        "entity_key": key,
        "source_sha256": "a" * 64,
        "source_file": "fixture.dwg",
        "handle": key,
        "layout": "Model",
        "layout_role": "model",
        "cad_role": cad_role,
        "layer": "FIBER_ROUTE",
        "object_name": "ACDBLINE",
        "dwg_type_name": "LINE",
        "points": [(0.0, 0.0), (1.0, 0.0)],
        "centroid": (0.0, 0.0),
        "raw_properties": {},
    })


def _ledger(path) -> dict:
    connection = sqlite3.connect(path)
    try:
        return dict(connection.execute(
            "SELECT disposition, entity_count FROM conservation_ledger"
        ).fetchall())
    finally:
        connection.close()


def test_orphan_member_keys_add_conservation_bucket(tmp_path) -> None:
    orphan = _entity("orphan-member", cad_role="block_definition")
    graphic = _entity("model-line", cad_role="model")
    path = tmp_path / "evidence.gpkg"

    write_evidence(
        path, [orphan, graphic], [], [], [], {}, None,
        orphan_member_keys=frozenset({"orphan-member"}),
    )

    ledger = _ledger(path)
    assert ledger["orphan_block_member"] == 1
    assert ledger["graphic_only"] == 1
    assert sum(ledger.values()) == 2


def test_write_evidence_without_orphan_keys_keeps_current_buckets(tmp_path) -> None:
    orphan_like = _entity("orphan-member", cad_role="block_definition")
    graphic = _entity("model-line", cad_role="model")
    path = tmp_path / "evidence.gpkg"

    write_evidence(path, [orphan_like, graphic], [], [], [], {}, None)

    ledger = _ledger(path)
    assert ledger == {"graphic_only": 1, "out_of_scope": 1}
