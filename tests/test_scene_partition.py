from __future__ import annotations

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.plan_domain import build_plan_domain
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



def test_exempt_entities_are_removed_from_catalog_candidates() -> None:
    samples = [
        _entity(
            f"sample-{index}",
            kind="LWPOLYLINE",
            layer=f"CABLE-{index}",
            points=((10.0, index * 4.0), (30.0, index * 4.0)),
            native_length=20.0,
        )
        for index in range(6)
    ]

    excluded, diagnostics = detect_style_catalog_entities(
        samples,
        exempt=lambda entity: entity.layer.startswith("CABLE"),
    )

    assert excluded == frozenset()
    assert diagnostics["status"] == "NO_CATALOG_DETECTED"
    assert diagnostics["exempted_entity_count"] == 6


def test_exempt_entity_does_not_vote_in_catalog_signature() -> None:
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
        layer="CABLE ROUTE",
        points=((10.0, 24.0), (30.0, 24.0)),
        native_length=20.0,
    )

    excluded, diagnostics = detect_style_catalog_entities(
        [*samples, route],
        exempt=lambda entity: entity.layer == "CABLE ROUTE",
    )

    assert excluded == frozenset(entity.entity_key for entity in samples)
    assert "route" not in excluded
    assert diagnostics["exempted_entity_count"] == 1


def test_default_call_adds_no_exemption_diagnostics_key() -> None:
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="vendor-style-2",
        points=((100.0, 100.0), (180.0, 125.0)),
        native_length=83.815273,
    )

    _, diagnostics = detect_style_catalog_entities([route])

    assert "exempted_entity_count" not in diagnostics


def test_plan_domain_keeps_unreviewed_catalog_candidates() -> None:
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

    view = build_plan_domain(samples)

    assert {entity.entity_key for entity in view.entities} == {
        entity.entity_key for entity in samples
    }
    assert view.diagnostics["scene_partition"]["status"] == "CANDIDATES_ONLY"
    assert (
        view.diagnostics["scene_partition"]["automatic_exclusion_applied"]
        is False
    )
