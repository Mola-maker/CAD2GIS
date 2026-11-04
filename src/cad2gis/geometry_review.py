"""Per-change engineering decisions for audited geometry changes.

The visual audit (``audit_visual_conversion``) lists every delivered geometry
that differs from its source beyond numeric tolerance, plus derived features
with no complete source geometry. Those are traceable but still need an
engineering decision. This module turns them into a review ledger:

* ``list_geometry_changes`` gives each change a content-addressed
  ``change_id`` bound to the run manifest, the audit report and the source
  hash, a question that fits the operation, and its latest decision;
* ``record_geometry_decision`` appends an accept / reject / defer receipt to
  ``geometry_decisions.jsonl`` in a review workspace outside the run.

The run, its GeoPackages and the audit are never modified. Accepting a
geometry change records engineering intent only; it is not GCP or absolute
positional accuracy evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = "cad2gis.geometry_decision.v1"
LEDGER_NAME = "geometry_decisions.jsonl"
DECISIONS = frozenset({"accept", "reject", "defer"})

_QUESTIONS = (
    ("repair_boundary_polygon",
     "Which region does this boundary represent, and which part of the source outline must the "
     "delivered polygon keep?"),
    ("collocate_with_support",
     "Should this device be delivered at its support point, or at the position drawn in the source?"),
    ("bridge",
     "Does this cable end really connect to the support it was bridged to?"),
    ("conservative_znro_component",
     "Accept this derived zone even though the source has no complete polygon for it?"),
    ("rectangular_frame",
     "Is the drawn frame an annotation of the device at the delivered point?"),
)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _question(operations: str, status: str) -> str:
    for marker, question in _QUESTIONS:
        if marker in operations:
            return question
    if status == "derived_without_direct_source":
        return "Accept this derived feature even though the source has no complete geometry for it?"
    return "Confirm that this delivered geometry change matches the engineering intent."


def _load_audit(audit_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    report_path = audit_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not report.get("run_dir") or not report.get("source_sha256"):
        raise ValueError(f"{report_path} is not a CAD2GIS visual audit report")
    manifest_path = Path(report["run_dir"]) / "run_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"The audited run is not available: {manifest_path}")
    binding = {
        "audit_report_sha256": _sha256_file(report_path),
        "run_manifest_sha256": _sha256_file(manifest_path),
        "source_sha256": str(report["source_sha256"]),
        "run_dir": str(Path(report["run_dir"]).resolve()),
    }
    return report, binding


def _changes(report: dict[str, Any], binding: dict[str, str]) -> list[dict[str, Any]]:
    items = []
    for kind, rows in (("explicit_geometry_change", report.get("explicit_geometry_changes") or []),
                       ("derived_without_direct_source", report.get("derived_without_direct_source") or [])):
        for row in rows:
            identity = {
                "run_manifest_sha256": binding["run_manifest_sha256"],
                "source_sha256": binding["source_sha256"],
                "kind": kind,
                "layer": row.get("layer"),
                "source_entity_key": row.get("source_entity_key"),
                "source_handle": row.get("source_handle"),
                "operations": row.get("operations"),
            }
            displacement = row.get("point_anchor_displacement_m")
            if displacement is None:
                displacement = row.get("hausdorff_native_m")
            items.append({
                "change_id": _canonical_sha256(identity),
                "kind": kind,
                "layer": row.get("layer"),
                "source_handle": row.get("source_handle"),
                "source_entity_key": row.get("source_entity_key"),
                "operations": row.get("operations"),
                "status": row.get("status"),
                "distance_basis": row.get("distance_basis"),
                "source_relative_change_m": displacement,
                "question": _question(str(row.get("operations") or ""), str(row.get("status") or "")),
            })
    items.sort(key=lambda item: (-(item["source_relative_change_m"] or 0.0), item["change_id"]))
    return items


def _ledger(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / LEDGER_NAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _latest(ledger: list[dict[str, Any]], binding: dict[str, str]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for receipt in ledger:
        if receipt.get("run_manifest_sha256") == binding["run_manifest_sha256"]:
            latest[receipt["change_id"]] = receipt
    return latest


def list_geometry_changes(audit_dir: str | Path, workspace_dir: str | Path) -> dict[str, Any]:
    """List audited geometry changes with their latest recorded decision."""

    report, binding = _load_audit(Path(audit_dir).resolve())
    latest = _latest(_ledger(Path(workspace_dir).resolve()), binding)
    changes = _changes(report, binding)
    for item in changes:
        receipt = latest.get(item["change_id"])
        item["decision"] = None if receipt is None else {
            key: receipt[key] for key in ("decision", "reviewer", "note", "recorded_at")}
    counts = {name: sum(1 for item in changes if (item["decision"] or {}).get("decision") == name)
              for name in sorted(DECISIONS)}
    counts["undecided"] = sum(1 for item in changes if item["decision"] is None)
    return {
        "schema_version": "cad2gis.geometry_review.v1",
        **binding,
        "change_count": len(changes),
        "decision_counts": counts,
        "changes": changes,
        "authority": "engineering intent only; not GCP or absolute positional accuracy evidence",
    }


def record_geometry_decision(
    audit_dir: str | Path, workspace_dir: str | Path, change_id: str, decision: str,
    *, reviewer: str = "", note: str = "",
) -> dict[str, Any]:
    """Append one decision receipt; the newest receipt for a change wins."""

    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    report, binding = _load_audit(Path(audit_dir).resolve())
    if change_id not in {item["change_id"] for item in _changes(report, binding)}:
        raise ValueError("change_id does not belong to this audited run; list the changes again")
    workspace = Path(workspace_dir).resolve()
    run_dir = Path(binding["run_dir"])
    if workspace == run_dir or run_dir in workspace.parents:
        raise ValueError("The review workspace must be outside the immutable run")
    workspace.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": LEDGER_SCHEMA,
        **binding,
        "change_id": change_id,
        "decision": decision,
        "reviewer": reviewer,
        "note": note,
        "recorded_at": datetime.now(UTC).isoformat(),
        "absolute_accuracy_verified": False,
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    with (workspace / LEDGER_NAME).open("a", encoding="utf-8", newline="\n") as ledger:
        ledger.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return receipt


__all__ = ["list_geometry_changes", "record_geometry_decision"]
