"""Unconverted-evidence package (story G6/G8b) — the audit trail for what did NOT convert.

A defensible ≥90% accuracy claim requires being honest about the remainder: every entity the
deterministic engine could not classify is collected here with the exact signals that were
available (block, layer, count, sample handles, nearest-text histogram, geometry fingerprint,
and the reason it was left unmapped). This package is:
  - a REVIEW input: an offline human (optionally AI-assisted) reads it and, when a code is
    genuinely a comms facility, adds a reviewed entry to block_codes.yaml (never at runtime);
  - a DELIVERABLE: it is what proves the pipeline loses nothing silently.

Grouped by (block, layer) because the real files repeat a handful of opaque codes thousands of
times — 20 grouped rows explain 800 unmapped entities.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UnmappedGroup:
    block: Optional[str]
    layer: Optional[str]
    entity_type: Optional[str]
    count: int
    sample_handles: list = field(default_factory=list)
    nearest_text_top: list = field(default_factory=list)  # [(text, n), ...] most common labels
    fingerprint: dict = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def collect_unmapped_evidence(contexts, engine, max_handles: int = 5, top_text: int = 5) -> list[dict]:
    """Run every FeatureContext through classify_context and package what stays unmapped.

    Returns grouped evidence rows (dicts), sorted by descending count — the biggest unexplained
    populations first, so review effort targets the highest-leverage codes.
    """
    groups: dict[tuple, list] = defaultdict(list)
    reasons: dict[tuple, str] = {}
    for ctx in contexts:
        r = engine.classify_context(ctx)
        if r.mapped:
            continue
        key = (ctx.block, ctx.layer, ctx.entity_type)
        groups[key].append(ctx)
        # keep the most specific reason we saw for this group
        ev = getattr(r, "evidence", {}) or {}
        if ev.get("decision") == "rejected":
            reasons[key] = f"reviewed non-comms: {ev.get('reason', '')}"
        elif ev.get("decision") == "gate_failed":
            reasons.setdefault(key, f"evidence gate failed (needs {ev.get('required')})")
        else:
            reasons.setdefault(key, "no rule and no reviewed block-code")

    rows: list[UnmappedGroup] = []
    for key, ctxs in groups.items():
        block, layer, etype = key
        text_counter: Counter = Counter()
        fp: dict = {}
        for c in ctxs:
            if c.nearest_text:
                text_counter[c.nearest_text] += 1
            if c.fingerprint and not fp:
                fp = dict(c.fingerprint)
        rows.append(
            UnmappedGroup(
                block=block,
                layer=layer,
                entity_type=etype,
                count=len(ctxs),
                sample_handles=[c.handle for c in ctxs[:max_handles] if c.handle],
                nearest_text_top=text_counter.most_common(top_text),
                fingerprint=fp,
                reason=reasons.get(key, ""),
            )
        )
    rows.sort(key=lambda g: -g.count)
    return [r.to_dict() for r in rows]


def write_evidence_json(rows: list[dict], path: str) -> None:
    import json
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "unmapped_groups": rows,
        "total_unmapped": sum(r["count"] for r in rows),
        "distinct_groups": len(rows),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
