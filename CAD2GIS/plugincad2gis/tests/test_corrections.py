from __future__ import annotations

from shapely.geometry import Point

from cad2gis.corrections import (
    CorrectionPatch,
    apply_patches,
    read_ledger,
    read_feature_collection,
    write_ledger_entry,
    write_feature_collection,
)
from cad2gis.model import Feature, FeatureCollection, SourceRef


def test_apply_reviewed_label_patch_preserves_provenance(tmp_path):
    coll = FeatureCollection(source_file="sample.dxf")
    coll.add(Feature(Point(1, 2), "__unmapped__", {}, SourceRef(handle="A1", layer="GXYZ")))
    patch = CorrectionPatch(
        patch_id="p1",
        patch_type="apply_reviewed_label",
        source_handle="A1",
        after={"feature_class": "duct", "attributes": {"review_status": "accepted"}},
        evidence={"reviewed_label": "duct"},
        reason="hand reviewed duct label",
    )

    out, records = apply_patches(coll, [patch])

    assert out.features[0].feature_class == "duct"
    assert out.features[0].attributes["review_status"] == "accepted"
    assert out.features[0].source.handle == "A1"
    assert records[0].status == "accepted"


def test_rejects_unknown_handle_without_mutating_collection():
    coll = FeatureCollection(source_file="sample.dxf")
    coll.add(Feature(Point(1, 2), "duct", {}, SourceRef(handle="A1", layer="GXYZ")))
    patch = CorrectionPatch(
        patch_id="p1",
        patch_type="apply_reviewed_label",
        source_handle="missing",
        after={"feature_class": "duct"},
        evidence={"reviewed_label": "duct"},
        reason="bad handle",
    )

    out, records = apply_patches(coll, [patch])

    assert out.features[0].feature_class == "duct"
    assert records[0].status == "rejected"
    assert "not found" in records[0].validation["errors"][0]


def test_rejects_invalid_class_and_before_mismatch():
    coll = FeatureCollection(source_file="sample.dxf")
    coll.add(Feature(Point(1, 2), "duct", {}, SourceRef(handle="A1", layer="GXYZ")))
    bad_class = CorrectionPatch(
        patch_id="p1",
        patch_type="reclassify_feature",
        source_handle="A1",
        before={"feature_class": "duct"},
        after={"feature_class": "spaceship"},
        evidence={"reviewed_label": "spaceship"},
        reason="invalid class",
    )
    stale_before = CorrectionPatch(
        patch_id="p2",
        patch_type="set_attribute",
        source_handle="A1",
        before={"feature_class": "manhole"},
        after={"attributes": {"node_id": "N1"}},
        evidence={"reviewed_label": "N1"},
        reason="stale review",
    )

    out, records = apply_patches(coll, [bad_class, stale_before])

    assert out.features[0].feature_class == "duct"
    assert [r.status for r in records] == ["rejected", "rejected"]
    assert "invalid feature_class" in records[0].validation["errors"][0]
    assert "before.feature_class mismatch" in records[1].validation["errors"][0]


def test_feature_collection_round_trip(tmp_path):
    path = tmp_path / "corrected_features.json"
    coll = FeatureCollection(source_file="sample.dxf", crs="local")
    coll.add(Feature(Point(1, 2), "duct", {"review_status": "accepted"},
                     SourceRef(handle="A1", layer="GXYZ", block="gc013b", entity_type="INSERT")))

    write_feature_collection(path, coll)
    restored = read_feature_collection(path)

    assert restored.source_file == "sample.dxf"
    assert restored.features[0].geometry.x == 1
    assert restored.features[0].feature_class == "duct"
    assert restored.features[0].source.handle == "A1"


def test_ledger_round_trip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    patch = CorrectionPatch(
        "p1",
        "reject_feature",
        "A1",
        after={"feature_class": "__unmapped__"},
        evidence={"negative": "paving"},
        reason="surface restoration",
    )
    _, records = apply_patches(FeatureCollection(), [patch])
    write_ledger_entry(path, records[0])

    assert read_ledger(path)[0].patch_id == "p1"
