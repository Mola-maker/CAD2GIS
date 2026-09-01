"""Validate and apply authoritative AutoCAD ``GEODATA`` registration facts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


GEODATA_REGISTRATION_SCHEMA = "cad2gis.dwg_geodata_registration.v1"


def _point(value: Any, name: str) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) < 2
    ):
        raise ValueError(f"{name} must contain two finite coordinates")
    result = [float(value[0]), float(value[1])]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain two finite coordinates")
    return result


def normalize_geodata_registration(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a minimal, deterministic GEODATA similarity registration."""

    if not isinstance(value, Mapping):
        raise ValueError("geodata_registration must be an object")
    schema = str(value.get("schema_version", ""))
    if schema != GEODATA_REGISTRATION_SCHEMA:
        raise ValueError(f"Unsupported geodata registration schema: {schema!r}")
    design = _point(value.get("design_point"), "geodata design_point")
    reference = _point(value.get("reference_point"), "geodata reference_point")
    north = _point(value.get("north_direction"), "geodata north_direction")
    norm = math.hypot(*north)
    if norm <= 1e-15:
        raise ValueError("geodata north_direction must be non-zero")
    north = [north[0] / norm, north[1] / norm]
    horizontal_scale = float(value.get("horizontal_unit_scale", 1.0))
    user_scale = float(value.get("user_scale_factor", 1.0))
    scale = horizontal_scale * user_scale
    if not all(
        math.isfinite(item) and item > 0.0
        for item in (horizontal_scale, user_scale, scale)
    ):
        raise ValueError("geodata scale factors must be finite and positive")
    coordinate_system_id = str(value.get("coordinate_system_id", "")).strip()
    target_crs = str(value.get("target_crs", "")).strip()
    authority = str(value.get("authority", "")).strip()
    if not coordinate_system_id or not target_crs or not authority:
        raise ValueError(
            "geodata coordinate_system_id, target_crs, and authority are required"
        )
    return {
        "schema_version": GEODATA_REGISTRATION_SCHEMA,
        "coordinate_system_id": coordinate_system_id,
        "target_crs": target_crs,
        "design_point": design,
        "reference_point": reference,
        "horizontal_unit_scale": horizontal_scale,
        "user_scale_factor": user_scale,
        "north_direction": north,
        "authority": authority,
    }


def registration_scale(value: Mapping[str, Any]) -> float:
    registration = normalize_geodata_registration(value)
    return float(
        registration["horizontal_unit_scale"]
        * registration["user_scale_factor"]
    )


def local_to_crs_point(
    point: Sequence[float], value: Mapping[str, Any],
) -> tuple[float, float]:
    registration = normalize_geodata_registration(value)
    design_x, design_y = registration["design_point"]
    reference_x, reference_y = registration["reference_point"]
    north_x, north_y = registration["north_direction"]
    scale = registration_scale(registration)
    dx = float(point[0]) - design_x
    dy = float(point[1]) - design_y
    return (
        reference_x + scale * (dx * north_y - dy * north_x),
        reference_y + scale * (dx * north_x + dy * north_y),
    )


def crs_to_local_point(
    point: Sequence[float], value: Mapping[str, Any],
) -> tuple[float, float]:
    registration = normalize_geodata_registration(value)
    design_x, design_y = registration["design_point"]
    reference_x, reference_y = registration["reference_point"]
    north_x, north_y = registration["north_direction"]
    scale = registration_scale(registration)
    dx = float(point[0]) - reference_x
    dy = float(point[1]) - reference_y
    return (
        design_x + (dx * north_y + dy * north_x) / scale,
        design_y + (-dx * north_x + dy * north_y) / scale,
    )


__all__ = [
    "GEODATA_REGISTRATION_SCHEMA",
    "crs_to_local_point",
    "local_to_crs_point",
    "normalize_geodata_registration",
    "registration_scale",
]
