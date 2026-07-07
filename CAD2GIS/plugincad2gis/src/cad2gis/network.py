"""Network model (story G8) — node-edge connectivity for comms infrastructure.

Comms data is a network, not loose geometry: point facilities (manholes, poles, closures,
cabinets, rooms) are NODES; cables and ducts are EDGES. This builds a connectivity graph by
snapping edge endpoints to the nearest node within a tolerance, then reports connectivity QC
(dangling ends, isolated nodes) that the accuracy scorer's `network` dimension consumes.

Pure shapely — no external graph library — to stay light and dependency-stable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .model import Feature, FeatureCollection

# Feature classes treated as nodes vs edges.
NODE_CLASSES = {"manhole", "pole", "closure", "cabinet", "room"}
EDGE_CLASSES = {"cable", "duct"}


@dataclass
class Node:
    id: int
    x: float
    y: float
    feature_class: str


@dataclass
class Edge:
    id: int
    feature_class: str
    start_node: Optional[int]  # node id or None if dangling
    end_node: Optional[int]


@dataclass
class NetworkQC:
    n_nodes: int
    n_edges: int
    dangling_ends: int          # edge endpoints not snapped to any node
    isolated_nodes: int         # nodes touched by no edge
    connected_endpoints: int
    total_endpoints: int

    @property
    def connectivity_ratio(self) -> float:
        return (self.connected_endpoints / self.total_endpoints) if self.total_endpoints else 1.0

    def to_dict(self) -> dict:
        return {
            "n_nodes": self.n_nodes,
            "n_edges": self.n_edges,
            "dangling_ends": self.dangling_ends,
            "isolated_nodes": self.isolated_nodes,
            "connected_endpoints": self.connected_endpoints,
            "total_endpoints": self.total_endpoints,
            "connectivity_ratio": round(self.connectivity_ratio, 4),
        }


@dataclass
class Network:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def qc(self) -> NetworkQC:
        touched: set[int] = set()
        dangling = 0
        total_ep = 0
        connected_ep = 0
        for e in self.edges:
            for nid in (e.start_node, e.end_node):
                total_ep += 1
                if nid is None:
                    dangling += 1
                else:
                    connected_ep += 1
                    touched.add(nid)
        isolated = sum(1 for n in self.nodes if n.id not in touched)
        return NetworkQC(
            n_nodes=len(self.nodes),
            n_edges=len(self.edges),
            dangling_ends=dangling,
            isolated_nodes=isolated,
            connected_endpoints=connected_ep,
            total_endpoints=total_ep,
        )


def _endpoints(geom):
    coords = list(geom.coords)
    return coords[0], coords[-1]


def build_network(coll: FeatureCollection, snap_tol: float = 1.0, synth_junctions: bool = True) -> Network:
    """Build a node-edge network from a converted feature collection.

    Point features in NODE_CLASSES become nodes; line features in EDGE_CLASSES become edges
    whose endpoints snap to the nearest node within *snap_tol*.

    When *synth_junctions* is set, clusters of edge endpoints that don't reach any real node but
    coincide with each other (within *snap_tol*) become SYNTHETIC junction nodes — comms routes
    connect to each other at splice/branch points, not only at manholes, so modelling those
    junctions is what makes connectivity reflect the true topology (Codex node-clustering method).
    """
    net = Network()
    for i, f in enumerate(coll.features):
        if f.feature_class in NODE_CLASSES and f.geometry.geom_type == "Point":
            net.nodes.append(Node(i, f.geometry.x, f.geometry.y, f.feature_class))
        elif f.feature_class == "room" and f.geometry.geom_type == "Polygon":
            c = f.geometry.centroid
            net.nodes.append(Node(i, c.x, c.y, "room"))

    def nearest(pt) -> Optional[int]:
        best_id, best_d = None, snap_tol
        for n in net.nodes:
            d = math.hypot(n.x - pt[0], n.y - pt[1])
            if d <= best_d:
                best_id, best_d = n.id, d
        return best_id

    edge_specs: list[tuple] = []  # (feature_class, a, b)
    for f in coll.features:
        if f.feature_class in EDGE_CLASSES and f.geometry.geom_type in ("LineString", "MultiLineString"):
            geom = f.geometry if f.geometry.geom_type == "LineString" else max(f.geometry.geoms, key=lambda g: g.length)
            a, b = _endpoints(geom)
            edge_specs.append((f.feature_class, a, b))

    # Synthesize junction nodes at clusters of unmatched edge endpoints (route-to-route splices).
    if synth_junctions:
        unmatched = [pt for _, a, b in edge_specs for pt in (a, b) if nearest(pt) is None]
        clusters = _cluster_points(unmatched, snap_tol)
        base = (max((n.id for n in net.nodes), default=-1)) + 1
        for k, (cx, cy, count) in enumerate(clusters):
            if count >= 2:  # a junction requires >=2 route endpoints meeting
                net.nodes.append(Node(base + k, cx, cy, "junction"))

    eid = 0
    for fc, a, b in edge_specs:
        net.edges.append(Edge(eid, fc, nearest(a), nearest(b)))
        eid += 1
    return net


def _cluster_points(pts, tol):
    """Greedy single-link clustering of endpoints within *tol*; returns (cx, cy, size) per cluster."""
    from shapely import STRtree
    from shapely.geometry import Point

    if not pts:
        return []
    geoms = [Point(p) for p in pts]
    tree = STRtree(geoms)
    seen: set[int] = set()
    out = []
    for i, g in enumerate(geoms):
        if i in seen:
            continue
        members = [i]
        seen.add(i)
        for j in tree.query(g.buffer(tol)):
            if j != i and j not in seen and g.distance(geoms[j]) <= tol:
                members.append(j)
                seen.add(j)
        cx = sum(pts[m][0] for m in members) / len(members)
        cy = sum(pts[m][1] for m in members) / len(members)
        out.append((cx, cy, len(members)))
    return out
