"""Deterministic rules-first mapping engine (story G6).

Classifies a CAD entity (by layer name, block name, entity type, optional text) into a
GIS feature class using an ordered rule set loaded from `comms_symbols.yaml`. First
matching rule (by descending priority) wins. Unmatched entities return an unmapped
result so the caller can route them to the review file / unconverted-evidence package.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

_DEFAULT_DICT = os.path.join(os.path.dirname(__file__), "comms_symbols.yaml")
_DEFAULT_CODES = os.path.join(os.path.dirname(__file__), "block_codes.yaml")


@dataclass
class Rule:
    name: str
    geom: str
    priority: int
    layer_regex: Optional[re.Pattern] = None
    block_regex: Optional[re.Pattern] = None
    entity_types: Optional[set[str]] = None
    attributes: dict = field(default_factory=dict)


@dataclass
class BlockCode:
    """One reviewed opaque block-code -> facility decision (from block_codes.yaml).

    facility=None means the code is explicitly rejected (kept unmapped). requires_text, when
    set, is an evidence gate: the entity's nearest-text label must match it to confirm the
    mapping — this is what separates gc170 (duct, next to 孔PVC110) from a stray placement.
    """

    code: str
    facility: Optional[str]
    requires_text: Optional[re.Pattern] = None
    evidence: str = ""


# Paving / surface-restoration terms — a positive-facility block is VETOED if its only nearby
# labels are paving (Codex review #2/#3): 砖 tile, 地砖 paving-tile, 水泥/砼 concrete, 沥 asphalt,
# 坝 embankment, 空 empty. Prevents a duct symbol being confirmed by a coincidental duct label
# when the true context is a paved surface.
_PAVING_VETO = re.compile(r"地砖|砖|水泥|砼|沥|坝|空")


def _label_pool(ctx) -> list[str]:
    """Evidence-ordered label pool for one context: ATTRIB values first (strongest — the INSERT's
    own attributes), then ranked nearest TEXT candidates. Codex recommended ATTRIB-first."""
    pool: list[str] = []
    pool.extend(getattr(ctx, "attrib_text", None) or [])
    cands = getattr(ctx, "text_candidates", None)
    if cands:
        pool.extend(t for t, _d in cands)
    elif getattr(ctx, "nearest_text", None):
        pool.append(ctx.nearest_text)
    return pool


@dataclass
class MappingResult:
    feature_class: Optional[str]
    geom: Optional[str]
    confidence: float
    attributes: dict = field(default_factory=dict)
    rule: Optional[str] = None
    evidence: dict = field(default_factory=dict)  # audit trail: what signals fired (never scored)

    @property
    def mapped(self) -> bool:
        return self.feature_class is not None


class MappingEngine:
    def __init__(self, rules: list[Rule], block_codes: Optional[list[BlockCode]] = None):
        self.rules = sorted(rules, key=lambda r: -r.priority)
        # index reviewed opaque codes by lowercased code for O(1) lookup
        self.block_codes: dict[str, BlockCode] = {}
        for bc in block_codes or []:
            self.block_codes[bc.code.lower()] = bc
        # geom per facility name, taken from the first rule that produces it (for classify_context)
        self._geom_for: dict[str, str] = {}
        for r in self.rules:
            self._geom_for.setdefault(r.name, r.geom)

    @classmethod
    def from_yaml(cls, path: str = _DEFAULT_DICT, codes_path: Optional[str] = _DEFAULT_CODES) -> "MappingEngine":
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        rules: list[Rule] = []
        for c in data.get("classes", []):
            m = c.get("match", {})
            rules.append(
                Rule(
                    name=c["name"],
                    geom=c.get("geom", ""),
                    priority=int(c.get("priority", 0)),
                    layer_regex=re.compile(m["layer_regex"], re.IGNORECASE) if m.get("layer_regex") else None,
                    block_regex=re.compile(m["block_regex"], re.IGNORECASE) if m.get("block_regex") else None,
                    entity_types={e.upper() for e in m.get("entity_types", [])} or None,
                    attributes=dict(c.get("attributes", {})),
                )
            )
        block_codes = cls._load_block_codes(codes_path) if codes_path else []
        return cls(rules, block_codes)

    @staticmethod
    def _load_block_codes(path: str) -> list[BlockCode]:
        import yaml

        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        out: list[BlockCode] = []
        for c in data.get("codes", []):
            rt = c.get("requires_text")
            out.append(
                BlockCode(
                    code=str(c["code"]),
                    facility=c.get("facility"),  # None => explicit reject
                    requires_text=re.compile(rt, re.IGNORECASE) if rt else None,
                    evidence=str(c.get("evidence", "")),
                )
            )
        return out

    def classify(
        self,
        layer: Optional[str] = None,
        block: Optional[str] = None,
        entity_type: Optional[str] = None,
        text: Optional[str] = None,
    ) -> MappingResult:
        et = entity_type.upper() if entity_type else None
        for r in self.rules:
            # Entity-type gate (only when the rule constrains it AND we know the entity type).
            if r.entity_types and et and et not in r.entity_types:
                continue
            layer_hit = bool(r.layer_regex and layer and r.layer_regex.search(layer))
            block_hit = bool(r.block_regex and block and r.block_regex.search(block))

            # AND/OR semantics: when a rule constrains BOTH layer and block, an INSERT (block
            # reference) must satisfy the block pattern too — otherwise every opaque block on a
            # matching layer would be misclassified (e.g. duct/paving gc* symbols on 通信 becoming
            # manholes). Non-INSERT entities (raw geometry) match on layer alone.
            if r.layer_regex and r.block_regex and et == "INSERT":
                matched = layer_hit and block_hit
            else:
                matched = layer_hit or block_hit

            if matched:
                conf = 0.9 if (layer_hit and (not r.entity_types or (et and et in r.entity_types))) else 0.75
                if block_hit and layer_hit:
                    conf = min(1.0, conf + 0.05)
                return MappingResult(r.name, r.geom, conf, dict(r.attributes), r.name)
        return MappingResult(None, None, 0.0, {}, None)

    def classify_context(self, ctx) -> MappingResult:
        """Context-aware classification using the full hit vector (story G8b).

        Order of authority:
          1. Deterministic rules (layer+block regex) — the well blocks (末端井…) resolve here.
          2. If still unmapped AND the block is a reviewed opaque code, consult block_codes.yaml
             with the nearest-text evidence gate. This is what turns the 807 opaque gc* INSERTs
             into duct symbols (or keeps them out as paving) — the highest-leverage accuracy fix.

        `ctx` is a FeatureContext (duck-typed: needs .layer/.block/.entity_type/.nearest_text).
        Confidence is audit metadata only; the block-code path never claims >0.85 because it
        relies on a secondary (text-proximity) signal.
        """
        base = self.classify(
            layer=ctx.layer, block=ctx.block, entity_type=ctx.entity_type,
            text=getattr(ctx, "nearest_text", None),
        )
        if base.mapped:
            base.evidence = {"path": "rule", "rule": base.rule}
            return base

        code = (ctx.block or "").lower()
        bc = self.block_codes.get(code)
        if bc is None:
            return base  # genuinely unmapped -> routes to evidence package

        pool = _label_pool(ctx)
        winner = None
        if bc.requires_text is not None:
            winner = next((t for t in pool if bc.requires_text.search(t)), None)

        if bc.facility is None:
            # explicitly reviewed as non-comms (paving/surface) -> stay unmapped, but record why
            return MappingResult(
                None, None, 0.0, {}, None,
                evidence={"path": "block_code", "code": bc.code, "decision": "rejected",
                          "reason": bc.evidence},
            )
        if bc.requires_text is not None and winner is None:
            # evidence gate failed: reviewed as facility but no nearby label confirms -> unmapped
            return MappingResult(
                None, None, 0.0, {}, None,
                evidence={"path": "block_code", "code": bc.code, "decision": "gate_failed",
                          "labels": pool[:5], "required": bc.requires_text.pattern},
            )
        # Paving veto: even with a confirming label, reject if the pool is dominated by paving
        # terms and NO stronger duct label appears (guards against a coincidental duct note).
        if winner is None and pool and all(_PAVING_VETO.search(t) for t in pool):
            return MappingResult(
                None, None, 0.0, {}, None,
                evidence={"path": "block_code", "code": bc.code, "decision": "paving_veto",
                          "labels": pool[:5]},
            )
        geom = self._geom_for.get(bc.facility, "")
        return MappingResult(
            bc.facility, geom, 0.82,
            {"facility": bc.facility, "discipline": "comms", "resolved_by": "block_code"},
            rule=f"block_code:{bc.code}",
            evidence={"path": "block_code", "code": bc.code, "decision": "mapped",
                      "matched_label": winner, "evidence": bc.evidence},
        )
