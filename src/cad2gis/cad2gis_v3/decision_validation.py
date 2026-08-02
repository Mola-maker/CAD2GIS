"""Independent deterministic validators and fail-closed auto decisions."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .repair_decisions import DecisionPack, RepairOperation


VALIDATION_REPORT_SCHEMA = "cad2gis.decision_validation_report.v1"
AUTO_DECISION_SCHEMA = "cad2gis.auto_decision.v1"

Dimension = Literal["geometry", "topology", "length", "crs"]
Disposition = Literal["AUTO_ACCEPTED", "QUARANTINED", "REJECTED"]


class DecisionValidationError(ValueError):
    """Validation evidence is malformed, incomplete, or cross-boundary."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_sha256(value: Any, path: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise DecisionValidationError(f"{path} must be a lowercase SHA-256 digest")
    return text


def _finite(value: Any, path: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionValidationError(f"{path} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0.0):
        suffix = " non-negative" if nonnegative else ""
        raise DecisionValidationError(f"{path} must be a{suffix} finite number")
    return result


def _canonical(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DecisionValidationError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DecisionValidationError(f"{path} contains a non-string key")
            result[key] = _canonical(item, f"{path}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item, f"{path}[]") for item in value]
    raise DecisionValidationError(f"{path} contains unsupported type {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _validator_sha256(validator_id: str) -> str:
    return _sha256(f"cad2gis-decision-validator-v1:{validator_id}".encode("ascii"))


@dataclass(frozen=True)
class ValidationReport:
    report_sha256: str
    pack_sha256: str
    evidence_graph_sha256: str
    operation_id: str
    dimension: Dimension
    validator_id: str
    validator_sha256: str
    passed: bool
    findings: tuple[str, ...]
    _metrics_json: str

    @classmethod
    def create(
        cls,
        *,
        pack_sha256: str,
        evidence_graph_sha256: str,
        operation_id: str,
        dimension: Dimension,
        validator_id: str,
        passed: bool,
        findings: Iterable[str],
        metrics: Mapping[str, Any],
    ) -> "ValidationReport":
        pack = _require_sha256(pack_sha256, "pack_sha256")
        graph = _require_sha256(evidence_graph_sha256, "evidence_graph_sha256")
        if dimension not in {"geometry", "topology", "length", "crs"}:
            raise DecisionValidationError(f"Unsupported validation dimension: {dimension!r}")
        if not isinstance(operation_id, str) or not operation_id.startswith("rop_"):
            raise DecisionValidationError("operation_id must be a repair operation ID")
        if not isinstance(validator_id, str) or not validator_id.strip():
            raise DecisionValidationError("validator_id must be a non-empty string")
        finding_values = tuple(str(item) for item in findings)
        metrics_json = _canonical_json(metrics)
        validator_hash = _validator_sha256(validator_id)
        identity = {
            "schema_version": VALIDATION_REPORT_SCHEMA,
            "pack_sha256": pack,
            "evidence_graph_sha256": graph,
            "operation_id": operation_id,
            "dimension": dimension,
            "validator_id": validator_id,
            "validator_sha256": validator_hash,
            "passed": bool(passed),
            "findings": list(finding_values),
            "metrics": json.loads(metrics_json),
        }
        report_hash = _sha256(_canonical_json(identity).encode("utf-8"))
        return cls(
            report_hash, pack, graph, operation_id, dimension, validator_id,
            validator_hash, bool(passed), finding_values, metrics_json,
        )

    @property
    def metrics(self) -> dict[str, Any]:
        return json.loads(self._metrics_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATION_REPORT_SCHEMA,
            "report_sha256": self.report_sha256,
            "pack_sha256": self.pack_sha256,
            "evidence_graph_sha256": self.evidence_graph_sha256,
            "operation_id": self.operation_id,
            "dimension": self.dimension,
            "validator_id": self.validator_id,
            "validator_sha256": self.validator_sha256,
            "passed": self.passed,
            "findings": list(self.findings),
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class GeometrySnapshot:
    fingerprints: tuple[tuple[str, str], ...]
    max_deviation_native: tuple[tuple[str, float], ...] = ()

    @classmethod
    def create(
        cls,
        fingerprints: Mapping[str, str],
        max_deviation_native: Mapping[str, float] | None = None,
    ) -> "GeometrySnapshot":
        normalized = tuple(sorted((str(key), str(value)) for key, value in fingerprints.items()))
        if any(not key or not value for key, value in normalized):
            raise DecisionValidationError("Geometry fingerprints require non-empty IDs and digests")
        deviations = tuple(sorted(
            (str(key), _finite(value, f"max_deviation_native.{key}", nonnegative=True))
            for key, value in (max_deviation_native or {}).items()
        ))
        return cls(normalized, deviations)

    @property
    def fingerprint_map(self) -> dict[str, str]:
        return dict(self.fingerprints)


def validate_geometry(
    pack: DecisionPack,
    operation: RepairOperation,
    before: GeometrySnapshot,
    after: GeometrySnapshot,
) -> ValidationReport:
    before_map = before.fingerprint_map
    after_map = after.fingerprint_map
    findings: list[str] = []
    if set(before_map) != set(after_map):
        findings.append("geometry entity set changed outside the registered graph")
    changed = {
        key for key in set(before_map) & set(after_map)
        if before_map[key] != after_map[key]
    }
    allowed = set(operation.entity_node_ids) if operation.changes_geometry else set()
    unauthorized = changed - allowed
    if unauthorized:
        findings.append(f"unauthorized geometry changes: {sorted(unauthorized)}")
    deviations = dict(after.max_deviation_native)
    missing_deviations = changed - set(deviations)
    if missing_deviations:
        findings.append(f"changed geometry lacks deviation evidence: {sorted(missing_deviations)}")
    return ValidationReport.create(
        pack_sha256=pack.pack_sha256,
        evidence_graph_sha256=pack.evidence_graph_sha256,
        operation_id=operation.operation_id,
        dimension="geometry",
        validator_id="cad2gis.geometry.source-fidelity.v1",
        passed=not findings,
        findings=findings,
        metrics={
            "changed_entity_count": len(changed),
            "unauthorized_change_count": len(unauthorized),
            "missing_deviation_count": len(missing_deviations),
            "max_deviation_native": max(deviations.values(), default=0.0),
            "invented_fact_count": 0,
        },
    )


@dataclass(frozen=True)
class TopologySnapshot:
    edges: frozenset[tuple[str, str]]
    orphan_node_ids: frozenset[str] = frozenset()

    @classmethod
    def create(
        cls,
        edges: Iterable[tuple[str, str]],
        orphan_node_ids: Iterable[str] = (),
    ) -> "TopologySnapshot":
        normalized_edges = frozenset((str(left), str(right)) for left, right in edges)
        if any(not left or not right for left, right in normalized_edges):
            raise DecisionValidationError("Topology edges require non-empty node IDs")
        return cls(normalized_edges, frozenset(str(item) for item in orphan_node_ids))


def validate_topology(
    pack: DecisionPack,
    operation: RepairOperation,
    before: TopologySnapshot,
    after: TopologySnapshot,
    *,
    declared_added_edges: Iterable[tuple[str, str]] = (),
    declared_removed_edges: Iterable[tuple[str, str]] = (),
) -> ValidationReport:
    added = after.edges - before.edges
    removed = before.edges - after.edges
    expected_added = frozenset((str(left), str(right)) for left, right in declared_added_edges)
    expected_removed = frozenset((str(left), str(right)) for left, right in declared_removed_edges)
    findings: list[str] = []
    if added != expected_added:
        findings.append("topology additions differ from deterministic simulation declaration")
    if removed != expected_removed:
        findings.append("topology removals differ from deterministic simulation declaration")
    new_orphans = after.orphan_node_ids - before.orphan_node_ids
    if new_orphans:
        findings.append(f"repair introduced orphan nodes: {sorted(new_orphans)}")
    return ValidationReport.create(
        pack_sha256=pack.pack_sha256,
        evidence_graph_sha256=pack.evidence_graph_sha256,
        operation_id=operation.operation_id,
        dimension="topology",
        validator_id="cad2gis.topology.graph-delta.v1",
        passed=not findings,
        findings=findings,
        metrics={
            "added_edge_count": len(added),
            "removed_edge_count": len(removed),
            "invalid_edge_delta": int(added != expected_added) + int(removed != expected_removed),
            "new_orphan_count": len(new_orphans),
            "invented_fact_count": 0,
        },
    )


@dataclass(frozen=True)
class LengthSnapshot:
    lengths_native: tuple[tuple[str, float], ...]

    @classmethod
    def create(cls, lengths_native: Mapping[str, float]) -> "LengthSnapshot":
        return cls(tuple(sorted(
            (str(key), _finite(value, f"lengths_native.{key}", nonnegative=True))
            for key, value in lengths_native.items()
        )))

    @property
    def length_map(self) -> dict[str, float]:
        return dict(self.lengths_native)


def validate_lengths(
    pack: DecisionPack,
    operation: RepairOperation,
    before: LengthSnapshot,
    after: LengthSnapshot,
    *,
    tolerance_native: float,
    dimension_evidence: Mapping[str, float] | None = None,
) -> ValidationReport:
    tolerance = _finite(tolerance_native, "tolerance_native", nonnegative=True)
    before_map = before.length_map
    after_map = after.length_map
    findings: list[str] = []
    if set(before_map) != set(after_map):
        findings.append("length entity set changed without a source-bound aggregate")
    deltas = {
        key: abs(after_map[key] - before_map[key])
        for key in set(before_map) & set(after_map)
    }
    excessive = {key: value for key, value in deltas.items() if value > tolerance}
    if excessive:
        findings.append(f"native length closure failed: {sorted(excessive)}")
    dimension_deltas: dict[str, float] = {}
    for key, raw_value in (dimension_evidence or {}).items():
        dimension = _finite(raw_value, f"dimension_evidence.{key}", nonnegative=True)
        if key not in after_map:
            findings.append(f"dimension evidence targets unknown aggregate: {key}")
            continue
        dimension_deltas[key] = abs(after_map[key] - dimension)
    bad_dimensions = {key: value for key, value in dimension_deltas.items() if value > tolerance}
    if bad_dimensions:
        findings.append(f"dimension-to-geometry closure failed: {sorted(bad_dimensions)}")
    total_delta = abs(sum(after_map.values()) - sum(before_map.values()))
    if total_delta > tolerance:
        findings.append("total native length changed beyond tolerance")
    return ValidationReport.create(
        pack_sha256=pack.pack_sha256,
        evidence_graph_sha256=pack.evidence_graph_sha256,
        operation_id=operation.operation_id,
        dimension="length",
        validator_id="cad2gis.length.native-closure.v1",
        passed=not findings,
        findings=findings,
        metrics={
            "max_entity_delta_native": max(deltas.values(), default=0.0),
            "total_delta_native": total_delta,
            "max_dimension_delta_native": max(dimension_deltas.values(), default=0.0),
            "closure_failure_count": len(excessive) + len(bad_dimensions),
            "invented_fact_count": 0,
        },
    )


def validate_crs_candidate(
    pack: DecisionPack,
    operation: RepairOperation,
    *,
    model_family: str,
    training_rmse: float,
    independent_check_rmse: float,
    independent_check_count: int,
    max_check_rmse: float,
    spatial_coverage_passed: bool,
    complex_model_justified: bool = False,
) -> ValidationReport:
    findings: list[str] = []
    train = _finite(training_rmse, "training_rmse", nonnegative=True)
    check = _finite(independent_check_rmse, "independent_check_rmse", nonnegative=True)
    threshold = _finite(max_check_rmse, "max_check_rmse", nonnegative=True)
    if isinstance(independent_check_count, bool) or not isinstance(independent_check_count, int):
        raise DecisionValidationError("independent_check_count must be an integer")
    if independent_check_count < 1:
        findings.append("no independent CRS check points")
    if check > threshold:
        findings.append("independent CRS check RMSE exceeds policy")
    if spatial_coverage_passed is not True:
        findings.append("GCP spatial coverage failed")
    if model_family not in {"identity", "similarity", "affine", "projective", "tps"}:
        findings.append("unsupported transform model family")
    if model_family not in {"identity", "similarity"} and not complex_model_justified:
        findings.append("shape-changing transform lacks residual-pattern justification")
    return ValidationReport.create(
        pack_sha256=pack.pack_sha256,
        evidence_graph_sha256=pack.evidence_graph_sha256,
        operation_id=operation.operation_id,
        dimension="crs",
        validator_id="cad2gis.crs.independent-check.v1",
        passed=not findings,
        findings=findings,
        metrics={
            "model_family": model_family,
            "training_rmse": train,
            "independent_check_rmse": check,
            "independent_check_count": independent_check_count,
            "max_check_rmse": threshold,
            "spatial_coverage_passed": bool(spatial_coverage_passed),
            "complex_model_justified": bool(complex_model_justified),
            "invented_fact_count": 0,
        },
    )


@dataclass(frozen=True)
class AutoDecisionPolicy:
    """Replace manual approval with evidence thresholds and abstention."""

    policy_id: str = "cad2gis.auto-decision.default.v1"
    semantic_min_confidence: float = 0.80
    geometry_min_confidence: float = 0.90
    georeference_min_confidence: float = 0.95
    semantic_min_agreement: int = 1
    geometry_min_agreement: int = 2
    georeference_min_agreement: int = 2

    def evaluate(
        self,
        pack: DecisionPack,
        operation: RepairOperation,
        reports: Iterable[ValidationReport],
    ) -> "AutoDecision":
        if pack.policy_id != self.policy_id:
            raise DecisionValidationError(
                f"Decision pack policy {pack.policy_id!r} does not match {self.policy_id!r}"
            )
        if operation.operation_id not in {item.operation_id for item in pack.operations}:
            raise DecisionValidationError("Operation is not present in the decision pack")
        report_values = tuple(sorted(reports, key=lambda item: item.dimension))
        dimensions = [item.dimension for item in report_values]
        if len(dimensions) != len(set(dimensions)):
            raise DecisionValidationError("Validation set contains duplicate dimensions")
        for report in report_values:
            if report.pack_sha256 != pack.pack_sha256:
                raise DecisionValidationError("Validation report belongs to another decision pack")
            if report.evidence_graph_sha256 != pack.evidence_graph_sha256:
                raise DecisionValidationError("Validation report belongs to another evidence graph")
            if report.operation_id != operation.operation_id:
                raise DecisionValidationError("Validation report belongs to another operation")

        required = {"geometry", "topology", "length"}
        if operation.risk == "georeference":
            required.add("crs")
        missing = required - set(dimensions)
        reasons: list[str] = []
        if missing:
            reasons.append(f"missing independent validators: {sorted(missing)}")
            disposition: Disposition = "QUARANTINED"
        elif any(not report.passed for report in report_values if report.dimension in required):
            reasons.extend(
                f"{report.dimension}: {finding}"
                for report in report_values if not report.passed
                for finding in (report.findings or ("validator failed",))
            )
            disposition = "REJECTED"
        elif any(int(report.metrics.get("invented_fact_count", 0)) != 0 for report in report_values):
            reasons.append("validator reported invented facts")
            disposition = "REJECTED"
        else:
            confidence_threshold = {
                "semantic": self.semantic_min_confidence,
                "geometry": self.geometry_min_confidence,
                "georeference": self.georeference_min_confidence,
            }[operation.risk]
            agreement_threshold = {
                "semantic": self.semantic_min_agreement,
                "geometry": self.geometry_min_agreement,
                "georeference": self.georeference_min_agreement,
            }[operation.risk]
            if operation.confidence < confidence_threshold:
                reasons.append(
                    f"confidence {operation.confidence:.3f} is below {confidence_threshold:.3f}"
                )
            if operation.agreement_count < agreement_threshold:
                reasons.append(
                    f"agreement {operation.agreement_count} is below {agreement_threshold}"
                )
            disposition = "QUARANTINED" if reasons else "AUTO_ACCEPTED"
        return AutoDecision.create(
            pack=pack,
            operation=operation,
            policy_id=self.policy_id,
            disposition=disposition,
            reasons=reasons,
            reports=report_values,
        )


@dataclass(frozen=True)
class AutoDecision:
    decision_sha256: str
    pack_sha256: str
    operation_id: str
    policy_id: str
    disposition: Disposition
    reasons: tuple[str, ...]
    report_sha256s: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        pack: DecisionPack,
        operation: RepairOperation,
        policy_id: str,
        disposition: Disposition,
        reasons: Iterable[str],
        reports: Iterable[ValidationReport],
    ) -> "AutoDecision":
        if disposition not in {"AUTO_ACCEPTED", "QUARANTINED", "REJECTED"}:
            raise DecisionValidationError(f"Unsupported disposition: {disposition!r}")
        reason_values = tuple(str(item) for item in reasons)
        report_hashes = tuple(sorted(report.report_sha256 for report in reports))
        identity = {
            "schema_version": AUTO_DECISION_SCHEMA,
            "pack_sha256": pack.pack_sha256,
            "operation_id": operation.operation_id,
            "policy_id": policy_id,
            "disposition": disposition,
            "reasons": list(reason_values),
            "report_sha256s": list(report_hashes),
        }
        digest = _sha256(_canonical_json(identity).encode("utf-8"))
        return cls(
            digest, pack.pack_sha256, operation.operation_id, policy_id,
            disposition, reason_values, report_hashes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUTO_DECISION_SCHEMA,
            "decision_sha256": self.decision_sha256,
            "pack_sha256": self.pack_sha256,
            "operation_id": self.operation_id,
            "policy_id": self.policy_id,
            "disposition": self.disposition,
            "reasons": list(self.reasons),
            "report_sha256s": list(self.report_sha256s),
        }


__all__ = [
    "AUTO_DECISION_SCHEMA",
    "VALIDATION_REPORT_SCHEMA",
    "AutoDecision",
    "AutoDecisionPolicy",
    "DecisionValidationError",
    "GeometrySnapshot",
    "LengthSnapshot",
    "TopologySnapshot",
    "ValidationReport",
    "validate_crs_candidate",
    "validate_geometry",
    "validate_lengths",
    "validate_topology",
]
