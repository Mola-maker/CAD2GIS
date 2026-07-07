"""G2 tests — the accuracy scorer must reward a correct conversion and fail a broken one."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("shapely")
from shapely.geometry import LineString, Point, Polygon  # noqa: E402

from cad2gis.model import Feature, FeatureCollection, SourceRef  # noqa: E402
from cad2gis.verify import BenchmarkSpec, score  # noqa: E402

_BENCH = os.path.join(
    os.path.dirname(__file__), "..", "src", "cad2gis", "verify", "benchmark", "synthetic_comms.json"
)


def _perfect_collection() -> FeatureCollection:
    c = FeatureCollection(crs="local-engineering", source_file="synthetic_comms.dxf")
    for i, (x, y) in enumerate([(60, 5), (120, 5), (180, 5)]):
        c.add(Feature(Point(x, y), "manhole", source=SourceRef(layer="RK_人孔")))
        c.add(Feature(Point(x, y + 2), "annotation", attributes={"text": f"RK{i+1}"}))
    for x, y in [(60, 40), (120, 40), (180, 40)]:
        c.add(Feature(Point(x, y), "pole", source=SourceRef(layer="GG_杆")))
    c.add(Feature(Polygon([(0, 0), (40, 0), (40, 25), (0, 25)]), "room"))
    # 2nd room = the (now-closed) unclosed ring from the fixture; benchmark expects room=2.
    c.add(Feature(Polygon([(80, 60), (110, 60), (110, 80), (80, 80)]), "room"))
    c.add(Feature(LineString([(60, 5), (120, 5), (180, 5)]), "cable"))
    c.add(Feature(LineString([(60, 40), (120, 40), (180, 40)]), "duct"))
    return c


def test_perfect_conversion_passes():
    bench = BenchmarkSpec.from_json(_BENCH)
    report = score(_perfect_collection(), bench)
    assert report.passed, report.to_dict()
    assert report.overall >= 0.99
    # network + positional must be reported as not-evaluated (not silently passed).
    not_eval = {d.name for d in report.dimensions if not d.evaluated}
    assert {"network", "positional"} <= not_eval


def test_broken_conversion_fails():
    bench = BenchmarkSpec.from_json(_BENCH)
    c = _perfect_collection()
    # Corrupt it: unmap half the classes and inject an invalid geometry.
    for f in c.features[:6]:
        f.feature_class = None
    c.add(Feature(Polygon([(0, 0), (1, 1), (0, 1), (1, 0)]), "room"))  # self-intersecting
    report = score(c, bench)
    assert not report.passed, report.to_dict()


def test_positional_scored_from_georef():
    bench = BenchmarkSpec.from_json(_BENCH)
    good = score(_perfect_collection(), bench, georef={"n_gcps": 200, "rmse": 0.5})
    bad = score(_perfect_collection(), bench, georef={"n_gcps": 200, "rmse": 12.0})
    pos_good = next(d for d in good.dimensions if d.name == "positional")
    pos_bad = next(d for d in bad.dimensions if d.name == "positional")
    assert pos_good.evaluated and pos_good.score > 0.8
    assert pos_bad.score < pos_good.score


def test_attribute_scored_from_provenance_and_schema():
    from cad2gis.warehouse import PUBLISHED_SCHEMA

    bench = BenchmarkSpec.from_json(_BENCH)
    report = score(_perfect_collection(), bench, schemas=PUBLISHED_SCHEMA)
    attr = next(d for d in report.dimensions if d.name == "attribute")
    # src_file provenance lives on f.source, not f.attributes — the scorer must still find it.
    assert attr.evaluated and attr.score > 0.0


def test_abstention_does_not_penalize_semantic_coverage():
    bench = BenchmarkSpec.from_json(_BENCH)
    c = _perfect_collection()
    # add a reviewed-reject paving symbol (correct negative) — must NOT drag coverage down
    c.add(Feature(Point(999, 999), None, attributes={"_map_evidence": {"decision": "rejected"}}))
    report = score(c, bench)
    sem = next(d for d in report.dimensions if d.name == "semantic")
    assert "abstained=1" in sem.details
