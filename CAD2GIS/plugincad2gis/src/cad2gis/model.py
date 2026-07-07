"""Internal typed data model — the stable contract every pipeline stage speaks.

This is deliberately geometry-library-light: geometries are shapely objects, but the
dataclasses don't hard-depend on shapely at import time so the model can be imported
in any environment. Keeping `SourceRef` provenance on every feature is what backs the
"lossless / traceable" conversion claim and lets later stages (audit, BOM, design)
bolt on without touching the core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

UNMAPPED = "__unmapped__"


@dataclass
class SourceRef:
    """Where a feature came from in the source CAD drawing (provenance)."""

    file: Optional[str] = None
    layer: Optional[str] = None
    block: Optional[str] = None
    handle: Optional[str] = None
    entity_type: Optional[str] = None


@dataclass
class Feature:
    """A single converted GIS feature."""

    geometry: Any  # shapely geometry
    feature_class: Optional[str] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    source: SourceRef = field(default_factory=SourceRef)
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        g = self.geometry
        try:
            return bool(getattr(g, "is_valid", False)) and not bool(getattr(g, "is_empty", True))
        except Exception:  # noqa: BLE001
            return False


@dataclass
class FeatureCollection:
    """A set of converted features plus conversion-level metadata."""

    features: list[Feature] = field(default_factory=list)
    crs: Optional[str] = None  # e.g. "EPSG:4490" or "local-engineering"
    source_file: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.features)

    def add(self, f: Feature) -> None:
        self.features.append(f)

    def by_class(self) -> dict[str, list[Feature]]:
        out: dict[str, list[Feature]] = {}
        for f in self.features:
            out.setdefault(f.feature_class or UNMAPPED, []).append(f)
        return out

    def counts_by_class(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.by_class().items()}


@dataclass
class FieldSpec:
    name: str
    dtype: str = "str"
    required: bool = False
    unit: Optional[str] = None
    domain: Optional[list] = None


@dataclass
class FeatureClassSchema:
    """Published schema for one GIS feature class (used by G10 warehousing & attribute QC)."""

    name: str
    geom_type: str  # "Point" | "LineString" | "Polygon"
    fields: list[FieldSpec] = field(default_factory=list)

    def required_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.required]
