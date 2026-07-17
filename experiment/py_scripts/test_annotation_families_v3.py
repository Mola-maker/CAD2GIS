"""Focused regressions for reviewed APD annotation-family isolation."""

import json
import sqlite3
from pathlib import Path

import pytest

from cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis_v3.evidence import write_evidence
from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.model import CadStyle, SourceEntity
from cad2gis_v3.semantics import classify_entities


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"
REGISTRY = ROOT / "config" / "apd_mapping_registry.json"


def _registry():
    profile = SourceProfile.load(PROFILE)
    return MappingRegistry.load(REGISTRY, profile.source_sha256)


def _entity(key, dwg_type, layer, point, *, text="", block_name=""):
    return SourceEntity(
        entity_key=key,
        source_sha256="x",
        source_file="apd.dwg",
        handle=key,
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer=layer,
        object_name="",
        dwg_type=dwg_type,
        points=(point,),
        centroid=point,
        closed=False,
        text=text,
        block_name=block_name,
        block_attributes={},
        style=CadStyle(),
    )


def test_registry_declares_three_strict_reviewed_annotation_families():
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    families = {family.family_id: family for family in registry.annotation_families}
    assert set(families) == {"fat", "pole_new", "pole_existing"}
    assert families["pole_new"].require_same_layer is True
    assert families["pole_existing"].require_same_layer is True
    assert families["pole_existing"].max_distance_native_m == 23.0
    assert families["fat"].target_class == "BOITE"
    assert profile.expected_census["direct_new_pole_annotations"] == 118
    assert profile.expected_census["direct_existing_pole_annotations"] == 49


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value["annotation_families"][1].update(
            family_id=value["annotation_families"][0]["family_id"]
        ), "family_id"),
        (lambda value: value["annotation_families"][0].update(text_pattern="["), "Invalid text_pattern"),
        (lambda value: value["annotation_families"][0].update(family_id=None), "required string fields"),
        (lambda value: value["annotation_families"][0].update(family_id="Pole-New"), "family_id"),
        (lambda value: value["annotation_families"][0].update(max_distance_native_m=True), "finite positive"),
        (lambda value: value["annotation_families"][0].update(max_distance_native_m=0), "finite positive"),
        (lambda value: value["annotation_families"][1].update(
            rule_id=value["annotation_families"][0]["rule_id"],
            provenance=value["annotation_families"][0]["provenance"],
        ), "rule_id"),
    ],
)
def test_registry_rejects_invalid_annotation_family_contract(tmp_path, mutate, message):
    value = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutate(value)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        MappingRegistry.load(path, value["source_sha256"])


def test_new_and_existing_pole_families_cannot_cross_assign():
    entities = [
        _entity("EXISTING", "INSERT", "EXISTING POLE", (0.0, 0.0), block_name="*U14"),
        _entity("NEW", "INSERT", "NEW POLE 7-3", (10.0, 0.0), block_name="*U13"),
        # Each label is geometrically closer to the wrong pole family.
        _entity(
            "NEW-LABEL", "TEXT", "NEW POLE 7-3", (0.5, 0.0),
            text="MR.DMPH.P001",
        ),
        _entity(
            "EXISTING-LABEL", "TEXT", "EXISTING POLE", (21.5, 0.0),
            text="EXT.MR.MF.LBB.S02.P050",
        ),
    ]
    features, relations, unresolved, diagnostics = classify_entities(entities, _registry())
    by_handle = {feature.source_handle: feature for feature in features}

    assert by_handle["NEW"].display_label == "MR.DMPH.P001"
    assert by_handle["EXISTING"].display_label == "EXT.MR.MF.LBB.S02.P050"
    assert unresolved == []
    assert diagnostics["annotation_assignments"]["PTECH"] == {
        "source_annotations": 2,
        "assigned": 2,
        "missing": 0,
        "unresolved": 0,
        "cross_layer_assignments": 0,
        "total_distance_native_m": 31.0,
    }
    assert diagnostics["annotation_assignments_by_family"]["pole_new"]["target_assets"] == 1
    assert diagnostics["annotation_assignments_by_family"]["pole_new"]["assigned"] == 1
    assert diagnostics["annotation_assignments_by_family"]["pole_existing"]["target_assets"] == 1
    assert diagnostics["annotation_assignments_by_family"]["pole_existing"]["assigned"] == 1
    assert {
        candidate["family_id"]
        for candidate in diagnostics["annotation_candidates"]
        if candidate["selected"]
    } == {"pole_new", "pole_existing"}
    assert all("pole_" in relation.method for relation in relations)


