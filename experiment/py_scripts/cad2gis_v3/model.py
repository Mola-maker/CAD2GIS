"""Typed stage-boundary models for the v3 APD pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]


@dataclass(frozen=True)
class CadStyle:
    aci_color: int = 256
    true_color: str = ""
    linetype: str = "ByLayer"
    lineweight: int = -1
    rotation: float = 0.0
    entity_aci_color: int = 256
    layer_aci_color: int = 7
    entity_true_color: str = ""
    layer_true_color: str = ""
    entity_linetype: str = "ByLayer"
    layer_linetype: str = "Continuous"
    entity_lineweight: int = -1
    layer_lineweight: int = -1

    @property
    def rotation_degrees(self) -> float:
        return math.degrees(self.rotation)

    @property
    def qgis_rotation_degrees(self) -> float:
        """Convert CAD counter-clockwise angle to QGIS clockwise rendering angle."""
        return (-self.rotation_degrees) % 360.0

    @property
    def render_key(self) -> str:
        color = (
            f"TRUECOLOR:{self.true_color.strip().upper().lstrip('#')}"
            if self.true_color.strip()
            else f"ACI:{self.aci_color}"
        )
        return (
            f"{color}|LT:{self.linetype or 'Continuous'}|LW:{self.lineweight}"
            f"|ROT_QGIS:{self.qgis_rotation_degrees:.9f}"
        )


@dataclass(frozen=True)
class SourceEntity:
    entity_key: str
    source_sha256: str
    source_file: str
    handle: str
    layout: str
    layout_role: str
    cad_role: str
    layer: str
    object_name: str
    dwg_type: str
    points: tuple[Point, ...]
    centroid: Point
    closed: bool
    text: str
    block_name: str
    block_attributes: dict[str, str]
    style: CadStyle
    dimension_value: float | None = None
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "SourceEntity":
        points = tuple((float(point[0]), float(point[1])) for point in record.get("points", ()))
        centroid_value = record.get("centroid") or ((0.0, 0.0) if not points else points[0])
        return cls(
            entity_key=str(record.get("entity_key", "")),
            source_sha256=str(record.get("source_sha256", "")),
            source_file=str(record.get("source_file", "")),
            handle=str(record.get("handle", "")),
            layout=str(record.get("layout", "")),
            layout_role=str(record.get("layout_role", "")),
            cad_role=str(record.get("cad_role", "")),
            layer=str(record.get("layer", "")),
            object_name=str(record.get("object_name", "")),
            dwg_type=str(record.get("dwg_type_name", "")),
            points=points,
            centroid=(float(centroid_value[0]), float(centroid_value[1])),
            closed=bool(record.get("closed", False)),
            text=str(record.get("text", "")),
            block_name=str(record.get("block_name", "")),
            block_attributes={
                str(key).upper(): str(value)
                for key, value in dict(record.get("block_attributes", {})).items()
            },
            style=CadStyle(
                aci_color=int(record.get("aci_color", 256)),
                true_color=str(record.get("true_color", "")),
                linetype=str(record.get("linetype", "ByLayer")),
                lineweight=int(record.get("lineweight", -1)),
                rotation=float(record.get("rotation", 0.0) or 0.0),
                entity_aci_color=int(record.get("entity_aci_color", record.get("aci_color", 256))),
                layer_aci_color=int(record.get("layer_aci_color", 7)),
                entity_true_color=str(record.get("entity_true_color", "")),
                layer_true_color=str(record.get("layer_true_color", "")),
                entity_linetype=str(record.get("entity_linetype", record.get("linetype", "ByLayer"))),
                layer_linetype=str(record.get("layer_linetype", "Continuous")),
                entity_lineweight=int(record.get("entity_lineweight", record.get("lineweight", -1))),
                layer_lineweight=int(record.get("layer_lineweight", -1)),
            ),
            dimension_value=(
                None if record.get("dimension_value") is None
                else float(record["dimension_value"])
            ),
            scale=(
                float(record.get("scale_x", 1.0)),
                float(record.get("scale_y", 1.0)),
                float(record.get("scale_z", 1.0)),
            ),
        )


@dataclass
class Feature:
    feature_key: str
    feature_class: str
    geometry_kind: str
    native_points: list[Point]
    source_entity_key: str
    source_handle: str
    source_layer: str
    geometry_role: str
    style: CadStyle
    attributes: dict[str, Any] = field(default_factory=dict)
    display_label: str = ""
    label_provenance: str = "UNAVAILABLE"
    field_provenance: dict[str, str] = field(default_factory=dict)
    lineage: list[dict[str, Any]] = field(default_factory=list)

    @property
    def native_centroid(self) -> Point:
        if not self.native_points:
            return (0.0, 0.0)
        return (
            sum(point[0] for point in self.native_points) / len(self.native_points),
            sum(point[1] for point in self.native_points) / len(self.native_points),
        )


@dataclass(frozen=True)
class Relation:
    relation_key: str
    relation_kind: str
    source_key: str
    target_key: str
    status: str
    method: str
    distance_native_m: float | None = None
    evidence_keys: tuple[str, ...] = ()


@dataclass
class StageBundle:
    entities: list[SourceEntity]
    features: list[Feature]
    relations: list[Relation]
    unresolved: list[dict[str, Any]]
    diagnostics: dict[str, Any]
