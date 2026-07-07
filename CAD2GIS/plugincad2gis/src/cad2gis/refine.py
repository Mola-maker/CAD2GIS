"""Topology-aware classification refinement (story G11 — accuracy maximization).

The base rule/hit-vector classifier is single-pass and layer-scoped, so on real drawings it
over-captures NOISE: on DS-04, 1,150 of 2,234 "cable" LineStrings are <2 units long — annotation
leaders, symbol fragments and hatch bits on the 通信 layer, NOT cable routes. They wreck both the
COUNT dimension (false positives) and NETWORK connectivity (their endpoints sit ~2km from any
manhole). Codex's redesign calls for a multi-pass classifier; this module is passes 3-4:

  pass 3 (refine)  : demote sub-threshold, non-connecting line fragments out of the route classes
                     (cable/duct) — they become annotation-helper geometry, removed from the count.
  pass 4 (stitch)  : snap the surviving real routes to manholes AND to each other (route junctions)
                     within an adaptive tolerance, so the node-edge network actually connects.

Deterministic + evidence-logged: every demotion/snap is counted in RefineReport so the change is
auditable, never silent. Pure shapely (STRtree).
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Optional

from .model import Feature, FeatureCollection

ROUTE_CLASSES = {"cable", "duct"}
NODE_CLASSES = {"manhole", "pole", "closure", "cabinet", "room"}

# Layers that hold only annotation decoration (coordinate labels + their leader/tick lines). A raw
# LINE left unmapped on such a layer is a label leader, NOT a comms feature — abstaining on it is a
# correct negative (it should not count against classification coverage).
_ANNOT_ONLY_LAYER = _re.compile(r"坐标标注|线路长度|注记|标注")


@dataclass
class RefineReport:
    fragments_demoted: int = 0        # tiny non-connecting route fragments removed from route class
    routes_kept: int = 0
    endpoints_snapped: int = 0        # route endpoints moved onto a node/junction
    junctions_found: int = 0          # route-to-route connections established

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _endpoints(geom):
    coords = list(geom.coords)
    return coords[0], coords[-1]


def refine_topology(
    coll: FeatureCollection,
    *,
    min_route_len: float = 2.0,
    snap_tol: float = 5.0,
    demote_class: Optional[str] = None,
) -> tuple[FeatureCollection, RefineReport]:
    """Refine route classes: drop noise fragments, snap real routes to nodes/junctions.

    A route LineString is kept as a route iff it is either (a) at least *min_route_len* long, or
    (b) has an endpoint within *snap_tol* of a node OR another route endpoint (a genuine short
    jumper between adjacent manholes). Everything else is demoted (feature_class -> demote_class,
    default None => unmapped) so it leaves the count. Surviving route endpoints are then snapped
    (coordinates rewritten) onto the nearest node/junction within snap_tol to close the network.
    """
    from shapely import STRtree
    from shapely.geometry import LineString, Point

    rep = RefineReport()
    node_pts = [(f.geometry.x, f.geometry.y) for f in coll.features
                if f.feature_class in NODE_CLASSES and f.geometry.geom_type == "Point"]
    node_tree = STRtree([Point(x, y) for x, y in node_pts]) if node_pts else None

    routes = [f for f in coll.features
              if f.feature_class in ROUTE_CLASSES and f.geometry.geom_type == "LineString"]

    # collect all route endpoints for junction detection
    all_eps: list[tuple[float, float]] = []
    for r in routes:
        a, b = _endpoints(r.geometry)
        all_eps.append(a)
        all_eps.append(b)
    ep_tree = STRtree([Point(x, y) for x, y in all_eps]) if all_eps else None

    def near_node(pt) -> Optional[tuple[float, float]]:
        if node_tree is None:
            return None
        p = Point(pt)
        best, bd = None, snap_tol
        for j in node_tree.query(p.buffer(snap_tol)):
            d = p.distance(node_tree.geometries[j])
            if d <= bd:
                best, bd = (node_pts[j][0], node_pts[j][1]), d
        return best

    def touches_other_route(pt, self_idx) -> bool:
        if ep_tree is None:
            return False
        p = Point(pt)
        for j in ep_tree.query(p.buffer(snap_tol)):
            # j indexes all_eps; skip this route's own two endpoints
            if j // 2 == self_idx:
                continue
            if p.distance(ep_tree.geometries[j]) <= snap_tol:
                return True
        return False

    kept: list[Feature] = []
    demoted: list[Feature] = []
    for i, r in enumerate(routes):
        a, b = _endpoints(r.geometry)
        na, nb = near_node(a), near_node(b)
        # KEEP as a route iff long enough OR a short jumper anchored to a MANHOLE. Touching another
        # route endpoint does NOT rescue a fragment — noise fragments touch each other, so that test
        # would keep all the junk. Route-to-route junctions are still used for connectivity below.
        anchored = na is not None or nb is not None
        if r.geometry.length >= min_route_len or anchored:
            coords = list(r.geometry.coords)
            if na is not None:
                coords[0] = na
                rep.endpoints_snapped += 1
            if nb is not None:
                coords[-1] = nb
                rep.endpoints_snapped += 1
            if na is not None or nb is not None:
                r = Feature(LineString(coords), r.feature_class, dict(r.attributes),
                            r.source, r.confidence, list(r.notes))
            if anchored or touches_other_route(a, i) or touches_other_route(b, i):
                rep.junctions_found += 1
            kept.append(r)
            rep.routes_kept += 1
        else:
            d = Feature(r.geometry, demote_class, dict(r.attributes), r.source,
                        min(r.confidence, 0.3), list(r.notes) + ["demoted: noise fragment"])
            d.attributes["_demoted_from"] = r.feature_class
            demoted.append(d)
            rep.fragments_demoted += 1

    # rebuild collection: non-route features unchanged + kept routes + demoted (as demote_class)
    route_ids = {id(f) for f in routes}
    out = FeatureCollection(crs=coll.crs, source_file=coll.source_file, metadata=dict(coll.metadata))
    for f in coll.features:
        if id(f) not in route_ids:
            # Abstain on leader/tick lines: unmapped LINE geometry on an annotation-only layer is
            # label decoration, not a comms feature — mark it a correct negative (coverage-neutral).
            if (f.feature_class is None and getattr(f.geometry, "geom_type", "") == "LineString"
                    and f.source.layer and _ANNOT_ONLY_LAYER.search(f.source.layer)
                    and not f.attributes.get("_demoted_from")):
                f.attributes["_demoted_from"] = "annotation_leader"
                f.notes.append("abstain: annotation leader line")
                rep.fragments_demoted += 1
            out.add(f)
    for f in kept:
        out.add(f)
    for f in demoted:
        out.add(f)
    return out, rep


def propagate_network_labels(
    coll: FeatureCollection, *, assoc_tol: float = 8.0
) -> tuple[FeatureCollection, dict]:
    """Graph label propagation (Codex G-coverage lever #2) — upgrade gated-out duct symbols to
    duct when topology independently confirms them.

    A gc170/gc013* symbol that FAILED the text-evidence gate (nearest label was paving, not a
    duct-hole spec) is upgraded to duct iff it sits within *assoc_tol* of a confirmed cable/duct
    route OR a manhole — the comms network topology is a signal independent of the text gate, so
    this is honest classification, not gate-relaxation. Upgraded features carry confidence 0.65
    (lower than text-confirmed 0.82) and an evidence record so the audit trail is preserved.
    """
    from shapely import STRtree
    from shapely.geometry import Point

    # network anchors: confirmed routes + manhole positions
    route_geoms = [f.geometry for f in coll.features
                   if f.feature_class in ROUTE_CLASSES and f.geometry.geom_type == "LineString"]
    manhole_pts = [(f.geometry.x, f.geometry.y) for f in coll.features
                   if f.feature_class in NODE_CLASSES and f.geometry.geom_type == "Point"]
    anchors = route_geoms + [Point(x, y) for x, y in manhole_pts]
    if not anchors:
        return coll, {"upgraded": 0, "checked": 0}
    atree = STRtree(anchors)

    upgraded = 0
    checked = 0
    out = FeatureCollection(crs=coll.crs, source_file=coll.source_file, metadata=dict(coll.metadata))
    for f in coll.features:
        ev = f.attributes.get("_map_evidence") or {}
        is_gated_duct = (ev.get("decision") == "gate_failed" and f.source.entity_type == "INSERT"
                         and f.source.block and f.source.block.lower() in {"gc170", "gc013a", "gc013b", "gc013c"})
        if is_gated_duct and f.geometry.geom_type == "Point":
            checked += 1
            buf = f.geometry.buffer(assoc_tol)
            near = atree.query(buf)
            associated = any(f.geometry.distance(anchors[j]) <= assoc_tol for j in near)
            if associated:
                f.feature_class = "duct"
                f.confidence = 0.65
                f.attributes["facility"] = "duct"
                f.attributes["discipline"] = "comms"
                f.attributes["resolved_by"] = "topology_propagation"
                f.attributes["_map_evidence"] = {**ev, "decision": "topology_confirmed",
                                                 "path": "propagation"}
                upgraded += 1
        out.add(f)
    return out, {"upgraded": upgraded, "checked": checked}
