"""Reviewed FTTH legend knowledge must survive to the QML and the gpkg.

The reviewed drawings use a shared legend for cable colour codes and pole
types.  The semantics stage records the legend name on
``delivery_style_render_key``; the georeference stage appends its rotation
suffix without clobbering it; the style writer must emit QML category values
that exactly match the gpkg column while showing only the legend name to the
user.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cad2gis.cad2gis_v3.cable_legend import (  # noqa: E402
    cable_spec_name,
    ptech_type_name,
)
from cad2gis.cad2gis_v3.georef import enrich_delivery_metrics  # noqa: E402
from cad2gis.cad2gis_v3.model import CadStyle, Feature  # noqa: E402
from cad2gis.cad2gis_v3.styles import write_styles  # noqa: E402


def _feature(feature_class, source_layer, style, *, render_key=None):
    return Feature(
        feature_key=f"{feature_class}-{source_layer}",
        feature_class=feature_class,
        geometry_kind="Point" if feature_class == "PTECH" else "LineString",
        native_points=[(0.0, 0.0), (1.0, 0.0)],
        source_entity_key=f"{feature_class}-{source_layer}",
        source_handle=source_layer,
        source_layer=source_layer,
        geometry_role="SOURCE_ROUTE",
        style=style,
        attributes={
            "delivery_style_render_key": render_key or f"{feature_class}:draft",
            "delivery_style_qgis_rotation_deg": 0.0,
        },
    )


def _qml_categories(path: Path, layer_name: str):
    import xml.etree.ElementTree as ET

    root = ET.parse(path / f"{layer_name}.qml").getroot()
    renderer = root.find("renderer-v2")
    return [
        (category.attrib["value"], category.attrib["label"])
        for category in renderer.find("categories").findall("category")
    ]


def test_ptech_type_names_cover_reviewed_layer_variants():
    green = SimpleNamespace(aci_color=3, true_color="#00FF00")
    for layer, expected in (
        ("NEW POLE 9m 4 inches", 'NP9 4"'),
        ("NEW POLE 7m(4 inches)", 'NP7 4"'),
        ("NEW POLE 7-4", 'NP7 4"'),
        ("Pole 7m 4'", 'NP7 4"'),
        ("NEW POLE 7m(3 inches)", 'NP7 3"'),
        ("NEW POLE 7-3", 'NP7 3"'),
        ("NEW POLE 7m(2.5 inches)", 'NP7 2.5"'),
        ("NEW POLE 7-2.5", 'NP7 2.5"'),
        ("EXT POLE", "EXISTING"),
        ("EXISTING POLE", "EXISTING"),
    ):
        feature = SimpleNamespace(source_layer=layer, style=green)
        assert ptech_type_name(feature) == expected, layer

    # A size-less FDT pole layer falls back to the reviewed colour mapping.
    cyan = SimpleNamespace(aci_color=4, true_color="#00FFFF")
    assert ptech_type_name(SimpleNamespace(
        source_layer="NEW POLE FDT 1", style=cyan,
    )) == 'NP7 3"'


def test_cable_spec_name_reads_route_layers_and_compact_core_labels():
    style = SimpleNamespace(aci_color=256)
    assert cable_spec_name("FO 24 CORE", style) == "24"
    assert cable_spec_name("FO 24 CORE LINE A - FDT 1", style) == "24"
    assert cable_spec_name("XXX.XXX - Cable Line A (FO Cable 24C_2T) - AE", style) == "24"
    assert cable_spec_name("SLING WIRE FDT 2", style) == "SLING WIRE"


def test_style_writer_qml_categories_match_gpkg_render_keys(tmp_path: Path):
    cable = _feature(
        "CABLE", "FO 48 CORE",
        CadStyle(aci_color=200, true_color="#8000A0", linetype="ByLayer", lineweight=29),
        render_key="CABLE:48",
    )
    pole = _feature(
        "PTECH", "NEW POLE 7m(4 inches)",
        CadStyle(aci_color=3, true_color="#00FF00", linetype="ByLayer", lineweight=29),
        render_key='PTECH:NP7 4"',
    )
    write_styles(tmp_path, [cable, pole], coverage_policy="abstain")

    cable_categories = _qml_categories(tmp_path, "CABLE")
    assert cable_categories == [("CABLE:48|ROT_QGIS:0.000000000", "48")]

    pole_categories = _qml_categories(tmp_path, "PTECH")
    assert pole_categories == [('PTECH:NP7 4"|ROT_QGIS:0.000000000', 'NP7 4"')]


def test_georef_rotation_suffix_preserves_semantic_legend_key():
    pole = _feature(
        "PTECH", "NEW POLE 7m(4 inches)",
        CadStyle(aci_color=3, true_color="#00FF00", linetype="ByLayer", lineweight=29),
        render_key='PTECH:NP7 4"',
    )

    class Transformer:
        def qgis_rotation(self, _centroid, _cad_rotation):
            return 12.345

        def point(self, native_point):
            return native_point

        coordinate_provenance = "TEST:fake"

    enrich_delivery_metrics([pole], Transformer())
    assert pole.attributes["delivery_style_render_key"] == (
        'PTECH:NP7 4"|ROT_QGIS:12.345000000'
    )
    assert pole.attributes["delivery_style_qgis_rotation_deg"] == 12.345


if __name__ == "__main__":
    pytest.main([__file__])


def test_znro_red_boundary_detection_rejects_bylayer_grey_and_accepts_batas_red():
    from cad2gis.cad2gis_v3.semantics import _is_red_boundary

    grey_bylayer = SimpleNamespace(
        style=SimpleNamespace(aci_color=256, true_color="#938953"),
    )
    assert _is_red_boundary(grey_bylayer) is False

    red_village_boundary = SimpleNamespace(
        style=SimpleNamespace(aci_color=1, true_color="#FF0000"),
    )
    assert _is_red_boundary(red_village_boundary) is True
