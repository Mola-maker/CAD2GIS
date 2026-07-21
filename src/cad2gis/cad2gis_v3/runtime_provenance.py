"""Runtime identity for the deterministic CAD-to-GIS conversion.

The conversion implementation and the environment in which it ran are two
different provenance domains.  ``implementation.py`` fingerprints source
files; this module records the optional native/GIS/runtime versions without
importing any heavy dependency as a requirement of the package.  Missing
optional components are represented explicitly instead of being guessed.

The returned objects contain only JSON-compatible values and are built in a
stable key order.  They are therefore safe to persist in a run manifest and
to compare in tests without relying on object ``repr`` output.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


RUNTIME_PROVENANCE_SCHEMA_VERSION = "cad2gis-runtime-provenance-v1"


def _canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically."""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _json_safe(value: Any) -> Any:
    """Normalize arbitrary reader diagnostics to deterministic JSON values.

    Reader inventories are normally dictionaries/lists of scalar values.  A
    defensive normalizer keeps the provenance boundary total when an adapter
    supplies a ``Path``, a tuple, or a small set of custom scalar values.
    Unknown objects are represented by their qualified type rather than their
    ``repr`` (which often contains a process-specific memory address).
    """

    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=lambda item: _canonical_json(item))
    value_type = type(value)
    return {
        "unserializable_type": (
            f"{value_type.__module__}.{value_type.__qualname__}"
        )
    }


def _optional_distribution_version(*names: str) -> str | None:
    for name in names:
        try:
            value = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        except Exception:
            continue
        if value:
            return str(value)
    return None


def _probe_gdal_ogr() -> dict[str, Any]:
    """Probe GDAL and OGR lazily; the conversion package remains importable
    without the conda GIS stack installed.
    """

    result: dict[str, Any] = {
        "gdal": {"available": False, "version": None},
        "ogr": {"available": False, "version": None},
    }
    gdal_version: str | None = None
    try:
        from osgeo import gdal  # type: ignore

        try:
            gdal_version = gdal.VersionInfo("RELEASE_NAME") or gdal.VersionInfo("--version")
        except Exception:
            gdal_version = None
        result["gdal"] = {
            "available": True,
            "version": str(gdal_version) if gdal_version else _optional_distribution_version("GDAL", "gdal"),
        }
    except Exception:
        pass

    try:
        from osgeo import ogr  # type: ignore

        driver_count = None
        try:
            driver_count = int(ogr.GetDriverCount())
        except Exception:
            driver_count = None
        result["ogr"] = {
            "available": True,
            # OGR is shipped by GDAL and has no independent release version;
            # expose the binding version and driver count instead of mistaking
            # the GEOS ABI major for an OGR release.
            "version": str(gdal_version) if gdal_version else _optional_distribution_version("GDAL", "gdal"),
            "driver_count": driver_count,
        }
    except Exception:
        pass
    return result


def _probe_proj() -> dict[str, Any]:
    result: dict[str, Any] = {
        "pyproj": {"available": False, "version": None},
        "proj": {"available": False, "version": None},
    }
    try:
        import pyproj  # type: ignore

        pyproj_version = getattr(pyproj, "__version__", None)
        proj_version = getattr(pyproj, "proj_version_str", None)
        if callable(proj_version):
            proj_version = proj_version()
        result["pyproj"] = {
            "available": True,
            "version": str(pyproj_version) if pyproj_version else _optional_distribution_version("pyproj"),
        }
        result["proj"] = {
            "available": bool(proj_version),
            "version": str(proj_version) if proj_version else None,
        }
    except Exception:
        # Keep pyproj and PROJ distinct; a missing Python binding is not proof
        # that a system PROJ library is unavailable.
        pass
    return result


