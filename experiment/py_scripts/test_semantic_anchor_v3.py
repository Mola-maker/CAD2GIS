"""Focused contracts for the coordinate-free semantic-anchor boundary."""

from __future__ import annotations

import hashlib
import json

import pytest

from cad2gis_v3.semantic_anchor import (
    Anchor,
    BINDING_SCHEMA_VERSION,
    GCPBinding,
    SemanticAnchorError,
    TargetCandidate,
    build_anchor,
    build_model_context,
    create_human_approval,
    create_target_candidate,
    export_gcp_binding,
    validate_gcp_binding,
    validate_model_decision,
)


SOURCE_SHA = "a" * 64


def _source():
    return {
        "source_sha256": SOURCE_SHA,
        "entity_key": "entity-1",
        "handle": "A01",
        # Native coordinates are facts for a deterministic reader, not anchor
        # fields.  They must never appear in model context.
        "native_points": [[10.0, 20.0]],
        "evidence_ids": ["source-evidence"],
    }


def _feature():
    return {
        "label": "P-001",
        "feature_class": "PTECH",
        "layer": "POLES",
        "evidence_ids": ["feature-evidence"],
    }


def _relations():
    return [
        {
            "relation_key": "r-2",
            "source_entity_key": "entity-1",
            "target_entity_key": "entity-3",
            "target_label": "P-003",
            "branch_angle": 45.0,
            "branch_length": 15.0,
            "evidence_id": "relation-2",
        },
        {
            "relation_key": "r-1",
            "source_entity_key": "entity-2",
            "target_entity_key": "entity-1",
            "source_label": "P-002",
            "angle_length_ratio": 2.0,
            "evidence_ids": ["relation-1"],
        },
        {
            "relation_key": "r-3",
            "source_entity_key": "entity-1",
            "target_entity_key": "entity-4",
            "adjacent_label": "P-004",
            "branch_angle": 90.0,
            "branch_length": 30.0,
        },
    ]


def test_anchor_is_stable_and_contains_only_semantic_summaries():
    first = build_anchor(_source(), _feature(), _relations())
    second = build_anchor(_source(), _feature(), list(reversed(_relations())))

    assert isinstance(first, Anchor)
    assert first == second
    assert first.source_sha256 == SOURCE_SHA
    assert first.entity_key == "entity-1"
    assert first.handle == "A01"
    assert first.label == "P-001"
    assert first.type == "PTECH"
    assert first.layer == "POLES"
    assert first.topology_degree == 3
    assert set(first.branch_angle_length_ratios) == {2.0, 3.0, 2.0}
    assert first.adjacent_labels == ("P-002", "P-003", "P-004")
    assert first.evidence_ids == (
        "entity-1",
        "feature-evidence",
        "relation-1",
        "relation-2",
        "source-evidence",
    )
    context = first.to_model_context()
    visible = json.dumps(context, sort_keys=True)
    assert "native_points" not in visible
    assert "10.0" not in visible
    assert context["anchor_id"] == first.anchor_id
    assert context["facts_sha256"] == first.facts_sha256
    assert first.anchor_id == second.anchor_id
    changed_facts = build_anchor(
        _source(),
        {**_feature(), "label": "P-999", "adjacent_labels": ["P-888"]},
        [
            {**_relations()[0], "target_label": "P-303"},
            *_relations()[1:],
        ],
    )
    assert changed_facts.anchor_id == first.anchor_id
    assert changed_facts.facts_sha256 != first.facts_sha256
    assert changed_facts.anchor_facts_sha256 == changed_facts.facts_sha256
    changed_identity = build_anchor(_source() | {"handle": "A02"}, _feature(), _relations())
    assert changed_identity.anchor_id != first.anchor_id


def test_model_context_rejects_coordinate_geometry_and_crs_keys():
    anchor = build_anchor(_source(), _feature(), _relations())
    with pytest.raises(SemanticAnchorError, match="Forbidden spatial key"):
        build_model_context(anchor, [], extra={"coordinate": [1.0, 2.0]})
    with pytest.raises(SemanticAnchorError, match="Forbidden spatial key"):
        build_model_context(anchor, [], extra={"geometry_kind": "Point"})
    with pytest.raises(SemanticAnchorError, match="Forbidden spatial key"):
        build_model_context(anchor, [], extra={"CRS": "EPSG:4326"})


