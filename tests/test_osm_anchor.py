"""Fail-closed contracts for relative OpenStreetMap location hints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cad2gis.cad2gis_v3 import osm_anchor  # noqa: E402


def test_derived_osm_anchor_is_relative_candidate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        osm_anchor,
        "query_osm_place",
        lambda _name: {
            "display_name": "Kletek, Sidoarjo",
            "lat": -7.35,
            "lon": 112.70,
            "epsg3857_centre": (12_544_000.0, -820_000.0),
            "bbox": {"south": -7.4, "north": -7.3, "west": 112.6, "east": 112.8},
        },
    )

    candidate = osm_anchor.derive_osm_anchor(
        "APD - KLETEK RW 05 SIDOARJO.dwg",
        (0.0, 0.0, 100.0, 100.0),
    )

    assert candidate["status"] == "candidate"
    assert candidate["authority"] == "relative_only"
    assert candidate["applicable_for_delivery"] is False
    assert candidate["refinement"] == "surveyed_gcp_required"
    assert candidate["bbox"]["west"] == 112.6


def test_osm_translation_requires_explicit_non_delivery_preview():
    candidate = {"translation_dx": 10.0, "translation_dy": -5.0}

    with pytest.raises(ValueError, match="relative-only review candidates"):
        osm_anchor.apply_osm_anchor((1.0, 2.0), candidate)

    assert osm_anchor.apply_osm_anchor(
        (1.0, 2.0), candidate, allow_relative_preview=True,
    ) == [11.0, -3.0]


def test_top_k_road_match_scores_joint_evidence_but_stays_review_only():
    source = [[(0.0, 0.0), (50.0, 0.0), (100.0, 20.0)]]
    roads = [
        {
            "osm_way_id": 10,
            "name": "matching road",
            "highway": "residential",
            "points": [(1_000.0, 2_000.0), (1_050.0, 2_000.0), (1_100.0, 2_020.0)],
            "endpoint_degree": 0,
        },
        {
            "osm_way_id": 20,
            "name": "cross road",
            "highway": "primary",
            "points": [(1_000.0, 2_000.0), (1_000.0, 2_250.0)],
            "endpoint_degree": 4,
        },
    ]

    result = osm_anchor.rank_osm_road_candidates(source, roads, top_k=2)

    assert result["status"] == "review_candidate"
    assert result["top_k"][0]["osm_way_id"] == 10
    assert result["top_k"][0]["score_components"].keys() == {
        "direction", "length", "shape", "topology", "coverage",
    }
    assert result["authority"] == "relative_only"
    assert result["applicable_for_delivery"] is False
    assert result["approval_required"] == "surveyed_gcp_or_explicit_human_review"


def test_top_k_road_match_abstains_when_best_candidates_are_tied():
    source = [[(0.0, 0.0), (100.0, 0.0)]]
    roads = [
        {"osm_way_id": way_id, "points": [(0.0, y), (100.0, y)]}
        for way_id, y in ((1, 100.0), (2, 200.0))
    ]

    result = osm_anchor.rank_osm_road_candidates(source, roads, top_k=2)

    assert result["status"] == "abstained"
    assert result["confidence_gap"] == 0.0
    assert len(result["top_k"]) == 2
    assert all(item["applicable_for_delivery"] is False for item in result["top_k"])
