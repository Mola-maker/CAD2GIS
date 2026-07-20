"""Offline contracts for strict AutoCAD reader discovery and inventory facts."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import autocad_reader
from autocad_reader import (
    ACCORECONSOLE_ENV,
    ACCORECONSOLE_TIMEOUT_ENV,
    BULK_PROTOCOL_SCHEMA,
    BULK_POLICY_SKIP_MALFORMED,
    BULK_POLICY_STRICT,
    BulkExtractionResult,
    CURVE_FACTS_SCHEMA,
    DWGRecordInventory,
    BulkProtocolError,
    BulkProtocolViolation,
    _AUTOLISP_EXTRACTOR,
    _authorize_com_fallback,
    _collect_records,
    _configured_accoreconsole_path,
    _discover_accoreconsole_paths,
    _extract_records_with_core_console,
    _record_from_bulk_row,
    extract_com_entity,
    extract_dwg_records,
    preflight_autocad_reader,
)
from cad2gis_v3.ports import _transform_facts


def _metadata_row() -> list[str]:
    return [
        "DOCUMENT_METADATA", "", "0", "DOCUMENT", "256", "-1",
        "ByLayer", "-1", "0", "0", "", "CGEOCS=3857;INSUNITS=6",
        "", "", "0", "0", "0",
    ]


def _row54(kind: str = "POINT") -> list[str]:
    is_insert = kind == "INSERT"
    points = "10,20" if kind in {"POINT", "INSERT"} else "1,2;3,4"
    row = [
        kind, "H1", "CABLE" if kind == "LWPOLYLINE" else "OBJECTS", "Model",
        "256", "-1", "ByLayer", "-1", "0.25" if is_insert else "0",
        "0", "CABINET" if is_insert else "", "", "", points,
        "0", "0", "0", "7", "-1", "Continuous", "-1",
        "2" if is_insert else "1", "-3" if is_insert else "1", "1",
        "OWNER", "", "", "", "", "",
    ]
    if kind == "LWPOLYLINE":
        row.extend([
            CURVE_FACTS_SCHEMA, "1,2,0;3,4,0", "0,0", "0",
            "0,0,1", "0,0,1", "LWPOLYLINE", "{}",
        ])
    else:
        row.extend(["", "", "", "", "", "", "", ""])
    if is_insert:
        row.extend([
            "10,20,7", "available", "2,4,0", "available",
            "0,0,1", "available", "0,0,1", "available",
            "PARENT", "block_definition", "BLOCK-H", "0", "",
            "not_external", "anchor_only", "supported",
        ])
    else:
        row.extend([
            "", "not_applicable", "", "not_applicable",
            "", "not_applicable", "", "not_applicable",
            "", "drawing_space", "", "", "", "not_external",
            "available", "supported",
        ])
    assert len(row) == 54
    return row


def _fake_core_console_output(monkeypatch, rows: list[list[str]]):
    def fake_run(command, **_kwargs):
        script = Path(command[command.index("/s") + 1]).read_text(encoding="utf-8")
        match = re.search(r'\(cad2gis-export "([^"]+)"\)', script)
        assert match is not None
        Path(match.group(1)).write_text(
            "\n".join("\t".join(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(autocad_reader.subprocess, "run", fake_run)


def test_core_console_resolution_precedence_and_multiversion_discovery(
    tmp_path, monkeypatch,
):
    program_files = tmp_path / "Program Files"
    versions = {}
    for year in (2025, 2027, 2028):
        executable = program_files / "Autodesk" / f"AutoCAD {year}" / "accoreconsole.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        versions[year] = executable
    monkeypatch.setattr(autocad_reader, "DEFAULT_ACCORECONSOLE", versions[2027])
    monkeypatch.setattr(autocad_reader.shutil, "which", lambda _name: None)
    environ = {"ProgramFiles": str(program_files)}

    discovered = _discover_accoreconsole_paths(environ=environ)
    assert [autocad_reader._autocad_version(path) for path in discovered] == [
        2028, 2027, 2025,
    ]
    selected, source = _configured_accoreconsole_path(environ=environ)
    assert selected == versions[2028].resolve()
    assert source == "version_discovery"

    selected, source = _configured_accoreconsole_path(
        environ={ACCORECONSOLE_ENV: str(versions[2025])},
    )
    assert selected == versions[2025].resolve()
    assert source == "environment"
    selected, source = _configured_accoreconsole_path(
        versions[2027], environ={ACCORECONSOLE_ENV: str(versions[2025])},
    )
    assert selected == versions[2027].resolve()
    assert source == "explicit"


def test_preflight_resolves_timeout_without_launching_autocad(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.touch()
    monkeypatch.setattr(
        autocad_reader.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("preflight must not launch AutoCAD"),
    )
    result = preflight_autocad_reader(environ={
        ACCORECONSOLE_ENV: str(executable),
        ACCORECONSOLE_TIMEOUT_ENV: "37.5",
    })
    assert result["ok"] is (os.name == "nt")
    assert result["status"] == ("ready" if os.name == "nt" else "unsupported_platform")
    assert result["core_console"] == {
        "ok": True,
        "path": str(executable.resolve()),
        "source": "environment",
        "version": None,
    }
    assert result["timeout"] == {
        "ok": True, "seconds": 37.5, "source": "environment",
    }

    invalid = preflight_autocad_reader(environ={
        ACCORECONSOLE_ENV: str(executable),
        ACCORECONSOLE_TIMEOUT_ENV: "NaN",
    })
    assert invalid["ok"] is False
    assert invalid["status"] == "invalid_configuration"
    assert any("positive finite" in error for error in invalid["errors"])


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda row: row.__setitem__(13, "1,2,3"), "points"),
        (lambda row: row.__setitem__(31, "1,2;3,4,0"), "vertices_wcs"),
        (lambda row: row.__setitem__(32, "0,NaN"), "bulges"),
        (lambda row: row.__setitem__(27, "{"), "dynamic_block_properties"),
        (
            lambda row: row.__setitem__(27, '{"Visibility":1,"Visibility":2}'),
            "dynamic_block_properties",
        ),
        (lambda row: row.__setitem__(37, "{"), "primitive_parameters"),
        (lambda row: row.__setitem__(39, ""), "insertion_point_status"),
    ],
)
def test_malformed_protocol_values_fail_closed_with_line_and_field(mutate, field):
    row = _row54("LWPOLYLINE")
    mutate(row)
    with pytest.raises(BulkProtocolError) as caught:
        _record_from_bulk_row(row, line_number=42)
    assert caught.value.line_number == 42
    assert caught.value.field_name == field
    assert "bulk row 42" in str(caught.value)
    assert f"field {field}" in str(caught.value)


def test_default_strict_rejects_and_explicit_compatibility_reports_skips(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "accoreconsole.exe"
    executable.touch()
    source = tmp_path / "source.dwg"
    source.touch()
    malformed = _row54("POINT")
    malformed[13] = "not-a-point"
    valid = _row54("POINT")
    valid[1] = "GOOD"
    _fake_core_console_output(monkeypatch, [_metadata_row(), malformed, valid])

    with pytest.raises(
        RuntimeError,
        match=r"bulk row 2, field points",
    ):
        _extract_records_with_core_console(
            source, accoreconsole=executable, compatibility_policy=BULK_POLICY_STRICT,
        )

    result = _extract_records_with_core_console(
        source,
        accoreconsole=executable,
        compatibility_policy=BULK_POLICY_SKIP_MALFORMED,
    )
    assert result.diagnostics["compatibility_policy"] == BULK_POLICY_SKIP_MALFORMED
    assert result.diagnostics["protocol_schema"] == BULK_PROTOCOL_SCHEMA
    assert result.diagnostics["total_rows"] == 3
    assert result.diagnostics["parsed_rows"] == 2
    assert result.diagnostics["skipped_rows"] == 1
    assert result.diagnostics["entity_rows"] == 1
    assert len(result.diagnostics["skipped_row_errors"]) == 1
    assert result.diagnostics["skipped_row_errors"][0]["line_number"] == 2
    assert result.diagnostics["skipped_row_errors"][0]["field"] == "points"
    assert "field points" in result.diagnostics["skipped_row_errors"][0]["error"]


def test_protocol_violation_cannot_be_hidden_by_com_fallback(monkeypatch):
    monkeypatch.setenv(autocad_reader.COM_FALLBACK_ENV, "1")
    with pytest.raises(RuntimeError, match="not eligible for COM fallback"):
        _authorize_com_fallback(BulkProtocolViolation("malformed row"))


def test_timeout_argument_is_forwarded_without_real_autocad(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.touch()
    source = tmp_path / "source.dwg"
    source.touch()

    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(autocad_reader.subprocess, "run", timeout_run)
    with pytest.raises(RuntimeError, match=r"timed out after 2\.5 seconds"):
        _extract_records_with_core_console(
            source, accoreconsole=executable, timeout="2.5",
        )

    monkeypatch.setenv(ACCORECONSOLE_TIMEOUT_ENV, "3.75")
    with pytest.raises(RuntimeError, match=r"timed out after 3\.75 seconds"):
        _extract_records_with_core_console(source, accoreconsole=executable)


def test_flat_inventory_preserves_compatibility_diagnostics(tmp_path, monkeypatch):
    source = tmp_path / "source.dwg"
    source.write_bytes(b"offline-fixture")
    record = _record_from_bulk_row(_row54("POINT"))
    grouped = BulkExtractionResult(
        [("Model", "model", [record])],
        diagnostics={
            "compatibility_policy": BULK_POLICY_SKIP_MALFORMED,
            "total_rows": 2,
            "parsed_rows": 1,
            "skipped_rows": 1,
            "skipped_row_errors": [{
                "line_number": 2, "field": "points", "error": "bad point",
            }],
        },
    )
    monkeypatch.setattr(
        autocad_reader,
        "_extract_records_with_core_console",
        lambda *_args, **_kwargs: grouped,
    )

    inventory = extract_dwg_records(
        source, compatibility_policy=BULK_POLICY_SKIP_MALFORMED,
    )
    assert isinstance(inventory, DWGRecordInventory)
    assert len(inventory) == 1
    assert inventory.diagnostics["skipped_rows"] == 1
    assert inventory.diagnostics["inventory_complete"] is False
    assert inventory.diagnostics["returned_records"] == 1


def test_extended_insert_contract_preserves_nonzero_base_and_nested_facts():
    record = _record_from_bulk_row(_row54("INSERT"), line_number=5)
    raw = record["raw_properties"]

    assert record["insertion_point_wcs"] == (10.0, 20.0, 7.0)
    assert record["block_base_point"] == (2.0, 4.0, 0.0)
    assert record["container_block_name"] == "PARENT"
    assert record["nesting_context"] == "block_definition"
    assert record["owner_handle"] == "OWNER"
    assert raw["bulk_protocol_schema"] == BULK_PROTOCOL_SCHEMA
    assert raw["transform_facts"] == {
        "schema_version": "cad2gis-block-transform-facts-v1",
        "coordinate_system": "WCS",
        "insertion_point": [10.0, 20.0, 7.0],
        "insertion_point_status": "available",
        "block_base_point": [2.0, 4.0, 0.0],
        "block_base_point_status": "available",
        "scale": [2.0, -3.0, 1.0],
        "scale_status": "available",
        "rotation": 0.25,
        "rotation_status": "available",
        "normal": [0.0, 0.0, 1.0],
        "normal_status": "available",
        "extrusion": [0.0, 0.0, 1.0],
        "extrusion_status": "available",
        "owner_handle": "OWNER",
        "container_block_name": "PARENT",
        "nesting_context": "block_definition",
    }
    assert raw["block_base_point"] == [2.0, 4.0, 0.0]
    assert raw["normal"] == [0.0, 0.0, 1.0]
    assert raw["extrusion"] == [0.0, 0.0, 1.0]
    port_facts, port_diagnostics = _transform_facts(record)
    assert port_diagnostics == []
    assert port_facts is not None
    assert port_facts.insertion == (10.0, 20.0, 7.0)
    assert port_facts.block_base == (2.0, 4.0, 0.0)
    assert port_facts.scale == (2.0, -3.0, 1.0)

    unavailable = _row54("INSERT")
    unavailable[40] = ""
    unavailable[41] = "unavailable"
    record = _record_from_bulk_row(unavailable)
    assert record["block_base_point"] is None
    assert record["raw_properties"]["block_base_point"] is None
    assert record["raw_properties"]["transform_facts"]["block_base_point"] is None
    port_facts, port_diagnostics = _transform_facts(record)
    assert port_facts is None
    assert port_diagnostics[0]["code"] == "missing_block_transform_facts"
    assert "block_base_point" in port_diagnostics[0]["missing_facts"]

    xref = _row54("INSERT")
    xref[28] = "external_reference_geometry_not_embedded"
    xref[49] = "4"
    xref[50] = r"refs\\network.dwg"
    xref[51] = "xref"
    xref[53] = "inventory_only"
    record = _record_from_bulk_row(xref)
    assert record["block_flags"] == 4
    assert record["external_reference_path"] == r"refs\network.dwg"
    assert record["external_reference_status"] == "xref"
    assert record["inventory_support_status"] == "inventory_only"


def test_com_insert_is_explicit_and_missing_base_never_becomes_origin():
    complete = SimpleNamespace(
        ObjectName="AcDbBlockReference", EffectiveName="CABINET", Name="CABINET",
        InsertionPoint=(100.0, 200.0, 3.0), BlockBasePoint=(2.0, 4.0, 0.0),
        Normal=(0.0, 0.0, 1.0), ExtrusionDirection=(0.0, 0.0, 1.0),
        XScaleFactor=2.0, YScaleFactor=-3.0, ZScaleFactor=1.0,
        Rotation=0.5, IsXRef=False, Handle="I1", OwnerHandle="OWNER",
    )
    record = extract_com_entity(complete, "BLOCKDEF:PARENT", "block_definition")
    facts = record["raw_properties"]["transform_facts"]
    assert facts["insertion_point"] == [100.0, 200.0, 3.0]
    assert facts["block_base_point"] == [2.0, 4.0, 0.0]
    assert facts["scale"] == [2.0, -3.0, 1.0]
    assert facts["rotation"] == pytest.approx(0.5)
    assert facts["normal"] == [0.0, 0.0, 1.0]
    assert facts["extrusion"] == [0.0, 0.0, 1.0]
    assert facts["container_block_name"] == "PARENT"

    missing = SimpleNamespace(
        ObjectName="AcDbBlockReference", EffectiveName="CABINET", Name="CABINET",
        InsertionPoint=(100.0, 200.0, 3.0),
        XScaleFactor=1.0, YScaleFactor=1.0, ZScaleFactor=1.0,
        Rotation=0.0, IsXRef=False, Handle="I2",
    )
    record = extract_com_entity(missing, "Model", "model")
    facts = record["raw_properties"]["transform_facts"]
    assert facts["block_base_point"] is None
    assert facts["block_base_point_status"] == "unavailable"
    assert facts["normal"] is None
    assert facts["extrusion"] is None
    assert "block_base_point_unavailable_in_com_backend" in raw_reason_prefixes(record)


def raw_reason_prefixes(record) -> str:
    return "|".join(record["raw_properties"]["unsupported_reasons"])


@pytest.mark.parametrize("object_name", ["AcDbHatch", "AcDbProxyEntity"])
def test_com_unsupported_objects_remain_observable(object_name):
    record = extract_com_entity(
        SimpleNamespace(ObjectName=object_name, Handle="U1", Layer="UNSUPPORTED"),
        "Model",
        "model",
    )
    assert record is not None
    assert record["points"] == []
    assert record["geometry_status"] == "unavailable"
    assert record["inventory_support_status"] == "inventory_only"
    assert "geometry_unsupported_in_com_backend" in (
        record["raw_properties"]["unsupported_reasons"]
    )


def test_com_xref_and_block_record_are_observable_without_geometry():
    xref = SimpleNamespace(
        ObjectName="AcDbBlockReference", EffectiveName="SITE-XREF", Name="SITE-XREF",
        InsertionPoint=(1.0, 2.0, 0.0), BlockBasePoint=(0.5, 0.5, 0.0),
        Normal=(0.0, 0.0, 1.0), ExtrusionDirection=(0.0, 0.0, 1.0),
        XScaleFactor=1.0, YScaleFactor=1.0, ZScaleFactor=1.0, Rotation=0.0,
        IsXRef=True, IsXRefOverlay=False, XRefPath=r"refs\site.dwg", Handle="X1",
    )
    record = extract_com_entity(xref, "Model", "model")
    assert record["external_reference_status"] == "xref"
    assert record["external_reference_path"] == r"refs\site.dwg"
    assert record["inventory_support_status"] == "inventory_only"

    class Collection:
        def __init__(self, values):
            self.values = list(values)
            self.Count = len(self.values)

        def Item(self, index):
            return self.values[index]

    block = SimpleNamespace(
        Name="SITE-XREF", IsLayout=False, Origin=(0.5, 0.5, 0.0),
        IsXRef=True, IsXRefOverlay=False, Path=r"refs\site.dwg", Handle="BR1",
        Count=0,
        Item=lambda _index: None,
    )
    instance_without_inline_base = SimpleNamespace(
        ObjectName="AcDbBlockReference", EffectiveName="SITE-XREF", Name="SITE-XREF",
        InsertionPoint=(10.0, 20.0, 0.0), Normal=(0.0, 0.0, 1.0),
        ExtrusionDirection=(0.0, 0.0, 1.0), XScaleFactor=1.0,
        YScaleFactor=1.0, ZScaleFactor=1.0, Rotation=0.0, Handle="X2",
    )
    database = SimpleNamespace(
        ModelSpace=Collection((instance_without_inline_base,)),
        Layouts=Collection(()), Blocks=Collection((block,)),
    )
    grouped = _collect_records(database)
    block_records = [
        item for _, _, records in grouped for item in records
        if item["dwg_type_name"] == "BLOCK_RECORD"
    ]
    assert len(block_records) == 1
    assert block_records[0]["block_base_point"] == (0.5, 0.5, 0.0)
    assert block_records[0]["external_reference_status"] == "xref"
    assert block_records[0]["geometry_status"] == "unavailable"
    model_insert = next(
        item for _, _, records in grouped for item in records
        if item["handle"] == "X2"
    )
    assert model_insert["block_base_point"] == (0.5, 0.5, 0.0)
    assert model_insert["external_reference_status"] == "xref"


def test_autolisp_inventory_emits_unsupported_and_block_definition_facts():
    assert "c2g-write-block-record" in _AUTOLISP_EXTRACTOR
    assert "geometry_unsupported_in_bulk_backend" in _AUTOLISP_EXTRACTOR
    assert "external_reference_geometry_not_embedded" in _AUTOLISP_EXTRACTOR
    assert "(c2g-point3 blockbase)" in _AUTOLISP_EXTRACTOR
    assert '"block_definition_record"' in _AUTOLISP_EXTRACTOR
    entity_writer = _AUTOLISP_EXTRACTOR.split(
        "(defun c2g-write-entity", 1,
    )[1].split("(defun c2g-write-block-record", 1)[0]
    block_writer = _AUTOLISP_EXTRACTOR.split(
        "(defun c2g-write-block-record", 1,
    )[1].split("(defun cad2gis-export", 1)[0]
    assert entity_writer.count("(chr 9)") == 53
    assert block_writer.count("(chr 9)") == 53
    assert _AUTOLISP_EXTRACTOR.count("(") == _AUTOLISP_EXTRACTOR.count(")")
