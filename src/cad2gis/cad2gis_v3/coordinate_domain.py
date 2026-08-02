"""Validate that CAD coordinates plausibly occupy their declared CRS domain.

A DWG ``CGEOCS`` value proves that a CRS name was stored in the drawing.  It
does not prove that the entity WCS coordinates have already been registered
into that CRS.  This gate catches the common local-engineering-coordinate
case before a nominal CRS label can produce a geographically false delivery.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from pyproj import CRS, Transformer

from .model import SourceEntity


COORDINATE_DOMAIN_SCHEMA_VERSION = "cad2gis-coordinate-domain-v1"


def assess_coordinate_domain(
    entities: Iterable[SourceEntity],
    source_crs: str,
    *,
    minimum_inside_fraction: float = 0.95,
) -> dict[str, Any]:
    points = [
        (float(point[0]), float(point[1]))
        for entity in entities
        for point in (entity.points or (entity.centroid,))
        if len(point) >= 2
        and math.isfinite(float(point[0]))
        and math.isfinite(float(point[1]))
    ]
    result: dict[str, Any] = {
        "schema_version": COORDINATE_DOMAIN_SCHEMA_VERSION,
        "source_crs": str(source_crs),
        "point_count": len(points),
        "minimum_inside_fraction": float(minimum_inside_fraction),
        "passed": False,
        "status": "INSUFFICIENT_GEOMETRY",
        "failures": [],
    }
    if not points:
        result["failures"].append("No finite drawing coordinates were available.")
        return result

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    result["observed_extent"] = {
        "min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys),
    }
    # Magnitude heuristic: a global CRS (EPSG:3857) accepts any finite
    # coordinate via its area-of-use check, so local engineering coordinates
    # would slip through.  Coordinates whose max |value| is below this bound
    # cannot plausibly be real world-scale EPSG:3857 eastings/northings.
    max_abs = max(abs(min(xs)), abs(max(xs)), abs(min(ys)), abs(max(ys)))
    result["max_abs_coordinate"] = max_abs
    crs = CRS.from_user_input(source_crs)
    if (
        crs.is_projected
        and str(crs.to_authority()[0]).upper() == "EPSG"
        and str(crs.to_authority()[1]) == "3857"
        and max_abs < 100_000.0
    ):
        result["status"] = "LOCAL_OR_MISREGISTERED_COORDINATES"
        result["failures"].append(
            "Declared EPSG:3857 but coordinate magnitude is below 100000 "
            "units — local engineering coordinates detected; OSM anchor or "
            "GCP registration required before geographic delivery."
        )
        return result
    if crs.is_geographic:
        area = crs.area_of_use
        west, south, east, north = (
            (-180.0, -90.0, 180.0, 90.0)
            if area is None
            else (area.west, area.south, area.east, area.north)
        )
        expected = (west, south, east, north)
    elif crs.is_projected and crs.area_of_use is not None:
        area = crs.area_of_use
        geodetic = crs.geodetic_crs
        transformer = Transformer.from_crs(geodetic, crs, always_xy=True)
        expected = transformer.transform_bounds(
            area.west, area.south, area.east, area.north, densify_pts=21,
        )
    else:
        result["status"] = "CRS_DOMAIN_UNAVAILABLE"
        result["failures"].append(
            "The declared CRS has no usable geographic area-of-use envelope."
        )
        return result

    min_x, min_y, max_x, max_y = (float(value) for value in expected)
    margin_x = max(1.0, abs(max_x - min_x) * 0.02)
    margin_y = max(1.0, abs(max_y - min_y) * 0.02)
    min_x, min_y = min_x - margin_x, min_y - margin_y
    max_x, max_y = max_x + margin_x, max_y + margin_y
    result["expected_extent"] = {
        "min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y,
        "basis": "declared_crs_area_of_use",
    }
    inside = sum(
        1 for x, y in points if min_x <= x <= max_x and min_y <= y <= max_y
    )
    fraction = inside / len(points)
    result["inside_point_count"] = inside
    result["inside_fraction"] = fraction
    result["passed"] = fraction >= minimum_inside_fraction
    result["status"] = (
        "PLAUSIBLE_DECLARED_CRS_DOMAIN"
        if result["passed"]
        else "LOCAL_OR_MISREGISTERED_COORDINATES"
    )
    if not result["passed"]:
        result["failures"].append(
            "Drawing coordinates do not plausibly occupy the declared CRS area "
            f"of use ({inside}/{len(points)} points inside)."
        )
    return result


__all__ = ["COORDINATE_DOMAIN_SCHEMA_VERSION", "assess_coordinate_domain"]
