"""Topology refinement tests (story G11) — noise-fragment demotion + junction connectivity.

Locks in the two accuracy levers: (1) sub-threshold, non-manhole-anchored route fragments are
demoted out of the route class (raising count accuracy), and (2) synthetic junction nodes at
route splice points make connectivity reflect real topology (raising the network dimension).
"""
from __future__ import annotations

import pytest

pytest.importorskip("shapely")

from shapely.geometry import LineString, Point  # noqa: E402

from cad2gis.model import Feature, FeatureCollection, SourceRef  # noqa: E402
from cad2gis.network import build_network  # noqa: E402
from cad2gis.refine import refine_topology  # noqa: E402


def _coll():
    coll = FeatureCollection(crs="local-engineering")
    # two manholes 100 apart
    coll.add(Feature(Point(0, 0), "manhole", {"facility": "manhole"}, SourceRef(entity_type="INSERT")))
    coll.add(Feature(Point(100, 0), "manhole", {"facility": "manhole"}, SourceRef(entity_type="INSERT")))
    # a real cable route between them
    coll.add(Feature(LineString([(0, 0), (100, 0)]), "cable", {"facility": "cable"}, SourceRef(entity_type="LWPOLYLINE")))
    # a tiny noise fragment far from any manhole (annotation/symbol bit)
    coll.add(Feature(LineString([(500, 500), (500.5, 500)]), "cable", {"facility": "cable"}, SourceRef(entity_type="LINE")))
    return coll


def test_noise_fragment_is_demoted():
    coll, rep = refine_topology(_coll(), min_route_len=2.0, snap_tol=5.0)
    assert rep.fragments_demoted == 1        # the 0.5-unit fragment
    assert rep.routes_kept == 1              # the 100-unit real route survives
    counts = coll.counts_by_class()
    assert counts.get("cable") == 1
    # the demoted fragment is no longer a cable
    demoted = [f for f in coll.features if f.attributes.get("_demoted_from") == "cable"]
    assert len(demoted) == 1


def test_short_jumper_anchored_to_manhole_is_kept():
    coll = FeatureCollection(crs="local-engineering")
    coll.add(Feature(Point(0, 0), "manhole", {"facility": "manhole"}, SourceRef(entity_type="INSERT")))
    coll.add(Feature(Point(1, 0), "manhole", {"facility": "manhole"}, SourceRef(entity_type="INSERT")))
    # a 1-unit jumper (< min_route_len) but both ends on manholes -> kept
    coll.add(Feature(LineString([(0, 0), (1, 0)]), "cable", {"facility": "cable"}, SourceRef(entity_type="LINE")))
    out, rep = refine_topology(coll, min_route_len=2.0, snap_tol=5.0)
    assert rep.fragments_demoted == 0
    assert out.counts_by_class().get("cable") == 1


def test_junction_synthesis_raises_connectivity():
    # Three cables meeting at a shared point (500,500) with NO manhole there. Without junction
    # synthesis those endpoints dangle; with it, a junction node connects them.
    coll = FeatureCollection(crs="local-engineering")
    for end in [(600, 500), (500, 600), (400, 500)]:
        coll.add(Feature(LineString([(500, 500), end]), "cable", {"facility": "cable"},
                         SourceRef(entity_type="LWPOLYLINE")))
    without = build_network(coll, snap_tol=3.0, synth_junctions=False).qc()
    with_j = build_network(coll, snap_tol=3.0, synth_junctions=True).qc()
    assert with_j.connectivity_ratio > without.connectivity_ratio
    assert with_j.n_nodes >= 1  # a junction node was synthesized


def test_label_propagation_upgrades_gated_duct_near_route():
    from cad2gis.refine import propagate_network_labels

    coll = FeatureCollection(crs="local-engineering")
    # a confirmed cable route
    coll.add(Feature(LineString([(0, 0), (100, 0)]), "cable", {"facility": "cable"},
                     SourceRef(entity_type="LWPOLYLINE")))
    # a gc170 symbol that FAILED the text gate (paving text nearby) but sits ON the route
    coll.add(Feature(Point(50, 0), None, {"_map_evidence": {"decision": "gate_failed", "code": "gc170"}},
                     SourceRef(entity_type="INSERT", block="gc170")))
    # a gc170 far from any route -> stays unmapped
    coll.add(Feature(Point(999, 999), None, {"_map_evidence": {"decision": "gate_failed", "code": "gc170"}},
                     SourceRef(entity_type="INSERT", block="gc170")))
    out, rep = propagate_network_labels(coll, assoc_tol=8.0)
    assert rep["upgraded"] == 1
    ducts = [f for f in out.features if f.feature_class == "duct"]
    assert len(ducts) == 1
    assert ducts[0].attributes["resolved_by"] == "topology_propagation"
    assert ducts[0].confidence == 0.65


def test_label_propagation_only_targets_gated_duct_symbols():
    from cad2gis.refine import propagate_network_labels

    coll = FeatureCollection(crs="local-engineering")
    coll.add(Feature(LineString([(0, 0), (100, 0)]), "cable", {"facility": "cable"},
                     SourceRef(entity_type="LWPOLYLINE")))
    # a REJECTED paving symbol (gc043) near the route -> must NOT be upgraded to duct
    coll.add(Feature(Point(50, 0), None, {"_map_evidence": {"decision": "rejected", "code": "gc043"}},
                     SourceRef(entity_type="INSERT", block="gc043")))
    out, rep = propagate_network_labels(coll, assoc_tol=8.0)
    assert rep["upgraded"] == 0  # rejected paving never upgrades
    assert not any(f.feature_class == "duct" for f in out.features)
