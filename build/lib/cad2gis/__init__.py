"""Public CAD2GIS package.

Importing :mod:`cad2gis` is intentionally lightweight.  The experimental GIS
backend is loaded only when a conversion or project-profile operation runs.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("cad2gis")
except PackageNotFoundError:  # Running directly from an unpacked source tree.
    __version__ = "0.1.0"

__all__ = [
    "__version__",
    "bootstrap_project",
    "convert",
    "convert_project",
    "inspect_source",
    "validate_project",
]


def __getattr__(name: str) -> Any:
    if name in __all__ and name != "__version__":
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
