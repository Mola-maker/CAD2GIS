from __future__ import annotations

import pytest

from cad2gis.cad2gis_v3.georef import DirectTransformer


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_inverse_rejects_nonfinite_target_coordinates(value):
    transformer = DirectTransformer("EPSG:23846", "EPSG:3857")
    with pytest.raises(ValueError, match="finite"):
        transformer.target_to_source_point((value, 0))
    with pytest.raises(ValueError, match="finite"):
        transformer.target_to_source_points([(0, 0), (0, value)])


@pytest.mark.parametrize("source_crs,point", [
    ("EPSG:23846", (750000.0, 600000.0)),
    ("EPSG:21148", (700000.0, 9300000.0)),
])
def test_legacy_datum_2d_inverse_preserves_forward_coordinates(source_crs, point):
    transformer = DirectTransformer(source_crs, "EPSG:3857")
    points = [point, (point[0] + 125.25, point[1] - 72.75)]
    expected = [transformer.forward.TransformPoint(*p)[:2] for p in points]
    assert transformer.points(points) == expected
    assert transformer.roundtrip_error(points) < 1e-6
    for p, target in zip(points, expected):
        assert transformer.target_to_source_point(target) == pytest.approx(p, abs=1e-6, rel=0)
    assert transformer.target_to_source_points(expected) == [
        transformer.target_to_source_point(p) for p in expected
    ]


def test_batched_crs_paths_match_scalar_reference() -> None:
    transformer = DirectTransformer("EPSG:3857", "EPSG:32748")
    points = [
        (12_000_000.0, 100_000.0),
        (12_000_125.25, 100_350.75),
        (12_001_000.5, 101_500.125),
    ]

    scalar_targets = [transformer.point(point) for point in points]
    batch_targets = transformer.points(points)
    assert batch_targets == pytest.approx(scalar_targets, rel=0.0, abs=1e-9)

    scalar_sources = [
        transformer.target_to_source_point(point) for point in batch_targets
    ]
    batch_sources = transformer.target_to_source_points(batch_targets)
    assert batch_sources == pytest.approx(scalar_sources, rel=0.0, abs=1e-9)

    scalar_roundtrip = max(
        transformer.source_length_to_m(
            ((source[0] - point[0]) ** 2 + (source[1] - point[1]) ** 2) ** 0.5
        )
        for point, source in zip(points, scalar_sources)
    )
    assert transformer.roundtrip_error(points) == pytest.approx(
        scalar_roundtrip, rel=0.0, abs=1e-12,
    )
