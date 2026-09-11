import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from cad2gis.cad2gis_v3.source_export import export_source


spec = importlib.util.spec_from_file_location(
    "source_relocator", Path(__file__).resolve().parents[1] / "tools/relocate_source_snapshot.py")
relocator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relocator)


@pytest.fixture
def copied_snapshot(tmp_path):
    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"explicit synthetic fixture")
    original = tmp_path / "original"
    manifest = export_source(source=source, run_dir=original, records=[{
        "entity_key": "a", "dwg_type_name": "LINE", "layout": "Model", "cad_role": "model",
        "points": [[0, 0], [3, 4]], "native_length": 5,
    }])
    copied = tmp_path / "copied"
    shutil.copytree(original, copied)
    return original, copied, manifest


def test_relocation_preserves_seal_and_does_not_upgrade_reader_authority(copied_snapshot):
    original, copied, manifest = copied_snapshot
    previous = (original / "source_manifest.json").read_bytes()
    receipt = relocator.relocate(copied)
    relocated = json.loads((copied / "source_manifest.json").read_text())
    assert {entry["artifact"] for entry in receipt["changes"]} == set(manifest["artifacts"])
    assert relocated["snapshot_sha256"] == manifest["snapshot_sha256"]
    assert relocated["reader_provenance"] == manifest["reader_provenance"]
    assert relocated["reader_provenance"]["mode"] == "injected_records"
    assert (original / "source_manifest.json").read_bytes() == previous
    assert next(copied.glob("source_manifest.before-relocation-*.json")).read_bytes() == previous
    assert relocator.relocate(copied)["changes"] == []


@pytest.mark.parametrize("artifact", ["reader_records.jsonl", "source.gpkg", "review/source_inventory.json", "reasoning/scene_visual/manifest.json"])
def test_corruption_never_reseals_or_overwrites_manifest(copied_snapshot, artifact):
    _, copied, _ = copied_snapshot
    previous = (copied / "source_manifest.json").read_bytes()
    with (copied / artifact).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        relocator.relocate(copied)
    assert (copied / "source_manifest.json").read_bytes() == previous
    assert not list(copied.glob("relocation-*.json"))