def test_external_target_candidate_is_content_addressed():
    facts = {
        "target_id": "survey-1",
        "label": "P-001",
        "type": "PTECH",
        "evidence_ids": ["survey-evidence"],
    }
    candidate = create_target_candidate(facts)
    expected = hashlib.sha256(
        json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert candidate.candidate_id == expected
    assert create_target_candidate(dict(reversed(list(facts.items())))).candidate_id == expected
    changed = create_target_candidate({**facts, "label": "P-002"})
    assert changed.candidate_id != candidate.candidate_id
    assert candidate.to_model_context()["candidate_id"] == candidate.candidate_id


@pytest.mark.parametrize("field", ["geometry", "bbox", "shape"])
def test_candidate_ir_rejects_spatial_facts_before_serialization(field):
    with pytest.raises(
        SemanticAnchorError,
        match="Unknown or non-semantic|Forbidden spatial key",
    ):
        create_target_candidate({
            "target_id": "survey-1",
            "label": "P-001",
            field: [1, 2, 3, 4],
        })


def test_direct_candidate_construction_cannot_bypass_content_addressing():
    facts = {"target_id": "survey-1", "label": "P-001"}
    with pytest.raises(SemanticAnchorError, match="content-addressed"):
        TargetCandidate(candidate_id="forged", target_id="survey-1", facts=facts)


def test_decision_is_limited_to_existing_ids_and_source_hash():
    anchor = build_anchor(_source(), _feature(), _relations())
    candidate = create_target_candidate({
        "target_id": "survey-1",
        "label": "P-001",
        "evidence_ids": ["survey-evidence"],
    })
    selected = validate_model_decision(
        {
            "action": "select",
            "candidate_ids": [candidate.candidate_id],
            "source_sha256": SOURCE_SHA,
        },
        [candidate],
        anchor.source_sha256,
    )
    assert selected.candidate_ids == (candidate.candidate_id,)
    other = create_target_candidate({
        "target_id": "survey-2",
        "label": "P-002",
    })
    ranked = validate_model_decision(
        {
            "action": "rank",
            "candidate_ids": [candidate.candidate_id, other.candidate_id],
            "source_sha256": SOURCE_SHA,
        },
        [candidate, other],
        SOURCE_SHA,
    )
    assert ranked.action == "rank"
    abstained = validate_model_decision(
        {"action": "abstain", "candidate_ids": [], "source_sha256": SOURCE_SHA},
        [candidate],
        SOURCE_SHA,
    )
    assert abstained.action == "abstain"
    with pytest.raises(SemanticAnchorError, match="unknown candidate"):
        validate_model_decision(
            {"action": "select", "candidate_ids": ["unknown"], "source_sha256": SOURCE_SHA},
            [candidate],
            SOURCE_SHA,
        )
    with pytest.raises(SemanticAnchorError, match="source hash"):
        validate_model_decision(
            {"action": "select", "candidate_ids": [candidate.candidate_id], "source_sha256": "b" * 64},
            [candidate],
            SOURCE_SHA,
        )


def test_human_approval_is_required_before_coordinate_free_binding_export():
    anchor = build_anchor(_source(), _feature(), _relations())
    candidate = create_target_candidate({
        "target_id": "survey-1",
        "label": "P-001",
        "evidence_ids": ["survey-evidence"],
    })
    decision = {
        "action": "select",
        "candidate_ids": [candidate.candidate_id],
        "source_sha256": SOURCE_SHA,
        "anchor_id": anchor.anchor_id,
        "entity_key": anchor.entity_key,
        "facts_sha256": anchor.facts_sha256,
    }
    approval = create_human_approval(
        "reviewer@example.test",
        "2026-07-19T08:30:00Z",
        SOURCE_SHA,
        candidate.candidate_id,
        anchor=anchor,
    )
    binding = export_gcp_binding(anchor, [candidate], decision, approval)
    assert binding.source_id == "entity-1"
    assert binding.target_id == "survey-1"
    assert binding.candidate_id == candidate.candidate_id
    assert set(binding.evidence_ids) == {
        "entity-1", "feature-evidence", "relation-1", "relation-2",
        "source-evidence", "survey-evidence",
    }
    serialized = binding.to_dict()
    assert set(serialized) == {
        "schema_version", "source_id", "target_id", "candidate_id", "evidence_ids",
    }
    assert serialized["schema_version"] == BINDING_SCHEMA_VERSION
    assert validate_gcp_binding(serialized) == binding
    assert GCPBinding.from_dict(serialized).to_dict() == serialized
    assert not any(
        forbidden in json.dumps(serialized).lower()
        for forbidden in ("coordinate", "geometry", "crs", "easting", "northing")
    )
    with pytest.raises(SemanticAnchorError, match="Missing|Approval"):
        export_gcp_binding(anchor, [candidate], decision, {
            **approval.to_dict(), "candidate_id": "unknown",
        })
    with pytest.raises(SemanticAnchorError, match="source hash"):
        export_gcp_binding(anchor, [candidate], decision, {
            **approval.to_dict(), "source_sha256": "b" * 64,
        })
    with pytest.raises(SemanticAnchorError, match="requires a selected"):
        export_gcp_binding(
            anchor,
            [candidate],
            {
                "action": "abstain",
                "candidate_ids": [],
                "source_sha256": SOURCE_SHA,
                "anchor_id": anchor.anchor_id,
                "entity_key": anchor.entity_key,
                "facts_sha256": anchor.facts_sha256,
            },
            approval,
        )


def test_decision_and_approval_cannot_replay_on_another_anchor_same_drawing():
    anchor = build_anchor(_source(), _feature(), _relations())
    replay_anchor = build_anchor(
        {**_source(), "entity_key": "entity-2", "handle": "A02"},
        _feature(),
        _relations(),
    )
    candidate = create_target_candidate({"target_id": "survey-1", "label": "P-001"})
    decision = {
        "action": "select",
        "candidate_ids": [candidate.candidate_id],
        "source_sha256": SOURCE_SHA,
        "anchor_id": anchor.anchor_id,
        "entity_key": anchor.entity_key,
        "facts_sha256": anchor.facts_sha256,
    }
    approval = create_human_approval(
        "reviewer@example.test",
        "2026-07-19T08:30:00Z",
        SOURCE_SHA,
        candidate.candidate_id,
        anchor=anchor,
    )
    # Same DWG hash and target candidate are not sufficient: the exact source
    # entity and complete anchor facts must match the human decision.
    with pytest.raises(SemanticAnchorError, match="anchor_id|entity_key|facts hash"):
        export_gcp_binding(replay_anchor, [candidate], decision, approval)


def test_binding_schema_version_is_required_and_fail_closed():
    anchor = build_anchor(_source(), _feature(), _relations())
    candidate = create_target_candidate({"target_id": "survey-1", "label": "P-001"})
    decision = {
        "action": "select",
        "candidate_ids": [candidate.candidate_id],
        "source_sha256": SOURCE_SHA,
        "anchor_id": anchor.anchor_id,
        "entity_key": anchor.entity_key,
        "facts_sha256": anchor.facts_sha256,
    }
    approval = create_human_approval(
        "reviewer@example.test",
        "2026-07-19T08:30:00Z",
        SOURCE_SHA,
        candidate.candidate_id,
        anchor=anchor,
    )
    payload = export_gcp_binding(anchor, [candidate], decision, approval).to_dict()
    payload["schema_version"] = "cad2gis.gcp_binding.v0"
    with pytest.raises(SemanticAnchorError, match="schema_version"):
        GCPBinding.from_dict(payload)
    payload = export_gcp_binding(anchor, [candidate], decision, approval).to_dict()
    del payload["schema_version"]
    with pytest.raises(SemanticAnchorError, match="fields mismatch"):
        validate_gcp_binding(payload)
