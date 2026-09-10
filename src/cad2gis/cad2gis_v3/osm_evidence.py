"""Replayable vector evidence for OSM-relative registration.

The sealed matching record owns the OSM snapshot. This module binds original
CAD routes, explicit topology and residuals to that snapshot, and derives WGS84
GeoJSON views without snapping or changing either source. Raster basemaps are
never coordinate or topology evidence.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import defaultdict

import numpy as np
from pyproj import Transformer
from shapely import box as shapely_box, points as shapely_points
from shapely.geometry import LineString
from shapely.strtree import STRtree


SCHEMA = "cad2gis.osm-evidence.v1"
_TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


class RoadSegmentIndex:
    """A deterministic nearest-segment view, with source way/segment lineage."""

    def __init__(self, roads):
        segments, references = [], []
        for road in roads:
            for index, (left, right) in enumerate(zip(road["points"], road["points"][1:])):
                if math.dist(left, right) > 1e-8:
                    segments.append((left, right))
                    references.append({"osm_way_id": road["osm_way_id"], "segment_index": index})
        self.references = references
        self.endpoints = np.asarray(segments, dtype=float).reshape((-1, 2, 2))
        self.vectors = self.endpoints[:, 1] - self.endpoints[:, 0]
        self.lengths2 = np.sum(self.vectors * self.vectors, axis=1)
        self.units = self.vectors / np.sqrt(self.lengths2)[:, None]
        self.tree = STRtree([LineString(segment) for segment in segments])
        # Stable identity, including an orientation-independent geometry key,
        # makes bounded correspondence ties independent of snapshot order.
        identity_order = sorted(range(len(segments)), key=lambda i: (
            references[i]["osm_way_id"], tuple(sorted(tuple(point) for point in segments[i])),
            references[i]["segment_index"]))
        self.identity_ranks = np.empty(len(segments), dtype=int)
        self.identity_ranks[identity_order] = np.arange(len(segments))

    def inspect(self, locations, directions=None, *, max_distance=None, min_direction_consistency=0.7):
        """Find nearest segments, optionally among compatible local directions.

        Without a radius, preserve geometric-nearest selection with directional
        tie breaking. With a radius (in index coordinate units), consider every
        segment within that distance before selecting the nearest directionally
        compatible one. This avoids a crossing stealing a nearby road's sample.
        If none qualifies, retain the geometric nearest for finite diagnostics;
        callers must still apply their distance and direction support gates.
        """
        locations = np.asarray(locations, dtype=float).reshape((-1, 2))
        if not len(self.endpoints) or not len(locations):
            raise ValueError("Nearest road inspection requires nonempty segments and locations")
        if directions is not None:
            directions = np.asarray(directions, dtype=float)
        if max_distance is not None:
            if not math.isfinite(max_distance) or max_distance < 0:
                raise ValueError("Road search max_distance must be finite and nonnegative")
            if not math.isfinite(min_direction_consistency) or not 0 <= min_direction_consistency <= 1:
                raise ValueError("Road search minimum direction consistency must be between zero and one")
            if directions is None or directions.shape != locations.shape:
                raise ValueError("Bounded road inspection requires directions for every location")
            lengths = np.linalg.norm(directions, axis=1)
            if not np.all(np.isfinite(directions)) or np.any(lengths <= 0):
                raise ValueError("Bounded road inspection requires finite nonzero directions")
            directions = directions / lengths[:, None]
        pairs = np.asarray(self.tree.query_nearest(shapely_points(locations), all_matches=True), dtype=int)
        alignment = (np.abs(np.sum(directions[pairs[0]] * self.units[pairs[1]], axis=1))
                     if directions is not None else np.zeros(len(pairs[0])))
        tie_order = self.identity_ranks[pairs[1]] if max_distance is not None else pairs[1]
        order = np.lexsort((tie_order, -alignment, pairs[0]))
        ordered = pairs[:, order]
        first = np.concatenate(([True], ordered[0, 1:] != ordered[0, :-1]))
        ids = ordered[1, first]
        unresolved = np.empty(0, dtype=int)
        if max_distance is not None:
            # A compatible geometric-nearest winner is necessarily also the
            # nearest compatible segment. Its exact ties already follow the
            # bounded ordering above, so only incompatible winners need search.
            nearest_alignment = np.abs(np.sum(directions * self.units[ids], axis=1))
            unresolved = np.flatnonzero(nearest_alignment < min_direction_consistency)
        if len(unresolved):
            # The index queries boxes; the exact clipped segment distance below
            # enforces a circular radius without relying on GEOS dwithin.
            remaining = locations[unresolved]
            boxes = shapely_box(remaining[:, 0] - max_distance, remaining[:, 1] - max_distance,
                                remaining[:, 0] + max_distance, remaining[:, 1] + max_distance)
            nearby = np.asarray(self.tree.query(boxes), dtype=int)
            sample_ids, segment_ids = nearby
            sample_ids = unresolved[sample_ids]
            vectors = self.vectors[segment_ids]
            starts = self.endpoints[segment_ids, 0]
            fractions = np.sum((locations[sample_ids] - starts) * vectors, axis=1) / self.lengths2[segment_ids]
            feet = starts + np.clip(fractions, 0.0, 1.0)[:, None] * vectors
            distances = np.linalg.norm(locations[sample_ids] - feet, axis=1)
            alignment = np.abs(np.sum(directions[sample_ids] * self.units[segment_ids], axis=1))
            eligible = (distances <= max_distance) & (alignment >= min_direction_consistency)
            if np.any(eligible):
                sample_ids, segment_ids = sample_ids[eligible], segment_ids[eligible]
                order = np.lexsort((self.identity_ranks[segment_ids], -alignment[eligible],
                                    distances[eligible], sample_ids))
                sample_ids, segment_ids = sample_ids[order], segment_ids[order]
                first = np.concatenate(([True], sample_ids[1:] != sample_ids[:-1]))
                ids[sample_ids[first]] = segment_ids[first]
        starts, vectors = self.endpoints[ids, 0], self.vectors[ids]
        fractions = np.sum((locations - starts) * vectors, axis=1) / self.lengths2[ids]
        feet = starts + np.clip(fractions, 0.0, 1.0)[:, None] * vectors
        return {"segment_ids": ids, "feet": feet, "fractions": fractions,
                "offsets": feet - locations, "distances": np.linalg.norm(locations - feet, axis=1),
                "directions": self.units[ids]}


def osm_topology(roads):
    """Build adjacency from shared OSM IDs, never from geometric crossings."""
    locations, neighbours, way_ids = {}, defaultdict(set), defaultdict(set)
    geometry_only = 0
    for road in roads:
        ids = road.get("node_ids", [])
        if not ids or len(ids) != len(road["points"]):
            geometry_only += 1
            continue
        for node_id, point in zip(ids, road["points"]):
            if node_id in locations and math.dist(locations[node_id], point) > 1e-6:
                raise ValueError(f"OSM node {node_id} has conflicting coordinates in the snapshot")
            locations[node_id] = list(point)
            way_ids[node_id].add(road["osm_way_id"])
        for left, right in zip(ids, ids[1:]):
            if left != right:
                neighbours[left].add(right)
                neighbours[right].add(left)
    nodes = [{"node_id": node_id, "point_3857": locations[node_id],
              "degree": len(neighbours[node_id]), "adjacent_node_ids": sorted(neighbours[node_id]),
              "osm_way_ids": sorted(way_ids[node_id]), "junction": len(neighbours[node_id]) >= 3}
             for node_id in sorted(locations)]
    return {"method": "explicit_shared_osm_node_ids", "geometry_only_way_count": geometry_only,
            "ways_with_node_ids": len(roads) - geometry_only, "nodes": nodes,
            "geometric_crossings_inferred": False}


def cad_topology(routes):
    """Expose exact shared CAD vertices without claiming physical connectivity."""
    neighbours, route_ids = defaultdict(set), defaultdict(set)
    for route_id, route in enumerate(routes):
        points = [tuple(point) for point in route]
        for point in points:
            route_ids[point].add(route_id)
        for left, right in zip(points, points[1:]):
            if left != right:
                neighbours[left].add(right)
                neighbours[right].add(left)
    return {"method": "explicit_coincident_cad_vertices", "physical_connectivity_verified": False,
            "geometric_crossings_inferred": False,
            "nodes": [{"node_id": index, "point_native": list(point), "degree": len(neighbours[point]),
                       "route_ids": sorted(route_ids[point]), "junction": len(neighbours[point]) >= 3}
                      for index, point in enumerate(sorted(route_ids))]}


def build_match_evidence(record, native_routes, base_routes, *, samples=None, directions=None, route_ids=None):
    """Return JSON evidence bound to this exact revision and snapshot.

    Original OSM vectors remain in record.osm_snapshot.roads, addressed by the
    hash below instead of being duplicated. CAD/native and base vectors are
    retained here so later manual revisions can recompute all diagnostics.
    """
    if len(native_routes) != len(base_routes) or any(len(a) != len(b) for a, b in zip(native_routes, base_routes)):
        raise ValueError("CAD evidence requires aligned native and base route vertices")
    roads = record["osm_snapshot"]["roads"]
    placed = record["status"] in {"matched", "coarse"} or record["base"]["mode"] == "nominal"
    evidence = {"schema_version": SCHEMA, "source_sha256": record["source_sha256"],
                "osm_snapshot_sha256": record["osm_snapshot"]["sha256"],
                "osm_input_sha256": record["osm_snapshot"].get("input_sha256"),
                "region_id": record.get("region_id"), "revision": record["revision"],
                "translation_3857": list(record["translation_3857"]),
                "placement_status": record["status"], "has_geographic_placement": placed,
                "authority": "relative_osm", "absolute_accuracy_verified": False,
                "osm_vector_path": "osm_snapshot.roads", "base_crs": "EPSG:3857",
                "native_source_crs": record["source_crs"],
                "source_unit_to_m": record["base"]["source_unit_to_m"],
                "cad_routes": [{"route_id": index, "native_points": copy.deepcopy(native),
                                "base_points_3857": copy.deepcopy(base), "native_sha256": _digest(native)}
                               for index, (native, base) in enumerate(zip(native_routes, base_routes))],
                "cad_topology": cad_topology(native_routes), "osm_topology": osm_topology(roads),
                "residuals": [], "residual_distance_method": "web_mercator_reference_latitude_cosine_approximation",
                "correspondence_method": "nearest_direction_compatible_segment_within_corridor",
                "correspondence_corridor_m": 25.0, "minimum_direction_consistency": 0.7}
    index = RoadSegmentIndex(roads)
    if placed and samples is not None and len(samples) and len(index.references):
        moved = np.asarray(samples) + record["translation_3857"]
        ground_scale = math.cos(math.radians(record["base"]["reference_latitude"]))
        inspected = index.inspect(moved, directions, max_distance=25.0 / ground_scale)
        for i, segment_id in enumerate(inspected["segment_ids"]):
            distance = float(inspected["distances"][i]) * ground_scale
            alignment = float(abs(np.dot(directions[i], inspected["directions"][i])))
            evidence["residuals"].append({"sample_id": i, "route_id": int(route_ids[i]),
                **index.references[int(segment_id)], "from_3857": moved[i].tolist(),
                "to_3857": inspected["feet"][i].tolist(), "distance_m_approx": distance,
                "direction_consistency": alignment, "supported": distance <= 25.0 and alignment >= 0.7,
                "residual_kind": "osm_corridor"})
    evidence["evidence_sha256"] = _digest(evidence)
    return evidence


def validate_match_evidence(record):
    evidence = record.get("evidence")
    if evidence is None:  # Previous profiles remain readable; no retroactive claim.
        return
    if not isinstance(evidence, dict) or evidence.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported OSM evidence schema")
    # GeoJSON can also be requested directly without constructing a nominal
    # transformer. Verify the enclosing seal so manual controls and diagnostic
    # values outside evidence cannot be changed behind the vector view's hash.
    if record.get("match_sha256") != _digest({key: value for key, value in record.items() if key != "match_sha256"}):
        raise ValueError("OSM evidence profile hash mismatch")
    data = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    if evidence.get("evidence_sha256") != _digest(data):
        raise ValueError("OSM evidence hash mismatch")
    expected = {"source_sha256": record["source_sha256"], "revision": record["revision"],
                "region_id": record.get("region_id"), "translation_3857": record["translation_3857"],
                "placement_status": record["status"],
                "osm_snapshot_sha256": record["osm_snapshot"]["sha256"],
                "osm_input_sha256": record["osm_snapshot"].get("input_sha256")}
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError("OSM evidence revision/source/snapshot binding mismatch")
    if evidence.get("authority") != "relative_osm" or evidence.get("absolute_accuracy_verified") is not False:
        raise ValueError("OSM evidence must retain relative-only authority")
    if _digest(record["osm_snapshot"]["roads"]) != expected["osm_snapshot_sha256"]:
        raise ValueError("OSM evidence snapshot content mismatch")


def evidence_geojson(record):
    """Derive five WGS84 FeatureCollections suitable for review, not surveying."""
    validate_match_evidence(record)
    evidence = record.get("evidence")
    layers = {name: {"type": "FeatureCollection", "features": []}
              for name in ("cad_routes", "osm_roads", "cad_nodes", "osm_nodes", "residuals")}
    if evidence is None:
        return layers
    common = {"source_sha256": record["source_sha256"], "evidence_sha256": evidence["evidence_sha256"],
              "authority": "relative_osm", "absolute_accuracy_verified": False,
              "revision": record["revision"], "placement_status": record["status"]}

    def geographic(point):
        lon, lat = _TO_WGS84.transform(*point)
        if not math.isfinite(lon) or not math.isfinite(lat):
            raise ValueError("Non-finite geographic OSM evidence point")
        return [lon, lat]

    def add(layer, geometry_type, coordinates, properties):
        layers[layer]["features"].append({"type": "Feature", "geometry": {
            "type": geometry_type, "coordinates": coordinates}, "properties": {**common, **properties}})

    delta = record["translation_3857"]

    def shifted(point):
        return [point[0] + delta[0], point[1] + delta[1]]

    native_to_base = {}
    if evidence["has_geographic_placement"]:
        for route in evidence["cad_routes"]:
            add("cad_routes", "LineString", [geographic(shifted(p)) for p in route["base_points_3857"]],
                {"route_id": route["route_id"], "native_sha256": route["native_sha256"]})
            native_to_base.update(zip((tuple(p) for p in route["native_points"]), route["base_points_3857"]))
        for node in evidence["cad_topology"]["nodes"]:
            add("cad_nodes", "Point", geographic(shifted(native_to_base[tuple(node["point_native"])])),
                {**node, "topology_method": "explicit_coincident_cad_vertices", "physical_connectivity_verified": False})
    for road in record["osm_snapshot"]["roads"]:
        add("osm_roads", "LineString", [geographic(p) for p in road["points"]],
            {key: value for key, value in road.items() if key != "points"})
    for node in evidence["osm_topology"]["nodes"]:
        add("osm_nodes", "Point", geographic(node["point_3857"]),
            {**node, "topology_method": "explicit_shared_osm_node_ids"})
    for residual in evidence["residuals"]:
        add("residuals", "LineString", [geographic(residual["from_3857"]), geographic(residual["to_3857"])],
            {key: value for key, value in residual.items() if key not in {"from_3857", "to_3857"}})
    target_to_3857 = Transformer.from_crs(record["target_crs"], "EPSG:3857", always_xy=True)
    metrics = {item["point_id"]: item for item in record.get("quality", {}).get("residuals", [])}
    for control in record.get("controls", []):
        target = list(target_to_3857.transform(control["target_easting"], control["target_northing"]))
        origin = [target[i] - control["translation_3857"][i] + delta[i] for i in (0, 1)]
        add("residuals", "LineString", [geographic(origin), geographic(target)],
            {"point_id": control["point_id"], "role": control["role"], "residual_kind": "manual_control",
             "distance_method": "WGS84_ellipsoidal_geodesic", **metrics.get(control["point_id"], {})})
    return layers
