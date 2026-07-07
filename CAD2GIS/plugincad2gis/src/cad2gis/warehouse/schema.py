"""Published GIS schema (story G10) — the warehousing contract per feature class.

A "standardized warehousing" (标准化入库) deliverable needs an explicit, published schema: each
GIS feature class has a fixed geometry type and a defined field set (with provenance), so the
GeoPackage is self-describing and the attribute-completeness accuracy dimension can be measured
against required fields. This is deliberately small and comms-focused; it is the extension point
where audit/BOM/design fields would later bolt on.

Provenance fields (src_layer/src_block/src_handle/src_file) back the "lossless/traceable" claim —
every warehoused feature can be traced to the exact CAD entity it came from.
"""
from __future__ import annotations

from ..model import FeatureClassSchema, FieldSpec

# Provenance fields carried on EVERY class (the traceability contract).
_PROVENANCE = [
    FieldSpec("src_file", "str", required=True),
    FieldSpec("src_layer", "str", required=False),
    FieldSpec("src_block", "str", required=False),
    FieldSpec("src_handle", "str", required=False),
    FieldSpec("src_entity", "str", required=False),
    FieldSpec("confidence", "float", required=False),
    FieldSpec("map_rule", "str", required=False),
]

PUBLISHED_SCHEMA: dict[str, FeatureClassSchema] = {
    "manhole": FeatureClassSchema(
        "manhole", "Point",
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str", required=True),
         FieldSpec("node_type", "str")] + _PROVENANCE,
    ),
    "cable": FeatureClassSchema(
        "cable", "LineString",
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str", required=True),
         FieldSpec("length_m", "float")] + _PROVENANCE,
    ),
    "duct": FeatureClassSchema(
        "duct", "LineString",  # duct may be LineString (route) or Point (cross-section symbol)
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str", required=True),
         FieldSpec("length_m", "float"),
         FieldSpec("spec", "str"),
         FieldSpec("holes", "int"),
         FieldSpec("material", "str"),
         FieldSpec("diameter_mm", "int")] + _PROVENANCE,
    ),
    "annotation": FeatureClassSchema(
        "annotation", "Point",
        [FieldSpec("text", "str", required=True),
         FieldSpec("facility", "str")] + _PROVENANCE,
    ),
    "control_point": FeatureClassSchema(
        "control_point", "Point",
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str", required=True),
         FieldSpec("point_id", "str")] + _PROVENANCE,
    ),
    "pole": FeatureClassSchema(
        "pole", "Point",
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str")] + _PROVENANCE,
    ),
    "closure": FeatureClassSchema(
        "closure", "Point",
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str")] + _PROVENANCE,
    ),
    "cabinet": FeatureClassSchema(
        "cabinet", "Point",
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str")] + _PROVENANCE,
    ),
    "room": FeatureClassSchema(
        "room", "Polygon",
        [FieldSpec("facility", "str", required=True),
         FieldSpec("discipline", "str"),
         FieldSpec("area_m2", "float")] + _PROVENANCE,
    ),
}


def schema_for(feature_class: str):
    return PUBLISHED_SCHEMA.get(feature_class)
