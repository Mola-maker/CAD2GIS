from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad2gis import native_runtime
from cad2gis.reader.contracts import ReaderCapability


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return payload.getvalue()


def test_runtime_install_is_checksum_pinned_and_cache_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _zip_bytes({"dwg2dxf.exe": b"portable-reader", "libredwg.dll": b"dll"})
    monkeypatch.setenv("CAD2GIS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv(native_runtime.LIBREDWG_CLI_ENV, raising=False)
    monkeypatch.setattr(native_runtime.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        native_runtime,
        "LIBREDWG_WINDOWS_X64_SHA256",
        hashlib.sha256(bundle).hexdigest(),
    )
    monkeypatch.setattr(
        native_runtime.urllib.request,
        "urlopen",
        lambda _request, timeout: io.BytesIO(bundle),
    )

    result = native_runtime.install_portable_runtime()

    executable = Path(result["libredwg"]["executable"])
    assert result["install_status"] == "installed"
    assert executable == tmp_path / "cache" / "runtime" / "libredwg-0.14" / "dwg2dxf.exe"
    assert executable.read_bytes() == b"portable-reader"
    assert (executable.parent / "cad2gis-runtime.json").is_file()


def test_runtime_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    archive.write_bytes(_zip_bytes({"../outside.txt": b"escape"}))

    with pytest.raises(RuntimeError, match="Unsafe path"):
        native_runtime._safe_extract(archive, tmp_path / "runtime")

    assert not (tmp_path / "outside.txt").exists()


def test_default_resolver_uses_cli_without_selecting_autocad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cad2gis.reader import libredwg, libredwg_cli, resolver

    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"dwg")
    sentinel = ["portable"]
    monkeypatch.delenv(resolver.READER_ENV, raising=False)
    monkeypatch.setattr(
        libredwg,
        "libredwg_capability",
        lambda: ReaderCapability("libredwg", False, "bindings missing", "optional"),
    )
    monkeypatch.setattr(
        libredwg_cli,
        "libredwg_cli_capability",
        lambda: ReaderCapability("libredwg-cli", True, "CLI ready", "none"),
    )
    monkeypatch.setattr(libredwg_cli, "extract_dwg_records", lambda _source: sentinel)

    assert resolver.extract_records(source) is sentinel


def test_libredwg_cli_adapter_preserves_model_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ezdxf

    from cad2gis.reader import libredwg_cli

    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"test-dwg")
    executable = tmp_path / "dwg2dxf.exe"
    executable.write_bytes(b"stub")
    monkeypatch.setenv(native_runtime.LIBREDWG_CLI_ENV, str(executable))

    def convert(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        output = Path(arguments[arguments.index("-o") + 1])
        document = ezdxf.new("R2018")
        model = document.modelspace()
        model.add_line((0, 0), (3, 4))
        model.add_text("label", dxfattribs={"insert": (1, 2)})
        block = document.blocks.new("TEST_BLOCK", base_point=(1, 2, 0))
        block.add_line((0, 0), (1, 0))
        model.add_blockref("TEST_BLOCK", (5, 5))
        document.saveas(output)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", convert)

    records = libredwg_cli.extract_dwg_records(source)
    model = [record for record in records if record.get("cad_role") == "model"]

    assert len(model) == 3
    assert {record["dwg_type_name"] for record in model} == {"LINE", "TEXT", "INSERT"}
    insert = next(record for record in model if record["dwg_type_name"] == "INSERT")
    transform = insert["raw_properties"]["transform_facts"]
    assert transform["block_base_point"] == [1.0, 2.0, 0.0]
    assert transform["block_base_point_status"] == "available"
    assert records.diagnostics["inventory_complete"] is True
    assert records.diagnostics["skipped_rows"] == 0
    assert records.diagnostics["intermediate_persisted"] is False
    metadata = next(record for record in records if record["dwg_type_name"] == "DOCUMENT_METADATA")
    assert "CGEOCS=" not in metadata["text"]
    assert metadata["raw_properties"]["metadata_evidence"] == "partial"


def test_legacy_apd_profile_can_gate_current_semantic_cable_collections() -> None:
    from cad2gis.cad2gis_v3.config import SourceProfile

    profile_path = (
        Path(__file__).resolve().parents[1]
        / "experiment"
        / "config"
        / "apd_source_profile.json"
    )
    expectations = SourceProfile.load(profile_path).expectations.feature_counts

    assert expectations["CABLE"] == 31
    assert expectations["INFRASTRUCTURE"] == 31


def test_libredwg_cli_prefers_dxf_actual_dimension_measurement() -> None:
    from cad2gis.reader import libredwg_cli

    entity = SimpleNamespace(
        dxf=SimpleNamespace(
            defpoint2=(0.0, 0.0, 0.0),
            defpoint3=(0.0, 89.0, 0.0),
            actual_measurement=89.0,
            text="",
        ),
        get_measurement=lambda: 4.0,
    )

    geometry = libredwg_cli._entity_geometry(None, entity, "DIMENSION")

    assert geometry["dimension_value"] == 89.0
