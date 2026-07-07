"""Accuracy protocol — 5-dimension scoring of a conversion against labeled ground truth.

Dimensions (per the independent review):
  - semantic     : features assigned the correct feature class (coverage x correctness)
  - geometric    : geometries valid & non-empty
  - count        : per-class feature-count reconciliation vs expected
  - attribute    : required-field completeness (needs published schemas; G10)
  - network      : node-edge connectivity correctness (G8)
  - positional   : real-world placement / CRS residuals (G9)

Only *evaluated* dimensions contribute to the overall weighted score. Dimensions whose
supporting stage isn't built yet are marked `evaluated=False` and excluded — we never
fake a pass. The default pass threshold is 0.90 (the contest's data-continuity target).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..model import FeatureCollection, FeatureClassSchema

DEFAULT_THRESHOLD = 0.90

# Relative importance of each dimension; normalized over the dimensions actually evaluated.
DIMENSION_WEIGHTS = {
    "semantic": 0.30,
    "geometric": 0.25,
    "count": 0.20,
    "attribute": 0.15,
    "network": 0.05,
    "positional": 0.05,
}


@dataclass
class BenchmarkSpec:
    """Labeled ground truth for one drawing (the correct, post-cleaning answer)."""

    name: str
    expected_counts: dict[str, int] = field(default_factory=dict)
    crs_expected: Optional[str] = None
    connectivity: dict[str, Any] = field(default_factory=dict)
    tolerance: dict[str, float] = field(default_factory=dict)
    source: Optional[str] = None

    @classmethod
    def from_json(cls, path: str) -> "BenchmarkSpec":
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return cls(
            name=d.get("name", "benchmark"),
            expected_counts=d.get("expected_counts", {}),
            crs_expected=(d.get("crs") or {}).get("expected"),
            connectivity=d.get("connectivity", {}),
            tolerance=d.get("tolerance", {}),
            source=d.get("source"),
        )


@dataclass
class DimensionScore:
    name: str
    score: float  # 0..1
    evaluated: bool = True
    details: str = ""

    @property
    def passed(self) -> bool:
        return self.evaluated and self.score >= DEFAULT_THRESHOLD


@dataclass
class AccuracyReport:
    dimensions: list[DimensionScore]
    threshold: float = DEFAULT_THRESHOLD

    @property
    def overall(self) -> float:
        evald = [d for d in self.dimensions if d.evaluated]
        if not evald:
            return 0.0
        wsum = sum(DIMENSION_WEIGHTS.get(d.name, 0.0) for d in evald)
        if wsum == 0:
            return sum(d.score for d in evald) / len(evald)
        return sum(DIMENSION_WEIGHTS.get(d.name, 0.0) * d.score for d in evald) / wsum

    @property
    def passed(self) -> bool:
        return self.overall >= self.threshold

    def to_dict(self) -> dict:
        return {
            "overall": round(self.overall, 4),
            "passed": self.passed,
            "threshold": self.threshold,
            "dimensions": [
                {
                    "name": d.name,
                    "score": round(d.score, 4),
                    "evaluated": d.evaluated,
                    "details": d.details,
                }
                for d in self.dimensions
            ],
        }


# The valid comms feature-class taxonomy. A feature mapped to one of these is semantically
# "in-vocabulary"; this is SEPARATE from expected_counts (which only lists classes we have an
# independent count for). Conflating the two wrongly marks cable/duct as incorrect.
VALID_CLASSES = {"manhole", "cable", "duct", "annotation", "control_point",
                 "pole", "closure", "cabinet", "room"}


def _score_semantic(coll: FeatureCollection, bench: BenchmarkSpec,
                    valid_classes: Optional[set] = None) -> DimensionScore:
    """Semantic score = in-vocabulary COVERAGE (with evidenced abstention excluded).

    Honesty note (confirmed by adversarial audit): without a per-feature labeled ground truth we
    CANNOT measure per-feature class correctness — every mapped feature is, by construction, in the
    valid taxonomy, so a "correctness" factor would be a tautological 1.0. We therefore score this
    dimension as coverage alone and say so, rather than dressing coverage up as coverage×correctness.
    `in_vocab` is still tracked to assert the invariant (should equal `mapped`); it is not a score.
    """
    if len(coll) == 0:
        return DimensionScore("semantic", 0.0, details="empty collection")
    known = valid_classes or VALID_CLASSES
    mapped = in_vocab = abstained = 0
    for f in coll.features:
        fc = f.feature_class
        # Correct negatives: features we abstained on WITH positive evidence they are non-features
        # (noise fragments demoted from a route class, or reviewed-reject/paving symbols). These are
        # not classification failures, so they don't count against coverage (Codex abstention rule).
        ev = f.attributes.get("_map_evidence") or {}
        if f.attributes.get("_demoted_from") or ev.get("decision") in ("rejected", "paving_veto"):
            abstained += 1
            continue
        if fc and fc != "__unmapped__":
            mapped += 1
            if fc in known:
                in_vocab += 1
    denom = len(coll) - abstained
    coverage = (mapped / denom) if denom else 0.0
    oov = mapped - in_vocab  # out-of-vocabulary mapped features (should be 0 by construction)
    return DimensionScore(
        "semantic", coverage,
        details=(f"coverage={coverage:.2f} (in-vocabulary rate; per-feature correctness NOT "
                 f"independently verified — no labeled ground truth) abstained={abstained}"
                 + (f" OOV={oov}" if oov else "")),
    )


def _score_geometric(coll: FeatureCollection, bench: BenchmarkSpec) -> DimensionScore:
    if len(coll) == 0:
        return DimensionScore("geometric", 0.0, details="empty collection")
    valid = sum(1 for f in coll.features if f.is_valid)
    return DimensionScore("geometric", valid / len(coll), details=f"valid={valid}/{len(coll)}")


def _score_counts(coll: FeatureCollection, bench: BenchmarkSpec) -> DimensionScore:
    exp = bench.expected_counts
    if not exp:
        return DimensionScore("count", 0.0, evaluated=False, details="no expected_counts")
    actual = coll.counts_by_class()
    total_exp = sum(exp.values())
    err = sum(abs(actual.get(cls, 0) - n) for cls, n in exp.items())
    score = max(0.0, 1.0 - err / total_exp) if total_exp else 0.0
    seen = {k: v for k, v in actual.items() if k in exp}
    return DimensionScore("count", score, details=f"expected={exp} actual={seen} abs_err={err}")


def _effective_value(f, name):
    """Value of a schema field for scoring — provenance fields live on f.source, class fields on
    f.attributes. The warehouse writer maps these the same way, so the scorer must too (else it
    under-counts provenance that is actually present)."""
    prov = {
        "src_file": f.source.file, "src_layer": f.source.layer, "src_block": f.source.block,
        "src_handle": f.source.handle, "src_entity": f.source.entity_type,
        "confidence": f.confidence,
    }
    if name in prov:
        return prov[name]
    return f.attributes.get(name)


def _score_attribute(
    coll: FeatureCollection, bench: BenchmarkSpec,
    schemas: Optional[dict[str, FeatureClassSchema]],
) -> DimensionScore:
    # Requires per-class required-field definitions; published in G10. Until then, don't fake it.
    if not schemas:
        return DimensionScore("attribute", 0.0, evaluated=False, details="no schemas provided (G10)")
    total = filled = 0
    for f in coll.features:
        sch = schemas.get(f.feature_class or "")
        if not sch:
            continue
        for name in sch.required_fields():
            total += 1
            if _effective_value(f, name) not in (None, ""):
                filled += 1
    if total == 0:
        return DimensionScore("attribute", 0.0, evaluated=False, details="no required fields to check")
    return DimensionScore("attribute", filled / total, details=f"filled={filled}/{total}")


def score(
    coll: FeatureCollection,
    bench: BenchmarkSpec,
    schemas: Optional[dict[str, FeatureClassSchema]] = None,
    network_qc: Optional[dict] = None,
    georef: Optional[dict] = None,
) -> AccuracyReport:
    """Score *coll* against *bench*. Unbuilt/absent dimensions are marked not-evaluated.

    network_qc: optional dict from cad2gis.network.NetworkQC.to_dict(); when provided the
    network dimension is scored on connectivity_ratio instead of being skipped.
    georef: optional dict from cad2gis.gcp.TransformFit.to_dict(); when provided the positional
    dimension is scored from the georeference RMSE against the tolerance (default 3 m target).
    """
    if network_qc and network_qc.get("total_endpoints", 0) > 0:
        net_dim = DimensionScore(
            "network", float(network_qc["connectivity_ratio"]),
            details=f"dangling={network_qc.get('dangling_ends')} isolated={network_qc.get('isolated_nodes')}",
        )
    else:
        net_dim = DimensionScore("network", 0.0, evaluated=False, details="no network_qc supplied (G8)")

    # Positional: score from georef RMSE. A linear roll-off from 0 at rmse_target to 1 at rmse=0;
    # a sub-target RMSE scores near-full. Honest: only evaluated when a real transform exists.
    if georef and georef.get("n_gcps", 0) >= 3 and "rmse" in georef:
        rmse = float(georef["rmse"])
        target = float(bench.tolerance.get("rmse_m", 3.0))
        pos = max(0.0, 1.0 - rmse / (2 * target))  # rmse==target -> 0.5, rmse==0 -> 1.0
        pos_dim = DimensionScore("positional", pos, details=f"rmse={rmse:.2f}m target={target}m n={georef['n_gcps']}")
    else:
        pos_dim = DimensionScore("positional", 0.0, evaluated=False, details="no georef supplied (G9)")

    dims = [
        _score_semantic(coll, bench),
        _score_geometric(coll, bench),
        _score_counts(coll, bench),
        _score_attribute(coll, bench, schemas),
        net_dim,
        pos_dim,
    ]
    return AccuracyReport(dims)
