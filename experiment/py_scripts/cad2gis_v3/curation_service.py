"""Application service joining the review domain to a model-provider port."""

from __future__ import annotations

import json
from typing import Any

from .curation import (
    CurationError,
    CurationProposal,
    ReviewBundle,
    create_audit,
    proposal_json_schema,
    validate_proposal,
)
from .curation_providers import ReviewProvider, ReviewRequest
from .curation_provenance import offline_curation_provenance


SYSTEM_PROMPT = (
    "You are a proposal-only CAD evidence reviewer. Return exactly one valid JSON "
    "object matching the supplied task-bound proposal schema. Select or rank only "
    "candidate_ids and evidence_ids already present in this task, or abstain. The "
    "immutable measurements and CAD facts are read-only evidence: never create, "
    "estimate, correct, copy into rationale, or rewrite coordinates, geometry, CRS, "
    "lengths, SPAN measurements, labels, IDs, layers, attributes, or topology. "
    "Do not return Markdown or explanatory text outside the JSON object."
)


def _task_context(bundle: ReviewBundle, task_id: str) -> dict[str, Any]:
    task_map = {task["task_id"]: task for task in bundle.tasks}
    if task_id not in task_map:
        raise CurationError(f"Unknown review task ID: {task_id}")
    task = task_map[task_id]
    evidence_ids = set(task["evidence_ids"])
    candidate_ids = set(task["candidate_ids"])
    objects = [
        item for item in bundle.payload["objects"]
        if item["evidence_id"] in evidence_ids
    ]
    candidates = [
        item for item in bundle.payload["candidates"]
        if item["candidate_id"] in candidate_ids
    ]
    if {item["evidence_id"] for item in objects} != evidence_ids:
        raise CurationError("Review task references missing immutable evidence")
    if {item["candidate_id"] for item in candidates} != candidate_ids:
        raise CurationError("Review task references missing existing candidates")
    return {
        "schema_version": bundle.payload["schema_version"],
        "bundle_sha256": bundle.bundle_sha256,
        "source_sha256": bundle.source_sha256,
        "evidence_sha256": bundle.evidence_sha256,
        "policy": bundle.payload["policy"],
        "task": task,
        "immutable_objects": objects,
        "existing_candidates": candidates,
    }


def build_review_request(bundle: ReviewBundle, task_id: str) -> ReviewRequest:
    """Build a coordinate-free, task-bound request for any provider adapter."""

    return ReviewRequest(
        system_prompt=SYSTEM_PROMPT,
        context=_task_context(bundle, task_id),
        json_schema=proposal_json_schema(bundle, task_id),
    )


def review_task(
    bundle: ReviewBundle,
    task_id: str,
    provider: ReviewProvider,
) -> tuple[CurationProposal, dict[str, Any]]:
    """Run one provider review and make local validation the final authority."""

    response = provider.review(build_review_request(bundle, task_id))
    try:
        proposal_payload = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise CurationError("LLM provider message is not a JSON object") from exc
    if not isinstance(proposal_payload, dict):
        raise CurationError("LLM provider proposal root must be an object")
    proposal = validate_proposal(proposal_payload, bundle)
    audit = create_audit(
        proposal,
        implementation=offline_curation_provenance(),
        channel={
            "kind": "cloud",
            "provider": response.provider,
            "protocol": response.protocol,
            "model": response.model,
            "capability": response.capability,
            "base_url_profile_sha256": response.base_url_profile_sha256,
            "task_id": task_id,
            "request_sha256": response.request_sha256,
            "response_sha256": response.response_sha256,
            "response_id": response.response_id,
        },
    )
    return proposal, audit
