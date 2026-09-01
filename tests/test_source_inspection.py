from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.project_profile import inspect_source


class _Inventory(list[dict]):
    def __init__(self, values: list[dict], diagnostics: dict) -> None:
        super().__init__(values)
        self.diagnostics = diagnostics


def _record(source: Path, source_sha256: str) -> dict:
    return {
        "entity_key": "line:1",
        "source_sha256": source_sha256,
        "source_file": source.name,
        "handle": "1",
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": "ROUTE",
        "object_name": "AcDbLine",
        "dwg_type_name": "LINE",
        "points": ((0.0, 0.0), (3.0, 4.0)),
        "centroid": (1.5, 2.0),
        "closed": False,
        "text": "",
        "block_name": "",
        "block_attributes": {},
        "native_length": 5.0,
        "raw_properties": {},
    }


def test_source_inspection_is_profile_free_and_source_bound(
    tmp_path: Path,
) -> None:
    source = tmp_path / "arbitrary-name.dwg"
    source.write_bytes(b"arbitrary-dwg-fixture")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    inventory = _Inventory(
        [_record(source, source_sha256)],
        {
            "inventory_complete": True,
            "skipped_rows": 0,
            "extraction_backend": "fixture",
        },
    )

    result = inspect_source(source=source, records=inventory)

    assert result["schema_version"] == "cad2gis-source-inventory-v1"
    assert result["source"]["sha256"] == source_sha256
    assert result["counts"]["records"] == 1
    assert result["counts"]["native_length_entities"] == 1
    assert result["plan_domain"]["status"] == "PASS"
    assert result["inspection_status"] == "PASS"
    assert result["onboarding"]["conversion_allowed"] is False
    assert result["onboarding"]["next_action"] == (
        "bootstrap_source_bound_project_pack"
    )


def test_source_inventory_census_matches_ingest_cad_roles(tmp_path: Path) -> None:
    source = tmp_path / "mixed-model-layout.dwg"
    source.write_bytes(b"mixed-model-layout")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    route = _record(source, source_sha256)
    title_block = {
        **_record(source, source_sha256),
        "entity_key": "title:1",
        "handle": "2",
        "cad_role": "title_block",
        "layout_role": "model",
        "layer": "TITLE BLOCK",
    }
    inventory = _Inventory(
        [route, title_block],
        {
            "inventory_complete": True,
            "skipped_rows": 0,
            "extraction_backend": "fixture",
        },
    )

    result = inspect_source(source=source, records=inventory)

    assert result["counts"]["records"] == 2
    assert result["counts"]["model_entities"] == 1
    assert result["cad_roles"] == {"model": 1, "title_block": 1}


@pytest.mark.parametrize(
    "diagnostics",
    [
        {"inventory_complete": False, "skipped_rows": 0},
        {"inventory_complete": True, "skipped_rows": 1},
        {"skipped_rows": 0},
    ],
)
def test_source_inspection_fails_closed_on_incomplete_reader(
    tmp_path: Path,
    diagnostics: dict,
) -> None:
    source = tmp_path / "input.dwg"
    source.write_bytes(b"input")
    inventory = _Inventory([], diagnostics)

    with pytest.raises(ValueError, match="not authoritative"):
        inspect_source(source=source, records=inventory)