def test_unknown_asset_like_id_is_unresolved_but_site_id_is_not_misreported():
    entities = [
        _entity("UNKNOWN", "TEXT", "NEW POLE 7-3", (0.0, 0.0), text="MR.DMPH.P999"),
        _entity("SITE-ID-1", "TEXT", "Service Core", (1.0, 0.0), text="DMPH-1.010"),
        _entity("SITE-ID-2", "TEXT", "Service Core", (2.0, 0.0), text="DMPH-2.011"),
    ]
    _, _, unresolved, diagnostics = classify_entities(entities, _registry())

    assert diagnostics["unrecognized_suspected_asset_ids"] == 1
    assert [item["entity_key"] for item in unresolved] == ["UNKNOWN"]
    assert unresolved[0]["status"] == "unrecognized_asset_id"
    assert unresolved[0]["family_id"] == "UNRECOGNIZED"


def test_recognized_label_on_unreviewed_source_layer_is_unresolved():
    entities = [
        _entity("P", "INSERT", "NEW POLE 7-3", (0.0, 0.0), block_name="*U13"),
        _entity("L", "TEXT", "EXISTING POLE", (0.0, 0.0), text="MR.DMPH.P001"),
    ]
    features, relations, unresolved, diagnostics = classify_entities(entities, _registry())

    assert features[0].display_label == ""
    assert relations == []
    assert unresolved[0]["status"] == "source_layer_mismatch"
    assert unresolved[0]["family_id"] == "pole_new"
    assert diagnostics["annotation_assignments"]["PTECH"]["assigned"] == 0


@pytest.mark.parametrize(
    "carrier_type", ["TEXT", "MTEXT", "ATTRIB", "ATTDEF", "MULTILEADER", "TABLE_CELL"],
)
def test_reviewed_annotation_families_accept_lossless_text_carriers(carrier_type):
    entities = [
        _entity("P", "INSERT", "NEW POLE 7-3", (0.0, 0.0), block_name="*U13"),
        _entity(
            "L", carrier_type, "NEW POLE 7-3", (12.0, 0.0),
            text="MR.DMPH.P001",
        ),
    ]
    features, relations, unresolved, diagnostics = classify_entities(
        entities, _registry(),
    )

    assert unresolved == []
    assert features[0].display_label == "MR.DMPH.P001"
    assert len(relations) == 1
    assert diagnostics["annotation_assignments_by_family"]["pole_new"]["assigned"] == 1


def test_annotation_family_and_layer_provenance_is_persisted(tmp_path):
    entities = [
        _entity("EXISTING", "INSERT", "EXISTING POLE", (0.0, 0.0), block_name="*U14"),
        _entity("NEW", "INSERT", "NEW POLE 7-3", (10.0, 0.0), block_name="*U13"),
        _entity(
            "NEW-LABEL", "TEXT", "NEW POLE 7-3", (0.5, 0.0),
            text="MR.DMPH.P001",
        ),
        _entity(
            "EXISTING-LABEL", "TEXT", "EXISTING POLE", (21.5, 0.0),
            text="EXT.MR.MF.LBB.S02.P050",
        ),
    ]
    features, relations, unresolved, diagnostics = classify_entities(
        entities, _registry(),
    )
    path = tmp_path / "annotation_evidence.gpkg"
    write_evidence(
        path, entities, features, relations, unresolved,
        {"semantics": diagnostics}, DirectTransformer("EPSG:3857", "EPSG:9481").source,
    )

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT family_id, source_layer, target_layer, rule_id, provenance "
            "FROM annotation_assignment_candidates WHERE selected=1 ORDER BY family_id"
        ).fetchall()
    assert rows == [
        (
            "pole_existing", "EXISTING POLE", "EXISTING POLE",
            "APD-ANNOTATION-POLE-EXISTING-001",
            "DWG_DERIVED:APD-ANNOTATION-POLE-EXISTING-001|"
            "RULE:APD-ANNOTATION-ASSIGN-001",
        ),
        (
            "pole_new", "NEW POLE 7-3", "NEW POLE 7-3",
            "APD-ANNOTATION-POLE-NEW-001",
            "DWG_DERIVED:APD-ANNOTATION-POLE-NEW-001|"
            "RULE:APD-ANNOTATION-ASSIGN-001",
        ),
    ]


def test_unrecognized_asset_id_evidence_has_explicit_review_fields(tmp_path):
    entities = [
        _entity("UNKNOWN", "TEXT", "NEW POLE 7-3", (0.0, 0.0), text="MR.DMPH.P999"),
        _entity("SITE-ID", "TEXT", "Service Core", (1.0, 0.0), text="DMPH-1.010"),
    ]
    features, relations, unresolved, diagnostics = classify_entities(
        entities, _registry(),
    )
    path = tmp_path / "unresolved_annotation_evidence.gpkg"
    write_evidence(
        path, entities, features, relations, unresolved,
        {"semantics": diagnostics}, DirectTransformer("EPSG:3857", "EPSG:9481").source,
    )

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT entity_key, family_id, text, source_layer, status "
            "FROM unresolved_items"
        ).fetchone()
    assert row == (
        "UNKNOWN", "UNRECOGNIZED", "MR.DMPH.P999", "NEW POLE 7-3",
        "unrecognized_asset_id",
    )
