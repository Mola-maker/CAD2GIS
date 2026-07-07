"""G4/G9 CRS-classifier tests."""
from __future__ import annotations

from cad2gis.crs import classify_extent


def test_geographic_extent():
    g = classify_extent(116.30, 39.90, 116.50, 40.05)
    assert g.label == "geographic"
    assert g.epsg == 4490


def test_projected_gauss_kruger():
    g = classify_extent(500000, 3900000, 520000, 3920000)
    assert g.label == "projected"


def test_local_engineering():
    g = classify_extent(0, 0, 200, 60)
    assert g.label == "local-engineering"
