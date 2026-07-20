"""Thin, QGIS-optional adapter around the canonical Cad2GIS pipeline."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


_PROJECT_KEYS = (
    "source",
    "run_dir",
    "project_dir",
    "source_profile",
    "mapping_registry",
    "gcp_profile",
)
_REQUIRED_PROJECT_KEYS = ("source", "run_dir")

LayerLoader = Callable[[str, str, str], Any]


def convert_project(project: Mapping[str, Any]) -> Any:
    """Validate adapter options and delegate conversion to ``cad2gis``.

    The adapter deliberately has no conversion stages of its own.  Importing the
    canonical function lazily also keeps this module importable in unit tests and
    in QGIS before the Cad2GIS Python environment has been activated.
    """
    if not isinstance(project, Mapping):
        raise TypeError("project must be a mapping of canonical Cad2GIS options")

    unsupported = sorted(set(project) - set(_PROJECT_KEYS))
    if unsupported:
        raise ValueError(f"unsupported Cad2GIS project option(s): {', '.join(unsupported)}")

    missing = [key for key in _REQUIRED_PROJECT_KEYS if not project.get(key)]
    if missing:
        raise ValueError(f"missing required Cad2GIS project option(s): {', '.join(missing)}")

    kwargs = {key: project[key] for key in _PROJECT_KEYS if key in project}
    from cad2gis.pipeline import convert_project as canonical_convert_project

    return canonical_convert_project(**kwargs)


def _default_layer_loader(uri: str, name: str, provider: str) -> Any:
    """Create and register one vector layer in the active QGIS project."""
    from qgis.core import QgsProject, QgsVectorLayer

    layer = QgsVectorLayer(uri, name, provider)
    if not layer.isValid():
        raise ValueError(f"QGIS could not load GeoPackage layer {name!r}")
    QgsProject.instance().addMapLayer(layer)
    return layer


def load_geopackage(
    gpkg_path: str | Path,
    *,
    layer_loader: LayerLoader | None = None,
) -> tuple[Any, ...]:
    """Load every vector table from an existing GeoPackage and return layers.

    ``layer_loader`` follows ``iface.addVectorLayer(uri, name, provider)``.  It
    is injectable so adapter behavior is testable without a QGIS installation.
    """
    path = Path(gpkg_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"GeoPackage does not exist: {path}")

    database_uri = f"file:{path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as database:
            rows = database.execute(
                "SELECT table_name, "
                "COALESCE(NULLIF(identifier, ''), table_name) "
                "FROM gpkg_contents "
                "WHERE data_type = 'features' "
                "ORDER BY table_name"
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"not a readable GeoPackage: {path}") from exc

    if not rows:
        raise ValueError(f"GeoPackage contains no vector feature tables: {path}")

    loader = layer_loader or _default_layer_loader
    return tuple(
        loader(f"{path}|layername={table_name}", display_name, "ogr")
        for table_name, display_name in rows
    )


def convert_and_load(
    project: Mapping[str, Any],
    *,
    layer_loader: LayerLoader | None = None,
) -> tuple[Any, tuple[Any, ...]]:
    """Run the canonical conversion, load its delivery GeoPackage, and return both."""
    result = convert_project(project)
    delivery_path = (
        result.get("delivery_path")
        if isinstance(result, Mapping)
        else getattr(result, "delivery_path", None)
    )
    if not delivery_path:
        raise ValueError("Cad2GIS result does not expose delivery_path")
    return result, load_geopackage(delivery_path, layer_loader=layer_loader)


__all__ = ["convert_and_load", "convert_project", "load_geopackage"]

