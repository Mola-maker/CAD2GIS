"""Canonical ingestion entrypoint with portable reader resolution."""

from __future__ import annotations

from pathlib import Path

from .cad2gis_v3.config import SourceProfile
from .cad2gis_v3.model import SourceEntity

def _reader_backend() -> str:
    from .reader.resolver import configured_reader

    return configured_reader()


def _extract_records(source_path: Path):
    from .reader.resolver import extract_records

    return extract_records(source_path)


def ingest(source: str | Path, profile: SourceProfile) -> tuple[list[SourceEntity], dict]:
    """Run the canonical ingest boundary using the configured reader."""
    from .cad2gis_v3.ingest import ingest as _ingest

    return _ingest(source, profile, extract_records=_extract_records)
