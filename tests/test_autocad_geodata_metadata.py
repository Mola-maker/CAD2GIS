"""The AutoCAD adapter may read document GEODATA without replacing CAD entities."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest

from cad2gis.reader import autocad, libredwg_cli
from cad2gis.cad2gis_v3.geodata import GEODATA_REGISTRATION_SCHEMA, local_to_crs_point


@pytest.fixture
def registration():
    return {
        "schema_version": GEODATA_REGISTRATION_SCHEMA,
        "coordinate_system_id": "UTM84-49S", "target_crs": "EPSG:32749",
        "design_point": [8572.1608, -1081.4506, 0.0],
        "reference_point": [434463.6991, 9229174.1289, 0.0],
        "horizontal_unit_scale": 1.0, "user_scale_factor": 1.0,
        "north_direction": [0.0, 1.0], "authority": "DWG_DIRECT:GEODATA",
    }


@pytest.fixture
def context(tmp_path, monkeypatch, registration):
    source = tmp_path / "source.dwg"
    source.write_bytes(b"original CAD bytes")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    executable = tmp_path / "dwg2dxf.exe"
    monkeypatch.setattr(libredwg_cli, "discover_libredwg_cli", lambda: (executable, "test"))
    monkeypatch.setattr(libredwg_cli, "_read_geodata_registration", lambda exe, path: (
        deepcopy(registration), {"status": "available", "reader": "dwgread.exe"}))
    records = [
        {"dwg_type_name": "DOCUMENT_METADATA", "text": "CGEOCS=UTM84-49S;INSUNITS=6",
         "raw_properties": {"native": "retained"}},
        {"dwg_type_name": "MTEXT", "text": "Degree \N{DEGREE SIGN} original CAD text",
         "points": [[8572.1608, -1081.4506]], "raw_properties": {"text": "original"}},
    ]
    return source, source_hash, records


def test_geodata_preserves_native_text_geometry_and_records_exact_source(context):
    source, source_hash, records = context
    original = deepcopy(records)
    result = autocad._attach_geodata_metadata(records, source, source_hash)
    assert records[1:] == original[1:]
    assert records[0]["text"] == original[0]["text"]
    properties = records[0]["raw_properties"]
    assert properties["native"] == "retained"
    assert result["source_sha256"] == source_hash
    assert properties["geodata_provenance"]["entity_geometry_and_text_backend"] == "autocad"
    # This is the onsite Semarang design/reference pair, not an identity CRS.
    assert local_to_crs_point((8572.1608, -1081.4506), properties["geodata_registration"]) == pytest.approx((434463.6991, 9229174.1289))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


def test_conflicting_native_crs_is_rejected_without_mutation(context):
    source, source_hash, records = context
    records[0]["text"] = "CGEOCS=WGS84.PseudoMercator;INSUNITS=6"
    before = deepcopy(records)
    with pytest.raises(ValueError, match="CGEOCS conflicts"):
        autocad._attach_geodata_metadata(records, source, source_hash)
    assert records == before


@pytest.mark.parametrize("when", ["before", "during"])
def test_changed_dwg_is_rejected(context, monkeypatch, registration, when):
    source, source_hash, records = context
    before = deepcopy(records)
    if when == "before":
        source.write_bytes(b"changed")
    else:
        def changed(exe, path):
            source.write_bytes(b"changed")
            return registration, {"status": "available"}
        monkeypatch.setattr(libredwg_cli, "_read_geodata_registration", changed)
    with pytest.raises(ValueError, match="DWG changed"):
        autocad._attach_geodata_metadata(records, source, source_hash)
    assert records == before


def test_multiple_geodata_objects_fail_closed(context, monkeypatch):
    source, source_hash, records = context
    monkeypatch.setattr(libredwg_cli, "_read_geodata_registration", lambda exe, path: (
        None, {"status": "unavailable", "geodata_object_count": 2, "detail": "Two competing registrations"}))
    with pytest.raises(ValueError, match="multiple DWG GEODATA"):
        autocad._attach_geodata_metadata(records, source, source_hash)


def test_missing_companion_does_not_replace_native_facts(context, monkeypatch):
    source, source_hash, records = context
    before = deepcopy(records)
    monkeypatch.setattr(libredwg_cli, "discover_libredwg_cli", lambda: (None, "unavailable"))
    diagnostics = autocad._attach_geodata_metadata(records, source, source_hash)
    assert diagnostics["status"] == "unavailable"
    assert records == before


def test_conflicting_preexisting_geodata_rejected(context, registration):
    source, source_hash, records = context
    other = deepcopy(registration)
    other["design_point"] = [0.0, 0.0, 0.0]
    records[0]["raw_properties"]["geodata_registration"] = other
    before = deepcopy(records)
    with pytest.raises(ValueError, match="conflicting GEODATA"):
        autocad._attach_geodata_metadata(records, source, source_hash)
    assert records == before


def test_public_native_inventory_includes_separate_geodata_authority(context, monkeypatch):
    source, source_hash, records = context
    original_text = records[1]["text"]
    records[0].update(handle="", layout="DOCUMENT")
    records[1].update(handle="21D6C", layout="Model")
    grouped = autocad.DWGRecordInventory(
        [("Model", "model", records)], diagnostics={"skipped_rows": 0, "total_rows": 2})
    monkeypatch.setattr(autocad, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(autocad, "_extract_records_with_core_console", lambda *a, **kw: grouped)
    inventory = autocad.extract_dwg_records(source)
    assert inventory.diagnostics["backend"] == "autocad_core_console_bulk"
    assert inventory.diagnostics["geodata"]["source_sha256"] == source_hash
    assert inventory.diagnostics["inventory_complete"] is True
    assert inventory[1]["text"] == original_text
    assert inventory[1]["source_sha256"] == source_hash
    assert inventory[0]["raw_properties"]["geodata_registration"]["target_crs"] == "EPSG:32749"
