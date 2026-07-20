"""Lightweight public project-profile facade.

The implementation lives behind :mod:`cad2gis.pipeline` so every integration
uses the same orchestration boundary.
"""

from __future__ import annotations

from .pipeline import bootstrap_project, inspect_source, validate_project

__all__ = ["bootstrap_project", "inspect_source", "validate_project"]
