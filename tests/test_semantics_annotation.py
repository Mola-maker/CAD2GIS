from __future__ import annotations

from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis.cad2gis_v3.semantics import (
    _GENERATED_CODE_PROVENANCE,
    _annotation_target_eligible,
    _assign_family_annotations,
    _attributed_block_style,
    _device_number_attribute,
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


def test_device_number_attribute_reads_untagged_numeric_attrib() -> None:
    entity = _annotation("entity:device", "10C", "KTK5.086.B02", (1.0, 2.0))
    object.__setattr__(
        entity, "raw_properties", {"owned_attribute_texts": ["16", "- dB"]},
    )
    assert _device_number_attribute(entity) == "16"


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


def test_attributed_block_style_uses_explicit_non_default_attribute_colour() -> None:
    root = SourceEntity(
        entity_key="root",
        source_sha256=SOURCE_SHA,
        source_file="drawing.dwg",
        handle="2988D",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="CLOSURE",
        object_name="AcDbBlockReference",
        dwg_type="INSERT",
        points=((10.0, 20.0),),
        centroid=(10.0, 20.0),
        closed=False,
        text="",
        block_name="*U108",
        block_attributes={"CLOSURE": "48"},
        style=CadStyle(aci_color=1, true_color="#FF0000"),
    )
    styles = {
        "root": [("", CadStyle(aci_color=5, true_color="#0000FF"))],
    }
    style = _attributed_block_style(root, styles)
    assert style is not None
    assert style.aci_color == 5
    assert style.true_color == "#0000FF"


def test_attributed_block_style_ignores_default_black_template() -> None:
    root = SourceEntity(
        entity_key="root",
        source_sha256=SOURCE_SHA,
        source_file="drawing.dwg",
        handle="4D208",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="FAT",
        object_name="AcDbBlockReference",
        dwg_type="INSERT",
        points=((10.0, 20.0),),
        centroid=(10.0, 20.0),
        closed=False,
        text="",
        block_name="FAT",
        block_attributes={"FAT": "16"},
        style=CadStyle(aci_color=30, true_color="#FF7F00"),
    )
    styles = {
        "root": [("", CadStyle(aci_color=7, true_color="#000000"))],
    }
    assert _attributed_block_style(root, styles) is None


def test_emr_label_shape_accepts_equipment_ids_only() -> None:
    from cad2gis.cad2gis_v3.semantics import _EMR_LABEL_RE
    assert _EMR_LABEL_RE.fullmatch("EMR-28560") is not None
    assert _EMR_LABEL_RE.fullmatch("EMR-29619") is not None
    assert _EMR_LABEL_RE.fullmatch("MR.UP.TMH.S01.P005") is None


def test_point_segment_distance_snaps_to_segment() -> None:
    from cad2gis.cad2gis_v3.semantics import _point_segment_distance
    assert abs(_point_segment_distance((1.0, 2.0), (0.0, 0.0), (4.0, 0.0)) - 2.0) < 1e-9
    assert abs(_point_segment_distance((-1.0, 0.0), (0.0, 0.0), (4.0, 0.0)) - 1.0) < 1e-9


def test_frame_boite_admission_rules_are_structural_not_colour_based():
    from cad2gis.cad2gis_v3.family_validation import annotation_pattern_specificity
    from cad2gis.cad2gis_v3.semantics import (
        _BOITE_FRAME_DEVICE_LAYER_TOKENS,
        _BOITE_FRAME_MIN_LABEL_SPECIFICITY,
    )

    # A pole-legend label such as ``NP7`` matches a generic alphanumeric
    # family and must never prove a BOITE frame.
    assert annotation_pattern_specificity(r"^[A-Za-z]+\d+$") < (
        _BOITE_FRAME_MIN_LABEL_SPECIFICITY
    )
    # Full FAT identifiers (TGGR04-1.022.A01 / KLDYA.011.C01) do.
    assert annotation_pattern_specificity(
        r"^TGGR\d+[^A-Za-z0-9]+\d+[^A-Za-z0-9]+\d+[^A-Za-z0-9]+[A-Za-z]+\d+$"
    ) >= _BOITE_FRAME_MIN_LABEL_SPECIFICITY

    # Base-map layers are never frame-derived BOITE sources; reviewed
    # telecom device layers are.
    assert not any(token in "BASIC MAP" for token in _BOITE_FRAME_DEVICE_LAYER_TOKENS)
    assert any(token in "FAT INFO" for token in _BOITE_FRAME_DEVICE_LAYER_TOKENS)
    assert any(token in "CLOSURE" for token in _BOITE_FRAME_DEVICE_LAYER_TOKENS)
