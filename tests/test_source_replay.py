import json

import pytest

from cad2gis.cad2gis_v3 import source_export, source_replay
from cad2gis.reader.libredwg_cli import DWGRecordInventory


def native_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source.dwg"
    source.write_bytes(b"native reader test")
    records = DWGRecordInventory([{
        "entity_key": "A", "source_sha256": source_export._sha256(source), "handle": "A",
        "dwg_type_name": "LINE", "cad_role": "model", "layout": "Model", "layer": "CABLE",
        "points": [[100000000.12345678, 2.25], [100000002.98765433, 4.75]],
    }], diagnostics={"inventory_complete": True, "skipped_rows": 0, "backend": "test_native_port"})
    monkeypatch.setattr(source_export, "_extract_records", lambda _: records)
    root = tmp_path / "snapshot"
    manifest = source_export.export_source(source=source, run_dir=root)
    return source, root, manifest, records


def test_native_replay_does_not_read_dwg_and_preserves_full_precision(tmp_path, monkeypatch):
    source, root, manifest, original = native_snapshot(tmp_path, monkeypatch)
    monkeypatch.setattr(source_export, "_extract_records", lambda _: pytest.fail("Reader invoked during replay"))
    records, receipt = source_replay.load_native_snapshot(root, source)
    assert records == original
    assert records.diagnostics == original.diagnostics
    assert receipt["snapshot_sha256"] == manifest["snapshot_sha256"]
    assert receipt["reader_invoked"] is False


@pytest.mark.parametrize("mutation", ["other_source", "records", "injected", "old_reader", "wrong_count"])
def test_replay_rejects_stale_or_wrong_authority(tmp_path, monkeypatch, mutation):
    source, root, manifest, _ = native_snapshot(tmp_path, monkeypatch)
    if mutation == "other_source":
        source.write_bytes(b"other drawing")
    elif mutation == "records":
        with (root / "reader_records.jsonl").open("a") as stream:
            stream.write('{}\n')
    elif mutation == "injected":
        manifest["reader_provenance"]["mode"] = "injected_records"
    elif mutation == "old_reader":
        manifest["reader_provenance"]["reader_identity"] = "old-reader"
    elif mutation == "wrong_count":
        manifest["entity_count"] += 1
    manifest["snapshot_sha256"] = source_export.snapshot_digest(manifest)
    (root / "source_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        source_replay.load_native_snapshot(root, source)
