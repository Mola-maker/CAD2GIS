from __future__ import annotations

import pytest

from cad2gis.cad2gis_v3.model import CadStyle, Feature
from cad2gis.cad2gis_v3.warehouse import (
    _dimension_measurement_label,
    _segment_business_label,
)
from cad2gis.reader.libredwg import _dimension_display_value


def test_dimension_display_value_prefers_rendered_dwg_text() -> None:
    assert _dimension_display_value(r"{\H0.75x;50m}") == 50.0
    assert _dimension_display_value(r"\A1;44m") == 44.0
    assert _dimension_display_value("50.5 m") == 50.5


def test_dimension_placeholder_falls_back_to_raw_measurement() -> None:
    assert _dimension_display_value(r"{\H0.75x;<>}") is None
    assert _dimension_display_value("") is None


def test_dimension_measurement_label_keeps_dwg_integer_text() -> None:
    assert _dimension_measurement_label(r"{\H0.75x;50m}", 49.669) == "50m"
    assert _dimension_measurement_label(r"{\H0.75x;<>}", 49.669) == "49.669m"


def test_segment_business_label_never_uses_length_evidence() -> None:
    feature = Feature(
        feature_key="route:1",
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=[(0.0, 0.0), (1.0, 0.0)],
        source_entity_key="entity:1",
        source_handle="1",
        source_layer="FIBER",
        geometry_role="SOURCE_ROUTE",
        style=CadStyle(),
        display_label="",
        label_provenance="UNAVAILABLE",
    )
    assert _segment_business_label(feature) == ("", "UNAVAILABLE")

    feature.display_label = "FO 24C / ROUTE A"
    feature.label_provenance = "DWG_DIRECT:text"
    assert _segment_business_label(feature) == (
        "FO 24C / ROUTE A",
        "DWG_DIRECT:text",
    )

    feature.label_provenance = "UNAVAILABLE"
    with pytest.raises(RuntimeError, match="lacks source provenance"):
        _segment_business_label(feature)
