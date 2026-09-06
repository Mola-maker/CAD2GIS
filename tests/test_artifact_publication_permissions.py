"""Published run bundles must inherit their destination parent's Windows ACL."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3 import pipeline


@pytest.mark.parametrize("existing", [False, True])
def test_acl_failure_keeps_current_bundle_and_staging_untouched(tmp_path, monkeypatch, existing):
    staged = Path(tempfile.mkdtemp(prefix=".run.staged-", dir=tmp_path))
    (staged / "delivery.gpkg").write_bytes(b"new completed delivery")
    destination = tmp_path / "run"
    if existing:
        destination.mkdir()
        (destination / "delivery.gpkg").write_bytes(b"previous delivery")

    def deny_inheritance(path):
        assert path == staged
        assert (path / "delivery.gpkg").read_bytes() == b"new completed delivery"
        raise PermissionError("cannot inherit output ACL")

    monkeypatch.setattr(pipeline, "inherit_output_permissions", deny_inheritance)
    with pytest.raises(PermissionError, match="output ACL"):
        pipeline._publish_run_bundle(staged, destination)

    assert (staged / "delivery.gpkg").read_bytes() == b"new completed delivery"
    assert not list(tmp_path.glob(".run.backup.*"))
    if existing:
        assert (destination / "delivery.gpkg").read_bytes() == b"previous delivery"
    else:
        assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL publication regression")
def test_windows_canonical_bundle_inherits_parent_acl_recursively(tmp_path):
    import ctypes
    import subprocess

    name = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(len(name))
    assert ctypes.windll.advapi32.GetUserNameW(name, ctypes.byref(size))
    subprocess.run(
        ["icacls.exe", str(tmp_path), "/grant", f"{name.value}:(OI)(CI)(RX)"],
        capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    staged = Path(tempfile.mkdtemp(prefix=".run.staged-", dir=tmp_path))
    (staged / "nested").mkdir()
    (staged / "nested" / "delivery.gpkg").write_bytes(b"closed delivery fixture")

    def inherited_owner(path):
        permissions = subprocess.run(
            ["icacls.exe", str(path)], capture_output=True, text=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return any(name.value.casefold() in line.casefold() and "(I)" in line
                   for line in permissions.stdout.splitlines())

    # Confirm the actual Python/Windows behavior under test, rather than merely
    # observing an ACL that was already inherited before publication.
    assert not inherited_owner(staged)
    destination = tmp_path / "run"
    pipeline._publish_run_bundle(staged, destination)
    assert inherited_owner(destination)
    assert inherited_owner(destination / "nested")
    assert inherited_owner(destination / "nested" / "delivery.gpkg")
    assert (destination / "nested" / "delivery.gpkg").read_bytes() == b"closed delivery fixture"
    assert not staged.exists()
