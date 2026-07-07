"""Structured correction patches and auditable correction ledgers."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .model import Feature, FeatureCollection, UNMAPPED
from .warehouse import PUBLISHED_SCHEMA

SUPPORTED_PATCH_TYPES = {
    "apply_reviewed_label",
    "reclassify_feature",
    "reject_feature",
    "set_attribute",
}


@dataclass
class CorrectionPatch:
    patch_id: str
    patch_type: str
    source_handle: str
    after: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    required_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CorrectionPatch":
        return cls(
            patch_id=str(data["patch_id"]),
            patch_type=str(data["patch_type"]),
            source_handle=str(data["source_handle"]),
            after=dict(data.get("after") or {}),
            evidence=dict(data.get("evidence") or {}),
            reason=str(data.get("reason") or ""),
            before=dict(data.get("before") or {}),
            confidence=data.get("confidence"),
            required_checks=list(data.get("required_checks") or []),
        )


@dataclass
class LedgerRecord:
    patch_id: str
    patch_type: str
    source_handle: str
    status: str
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    validation: dict[str, Any] = field(default_factory=dict)
    score_delta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LedgerRecord":
        return cls(
            patch_id=str(data["patch_id"]),
            patch_type=str(data["patch_type"]),
            source_handle=str(data["source_handle"]),
            status=str(data["status"]),
            before=dict(data.get("before") or {}),
            after=dict(data.get("after") or {}),
            evidence=dict(data.get("evidence") or {}),
            reason=str(data.get("reason") or ""),
            validation=dict(data.get("validation") or {}),
            score_delta=dict(data.get("score_delta") or {}),
        )


def _feature_before(feature: Feature) -> dict[str, Any]:
    return {
        "feature_class": feature.feature_class,
        "attributes": dict(feature.attributes),
        "source_handle": feature.source.handle,
    }


def _find_by_handle(coll: FeatureCollection, handle: str) -> Feature | None:
    for feature in coll.features:
        if feature.source.handle == handle:
            return feature
    return None


def _reject(patch: CorrectionPatch, error: str, before: dict[str, Any] | None = None) -> LedgerRecord:
    return LedgerRecord(
        patch_id=patch.patch_id,
        patch_type=patch.patch_type,
        source_handle=patch.source_handle,
        status="rejected",
        before=before or {},
        after=patch.after,
        evidence=patch.evidence,
        reason=patch.reason,
        validation={"passed": False, "errors": [error]},
    )


def _geometry_allowed(feature: Feature, feature_class: str) -> bool:
    if feature_class == UNMAPPED:
        return True
    schema = PUBLISHED_SCHEMA.get(feature_class)
    if not schema:
        return False
    geom_type = getattr(feature.geometry, "geom_type", "")
    if feature_class == "duct":
        return geom_type in {"Point", "LineString", "MultiLineString"}
    return geom_type == schema.geom_type or (schema.geom_type == "LineString" and geom_type == "MultiLineString")


def _validate_patch(feature: Feature, patch: CorrectionPatch) -> list[str]:
    errors: list[str] = []
    if patch.patch_type not in SUPPORTED_PATCH_TYPES:
        errors.append(f"unsupported patch type: {patch.patch_type}")
    if not patch.reason:
        errors.append("reason is required")
    if not patch.evidence:
        errors.append("evidence is required")
    if patch.confidence is not None and not (0.0 <= float(patch.confidence) <= 1.0):
        errors.append("confidence must be between 0 and 1")
    expected_class = patch.before.get("feature_class")
    if expected_class is not None and expected_class != feature.feature_class:
        errors.append("before.feature_class mismatch")
    target_class = patch.after.get("feature_class")
    if target_class is not None:
        if target_class != UNMAPPED and target_class not in PUBLISHED_SCHEMA:
            errors.append(f"invalid feature_class: {target_class}")
        elif not _geometry_allowed(feature, target_class):
            errors.append(f"geometry {getattr(feature.geometry, 'geom_type', '')} is not allowed for {target_class}")
    evidence_text = json.dumps(patch.evidence, ensure_ascii=False).lower()
    if target_class not in (None, UNMAPPED) and any(token in evidence_text for token in ("paving", "surface", "road", "路面", "铺装")):
        errors.append("negative paving/surface evidence cannot promote a comms feature")
    return errors


def _apply_patch(feature: Feature, patch: CorrectionPatch) -> None:
    if patch.patch_type in {"apply_reviewed_label", "reclassify_feature"}:
        feature.feature_class = patch.after.get("feature_class", feature.feature_class)
        feature.attributes.update(patch.after.get("attributes") or {})
        feature.attributes["review_status"] = feature.attributes.get("review_status", "accepted")
    elif patch.patch_type == "reject_feature":
        feature.attributes["_rejected_from"] = feature.feature_class
        feature.feature_class = patch.after.get("feature_class", UNMAPPED)
        feature.attributes.update(patch.after.get("attributes") or {})
        feature.attributes["review_status"] = "rejected"
    elif patch.patch_type == "set_attribute":
        feature.attributes.update(patch.after.get("attributes") or {})


def apply_patches(
    coll: FeatureCollection,
    patches: list[CorrectionPatch],
) -> tuple[FeatureCollection, list[LedgerRecord]]:
    out = copy.deepcopy(coll)
    records: list[LedgerRecord] = []
    for patch in patches:
        feature = _find_by_handle(out, patch.source_handle)
        before = _feature_before(feature) if feature else {}
        if feature is None:
            records.append(_reject(patch, f"source handle not found: {patch.source_handle}", before))
            continue
        errors = _validate_patch(feature, patch)
        if errors:
            records.append(_reject(patch, errors[0], before))
            continue
        _apply_patch(feature, patch)
        records.append(LedgerRecord(
            patch_id=patch.patch_id,
            patch_type=patch.patch_type,
            source_handle=patch.source_handle,
            status="accepted",
            before=before,
            after=_feature_before(feature),
            evidence=patch.evidence,
            reason=patch.reason,
            validation={"passed": True, "checks": patch.required_checks},
        ))
    return out, records


def patches_from_jsonable(data: dict[str, Any] | list[dict[str, Any]]) -> list[CorrectionPatch]:
    raw = data.get("proposals", data.get("patches", [])) if isinstance(data, dict) else data
    return [CorrectionPatch.from_dict(item) for item in raw]


def patches_to_jsonable(patches: list[CorrectionPatch]) -> dict[str, Any]:
    return {"proposals": [patch.to_dict() for patch in patches]}


def read_patches(path: str | Path) -> list[CorrectionPatch]:
    return patches_from_jsonable(json.loads(Path(path).read_text(encoding="utf-8")))


def _feature_to_dict(feature: Feature) -> dict[str, Any]:
    return {
        "geometry_wkt": feature.geometry.wkt,
        "feature_class": feature.feature_class,
        "attributes": feature.attributes,
        "source": asdict(feature.source),
        "confidence": feature.confidence,
        "notes": feature.notes,
    }


def _feature_from_dict(data: dict[str, Any]) -> Feature:
    from shapely import wkt
    from .model import SourceRef

    return Feature(
        geometry=wkt.loads(data["geometry_wkt"]),
        feature_class=data.get("feature_class"),
        attributes=dict(data.get("attributes") or {}),
        source=SourceRef(**dict(data.get("source") or {})),
        confidence=float(data.get("confidence", 1.0)),
        notes=list(data.get("notes") or []),
    )


def write_feature_collection(path: str | Path, coll: FeatureCollection) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "crs": coll.crs,
        "source_file": coll.source_file,
        "metadata": coll.metadata,
        "features": [_feature_to_dict(feature) for feature in coll.features],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_feature_collection(path: str | Path) -> FeatureCollection:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    coll = FeatureCollection(
        crs=payload.get("crs"),
        source_file=payload.get("source_file"),
        metadata=dict(payload.get("metadata") or {}),
    )
    for item in payload.get("features") or []:
        coll.add(_feature_from_dict(item))
    return coll


def write_ledger_entry(path: str | Path, record: LedgerRecord) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def read_ledger(path: str | Path) -> list[LedgerRecord]:
    path = Path(path)
    if not path.exists():
        return []
    records: list[LedgerRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(LedgerRecord.from_dict(json.loads(line)))
    return records
