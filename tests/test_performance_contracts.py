"""Performance changes must preserve geometry and decision semantics."""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cad2gis.cad2gis_v3.artifact_io import (  # noqa: E402
    read_json_object,
    write_json_object,
)
from cad2gis.cad2gis_v3.spatial_filter import _deployment_anchor_radius  # noqa: E402
from cad2gis.cad2gis_v3.stage_contract import StageRecorder  # noqa: E402
from cad2gis.cad2gis_v3.topology import (  # noqa: E402
    _NearestFeatureIndex,
    _nearest_unique_linear,
)


def test_strtree_nearest_is_semantically_identical_to_linear_reference():
    random_source = random.Random(240901)
    features = [
        SimpleNamespace(
            feature_key=f"feature-{index:04d}",
            native_centroid=(
                random_source.uniform(-500.0, 500.0),
                random_source.uniform(-500.0, 500.0),
            ),
        )
        for index in range(500)
    ]
    # Deliberate near-tie locks down the 1 cm ambiguity rule.
    features.extend([
        SimpleNamespace(feature_key="tie-a", native_centroid=(0.0, 0.0)),
        SimpleNamespace(feature_key="tie-b", native_centroid=(0.005, 0.0)),
    ])
    index = _NearestFeatureIndex(features)
    queries = [
        (random_source.uniform(-550.0, 550.0), random_source.uniform(-550.0, 550.0))
        for _ in range(100)
    ] + [(0.0025, 0.0), (10_000.0, 10_000.0)]
    for point in queries:
        expected = _nearest_unique_linear(point, features, 75.0)
        actual = index.nearest_unique(point, 75.0)
        assert actual[2] == expected[2]
        assert (
            None if actual[0] is None else actual[0].feature_key
        ) == (None if expected[0] is None else expected[0].feature_key)
        if expected[1] is None:
            assert actual[1] is None
        else:
            assert math.isclose(actual[1], expected[1], abs_tol=1e-12)


def test_strtree_anchor_density_preserves_legacy_median_rule():
    points = [(0.0, 0.0), (10.0, 0.0), (30.0, 0.0), (30.0, 0.0)]
    distances = []
    for index, point in enumerate(points):
        distances.append(min(
            math.dist(point, other)
            for other_index, other in enumerate(points)
            if other_index != index
        ))
    positive = sorted(value for value in distances if value > 0.01)
    expected = max(100.0, 10.0 * positive[len(positive) // 2])
    assert _deployment_anchor_radius(points) == expected


def test_gzip_json_is_deterministic_and_transparently_readable(tmp_path: Path):
    payload = {"schema_version": "test.v1", "nodes": [{"value": "x" * 5000}]}
    left = tmp_path / "left.json.gz"
    right = tmp_path / "right.json.gz"
    write_json_object(left, payload)
    write_json_object(right, payload)
    assert left.read_bytes() == right.read_bytes()
    assert left.stat().st_size < len(str(payload).encode("utf-8"))
    assert read_json_object(left) == payload


def test_stage_contract_cache_key_and_output_hash_are_deterministic():
    first = StageRecorder()
    second = StageRecorder()
    first.run(
        "classify", version="v1", inputs={"source": "abc"},
        operation=lambda: [3, 2, 1], summarize=lambda value: {"values": value},
    )
    second.run(
        "classify", version="v1", inputs={"source": "abc"},
        operation=lambda: [3, 2, 1], summarize=lambda value: {"values": value},
    )
    assert first.receipts[0]["cache_key"] == second.receipts[0]["cache_key"]
    assert first.receipts[0]["output_sha256"] == second.receipts[0]["output_sha256"]
