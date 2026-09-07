"""Two-decimal presentation without quantizing authoritative source or geometry."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET

LENGTH_EXPRESSION = ('CASE WHEN "length_value_m" IS NOT NULL THEN '
                     'format_number("length_value_m", 2) || \' m\' || '
                     'CASE WHEN "length_source" = \'dwg_dimension\' THEN \'\' '
                     'WHEN "length_source" = \'dwg_curve_geometry\' THEN \' [CAD curve]\' ELSE \' [unverified source]\' END END')


def csv_value(value):
    if isinstance(value, float) and math.isfinite(value):
        rendered = format(value, ".2f")
        return "0.00" if rendered == "-0.00" else rendered
    return value


def configure_numeric_fields(style, fields):
    """QGIS Range formatter controls table display; stored SQLite REAL is intact."""
    configuration = style.find("fieldConfiguration")
    if configuration is None:
        configuration = ET.SubElement(style, "fieldConfiguration")
    for info in fields:
        name, kind = info[1], str(info[2]).upper()
        if not any(token in kind for token in ("REAL", "DOUBLE", "FLOAT", "NUMERIC", "DECIMAL")):
            continue
        field = next((node for node in configuration.findall("field") if node.get("name") == name), None)
        if field is None:
            field = ET.SubElement(configuration, "field", name=name)
        for widget in list(field.findall("editWidget")):
            field.remove(widget)
        widget = ET.SubElement(field, "editWidget", type="Range")
        options = ET.SubElement(ET.SubElement(widget, "config"), "Option", type="Map")
        for key, value, datatype in [("Precision", "2", "int"), ("AllowNull", "true", "bool"),
                                     ("Style", "SpinBox", "QString"), ("Min", "-1e100", "double"),
                                     ("Max", "1e100", "double"), ("Step", "0.01", "double")]:
            ET.SubElement(options, "Option", name=key, value=value, type=datatype)