def _reader_runtime_identity(reader_inventory: Any) -> dict[str, Any]:
    """Extract reader/CoreConsole identity without assuming one inventory
    schema.  The authoritative reader currently reports backend statuses;
    future readers may provide a nested ``runtime`` object.
    """

    identity: dict[str, Any] = {
        "status": "not_reported",
        "core_console": {
            "path": None,
            "version": None,
            "source": None,
        },
        "inventory": {},
    }

    if isinstance(reader_inventory, Mapping):
        raw = _json_safe(reader_inventory)
        identity["inventory"] = raw
        for key in ("runtime", "reader_runtime", "autocad", "auto_cad", "core_console"):
            candidate = reader_inventory.get(key)
            if isinstance(candidate, Mapping):
                identity["core_console"].update(
                    {
                        str(name): _json_safe(value)
                        for name, value in candidate.items()
                        if str(name) in {"path", "version", "source", "status"}
                    }
                )
                identity["status"] = str(candidate.get("status", "reported"))
                break
        # Bulk-reader diagnostics may flatten the executable facts instead of
        # nesting a ``core_console`` object.  Preserve those facts as an
        # explicit runtime identity rather than losing them at the boundary.
        for path_key in ("accoreconsole_path", "core_console_path", "autocad_path"):
            path_value = reader_inventory.get(path_key)
            if path_value and not identity["core_console"].get("path"):
                identity["core_console"]["path"] = str(path_value)
                identity["core_console"]["source"] = str(
                    reader_inventory.get("accoreconsole_source", "reader_inventory")
                )
                identity["status"] = "reported"
                break
        for version_key in ("accoreconsole_version", "core_console_version", "autocad_version"):
            if reader_inventory.get(version_key) is not None:
                identity["core_console"]["version"] = _json_safe(
                    reader_inventory.get(version_key)
                )
                break
        backend_statuses = reader_inventory.get("backend_statuses")
        if backend_statuses is not None and identity["status"] == "not_reported":
            identity["status"] = "reported"
            identity["backend_statuses"] = _json_safe(backend_statuses)
    elif reader_inventory is not None:
        # A raw record list is useful for count/identity diagnostics but is not
        # copied in full into the runtime manifest.
        try:
            identity["inventory"] = {"record_count": len(reader_inventory)}
        except Exception:
            identity["inventory"] = {"value": _json_safe(reader_inventory)}

    configured = os.environ.get("CAD2GIS_ACCORECONSOLE", "").strip()
    if configured and not identity["core_console"].get("path"):
        path = Path(configured)
        identity["core_console"]["path"] = str(path.resolve())
        identity["core_console"]["source"] = "environment"
        match = re.search(r"(?i)AutoCAD\s+(\d{4})", str(path.parent))
        if match:
            identity["core_console"]["version"] = int(match.group(1))
        identity["status"] = "configured"
    return identity


def collect_runtime_provenance(*, reader_inventory: Any = None) -> dict[str, Any]:
    """Collect deterministic runtime identity with optional reader facts.

    No value is inferred from a missing dependency.  Every optional component
    has an explicit ``available`` flag, while Python/SQLite/OS are always
    reported by the standard library.
    """

    gdal_ogr = _probe_gdal_ogr()
    proj = _probe_proj()
    runtime = {
        "schema_version": RUNTIME_PROVENANCE_SCHEMA_VERSION,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": str(Path(sys.executable).resolve()) if sys.executable else None,
        },
        "sqlite": {
            "runtime_version": sqlite3.sqlite_version,
            "python_binding_version": getattr(sqlite3, "version", None),
        },
        "os": {
            "name": os.name,
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        **gdal_ogr,
        **proj,
        "autocad": _reader_runtime_identity(reader_inventory),
    }
    # Validate that future additions cannot accidentally persist NaN or a
    # non-JSON object.  Returning the normalized value also gives callers a
    # stable object even when an adapter used tuples/Paths.
    return json.loads(_canonical_json(runtime))


def runtime_manifest_fields(provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return manifest fields for runtime identity and its content digest."""

    value = (
        collect_runtime_provenance() if provenance is None else json.loads(_canonical_json(provenance))
    )
    if value.get("schema_version") != RUNTIME_PROVENANCE_SCHEMA_VERSION:
        raise ValueError(
            "Runtime provenance schema mismatch: "
            f"expected {RUNTIME_PROVENANCE_SCHEMA_VERSION!r}"
        )
    digest = _sha256_json(value)
    return {
        "runtime_provenance_sha256": digest,
        "runtime_provenance": value,
    }


__all__ = [
    "RUNTIME_PROVENANCE_SCHEMA_VERSION",
    "collect_runtime_provenance",
    "runtime_manifest_fields",
]
