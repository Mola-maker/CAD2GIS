from __future__ import annotations

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.scene_partition import detect_style_catalog_entities


SOURCE_HASH = "b" * 64


def _entity(
    key: str,
    *,
    kind: str,
    layer: str,
    points: tuple[tuple[float, float], ...],
    block_name: str = "",
    native_length: float | None = None,
) -> SourceEntity:
    x = sum(point[0] for point in points) / len(points)
    y = sum(point[1] for point in points) / len(points)
    return SourceEntity.from_record({
        "entity_key": key,
        "source_sha256": SOURCE_HASH,
        "source_file": "generic.dwg",
        "handle": key,
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "style_legend",
        "layer": layer,
        "object_name": f"ACDB{kind}",
        "dwg_type_name": kind,
        "points": points,
        "centroid": (x, y),
        "closed": False,
        "text": "",
        "block_name": block_name,
        "block_attributes": {},
        "native_length": native_length,
    })


def test_detects_translated_multistyle_line_catalog_without_layer_names() -> None:
    samples = [
        _entity(
            f"sample-{index}",
            kind="LWPOLYLINE",
            layer=f"vendor-style-{index}",
            points=((10.0, index * 4.0), (30.0, index * 4.0)),
            native_length=20.0,
        )
        for index in range(6)
    ]
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="vendor-style-2",
        points=((100.0, 100.0), (180.0, 125.0)),
        native_length=83.815273,
    )

    excluded, diagnostics = detect_style_catalog_entities([*samples, route])

    assert excluded == frozenset(entity.entity_key for entity in samples)
    assert diagnostics["status"] == "CATALOG_EXCLUDED"
    assert diagnostics["groups"][0]["kind"] == "translated_shape_catalog"


def test_detects_aligned_diverse_symbol_catalog_but_keeps_repeated_assets() -> None:
    catalog = [
        _entity(
            f"catalog-{index}",
            kind="INSERT",
            layer=f"symbol-layer-{index}",
            block_name=f"symbol-{index}",
            points=((42.0, index * 8.0),),
        )
        for index in range(7)
    ]
    assets = [
        _entity(
            f"asset-{index}",
            kind="INSERT",
            layer="pole",
            block_name="standard-pole",
            points=((100.0, index * 10.0),),
        )
        for index in range(8)
    ]

    excluded, _ = detect_style_catalog_entities([*catalog, *assets])

    assert excluded == frozenset(entity.entity_key for entity in catalog)

