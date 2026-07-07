"""Per-feature semantic verification tests (story G-sem) — real cross-source correctness.

Locks in that per-feature verification uses INDEPENDENT signals (not the classifier's own rule
path), so it is a real correctness measurement, not a tautology:
  - manhole verified via cross-source surveyed-label matching (separate entity type)
  - cable verified via topological anchoring (endpoint snaps to a manhole/route)
  - duct verified via geometry fingerprint (single CIRCLE = cross-section)
  - annotation verified via non-empty text
"""
from __future__ import annotations

import pytest

pytest.importorskip("shapely")

from shapely.geometry import LineString, Point  # noqa: E402

from cad2gis.gcp import GCP, fit_transform  # noqa: E402
from cad2gis.model import Feature, FeatureCollection, SourceRef  # noqa: E402
from cad2gis.verify import verify_per_feature  # noqa: E402


def _coll():
    coll = FeatureCollection(crs="local-engineering")
    # manhole at (0,0); surveyed label says real-world (100,200) — transform is +100/+200
    coll.add(Feature(Point(0, 0), "manhole", {"is_node_block": True},
                     SourceRef(entity_type="INSERT", block="末端井")))
    # cable anchored to the manhole at one end
    coll.add(Feature(LineString([(0, 0), (50, 0)]), "cable", {},
                     SourceRef(entity_type="LWPOLYLINE")))
    # a floating cable NOT anchored to anything (should NOT verify)
    coll.add(Feature(LineString([(999, 999), (1000, 999)]), "cable", {},
                     SourceRef(entity_type="LWPOLYLINE")))
    # annotation with text
    coll.add(Feature(Point(1, 1), "annotation", {"text": "T131"},
                     SourceRef(entity_type="TEXT")))
    return coll


def test_manhole_verified_by_cross_source_label_matching():
    coll = _coll()
    gcps = [GCP(0, 0, 100, 200), GCP(5, 0, 105, 200), GCP(0, 5, 100, 205), GCP(5, 5, 105, 205)]
    fit = fit_transform(gcps)
    # the manhole at (0,0) transforms to (100,200) == the surveyed dst -> verified
    pf = verify_per_feature(coll, transform=fit,
                            gcps_refined=[GCP(0, 0, 100, 200)])
    mh = pf.by_class["manhole"]
    assert mh["verified"] == 1 and mh["rate"] == 1.0


def test_cable_verified_by_topological_anchoring():
    pf = verify_per_feature(_coll())  # no transform needed for cable/annotation checks
    cable = pf.by_class["cable"]
    assert cable["total"] == 2
    assert cable["verified"] == 1  # only the anchored cable verifies


def test_annotation_verified_by_text_presence():
    pf = verify_per_feature(_coll())
    ann = pf.by_class["annotation"]
    assert ann["verified"] == 1


def test_duct_verified_by_fingerprint_independent_of_name():
    coll = FeatureCollection(crs="local-engineering")
    # a duct symbol whose block def is a single CIRCLE (cross-section shape) -> verified by geometry
    coll.add(Feature(Point(5, 5), "duct", {}, SourceRef(entity_type="INSERT", block="gc170")))
    pf = verify_per_feature(coll, fingerprints={"gc170": {"CIRCLE": 1}})
    assert pf.by_class["duct"]["verified"] == 1
    # a duct whose block def is NOT a single circle -> not verified by this signal
    coll2 = FeatureCollection(crs="local-engineering")
    coll2.add(Feature(Point(5, 5), "duct", {}, SourceRef(entity_type="INSERT", block="gc999")))
    pf2 = verify_per_feature(coll2, fingerprints={"gc999": {"LINE": 6}})
    assert pf2.by_class["duct"]["verified"] == 0


def test_duct_verified_by_reviewed_label_subset():
    coll = FeatureCollection(crs="local-engineering")
    coll.add(Feature(Point(5, 5), "duct", {"resolved_by": "topology_propagation"},
                     SourceRef(entity_type="INSERT", block="gc013b", handle="A1")))
    coll.add(Feature(Point(9, 9), "duct", {"resolved_by": "topology_propagation"},
                     SourceRef(entity_type="INSERT", block="gc013c", handle="A2")))

    pf = verify_per_feature(
        coll,
        fingerprints={"gc013b": {"LINE": 1, "POLYLINE": 1}, "gc013c": {"POLYLINE": 1}},
        reviewed_labels={
            "duct": {
                "A1": {"class": "duct", "evidence": "hand-reviewed DS-06 duct schedule row"},
                "A2": {"class": "paving", "evidence": "negative control"},
            }
        },
    )

    assert pf.by_class["duct"]["total"] == 2
    assert pf.by_class["duct"]["verified"] == 1
    assert pf.by_class["duct"]["rate"] == 0.5


def test_topology_propagated_duct_does_not_self_verify_without_independent_signal():
    coll = FeatureCollection(crs="local-engineering")
    coll.add(Feature(Point(5, 5), "duct", {"resolved_by": "topology_propagation"},
                     SourceRef(entity_type="INSERT", block="gc013b", handle="A1")))

    pf = verify_per_feature(
        coll,
        fingerprints={"gc013b": {"LINE": 1, "POLYLINE": 1}},
        reviewed_labels={"duct": {}},
    )

    assert pf.by_class["duct"]["verified"] == 0


def test_overall_correctness_is_real_not_tautological():
    # With one floating (unanchored) cable, overall correctness must be < 1.0 — proving the metric
    # actually catches defects (unlike the old tautological correctness=1.0).
    pf = verify_per_feature(_coll())
    assert pf.overall_verified < pf.overall_verifiable
    assert pf.per_feature_correctness < 1.0
