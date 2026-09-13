"""Exercise the SWIG reader boundary without requiring a native DWG install."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad2gis.cad2gis_v3.ingest import ingest
from cad2gis.reader import libredwg


def _entity(handle: int) -> SimpleNamespace:
    return SimpleNamespace(
        supertype=1,
        type=19,
        handle=SimpleNamespace(value=handle),
        tio=SimpleNamespace(entity=SimpleNamespace(this=handle)),
    )


@pytest.fixture
def native_reader(monkeypatch, tmp_path):
    """Mock native calls, retaining the real extraction and cursor workflow."""
    source = tmp_path / "fixture.dwg"
    source.write_bytes(b"AC1032: synthetic native-reader status fixture")
    original_mkstemp = libredwg.tempfile.mkstemp
    original_flush = libredwg._flush_cursor
    monkeypatch.delenv("CAD2GIS_LAYOUT", raising=False)
    monkeypatch.setattr(libredwg, "_require_libredwg", lambda: None)
    monkeypatch.setattr(libredwg, "DWG_SUPERTYPE_ENTITY", 1)
    monkeypatch.setattr(libredwg, "DWG_TYPE_ATTRIB", None)
    monkeypatch.setattr(libredwg, "_DWG_TYPE_NAME_MAP", {19: "LINE"})
    monkeypatch.setattr(libredwg, "_type_name", lambda _type: "LINE")
    monkeypatch.setattr(
        libredwg.tempfile, "mkstemp",
        lambda **kwargs: original_mkstemp(dir=tmp_path, **kwargs),
    )
    monkeypatch.setattr(
        libredwg, "_resolve_layout",
        lambda *_args, **_kwargs: ("Model", "model", "model", []),
    )
    monkeypatch.setattr(
        libredwg, "_build_record",
        lambda **kwargs: {
            "handle": f"{kwargs['obj'].handle.value:X}",
            "raw_properties": {"unsupported_reasons": []},
        },
    )

    def configure(read_error: int, *, objects=None, inaccessible=()):
        objects = [_entity(1)] if objects is None else objects
        calls = {"objects": [], "metadata": [], "cursors": []}
        data = SimpleNamespace(
            num_objects=len(objects), header_vars=SimpleNamespace(INSUNITS=6),
        )
        monkeypatch.setattr(libredwg, "Dwg_Data", lambda: data)
        monkeypatch.setattr(libredwg, "new_Dwg_Object_Array", lambda _size: objects)
        monkeypatch.setattr(libredwg, "dwg_read_file", lambda _source, _data: read_error)

        def object_at(array, index):
            calls["objects"].append(index)
            if index in inaccessible:
                raise OSError("native object inaccessible")
            return array[index]

        def metadata(name, value):
            def read(*_args, **_kwargs):
                calls["metadata"].append(name)
                return value
            return read

        def flush(diagnostics, path):
            calls["cursors"].append(json.loads(json.dumps(diagnostics)))
            original_flush(diagnostics, path)

        monkeypatch.setattr(libredwg, "Dwg_Object_Array_getitem", object_at)
        monkeypatch.setattr(libredwg, "_flush_cursor", flush)
        monkeypatch.setattr(
            libredwg, "_read_anon_block_names_json", metadata("names", ({}, {})),
        )
        monkeypatch.setattr(
            libredwg, "_read_entity_layout_map_json", metadata("layouts", {}),
        )
        monkeypatch.setattr(
            libredwg, "_read_block_header_metadata", metadata("blocks", ({}, {}, {})),
        )
        monkeypatch.setattr(libredwg, "_read_layer_styles", metadata("styles", {}))
        return source, calls

    return configure


def _assert_persisted(diagnostics, source):
    assert diagnostics["source_file"] == str(source.resolve())
    assert diagnostics["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert json.loads(Path(diagnostics["cursor_path"]).read_text(encoding="utf-8")) == diagnostics


def test_success_only_claims_complete_after_traversal(native_reader):
    source, calls = native_reader(0)

    records = libredwg.extract_dwg_records(source)

    assert [record["handle"] for record in records] == ["DOCUMENT_METADATA", "1"]
    diagnostics = records.diagnostics
    assert diagnostics["libredwg_read_status"] == "ok"
    assert diagnostics["libredwg_read_error_flags"] == []
    assert diagnostics["libredwg_read_unknown_bits"] == 0
    assert diagnostics["inventory_complete"] is True
    assert diagnostics["skipped_rows"] == 0
    assert diagnostics["processed_entities"] == 1
    assert diagnostics["returned_records"] == 2
    assert calls["cursors"][0]["inventory_complete"] is False
    assert calls["cursors"][-1]["inventory_complete"] is True
    _assert_persisted(diagnostics, source)


# Literal values/names from LibreDWG 0.14 include/dwg.h, independent of the
# implementation table. In particular 3 and 68 are warning combinations.
@pytest.mark.parametrize(("code", "flags"), [
    (1, ["DWG_ERR_WRONGCRC"]),
    (2, ["DWG_ERR_NOTYETSUPPORTED"]),
    (4, ["DWG_ERR_UNHANDLEDCLASS"]),
    (8, ["DWG_ERR_INVALIDTYPE"]),
    (16, ["DWG_ERR_INVALIDHANDLE"]),
    (32, ["DWG_ERR_INVALIDEED"]),
    (64, ["DWG_ERR_VALUEOUTOFBOUNDS"]),
    (3, ["DWG_ERR_WRONGCRC", "DWG_ERR_NOTYETSUPPORTED"]),
    (68, ["DWG_ERR_UNHANDLEDCLASS", "DWG_ERR_VALUEOUTOFBOUNDS"]),
])
def test_warning_returns_inspectable_records_without_claiming_completeness(
    native_reader, code, flags,
):
    source, calls = native_reader(code)

    records = libredwg.extract_dwg_records(source)

    assert [record["handle"] for record in records] == ["DOCUMENT_METADATA", "1"]
    assert calls["objects"] == [0]
    diagnostics = records.diagnostics
    assert diagnostics["libredwg_read_error"] == code
    assert diagnostics["libredwg_read_error_flags"] == flags
    assert diagnostics["libredwg_read_status"] == "warning"
    assert diagnostics["inventory_complete"] is False
    assert diagnostics["skipped_rows"] == 0
    _assert_persisted(diagnostics, source)

    # Recovering records for inspection must not bypass authoritative ingest.
    profile = SimpleNamespace(validate_source=lambda _source: diagnostics["source_sha256"])
    with pytest.raises(RuntimeError, match="Authoritative reader inventory is incomplete"):
        ingest(source, profile, extract_records=lambda _source: records)


@pytest.mark.parametrize(("code", "flags"), [
    (128, ["DWG_ERR_CLASSESNOTFOUND"]),
    (256, ["DWG_ERR_SECTIONNOTFOUND"]),
    (512, ["DWG_ERR_PAGENOTFOUND"]),
    (1024, ["DWG_ERR_INTERNALERROR"]),
    (2048, ["DWG_ERR_INVALIDDWG"]),
    (4096, ["DWG_ERR_IOERROR"]),
    (8192, ["DWG_ERR_OUTOFMEM"]),
    (4100, ["DWG_ERR_UNHANDLEDCLASS", "DWG_ERR_IOERROR"]),
    (10304, ["DWG_ERR_VALUEOUTOFBOUNDS", "DWG_ERR_INVALIDDWG", "DWG_ERR_OUTOFMEM"]),
])
def test_critical_bits_abort_before_native_traversal_and_retain_evidence(
    native_reader, code, flags,
):
    source, calls = native_reader(code)

    with pytest.raises(libredwg.LibreDWGReadError) as caught:
        libredwg.extract_dwg_records(source)

    assert calls["objects"] == []
    assert calls["metadata"] == []
    diagnostics = caught.value.diagnostics
    assert diagnostics["libredwg_read_error"] == code
    assert diagnostics["libredwg_read_error_flags"] == flags
    assert diagnostics["libredwg_read_status"] == "critical"
    assert diagnostics["inventory_complete"] is False
    assert str(code) in str(caught.value)
    for flag in flags:
        assert flag in str(caught.value)
    _assert_persisted(diagnostics, source)


@pytest.mark.parametrize(("code", "unknown_bits"), [(16384, 16384), (16385, 16384), (-1, None)])
def test_unknown_status_fails_closed(native_reader, code, unknown_bits):
    source, calls = native_reader(code)

    with pytest.raises(libredwg.LibreDWGReadError, match="unknown native status") as caught:
        libredwg.extract_dwg_records(source)

    diagnostics = caught.value.diagnostics
    assert diagnostics["libredwg_read_error"] == code
    assert diagnostics["libredwg_read_unknown_bits"] == unknown_bits
    assert diagnostics["libredwg_read_status"] == "unknown"
    assert diagnostics["inventory_complete"] is False
    assert calls["objects"] == []
    assert calls["metadata"] == []
    _assert_persisted(diagnostics, source)


def test_object_read_failure_keeps_survivors_but_marks_inventory_incomplete(native_reader):
    source, _calls = native_reader(0, objects=[_entity(1), _entity(2)], inaccessible=(0,))

    records = libredwg.extract_dwg_records(source)

    assert [record["handle"] for record in records] == ["DOCUMENT_METADATA", "2"]
    diagnostics = records.diagnostics
    assert diagnostics["inventory_complete"] is False
    assert diagnostics["skipped_rows"] == 1
    assert diagnostics["object_access_failures"] == 1
    assert diagnostics["crash_count"] == 0
    assert diagnostics["reader_failures"] == [{
        "object_index": 0, "stage": "object_access", "exception_type": "OSError",
        "message": "native object inaccessible",
    }]
    assert diagnostics["unsupported_reason_counts"] == {"libredwg_object_access_failed[OSError]": 1}
    _assert_persisted(diagnostics, source)


def test_entity_exception_counts_skips_and_preserves_failure(native_reader, monkeypatch):
    source, _calls = native_reader(0, objects=[_entity(1), _entity(2)])
    build_record = libredwg._build_record

    def build(**kwargs):
        if kwargs["obj"].handle.value == 1:
            raise ValueError("invalid native geometry")
        return build_record(**kwargs)

    monkeypatch.setattr(libredwg, "_build_record", build)

    records = libredwg.extract_dwg_records(source)

    assert [record["handle"] for record in records] == ["DOCUMENT_METADATA", "2"]
    diagnostics = records.diagnostics
    assert diagnostics["inventory_complete"] is False
    assert diagnostics["skipped_rows"] == 1
    assert diagnostics["crash_count"] == 1
    assert diagnostics["object_access_failures"] == 0
    assert diagnostics["reader_failures"] == [{
        "object_index": 0, "stage": "entity_record", "exception_type": "ValueError",
        "message": "invalid native geometry",
    }]
    assert diagnostics["unsupported_reason_counts"] == {"libredwg_reader_crash[ValueError]": 1}
    _assert_persisted(diagnostics, source)


def test_nonentity_objects_are_not_reported_as_lost_rows(native_reader):
    source, _calls = native_reader(0, objects=[SimpleNamespace(supertype=2), _entity(1)])

    records = libredwg.extract_dwg_records(source)

    assert [record["handle"] for record in records] == ["DOCUMENT_METADATA", "1"]
    assert records.diagnostics["inventory_complete"] is True
    assert records.diagnostics["skipped_rows"] == 0
    assert records.diagnostics["reader_failures"] == []
