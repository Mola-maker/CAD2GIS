from __future__ import annotations

from cad2gis.cad2gis_v3.coordinate_domain import assess_coordinate_domain
from cad2gis.cad2gis_v3.model import SourceEntity


def _line(points: tuple[tuple[float, float], ...]) -> SourceEntity:
    return SourceEntity.from_record({
        "entity_key": "line",
        "source_sha256": "c" * 64,
        "source_file": "generic.dwg",
        "handle": "1",
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": "route",
        "object_name": "ACDBLINE",
        "dwg_type_name": "LINE",
        "points": points,
        "centroid": points[0],
        "closed": False,
        "text": "",
        "block_name": "",
        "block_attributes": {},
    })


def test_rejects_local_coordinates_mislabeled_as_indonesian_utm_46n() -> None:
    result = assess_coordinate_domain(
        [_line(((2300.0, -9600.0), (10500.0, -8800.0)))],
        "EPSG:23846",
    )

    assert result["passed"] is False
    assert result["status"] == "LOCAL_OR_MISREGISTERED_COORDINATES"
    assert result["inside_fraction"] == 0.0


def test_accepts_coordinates_inside_indonesian_utm_46n_domain() -> None:
    result = assess_coordinate_domain(
        [_line(((760000.0, 605000.0), (765000.0, 610000.0)))],
        "EPSG:23846",
    )

    assert result["passed"] is True
    assert result["status"] == "PLAUSIBLE_DECLARED_CRS_DOMAIN"
