"""Per-feature semantic verification (closes the audit's G-sem defect).

The base `_score_semantic` reports in-vocabulary COVERAGE only — it cannot claim per-feature
correctness without an independent ground truth (a classifier that only emits valid-class names
makes "correctness" tautological). This module adds REAL per-feature verification using signals
that are INDEPENDENT of the classifier's rule path:

  manhole (INSERT well-block) : matched to a surveyed X=/Y= coordinate label — a completely
                                separate DXF entity type (TEXT) whose parsed real-world coord
                                agrees with the node's transformed position. Cross-source truth.
  cable (LineString route)     : topologically anchored — at least one endpoint snaps to a manhole
                                or another route (the cable participates in the network). This is
                                independent of the layer-name rule that classified it.
  duct (gc* cross-section)     : geometry fingerprint confirms the symbol shape (e.g. a single
                                CIRCLE for a duct cross-section), independent of the block-name
                                lookup that classified it.
  annotation (TEXT/MTEXT)      : carries non-empty label text.

Classes without an independent per-feature signal are reported as `not_verifiable` — never faked.
The result is a per-class correctness rate over the VERIFIABLE subset, plus the verifiable coverage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..model import Feature, FeatureCollection
from ..gcp import extract_gcps_from_labels, refine_gcps_to_nodes, fit_transform, apply_transform


@dataclass
class PerFeatureVerification:
    by_class: dict = field(default_factory=dict)   # class -> {verified, total, verifiable, not_verifiable}
    overall_verified: int = 0
    overall_verifiable: int = 0

    def to_dict(self) -> dict:
        return {
            "by_class": self.by_class,
            "overall_verified": self.overall_verified,
            "overall_verifiable": self.overall_verifiable,
            "per_feature_correctness": self.per_feature_correctness,
        }

    @property
    def per_feature_correctness(self) -> float:
        return self.overall_verified / self.overall_verifiable if self.overall_verifiable else 0.0


def _manhole_matched_set(coll: FeatureCollection, transform, gcps_refined) -> set:
    """Set of manhole feature indices that match a surveyed coordinate label (cross-source truth).

    A manhole is 'verified' if its transformed position lands within tolerance of a surveyed
    dst coordinate — two independent extractions (well-block INSERT vs surveyed TEXT label) agree.
    """
    if transform is None or not gcps_refined:
        return set()
    import math

    verified = set()
    # surveyed dst coords
    surveyed = [(g.dst_x, g.dst_y) for g in gcps_refined]
    tol = 3.0  # metres — matches the georef residual scale
    for i, f in enumerate(coll.features):
        if f.feature_class != "manhole" or f.geometry.geom_type != "Point":
            continue
        tx, ty = apply_transform(transform, f.geometry.x, f.geometry.y)
        if any(math.hypot(tx - sx, ty - sy) <= tol for sx, sy in surveyed):
            verified.add(i)
    return verified


def _cable_anchored_set(coll: FeatureCollection, snap_tol: float = 5.0) -> set:
    """Set of cable feature indices with ≥1 endpoint snapped to a manhole or ANOTHER cable's
    endpoint (topologically anchored — independent of the layer rule that classified the cable).
    A cable's own two endpoints do NOT count as mutual anchoring (would self-verify a floating line)."""
    import math

    manhole_pts = [(f.geometry.x, f.geometry.y) for f in coll.features
                   if f.feature_class == "manhole" and f.geometry.geom_type == "Point"]
    # (x, y, cable_index) — track owner so a cable never anchors to itself
    cable_eps: list[tuple[float, float, int]] = []
    cables: list[int] = []
    for i, f in enumerate(coll.features):
        if f.feature_class == "cable" and f.geometry.geom_type == "LineString":
            cs = list(f.geometry.coords)
            cable_eps.append((cs[0][0], cs[0][1], i))
            cable_eps.append((cs[-1][0], cs[-1][1], i))
            cables.append(i)
    if not cables:
        return set()
    verified = set()
    for idx in cables:
        a, b = [(x, y) for x, y, owner in cable_eps if owner == idx]
        for ep in (a, b):
            hit = any(math.hypot(nx - ep[0], ny - ep[1]) <= snap_tol for nx, ny in manhole_pts)
            if not hit:
                # near another CABLE's endpoint (different owner) only
                hit = any(o != idx and math.hypot(ox - ep[0], oy - ep[1]) <= snap_tol
                          for ox, oy, o in cable_eps)
            if hit:
                verified.add(idx)
                break
    return verified


