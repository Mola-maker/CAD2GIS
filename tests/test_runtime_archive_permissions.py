"""A source runtime must remain executable after archive extraction."""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import tarfile

import pytest

from cad2gis import native_runtime


def _packager_extract(archive: Path, destination: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "tools/build_runtime_bundle.py"
    spec = importlib.util.spec_from_file_location("runtime_bundle_permissions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.extract(archive, destination)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode contract")
@pytest.mark.parametrize("extract", [native_runtime._safe_extract_tar, _packager_extract])
def test_extracted_build_script_can_execute_without_unsafe_modes(tmp_path, extract):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        script = b"#!/bin/sh\nprintf 'configure-ok'\n"
        member = tarfile.TarInfo("source/configure")
        member.mode = 0o6777
        member.size = len(script)
        bundle.addfile(member, io.BytesIO(script))
        data = tarfile.TarInfo("source/README")
        data.mode = 0o666
        data.size = 4
        bundle.addfile(data, io.BytesIO(b"data"))
    output = tmp_path / "runtime"
    extract(archive, output)
    executable = output / "source/configure"
    result = subprocess.run([str(executable)], capture_output=True, text=True, check=True)
    assert result.stdout == "configure-ok"
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755
    assert stat.S_IMODE((output / "source/README").stat().st_mode) == 0o644


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_native_source_archive_rejects_nonregular_entries(tmp_path, kind):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("source/special")
        member.type = kind
        member.linkname = "../../outside"
        bundle.addfile(member)
    with pytest.raises(RuntimeError, match="Unsafe"):
        native_runtime._safe_extract_tar(archive, tmp_path / "runtime")
