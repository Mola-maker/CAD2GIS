"""Independent implementation provenance for the optional review lane."""

from __future__ import annotations

from pathlib import Path

from .implementation import build_implementation_provenance


OFFLINE_CURATION_FILES = (
    "cad2gis_v3/curation.py",
    "cad2gis_v3/curation_cli.py",
    "cad2gis_v3/curation_provenance.py",
    "cad2gis_v3/curation_providers/__init__.py",
    "cad2gis_v3/curation_providers/base.py",
    "cad2gis_v3/curation_providers/config.py",
    "cad2gis_v3/curation_providers/openai_compatible.py",
    "cad2gis_v3/curation_service.py",
    "cad2gis_v3/implementation.py",
    "curate_v3.py",
)


def offline_curation_provenance(root: Path | None = None) -> dict:
    """Fingerprint review/domain/provider code without changing production scope."""

    if root is None:
        root = Path(__file__).resolve().parent.parent
    return build_implementation_provenance(
        root,
        scope="offline-curation",
        scope_version=1,
        relative_paths=OFFLINE_CURATION_FILES,
    )
