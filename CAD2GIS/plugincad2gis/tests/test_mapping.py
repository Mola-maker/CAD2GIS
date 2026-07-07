"""G6 tests — the semantic mapping engine classifies comms layers deterministically."""
from __future__ import annotations

import pytest

pytest.importorskip("yaml")
from cad2gis.mapping import MappingEngine  # noqa: E402

# (layer, entity_type, expected_class) drawn from the synthetic fixture's naming.
CASES = [
    ("GX_光缆", "LWPOLYLINE", "cable"),
    ("GD_管道", "LWPOLYLINE", "duct"),
    ("RK_人孔", "POINT", "manhole"),
    ("GG_杆", "POINT", "pole"),
    ("JF_机房", "LWPOLYLINE", "room"),
    ("ZS_注记", "TEXT", "annotation"),
    ("FIBER-CABLE-01", "LINE", "cable"),
    ("manhole_pts", "POINT", "manhole"),
]


@pytest.fixture(scope="module")
def engine():
    return MappingEngine.from_yaml()


@pytest.mark.parametrize("layer,etype,expected", CASES)
def test_classify(engine, layer, etype, expected):
    r = engine.classify(layer=layer, entity_type=etype)
    assert r.feature_class == expected, f"{layer} -> {r.feature_class} (want {expected})"
    assert r.mapped and 0.0 < r.confidence <= 1.0


def test_unmapped_returns_none(engine):
    r = engine.classify(layer="RANDOM_TITLEBLOCK", entity_type="INSERT")
    assert not r.mapped and r.feature_class is None
