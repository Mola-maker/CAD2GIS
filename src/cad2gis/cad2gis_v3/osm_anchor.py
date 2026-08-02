"""OSM place-name geolocation for local engineering coordinates.

DWGs often declare ``CGEOCS=WGS84.PseudoMercator`` while their entities use
local engineering coordinates.  This module extracts the place name from the
source filename, asks the OSM Nominatim API for the place's real location,
and derives a bbox-centre translation that can be stored per project as a
coarse anchor.  A later optional GCP review can refine it.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_TIMEOUT_S = 15.0
_OSM_ANCHOR_SCHEMA = "cad2gis-osm-anchor-v1"

_LOCALITY_KEYWORDS = re.compile(
    r"(?i)(kelurahan|kecamatan|kabupaten|desa|dusun|rw|rt|kampung|"
    r"village|town|city|district|provinsi)"
)
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
    name = re.sub(r"(?i)^APD\s*[-_]?\s*", "", name)
    # Strip trailing project qualifiers like " - SF" / " - MAIN".
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
    import math
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * 20037508.34 / 180.0
    return x, y


def derive_osm_anchor(
    source_path: str | Path,
    entity_bbox: Sequence[float],
    *,
    place_name: str | None = None,
) -> dict[str, Any]:
    """Derive a coarse translation so local bbox centre lands on the OSM place.

    Args:
        source_path: DWG path (place name is extracted from the file name).
        entity_bbox: ``(min_x, min_y, max_x, max_y)`` of the drawing entities
            in local coordinates.
        place_name: Optional override; derived from the file name when absent.

    Returns an anchor record (or a ``status: "unavailable"`` record).
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
        "status": "derived",
        "place_name": resolved_name,
        "display_name": query["display_name"],
        "source_path": str(source_path),
        "local_entity_bbox": [min_x, min_y, max_x, max_y],
        "local_centre": [local_centre_x, local_centre_y],
        "target_epsg3857_centre": [target_x, target_y],
        "translation_dx": round(target_x - local_centre_x, 4),
        "translation_dy": round(target_y - local_centre_y, 4),
        "source": "OPENSTREETMAP_NOMINATIM",
        "precision": "coarse_bbox_centre",
        "refinement": "gcp_optional",
    }
    if "epsg3857_bbox" in query:
        result["target_epsg3857_bbox"] = query["epsg3857_bbox"]
    return result


def apply_osm_anchor(
    point: Sequence[float],
    anchor: Mapping[str, Any],
) -> list[float]:
    """Translate one local point by the anchor's stored translation."""
    dx = float(anchor.get("translation_dx", 0.0))
    dy = float(anchor.get("translation_dy", 0.0))
    return [float(point[0]) + dx, float(point[1]) + dy]


__all__ = [
    "_OSM_ANCHOR_SCHEMA",
    "apply_osm_anchor",
    "derive_osm_anchor",
    "query_osm_place",
]
