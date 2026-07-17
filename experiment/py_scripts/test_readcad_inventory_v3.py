"""Focused contracts for the loss-aware direct-DWG inventory boundary."""

import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from autocad_reader import (
    COM_FALLBACK_ENV,
    _AUTOLISP_EXTRACTOR,
    _authorize_com_fallback,
    _extract_records_with_core_console,
    _record_from_bulk_row,
    extract_com_entity,
)
from cad2gis_v3.evidence import write_evidence
from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.model import SourceEntity


def _bulk_row(
    kind,
    *,
    text="",
    attributes="",
    points="1,2",
    measurement="0",
    dimension_override="",
    native_length="",
):
    columns = [
        kind, "A1", "CAD", "Model", "256", "-1", "ByLayer", "-1",
        "0", "0", "*U1" if kind == "INSERT" else "", text, attributes,
        points, measurement, "0", "0", "7", "-1", "Continuous", "25",
        "2", "3", "1", "OWNER", "", dimension_override, "", "",
        native_length,
    ]
    assert len(columns) == 30
    return columns


def test_bulk_protocol_preserves_legacy_rows_and_escaped_attribute_payloads():
    row = _bulk_row(
        "INSERT",
        attributes=r"TAG=A\pB=C",
        native_length="12.75",
    )
    record = _record_from_bulk_row(row)

    assert record["block_attributes"] == {"TAG": "A|B=C"}
    assert record["scale_x"] == 2.0
    assert record["scale_y"] == 3.0
    assert record["owner_handle"] == "OWNER"
    assert record["native_length"] == pytest.approx(12.75)
    assert record["raw_properties"]["extraction_backend"] == "autocad_core_console_bulk"
    assert record["raw_properties"]["reader_backend_status"] == "authoritative"
    assert record["raw_properties"]["block_reference_name"] == "*U1"
    assert record["raw_properties"]["dynamic_block_properties_status"] == (
        "unsupported_by_core_console_bulk"
    )
    json.dumps(record["raw_properties"], sort_keys=True)

    legacy = _record_from_bulk_row(row[:17])
    assert legacy is not None
    assert legacy["native_length"] is None
    assert "legacy_bulk_protocol_without_raw_extension" in (
        legacy["raw_properties"]["unsupported_reasons"]
    )


def test_com_fallback_is_fail_closed_and_requires_explicit_opt_in(monkeypatch, capsys):
    failure = RuntimeError("core console failed")
    monkeypatch.delenv(COM_FALLBACK_ENV, raising=False)
    with pytest.raises(RuntimeError, match="COM fallback is disabled"):
        _authorize_com_fallback(failure)

    monkeypatch.setenv(COM_FALLBACK_ENV, "1")
    _authorize_com_fallback(failure)
    assert "explicitly enabled COM fallback" in capsys.readouterr().out


