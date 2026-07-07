"""Context-aware classification tests (story G8b) — the hit-vector resolves opaque gc* codes.

Layer + block-code alone leaves the gc* INSERTs unmapped. The nearest-TEXT evidence gate is
what separates gc170 (duct symbol, next to 3孔PVC110) from gc043 (paving symbol, next to 地砖).
These tests lock that behavior in against the real probe evidence (build/unmapped_probe.json)
WITHOUT needing the 68MB real DXF — FeatureContext is constructed directly.
"""
from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from cad2gis.evidence import collect_unmapped_evidence  # noqa: E402
from cad2gis.feature_context import FeatureContext  # noqa: E402
from cad2gis.mapping import MappingEngine  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    return MappingEngine.from_yaml()


def _ctx(block, layer="通信", text=None, etype="INSERT"):
    return FeatureContext(layer=layer, block=block, entity_type=etype, nearest_text=text)


def _ctx_multi(block, candidates, attribs=None, layer="ZBTZ"):
    # candidates: list of (text, dist) — ranked kNN nearest labels
    return FeatureContext(
        layer=layer, block=block, entity_type="INSERT",
        nearest_text=candidates[0][0] if candidates else None,
        text_candidates=list(candidates), attrib_text=list(attribs or []),
    )


def test_named_well_block_still_resolves_via_rule(engine):
    # A well block resolves through the deterministic rule path, not the block-code table.
    r = engine.classify_context(_ctx("末端井"))
    assert r.feature_class == "manhole"
    assert r.evidence.get("path") == "rule"


def test_opaque_duct_symbol_resolves_with_text_gate(engine):
    # gc170 next to a duct-hole label -> duct (the highest-leverage real-data win).
    r = engine.classify_context(_ctx("gc170", text="3孔PVC110"))
    assert r.feature_class == "duct"
    assert r.evidence.get("path") == "block_code"
    assert r.evidence.get("decision") == "mapped"


def test_opaque_duct_symbol_without_duct_text_is_gated_out(engine):
    # Same gc170 but nearest label is paving -> evidence gate fails -> stays unmapped.
    r = engine.classify_context(_ctx("gc170", text="地砖"))
    assert r.feature_class is None
    assert r.evidence.get("decision") == "gate_failed"


def test_paving_symbol_is_explicitly_rejected(engine):
    # gc043 is reviewed non-comms -> rejected regardless of text, never becomes a comms feature.
    r = engine.classify_context(_ctx("gc043", text="3孔PVC110"))
    assert r.feature_class is None
    assert r.evidence.get("decision") == "rejected"


def test_unknown_opaque_code_stays_unmapped(engine):
    r = engine.classify_context(_ctx("gc999", text="whatever"))
    assert r.feature_class is None
    assert r.evidence.get("path") != "block_code"


def test_evidence_package_groups_and_ranks(engine):
    # Two paving instances + one gated-out duct -> grouped, biggest population first.
    contexts = [
        _ctx("gc043", text="地砖"),
        _ctx("gc043", text="水泥"),
        _ctx("gc170", text="地砖"),  # gated out
    ]
    rows = collect_unmapped_evidence(contexts, engine)
    assert rows[0]["block"] == "gc043" and rows[0]["count"] == 2
    assert any(r["block"] == "gc170" for r in rows)
    # reason strings are populated so a reviewer knows WHY each group is unmapped
    assert all(r["reason"] for r in rows)


def test_knn_picks_duct_label_even_when_paving_is_nearer(engine):
    # A paving label is spatially closer, but a duct-hole label exists in the top-k -> duct.
    ctx = _ctx_multi("gc170", [("地砖", 0.4), ("3孔PVC110", 1.2)])
    r = engine.classify_context(ctx)
    assert r.feature_class == "duct"
    assert r.evidence.get("matched_label") == "3孔PVC110"


def test_attrib_text_is_authoritative_over_nearby_labels(engine):
    # The INSERT's own ATTRIB carries the duct spec -> confirms duct even if nearby text is paving.
    ctx = _ctx_multi("gc170", [("地砖", 0.3)], attribs=["6孔PVC110"])
    r = engine.classify_context(ctx)
    assert r.feature_class == "duct"
    assert r.evidence.get("matched_label") == "6孔PVC110"


def test_gate_fails_when_only_paving_labels_present(engine):
    # No duct-hole label anywhere in the pool -> gated out (stays unmapped).
    ctx = _ctx_multi("gc170", [("地砖", 0.3), ("水泥", 0.9), ("坝", 1.1)])
    r = engine.classify_context(ctx)
    assert r.feature_class is None
    assert r.evidence.get("decision") == "gate_failed"


def test_reject_code_never_leaks_even_with_strong_duct_evidence(engine):
    # ADVERSARIAL GUARANTEE: a reviewed reject code (facility:null) must stay rejected even when
    # given the strongest possible duct evidence (duct nearest-text + duct ATTRIB). The
    # `facility is None` short-circuit precedes the mapping gate, so no paving symbol can ever
    # become a comms facility. This locks in the deterministic negative-knowledge guarantee.
    reject_codes = [c for c, bc in engine.block_codes.items() if bc.facility is None]
    assert reject_codes, "expected reviewed reject codes in block_codes.yaml"
    for code in reject_codes:
        ctx = _ctx_multi(code, [("3孔PVC110", 0.1), ("12孔PVC110", 0.2)], attribs=["6孔PVC110"])
        r = engine.classify_context(ctx)
        assert r.feature_class is None, f"paving code {code} leaked to {r.feature_class}"
        assert r.evidence.get("decision") == "rejected"
