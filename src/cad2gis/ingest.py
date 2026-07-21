"""Canonical ingestion entrypoint with cross-platform reader switching.

The reader backend is selected via the ``CAD2GIS_READER_BACKEND``
environment variable.  ``libredwg`` is the default cross-platform primary;
``autocad`` is an opt-in Windows-only fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

from .cad2gis_v3.config import SourceProfile
from .cad2gis_v3.model import SourceEntity

_READER_ENV = "CAD2GIS_READER_BACKEND"
_DEFAULT_READER = "libredwg"


def _reader_backend() -> str:
    backend = os.environ.get(_READER_ENV, _DEFAULT_READER).strip().lower()
    if backend not in {"libredwg", "autocad"}:
        raise ValueError(
            f"unknown reader backend {backend!r}; expected libredwg or autocad"
        )
    return backend


def _extract_records(source_path: Path):
    backend = _reader_backend()
    if backend == "libredwg":
        from .reader.libredwg import extract_dwg_records
    else:
        from .reader.autocad import extract_dwg_records
    return extract_dwg_records(source_path)


def ingest(source: str | Path, profile: SourceProfile) -> tuple[list[SourceEntity], dict]:
    """Run the canonical ingest boundary using the configured reader."""
    from .cad2gis_v3.ingest import ingest as _ingest

    return _ingest(source, profile, extract_records=_extract_records)
