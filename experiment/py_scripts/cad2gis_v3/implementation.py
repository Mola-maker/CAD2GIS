"""Reproducible provenance for the deterministic production conversion.

The production fingerprint is intentionally built from an explicit allow-list.
Offline review, curation and model-provider code therefore cannot silently
change the identity of the deterministic CAD-to-GIS implementation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Iterable


IMPLEMENTATION_SCHEMA_VERSION = "cad2gis-implementation-provenance-v1"
PRODUCTION_CONVERSION_SCOPE = "production-conversion"
PRODUCTION_CONVERSION_SCOPE_VERSION = 1

# Paths are relative to experiment/py_scripts.  Keep this list explicit: adding
# a runtime conversion dependency is an architectural decision and must update
# the recorded scope, while optional review/provider modules remain outside it.
PRODUCTION_CONVERSION_FILES = (
    "apd_rules.py",
    "autocad_reader.py",
    "cad2gis_v3/__init__.py",
    "cad2gis_v3/calibration.py",
    "cad2gis_v3/cli.py",
    "cad2gis_v3/config.py",
    "cad2gis_v3/evidence.py",
    "cad2gis_v3/georef.py",
    "cad2gis_v3/implementation.py",
    "cad2gis_v3/ingest.py",
    "cad2gis_v3/model.py",
    "cad2gis_v3/pipeline.py",
    "cad2gis_v3/ports.py",
    "cad2gis_v3/semantics.py",
    "cad2gis_v3/styles.py",
    "cad2gis_v3/topology.py",
    "cad2gis_v3/warehouse.py",
    "convert_v3.py",
    "schema_config.py",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized_relative_paths(relative_paths: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for raw_path in relative_paths:
        path = PurePosixPath(str(raw_path).replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
            raise ValueError(f"Implementation scope path must be relative: {raw_path!r}")
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        raise ValueError("Implementation scope contains duplicate paths")
    return tuple(sorted(normalized))


def build_implementation_provenance(
    root: Path,
    *,
    scope: str,
    scope_version: int,
    relative_paths: Iterable[str],
) -> dict:
    """Build a path-stable, content-addressed implementation descriptor."""
    root = Path(root).resolve()
    if not scope or not scope.strip():
        raise ValueError("Implementation scope must be non-empty")
    if isinstance(scope_version, bool) or not isinstance(scope_version, int) or scope_version < 1:
        raise ValueError("Implementation scope_version must be a positive integer")

    files = []
    for relative_path in _normalized_relative_paths(relative_paths):
        path = root.joinpath(*PurePosixPath(relative_path).parts)
        if not path.is_file():
            raise FileNotFoundError(
                f"Implementation scope file does not exist: {relative_path}"
            )
        content = path.read_bytes()
        files.append({
            "path": relative_path,
            "sha256": _sha256_bytes(content),
            "size_bytes": len(content),
        })

    descriptor = {
        "schema_version": IMPLEMENTATION_SCHEMA_VERSION,
        "scope": scope.strip(),
        "scope_version": scope_version,
        "files": files,
    }
    canonical = json.dumps(
        descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {**descriptor, "sha256": _sha256_bytes(canonical)}


def production_conversion_provenance(root: Path | None = None) -> dict:
    """Return provenance for only the deterministic production conversion."""
    if root is None:
        root = Path(__file__).resolve().parent.parent
    return build_implementation_provenance(
        root,
        scope=PRODUCTION_CONVERSION_SCOPE,
        scope_version=PRODUCTION_CONVERSION_SCOPE_VERSION,
        relative_paths=PRODUCTION_CONVERSION_FILES,
    )


def implementation_manifest_fields(provenance: dict | None = None) -> dict:
    """Emit the v3 manifest object plus its legacy digest compatibility field."""
    implementation = (
        production_conversion_provenance() if provenance is None else dict(provenance)
    )
    digest = implementation.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Implementation provenance has no valid sha256")
    return {
        "implementation_sha256": digest,
        "implementation": implementation,
    }
