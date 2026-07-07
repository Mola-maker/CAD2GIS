"""Deterministic Accuracy Doctor diagnostics.

This module finds suspicious conversion output without changing GIS data. The records it emits are
the shared contract for the QGIS dock, web dashboard, CLI, and optional LLM doctor prompt package.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .model import Feature, FeatureCollection


@dataclass
class Issue:
    issue_id: str
    issue_type: str
    severity: str
    feature_class: str | None = None
    source_handle: str | None = None
    geometry_type: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_patch_types: list[str] = field(default_factory=list)
    status: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Issue":
        return cls(
            issue_id=str(data["issue_id"]),
            issue_type=str(data["issue_type"]),
            severity=str(data.get("severity", "warning")),
            feature_class=data.get("feature_class"),
            source_handle=data.get("source_handle"),
            geometry_type=data.get("geometry_type"),
            evidence=dict(data.get("evidence") or {}),
            suggested_patch_types=list(data.get("suggested_patch_types") or []),
            status=str(data.get("status", "open")),
        )


def _feature_summary(feature: Feature) -> dict[str, Any]:
    source = feature.source
    return {
        "source_file": source.file,
        "layer": source.layer,
        "block": source.block,
        "entity_type": source.entity_type,
        "confidence": feature.confidence,
        "notes": list(feature.notes),
    }


def _issue_id(issue_type: str, feature: Feature, ordinal: int) -> str:
    if feature.source.handle:
        handle = feature.source.handle
    else:
        geom_key = getattr(feature.geometry, "wkb_hex", repr(feature.geometry))
        raw = "|".join(str(v or "") for v in (
            feature.source.file,
            feature.source.layer,
            feature.source.block,
            feature.source.entity_type,
            feature.feature_class,
            geom_key,
        ))
        handle = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{issue_type}:{handle}"


def _geom_type(feature: Feature) -> str | None:
    return getattr(feature.geometry, "geom_type", None)


def _is_route(feature: Feature) -> bool:
    return feature.feature_class in {"cable", "duct"} and _geom_type(feature) in {"LineString", "MultiLineString"}


def diagnose_collection(
    coll: FeatureCollection,
    *,
    per_feature: dict[str, Any] | None = None,
    network: dict[str, Any] | None = None,
) -> list[Issue]:
    """Return deterministic issues for a converted collection.

    The detectors are intentionally conservative. They surface likely review work but do not change
    features or improve scores by themselves.
    """
    issues: list[Issue] = []
    per_feature = per_feature or {}
    network = network or {}
    by_class = per_feature.get("by_class") or {}
    duct_stats = by_class.get("duct") or {}
    duct_has_unverified = duct_stats.get("verified", 0) < duct_stats.get("total", 1)

    for idx, feature in enumerate(coll.features):
        evidence = _feature_summary(feature)
        geom_type = _geom_type(feature)

        if feature.feature_class == "duct" and duct_has_unverified:
            if feature.attributes.get("resolved_by") == "topology_propagation" or geom_type == "Point":
                issues.append(Issue(
                    issue_id=_issue_id("unverified_duct", feature, idx),
                    issue_type="unverified_duct",
                    severity="warning",
                    feature_class=feature.feature_class,
                    source_handle=feature.source.handle,
                    geometry_type=geom_type,
                    evidence=evidence,
                    suggested_patch_types=["apply_reviewed_label", "reject_feature"],
                ))

        ev = feature.attributes.get("_map_evidence") or {}
        text_blob = " ".join(str(v) for v in [feature.source.layer, feature.source.block, ev, feature.attributes])
        if feature.feature_class not in {None, "__unmapped__"} and any(
            token in text_blob.lower() for token in ("paving", "surface", "road", "tree", "路面", "铺装")
        ):
            issues.append(Issue(
                issue_id=_issue_id("possible_paving_leak", feature, idx),
                issue_type="possible_paving_leak",
                severity="error",
                feature_class=feature.feature_class,
                source_handle=feature.source.handle,
                geometry_type=geom_type,
                evidence=evidence,
                suggested_patch_types=["reject_feature"],
            ))

    if int(network.get("dangling_ends") or 0) > 0:
        for idx, feature in enumerate(coll.features):
            if _is_route(feature):
                issues.append(Issue(
                    issue_id=_issue_id("dangling_route", feature, idx),
                    issue_type="dangling_route",
                    severity="warning",
                    feature_class=feature.feature_class,
                    source_handle=feature.source.handle,
                    geometry_type=_geom_type(feature),
                    evidence={**_feature_summary(feature), "dangling_ends": network.get("dangling_ends")},
                    suggested_patch_types=["reject_feature"],
                ))
                break

    return issues


def issues_to_jsonable(issues: list[Issue]) -> dict[str, Any]:
    return {"issues": [issue.to_dict() for issue in issues]}


def issues_from_jsonable(data: dict[str, Any] | list[dict[str, Any]]) -> list[Issue]:
    raw = data.get("issues", []) if isinstance(data, dict) else data
    return [Issue.from_dict(item) for item in raw]


def write_diagnostics(path: str | Path, issues: list[Issue], *, metadata: dict[str, Any] | None = None) -> None:
    payload = issues_to_jsonable(issues)
    if metadata:
        payload["metadata"] = metadata
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_diagnostics(path: str | Path) -> list[Issue]:
    return issues_from_jsonable(json.loads(Path(path).read_text(encoding="utf-8")))
