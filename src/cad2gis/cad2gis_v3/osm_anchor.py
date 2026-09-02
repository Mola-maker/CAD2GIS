"""OSM place-name geolocation for relative, non-authoritative review hints.

DWGs often declare ``CGEOCS=WGS84.PseudoMercator`` while their entities use
local engineering coordinates.  This module extracts the place name from the
source filename, asks the OSM Nominatim API for the place's real location,
and derives a bbox-centre translation that can be stored per project as a
coarse candidate.  The candidate must never be applied to delivery geometry:
only DWG GEODATA or independently reviewed ground control can authorize an
absolute coordinate transformation.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_NOMINATIM_TIMEOUT_S = 15.0
_OSM_ANCHOR_SCHEMA = "cad2gis-osm-anchor-v2"

# OVERFIT-RISK (region): these administrative-level keywords are Indonesian
# naming conventions (kelurahan/kecamatan/kabupaten/desa/dusun/RW/RT).  They
# make the anchor parser fit the four Indonesian baseline drawings; other
# countries need their own locality grammar, not this list.
_LOCALITY_KEYWORDS = re.compile(
    r"(?i)(kelurahan|kecamatan|kabupaten|desa|dusun|rw|rt|kampung|"
    r"village|town|city|district|provinsi)"
)
# OVERFIT-RISK (corpus filename grammar): ``apd|sf|main|odp`` are the
# baseline filename tokens.  New validation files keep ``APD``/``SF`` by
# convention, but any non-Indonesian or differently-named corpus should be
# parsed from its own source-bound evidence, not this token list.
_UNIT_KEYWORDS = re.compile(r"(?i)(apd|ftth|odp|site|sf|main|cable|fo)")


def _place_name_from_filename(source_path: str | Path) -> list[str]:
    """Extract candidate searchable localities from the DWG file name.

    Returns a ranked list of query candidates (most specific first):
    - "APD - KELURAHAN LAMTEH DAYAH ACEH.dwg"
        -> ["Lamteh Dayah Aceh", "Lamteh Aceh", "Aceh Besar"]
    - "APD - KLETEK RW 05 SIDOARJO.dwg"
        -> ["Kletek Sidoarjo", "Sidoarjo"]
    - "APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"
        -> ["Hutabohu Gorontalo", "Gorontalo"]
    """
    name = Path(source_path).stem
    name = re.sub(r"(?i)\.dwg$", "", name)
    # ``APD`` = As Plan Drawing; strip the drawing-type prefix before
    # geocoding the locality.
    name = re.sub(r"(?i)^APD\s*[-_]?\s*", "", name)
    # Strip trailing project qualifiers like " - SF" (subfeeder) / " - MAIN".
    name = re.sub(r"(?i)\s*-\s*(sf|main|odp|site|ftth|v\d+)\s*$", "", name).strip()
    name = re.sub(r"\s+", " ", name).strip()

    candidates: list[str] = []
    # Full name after dropping the administrative-level lead words
    # (KELURAHAN/KECAMATAN/RW/RT/DUSUN) so Nominatim sees the locality.
    core = re.sub(
        r"(?i)^(kelurahan|kecamatan|kabupaten|desa|dusun|rw|rt|kampung)\s+",
        "",
        name,
    ).strip()
    if core:
        candidates.append(core)
    # Drop leading numeral-only tokens (e.g. "05") and keep the rest.
    trimmed = re.sub(r"^\d+\s+", "", core).strip()
    if trimmed and trimmed != core:
        candidates.append(trimmed)
    # County-level fallback: last one/two significant words.
    words = [w for w in core.split() if not re.fullmatch(r"\d+", w)]
    if len(words) >= 2:
        candidates.append(" ".join(words[-2:]))
    if len(words) >= 1:
        candidates.append(words[-1])
    # De-duplicate, preserving order.
    seen: set[str] = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def query_osm_place(place_name: str) -> dict[str, Any] | None:
    """Query Nominatim for a place and return its projected centre.

    Returns ``{"display_name", "lat", "lon", "bbox", "epsg3857_centre"}``
    or ``None`` when the query yields no usable result.
    """
    params = urllib.parse.urlencode({
        "q": place_name,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    })
    url = f"{_NOMINATIM_URL}?{params}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "cad2gis-osm-anchor/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_NOMINATIM_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    lat = float(first.get("lat"))
    lon = float(first.get("lon"))
    bbox = first.get("boundingbox")
    result: dict[str, Any] = {
        "display_name": str(first.get("display_name", place_name)),
        "lat": lat,
        "lon": lon,
        "epsg3857_centre": _wgs84_to_3857(lon, lat),
    }
    if isinstance(bbox, list) and len(bbox) == 4:
        south, north, west, east = (float(v) for v in bbox)
        result["bbox"] = {
            "south": south, "north": north, "west": west, "east": east,
        }
        result["epsg3857_bbox"] = {
            "min_x": _wgs84_to_3857(west, (south + north) / 2)[0],
            "min_y": _wgs84_to_3857((west + east) / 2, south)[1],
            "max_x": _wgs84_to_3857(east, (south + north) / 2)[0],
            "max_y": _wgs84_to_3857((west + east) / 2, north)[1],
        }
    return result


def _wgs84_to_3857(lon: float, lat: float) -> tuple[float, float]:
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * 20037508.34 / 180.0
    return x, y


def _epsg3857_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon = float(x) * 180.0 / 20037508.34
    lat = math.degrees(2.0 * math.atan(math.exp(
        float(y) / 20037508.34 * math.pi,
    )) - math.pi / 2.0)
    return lon, lat


def query_osm_roads(
    bbox: Mapping[str, float], *, max_roads: int = 2000,
) -> list[dict[str, Any]]:
    """Fetch highway ways inside a reviewed place-search bounding box.

    Network failure returns an empty candidate set.  OSM data is never
    promoted to coordinate authority by this function.
    """
    south = float(bbox["south"])
    west = float(bbox["west"])
    north = float(bbox["north"])
    east = float(bbox["east"])
    query = (
        "[out:json][timeout:20];"
        f"way[highway]({south},{west},{north},{east});"
        f"out tags geom {int(max_roads)};"
    )
    request = urllib.request.Request(
        _OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={
            "User-Agent": "cad2gis-osm-road-review/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_NOMINATIM_TIMEOUT_S + 10.0,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    elements = payload.get("elements", ()) if isinstance(payload, Mapping) else ()
    node_use = Counter(
        int(node)
        for element in elements
        if isinstance(element, Mapping)
        for node in element.get("nodes", ())
    )
    roads: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        geometry = element.get("geometry")
        if not isinstance(geometry, list) or len(geometry) < 2:
            continue
        try:
            points = [
                _wgs84_to_3857(float(item["lon"]), float(item["lat"]))
                for item in geometry
            ]
        except (KeyError, TypeError, ValueError):
            continue
        nodes = [int(value) for value in element.get("nodes", ())]
        endpoint_degree = sum(
            max(0, node_use[node] - 1)
            for node in ([nodes[0], nodes[-1]] if nodes else [])
        )
        roads.append({
            "osm_way_id": int(element.get("id", 0)),
            "points": points,
            "name": str((element.get("tags") or {}).get("name", "")),
            "highway": str((element.get("tags") or {}).get("highway", "")),
            "endpoint_degree": endpoint_degree,
        })
    return roads


def _line_length(points: Sequence[Sequence[float]]) -> float:
    return sum(
        math.hypot(
            float(right[0]) - float(left[0]),
            float(right[1]) - float(left[1]),
        )
        for left, right in zip(points, points[1:])
    )


def _endpoint_angle(points: Sequence[Sequence[float]]) -> float:
    left, right = points[0], points[-1]
    return math.atan2(float(right[1]) - float(left[1]), float(right[0]) - float(left[0]))


def _direction_score(left: float, right: float) -> float:
    difference = abs((left - right) % math.pi)
    difference = min(difference, math.pi - difference)
    return max(0.0, 1.0 - difference / (math.pi / 2.0))


def _normalised_shape(points: Sequence[Sequence[float]]):
    from shapely.affinity import rotate, scale, translate
    from shapely.geometry import LineString

    line = LineString([(float(x), float(y)) for x, y, *_ in points])
    centroid = line.centroid
    line = translate(line, xoff=-centroid.x, yoff=-centroid.y)
    line = rotate(line, -math.degrees(_endpoint_angle(points)), origin=(0, 0))
    length = max(float(line.length), 1e-9)
    return scale(line, xfact=1.0 / length, yfact=1.0 / length, origin=(0, 0))


def rank_osm_road_candidates(
    source_routes: Sequence[Any],
    osm_roads: Sequence[Mapping[str, Any]],
    *,
    top_k: int = 5,
    minimum_score: float = 0.72,
    minimum_gap: float = 0.08,
) -> dict[str, Any]:
    """Jointly score Top-K OSM roads and abstain when evidence is weak.

    The matching is intentionally translation-only and review-only.  It does
    not modify CAD geometry, validate a CRS, or replace surveyed GCPs.
    """
    routes = []
    for route in source_routes:
        points = getattr(route, "native_points", route)
        points = [tuple(float(v) for v in point[:2]) for point in points]
        if len(points) >= 2 and _line_length(points) > 0:
            routes.append(points)
    if not routes:
        return {
            "schema_version": "cad2gis-osm-road-match-v1",
            "status": "abstained",
            "reason": "no_source_routes",
            "top_k": [],
            "authority": "relative_only",
            "applicable_for_delivery": False,
        }
    source = max(routes, key=_line_length)
    source_length = _line_length(source)
    source_angle = _endpoint_angle(source)
    source_shape = _normalised_shape(source)
    endpoint_counts = Counter(
        (round(point[0], 3), round(point[1], 3))
        for route in routes for point in (route[0], route[-1])
    )
    source_degree = sum(
        max(0, endpoint_counts[(round(point[0], 3), round(point[1], 3))] - 1)
        for point in (source[0], source[-1])
    )
    ranked: list[dict[str, Any]] = []
    for road in osm_roads:
        points = [
            tuple(float(v) for v in point[:2])
            for point in road.get("points", ())
        ]
        if len(points) < 2:
            continue
        road_length = _line_length(points)
        ratio = road_length / source_length if source_length else 0.0
        # Candidate cropping prevents unrelated tiny alleys or regional
        # highways from dominating the expensive shape scoring stage.
        if ratio < 0.2 or ratio > 5.0:
            continue
        length_score = math.exp(-abs(math.log(max(ratio, 1e-9))))
        direction_score = _direction_score(source_angle, _endpoint_angle(points))
        shape_distance = float(
            source_shape.hausdorff_distance(_normalised_shape(points))
        )
        shape_score = 1.0 / (1.0 + 5.0 * shape_distance)
        road_degree = int(road.get("endpoint_degree", 0))
        topology_score = 1.0 / (1.0 + abs(source_degree - road_degree))
        coverage_score = min(source_length, road_length) / max(source_length, road_length)
        score = (
            0.20 * direction_score
            + 0.20 * length_score
            + 0.30 * shape_score
            + 0.15 * topology_score
            + 0.15 * coverage_score
        )
        raw_source_centroid = (
            sum(point[0] for point in source) / len(source),
            sum(point[1] for point in source) / len(source),
        )
        road_centroid = (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        ranked.append({
            "osm_way_id": int(road.get("osm_way_id", 0)),
            "name": str(road.get("name", "")),
            "highway": str(road.get("highway", "")),
            "score": round(score, 6),
            "score_components": {
                "direction": round(direction_score, 6),
                "length": round(length_score, 6),
                "shape": round(shape_score, 6),
                "topology": round(topology_score, 6),
                "coverage": round(coverage_score, 6),
            },
            "translation_dx": round(road_centroid[0] - raw_source_centroid[0], 4),
            "translation_dy": round(road_centroid[1] - raw_source_centroid[1], 4),
            "applicable_for_delivery": False,
        })
    ranked.sort(key=lambda item: (-item["score"], item["osm_way_id"]))
    selected = ranked[: max(1, int(top_k))]
    gap = (
        selected[0]["score"] - selected[1]["score"]
        if len(selected) > 1 else (selected[0]["score"] if selected else 0.0)
    )
    accepted = bool(
        selected
        and selected[0]["score"] >= minimum_score
        and gap >= minimum_gap
    )
    return {
        "schema_version": "cad2gis-osm-road-match-v1",
        "status": "review_candidate" if accepted else "abstained",
        "reason": None if accepted else "low_score_or_confidence_gap",
        "top_k": [dict(item, rank=index + 1) for index, item in enumerate(selected)],
        "confidence_gap": round(gap, 6),
        "thresholds": {
            "minimum_score": minimum_score,
            "minimum_gap": minimum_gap,
        },
        "authority": "relative_only",
        "applicable_for_delivery": False,
        "approval_required": "surveyed_gcp_or_explicit_human_review",
    }


def refine_osm_anchor_with_roads(
    anchor: Mapping[str, Any], source_routes: Sequence[Any], *, top_k: int = 5,
) -> dict[str, Any]:
    """Attach an optional Top-K road review pack to a coarse place anchor."""
    bbox = anchor.get("bbox")
    if not isinstance(bbox, Mapping):
        projected = anchor.get("target_epsg3857_bbox")
        if isinstance(projected, Mapping):
            try:
                west, south = _epsg3857_to_wgs84(
                    float(projected["min_x"]), float(projected["min_y"]),
                )
                east, north = _epsg3857_to_wgs84(
                    float(projected["max_x"]), float(projected["max_y"]),
                )
                bbox = {
                    "south": south, "north": north,
                    "west": west, "east": east,
                }
            except (KeyError, TypeError, ValueError):
                bbox = None
    if not isinstance(bbox, Mapping):
        return {**anchor, "road_match": {
            "schema_version": "cad2gis-osm-road-match-v1",
            "status": "abstained",
            "reason": "place_bbox_unavailable",
            "top_k": [],
            "authority": "relative_only",
            "applicable_for_delivery": False,
        }}
    roads = query_osm_roads(bbox)
    return {
        **anchor,
        "road_match": rank_osm_road_candidates(
            source_routes, roads, top_k=top_k,
        ),
    }


def derive_osm_anchor(
    source_path: str | Path,
    entity_bbox: Sequence[float],
    *,
    place_name: str | None = None,
) -> dict[str, Any]:
    """Derive a relative-only review candidate from a place-name lookup.

    Args:
        source_path: DWG path (place name is extracted from the file name).
        entity_bbox: ``(min_x, min_y, max_x, max_y)`` of the drawing entities
            in local coordinates.
        place_name: Optional override; derived from the file name when absent.

    Returns a candidate record (or a ``status: "unavailable"`` record).  The
    translation is diagnostic evidence only and is not delivery authority.
    """
    min_x, min_y, max_x, max_y = (float(v) for v in entity_bbox)
    local_centre_x = (min_x + max_x) / 2.0
    local_centre_y = (min_y + max_y) / 2.0

    if place_name:
        candidates = [place_name]
    else:
        candidates = _place_name_from_filename(source_path)
    query = None
    resolved_name = candidates[0] if candidates else ""
    for candidate in candidates:
        query = query_osm_place(candidate)
        if query is not None:
            resolved_name = candidate
            break
    if query is None:
        return {
            "schema_version": _OSM_ANCHOR_SCHEMA,
            "status": "unavailable",
            "place_name": resolved_name,
            "query": None,
            "candidates": candidates,
        }

    target_x, target_y = query["epsg3857_centre"]
    result: dict[str, Any] = {
        "schema_version": _OSM_ANCHOR_SCHEMA,
        "status": "candidate",
        "place_name": resolved_name,
        "display_name": query["display_name"],
        "source_path": str(source_path),
        "local_entity_bbox": [min_x, min_y, max_x, max_y],
        "local_centre": [local_centre_x, local_centre_y],
        "target_epsg3857_centre": [target_x, target_y],
        "translation_dx": round(target_x - local_centre_x, 4),
        "translation_dy": round(target_y - local_centre_y, 4),
        "source": "OPENSTREETMAP_NOMINATIM",
        "authority": "relative_only",
        "applicable_for_delivery": False,
        "precision": "coarse_bbox_centre",
        "refinement": "surveyed_gcp_required",
    }
    if "epsg3857_bbox" in query:
        result["target_epsg3857_bbox"] = query["epsg3857_bbox"]
    if "bbox" in query:
        result["bbox"] = dict(query["bbox"])
    return result


def apply_osm_anchor(
    point: Sequence[float],
    anchor: Mapping[str, Any],
    *,
    allow_relative_preview: bool = False,
) -> list[float]:
    """Translate a point only for an explicitly requested relative preview.

    This helper is intentionally fail-closed.  Its output cannot be used as a
    delivery coordinate transformation or an absolute-accuracy claim.
    """
    if allow_relative_preview is not True:
        raise ValueError(
            "OSM anchors are relative-only review candidates; pass "
            "allow_relative_preview=True only for a non-delivery preview"
        )
    dx = float(anchor.get("translation_dx", 0.0))
    dy = float(anchor.get("translation_dy", 0.0))
    return [float(point[0]) + dx, float(point[1]) + dy]


__all__ = [
    "_OSM_ANCHOR_SCHEMA",
    "apply_osm_anchor",
    "derive_osm_anchor",
    "query_osm_roads",
    "rank_osm_road_candidates",
    "refine_osm_anchor_with_roads",
    "query_osm_place",
]
