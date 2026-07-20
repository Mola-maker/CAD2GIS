"""Typed stage-boundary models for the v3 APD pipeline."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]
Point3D = tuple[float, float, float]

CURVE_FACTS_SCHEMA = "cad2gis-curve-facts-v1"


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{path} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{path} must be a finite number")
    return result


def _point3(value: Any, path: str) -> list[float]:
    try:
        coordinates = list(value)
    except TypeError as exc:
        raise ValueError(f"{path} must contain exactly three coordinates") from exc
    if len(coordinates) != 3:
        raise ValueError(f"{path} must contain exactly three coordinates")
    return [
        _finite_number(coordinate, f"{path}[{index}]")
        for index, coordinate in enumerate(coordinates)
    ]


def _canonical_json_value(value: Any, path: str) -> Any:
    """Return a sorted, finite JSON value for fingerprint inputs."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain non-finite numbers")
        return value
    if isinstance(value, dict):
        return {
            str(key): _canonical_json_value(item, f"{path}.{key}")
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [
            _canonical_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{path} contains a non-JSON value: {type(value).__name__}")


def canonical_curve_facts(value: Any) -> dict[str, Any]:
    """Validate and canonicalize the versioned immutable CAD curve contract.

    Empty input is deliberately accepted for legacy fixtures and non-curve
    records.  Once a facts object is present, its ordered WCS vertices and
    per-vertex bulges are shape-bound and all fingerprint numbers are finite.
    """
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError("curve_facts must be an object")
    schema_version = str(value.get("schema_version", ""))
    if schema_version != CURVE_FACTS_SCHEMA:
        raise ValueError(
            f"Unsupported curve facts schema: {schema_version!r}; "
            f"expected {CURVE_FACTS_SCHEMA!r}"
        )
    coordinate_system = str(value.get("coordinate_system", "WCS")).upper()
    if coordinate_system != "WCS":
        raise ValueError("curve_facts.coordinate_system must be WCS")

    raw_vertices = value.get("vertices_wcs", ())
    if not isinstance(raw_vertices, (list, tuple)):
        raise ValueError("curve_facts.vertices_wcs must be an ordered array")
    vertices = [
        _point3(point, f"curve_facts.vertices_wcs[{index}]")
        for index, point in enumerate(raw_vertices)
    ]

    raw_bulges = value.get("bulges")
    if raw_bulges is None:
        bulges = [0.0] * len(vertices)
    else:
        if not isinstance(raw_bulges, (list, tuple)):
            raise ValueError("curve_facts.bulges must be an ordered array")
        if len(raw_bulges) != len(vertices):
            raise ValueError(
                "curve_facts.bulges must contain exactly one value per WCS vertex"
            )
        bulges = [
            _finite_number(item, f"curve_facts.bulges[{index}]")
            for index, item in enumerate(raw_bulges)
        ]

    elevation_value = value.get("elevation")
    elevation = (
        None if elevation_value is None
        else _finite_number(elevation_value, "curve_facts.elevation")
    )
    normal_value = value.get("normal")
    normal = None if normal_value is None else _point3(normal_value, "curve_facts.normal")
    extrusion_value = value.get("extrusion")
    extrusion = (
        None if extrusion_value is None
        else _point3(extrusion_value, "curve_facts.extrusion")
    )
    closed_value = value.get("closed", False)
    if not isinstance(closed_value, bool):
        raise ValueError("curve_facts.closed must be boolean")
    primitive_parameters = value.get("primitive_parameters", {})
    if not isinstance(primitive_parameters, dict):
        raise ValueError("curve_facts.primitive_parameters must be an object")
    native_length_value = value.get("native_length")
    native_length = (
        None if native_length_value is None
        else _finite_number(native_length_value, "curve_facts.native_length")
    )
    if native_length is not None and native_length < 0:
        raise ValueError("curve_facts.native_length must be non-negative")

    return {
        "schema_version": CURVE_FACTS_SCHEMA,
        "coordinate_system": "WCS",
        "primitive_type": str(value.get("primitive_type", "")),
        "vertices_wcs": vertices,
        "bulges": bulges,
        "elevation": elevation,
        "normal": normal,
        "extrusion": extrusion,
        "closed": closed_value,
        "primitive_parameters": _canonical_json_value(
            primitive_parameters, "curve_facts.primitive_parameters"
        ),
        "native_length": native_length,
        "native_length_source": str(value.get("native_length_source", "")),
    }


def canonical_curve_fingerprint(value: Any) -> str:
    """Return the SHA-256 of the canonical versioned curve-facts JSON."""
    facts = canonical_curve_facts(value)
    if not facts:
        return ""
    payload = json.dumps(
        facts,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


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
    owner_handle: str = ""
    dimension_text_override: str = ""
    native_length: float | None = None
    raw_properties: dict[str, Any] = field(default_factory=dict)
    curve_facts: dict[str, Any] = field(default_factory=dict)
    curve_fingerprint: str = ""

    def __post_init__(self) -> None:
        """Enforce loss-aware reader facts as class invariants.

        ``SourceEntity`` is also constructed directly by diagnostic callers,
        so these guarantees cannot live only in ``from_record``.  In
        particular, non-finite lengths must never reach a comparison gate:
        comparisons with NaN are false and would otherwise fail open.
        """
        if self.dimension_value is not None:
            object.__setattr__(
                self,
                "dimension_value",
                _finite_number(self.dimension_value, "source_entity.dimension_value"),
            )
        if self.native_length is not None:
            native_length = _finite_number(
                self.native_length, "source_entity.native_length"
            )
            if native_length < 0.0:
                raise ValueError("source_entity.native_length must be non-negative")
            object.__setattr__(self, "native_length", native_length)

        curve_facts = canonical_curve_facts(self.curve_facts)
        curve_fingerprint = canonical_curve_fingerprint(curve_facts)
        if self.curve_fingerprint and self.curve_fingerprint != curve_fingerprint:
            raise ValueError(
                "curve_fingerprint does not match canonical curve_facts: "
                f"{self.curve_fingerprint!r}"
            )
        object.__setattr__(self, "curve_facts", curve_facts)
        object.__setattr__(self, "curve_fingerprint", curve_fingerprint)

    @property
    def extraction_backend(self) -> str:
        return str(self.raw_properties.get("extraction_backend", ""))

    @property
    def reader_backend_status(self) -> str:
        return str(self.raw_properties.get("reader_backend_status", ""))

    @property
    def curve_schema_version(self) -> str:
        return str(self.curve_facts.get("schema_version", ""))

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "SourceEntity":
        points = tuple((float(point[0]), float(point[1])) for point in record.get("points", ()))
        centroid_value = record.get("centroid") or ((0.0, 0.0) if not points else points[0])
        curve_facts = canonical_curve_facts(record.get("curve_facts") or {})
        curve_fingerprint = canonical_curve_fingerprint(curve_facts)
        supplied_fingerprint = str(record.get("curve_fingerprint", "") or "")
        if supplied_fingerprint and supplied_fingerprint != curve_fingerprint:
            raise ValueError(
                "curve_fingerprint does not match canonical curve_facts: "
                f"{supplied_fingerprint!r}"
            )
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
            owner_handle=str(record.get("owner_handle", "")),
            dimension_text_override=str(record.get("dimension_text_override", "")),
            native_length=(
                None if record.get("native_length") is None
                else float(record["native_length"])
            ),
            raw_properties=dict(record.get("raw_properties") or {}),
            curve_facts=curve_facts,
            curve_fingerprint=curve_fingerprint,
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
