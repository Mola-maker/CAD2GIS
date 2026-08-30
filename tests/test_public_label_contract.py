from __future__ import annotations

from types import SimpleNamespace

from cad2gis.cad2gis_v3.model import CadStyle, SourceEntity
from cad2gis.cad2gis_v3.semantics import classify_entities


def _entity(*, text: str) -> SourceEntity:
    return SourceEntity(
        entity_key="source:text:1a",
        source_sha256="a" * 64,
        source_file="unseen-vendor.dwg",
        handle="1a",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="HOME LABELS",
        object_name="AcDbText",
        dwg_type="TEXT",
        points=((10.0, 20.0),),
        centroid=(10.0, 20.0),
        closed=False,
        text=text,
        block_name="",
        block_attributes={},
        style=CadStyle(),
    )


def _registry():
    return SimpleNamespace(
        block_families={},
        insert_layer_families={},
        positive_route_layer_regex="(?!)",
        layers={"homepass": ("HOME LABELS",)},
        field_rules={
            "IMB": {
                "CODE": {
                    "kind": "entity-text",
                    "provenance": "DWG_DIRECT:text|RULE:TEST-IMB-CODE",
                }
            }
        },
        display_label_rules={
            "IMB": {
                "kind": "attribute-field",
                "field": "CODE",
                "provenance": "DWG_DIRECT:text|RULE:TEST-IMB-LABEL",
            }
        },
        annotation_families=(),
        decision_rules={},
        thresholds={},
    )


def test_generated_stable_code_is_not_a_public_label() -> None:
    features, _, _, _ = classify_entities(
        [_entity(text="")],
        _registry(),
        coverage_policy="abstain",
    )

    assert len(features) == 1
    feature = features[0]
    assert feature.attributes["CODE"] == "IMB-CAD-1A"
    assert feature.field_provenance["CODE"] == "DWG_DERIVED:stable-handle-id"
    assert feature.display_label == ""
    assert feature.label_provenance == "UNAVAILABLE"


def test_explicit_source_text_remains_a_public_label() -> None:
    features, _, _, _ = classify_entities(
        [_entity(text="CUSTOMER-042")],
        _registry(),
        coverage_policy="abstain",
    )

    feature = features[0]
    assert feature.attributes["CODE"] == "CUSTOMER-042"
    assert feature.field_provenance["CODE"].startswith("DWG_DIRECT:text")
    assert feature.display_label == "CUSTOMER-042"
    assert feature.label_provenance.startswith("DWG_DIRECT:text")
