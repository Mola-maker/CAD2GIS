"""Independent CLI for proposal-only human/LLM curation."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .curation import (
    CurationError,
    build_review_bundle,
    create_audit,
    load_and_validate_proposal,
    load_review_bundle,
    proposal_json_schema,
    write_json_atomic,
)
from .curation_providers import (
    SUPPORTED_PROVIDERS,
    OpenAICompatibleProvider,
    ProviderError,
    load_provider_config,
)
from .curation_provenance import offline_curation_provenance
from .curation_service import review_task


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optional APD review curation; never part of offline conversion",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bundle = subparsers.add_parser(
        "bundle", help="Create a content-addressed review bundle from deterministic evidence",
    )
    bundle.add_argument("--evidence", required=True, type=Path)
    bundle.add_argument("--dwg", required=True, type=Path)
    bundle.add_argument("--out", required=True, type=Path)

    schema = subparsers.add_parser(
        "schema", help="Emit the strict proposal schema for one existing task",
    )
    schema.add_argument("--bundle", required=True, type=Path)
    schema.add_argument("--task-id", required=True)
    schema.add_argument("--out", required=True, type=Path)

    validate = subparsers.add_parser(
        "validate", help="Validate a human/provider proposal and write an audit artifact",
    )
    validate.add_argument("--bundle", required=True, type=Path)
    validate.add_argument("--proposal", required=True, type=Path)
    validate.add_argument("--out-audit", required=True, type=Path)

    cloud = subparsers.add_parser(
        "cloud", help="Review one task through the selected provider profile",
    )
    cloud.add_argument("--provider", choices=SUPPORTED_PROVIDERS)
    cloud.add_argument("--bundle", required=True, type=Path)
    cloud.add_argument("--task-id", required=True)
    cloud.add_argument("--out-proposal", required=True, type=Path)
    cloud.add_argument("--out-audit", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "bundle":
            bundle = build_review_bundle(args.evidence, args.dwg)
            output = write_json_atomic(args.out, bundle.to_dict())
            result = {
                "status": "success",
                "mode": "proposal_only",
                "bundle": str(output),
                "bundle_sha256": bundle.bundle_sha256,
                "source_sha256": bundle.source_sha256,
                "evidence_sha256": bundle.evidence_sha256,
                "objects": len(bundle.payload["objects"]),
                "candidates": len(bundle.payload["candidates"]),
                "tasks": len(bundle.payload["tasks"]),
            }
        elif args.command == "schema":
            bundle = load_review_bundle(args.bundle)
            schema = proposal_json_schema(bundle, args.task_id)
            output = write_json_atomic(args.out, schema)
            result = {
                "status": "success",
                "mode": "proposal_only",
                "schema": str(output),
                "task_id": args.task_id,
                "bundle_sha256": bundle.bundle_sha256,
            }
        elif args.command == "validate":
            bundle = load_review_bundle(args.bundle)
            proposal = load_and_validate_proposal(args.proposal, bundle)
            audit = create_audit(
                proposal, implementation=offline_curation_provenance(),
            )
            output = write_json_atomic(args.out_audit, audit)
            result = {
                "status": "success",
                "mode": "proposal_only",
                "audit": str(output),
                "audit_sha256": audit["audit_sha256"],
                "decisions": len(proposal.decisions),
            }
        else:
            bundle = load_review_bundle(args.bundle)
            config = load_provider_config(provider=args.provider)
            provider = OpenAICompatibleProvider(config)
            proposal, audit = review_task(bundle, args.task_id, provider)
            proposal_output = write_json_atomic(args.out_proposal, proposal.to_dict())
            audit_output = write_json_atomic(args.out_audit, audit)
            result = {
                "status": "success",
                "mode": "proposal_only",
                "proposal": str(proposal_output),
                "audit": str(audit_output),
                "audit_sha256": audit["audit_sha256"],
                "task_id": args.task_id,
                "provider": config.provider,
                "model": config.model,
                "capability": config.capability,
            }
    except (CurationError, ProviderError, OSError, sqlite3.Error) as exc:
        print(json.dumps({
            "status": "error",
            "mode": "proposal_only",
            "error": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
