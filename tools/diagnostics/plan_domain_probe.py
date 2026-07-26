"""Probe reader inventory and generic plan-domain materialization for one DWG."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.plan_domain import PlanDomainError, build_plan_domain
from cad2gis.reader.autocad import extract_dwg_records


def _summary(diagnostics: dict[str, Any]) -> dict[str, Any]:
    issues = list(diagnostics.get("issues", ()))
    return {
        key: value
        for key, value in diagnostics.items()
        if key != "issues"
    } | {
        "issue_count": len(issues),
        "issue_codes": dict(sorted(Counter(
            str(issue.get("code", "unknown")) for issue in issues
        ).items())),
        "issue_sample": issues[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    arguments = parser.parse_args()
    records = extract_dwg_records(arguments.source.resolve())
    entities = [SourceEntity.from_record(record) for record in records]
    result: dict[str, Any] = {
        "source": str(arguments.source.resolve()),
        "inventory_count": len(entities),
        "reader": dict(records.diagnostics),
    }
    try:
        view = build_plan_domain(entities)
    except PlanDomainError as exc:
        result["plan_domain"] = _summary(exc.diagnostics)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 2
    result["plan_domain"] = _summary(view.diagnostics)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
