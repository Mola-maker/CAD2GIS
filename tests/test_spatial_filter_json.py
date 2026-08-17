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
