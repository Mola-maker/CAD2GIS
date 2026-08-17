from __future__ import annotations

from cad2gis.cad2gis_v3 import pipeline
from cad2gis.cad2gis_v3.model import CadStyle, SourceEntity


def _entity(entity_key: str, raw_properties: dict) -> SourceEntity:
    return SourceEntity(
        entity_key=entity_key,
        source_sha256="a" * 64,
        source_file="fixture.dwg",
        handle=entity_key,
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="0",
        object_name="AcDbLine",
        dwg_type="LINE",
        points=((0.0, 0.0), (1.0, 1.0)),
        centroid=(0.5, 0.5),
        closed=False,
        text="",
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=1),
        raw_properties=raw_properties,
    )


def test_drawing_space_entities_excludes_materialized_block_members() -> None:
    drawing = _entity("drawing", {})
    materialized = _entity("materialized", {
        "plan_domain": {
            "materialization": "nested-insert-affine",
            "root_entity_key": "root",
        }
    })
    selected = pipeline._drawing_space_entities([drawing, materialized])
    assert selected == [drawing]
