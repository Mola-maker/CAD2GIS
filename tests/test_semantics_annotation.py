from __future__ import annotations

from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis.cad2gis_v3.semantics import (
    _GENERATED_CODE_PROVENANCE,
    _annotation_target_eligible,
    _assign_family_annotations,
)
from cad2gis.cad2gis_v3.spatial_filter import is_pole_identifier_shape


SOURCE_SHA = "a" * 64


def _annotation(entity_key: str, handle: str, text: str, centroid: tuple[float, float]) -> SourceEntity:
    return SourceEntity(
        entity_key=entity_key,
        source_sha256=SOURCE_SHA,
        source_file="drawing.dwg",
        handle=handle,
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="POLE ID",
        object_name="AcDbText",
        dwg_type="TEXT",
        points=(centroid,),
        centroid=centroid,
        closed=False,
        text=text,
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=7),
    )


def _target(
    *,
    feature_key: str,
    handle: str,
    source_layer: str = "POLE ID",
    display_label: str = "PTECH-CAD-ABC",
    label_provenance: str = "DWG_DERIVED:pole-label|RULE:REVIEWED-PTECH-LABEL-001",
    code_provenance: str = _GENERATED_CODE_PROVENANCE,
) -> Feature:
    return Feature(
        feature_key=feature_key,
        feature_class="PTECH",
        geometry_kind="Point",
        native_points=[(10.0, 0.0)],
        source_entity_key=f"entity:{handle}",
        source_handle=handle,
        source_layer=source_layer,
        geometry_role="SOURCE_ASSET",
        style=CadStyle(aci_color=3),
        display_label=display_label,
        label_provenance=label_provenance,
        field_provenance={"CODE": code_provenance},
    )


def test_generated_handle_label_is_eligible_for_dwg_text() -> None:
    target = _target(feature_key="feature:A", handle="A")
    assert target.display_label == "PTECH-CAD-ABC"
    assert _annotation_target_eligible(target) is True


def test_semantic_dwg_label_is_not_overwritten() -> None:
    target = _target(
        feature_key="feature:A",
        handle="A",
        display_label="MR.DMPH.P104",
        label_provenance="DWG_DIRECT:text|RULE:REVIEWED-PTECH-LABEL-001",
        code_provenance="DWG_DIRECT:text|RULE:REVIEWED-PTECH-LABEL-001",
    )
    assert _annotation_target_eligible(target) is False


def test_assignment_overwrites_generated_handle_but_not_reviewed_label() -> None:
    annotation = _annotation(
        "entity:label", "10B", "MR.DMPH.P104", (11.0, 0.5),
    )
    generated = _target(feature_key="feature:G", handle="10A")
    reviewed = _target(
        feature_key="feature:R",
        handle="10C",
        display_label="MR.DMPH.P200",
        label_provenance="DWG_DIRECT:text|RULE:REVIEWED-PTECH-LABEL-001",
        code_provenance="DWG_DIRECT:text|RULE:REVIEWED-PTECH-LABEL-001",
    )

    assignments, failures, _ = _assign_family_annotations(
        [annotation],
        [generated, reviewed],
        15.0,
        family_id="test-family",
    )

    assert not failures
    assert len(assignments) == 1
    entity, target, distance = assignments[0]
    assert entity is annotation
    assert target is generated
    assert distance == ((11.0 - 10.0) ** 2 + (0.5 - 0.0) ** 2) ** 0.5


def test_pole_identifier_shape_accepts_reviewed_apd_shapes_only() -> None:
    assert is_pole_identifier_shape("MR.KLDYA.P017") is True
    assert is_pole_identifier_shape("EXT.MR.BDR.P069") is True
    assert is_pole_identifier_shape("EXT. MR.KSRA.P008") is True
    assert is_pole_identifier_shape("SLACK - 2 EXT") is False
    assert is_pole_identifier_shape("IJY - KLDYA - 48C") is False


def test_owner_relation_wins_over_equidistant_nearest_neighbour() -> None:
    annotation = _annotation("entity:label", "10B", "MR.DMPH.P104", (10.0, 0.0))
    object.__setattr__(annotation, "owner_handle", "10A")
    owner_target = _target(feature_key="feature:A", handle="10A")
    other_target = _target(feature_key="feature:B", handle="10B")
    # Both targets are equidistant: without owner evidence this is the legacy
    # 0.01 m multiple-optima abstention.
    owner_target.native_points = [(10.0, 0.0)]
    other_target.native_points = [(10.0, 0.0)]

    assignments, failures, candidates = _assign_family_annotations(
        [annotation], [owner_target, other_target], 15.0,
        family_id="test-family",
    )

    assert not failures
    assert len(assignments) == 1
    assert assignments[0][1] is owner_target
    selected = [item for item in candidates if item["selected"]]
    assert selected[0]["link_kind"] == "owner"
    assert selected[0]["relation_priority"] == 0


def test_block_path_relation_wins_over_equidistant_nearest_neighbour() -> None:
    annotation = _annotation("entity:label", "10B", "MR.DMPH.P104", (10.0, 0.0))
    object.__setattr__(
        annotation,
        "raw_properties",
        {
            "plan_domain": {
                "materialization": "nested-insert-affine",
                "root_entity_key": "entity:10A",
                "instance_path": ["entity:10A"],
            }
        },
    )
    path_target = _target(feature_key="feature:A", handle="10A")
    other_target = _target(feature_key="feature:B", handle="10B")
    path_target.native_points = [(10.0, 0.0)]
    other_target.native_points = [(10.0, 0.0)]

    assignments, failures, candidates = _assign_family_annotations(
        [annotation], [path_target, other_target], 15.0,
        family_id="test-family",
    )

    assert not failures
    assert len(assignments) == 1
    assert assignments[0][1] is path_target
    selected = [item for item in candidates if item["selected"]]
    assert selected[0]["link_kind"] == "block_path"
    assert selected[0]["relation_priority"] == 1


def test_legacy_relation_priority_off_keeps_geometric_tie_abstention() -> None:
    annotation = _annotation("entity:label", "10B", "MR.DMPH.P104", (10.0, 0.0))
    object.__setattr__(annotation, "owner_handle", "10A")
    owner_target = _target(feature_key="feature:A", handle="10A")
    other_target = _target(feature_key="feature:B", handle="10B")
    owner_target.native_points = [(10.0, 0.0)]
    other_target.native_points = [(10.0, 0.0)]

    assignments, failures, _ = _assign_family_annotations(
        [annotation], [owner_target, other_target], 15.0,
        family_id="test-family",
        relation_priority=False,
    )

    assert not assignments
    assert failures and failures[0]["status"] == "multiple_optima"
