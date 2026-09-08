"""Revisioned, shape-preserving placement against relative OSM references.

Every revision maps immutable native CAD coordinates through one fixed base
operation and one total translation.  OSM fit quality is deliberately separate
from surveyed absolute accuracy.  Network access is optional; an Overpass JSON
snapshot makes matching reproducible without a service connection.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import CRS, Geod, Transformer
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from . import osm_anchor


SCHEMA = "cad2gis.osm-matching.v1"
ATTRIBUTION = {
    "text": "© OpenStreetMap contributors",
    "url": "https://www.openstreetmap.org/copyright",
    "license": "ODbL-1.0",
}
_TO_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _seal(record: dict) -> dict:
    result = copy.deepcopy(record)
    result.pop("match_sha256", None)
    result["match_sha256"] = _digest(result)
    return result


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise ValueError(f"{name} must be a SHA-256 hexadecimal digest")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _region(value):
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
        raise ValueError("region_id must be a simple non-empty identifier or null")
    return value


def _point(value: Sequence, name: str = "point") -> tuple[float, float]:
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) < 2:
        raise ValueError(f"{name} must contain two coordinates")
    return _number(value[0], name), _number(value[1], name)


def _scale(nominal) -> float:
    value = _number(nominal.source_length_to_m(1.0), "CAD unit scale")
    if value <= 0:
        raise ValueError("CAD unit scale must be positive")
    return value


def _crs_identity(value):
    if value is None or not str(value).strip():
        return None
    try:
        crs = CRS.from_user_input(value)
    except (ValueError, TypeError):
        return {"local_name": str(value)}
    authority = crs.to_authority()
    return {"authority": list(authority)} if authority else {"wkt": crs.to_wkt()}


def _nominal_identity(nominal):
    """Bind replay to the fixed CRS, native units and GEODATA operation."""
    return _digest({"source_crs": _crs_identity(nominal.source_crs),
                    "target_crs": _crs_identity(nominal.target_crs),
                    "unit_contract": nominal.unit_contract.to_manifest_dict(),
                    "geodata_registration": getattr(nominal, "geodata_registration", None),
                    "lineage_model": getattr(nominal, "lineage_model", "nominal_direct")})


def _validate_record(record: Mapping, nominal, source_sha256: str | None = None) -> dict:
    if not isinstance(record, Mapping) or record.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported OSM matching profile schema")
    result = copy.deepcopy(dict(record))
    saved_hash = result.pop("match_sha256", None)
    if saved_hash != _digest(result):
        raise ValueError("OSM matching profile hash mismatch")
    result["match_sha256"] = saved_hash
    _hash(result.get("source_sha256"), "source_sha256")
    if source_sha256 is not None and result["source_sha256"] != source_sha256:
        raise ValueError("OSM matching profile belongs to a different source")
    for field in ("source_crs", "target_crs"):
        if _crs_identity(result[field]) != _crs_identity(getattr(nominal, field)):
            raise ValueError(f"OSM matching profile {field} mismatch")
    if result.get("authority") != "relative_osm" or result.get("absolute_accuracy_verified") is not False:
        raise ValueError("OSM matching profile must retain relative-only authority")
    if result.get("status") not in {"matched", "coarse", "unavailable", "abstained"}:
        raise ValueError("Invalid OSM matching status")
    _region(result.get("region_id"))
    revision = result.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("OSM matching revision must be a positive integer")
    if revision > 1:
        _hash(result.get("parent_match_sha256"), "parent_match_sha256")
    base = result.get("base", {})
    if base.get("mode") not in {"nominal", "local"}:
        raise ValueError("Invalid OSM matching base operation")
    if base.get("nominal_operation_sha256") != _nominal_identity(nominal):
        raise ValueError("OSM matching nominal operation binding mismatch")
    scale = _number(base.get("scale_to_3857"), "scale_to_3857")
    if scale <= 0:
        raise ValueError("OSM matching base scale must be positive")
    if not math.isclose(_number(base.get("source_unit_to_m"), "source_unit_to_m"),
                        _scale(nominal), rel_tol=1e-12):
        raise ValueError("OSM matching profile CAD unit scale mismatch")
    latitude = _number(base.get("reference_latitude"), "reference_latitude")
    if abs(latitude) > 85.0:
        raise ValueError("OSM matching reference latitude is outside supported Mercator extent")
    expected_scale = 1.0 if base["mode"] == "nominal" else _scale(nominal) / math.cos(math.radians(latitude))
    if not math.isclose(scale, expected_scale, rel_tol=1e-12):
        raise ValueError("OSM matching base scale does not match its unit/latitude contract")
    _point(result.get("translation_3857"), "translation_3857")
    snapshot = result.get("osm_snapshot")
    if snapshot is not None:
        if not isinstance(snapshot, Mapping) or snapshot.get("sha256") != _digest(snapshot.get("roads")):
            raise ValueError("OSM road snapshot hash mismatch")
    return result


class LocalMatchingReference:
    """Unit-only local reference for registration, with no geographic claim.

    ``point`` expresses local distances in target axis units solely to support
    lineage comparisons. OSM matching uses native coordinates and this unit
    contract directly; it never interprets this reference as geographic data.
    """

    def __init__(self, source_crs, target_crs, unit_contract):
        from ..coordinate_runtime import set_traditional_axis_order
        from ..native_runtime import ensure_osgeo_runtime

        ensure_osgeo_runtime()
        from osgeo import osr

        if unit_contract.can_direct_transform:
            raise ValueError("LocalMatchingReference requires a local registration unit contract")
        self.source_crs, self.target_crs = source_crs, target_crs
        self.unit_contract = unit_contract
        self.unit_crs_manifest = unit_contract.to_manifest_dict()
        self.target_axis_scale_to_m = unit_contract.target_length_to_m(1.0)
        self.source_to_crs_axis_factor = unit_contract.source_length_to_m(1.0) / self.target_axis_scale_to_m
        self.source = osr.SpatialReference()
        self.source.SetLocalCS("Native CAD drawing coordinates; geographic location undetermined")
        self.source.SetLinearUnits(unit_contract.cad_unit.symbol, unit_contract.source_length_to_m(1.0))
        self.target = osr.SpatialReference()
        if self.target.SetFromUserInput(target_crs) != 0:
            raise ValueError("Invalid local matching target CRS")
        set_traditional_axis_order(self.source, osr)
        set_traditional_axis_order(self.target, osr)
        self.lineage_model = "local_unit_reference"
        self.coordinate_provenance = "DWG_DERIVED:local-unit-reference-geographic-location-undetermined"
        self.grid_length_provenance = "DWG_DERIVED:local-drawing-distance-in-metres"
        self.geodesic_provenance = "UNAVAILABLE:local-coordinates-have-no-geodesic-location"

    def point(self, point):
        x, y = _point(point)
        return x * self.source_to_crs_axis_factor, y * self.source_to_crs_axis_factor

    def points(self, points):
        return [self.point(point) for point in points]

    def target_to_source_point(self, point):
        x, y = _point(point)
        return x / self.source_to_crs_axis_factor, y / self.source_to_crs_axis_factor

    def target_to_source_points(self, points):
        return [self.target_to_source_point(point) for point in points]

    def source_length_to_m(self, value):
        return self.unit_contract.source_length_to_m(value)

    def target_length_to_m(self, value):
        return self.unit_contract.target_length_to_m(value)

    def grid_length_m(self, points):
        return self.target_length_to_m(sum(math.dist(a, b) for a, b in zip(points, points[1:])))

    def roundtrip_error(self, points):
        return max((self.source_length_to_m(math.dist(point[:2], self.target_to_source_point(self.point(point))))
                    for point in points), default=0.0)

    def engine_crosscheck_error(self, points):
        # There is no geographic CRS engine operation at this local boundary.
        return 0.0

    def operation_metadata(self, reference_point=None):
        return {"description": "Local CAD unit conversion only; no geographic CRS operation",
                "definition": "native CAD units to local distances in target axis units",
                "declared_accuracy_m": None, "geographic_operation_applied": False,
                "absolute_accuracy_validation": "unavailable until geographic registration",
                "unit_crs_contract": self.unit_crs_manifest}

    def geodesic_length(self, points):
        raise ValueError("Local CAD reference has no geodesic location; apply registration first")

    def qgis_rotation(self, native_anchor, cad_radians):
        from .georef import _qgis_rotation
        return _qgis_rotation(self.point, native_anchor, cad_radians)


class OSMMatchingTransformer:
    """Native-to-target wrapper with explicit OSM coordinate provenance."""

    def __init__(self, record: Mapping, nominal):
        self.record = _validate_record(record, nominal)
        if self.record["status"] not in {"matched", "coarse"}:
            raise ValueError("Only matched or coarse OSM records have an applicable transform")
        self.nominal = nominal
        self.source_crs = nominal.source_crs
        self.target_crs = nominal.target_crs
        self.unit_contract = nominal.unit_contract
        self.unit_crs_manifest = dict(getattr(nominal, "unit_crs_manifest", {}))
        self.source = getattr(nominal, "source", None)
        self.target = getattr(nominal, "target", None)
        self.target_axis_scale_to_m = nominal.target_length_to_m(1.0)
        self.geod = Geod(ellps="WGS84")
        self.target_to_geographic = Transformer.from_crs(self.target_crs, "EPSG:4326", always_xy=True)
        self._target_to_3857 = Transformer.from_crs(self.target_crs, "EPSG:3857", always_xy=True)
        self._from_3857 = Transformer.from_crs("EPSG:3857", self.target_crs, always_xy=True)
        self.lineage_model = "osm_relative_translation"
        token = self.record["match_sha256"][:12]
        self.coordinate_provenance = f"DWG_DERIVED:OSM-relative-translation-{token}"
        self.grid_length_provenance = f"DWG_DERIVED:{self.target_crs}-OSM-relative-{token}-geometry-length"
        self.geodesic_provenance = f"DWG_DERIVED:OSM-relative-{token}-WGS84-geodesic"

    def base_point(self, point):
        x, y = _point(point)
        if self.record["base"]["mode"] == "local":
            factor = self.record["base"]["scale_to_3857"]
            return x * factor, y * factor
        return _point(self._target_to_3857.transform(*self.nominal.point((x, y))), "nominal projection")

    def point(self, point):
        x, y = self.base_point(point)
        dx, dy = self.record["translation_3857"]
        return _point(self._from_3857.transform(x + dx, y + dy), "OSM projection")

    def points(self, points):
        return [self.point(point) for point in points]

    def target_to_source_point(self, point):
        x, y = self._target_to_3857.transform(*_point(point))
        dx, dy = self.record["translation_3857"]
        x, y = x - dx, y - dy
        if self.record["base"]["mode"] == "local":
            factor = self.record["base"]["scale_to_3857"]
            return x / factor, y / factor
        return self.nominal.target_to_source_point(self._from_3857.transform(x, y))

    def target_to_source_points(self, points):
        return [self.target_to_source_point(point) for point in points]

    def source_length_to_m(self, value):
        return self.nominal.source_length_to_m(value)

    def target_length_to_m(self, value):
        return self.nominal.target_length_to_m(value)

    def grid_length_m(self, points):
        return self.target_length_to_m(sum(math.dist(a, b) for a, b in zip(points, points[1:])))

    def geodesic_length(self, points):
        geographic = [self.target_to_geographic.transform(*point) for point in self.points(points)]
        return sum(abs(self.geod.inv(*a, *b)[2]) for a, b in zip(geographic, geographic[1:]))

    def qgis_rotation(self, native_anchor, cad_radians):
        from .georef import _qgis_rotation
        return _qgis_rotation(self.point, native_anchor, cad_radians)


def transformer_from_record(record, nominal):
    return OSMMatchingTransformer(record, nominal)


def load_match_profile(path, source_sha256, nominal):
    record = _validate_record(json.loads(Path(path).read_text(encoding="utf-8")), nominal, source_sha256)
    return transformer_from_record(record, nominal), record


def _native_routes(features) -> list[list[tuple[float, float]]]:
    routes = []
    seen = set()
    for feature in features:
        kind = getattr(feature, "feature_class", "CABLE")
        if kind not in {"CABLE", "ROAD"}:
            continue
        points = getattr(feature, "native_points", feature)
        values = [_point(value) for value in points]
        values = [value for index, value in enumerate(values) if index == 0 or value != values[index - 1]]
        if len(values) < 2:
            continue
        identity = min(tuple(values), tuple(reversed(values)))
        if identity not in seen:
            seen.add(identity)
            routes.append(values)
    return routes


def _parse_roads(payload) -> list[dict]:
    """Read Overpass geometry or node-referenced ways into one frozen format."""
    if not isinstance(payload, Mapping):
        raise ValueError("OSM snapshot must be a JSON object")
    elements = payload.get("elements", [])
    if not isinstance(elements, list):
        raise ValueError("OSM snapshot elements must be an array")
    nodes = {item.get("id"): item for item in elements if isinstance(item, Mapping) and item.get("type") == "node"}
    uses = Counter(node for item in elements if isinstance(item, Mapping)
                   and item.get("type") == "way" and (item.get("tags") or {}).get("highway")
                   for node in item.get("nodes", []))
    roads = []
    for item in elements:
        if not isinstance(item, Mapping) or item.get("type") != "way":
            continue
        tags = item.get("tags") or {}
        if not tags.get("highway"):
            continue
        geometry = item.get("geometry") or [nodes.get(key) for key in item.get("nodes", [])]
        points = []
        for value in geometry:
            if not isinstance(value, Mapping):
                points = []
                break
            lon, lat = _number(value.get("lon"), "OSM longitude"), _number(value.get("lat"), "OSM latitude")
            if not -180 <= lon <= 180 or not -85 <= lat <= 85:
                raise ValueError("OSM coordinates outside supported longitude/latitude extent")
            point = list(_point(_TO_3857.transform(lon, lat)))
            if not points or point != points[-1]:
                points.append(point)
        if len(points) >= 2:
            ids = item.get("nodes", [])
            roads.append({"osm_way_id": int(item.get("id", 0)), "points": points,
                          "name": str(tags.get("name", "")), "highway": str(tags["highway"]),
                          "endpoint_degree": sum(max(0, uses[node] - 1) for node in ([ids[0], ids[-1]] if ids else []))})
    return sorted(roads, key=lambda road: (road["osm_way_id"], _digest(road)))


def _normalise_roads(roads) -> list[dict]:
    result = []
    for road in roads:
        points = [list(_point(point, "OSM projected point")) for point in road.get("points", [])]
        if len(points) >= 2:
            result.append({"osm_way_id": int(road.get("osm_way_id", 0)), "points": points,
                           "name": str(road.get("name", "")), "highway": str(road.get("highway", "")),
                           "endpoint_degree": int(road.get("endpoint_degree", 0))})
    return sorted(result, key=lambda road: (road["osm_way_id"], _digest(road)))


def _extent(points):
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _centre(points):
    xmin, ymin, xmax, ymax = _extent(points)
    return (xmin + xmax) / 2.0, (ymin + ymax) / 2.0


def _bbox(points, margin=500.0):
    xmin, ymin, xmax, ymax = _extent(points)
    west, south = _TO_WGS84.transform(xmin - margin, ymin - margin)
    east, north = _TO_WGS84.transform(xmax + margin, ymax + margin)
    if not all(math.isfinite(v) for v in (west, south, east, north)) or not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise ValueError("OSM search bounds are outside supported geographic extent")
    return {"west": west, "south": south, "east": east, "north": north}


def _hint(anchor, place_name, cache_dir=None):
    # Existing geographic place centres are useful; legacy CAD translations
    # are not replayed because they may use another drawing, unit, or extent.
    if place_name:
        cache_key = {"place_name": " ".join(place_name.split()),
                     "provider": os.environ.get("CAD2GIS_NOMINATIM_URL", osm_anchor._NOMINATIM_URL).strip()}
        cache_path = Path(cache_dir) / f"osm-place-{_digest(cache_key)}.json" if cache_dir is not None else None
        if cache_path is not None and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("query") == cache_key and cached.get("sha256") == _digest(cached.get("result")):
                    hint = cached["result"]
                    centre = _point(hint["epsg3857_centre"], "cached place centre")
                    lon, lat = _TO_WGS84.transform(*centre)
                    if -180 <= lon <= 180 and -85 <= lat <= 85:
                        return hint
            except (ValueError, TypeError, KeyError, OSError):
                pass
        try:
            hint = osm_anchor.query_osm_place(place_name)
        except (ValueError, TypeError, KeyError, OverflowError):
            return None
        if hint is not None and cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(_canonical({"query": cache_key, "result": hint, "sha256": _digest(hint)}))
        return hint
    if isinstance(anchor, Mapping):
        centre = anchor.get("target_epsg3857_centre")
        if centre:
            x, y = _point(centre, "cached OSM place centre")
            lon, lat = _TO_WGS84.transform(x, y)
            if -180 <= lon <= 180 and -85 <= lat <= 85:
                return {"epsg3857_centre": [x, y], "lat": lat,
                        "display_name": anchor.get("display_name", anchor.get("place_name", "")),
                        "bbox": anchor.get("bbox") or _bbox([(x, y)], margin=1500.0)}
    return None


def _fetch_roads(bbox, cache_dir):
    key = _digest(bbox)
    path = Path(cache_dir) / f"osm-roads-{key}.json" if cache_dir is not None else None
    if path is not None and path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("bbox") == bbox and cached.get("sha256") == _digest(cached.get("roads")):
                return _normalise_roads(cached["roads"]), "cache"
        except (ValueError, TypeError, KeyError, OSError):
            pass
    try:
        roads = _normalise_roads(osm_anchor.query_osm_roads(bbox))
    except (ValueError, TypeError, KeyError, OverflowError):
        return [], "overpass_unavailable"
    if path is not None and roads:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Content-addressed cache is optional. The complete accepted snapshot
        # also lives in the sealed match record, which is the replay authority.
        path.write_bytes(_canonical({"bbox": bbox, "roads": roads, "sha256": _digest(roads)}))
    return roads, "overpass"


def _samples(routes, scale_to_ground):
    total_length = sum(LineString(route).length for route in routes)
    spacing = max(total_length / 350.0, 5.0 / scale_to_ground)
    locations, directions, route_ids = [], [], []
    for route_id, route in enumerate(routes):
        for a, b in zip(route, route[1:]):
            a, b = np.asarray(a), np.asarray(b)
            length = float(np.linalg.norm(b - a))
            if length <= 1e-9:
                continue
            count = min(100, max(2, math.ceil(length / spacing)))
            direction = (b - a) / length
            for offset in (np.arange(count) + 0.5) / count:
                locations.append(a + offset * (b - a))
                directions.append(direction)
                route_ids.append(route_id)
    return np.asarray(locations), np.asarray(directions), np.asarray(route_ids)


def _match_routes(routes, roads, scale_to_ground, initial_translation):
    """Score translation hypotheses against all sampled routes, never one line."""
    segments = [(a, b) for road in roads for a, b in zip(road["points"], road["points"][1:]) if math.dist(a, b) > 1e-8]
    source_segments = sum(len(route) - 1 for route in routes)
    if not segments or source_segments < 2:
        return None, {"reason": "insufficient_route_structure", "candidates": []}
    samples, directions, route_ids = _samples(routes, scale_to_ground)
    endpoints = np.asarray(segments, dtype=float)
    vectors = endpoints[:, 1] - endpoints[:, 0]
    lengths2 = np.sum(vectors * vectors, axis=1)
    tree = STRtree([LineString(segment) for segment in segments])
    tolerance = 25.0 / scale_to_ground

    def inspect(delta):
        moved = samples + delta
        ids = np.asarray(tree.nearest([Point(point) for point in moved]), dtype=int)
        starts, vectors_at = endpoints[ids, 0], vectors[ids]
        t = np.clip(np.sum((moved - starts) * vectors_at, axis=1) / lengths2[ids], 0.0, 1.0)
        feet = starts + t[:, None] * vectors_at
        distances = np.linalg.norm(moved - feet, axis=1)
        directional = np.abs(np.sum(directions * vectors_at, axis=1)) / np.sqrt(lengths2[ids])
        return distances, directional, feet - moved

    hypotheses = [np.asarray(initial_translation, dtype=float)]
    # Several complete CAD routes independently propose candidates. Endpoints
    # supplement centroids because OSM ways and CAD spans can be split differently.
    proposers = sorted(routes, key=lambda route: -LineString(route).length)[:16]
    roads_by_id = {road["osm_way_id"]: road for road in roads}
    for route in proposers:
        ranked = osm_anchor.rank_osm_road_candidates([route], roads, top_k=6, minimum_score=0.0, minimum_gap=0.0)
        for item in ranked["top_k"]:
            road = roads_by_id[item["osm_way_id"]]["points"]
            hypotheses.append(np.asarray([item["translation_dx"], item["translation_dy"]]))
            same_direction = np.dot(np.asarray(route[-1]) - route[0], np.asarray(road[-1]) - road[0]) >= 0
            left, right = (road[0], road[-1]) if same_direction else (road[-1], road[0])
            hypotheses.extend([np.asarray(left) - route[0], np.asarray(right) - route[-1]])
    unique = {}
    for hypothesis in hypotheses:
        key = tuple(np.round(hypothesis * scale_to_ground / 5.0).astype(np.int64))
        unique.setdefault(key, hypothesis)
    candidates = []
    for hypothesis in unique.values():
        delta = hypothesis.copy()
        for _ in range(5):
            distances, directional, offsets = inspect(delta)
            eligible = (distances < tolerance * 4.0) & (directional >= 0.7)
            if np.count_nonzero(eligible) < 3:
                break
            correction = np.median(offsets[eligible], axis=0)
            delta += correction
            if np.linalg.norm(correction) * scale_to_ground < 0.05:
                break
        distances, directional, _ = inspect(delta)
        support = (distances <= tolerance) & (directional >= 0.7)
        route_coverages = [float(np.mean(support[route_ids == index])) for index in range(len(routes))]
        coverage = float(np.mean(support))
        direction = float(np.mean(directional))
        residual_score = float(np.mean(np.exp(-distances / tolerance)))
        score = 0.55 * coverage + 0.20 * direction + 0.25 * residual_score
        candidates.append({"translation_3857": delta.tolist(), "score": score,
                           "coverage": coverage, "direction_consistency": direction,
                           "route_coverages": route_coverages,
                           "supported_route_fraction": float(np.mean(np.asarray(route_coverages) >= 0.6)),
                           "distance_p95_m": float(np.percentile(distances, 95)) * scale_to_ground})
    candidates.sort(key=lambda value: (-value["score"], value["translation_3857"]))
    distinct = []
    for candidate in candidates:
        if all(math.dist(candidate["translation_3857"], other["translation_3857"]) * scale_to_ground > 12.5 for other in distinct):
            distinct.append(candidate)
        if len(distinct) == 5:
            break
    best = distinct[0]
    gap = best["score"] - distinct[1]["score"] if len(distinct) > 1 else best["score"]
    accepted = (best["coverage"] >= 0.75 and best["direction_consistency"] >= 0.70
                and best["supported_route_fraction"] >= 0.75 and gap >= 0.06)
    quality = {"method": "multi_route_translation_consensus", "route_count": len(routes),
               "source_segment_count": source_segments, "sample_count": len(samples),
               "confidence_gap": gap, "candidates": distinct,
               "thresholds": {"corridor_m": 25.0, "coverage": 0.75,
                              "supported_route_fraction": 0.75, "confidence_gap": 0.06},
               "reason": None if accepted else "weak_or_ambiguous_network_alignment"}
    return best["translation_3857"] if accepted else None, quality


def match_osm(*, source: Path, source_sha256: str, features: list, nominal,
              coordinate_domain: dict, osm_data: Path | None = None,
              place_name: str | None = None, anchor: dict | None = None,
              cache_dir: Path | None = None, region_id: str | None = None):
    """Build a sealed OSM matching revision and its optional delivery transform."""
    _hash(source_sha256, "source_sha256")
    region_id = _region(region_id)
    native_routes = _native_routes(features)
    native_points = [point for route in native_routes for point in route]
    if not native_points:
        # Device-only drawing partitions still need a geographic review view.
        # Their native extent can seed coarse placement without inventing roads.
        native_points = [_point(point) for feature in features
                         for point in getattr(feature, "native_points", feature)]
    unit_to_m = _scale(nominal)
    nominal_valid = coordinate_domain.get("passed") is True and not isinstance(nominal, LocalMatchingReference)
    target_to_3857 = Transformer.from_crs(nominal.target_crs, "EPSG:3857", always_xy=True)
    nominal_points = [list(_point(target_to_3857.transform(*nominal.point(point)))) for point in native_points] if nominal_valid else []
    hint = None
    roads, snapshot_origin, bbox, offline_hash = [], "unavailable", None, None
    if native_points and osm_data is not None:
        path = Path(osm_data)
        payload = path.read_bytes()
        roads = _parse_roads(json.loads(payload.decode("utf-8-sig")))
        snapshot_origin = "offline_overpass"
        offline_hash = hashlib.sha256(payload).hexdigest()
    elif native_points:
        if place_name:
            hint = _hint(anchor, place_name, cache_dir=cache_dir)
            if hint is not None:
                bbox = hint.get("bbox") or _bbox([hint["epsg3857_centre"]], margin=1500.0)
        elif nominal_points:
            bbox = _bbox(nominal_points)
        else:
            hint = _hint(anchor, place_name, cache_dir=cache_dir)
            if hint is not None:
                bbox = hint.get("bbox") or _bbox([hint["epsg3857_centre"]], margin=1500.0)
        if bbox is not None:
            roads, snapshot_origin = _fetch_roads(bbox, cache_dir)
    road_points = [point for road in roads for point in road["points"]]
    centre = (_point(hint["epsg3857_centre"]) if hint else _centre(nominal_points) if nominal_points
              else _centre(road_points) if road_points else None)
    latitude = _TO_WGS84.transform(*centre)[1] if centre else 0.0
    if abs(latitude) > 85.0:
        raise ValueError("OSM matching latitude outside supported Mercator extent")
    scale_to_ground = math.cos(math.radians(latitude))
    base = {"mode": "nominal" if nominal_valid else "local",
            "source_unit_to_m": unit_to_m, "reference_latitude": latitude,
            "nominal_operation_sha256": _nominal_identity(nominal),
            "scale_to_3857": 1.0 if nominal_valid else unit_to_m / scale_to_ground}
    record = {"schema_version": SCHEMA, "source_sha256": source_sha256,
              "source_path": str(source), "source_crs": nominal.source_crs, "target_crs": nominal.target_crs,
              "revision": 1, "parent_match_sha256": None,
              "region_id": region_id,
              "status": "unavailable", "authority": "relative_osm", "absolute_accuracy_verified": False,
              "needs_review": True, "base": base, "translation_3857": [0.0, 0.0],
              "attribution": dict(ATTRIBUTION), "place_name": place_name,
              "search_bbox": bbox, "quality": {"reason": "no_geographic_reference"},
              "osm_snapshot": {"origin": snapshot_origin, "input_sha256": offline_hash,
                               "roads": roads, "sha256": _digest(roads)}}
    if not native_points:
        record["quality"] = {"reason": "empty_drawing_partition"}
        return None, _seal(record)
    if centre is None:
        if not native_routes:
            record["quality"] = {"reason": "no_source_routes"}
        return None, _seal(record)
    base_points = (nominal_points if nominal_valid else
                   [[p[0] * base["scale_to_3857"], p[1] * base["scale_to_3857"]] for p in native_points])
    base_centre = _centre(base_points)
    initial = ([0.0, 0.0] if nominal_valid and hint is None
               else [centre[0] - base_centre[0], centre[1] - base_centre[1]])
    if not native_routes:
        record["quality"] = {"reason": "no_source_routes", "automatic_matching_status": "not_attempted"}
        if hint is not None or road_points:
            record["status"] = "coarse"
            record["translation_3857"] = initial
            record["quality"]["fallback"] = "place_centre" if hint else "osm_snapshot_bbox_centre"
            record = _seal(record)
            return transformer_from_record(record, nominal), record
        return None, _seal(record)
    base_routes = ([[list(_point(target_to_3857.transform(*nominal.point(point)))) for point in route] for route in native_routes]
                   if nominal_valid else [[[p[0] * base["scale_to_3857"], p[1] * base["scale_to_3857"]] for p in route] for route in native_routes])
    if roads:
        translation, quality = _match_routes(base_routes, roads, scale_to_ground, initial)
        record["quality"] = quality
        record["status"] = "matched" if translation is not None else "abstained"
        record["needs_review"] = translation is None
        record["translation_3857"] = translation or initial
        if translation is None and not nominal_valid:
            # Reject the road hypothesis while retaining the independent area
            # hint so local drawings can still be reviewed and manually refined.
            # This uses the whole search/snapshot centre, never the best rejected
            # candidate or a stale legacy anchor translation.
            record["status"] = "coarse"
            record["quality"] = {**quality, "automatic_matching_status": "abstained",
                                 "fallback": "place_centre" if hint else "osm_snapshot_bbox_centre"}
    elif hint is not None:
        record["status"] = "coarse"
        record["translation_3857"] = initial
        record["quality"] = {"reason": "place_centre_only_roads_unavailable", "method": "coarse_place_centre"}
    else:
        record["status"] = "unavailable"
        record["quality"] = {"reason": "osm_roads_unavailable"}
    record = _seal(record)
    return (transformer_from_record(record, nominal) if record["status"] in {"matched", "coarse"} else None), record


def refine_match(record, nominal, controls, *, source_sha256, parent_run_sha256, region_id=None):
    """Fit a new total translation from train picks; retain holdouts as diagnostics."""
    original = _validate_record(record, nominal, source_sha256)
    _hash(parent_run_sha256, "parent_run_sha256")
    region_id = _region(original.get("region_id") if region_id is None else region_id)
    # A previously abstained candidate can be resolved with explicit paired
    # references. The base remains identical; its candidate shift is ignored.
    base_record = _seal({**original, "status": "coarse"})
    transform = transformer_from_record(base_record, nominal)
    target_to_3857 = Transformer.from_crs(original["target_crs"], "EPSG:3857", always_xy=True)
    compiled, train = [], []
    ids = set()
    for item in controls:
        if not isinstance(item, Mapping):
            raise ValueError("OSM refinement controls must be objects")
        enabled = item.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("Control enabled must be boolean")
        if not enabled:
            continue
        identity = str(item.get("point_id", "")).strip()
        if not identity or identity in ids:
            raise ValueError("Control point IDs must be non-empty and unique")
        ids.add(identity)
        role = item.get("role", "train")
        if role not in {"train", "check"}:
            raise ValueError("Control role must be train or check")
        if not CRS.from_user_input(item.get("target_crs")).equals(CRS.from_user_input(original["target_crs"])):
            raise ValueError("Control target_crs does not match the OSM match profile")
        native = _point([item.get("cad_x"), item.get("cad_y")], "control CAD point")
        target = _point([item.get("target_easting"), item.get("target_northing")], "control target point")
        reference = _point(target_to_3857.transform(*target), "control Mercator reference")
        origin = transform.base_point(native)
        weight = _number(item.get("weight", 1.0), "control weight")
        if weight <= 0:
            raise ValueError("Control weight must be positive")
        control = {"point_id": identity, "role": role, "cad_x": native[0], "cad_y": native[1],
                   "target_easting": target[0], "target_northing": target[1],
                   "target_crs": original["target_crs"], "enabled": True, "weight": weight,
                   "source": str(item.get("source", "OPENSTREETMAP_VISUAL_REFERENCE")),
                   "translation_3857": [reference[0] - origin[0], reference[1] - origin[1]]}
        for field in ("longitude", "latitude", "accuracy_m"):
            if field in item:
                control[field] = _number(item[field], f"control {field}")
        compiled.append(control)
        if role == "train":
            train.append(control)
    if len({(item["cad_x"], item["cad_y"]) for item in train}) < 2:
        raise ValueError("OSM refinement requires at least 2 distinct enabled training points")
    compiled.sort(key=lambda item: item["point_id"])
    total_weight = sum(item["weight"] for item in train)
    delta = [sum(item["translation_3857"][axis] * item["weight"] for item in train) / total_weight for axis in (0, 1)]
    result = copy.deepcopy(original)
    result.update({"revision": original["revision"] + 1, "parent_match_sha256": original["match_sha256"],
                   "parent_run_sha256": parent_run_sha256, "translation_3857": delta,
                   "region_id": region_id,
                   "status": "matched", "controls_sha256": _digest(compiled), "controls": compiled})
    candidate = transformer_from_record(_seal(result), nominal)
    residuals = []
    for item in compiled:
        actual = candidate.point([item["cad_x"], item["cad_y"]])
        distance = nominal.target_length_to_m(math.dist(actual, [item["target_easting"], item["target_northing"]]))
        residuals.append({"point_id": item["point_id"], "role": item["role"], "error_m": distance})

    def metrics(role):
        values = [item["error_m"] for item in residuals if item["role"] == role]
        return {"count": len(values), "rmse_m": math.sqrt(sum(value * value for value in values) / len(values)) if values else None,
                "max_m": max(values) if values else None}

    train_metrics, check_metrics = metrics("train"), metrics("check")
    result["quality"] = {"method": "manual_translation_from_original_base", "training": train_metrics,
                         "check": check_metrics, "residuals": residuals,
                         "train_metrics": train_metrics, "check_metrics": check_metrics,
                         "checks_used_for_fit": False, "absolute_accuracy_verified": False}
    result["needs_review"] = any(item["error_m"] > 40.0 for item in residuals)
    return _seal(result)