def test_core_console_rejects_metadata_only_partial_inventory(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.touch()
    source = tmp_path / "source.dwg"
    source.touch()

    def fake_run(command, **_kwargs):
        script = Path(command[command.index("/s") + 1]).read_text(encoding="utf-8")
        match = re.search(r'\(cad2gis-export "([^"]+)"\)', script)
        assert match is not None
        output = Path(match.group(1))
        output.write_text(
            "\t".join([
                "DOCUMENT_METADATA", "", "0", "DOCUMENT", "256", "-1",
                "ByLayer", "-1", "0", "0", "", "CGEOCS=", "", "", "0",
                "0", "0",
            ]) + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("autocad_reader.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="no CAD entity rows"):
        _extract_records_with_core_console(source, accoreconsole=executable)


@pytest.mark.parametrize(
    ("kind", "object_name", "text_source"),
    [
        ("TEXT", "ACDBTEXT", "entity_text"),
        ("MTEXT", "ACDBMTEXT", "entity_text"),
        ("ATTRIB", "ACDBATTRIBUTE", "attribute_text"),
        ("ATTDEF", "ACDBATTRIBUTEDEFINITION", "attribute_text"),
        ("MLEADER", "ACDBMLEADER", "multileader_text"),
        ("TABLE", "ACDBTABLE", "table_cells"),
        ("DIMENSION", "ACDBDIMENSION", "dimension_text_override"),
    ],
)
def test_bulk_protocol_retains_every_supported_annotation_carrier(
    kind, object_name, text_source,
):
    record = _record_from_bulk_row(
        _bulk_row(
            kind,
            # c2g-escape doubles the CAD formatting backslash for TSV.
            text=r"{LABEL\\PSECOND}",
            measurement="9.5",
            dimension_override="OVERRIDE" if kind == "DIMENSION" else "",
        )
    )
    assert record["object_name"] == object_name
    assert record["text"] == "LABEL\nSECOND"
    assert record["raw_properties"]["raw_text"] == r"{LABEL\PSECOND}"
    assert record["raw_properties"]["text_source"] == text_source
    if kind == "DIMENSION":
        assert record["dimension_value"] == pytest.approx(9.5)
        assert record["dimension_text_override"] == "OVERRIDE"
    assert kind in _AUTOLISP_EXTRACTOR


def test_autolisp_text_reader_does_not_treat_dimension_style_as_text():
    assert '(= kind "MTEXT")' in _AUTOLISP_EXTRACTOR
    assert "(= (car item) 3)" in _AUTOLISP_EXTRACTOR
    assert "(= (car item) 304)" in _AUTOLISP_EXTRACTOR
    assert '"LEADER_LINE*"' in _AUTOLISP_EXTRACTOR
    assert "(c2g-text data kind)" in _AUTOLISP_EXTRACTOR


def test_com_inventory_preserves_effective_and_raw_dynamic_block_facts():
    class Attribute:
        def __init__(self, tag, value):
            self.TagString = tag
            self.TextString = value

    class DynamicProperty:
        PropertyName = "Visibility"
        Value = "POLE"
        ReadOnly = False
        AllowedValues = ("POLE", "HANDHOLE")

    class Entity:
        ObjectName = "AcDbDynamicBlockReference"
        EffectiveName = "POLE_SYMBOL"
        Name = "*U14"
        InsertionPoint = (10.0, 20.0, 0.0)
        Handle = "B1"
        OwnerID = "OWNER-B"
        Layer = "EXISTING POLE"
        XScaleFactor = 2.0
        YScaleFactor = 3.0
        ZScaleFactor = 1.0
        IsDynamicBlock = True

        def GetConstantAttributes(self):
            return (Attribute("TYPE", "CONSTANT"),)

        def GetAttributes(self):
            return (Attribute("CODE", "P050"),)

        def GetDynamicBlockProperties(self):
            return (DynamicProperty(),)

    record = extract_com_entity(Entity(), "Model", "model")
    raw = record["raw_properties"]
    assert record["block_name"] == "POLE_SYMBOL"
    assert record["block_attributes"] == {"TYPE": "CONSTANT", "CODE": "P050"}
    assert raw["block_effective_name"] == "POLE_SYMBOL"
    assert raw["block_reference_name"] == "*U14"
    assert raw["dynamic_block_properties_status"] == "available"
    assert raw["dynamic_block_properties"]["Visibility"]["value"] == "POLE"
    assert raw["reader_backend_status"] == "com_direct"
    json.dumps(raw, sort_keys=True)


def test_com_text_table_dimension_and_native_length_survive_source_model_boundary():
    class Table:
        ObjectName = "AcDbTable"
        Rows = 2
        Columns = 2
        InsertionPoint = (1.0, 2.0, 0.0)

        @staticmethod
        def GetText(row, column):
            return (("A", "B"), ("C", "D"))[row][column]

    carriers = [
        SimpleNamespace(
            ObjectName="AcDbText", TextString="TEXT", InsertionPoint=(1, 2, 0)
        ),
        SimpleNamespace(
            ObjectName="AcDbMText", TextString=r"MTEXT\P2", InsertionPoint=(1, 2, 0)
        ),
        SimpleNamespace(
            ObjectName="AcDbAttribute", TextString="ATTRIB", TagString="TAG",
            InsertionPoint=(1, 2, 0),
        ),
        SimpleNamespace(
            ObjectName="AcDbAttributeDefinition", TextString="ATTDEF", TagString="TAG",
            InsertionPoint=(1, 2, 0),
        ),
        SimpleNamespace(
            ObjectName="AcDbMLeader", TextString="MLEADER", TextLocation=(1, 2, 0)
        ),
        Table(),
        SimpleNamespace(
            ObjectName="AcDbAlignedDimension", Measurement=8.25, TextOverride="SPAN",
            XLine1Point=(0, 0, 0), XLine2Point=(8.25, 0, 0),
        ),
    ]
    records = [extract_com_entity(entity, "Model", "model") for entity in carriers]
    assert [record["text"] for record in records] == [
        "TEXT", "MTEXT\n2", "ATTRIB", "ATTDEF", "MLEADER", "A\tB\nC\tD", "SPAN",
    ]
    assert records[-1]["dimension_value"] == pytest.approx(8.25)

    line = extract_com_entity(
        SimpleNamespace(
            ObjectName="AcDbLine", StartPoint=(0, 0, 0), EndPoint=(3, 4, 0),
            Length=5.0, Handle="L1",
        ),
        "Model",
        "model",
        reader_backend_status="fallback_after_core_console_failure",
    )
    line.update(
        entity_key="entity-L1", source_sha256="sha", source_file="source.dwg"
    )
    entity = SourceEntity.from_record(line)
    assert entity.native_length == pytest.approx(5.0)
    assert entity.owner_handle == ""
    assert entity.extraction_backend == "autocad_com"
    assert entity.reader_backend_status == "fallback_after_core_console_failure"
    assert entity.raw_properties["native_length_source"] == "autocad_com:Length"
    json.dumps(entity.raw_properties, sort_keys=True)


def test_lossless_instance_and_annotation_facts_are_persisted_for_curation(tmp_path):
    insert_record = _record_from_bulk_row(
        _bulk_row("INSERT", attributes=r"CODE=POLE-1", points="10,20")
    )
    insert_record.update(
        entity_key="ENTITY-INSERT", source_sha256="source", source_file="source.dwg"
    )
    text_record = _record_from_bulk_row(
        _bulk_row("MTEXT", text=r"POLE\pLABEL", points="11,21")
    )
    text_record.update(
        entity_key="ENTITY-TEXT", source_sha256="source", source_file="source.dwg"
    )
    entities = [
        SourceEntity.from_record(insert_record),
        SourceEntity.from_record(text_record),
    ]
    evidence = tmp_path / "inventory.gpkg"
    write_evidence(
        evidence, entities, [], [], [], {},
        DirectTransformer("EPSG:3857", "EPSG:9481").source,
    )
    with sqlite3.connect(evidence) as connection:
        entity_row = connection.execute(
            "SELECT extraction_backend, reader_backend_status, raw_properties "
            "FROM cad_entities WHERE entity_key='ENTITY-INSERT'"
        ).fetchone()
        block_row = connection.execute(
            "SELECT raw_block_name, effective_block_name, block_attributes, raw_properties "
            "FROM block_instances WHERE entity_key='ENTITY-INSERT'"
        ).fetchone()
        carrier_row = connection.execute(
            "SELECT carrier_type, text, text_source, raw_properties "
            "FROM annotation_carriers WHERE entity_key='ENTITY-TEXT'"
        ).fetchone()
    assert entity_row[:2] == ("autocad_core_console_bulk", "authoritative")
    assert json.loads(entity_row[2])["schema_version"] == "cad2gis-raw-properties-v1"
    assert block_row[0] == "*U1"
    assert block_row[1] == "*U1"
    assert json.loads(block_row[2]) == {"CODE": "POLE-1"}
    assert json.loads(block_row[3])["block_reference_name"] == "*U1"
    assert carrier_row[:3] == ("MTEXT", "POLE|LABEL", "entity_text")
    assert json.loads(carrier_row[3])["raw_text"] == "POLE|LABEL"
