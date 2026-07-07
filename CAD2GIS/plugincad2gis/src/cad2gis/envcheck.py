"""Toolchain doctor — verify the geospatial stack the pipeline depends on.

Run inside the conda env:  `cad2gis doctor`
Prints an OK/MISS/opt table and returns non-zero if a *critical* component is missing.
This is the G1 "reproducible environment" verification gate.
"""
from __future__ import annotations

import importlib
import shutil
import sys

# (label, import_target, critical)
_PY_MODULES = [
    ("ezdxf", "ezdxf", True),
    ("gdal (osgeo)", "osgeo.gdal", True),
    ("pyproj", "pyproj", True),
    ("shapely", "shapely", True),
    ("geopandas", "geopandas", True),
    ("numpy", "numpy", True),
    ("yaml (pyyaml)", "yaml", True),
    ("pyogrio", "pyogrio", False),
    ("fiona", "fiona", False),
    ("qgis.core", "qgis.core", False),
]

# (label, binary, critical)
_BINARIES = [
    ("ogr2ogr", "ogr2ogr", True),
    ("gdalinfo", "gdalinfo", True),
    ("grass (via QGIS)", "grass", False),  # optional; QGIS ships a GRASS Processing provider
    ("ODAFileConverter", "ODAFileConverter", False),
    ("dwg2dxf (LibreDWG)", "dwg2dxf", False),
]


def _mod_version(name: str):
    try:
        m = importlib.import_module(name)
    except Exception as e:  # noqa: BLE001 - report any import failure
        return None, str(e).splitlines()[0][:60]
    for attr in ("__version__", "version", "VERSION"):
        v = getattr(m, attr, None)
        if isinstance(v, str):
            return v, None
    return "installed", None


def run() -> int:
    missing_critical = 0
    print(f"python           {sys.version.split()[0]}  ({sys.executable})")
    print("-" * 68)
    for label, target, critical in _PY_MODULES:
        ver, err = _mod_version(target)
        ok = ver is not None
        if not ok and critical:
            missing_critical += 1
        status = "OK  " if ok else ("MISS" if critical else "opt ")
        print(f"[{status}] {label:<20} {ver or err or 'not found'}")
    print("-" * 68)
    for label, binname, critical in _BINARIES:
        path = shutil.which(binname)
        ok = path is not None
        if not ok and critical:
            missing_critical += 1
        status = "OK  " if ok else ("MISS" if critical else "opt ")
        print(f"[{status}] {label:<20} {path or 'not on PATH'}")
    print("-" * 68)
    if missing_critical:
        print(f"RESULT: {missing_critical} critical component(s) missing.")
        return 1
    print("RESULT: all critical components present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
