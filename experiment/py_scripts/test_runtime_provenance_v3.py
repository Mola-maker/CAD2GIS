"""Focused tests for environment identity and manifest determinism."""

from __future__ import annotations

from cad2gis_v3.runtime_provenance import (
    RUNTIME_PROVENANCE_SCHEMA_VERSION,
    collect_runtime_provenance,
    runtime_manifest_fields,
)


def test_runtime_provenance_is_json_stable_and_reports_required_domains():
    inventory = {
        "backend_statuses": {"com_direct": 3},
        "runtime": {"path": "C:/Program Files/Autodesk/AutoCAD 2025/accoreconsole.exe", "version": 2025},
    }
    first = collect_runtime_provenance(reader_inventory=inventory)
    second = collect_runtime_provenance(reader_inventory=inventory)
    assert first == second
    assert first["schema_version"] == RUNTIME_PROVENANCE_SCHEMA_VERSION
    assert {"python", "sqlite", "os", "gdal", "ogr", "proj", "pyproj", "autocad"} <= set(first)
    assert first["autocad"]["core_console"]["version"] == 2025
    fields = runtime_manifest_fields(first)
    assert fields["runtime_provenance"] == first
    assert len(fields["runtime_provenance_sha256"]) == 64


def test_runtime_provenance_preserves_unknown_reader_facts_instead_of_dropping_them():
    result = collect_runtime_provenance(reader_inventory={"new_reader_key": {"count": 1}})
    assert result["autocad"]["inventory"]["new_reader_key"]["count"] == 1


def test_runtime_provenance_accepts_flattened_core_console_reader_facts():
    result = collect_runtime_provenance(
        reader_inventory={
            "accoreconsole_path": "C:/Program Files/Autodesk/AutoCAD 2027/accoreconsole.exe",
            "accoreconsole_source": "version_discovery",
            "accoreconsole_version": 2027,
        }
    )
    assert result["autocad"]["core_console"] == {
        "path": "C:/Program Files/Autodesk/AutoCAD 2027/accoreconsole.exe",
        "version": 2027,
        "source": "version_discovery",
    }
