"""Reject damaged installable payloads before writing Linux installers."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest


def tool(name):
    path = Path(__file__).resolve().parents[1] / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("damage", ["missing", "changed", "extra"])
def test_installer_rejects_payload_drift(tmp_path, damage):
    data = tmp_path / "program"
    data.write_bytes(b"verified")
    manifest = {"files": {"program": hashlib.sha256(b"verified").hexdigest()}}
    package = tool("build_linux_package")
    package.validate_payload(tmp_path, manifest)
    if damage == "missing":
        data.unlink()
    elif damage == "changed":
        data.write_bytes(b"corrupted")
    else:
        (tmp_path / "unexpected.pyc").write_bytes(b"cache")
    with pytest.raises(ValueError, match="missing or changed|not in the inventory"):
        package.validate_payload(tmp_path, manifest)


@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal alias regression")
def test_runtime_copy_excludes_case_broken_terminfo_and_bytecode(tmp_path):
    source, output = tmp_path / "python", tmp_path / "copied"
    terminal = source / "share/terminfo"
    (terminal / "A").mkdir(parents=True)
    (terminal / "1").mkdir()
    (terminal / "A/adm1178").write_text("terminal")
    (terminal / "1/1178").symlink_to("../a/adm1178")
    binary = source / "bin/python3"
    binary.parent.mkdir()
    binary.write_bytes(b"python")
    binary.chmod(0o755)
    cache = source / "lib/__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"bytecode")
    tool("build_runtime_bundle").copy_runtime(source, output)
    assert not (output / "share/terminfo").exists()
    assert not (output / "lib/__pycache__").exists()
    assert (output / "bin/python3").read_bytes() == b"python"
    assert (output / "bin/python3").stat().st_mode & 0o111
    assert (terminal / "1/1178").is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX bundle links")
def test_installer_rejects_uninventoried_dangling_link(tmp_path):
    data = tmp_path / "program"
    data.write_bytes(b"verified")
    manifest = {"files": {"program": hashlib.sha256(b"verified").hexdigest()}}
    (tmp_path / "broken").symlink_to("missing")
    with pytest.raises(ValueError, match="invalid symlink"):
        tool("build_linux_package").validate_payload(tmp_path, manifest)
