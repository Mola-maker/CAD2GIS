from __future__ import annotations

from cad2gis.cad2gis_v3.warehouse import _dimension_measurement_label
from cad2gis.reader.libredwg import _dimension_display_value


def test_dimension_display_value_prefers_rendered_dwg_text() -> None:
    assert _dimension_display_value(r"{\H0.75x;50m}") == 50.0
    assert _dimension_display_value(r"\A1;44m") == 44.0
    assert _dimension_display_value("50.5 m") == 50.5


def test_dimension_placeholder_falls_back_to_raw_measurement() -> None:
    assert _dimension_display_value(r"{\H0.75x;<>}") is None
    assert _dimension_display_value("") is None


def test_dimension_measurement_label_keeps_dwg_integer_text() -> None:
    assert _dimension_measurement_label(r"{\H0.75x;50m}", 49.669) == "50 m [DWG DIMENSION]"
    assert _dimension_measurement_label(r"{\H0.75x;<>}", 49.669) == "49.669 m [DWG DIMENSION]"
