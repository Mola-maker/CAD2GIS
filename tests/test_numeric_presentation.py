import xml.etree.ElementTree as ET

from cad2gis.presentation import LENGTH_EXPRESSION, configure_numeric_fields, csv_value


def test_two_decimal_presentation_does_not_round_ids_text_or_source_value():
    source = 13901050.64940948
    assert csv_value(source) == "13901050.65"
    assert source == 13901050.64940948
    assert csv_value(21.14648990531311) == "21.15"
    assert csv_value(-0.0001) == "0.00"
    assert csv_value(123) == 123
    assert csv_value('NP7 2.5"') == 'NP7 2.5"'
    assert csv_value(None) is None


def test_qgis_numeric_formatter_is_idempotent_and_only_targets_real_fields():
    root = ET.Element("qgis")
    fields = [(0, "fid", "INTEGER"), (1, "length_value_m", "REAL"), (2, "label", "TEXT")]
    configure_numeric_fields(root, fields)
    first = ET.tostring(root)
    configure_numeric_fields(root, fields)
    assert ET.tostring(root) == first
    assert [node.get("name") for node in root.findall("fieldConfiguration/field")] == ["length_value_m"]
    assert root.find('.//Option[@name="Precision"]').get("value") == "2"
    assert "'dwg_dimension' THEN ''" in LENGTH_EXPRESSION
    assert "'dwg_curve_geometry' THEN ' [CAD curve]'" in LENGTH_EXPRESSION
    assert "geodesic_length_m" not in LENGTH_EXPRESSION
    assert "delivery_grid_length_m" not in LENGTH_EXPRESSION
