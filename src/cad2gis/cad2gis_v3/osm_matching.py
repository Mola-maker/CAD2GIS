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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import CRS, Geod, Transformer
from shapely.geometry import LineString

from . import osm_anchor
from .osm_evidence import RoadSegmentIndex, build_match_evidence, osm_topology, validate_match_evidence


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


def _editor_state(value):
    """Bound server-owned pick/undo history to the control's sealed provenance."""
    if not isinstance(value, Mapping):
        raise ValueError("Control editor_state must be an object")
    try:
        size = len(_canonical(value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Control editor_state must contain finite JSON data") from exc
    if size > 32 * 1024:
        raise ValueError("Control editor_state exceeds 32 KiB")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported control editor_state schema")
    if value.get("last_action") not in {"pick", "edit", "nudge", "undo", "reset"}:
        raise ValueError("Invalid control editor_state last_action")
    if value.get("distance_method") != "WGS84_ellipsoidal_geodesic":
        raise ValueError("Invalid control editor_state distance_method")

    def target(point):
        lon, lat = _point(point, "editor_state target")
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError("Control editor_state target is outside WGS84 bounds")
        return [lon, lat]

    history = value.get("undo_targets")
    if not isinstance(history, list) or len(history) > 128:
        raise ValueError("Control editor_state undo_targets must contain at most 128 targets")
    state = {"schema_version": 1, "initial_target": target(value.get("initial_target")),
             "undo_targets": [target(point) for point in history], "last_action": value["last_action"],
             "distance_method": value["distance_method"]}
    if "last_nudge" in value:
        nudge = value["last_nudge"]
        if not isinstance(nudge, Mapping) or nudge.get("direction") not in {"east", "west", "north", "south"}:
            raise ValueError("Invalid control editor_state last_nudge")
        step = _number(nudge.get("step_m"), "editor_state step_m")
        if step not in {0.01, 0.1, 1.0}:
            raise ValueError("Control editor_state step_m must be 0.01, 0.1 or 1 metre")
        state["last_nudge"] = {"direction": nudge["direction"], "step_m": step}
    return state


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
    validate_match_evidence(result)
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
    roads = []
    for item in elements:
        if not isinstance(item, Mapping) or item.get("type") != "way":
            continue
        tags = item.get("tags") or {}
        if not isinstance(tags, Mapping):
            raise ValueError("OSM way tags must be an object")
        if not tags.get("highway"):
            continue
        ids = item.get("nodes", [])
        if not isinstance(ids, list) or any(type(node) is not int for node in ids):
            raise ValueError("OSM way nodes must be integer node IDs")
        geometry = item.get("geometry") or [nodes.get(key) for key in ids]
        if not isinstance(geometry, list) or (ids and len(ids) != len(geometry)):
            raise ValueError("OSM node IDs and geometry must have matching lengths")
        points = []
        for value in geometry:
            if not isinstance(value, Mapping):
                raise ValueError("OSM road geometry is incomplete; all referenced nodes are required")
            lon, lat = _number(value.get("lon"), "OSM longitude"), _number(value.get("lat"), "OSM latitude")
            if not -180 <= lon <= 180 or not -85 <= lat <= 85:
                raise ValueError("OSM coordinates outside supported longitude/latitude extent")
            point = list(_point(_TO_3857.transform(lon, lat)))
            # Keep the one-to-one node/vertex relation, including coincident
            # distinct IDs. Zero-length segments are excluded only from fitting.
            points.append(point)
        if len(points) >= 2:
            roads.append({"osm_way_id": int(item.get("id", 0)), "points": points,
                          "name": str(tags.get("name", "")), "highway": str(tags["highway"]),
                          "node_ids": list(ids),
                          **{key: str(tags[key]) for key in ("layer", "bridge", "tunnel", "junction") if key in tags}})
    # Count actual adjacent nodes at endpoints; way occurrence counts miss
    # interior junctions and overcount closed or repeated node references.
    topology = osm_topology(roads)
    degrees = {node["node_id"]: node["degree"] for node in topology["nodes"]}
    for road in roads:
        ids = road["node_ids"]
        road["endpoint_degree"] = sum(max(0, degrees[node] - 1) for node in ([ids[0], ids[-1]] if ids else []))
    return _normalise_roads(roads)


def _normalise_roads(roads) -> list[dict]:
    result = []
    for road in roads:
        points = [list(_point(point, "OSM projected point")) for point in road.get("points", [])]
        if len(points) >= 2:
            ids = road.get("node_ids", [])
            if not isinstance(ids, list) or any(type(node) is not int for node in ids) or (ids and len(ids) != len(points)):
                raise ValueError("OSM projected node IDs must align with geometry vertices")
            result.append({"osm_way_id": int(road.get("osm_way_id", 0)), "points": points,
                           "name": str(road.get("name", "")), "highway": str(road.get("highway", "")),
                           "endpoint_degree": int(road.get("endpoint_degree", 0)), "node_ids": list(ids),
                           **{key: str(road[key]) for key in ("layer", "bridge", "tunnel", "junction") if key in road}})
    osm_topology(result)  # Fail closed on contradictory coordinates for one ID.
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
    """Sample by route arclength so redundant CAD vertices carry no extra vote."""
    total_length = sum(LineString(route).length for route in routes)
    spacing = max(total_length / 350.0, 5.0 / scale_to_ground)
    locations, directions, route_ids = [], [], []
    for route_id, route in enumerate(routes):
        points = np.asarray(route, dtype=float)
        vectors = np.diff(points, axis=0)
        lengths = np.linalg.norm(vectors, axis=1)
        keep = lengths > 1e-9
        starts, vectors, lengths = points[:-1][keep], vectors[keep], lengths[keep]
        if not len(lengths):
            continue
        cumulative = np.cumsum(lengths)
        count = max(2, math.ceil(cumulative[-1] / spacing))
        offsets = (np.arange(count) + 0.5) * (cumulative[-1] / count)
        indices = np.searchsorted(cumulative, offsets, side="right")
        previous = np.concatenate(([0.0], cumulative[:-1]))
        fractions = (offsets - previous[indices]) / lengths[indices]
        locations.extend(starts[indices] + fractions[:, None] * vectors[indices])
        directions.extend(vectors[indices] / lengths[indices, None])
        route_ids.extend([route_id] * count)
    return np.asarray(locations), np.asarray(directions), np.asarray(route_ids)


def _observability(directions, weights):
    """Normal information for two translation parameters, independent of scale.

    For a line normal n, residual(delta) = n.T @ delta + constant. Thus
    H = sum(w * n @ n.T) has rank one for any number of parallel spans. More
    vertices or separated parallel lines cannot provide the missing constraint.
    Eigenvalues are normalized by length; they are not survey uncertainty.
    """
    directions, weights = np.asarray(directions), np.asarray(weights)
    if not len(directions) or float(np.sum(weights)) <= 0:
        return {"rank": 0, "eigenvalues": [0.0, 0.0], "minimum_eigenvalue": 0.0,
                "condition_number": None, "observable": False}
    normals = np.column_stack((-directions[:, 1], directions[:, 0]))
    information = normals.T @ (weights[:, None] * normals) / np.sum(weights)
    eigenvalues = np.maximum(np.linalg.eigvalsh(information), 0.0)
    return {"rank": int(np.count_nonzero(eigenvalues > 1e-8)), "eigenvalues": eigenvalues.tolist(),
            "minimum_eigenvalue": float(eigenvalues[0]),
            "condition_number": float(eigenvalues[1] / eigenvalues[0]) if eigenvalues[0] > 1e-8 else None,
            "observable": bool(eigenvalues[0] >= 0.04)}


def _sample_weights(routes, route_ids):
    counts = np.bincount(route_ids, minlength=len(routes))
    per_route = [LineString(route).length / max(count, 1) for route, count in zip(routes, counts)]
    return np.asarray(per_route)[route_ids]


def _landmark_independence(axes):
    """Best two-direction constraint; duplicate nearby axes add no vote."""
    if len(axes) < 2:
        return 0.0
    axes = np.asarray(axes)
    return (1.0 - float(np.min(np.abs(axes @ axes.T)))) * 0.5


def _geometric_landmarks(routes):
    """Return two-direction geometric anchors, without asserting connectivity.

    Coincident endpoints allow split ways to propose the same translation as
    an unsplit polyline. These are geometric hypotheses only: bridges and
    coincident but disconnected ways do not become connected OSM nodes.
    """
    incident = {}
    for route in routes:
        for left, right in zip(route, route[1:]):
            vector = np.subtract(right, left)
            length = float(np.linalg.norm(vector))
            if length <= 1e-8:
                continue
            unit = vector / length
            if unit[0] < 0 or (unit[0] == 0 and unit[1] < 0):
                unit = -unit
            for point in (left, right):
                incident.setdefault(tuple(point), set()).add(tuple(unit))
    landmarks = []
    for point, raw_axes in sorted(incident.items()):
        axes = []
        # Canonical ordering precedes near-parallel deduplication: greedy
        # grouping in input order can change observability at a threshold.
        for unit in sorted(raw_axes):
            if all(abs(float(np.dot(unit, axis))) < 0.9999 for axis in axes):
                axes.append(unit)
        if len(axes) < 2:
            continue
        axes = np.asarray(axes)
        independence = _landmark_independence(axes)
        if independence >= 0.04:
            landmarks.append((point, axes, independence))
    return landmarks


def _landmark_hypotheses(routes, roads, scale_to_ground):
    """Vote for translations across the snapshot, independent of way lengths.

    Whole-way shape ranking can exclude every correct road when OSM ways are
    short fragments. Two nonparallel directions supply a translation anchor;
    repeated anchors vote for the same displacement. Five-metre bins rank
    search areas only; distinct translations inside a bin remain separate.
    """
    source = _geometric_landmarks(routes)
    reference = _geometric_landmarks([road["points"] for road in roads])
    selected = sorted(source, key=lambda item: (-item[2], item[0]))[:32]
    votes = {}
    pair_count = 0
    for source_id, (point, axes, _) in enumerate(selected):
        for target, target_axes, _ in reference:
            similarities = np.abs(axes @ target_axes.T)
            compatible = similarities >= 0.95
            source_supported = axes[np.any(compatible, axis=1)]
            target_supported = target_axes[np.any(compatible, axis=0)]
            if (_landmark_independence(source_supported) < 0.04
                    or _landmark_independence(target_supported) < 0.04):
                continue
            delta = np.subtract(target, point)
            key = tuple(np.rint(delta * scale_to_ground / 5.0).astype(np.int64))
            entry = votes.setdefault(key, {"sources": set(), "translations": {}})
            entry["sources"].add(source_id)
            fine_key = tuple(np.rint(delta * scale_to_ground / 0.05).astype(np.int64))
            previous = entry["translations"].get(fine_key)
            if previous is None or tuple(delta) < tuple(previous):
                entry["translations"][fine_key] = delta
            pair_count += 1
    ordered = sorted(votes.items(), key=lambda item: (-len(item[1]["sources"]), item[0]))
    # Averaging distinct votes could turn two nearby exact road networks into
    # one seed and manufacture a confidence gap. Retain each numerical seed.
    selected_bins = ordered[:64]
    seeds = [entry["translations"][key] for _, entry in selected_bins
             for key in sorted(entry["translations"])]
    seed_count = len(seeds)
    seeds = seeds[:256]
    return seeds, {"method": "two_direction_geometric_translation_votes",
                   "source_landmark_count": len(source), "selected_source_landmarks": len(selected),
                   "reference_landmark_count": len(reference), "compatible_pair_count": pair_count,
                   "translation_bin_count": len(votes), "selected_translation_bins": len(selected_bins),
                   "distinct_seed_count": seed_count, "selected_seed_count": len(seeds),
                   "source_limit": 32, "bin_limit": 64, "hypothesis_limit": 256, "seed_bin_m": 5.0,
                   "numerical_seed_merge_m": 0.05,
                   "direction_consistency": 0.95,
                   "seed_budget_exhausted": seed_count > 256,
                   "truncated": len(source) > 32 or len(votes) > 64 or seed_count > 256,
                   "topology_connections_inferred": False}


def _match_routes(routes, roads, scale_to_ground, initial_translation):
    """Fit bounded translation hypotheses using robust point-to-line consensus."""
    index = RoadSegmentIndex(roads)
    source_segments = sum(len(route) - 1 for route in routes)
    if not index.references or source_segments < 2:
        return None, {"reason": "insufficient_route_structure", "candidates": []}
    samples, directions, route_ids = _samples(routes, scale_to_ground)
    if not len(samples):
        return None, {"reason": "insufficient_route_structure", "candidates": []}
    weights = _sample_weights(routes, route_ids)
    source_vectors = np.asarray([np.subtract(b, a) for route in routes for a, b in zip(route, route[1:])
                                 if math.dist(a, b) > 1e-8])
    source_lengths = np.linalg.norm(source_vectors, axis=1)
    source_structure = _observability(source_vectors / source_lengths[:, None], source_lengths)
    quality = {"method": "observable_multi_route_translation_consensus", "route_count": len(routes),
               "sampling_method": "uniform_route_arclength", "sample_weighting": "represented_route_length",
               "distance_method": "web_mercator_reference_latitude_cosine_approximation",
               "source_segment_count": source_segments, "sample_count": len(samples),
               "source_observability": source_structure,
               "search_complete": False, "confidence_interpretation": "score_gap_among_bounded_hypotheses",
               "thresholds": {"corridor_m": 25.0, "coverage": 0.75, "direction_consistency": 0.70,
                              "supported_route_fraction": 0.75, "confidence_gap": 0.06,
                              "minimum_normal_eigenvalue": 0.04, "candidate_merge_distance_m": 0.25}}
    if not source_structure["observable"]:
        return None, {**quality, "reason": "source_geometry_unobservable", "candidates": []}
    tolerance = 25.0 / scale_to_ground

    def inspect(delta, radius):
        result = index.inspect(samples + delta, directions, max_distance=radius)
        result["directional"] = np.abs(np.sum(directions * result["directions"], axis=1))
        return result

    hypotheses = [np.asarray(initial_translation, dtype=float)]
    landmark_seeds, landmark_search = _landmark_hypotheses(routes, roads, scale_to_ground)
    hypotheses.extend(landmark_seeds)
    quality["landmark_search"] = landmark_search
    quality["correspondence_method"] = "nearest_direction_compatible_segment_within_radius"
    quality["refinement"] = {"maximum_iterations": 100, "convergence_step_m": 0.01}
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
        # Seed binning is numerical work reduction, never the acceptance corridor.
        key = tuple(np.round(hypothesis * scale_to_ground / 0.05).astype(np.int64))
        unique.setdefault(key, hypothesis)
    candidates = []
    for hypothesis in unique.values():
        delta = hypothesis.copy()
        converged = False
        last_step_m = None
        for iteration in range(100):
            fitted = inspect(delta, tolerance * 4.0)
            eligible = (fitted["distances"] < tolerance * 4.0) & (fitted["directional"] >= 0.7)
            if np.count_nonzero(eligible) < 3:
                break
            vectors = fitted["directions"][eligible]
            normals = np.column_stack((-vectors[:, 1], vectors[:, 0]))
            signed = np.sum(fitted["offsets"][eligible] * normals, axis=1)
            # Huber influence limits distant parallel carriageways/outliers. A
            # coordinate-wise median freezes a minority direction's correction;
            # the normal equations retain its independent spatial constraint.
            robust = weights[eligible] / np.maximum(1.0, np.abs(signed) / (tolerance * 0.5))
            hessian = normals.T @ (robust[:, None] * normals)
            gradient = normals.T @ (robust * signed)
            correction = np.linalg.lstsq(hessian, gradient, rcond=1e-8)[0]
            delta += correction
            last_step_m = float(np.linalg.norm(correction)) * scale_to_ground
            if last_step_m < 0.01:
                converged = True
                break
        fitted = inspect(delta, tolerance)
        distances, directional = fitted["distances"], fitted["directional"]
        support = (distances <= tolerance) & (directional >= 0.7)
        route_coverages = [float(np.mean(support[route_ids == i])) for i in range(len(routes))]
        coverage = float(np.average(support, weights=weights))
        direction = float(np.average(directional, weights=weights))
        residual_score = float(np.average(np.exp(-distances / tolerance), weights=weights))
        score = 0.55 * coverage + 0.20 * direction + 0.25 * residual_score
        structure = _observability(fitted["directions"][support], weights[support])
        candidates.append({"translation_3857": delta.tolist(), "score": score,
                           "refinement_converged": converged, "refinement_iterations": iteration + 1,
                           "last_refinement_step_m": last_step_m,
                           "coverage": coverage, "direction_consistency": direction,
                           "route_coverages": route_coverages, "observability": structure,
                           "supported_route_fraction": float(np.mean(np.asarray(route_coverages) >= 0.6)),
                           "distance_p95_m": float(np.percentile(distances, 95)) * scale_to_ground})
    candidates.sort(key=lambda value: (-value["score"], value["translation_3857"]))
    distinct = []
    for candidate in candidates:
        # A road 8 m away is a different placement, even inside a 25 m corridor.
        if all(math.dist(candidate["translation_3857"], other["translation_3857"]) * scale_to_ground > 0.25 for other in distinct):
            distinct.append(candidate)
        if len(distinct) == 5:
            break
    best = distinct[0]
    gap = best["score"] - distinct[1]["score"] if len(distinct) > 1 else best["score"]
    accepted = (best["coverage"] >= 0.75 and best["direction_consistency"] >= 0.70
                and best["supported_route_fraction"] >= 0.75 and gap >= 0.06
                and best["observability"]["observable"]
                and best["refinement_converged"]
                and not landmark_search["seed_budget_exhausted"])
    quality.update({"confidence_gap": gap, "hypothesis_count": len(unique), "candidates": distinct,
                    "reason": None if accepted else "weak_or_ambiguous_network_alignment"})
    if not best["observability"]["observable"]:
        quality["reason"] = "matched_geometry_unobservable"
    elif not best["refinement_converged"]:
        quality["reason"] = "translation_refinement_not_converged"
    if landmark_search["seed_budget_exhausted"]:
        quality["reason"] = "landmark_hypothesis_budget_exhausted"
    return best["translation_3857"] if accepted else None, quality


def _seal_with_evidence(record, native_routes, base_routes):
    samples, directions, route_ids = _samples(base_routes, math.cos(math.radians(record["base"]["reference_latitude"])))
    record["evidence"] = build_match_evidence(record, native_routes, base_routes,
                                             samples=samples, directions=directions, route_ids=route_ids)
    return _seal(record)


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
    base_routes = ([[list(_point(target_to_3857.transform(*nominal.point(point)))) for point in route] for route in native_routes]
                   if nominal_valid else [[[p[0] * base["scale_to_3857"], p[1] * base["scale_to_3857"]] for p in route] for route in native_routes])
    if not native_points:
        record["quality"] = {"reason": "empty_drawing_partition"}
        return None, _seal_with_evidence(record, native_routes, base_routes)
    if centre is None:
        if not native_routes:
            record["quality"] = {"reason": "no_source_routes"}
        return None, _seal_with_evidence(record, native_routes, base_routes)
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
            record = _seal_with_evidence(record, native_routes, base_routes)
            return transformer_from_record(record, nominal), record
        return None, _seal_with_evidence(record, native_routes, base_routes)
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
    record = _seal_with_evidence(record, native_routes, base_routes)
    return (transformer_from_record(record, nominal) if record["status"] in {"matched", "coarse"} else None), record


def refine_match(record, nominal, controls, *, source_sha256, parent_run_sha256, region_id=None):
    """Fit a new total translation from train picks; retain holdouts as diagnostics."""
    original = _validate_record(record, nominal, source_sha256)
    _hash(parent_run_sha256, "parent_run_sha256")
    region_id = _region(original.get("region_id") if region_id is None else region_id)
    # A previously abstained candidate can be resolved with explicit paired
    # references. The base remains identical; its candidate shift is ignored.
    base_record = _seal({**{key: value for key, value in original.items() if key != "evidence"}, "status": "coarse"})
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
        if "editor_state" in item:
            control["editor_state"] = _editor_state(item["editor_state"])
        compiled.append(control)
        if role == "train":
            train.append(control)
    if len({(item["cad_x"], item["cad_y"]) for item in train}) < 2:
        raise ValueError("OSM refinement requires at least 2 distinct enabled training points")
    compiled.sort(key=lambda item: item["point_id"])
    total_weight = sum(item["weight"] for item in train)
    delta = [sum(item["translation_3857"][axis] * item["weight"] for item in train) / total_weight for axis in (0, 1)]
    result = copy.deepcopy(original)
    result.pop("evidence", None)  # Recomputed after the new revision's fit.
    result.update({"revision": original["revision"] + 1, "parent_match_sha256": original["match_sha256"],
                   "parent_run_sha256": parent_run_sha256, "translation_3857": delta,
                   "region_id": region_id,
                   "status": "matched", "controls_sha256": _digest(compiled), "controls": compiled})
    candidate = transformer_from_record(_seal(result), nominal)
    residuals = []
    for item in compiled:
        actual = candidate.point([item["cad_x"], item["cad_y"]])
        target = [item["target_easting"], item["target_northing"]]
        # Projected axis metres (especially EPSG:3857) are not ground metres.
        actual_geo = _point(candidate.target_to_geographic.transform(*actual), "actual geographic control")
        target_geo = _point(candidate.target_to_geographic.transform(*target), "target geographic control")
        distance = _number(abs(candidate.geod.inv(*actual_geo, *target_geo)[2]), "control ground residual")
        residuals.append({"point_id": item["point_id"], "role": item["role"], "error_m": distance,
                          "grid_error_m": nominal.target_length_to_m(math.dist(actual, target))})

    def metrics(role):
        values = [item["error_m"] for item in residuals if item["role"] == role]
        return {"count": len(values), "rmse_m": math.sqrt(sum(value * value for value in values) / len(values)) if values else None,
                "max_m": max(values) if values else None}

    train_metrics, check_metrics = metrics("train"), metrics("check")
    result["quality"] = {"method": "manual_translation_from_original_base", "training": train_metrics,
                         "residual_distance_method": "WGS84_ellipsoidal_geodesic",
                         "check": check_metrics, "residuals": residuals,
                         "train_metrics": train_metrics, "check_metrics": check_metrics,
                         "checks_used_for_fit": False, "absolute_accuracy_verified": False}
    result["needs_review"] = any(item["error_m"] > 40.0 for item in residuals)
    if original.get("evidence") is not None:
        routes = original["evidence"]["cad_routes"]
        return _seal_with_evidence(result, [item["native_points"] for item in routes],
                                  [item["base_points_3857"] for item in routes])
    return _seal(result)