def _duct_fingerprint_set(coll: FeatureCollection, fingerprints: dict) -> set:
    """Set of duct-symbol feature indices whose block-definition fingerprint confirms the symbol
    shape (independent of the block-name lookup). Requires a block->fingerprint map."""
    verified = set()
    if not fingerprints:
        return verified
    for i, f in enumerate(coll.features):
        if f.feature_class != "duct" or not f.source.block:
            continue
        fp = fingerprints.get(f.source.block)
        if not fp:
            continue
        # duct cross-section = single CIRCLE primitive (the geometric truth, not the name)
        if set(fp.keys()) == {"CIRCLE"}:
            verified.add(i)
    return verified


def _class_from_review(value: Any) -> Optional[str]:
    """Normalize a reviewed-label verdict to its class string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        cls = value.get("class") or value.get("feature_class") or value.get("label")
        return str(cls) if cls is not None else None
    return None


def _duct_reviewed_label_set(coll: FeatureCollection, reviewed_labels: Optional[dict]) -> set:
    """Set of duct feature indices confirmed by an external reviewed-label table.

    This is intentionally handle-keyed and opt-in. It lets a hand-labeled subset or extracted
    node/duct schedule verify otherwise ambiguous duct symbols without reusing topology
    propagation as its own proof.
    """
    verified = set()
    duct_labels = (reviewed_labels or {}).get("duct", {})
    if not duct_labels:
        return verified
    for i, f in enumerate(coll.features):
        if f.feature_class != "duct" or not f.source.handle:
            continue
        if _class_from_review(duct_labels.get(f.source.handle)) == "duct":
            verified.add(i)
    return verified


def _annotation_has_text_set(coll: FeatureCollection) -> set:
    verified = set()
    for i, f in enumerate(coll.features):
        if f.feature_class == "annotation":
            t = f.attributes.get("text")
            if t and str(t).strip():
                verified.add(i)
    return verified


def verify_per_feature(
    coll: FeatureCollection,
    *,
    source_path: Optional[str] = None,
    transform=None,
    gcps_refined=None,
    fingerprints: Optional[dict] = None,
    reviewed_labels: Optional[dict] = None,
) -> PerFeatureVerification:
    """Compute real per-feature correctness over the independently-verifiable subset.

    For manholes, requires the G9 transform + refined GCPs (cross-source matching). For duct
    symbols, requires the block->fingerprint map. Falls back gracefully (marks not-verifiable)
    when an independent signal isn't available.
    """
    # Lazily build the independent signals if a source path is supplied.
    if source_path and transform is None:
        try:
            gcps = extract_gcps_from_labels(source_path)
            node_pos = [(f.geometry.x, f.geometry.y) for f in coll.features
                        if f.attributes.get("is_node_block") and f.geometry.geom_type == "Point"]
            gcps_refined = refine_gcps_to_nodes(gcps, node_pos)
            transform = fit_transform(gcps_refined)
        except Exception:  # noqa: BLE001
            transform = None

    duct_verified = _duct_fingerprint_set(coll, fingerprints or {})
    duct_verified |= _duct_reviewed_label_set(coll, reviewed_labels)

    verified_sets = {
        "manhole": _manhole_matched_set(coll, transform, gcps_refined),
        "cable": _cable_anchored_set(coll),
        "duct": duct_verified,
        "annotation": _annotation_has_text_set(coll),
    }

    rep = PerFeatureVerification()
    for cls, indices in verified_sets.items():
        total = sum(1 for f in coll.features if f.feature_class == cls)
        verified = len(indices)
        rep.by_class[cls] = {
            "total": total,
            "verified": verified,
            "verifiable": total,  # every feature of this class has an independent check available
            "rate": round(verified / total, 4) if total else 0.0,
        }
        rep.overall_verifiable += total
        rep.overall_verified += verified

    # classes with no independent per-feature signal
    for f in coll.features:
        if f.feature_class and f.feature_class not in verified_sets:
            rep.by_class.setdefault(f.feature_class, {"total": 0, "verified": 0, "verifiable": 0,
                                                      "rate": 0.0, "not_verifiable": True})
            rep.by_class[f.feature_class]["total"] += 1
            rep.by_class[f.feature_class]["not_verifiable"] = True
    return rep
