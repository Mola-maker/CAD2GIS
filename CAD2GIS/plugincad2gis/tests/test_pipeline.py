"""G3 + pipeline tests — parse the real synthetic DXF and run the full chain to >=0.90."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("ezdxf")
pytest.importorskip("shapely")

from cad2gis import samples  # noqa: E402
from cad2gis.parse import parse_dxf  # noqa: E402
from cad2gis.pipeline import run  # noqa: E402

_BENCH = os.path.join(
    os.path.dirname(__file__), "..", "src", "cad2gis", "verify", "benchmark", "synthetic_comms.json"
)


@pytest.fixture(scope="module")
def dxf(tmp_path_factory):
    d = tmp_path_factory.mktemp("samples")
    return samples.generate(str(d))


def test_parse_extracts_all_entities(dxf):
    coll, stats = parse_dxf(dxf)
    # 15 authored entities: 5 LWPOLYLINE + 6 POINT + 3 TEXT + 1 LINE.
    assert stats.entities_seen == 15
    assert stats.features_out == 15
    assert set(stats.by_type) == {"LWPOLYLINE", "POINT", "TEXT", "LINE"}
    # provenance present on every feature.
    assert all(f.source.entity_type for f in coll.features)


def test_end_to_end_accuracy(dxf):
    coll, rep = run(dxf, benchmark=_BENCH)
    acc = rep.accuracy
    assert acc is not None and acc["passed"], acc
    assert acc["overall"] >= 0.90
    # cleaning collapsed the 3 injected errors: cable -> 1, and topology ran.
    assert rep.counts_final.get("cable") == 1
    assert rep.topology["dangles_trimmed"] >= 1
    assert rep.topology["lines_removed_duplicate"] >= 1
    # network + CRS were evaluated.
    assert rep.network["connectivity_ratio"] == 1.0
    assert rep.crs["epsg"] == 4490
