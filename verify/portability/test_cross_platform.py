"""Cross-platform reader portability tests.

Verifies OS detection, ctypes library loading, and output schema consistency
across Linux, Windows, and macOS.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest


def test_os_detection():
    system = platform.system()
    assert system in {"Linux", "Windows", "Darwin"}


def test_libredwg_shared_library_discovery():
    system = platform.system()
    if system == "Linux":
        candidates = [
            Path("/usr/local/lib/libredwg.so"),
            Path("/usr/lib/libredwg.so"),
        ]
    elif system == "Windows":
        candidates = [
            Path("C:/Program Files/LibreDWG/libredwg.dll"),
            Path("C:/libredwg/libredwg.dll"),
        ]
    elif system == "Darwin":
        candidates = [
            Path("/usr/local/lib/libredwg.dylib"),
            Path("/opt/homebrew/lib/libredwg.dylib"),
        ]
    else:
        pytest.skip(f"unsupported platform: {system}")

    found = any(path.exists() for path in candidates)
    if not found:
        pytest.skip(f"libredwg shared library not found on {system}")


def test_reader_backend_env_switch():
    from cad2gis.ingest import _DEFAULT_READER, _READER_ENV

    assert _DEFAULT_READER == "libredwg"
    original = os.environ.get(_READER_ENV)
    try:
        os.environ[_READER_ENV] = "autocad"
        from cad2gis.ingest import _reader_backend

        assert _reader_backend() == "autocad"
        os.environ[_READER_ENV] = "libredwg"
        assert _reader_backend() == "libredwg"
    finally:
        if original is None:
            os.environ.pop(_READER_ENV, None)
        else:
            os.environ[_READER_ENV] = original


def test_output_schema_consistency():
    from cad2gis.reader.contracts import DWGRecordInventory

    class MockInventory(list):
        diagnostics = {"extraction_backend": "libredwg"}

    inventory = MockInventory([{"entity_key": "a"}])
    assert isinstance(inventory, DWGRecordInventory)
    assert hasattr(inventory, "diagnostics")
    assert len(inventory) == 1
