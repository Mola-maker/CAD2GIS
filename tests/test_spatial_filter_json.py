from __future__ import annotations

import json
from pathlib import Path

from cad2gis.cad2gis_v3 import spatial_filter


def test_save_spatial_regions_persists_layer_verdicts(tmp_path: Path) -> None:
    path = tmp_path / "spatial_regions.json"
    llm_result = {
        "decisions": [
            {
                "cluster_id": "LC-001",
                "member_count": 3,
                "disposition": "legend",
                "confidence": 1.0,
                "justification": "legend column",
                "detector_source": "legend_detector",
                "applied": True,
            }
        ],
        "diagnostics": {
            "llm_model": "fixture-model",
            "status": "complete",
        },
    }
    legend_diag = {
        "clusters": [
            {
                "cluster_id": "LC-001",
                "member_ids": ["e1", "e2", "e3"],
            }
        ]
    }
    flag_map = {"e1": "legend_detector", "e2": "legend_detector", "e3": "legend_detector"}
    layer_semantics = {
        "status": "complete",
        "verdicts": [
            {
                "layer": "TITLE BLOCK",
                "verdict": "non_subject",
                "confidence": 1.0,
                "justification": "title block",
            }
        ],
    }

    spatial_filter._save_spatial_regions(
        path,
        llm_result,
        legend_diag,
        flag_map,
        "assist",
        layer_semantics=layer_semantics,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["clusters"][0]["member_ids"] == ["e1", "e2", "e3"]
    assert payload["layer_verdicts"] == [
        {
            "layer": "TITLE BLOCK",
            "verdict": "non_subject",
            "confidence": 1.0,
            "justification": "title block",
            "provenance": {
                "confirmed_by": "llm-assist",
                "confirmed_at": payload["layer_verdicts"][0]["provenance"][
                    "confirmed_at"
                ],
            },
        }
    ]


def test_save_spatial_regions_ignored_in_off_mode(tmp_path: Path) -> None:
    path = tmp_path / "spatial_regions.json"
    spatial_filter._save_spatial_regions(
        path, {"decisions": [], "diagnostics": {}}, {"clusters": []}, {}, "off",
    )
    assert not path.exists()


class _FakeEntity:
    def __init__(self, key, layer, points, *, dwg_type="LWPOLYLINE",
                 native_length=None, text="", materialized=False,
                 block_attributes=None):
        self.entity_key = key
        self.handle = key
        self.layer = layer
        self.points = tuple(tuple(p) for p in points)
        self.dwg_type = dwg_type
        self.native_length = native_length
        self.text = text
        self.centroid = (
            sum(p[0] for p in self.points) / len(self.points),
            sum(p[1] for p in self.points) / len(self.points),
        )
        self.raw_properties = (
            {"plan_domain": {"materialization": "nested-insert-affine"}}
            if materialized else {}
        )


def _polyline(x0, y0, offsets):
    return [(x0 + x, y0 + y) for x, y in offsets]


def test_translated_duplicate_route_pairs_matches_exact_copy() -> None:
    main = _FakeEntity(
        "main", "FO 48 CORE", _polyline(10_000, 5_000, [(0, 0), (10, 5), (30, -5)]),
        native_length=50.0,
    )
    sub = _FakeEntity(
        "sub", "FO 48 CORE", _polyline(10_900, 5_000, [(0, 0), (10, 5), (30, -5)]),
        native_length=50.0,
    )
    pairs, records = spatial_filter._translated_duplicate_route_pairs([main, sub])
    assert pairs == [(main, sub, 0.0)]
    assert records[0]["translation_dx_m"] == 900.0


def test_topology_subdrawing_keys_removes_only_unanchored_copy() -> None:
    main = _FakeEntity(
        "main", "FO 48 CORE", _polyline(10_000, 5_000, [(0, 0), (10, 5), (30, -5)]),
        native_length=50.0,
    )
    sub = _FakeEntity(
        "sub", "FO 48 CORE", _polyline(10_900, 5_000, [(0, 0), (10, 5), (30, -5)]),
        native_length=50.0,
    )
    pole = _FakeEntity(
        "pole", "NEW POLE", [(10_010, 5_000)], dwg_type="INSERT",
    )
    keys, records = spatial_filter._topology_subdrawing_keys([main, sub], [pole])
    assert keys == frozenset({"sub"})
    assert records[0]["disposition"] == "subdrawing"


def test_topology_subdrawing_keys_fails_closed_when_both_anchored() -> None:
    main = _FakeEntity(
        "main", "FO 48 CORE", _polyline(10_000, 5_000, [(0, 0), (10, 5), (30, -5)]),
        native_length=50.0,
    )
    sub = _FakeEntity(
        "sub", "FO 48 CORE", _polyline(10_900, 5_000, [(0, 0), (10, 5), (30, -5)]),
        native_length=50.0,
    )
    poles = [
        _FakeEntity("pole-main", "NEW POLE", [(10_010, 5_000)], dwg_type="INSERT"),
        _FakeEntity("pole-sub", "NEW POLE", [(10_910, 5_000)], dwg_type="INSERT"),
    ]
    assert spatial_filter._topology_subdrawing_keys([main, sub], poles)[0] == frozenset()


def test_deployment_anchor_radius_uses_median_pole_spacing() -> None:
    anchors = [(0.0, 0.0), (10.0, 0.0), (30.0, 0.0), (60.0, 0.0)]
    radius = spatial_filter._deployment_anchor_radius(anchors)
    assert radius == 200.0  # median spacing is 20 -> 10x, above the 100 floor


def test_materialized_frame_outlier_rejects_frame_far_from_anchors() -> None:
    frame = _FakeEntity(
        "frame", "CLOSURE", [(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)],
        materialized=True,
    )
    anchors = [(10_000.0, 10_000.0), (10_020.0, 10_000.0)]
    radius = spatial_filter._deployment_anchor_radius(anchors)
    assert spatial_filter._materialized_frame_outlier(frame, anchors, radius) is True


def test_materialized_frame_outlier_keeps_frame_near_anchor() -> None:
    frame = _FakeEntity(
        "frame", "CLOSURE", [(10_010, 10_000), (10_012, 10_000),
                             (10_012, 10_002), (10_010, 10_002), (10_010, 10_000)],
        materialized=True,
    )
    anchors = [(10_000.0, 10_000.0), (10_020.0, 10_000.0)]
    radius = spatial_filter._deployment_anchor_radius(anchors)
    assert spatial_filter._materialized_frame_outlier(frame, anchors, radius) is False
