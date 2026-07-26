"""Typed admission gate for external CAD source dependencies."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .model import SourceEntity


SOURCE_DEPENDENCY_SCHEMA_VERSION = "cad2gis-source-dependencies-v1"
_GEOMETRY_EXTERNAL_STATES = frozenset({
    "xref", "xref_overlay", "external_dependent",
})


def assess_source_dependencies(
    entities: Iterable[SourceEntity],
) -> dict[str, Any]:
    geometry_dependencies = []
    visual_reference_entities = []
    for entity in entities:
        properties = entity.raw_properties
        status = str(
            properties.get("external_reference_status")
            or properties.get("xref_status")
            or "not_external"
        ).strip().casefold()
        if status in _GEOMETRY_EXTERNAL_STATES:
            geometry_dependencies.append({
                "entity_key": entity.entity_key,
                "handle": entity.handle,
                "kind": entity.dwg_type,
                "status": status,
                "path": str(
                    properties.get("external_reference_path")
                    or properties.get("xref_path")
                    or ""
                ),
            })
        if entity.dwg_type.upper() in {"IMAGE", "PDFUNDERLAY", "DGNUNDERLAY"}:
            visual_reference_entities.append({
                "entity_key": entity.entity_key,
                "handle": entity.handle,
                "kind": entity.dwg_type,
                "path": str(
                    properties.get("external_reference_path")
                    or properties.get("source_file_path")
                    or ""
                ),
            })

    blocking = bool(geometry_dependencies)
    return {
        "schema_version": SOURCE_DEPENDENCY_SCHEMA_VERSION,
        "status": (
            "SOURCE_GEOMETRY_DEPENDENCY_MISSING"
            if blocking
            else (
                "SOURCE_VISUAL_DEPENDENCY_RECORDED"
                if visual_reference_entities
                else "SOURCE_COMPLETE_NO_EXTERNAL_DEPENDENCIES"
            )
        ),
        "passed": not blocking,
        "geometry_dependency_count": len(geometry_dependencies),
        "geometry_dependencies": geometry_dependencies,
        "visual_reference_count": len(visual_reference_entities),
        "visual_references": visual_reference_entities,
        "failures": (
            [
                "External DWG geometry is not materialized in the immutable "
                "inventory; supply a complete source bundle or bound snapshot."
            ]
            if blocking else []
        ),
    }


__all__ = [
    "SOURCE_DEPENDENCY_SCHEMA_VERSION",
    "assess_source_dependencies",
]
