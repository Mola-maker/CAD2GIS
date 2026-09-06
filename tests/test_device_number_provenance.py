"""A nearby numeric annotation is not a direct block attribute."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cad2gis.cad2gis_v3.config import AnnotationFamily
from cad2gis.cad2gis_v3.model import CadStyle, SourceEntity
from cad2gis.cad2gis_v3.semantics import classify_entities


def entity(handle, kind, layer, points, *, text="", block="", attrs=None):
    return SourceEntity(
        entity_key=f"source:{handle}", source_sha256="a" * 64, source_file="source.dwg",
        handle=handle, layout="Model", layout_role="model", cad_role="model", layer=layer,
        object_name=kind, dwg_type=kind, points=points, centroid=(1.0, 1.0) if kind == "LWPOLYLINE" else points[0],
        closed=len(points) > 1 and points[0] == points[-1], text=text, block_name=block,
        block_attributes=attrs or {}, style=CadStyle(),
    )


@pytest.mark.parametrize("mode", ["nearby", "attribute", "outside"])
def test_device_number_source_is_retained_and_nearby_selection_requires_review(mode):
    frame = entity("frame", "LWPOLYLINE", "FAT FRAME",
                   ((0., 0.), (2., 0.), (2., 2.), (0., 2.), (0., 0.)))
    if mode == "attribute":
        frame = entity("frame", "INSERT", "FAT FRAME", ((1., 1.),), block="FAT", attrs={"F": "7"})
        object.__setattr__(frame, "raw_properties", {"owned_attribute_texts": ["7"]})
    label = entity("name", "TEXT", "FAT CODE", ((3., 1.),), text="TGGR04-1.022.A03")
    integer = entity("number", "TEXT", "FAT LABEL", ((30., 30.),) if mode == "outside" else ((2., 2.),), text="9")
    original = deepcopy([frame, label, integer])
    family = AnnotationFamily(
        family_id="device", target_class="BOITE",
        text_pattern=r"^TGGR\d+[^A-Za-z0-9]+\d+[^A-Za-z0-9]+\d+[^A-Za-z0-9]+A\d+$",
        source_layer_pattern="FAT CODE", target_layer_pattern="FAT FRAME", require_same_layer=False,
        max_distance_native_m=15., rule_id="fixture-device", provenance="DWG_DERIVED:reviewed-annotation",
    )
    registry = SimpleNamespace(
        layers={}, positive_route_layer_regex="^CABLE$", block_families={"BOITE": ("FAT",)},
        insert_layer_families={"BOITE": ("FAT FRAME",)}, annotation_families=(family,),
        decision_rules={"annotation_assignment": {"rule_id": "fixture", "method": "nearest"}},
    )
    features, _, unresolved, diagnostics = classify_entities(original, registry, coverage_policy="abstain")
    result, = [feature for feature in features if feature.feature_class == "BOITE"]
    assert original == [frame, label, integer]
    if mode == "nearby":
        assert result.native_points == [[1.0, 1.0]]
        assert result.display_label == "TGGR04-1.022.A03 · 9"
        assert "DWG_DERIVED:nearby-integer-label" in result.label_provenance
        assert "block-attribute-text" not in result.label_provenance
        proof, = [item for item in result.lineage if item["operation"] == "select_device_number_label"]
        assert proof["source_entity_key"] == integer.entity_key
        assert proof["selected_value"] == integer.text
        assert proof["target_source_entity_key"] == frame.entity_key
        assert proof["distance_native_m"] == pytest.approx(2 ** .5)
        assert proof["geometry_changed"] is False
        review, = diagnostics["device_number_label_reviews"]
        assert review["status"] == "review_required" and review in unresolved
    elif mode == "attribute":
        assert result.display_label == "TGGR04-1.022.A03 · 7"
        assert "DWG_DIRECT:block-attribute-text" in result.label_provenance
        assert not diagnostics["device_number_label_reviews"]
    else:
        assert "DEVICE_NUMBER" not in result.attributes
        assert not diagnostics["device_number_label_reviews"]
