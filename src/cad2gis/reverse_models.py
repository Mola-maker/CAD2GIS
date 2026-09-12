"""Shared, dependency-light contracts for GIS-to-CAD conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ReverseConversionError(ValueError):
    """An actionable reverse-conversion failure with a stable CLI code."""

    def __init__(self, message: str, *, code: str = "REVERSE_INPUT_INVALID") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GISFeature:
    feature_id: str
    layer: str
    geometry: Any
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GISDataset:
    features: list[GISFeature]
    layer_crs: dict[str, str | None]
    metadata: dict[str, Any] = field(default_factory=dict)
