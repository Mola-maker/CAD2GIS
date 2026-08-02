from __future__ import annotations

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.source_dependencies import assess_source_dependencies


def _entity(kind: str, status: str = "not_external") -> SourceEntity:
    return SourceEntity.from_record({
        "entity_key": f"{kind}:{status}",
        "source_sha256": "d" * 64,
        "source_file": "host.dwg",
        "handle": "1",
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": "0",
        "object_name": f"ACDB{kind}",
        "dwg_type_name": kind,
        "points": ((0.0, 0.0),),
        "centroid": (0.0, 0.0),
        "closed": False,
        "text": "",
        "block_name": "",
        "block_attributes": {},
        "raw_properties": {
            "external_reference_status": status,
            "external_reference_path": "dependency.dwg",
        },
    })


def test_blocks_unmaterialized_dwg_geometry_dependency() -> None:
    result = assess_source_dependencies([_entity("INSERT", "xref")])
    assert result["passed"] is False
    assert result["status"] == "SOURCE_GEOMETRY_DEPENDENCY_MISSING"


def test_records_visual_reference_without_blocking_vector_conversion() -> None:
    result = assess_source_dependencies([_entity("IMAGE")])
    assert result["passed"] is True
    assert result["status"] == "SOURCE_VISUAL_DEPENDENCY_RECORDED"
    assert result["visual_reference_count"] == 1
