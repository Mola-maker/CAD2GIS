from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.iteration import (
    IterationError,
    decide_iteration_candidate,
    evaluate_iteration_candidate,
    export_iteration_learning,
    inspect_iteration,
    learning_context_for_bundle,
    prepare_iteration_context,
    record_iteration_feedback,
    start_feedback_iteration,
)


SOURCE_SHA256 = "a" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run(
    root: Path,
    name: str,
    *,
    status: str = "CONDITIONAL",
    unresolved: int = 3,
    source_entities: int = 10,
    passing: bool = True,
    visual: bool = False,
) -> Path:
    run = root / name
    run.mkdir()
    artifacts: dict[str, object] = {}
    if visual:
        render = run / "reasoning" / "visual" / "overview.png"
        render.parent.mkdir(parents=True)
        render.write_bytes(b"\x89PNG\r\n\x1a\nfeedback-render")
        visual_manifest = render.parent / "manifest.json"
        _write_json(
            visual_manifest,
            {
                "schema_version": "cad2gis.visual_evidence.v1",
                "regions": [
                    {
                        "region_id": "overview",
                        "render_path": "reasoning/visual/overview.png",
                        "render_sha256": _sha256(render),
                        "authority": "secondary_visual_evidence_only",
                        "visible_entity_count": 10,
                    }
                ],
            },
        )
        artifacts["visual_evidence"] = {
            "path": "reasoning/visual/manifest.json",
            "sha256": _sha256(visual_manifest),
        }
    _write_json(
        run / "run_manifest.json",
        {
            "schema_version": "cad2gis-run-manifest-v4",
            "run_status": status,
            "source": {"path": "drawing.dwg", "sha256": SOURCE_SHA256},
            "source_entity_count": source_entities,
            "unresolved_count": unresolved,
            "delivery_counts": {"CABLE": 2},
            "artifacts": artifacts,
            "validation": {
                "source_geometry": {"passed": passing},
                "topology": {"passed": passing},
                "segment_delivery": {"passed": passing},
            },
        },
    )
    return run


def test_feedback_iteration_accepts_only_reviewed_non_regressing_candidate(
    tmp_path: Path,
) -> None:
    base = _run(tmp_path, "run-base", visual=True)
    candidate = _run(tmp_path, "run-candidate", unresolved=1)
    session_dir = tmp_path / "iteration"
    started = start_feedback_iteration(
        base,
        session_dir=session_dir,
        max_iterations=3,
    )
    session_path = Path(started["session_path"])
    user_image = tmp_path / "annotated.png"
    user_image.write_bytes(b"\x89PNG\r\n\x1a\nuser-annotation")

    feedback = record_iteration_feedback(
        session_path,
        [
            {
                "category": "semantic_mapping",
                "severity": "major",
                "observation": "主干线被识别成了普通参考线。",
                "expected_outcome": "主干线应进入 CABLE，图框继续保留为文档实体。",
                "visual_refs": [
                    {
                        "kind": "run_region",
                        "artifact": "visual_evidence",
                        "region_id": "overview",
                    },
                    {
                        "kind": "user_image",
                        "path": str(user_image),
                        "description": "红框标出了应识别的主干线。",
                    },
                ],
            }
        ],
    )
    feedback_id = feedback["recorded_feedback_ids"][0]
    context = prepare_iteration_context(session_path)
    assert context["context"]["routes"][0]["category"] == "semantic_mapping"
    assert context["context"]["loop_contract"]["source_geometry_writable"] is False
    repeated_context = prepare_iteration_context(session_path)
    assert repeated_context["context_path"] == context["context_path"]

    changed = tmp_path / "mapping-registry.json"
    _write_json(changed, {"reviewed": True})
    evaluated = evaluate_iteration_candidate(
        session_path,
        candidate,
        addressed_feedback_ids=[feedback_id],
        change_summary="使用已观察到的图层 ID 更新语义映射，并生成新的 run。",
        changed_artifacts=[changed],
    )
    candidate_id = evaluated["evaluation"]["candidate_id"]
    assert evaluated["evaluation"]["comparison"]["eligible_for_acceptance"] is True
    assert evaluated["evaluation"]["comparison"]["automatic_promotion"] is False

    with pytest.raises(IterationError, match="user_confirmed"):
        decide_iteration_candidate(
            session_path,
            candidate_id,
            verdict="accept",
            rationale="视觉与语言证据均已满足。",
        )

    accepted = decide_iteration_candidate(
        session_path,
        candidate_id,
        verdict="accept",
        rationale="用户确认新版视觉结果符合预期。",
        user_confirmed=True,
    )
    assert accepted["session"]["status"] == "accepted"
    assert accepted["learning"]["lesson_id"].startswith("lesson_")
    assert inspect_iteration(session_path)["feedback"]["resolved"] == 1

    registry = tmp_path / "project" / "iteration-learning.json"
    exported = export_iteration_learning(session_path, registry)
    assert exported["lesson_count"] == 1
    context_value = learning_context_for_bundle(
        registry,
        {"source": {"sha256": SOURCE_SHA256}},
    )
    assert context_value["mode"] == "source_bound_suggestions_only"
    assert context_value["automatic_application"] is False


def test_iteration_rejects_candidate_with_deterministic_regression(
    tmp_path: Path,
) -> None:
    base = _run(tmp_path, "base", unresolved=1)
    candidate = _run(
        tmp_path,
        "candidate",
        status="UNSAFE",
        unresolved=4,
        passing=False,
    )
    started = start_feedback_iteration(
        base,
        session_dir=tmp_path / "iteration",
        max_iterations=1,
    )
    session_path = started["session_path"]
    recorded = record_iteration_feedback(
        session_path,
        [
            {
                "category": "network_topology",
                "observation": "连接关系仍不正确。",
                "expected_outcome": "只连接证据明确的端点候选。",
            }
        ],
    )
    evaluated = evaluate_iteration_candidate(
        session_path,
        candidate,
        addressed_feedback_ids=recorded["recorded_feedback_ids"],
        change_summary="尝试了端点候选。",
    )
    candidate_id = evaluated["evaluation"]["candidate_id"]
    comparison = evaluated["evaluation"]["comparison"]
    assert comparison["eligible_for_acceptance"] is False
    assert any("run_status regressed" in item for item in comparison["regressions"])

    with pytest.raises(IterationError, match="deterministic regressions"):
        decide_iteration_candidate(
            session_path,
            candidate_id,
            verdict="accept",
            rationale="不应接受。",
            user_confirmed=True,
        )
    rejected = decide_iteration_candidate(
        session_path,
        candidate_id,
        verdict="reject",
        rationale="候选破坏了既有验证 gate。",
    )
    assert rejected["session"]["status"] == "exhausted"


def test_iteration_session_detects_tampering(tmp_path: Path) -> None:
    base = _run(tmp_path, "base")
    started = start_feedback_iteration(base, session_dir=tmp_path / "iteration")
    session_path = Path(started["session_path"])
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["status"] = "accepted"
    _write_json(session_path, payload)

    with pytest.raises(IterationError, match="digest mismatch"):
        inspect_iteration(session_path)
