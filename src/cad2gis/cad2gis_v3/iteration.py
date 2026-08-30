"""Auditable feedback iterations over immutable CAD2GIS conversion runs.

The loop in this module adapts project-scoped interpretation state.  It never
rewrites source geometry, silently promotes a candidate, or mutates plugin
code.  Every feedback item, visual artifact, candidate run, and accepted
lesson is content-bound so a host agent can retry without losing provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SESSION_SCHEMA = "cad2gis.iteration_session.v1"
CONTEXT_SCHEMA = "cad2gis.iteration_context.v1"
EVALUATION_SCHEMA = "cad2gis.iteration_evaluation.v1"
LEARNING_SCHEMA = "cad2gis.iteration_learning.v1"
LEARNING_REGISTRY_SCHEMA = "cad2gis.iteration_learning_registry.v1"

_SHA256_LENGTH = 64
_IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_FEEDBACK_ITEMS = 100
_MAX_VISUAL_REFS = 16
_STATUS_RANK = {"FAILED": 0, "UNSAFE": 1, "CONDITIONAL": 2, "VERIFIED": 3}
_CATEGORIES = {
    "scene_interpretation",
    "semantic_mapping",
    "network_topology",
    "label_assignment",
    "styling",
    "georeference",
    "completeness",
    "other",
}
_SEVERITIES = {"minor", "major", "critical"}
_FEEDBACK_KEYS = {
    "category",
    "severity",
    "observation",
    "expected_outcome",
    "visual_refs",
    "evidence_node_ids",
    "tags",
}


class IterationError(ValueError):
    """An iteration artifact escaped its source-bound safety contract."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, *, max_bytes: int = 64 * 1024 * 1024) -> dict[str, Any]:
    if not path.is_file():
        raise IterationError(f"JSON artifact does not exist: {path}")
    if path.stat().st_size > max_bytes:
        raise IterationError(f"JSON artifact exceeds {max_bytes} bytes: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IterationError(f"Cannot read JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise IterationError(f"JSON artifact root must be an object: {path}")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _bounded_text(value: Any, name: str, *, maximum: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise IterationError(f"{name} must be non-empty")
    if len(text) > maximum:
        raise IterationError(f"{name} exceeds {maximum} characters")
    return text


def _session_file(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    return path / "iteration_session.json" if path.is_dir() else path


def _seal_session(session: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(session)
    sealed.pop("session_sha256", None)
    sealed["session_sha256"] = _digest(sealed)
    return sealed


def _load_session(value: str | Path) -> tuple[Path, dict[str, Any]]:
    path = _session_file(value)
    session = _read_json(path, max_bytes=16 * 1024 * 1024)
    if session.get("schema_version") != SESSION_SCHEMA:
        raise IterationError("Unsupported iteration session schema")
    expected = session.get("session_sha256")
    if not isinstance(expected, str) or len(expected) != _SHA256_LENGTH:
        raise IterationError("Iteration session has no valid content digest")
    unsigned = dict(session)
    unsigned.pop("session_sha256", None)
    if _digest(unsigned) != expected:
        raise IterationError("Iteration session content digest mismatch")
    return path, session


def _write_session(path: Path, session: dict[str, Any]) -> dict[str, Any]:
    session["updated_at"] = _now()
    sealed = _seal_session(session)
    _write_json_atomic(path, sealed)
    return sealed


def _resolve_run_artifact(run_dir: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise IterationError("Run artifact path must be a non-empty string")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = run_dir / path
    path = path.resolve()
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise IterationError(
            f"Run artifact escapes immutable run directory: {path}"
        ) from exc
    if not path.is_file():
        raise IterationError(f"Run artifact does not exist: {path}")
    return path


def _run_snapshot(run_dir: str | Path) -> dict[str, Any]:
    directory = Path(run_dir).expanduser().resolve()
    if not directory.is_dir():
        raise IterationError(f"Run directory does not exist: {directory}")
    manifest_path = directory / "run_manifest.json"
    manifest = _read_json(manifest_path)
    source = manifest.get("source")
    source_sha256 = source.get("sha256") if isinstance(source, dict) else None
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != _SHA256_LENGTH
        or any(char not in "0123456789abcdefABCDEF" for char in source_sha256)
    ):
        raise IterationError("Run manifest has no valid source SHA-256")
    status = str(manifest.get("run_status", ""))
    if status not in _STATUS_RANK:
        raise IterationError(f"Unsupported run_status in manifest: {status!r}")
    return {
        "run_dir": str(directory),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _file_sha256(manifest_path),
        "source_sha256": source_sha256.lower(),
        "run_status": status,
        "source_entity_count": manifest.get("source_entity_count"),
        "unresolved_count": manifest.get("unresolved_count"),
        "delivery_counts": dict(manifest.get("delivery_counts") or {}),
        "validation": dict(manifest.get("validation") or {}),
        "artifacts": dict(manifest.get("artifacts") or {}),
    }


def _next_action(session: Mapping[str, Any]) -> str:
    status = session.get("status")
    if status == "awaiting_decision":
        return "decide_iteration_candidate"
    if status == "exhausted":
        return "start_new_session_or_request_human_design_change"
    feedback = session.get("feedback") or []
    if not any(item.get("status") == "open" for item in feedback):
        return "record_iteration_feedback"
    if not session.get("last_context"):
        return "prepare_iteration_context"
    return "produce_new_immutable_run_then_evaluate_iteration_candidate"


def _summary(path: Path, session: Mapping[str, Any]) -> dict[str, Any]:
    feedback = list(session.get("feedback") or [])
    candidates = list(session.get("candidates") or [])
    return {
        "schema_version": SESSION_SCHEMA,
        "session_path": str(path),
        "session_id": session.get("session_id"),
        "session_sha256": session.get("session_sha256"),
        "status": session.get("status"),
        "source_sha256": session.get("source_sha256"),
        "active_run": session.get("active_run"),
        "iteration_budget": session.get("iteration_budget"),
        "feedback": {
            "total": len(feedback),
            "open": sum(item.get("status") == "open" for item in feedback),
            "resolved": sum(item.get("status") == "resolved" for item in feedback),
        },
        "candidates": {
            "total": len(candidates),
            "pending": sum(item.get("verdict") == "pending" for item in candidates),
            "accepted": sum(item.get("verdict") == "accept" for item in candidates),
        },
        "last_context": session.get("last_context"),
        "learning_artifacts": list(session.get("learning_artifacts") or []),
        "next_action": _next_action(session),
    }


def start_feedback_iteration(
    base_run_dir: str | Path,
    *,
    session_dir: str | Path | None = None,
    max_iterations: int = 3,
) -> dict[str, Any]:
    """Create a source-bound iteration session without touching the base run."""

    if isinstance(max_iterations, bool) or not 1 <= int(max_iterations) <= 12:
        raise IterationError("max_iterations must be between 1 and 12")
    base = _run_snapshot(base_run_dir)
    session_id = f"itr_{uuid.uuid4().hex[:16]}"
    if session_dir is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = (
            Path(base["run_dir"]).parent
            / "iterations"
            / f"{stamp}-{base['manifest_sha256'][:8]}-{session_id[-6:]}"
        )
    else:
        directory = Path(session_dir).expanduser().resolve()
    if directory.exists():
        raise IterationError(f"Iteration session directory already exists: {directory}")
    directory.mkdir(parents=True)
    path = directory / "iteration_session.json"
    created_at = _now()
    session = {
        "schema_version": SESSION_SCHEMA,
        "session_id": session_id,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "collecting_feedback",
        "source_sha256": base["source_sha256"],
        "base_run": base,
        "active_run": base,
        "iteration_budget": {"max_iterations": int(max_iterations), "used": 0},
        "authority": {
            "adaptation_scope": "project_interpretation_and_configuration_only",
            "source_geometry_writable": False,
            "plugin_code_self_modify": False,
            "automatic_candidate_promotion": False,
            "user_confirmation_required": True,
        },
        "feedback": [],
        "candidates": [],
        "last_context": None,
        "learning_artifacts": [],
    }
    sealed = _write_session(path, session)
    return _summary(path, sealed)


def inspect_iteration(value: str | Path) -> dict[str, Any]:
    path, session = _load_session(value)
    return _summary(path, session)


def _visual_catalog(run: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    run_dir = Path(str(run["run_dir"]))
    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    for artifact_name in ("scene_visual", "visual_evidence"):
        artifact = (run.get("artifacts") or {}).get(artifact_name)
        if not isinstance(artifact, dict):
            continue
        manifest_path = _resolve_run_artifact(run_dir, artifact.get("path"))
        actual_manifest_sha = _file_sha256(manifest_path)
        declared_manifest_sha = artifact.get("sha256")
        if declared_manifest_sha and declared_manifest_sha != actual_manifest_sha:
            raise IterationError(f"{artifact_name} manifest digest mismatch")
        manifest = _read_json(manifest_path)
        regions = manifest.get("regions")
        if not isinstance(regions, list):
            continue
        for value in regions:
            if not isinstance(value, dict):
                continue
            region_id = str(value.get("region_id", "")).strip()
            if not region_id:
                continue
            render_path = _resolve_run_artifact(run_dir, value.get("render_path"))
            render_sha = _file_sha256(render_path)
            if value.get("render_sha256") and value.get("render_sha256") != render_sha:
                raise IterationError(
                    f"Visual render digest mismatch for region {region_id}"
                )
            catalog[(artifact_name, region_id)] = {
                "kind": "run_region",
                "artifact": artifact_name,
                "region_id": region_id,
                "manifest_sha256": actual_manifest_sha,
                "render_path": str(render_path.relative_to(run_dir)).replace("\\", "/"),
                "render_sha256": render_sha,
                "authority": value.get("authority", "secondary_visual_evidence_only"),
                "visible_entity_count": value.get("visible_entity_count"),
            }
    return catalog


def _evidence_node_ids(run: Mapping[str, Any]) -> set[str]:
    artifact = (run.get("artifacts") or {}).get("evidence_graph")
    if not isinstance(artifact, dict):
        return set()
    run_dir = Path(str(run["run_dir"]))
    graph = _read_json(_resolve_run_artifact(run_dir, artifact.get("path")))
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return set()
    return {
        str(node.get("node_id"))
        for node in nodes
        if isinstance(node, dict) and node.get("node_id")
    }


def _normalize_string_list(value: Any, name: str, *, maximum: int = 128) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise IterationError(f"{name} must be an array of at most {maximum} strings")
    result: list[str] = []
    for item in value:
        text = _bounded_text(item, name, maximum=512)
        if text not in result:
            result.append(text)
    return result


def _normalize_visual_refs(
    refs: Any,
    *,
    catalog: Mapping[tuple[str, str], dict[str, Any]],
    evidence_dir: Path,
) -> list[dict[str, Any]]:
    if refs is None:
        return []
    if not isinstance(refs, list) or len(refs) > _MAX_VISUAL_REFS:
        raise IterationError(
            f"visual_refs must be an array of at most {_MAX_VISUAL_REFS} references"
        )
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(refs):
        if not isinstance(value, dict):
            raise IterationError(f"visual_refs[{index}] must be an object")
        kind = str(value.get("kind", "")).strip()
        if kind == "run_region":
            if set(value) - {"kind", "artifact", "region_id"}:
                raise IterationError(
                    f"visual_refs[{index}] has unsupported run_region keys"
                )
            artifact = str(value.get("artifact") or "visual_evidence")
            region_id = _bounded_text(
                value.get("region_id"), f"visual_refs[{index}].region_id", maximum=256
            )
            match = catalog.get((artifact, region_id))
            if match is None:
                raise IterationError(
                    f"Unknown visual region {artifact}:{region_id}; list regions first"
                )
            normalized.append(dict(match))
            continue
        if kind == "user_image":
            if set(value) - {"kind", "path", "description"}:
                raise IterationError(
                    f"visual_refs[{index}] has unsupported user_image keys"
                )
            source = Path(str(value.get("path", ""))).expanduser().resolve()
            if not source.is_file() or source.suffix.lower() not in _IMAGE_SUFFIXES:
                raise IterationError(f"Unsupported or missing feedback image: {source}")
            if source.stat().st_size > _MAX_IMAGE_BYTES:
                raise IterationError(f"Feedback image exceeds {_MAX_IMAGE_BYTES} bytes")
            sha256 = _file_sha256(source)
            evidence_dir.mkdir(parents=True, exist_ok=True)
            target = evidence_dir / f"{sha256}{source.suffix.lower()}"
            if not target.exists():
                temporary = evidence_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
                try:
                    shutil.copyfile(source, temporary)
                    os.replace(temporary, target)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            if _file_sha256(target) != sha256:
                raise IterationError("Copied feedback image digest mismatch")
            normalized.append(
                {
                    "kind": "user_image",
                    "stored_path": str(target),
                    "sha256": sha256,
                    "media_type": source.suffix.lower().lstrip("."),
                    "description": _bounded_text(
                        value.get("description"),
                        f"visual_refs[{index}].description",
                        maximum=2000,
                    ),
                }
            )
            continue
        raise IterationError(
            f"visual_refs[{index}].kind must be 'run_region' or 'user_image'"
        )
    return normalized


def record_iteration_feedback(
    value: str | Path,
    feedback_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Append language and visual evidence to a content-bound session."""

    path, session = _load_session(value)
    if session["status"] == "awaiting_decision":
        raise IterationError(
            "Decide the pending candidate before recording more feedback"
        )
    if session["status"] == "exhausted":
        raise IterationError(
            "Iteration budget is exhausted; start a new reviewed session"
        )
    if (
        session["iteration_budget"]["used"]
        >= session["iteration_budget"]["max_iterations"]
    ):
        raise IterationError(
            "Iteration budget is exhausted; start a new reviewed session"
        )
    if not isinstance(feedback_items, Sequence) or isinstance(
        feedback_items, (str, bytes)
    ):
        raise IterationError("feedback_items must be an array of objects")
    if not feedback_items or len(feedback_items) > _MAX_FEEDBACK_ITEMS:
        raise IterationError(
            f"feedback_items must contain 1 to {_MAX_FEEDBACK_ITEMS} items"
        )
    if len(session["feedback"]) + len(feedback_items) > _MAX_FEEDBACK_ITEMS:
        raise IterationError(f"Session feedback limit is {_MAX_FEEDBACK_ITEMS}")

    catalog = _visual_catalog(session["active_run"])
    valid_node_ids: set[str] | None = None
    appended: list[dict[str, Any]] = []
    for index, raw in enumerate(feedback_items):
        if not isinstance(raw, Mapping):
            raise IterationError(f"feedback_items[{index}] must be an object")
        unknown = set(raw) - _FEEDBACK_KEYS
        if unknown:
            raise IterationError(
                f"feedback_items[{index}] has unsupported keys: {sorted(unknown)}"
            )
        category = str(raw.get("category", "")).strip()
        severity = str(raw.get("severity", "major")).strip()
        if category not in _CATEGORIES:
            raise IterationError(f"Unsupported feedback category: {category!r}")
        if severity not in _SEVERITIES:
            raise IterationError(f"Unsupported feedback severity: {severity!r}")
        observation = _bounded_text(
            raw.get("observation"),
            f"feedback_items[{index}].observation",
            maximum=8000,
            required=False,
        )
        expected_outcome = _bounded_text(
            raw.get("expected_outcome"),
            f"feedback_items[{index}].expected_outcome",
            maximum=4000,
        )
        visual_refs = _normalize_visual_refs(
            raw.get("visual_refs"),
            catalog=catalog,
            evidence_dir=path.parent / "evidence",
        )
        if not observation and not visual_refs:
            raise IterationError(
                f"feedback_items[{index}] needs language observation or visual_refs"
            )
        node_ids = _normalize_string_list(
            raw.get("evidence_node_ids"),
            f"feedback_items[{index}].evidence_node_ids",
        )
        if node_ids:
            if valid_node_ids is None:
                valid_node_ids = _evidence_node_ids(session["active_run"])
            missing = sorted(set(node_ids) - valid_node_ids)
            if missing:
                raise IterationError(
                    f"Feedback references unknown evidence node IDs: {missing}"
                )
        normalized = {
            "category": category,
            "severity": severity,
            "observation": observation,
            "expected_outcome": expected_outcome,
            "visual_refs": visual_refs,
            "evidence_node_ids": node_ids,
            "tags": _normalize_string_list(
                raw.get("tags"), f"feedback_items[{index}].tags", maximum=32
            ),
        }
        feedback_id = (
            "fb_"
            + _digest(
                {
                    "session_id": session["session_id"],
                    "ordinal": len(session["feedback"]) + len(appended),
                    "feedback": normalized,
                }
            )[:20]
        )
        appended.append(
            {
                "feedback_id": feedback_id,
                "recorded_at": _now(),
                "status": "open",
                **normalized,
            }
        )
    session["feedback"].extend(appended)
    session["status"] = "collecting_feedback"
    session["last_context"] = None
    sealed = _write_session(path, session)
    result = _summary(path, sealed)
    result["recorded_feedback_ids"] = [item["feedback_id"] for item in appended]
    return result


_ROUTES = {
    "scene_interpretation": {
        "tools": [
            "list_scene_visual_regions",
            "get_scene_visual_region_context",
            "list_cad_scene_nodes",
            "create_scene_interpretation_plan",
            "validate_scene_interpretation_plan",
        ],
        "output": "hash-bound scene role plan",
    },
    "semantic_mapping": {
        "tools": [
            "prepare_semantic_batches",
            "list_semantic_candidates",
            "create_semantic_decision_pack",
            "compile_semantic_layers",
            "inspect_semantic_coverage",
        ],
        "output": "source/candidate-bound semantic decision pack",
    },
    "completeness": {
        "tools": [
            "inspect_semantic_coverage",
            "list_semantic_batches",
            "list_semantic_candidates",
        ],
        "output": "explicitly accounted entity states",
    },
    "network_topology": {
        "tools": [
            "list_evidence_nodes",
            "list_endpoint_join_candidates",
            "list_network_repair_candidates",
            "create_decision_pack",
            "validate_decision_pack",
        ],
        "output": "registered-operation decision pack using existing IDs only",
    },
    "label_assignment": {
        "tools": ["list_label_candidates", "get_evidence_node"],
        "output": "candidate-bound label selection",
    },
    "styling": {
        "tools": ["list_legend_catalog_candidates", "get_evidence_node"],
        "output": "reviewed mapping/style configuration",
    },
    "georeference": {
        "tools": ["prepare_review_workspace", "inspect_run"],
        "output": "new reviewed GCP/profile input with independent validation",
    },
    "other": {
        "tools": ["inspect_run", "list_evidence_nodes", "get_evidence_node"],
        "output": "evidence-backed diagnosis before any mutation",
    },
}


def prepare_iteration_context(value: str | Path) -> dict[str, Any]:
    """Write the strict host-agent context for the next bounded retry."""

    path, session = _load_session(value)
    if session["status"] == "awaiting_decision":
        raise IterationError(
            "Decide the pending candidate before preparing another context"
        )
    if session["status"] == "exhausted":
        raise IterationError("Iteration budget is exhausted")
    existing_reference = session.get("last_context")
    if isinstance(existing_reference, dict):
        existing_path = Path(str(existing_reference.get("path", "")))
        if existing_path.is_file() and _file_sha256(
            existing_path
        ) == existing_reference.get("sha256"):
            return {
                "session": _summary(path, session),
                "context": _read_json(existing_path),
                "context_path": str(existing_path),
            }
        raise IterationError("Last iteration context is missing or has changed")
    open_feedback = [item for item in session["feedback"] if item["status"] == "open"]
    if not open_feedback:
        raise IterationError("No open feedback is available")
    categories = sorted({item["category"] for item in open_feedback})
    routes = [{"category": category, **_ROUTES[category]} for category in categories]
    context = {
        "schema_version": CONTEXT_SCHEMA,
        "session_id": session["session_id"],
        "session_sha256": session["session_sha256"],
        "source_sha256": session["source_sha256"],
        "active_run": session["active_run"],
        "open_feedback": open_feedback,
        "routes": routes,
        "loop_contract": {
            "max_iterations": session["iteration_budget"]["max_iterations"],
            "remaining": (
                session["iteration_budget"]["max_iterations"]
                - session["iteration_budget"]["used"]
            ),
            "new_run_directory_required": True,
            "rank_or_select_observed_ids_only": True,
            "source_geometry_writable": False,
            "candidate_auto_promotion": False,
            "acceptance_requires_user_confirmation": True,
        },
    }
    context["context_sha256"] = _digest(context)
    context_id = "ctx_" + context["context_sha256"][:20]
    context_path = path.parent / "contexts" / f"{context_id}.json"
    if context_path.exists():
        existing = _read_json(context_path)
        if existing != context:
            raise IterationError("Existing iteration context has mismatched content")
    else:
        _write_json_atomic(context_path, context)
    session["last_context"] = {
        "context_id": context_id,
        "path": str(context_path),
        "sha256": _file_sha256(context_path),
    }
    sealed = _write_session(path, session)
    return {
        "session": _summary(path, sealed),
        "context": context,
        "context_path": str(context_path),
    }


def _passed_gates(value: Any, prefix: str = "") -> dict[str, bool]:
    result: dict[str, bool] = {}
    if not isinstance(value, dict):
        return result
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key == "passed" and isinstance(item, bool):
            result[prefix or "validation"] = item
        elif isinstance(item, dict):
            result.update(_passed_gates(item, path))
    return result


def _compare_runs(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    regressions: list[str] = []
    improvements: list[str] = []
    if before["source_sha256"] != after["source_sha256"]:
        raise IterationError("Candidate run belongs to a different source DWG")
    before_count = before.get("source_entity_count")
    after_count = after.get("source_entity_count")
    if isinstance(before_count, int):
        if not isinstance(after_count, int):
            regressions.append("candidate omitted source_entity_count")
        elif before_count != after_count:
            regressions.append(
                f"source_entity_count changed: {before_count} -> {after_count}"
            )
    before_rank = _STATUS_RANK[str(before["run_status"])]
    after_rank = _STATUS_RANK[str(after["run_status"])]
    if after_rank < before_rank:
        regressions.append(
            f"run_status regressed: {before['run_status']} -> {after['run_status']}"
        )
    elif after_rank > before_rank:
        improvements.append(
            f"run_status improved: {before['run_status']} -> {after['run_status']}"
        )
    before_unresolved = before.get("unresolved_count")
    after_unresolved = after.get("unresolved_count")
    if isinstance(before_unresolved, int):
        if not isinstance(after_unresolved, int):
            regressions.append("candidate omitted unresolved_count")
        elif after_unresolved > before_unresolved:
            regressions.append(
                f"unresolved_count increased: {before_unresolved} -> {after_unresolved}"
            )
        elif after_unresolved < before_unresolved:
            improvements.append(
                f"unresolved_count decreased: {before_unresolved} -> {after_unresolved}"
            )
    before_gates = _passed_gates(before.get("validation"))
    after_gates = _passed_gates(after.get("validation"))
    for gate, before_value in sorted(before_gates.items()):
        after_value = after_gates.get(gate)
        if before_value is True and after_value is not True:
            regressions.append(f"previously passing validation gate regressed: {gate}")
        elif before_value is False and after_value is True:
            improvements.append(f"validation gate improved: {gate}")
    return {
        "regressions": regressions,
        "improvements": improvements,
        "eligible_for_acceptance": not regressions,
        "automatic_promotion": False,
        "user_visual_and_language_verdict_required": True,
    }


def _changed_artifacts(values: Sequence[str | Path]) -> list[dict[str, Any]]:
    if len(values) > 32:
        raise IterationError("changed_artifacts accepts at most 32 files")
    result: list[dict[str, Any]] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise IterationError(f"Changed artifact does not exist: {path}")
        result.append({"path": str(path), "sha256": _file_sha256(path)})
    return result


def evaluate_iteration_candidate(
    value: str | Path,
    candidate_run_dir: str | Path,
    *,
    addressed_feedback_ids: Sequence[str],
    change_summary: str,
    changed_artifacts: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Compare a new immutable run with the active run and persist the audit."""

    path, session = _load_session(value)
    pending = [item for item in session["candidates"] if item["verdict"] == "pending"]
    if pending:
        raise IterationError("Decide the pending candidate before evaluating another")
    budget = session["iteration_budget"]
    if budget["used"] >= budget["max_iterations"]:
        raise IterationError("Iteration budget is exhausted")
    open_ids = {
        item["feedback_id"] for item in session["feedback"] if item["status"] == "open"
    }
    addressed = _normalize_string_list(
        list(addressed_feedback_ids),
        "addressed_feedback_ids",
        maximum=_MAX_FEEDBACK_ITEMS,
    )
    if not addressed:
        raise IterationError("A candidate must address at least one open feedback item")
    missing = sorted(set(addressed) - open_ids)
    if missing:
        raise IterationError(f"Candidate references non-open feedback IDs: {missing}")
    summary = _bounded_text(change_summary, "change_summary", maximum=8000)
    candidate = _run_snapshot(candidate_run_dir)
    if candidate["run_dir"] == session["active_run"]["run_dir"]:
        raise IterationError("Candidate run must use a new immutable run directory")
    comparison = _compare_runs(session["active_run"], candidate)
    ordinal = budget["used"] + 1
    candidate_id = f"candidate_{ordinal}_{candidate['manifest_sha256'][:12]}"
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "candidate_id": candidate_id,
        "created_at": _now(),
        "source_sha256": session["source_sha256"],
        "before": session["active_run"],
        "after": candidate,
        "addressed_feedback_ids": addressed,
        "change_summary": summary,
        "changed_artifacts": _changed_artifacts(changed_artifacts),
        "comparison": comparison,
    }
    evaluation["evaluation_sha256"] = _digest(evaluation)
    evaluation_path = path.parent / "evaluations" / f"{candidate_id}.json"
    _write_json_atomic(evaluation_path, evaluation)
    session["candidates"].append(
        {
            "candidate_id": candidate_id,
            "run": candidate,
            "evaluation_path": str(evaluation_path),
            "evaluation_sha256": _file_sha256(evaluation_path),
            "addressed_feedback_ids": addressed,
            "change_summary": summary,
            "eligible_for_acceptance": comparison["eligible_for_acceptance"],
            "verdict": "pending",
        }
    )
    budget["used"] = ordinal
    session["status"] = "awaiting_decision"
    sealed = _write_session(path, session)
    return {
        "session": _summary(path, sealed),
        "evaluation": evaluation,
        "evaluation_path": str(evaluation_path),
    }


def _learning_artifact(
    session_path: Path,
    session: Mapping[str, Any],
    candidate: Mapping[str, Any],
    rationale: str,
) -> dict[str, Any]:
    feedback_by_id = {item["feedback_id"]: item for item in session["feedback"]}
    evidence = []
    for feedback_id in candidate["addressed_feedback_ids"]:
        feedback = feedback_by_id[feedback_id]
        evidence.append(
            {
                "feedback_id": feedback_id,
                "category": feedback["category"],
                "observation": feedback["observation"],
                "expected_outcome": feedback["expected_outcome"],
                "visual_sha256": sorted(
                    {
                        str(ref.get("sha256") or ref.get("render_sha256"))
                        for ref in feedback["visual_refs"]
                        if ref.get("sha256") or ref.get("render_sha256")
                    }
                ),
                "evidence_node_ids": feedback["evidence_node_ids"],
            }
        )
    lesson = {
        "schema_version": LEARNING_SCHEMA,
        "scope": "source_bound_suggestion_only",
        "source_sha256": session["source_sha256"],
        "candidate_id": candidate["candidate_id"],
        "accepted_at": _now(),
        "change_summary": candidate["change_summary"],
        "acceptance_rationale": rationale,
        "feedback_evidence": evidence,
        "before_manifest_sha256": session["active_run"]["manifest_sha256"],
        "after_manifest_sha256": candidate["run"]["manifest_sha256"],
        "authority": {
            "automatic_application": False,
            "may_inform_future_onboarding": True,
            "cross_source_generalization": False,
        },
    }
    lesson["lesson_id"] = "lesson_" + _digest(lesson)[:20]
    lesson["lesson_sha256"] = _digest(lesson)
    learning_path = session_path.parent / "learning" / f"{lesson['lesson_id']}.json"
    _write_json_atomic(learning_path, lesson)
    return {
        "lesson_id": lesson["lesson_id"],
        "path": str(learning_path),
        "sha256": _file_sha256(learning_path),
    }


def decide_iteration_candidate(
    value: str | Path,
    candidate_id: str,
    *,
    verdict: str,
    rationale: str,
    user_confirmed: bool = False,
) -> dict[str, Any]:
    """Accept, reject, or revise a candidate; acceptance is never implicit."""

    path, session = _load_session(value)
    decision = str(verdict).strip().lower()
    if decision not in {"accept", "reject", "revise"}:
        raise IterationError("verdict must be accept, reject, or revise")
    reason = _bounded_text(rationale, "rationale", maximum=8000)
    matches = [
        item for item in session["candidates"] if item["candidate_id"] == candidate_id
    ]
    if len(matches) != 1:
        raise IterationError(f"Unknown iteration candidate: {candidate_id}")
    candidate = matches[0]
    if candidate["verdict"] != "pending":
        raise IterationError(f"Candidate already has verdict {candidate['verdict']}")
    current = _run_snapshot(candidate["run"]["run_dir"])
    if current["manifest_sha256"] != candidate["run"]["manifest_sha256"]:
        raise IterationError("Candidate run manifest changed after evaluation")
    learning = None
    if decision == "accept":
        if user_confirmed is not True:
            raise IterationError("accept requires explicit user_confirmed=true")
        if candidate["eligible_for_acceptance"] is not True:
            raise IterationError(
                "Candidate has deterministic regressions and cannot be accepted"
            )
        learning = _learning_artifact(path, session, candidate, reason)
        session["learning_artifacts"].append(learning)
        session["active_run"] = candidate["run"]
        addressed = set(candidate["addressed_feedback_ids"])
        for feedback in session["feedback"]:
            if feedback["feedback_id"] in addressed:
                feedback["status"] = "resolved"
                feedback["resolution"] = {
                    "candidate_id": candidate_id,
                    "accepted_at": _now(),
                }
        session["status"] = (
            "accepted"
            if not any(item["status"] == "open" for item in session["feedback"])
            else "collecting_feedback"
        )
    else:
        session["status"] = (
            "exhausted"
            if session["iteration_budget"]["used"]
            >= session["iteration_budget"]["max_iterations"]
            else "collecting_feedback"
        )
    candidate["verdict"] = decision
    candidate["decision"] = {
        "decided_at": _now(),
        "rationale": reason,
        "user_confirmed": bool(user_confirmed),
    }
    session["last_context"] = None
    sealed = _write_session(path, session)
    return {
        "session": _summary(path, sealed),
        "candidate_id": candidate_id,
        "verdict": decision,
        "learning": learning,
    }


def export_iteration_learning(
    value: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Merge accepted source-bound lessons into a suggestions-only registry."""

    session_path, session = _load_session(value)
    if not session["learning_artifacts"]:
        raise IterationError("No accepted learning artifacts are available")
    destination = Path(output_path).expanduser().resolve()
    lessons: list[dict[str, Any]] = []
    for reference in session["learning_artifacts"]:
        lesson_path = Path(reference["path"])
        if _file_sha256(lesson_path) != reference["sha256"]:
            raise IterationError("Accepted learning artifact digest mismatch")
        lessons.append(_read_json(lesson_path))
    if destination.exists():
        registry = _read_json(destination, max_bytes=16 * 1024 * 1024)
        if registry.get("schema_version") != LEARNING_REGISTRY_SCHEMA:
            raise IterationError("Unsupported existing learning registry schema")
        if registry.get("source_sha256") != session["source_sha256"]:
            raise IterationError("Learning registry belongs to a different source DWG")
        existing = {
            item.get("lesson_id"): item
            for item in registry.get("lessons", [])
            if isinstance(item, dict) and item.get("lesson_id")
        }
    else:
        registry = {
            "schema_version": LEARNING_REGISTRY_SCHEMA,
            "scope": "source_bound_suggestion_only",
            "source_sha256": session["source_sha256"],
            "created_at": _now(),
            "lessons": [],
            "authority": {
                "automatic_application": False,
                "cross_source_generalization": False,
                "source_geometry_writable": False,
            },
        }
        existing = {}
    for lesson in lessons:
        existing[lesson["lesson_id"]] = lesson
    registry["lessons"] = [existing[key] for key in sorted(existing)]
    registry["updated_at"] = _now()
    registry["registry_sha256"] = _digest(
        {key: value for key, value in registry.items() if key != "registry_sha256"}
    )
    _write_json_atomic(destination, registry)
    return {
        "schema_version": LEARNING_REGISTRY_SCHEMA,
        "path": str(destination),
        "sha256": _file_sha256(destination),
        "source_sha256": session["source_sha256"],
        "lesson_count": len(registry["lessons"]),
        "mode": "suggestions_only",
        "session_path": str(session_path),
    }


def learning_context_for_bundle(
    registry_path: str | Path,
    onboarding_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a registry and return compact source-bound onboarding context."""

    path = Path(registry_path).expanduser().resolve()
    registry = _read_json(path, max_bytes=16 * 1024 * 1024)
    if registry.get("schema_version") != LEARNING_REGISTRY_SCHEMA:
        raise IterationError("Unsupported learning registry schema")
    expected_registry_sha = registry.get("registry_sha256")
    unsigned_registry = {
        key: value for key, value in registry.items() if key != "registry_sha256"
    }
    if (
        not isinstance(expected_registry_sha, str)
        or _digest(unsigned_registry) != expected_registry_sha
    ):
        raise IterationError("Learning registry content digest mismatch")
    source = onboarding_bundle.get("source")
    source_sha256 = source.get("sha256") if isinstance(source, dict) else None
    if registry.get("source_sha256") != source_sha256:
        raise IterationError(
            "Learning registry does not match onboarding source SHA-256"
        )
    lessons = registry.get("lessons")
    if not isinstance(lessons, list):
        raise IterationError("Learning registry lessons must be an array")
    return {
        "schema_version": LEARNING_REGISTRY_SCHEMA,
        "registry_path": str(path),
        "registry_sha256": _file_sha256(path),
        "mode": "source_bound_suggestions_only",
        "automatic_application": False,
        "lessons": lessons,
    }


__all__ = [
    "IterationError",
    "decide_iteration_candidate",
    "evaluate_iteration_candidate",
    "export_iteration_learning",
    "inspect_iteration",
    "learning_context_for_bundle",
    "prepare_iteration_context",
    "record_iteration_feedback",
    "start_feedback_iteration",
]
