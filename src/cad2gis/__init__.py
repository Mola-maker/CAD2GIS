"""Public CAD2GIS package.

Importing :mod:`cad2gis` is intentionally lightweight.  The experimental GIS
backend is loaded only when a conversion or project-profile operation runs.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib
from typing import Any

def _package_version() -> str:
    """Prefer the active checkout version over stale global metadata."""
    project_file = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if project_file.is_file():
        try:
            project = tomllib.loads(project_file.read_text(encoding="utf-8"))
            return str(project["project"]["version"])
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
            pass
    try:
        return version("cad2gis")
    except PackageNotFoundError:  # Running from a partial source archive.
        return "0.4.0"


__version__ = _package_version()

__all__ = [
    "__version__",
    "apply_ai_onboarding",
    "auto_onboard_project",
    "bootstrap_project",
    "convert",
    "convert_project",
    "export_source",
    "inspect_source",
    "prepare_ai_onboarding",
    "validate_project",
]


def __getattr__(name: str) -> Any:
    if name in __all__ and name != "__version__":
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
