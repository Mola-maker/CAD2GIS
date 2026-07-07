"""CAD2GIS QGIS plugin entry point.

`classFactory` is the QGIS plugin loader contract. The plugin adds the repo `src/` dir to sys.path
so `import cad2gis` resolves inside QGIS's Python, then wires the dockwidget.
"""
import os
import sys


def _resolve_repo_src() -> str:
    """Find the project src directory from either a symlinked or copied QGIS plugin install."""
    here = os.path.abspath(os.path.dirname(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, "..", "src")),
    ]
    repo_root = os.environ.get("CAD2GIS_REPO_ROOT")
    if repo_root:
        candidates.append(os.path.abspath(os.path.join(repo_root, "src")))
    try:
        from ._cad2gis_repo_path import REPO_ROOT

        candidates.append(os.path.abspath(os.path.join(REPO_ROOT, "src")))
    except Exception:  # noqa: BLE001 - optional file exists only for copied installs
        pass
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "cad2gis")):
            return candidate
    return candidates[0]


def classFactory(iface):  # noqa: N802 - QGIS-contracted name
    repo_src = _resolve_repo_src()
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    from .plugin import Cad2gisPlugin

    return Cad2gisPlugin(iface)
