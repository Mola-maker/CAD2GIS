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
            ["icacls.exe", str(path)], capture_output=True, text=True, encoding="oem", check=True,
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


def test_style_field_inspection_releases_database_before_bundle_publication(tmp_path, monkeypatch):
    import sqlite3
    from cad2gis.cad2gis_v3 import styles, warehouse
    from cad2gis.cad2gis_v3.georef import DirectTransformer

    staged = tmp_path / ".stage"
    staged.mkdir()
    delivery = staged / "delivery.gpkg"
    warehouse.write_delivery(delivery, [], DirectTransformer("EPSG:3857", "EPSG:3857"))
    connect = sqlite3.connect
    retained_connections = []

    def track(*args, **kwargs):
        connection = connect(*args, **kwargs)
        if kwargs.get("uri"):
            # Keeping references makes the test independent of garbage
            # collection, which otherwise hides open SQLite handles.
            retained_connections.append(connection)
        return connection

    monkeypatch.setattr(styles.sqlite3, "connect", track)
    try:
        styles.write_styles(staged / "styles", [], delivery)
        assert retained_connections
        for connection in retained_connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
        pipeline._publish_run_bundle(staged, tmp_path / "published")
        assert (tmp_path / "published/delivery.gpkg").is_file()
    finally:
        for connection in retained_connections:
            connection.close()


def test_persistent_windows_publication_error_is_bounded_and_preserves_files(tmp_path, monkeypatch):
    from types import SimpleNamespace
    source, destination = tmp_path / "staged", tmp_path / "previous"
    source.write_bytes(b"candidate")
    destination.write_bytes(b"original")
    attempts, waits = [], []
    error = PermissionError("persistent lock")
    error.winerror = 32

    def blocked(*args):
        attempts.append(args)
        raise error

    monkeypatch.setattr(pipeline, "os", SimpleNamespace(name="nt", replace=blocked))
    monkeypatch.setattr(pipeline.time, "sleep", waits.append)
    with pytest.raises(PermissionError, match="persistent lock"):
        pipeline._replace_run_directory(source, destination)
    assert len(attempts) == 6
    assert sum(waits) <= 2.5
    assert source.read_bytes() == b"candidate"
    assert destination.read_bytes() == b"original"


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-sharing publication regression")
def test_windows_publication_recovers_after_temporary_directory_lock(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes
    import threading

    staged = tmp_path / ".stage"
    staged.mkdir()
    (staged / "delivery.gpkg").write_bytes(b"closed delivery")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # A scanner with read/write sharing, but no delete sharing, blocks rename.
    handle = kernel.CreateFileW(str(staged), 0x80000000, 3, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value
    release = threading.Event()
    failures = []
    replace = os.replace

    def attempt(source, destination):
        try:
            return replace(source, destination)
        except PermissionError as exc:
            failures.append(exc.winerror)
            release.set()
            raise

    def unlock():
        release.wait(timeout=5)
        kernel.CloseHandle(handle)

    worker = threading.Thread(target=unlock)
    worker.start()
    monkeypatch.setattr(pipeline.os, "replace", attempt)
    try:
        pipeline._publish_run_bundle(staged, tmp_path / "published")
        assert failures
        assert (tmp_path / "published/delivery.gpkg").read_bytes() == b"closed delivery"
    finally:
        release.set()
        worker.join(timeout=6)
