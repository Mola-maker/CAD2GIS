from __future__ import annotations

from shapely.geometry import LineString, Point

from cad2gis.diagnostics import diagnose_collection, issues_from_jsonable, issues_to_jsonable
from cad2gis.model import Feature, FeatureCollection, SourceRef


def test_diagnose_unverified_duct_and_dangling_route():
    coll = FeatureCollection(source_file="sample.dxf")
    coll.add(Feature(Point(0, 0), "duct", {"resolved_by": "topology_propagation"},
                     SourceRef(handle="D1", layer="GXYZ", block="gc013b", entity_type="INSERT")))
    coll.add(Feature(LineString([(10, 10), (20, 10)]), "cable", {},
                     SourceRef(handle="C1", layer="COMM", entity_type="LWPOLYLINE")))

    issues = diagnose_collection(
        coll,
        per_feature={"by_class": {"duct": {"total": 1, "verified": 0}}},
        network={"dangling_ends": 2},
    )

    assert [i.issue_type for i in issues] == ["unverified_duct", "dangling_route"]
    assert issues[0].source_handle == "D1"
    assert issues[1].source_handle == "C1"
    assert issues[0].suggested_patch_types == ["apply_reviewed_label", "reject_feature"]


def test_issue_json_round_trip():
    coll = FeatureCollection(source_file="sample.dxf")
    coll.add(Feature(Point(0, 0), "duct", {"resolved_by": "topology_propagation"},
                     SourceRef(handle="D1", layer="GXYZ", block="gc013b", entity_type="INSERT")))

    issues = diagnose_collection(coll, per_feature={"by_class": {"duct": {"verified": 0}}})
    payload = issues_to_jsonable(issues)
    restored = issues_from_jsonable(payload)

    assert restored[0].issue_id == issues[0].issue_id
    assert restored[0].evidence["block"] == "gc013b"
