"""Read-only MCP identity diagnostics and content-minimal call tracing."""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
from functools import wraps
import hashlib
from importlib.metadata import PackageNotFoundError, version
import inspect
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any, Callable
import uuid


TRACE_SCHEMA = "cad2gis.mcp_trace.v1"
DIAGNOSTIC_SCHEMA = "cad2gis.debug_mcp.v1"
_TRACE_LOCK = threading.Lock()
_MUTATING = frozenset({
    "install_runtime", "bootstrap_project", "apply_ai_onboarding",
    "auto_onboard_and_convert", "run_conversion", "create_decision_pack",
    "prepare_review_workspace", "start_feedback_iteration",
    "record_iteration_feedback", "evaluate_iteration_candidate",
    "decide_iteration_candidate", "export_iteration_learning",
    "export_source", "prepare_semantic_batches", "commit_semantic_patch",
    "compile_semantic_revision", "initialize_semantic_store",
    "cancel_compile_job", "reconcile_compile_jobs",
})


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode()).hexdigest()


def _version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _code_identity() -> dict[str, Any]:
    package = Path(__file__).resolve().parent
    files = {
        path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package.rglob("*.py"))
    }
    checkout = package.parent.parent
    commit = _checkout_commit(checkout)
    return {
        "package_path": str(package), "checkout_commit": commit,
        "source_files_sha256": _digest(files), "source_file_count": len(files),
    }


def _checkout_commit(checkout: Path) -> str | None:
    # Spawning Git inside a Windows stdio session can inherit pipe handles and
    # hang even during subprocess timeout cleanup. HEAD is read-only metadata;
    # the source content digest above remains the implementation authority.
    try:
        gitdir = checkout / ".git"
        if gitdir.is_file():
            value = gitdir.read_text(encoding="utf-8").strip()
            if not value.startswith("gitdir: "):
                return None
            gitdir = (checkout / value[8:]).resolve()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        if head.startswith("ref: refs/"):
            ref = head[5:]
            if ".." in Path(ref).parts:
                return None
            ref_path = common / ref
            if ref_path.is_file():
                head = ref_path.read_text(encoding="utf-8").strip()
            else:
                head = next((line.split()[0] for line in (common / "packed-refs").read_text(encoding="utf-8").splitlines()
                             if not line.startswith(("#", "^")) and line.endswith(" " + ref)), "")
        return head if len(head) in {40, 64} and all(c in "0123456789abcdef" for c in head) else None
    except (OSError, ValueError):
        return None


def _plugin_manifests() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2] / "plugins" / "cad2gis-agent"
    if not root.is_dir():
        return {"status": "not_packaged_with_runtime", "manifests": {}}
    manifests = {}
    for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        try:
            raw = (root / relative).read_bytes()
            value = json.loads(raw)
            manifests[relative] = {"version": value.get("version"), "sha256": hashlib.sha256(raw).hexdigest()}
        except (OSError, ValueError, AttributeError):
            manifests[relative] = {"version": None, "status": "missing_or_invalid"}
    return {"status": "available", "manifests": manifests}


def runtime_identity(tool_schemas: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe actual import locations and schemas without opening a drawing."""
    from . import __version__, runtime
    from .contracts import (
        AGENT_PROMPT_CONTRACT_VERSION, PLUGIN_CONTRACT_VERSION,
        SKILL_CONTRACT_VERSION, mcp_tool_contract,
    )

    schemas = sorted(tool_schemas, key=lambda item: item["name"])
    bundle = Path(__file__).resolve().parents[2] / "plugins" / "cad2gis-agent" / "skills" / "convert-cad-to-gis"
    prompt_files = ("SKILL.md", "agents/openai.yaml", "references/agent-prompt-contract.md")
    bundled_prompts = {}
    for relative in prompt_files:
        path = bundle / relative
        if path.is_file():
            contents = path.read_bytes()
            bundled_prompts[relative] = {
                "sha256": hashlib.sha256(contents).hexdigest(),
                "declares_current_prompt": AGENT_PROMPT_CONTRACT_VERSION in contents.decode("utf-8"),
            }
    identity = {
        **_code_identity(), "python_executable": sys.executable,
        "python_version": sys.version.split()[0], "package_version": __version__,
        "plugin_version": PLUGIN_CONTRACT_VERSION,
        "plugin_bundle": _plugin_manifests(),
        "skill_contract_version": SKILL_CONTRACT_VERSION,
        "prompt_contract_version": AGENT_PROMPT_CONTRACT_VERSION,
        "mcp_sdk_version": _version("mcp"),
        "tool_contract": mcp_tool_contract(),
        "tools_schema_sha256": _digest(schemas),
        "tool_count": len(schemas),
        "backend": dict(runtime.backend_contract()),
        "bundled_prompts": bundled_prompts,
        "bundled_prompts_sha256": _digest(bundled_prompts) if bundled_prompts else None,
        "host_loaded_skill": "not_observable_by_server; compare client bundle digest explicitly",
    }
    identity["identity_digest"] = _digest(identity)
    return identity


def _sqlite_capabilities() -> dict[str, Any]:
    with closing(sqlite3.connect(":memory:")) as connection:
        options = [row[0] for row in connection.execute("PRAGMA compile_options")]
        return {
            "version": sqlite3.sqlite_version,
            "source_id": connection.execute("SELECT sqlite_source_id()").fetchone()[0],
            "fts5": "ENABLE_FTS5" in options, "rtree": "ENABLE_RTREE" in options,
        }


def diagnose(
    *, scope: str, tool_schemas: list[dict[str, Any]],
    expected_identity: dict[str, str] | None = None,
    graph_path: str = "", run_dir: str = "",
) -> dict[str, Any]:
    """Read bounded diagnostics; paths are validated again at the MCP boundary."""
    from . import agent_mcp

    if scope not in {"identity", "runtime", "artifacts", "query"}:
        raise ValueError("scope must be identity, runtime, artifacts or query")
    identity = runtime_identity(tool_schemas)
    expected = expected_identity or {}
    allowed = {
        "package_version", "plugin_version", "skill_contract_version",
        "prompt_contract_version", "source_files_sha256", "tools_schema_sha256",
        "mcp_sdk_version", "checkout_commit", "bundled_prompts_sha256",
    }
    if set(expected) - allowed or any(not isinstance(v, str) for v in expected.values()):
        raise ValueError("expected_identity contains unsupported fields")
    differences = [
        {"field": key, "expected": value, "actual": identity.get(key)}
        for key, value in sorted(expected.items()) if identity.get(key) != value
    ]
    for path, detail in identity["bundled_prompts"].items():
        if not detail["declares_current_prompt"]:
            differences.append({"field": f"bundled_prompts/{path}", "expected": "current_prompt", "actual": "missing_current_prompt"})
    if identity["package_version"] != identity["plugin_version"]:
        differences.append({"field": "plugin_version", "expected": identity["package_version"], "actual": identity["plugin_version"]})
    if identity["plugin_bundle"]["status"] == "available":
        for name, manifest in identity["plugin_bundle"]["manifests"].items():
            if manifest.get("version") != identity["plugin_version"]:
                differences.append({"field": name, "expected": identity["plugin_version"], "actual": manifest.get("version")})
        if len(identity["bundled_prompts"]) != 3:
            differences.append({"field": "bundled_prompts", "expected": "3 packaged files", "actual": len(identity["bundled_prompts"])})
    report: dict[str, Any] = {
        "schema_version": DIAGNOSTIC_SCHEMA,
        "status": "VERSION_DRIFT" if differences else "ok", "scope": scope,
        "identity": identity, "identity_differences": differences,
        "filesystem_roots": [str(path) for path in agent_mcp._roots()],
        "protocol_version": None,
        "protocol_note": "Negotiated by the client session; SDK version is not a protocol claim.",
        "sqlite": _sqlite_capabilities(),
        "redis": {"status": "not_configured", "required": False},
        "read_only": True,
    }
    if scope == "runtime":
        report["runtime"] = agent_mcp.get_runtime_status()
    elif scope == "artifacts":
        if not run_dir:
            raise ValueError("run_dir is required for artifacts scope")
        directory = agent_mcp._path(run_dir)
        if (directory / "source_manifest.json").is_file():
            from .cad2gis_v3.source_query import validate_source_snapshot
            _, manifest = validate_source_snapshot(directory)
            report["artifacts"] = {
                "status": "verified", "snapshot_sha256": manifest["snapshot_sha256"],
                "source_sha256": manifest["source"]["sha256"],
                "verified_artifacts": list(manifest["artifacts"]),
                "scope": "snapshot_and_artifact_byte_hashes",
            }
        else:
            report["artifacts"] = agent_mcp.audit_run(run_dir)
    elif scope == "query":
        if not graph_path and not run_dir:
            raise ValueError("graph_path or source run_dir is required for query scope")
        started = time.perf_counter()
        if run_dir:
            from .cad2gis_v3.source_query import source_index_path
            directory = agent_mcp._path(run_dir)
            if not source_index_path(directory).is_file():
                report["query"] = {"status": "index_not_built", "executed": False,
                                   "recovery": "Use query_source_entities to build the derived index explicitly."}
                return report
            result = agent_mcp.query_source_entities(run_dir, limit=1)
            report["query"] = {**result["metadata"], "returned_count": result["returned_count"]}
        else:
            result = agent_mcp.list_evidence_nodes(graph_path, limit=1)
            report["query"] = {
                key: result.get(key) for key in
                ("graph_sha256", "source_sha256", "query_backend", "total")
            }
        report["query"]["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return report


def error_code(exc: BaseException) -> str:
    """Map known failures to stable codes without exporting exception contents."""
    declared = getattr(exc, "code", None)
    if isinstance(declared, str) and declared:
        return declared
    name = type(exc).__name__
    if name == "EvidenceIndexError":
        return "ARTIFACT_BINDING_INVALID"
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        return "CANCELLED"
    if isinstance(exc, TimeoutError):
        return "QUERY_BUDGET_EXCEEDED"
    if isinstance(exc, sqlite3.OperationalError):
        return "DATABASE_BUSY" if "locked" in str(exc).lower() else "DATABASE_ERROR"
    if isinstance(exc, FileExistsError):
        return "OUTPUT_EXISTS"
    if isinstance(exc, FileNotFoundError):
        return "ARTIFACT_NOT_FOUND"
    if isinstance(exc, ValueError):
        return "VALIDATION_FAILED"
    return "EXECUTION_FAILED"


def _emit(event: dict[str, Any]) -> None:
    # No raw tool arguments, CAD text, provider response or exception messages.
    try:
        line = json.dumps(event, ensure_ascii=True, allow_nan=False)
        with _TRACE_LOCK:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
    except (OSError, ValueError):
        # Diagnostics cannot change a committed business outcome.
        pass


def database_tool(name: str, handler: Callable[..., Any]) -> Callable[..., Any]:
    """Keep MCP responsive while bounded queries or durable compile jobs run.

    Cancellation interrupts queries. A cancelled write RPC leaves its worker
    outcome unknown; the client uses the durable idempotency/status API.
    """
    query_names = {"query_source_entities", "get_entity_context_batch"}
    bounded_names = query_names | {"query_relationship_candidates"}

    @wraps(handler)
    async def call(*args: Any, **kwargs: Any) -> Any:
        cancel = threading.Event()
        # Reserve space for the MCP envelope and its structured + text forms.
        # Both forms carry identical compact JSON for older and newer clients.
        budget = kwargs.get("max_bytes", 65536)
        if name in bounded_names:
            if isinstance(budget, bool) or not isinstance(budget, int) or not 8192 <= budget <= 65536:
                raise ValueError("MCP max_bytes must be 8192..65536 including protocol overhead")
            kwargs["max_bytes"] = (budget - 2048) // 3

        def run() -> Any:
            if name in query_names:
                from .cad2gis_v3.source_query import query_cancellation
                with query_cancellation(cancel):
                    return handler(*args, **kwargs)
            return handler(*args, **kwargs)

        try:
            result = await asyncio.to_thread(run)
        except asyncio.CancelledError:
            cancel.set()
            raise
        if name in {
            "export_source", "prepare_semantic_batches", "initialize_semantic_store",
            "commit_semantic_patch", "compile_semantic_revision",
            "cancel_compile_job", "reconcile_compile_jobs",
        } and isinstance(result, dict):
            # Successful calls return a durable artifact/revision/job receipt.
            # This does not assert that a candidate was accepted as delivery.
            result = {**result, "committed": True}
        if name in bounded_names:
            from mcp.types import CallToolResult, TextContent
            content = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            output = CallToolResult(content=[TextContent(type="text", text=content)], structuredContent=result)
            if len(output.model_dump_json(exclude_none=True).encode()) > budget - 128:
                raise ValueError("MCP response exceeded its protocol byte budget")
            return output
        return result
    return call


def traced_tool(name: str, handler: Callable[..., Any], server: Any,
                identity_digest: str = "") -> Callable[..., Any]:
    """Preserve the tool's real signature while emitting one terminal event."""
    def begin() -> tuple[dict[str, Any], float]:
        request_id = None
        try:
            request_id = str(server.get_context().request_id)
        except (AttributeError, LookupError, ValueError):
            pass
        event = {
            "schema_version": TRACE_SCHEMA, "trace_id": uuid.uuid4().hex,
            "request_id": request_id, "tool_name": name,
            "identity_digest": identity_digest,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _emit({**event, "phase": "started"})
        return event, time.perf_counter()

    def finish(event: dict[str, Any], start: float, result: Any = None,
               failure: BaseException | None = None) -> None:
        terminal = {
            **event, "phase": "succeeded" if failure is None else
            ("cancelled" if error_code(failure) == "CANCELLED" else "failed"),
            "duration_ms": round((time.perf_counter() - start) * 1000, 3),
            "error_code": error_code(failure) if failure else None,
            "committed": "unknown" if name in _MUTATING else False,
        }
        if failure is None and hasattr(result, "structuredContent"):
            result = result.structuredContent
        if failure is None and isinstance(result, dict):
            terminal["response_bytes"] = len(json.dumps(result, default=str).encode())
            metadata = result.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            for key in ("source_sha256", "snapshot_id", "snapshot_sha256", "index_sha256",
                        "revision", "patch_sha256", "query_backend", "job_id", "returned_count"):
                value = result.get(key, metadata.get(key))
                if isinstance(value, (str, int)):
                    terminal[key] = value
            if isinstance(result.get("committed"), bool):
                terminal["committed"] = result["committed"]
        _emit(terminal)

    def tool_failure(exc: BaseException, event: dict[str, Any]) -> BaseException:
        if not isinstance(exc, Exception):
            return exc
        from mcp.server.fastmcp.exceptions import ToolError
        return ToolError(json.dumps({
            "error_code": error_code(exc), "trace_id": event["trace_id"],
            "retryable": error_code(exc) in {"DATABASE_BUSY", "QUERY_BUDGET_EXCEEDED"},
            "committed": "unknown" if name in _MUTATING else False,
            "recovery": "Inspect the operation status before retrying a mutation.",
        }))

    if inspect.iscoroutinefunction(handler):
        @wraps(handler)
        async def async_call(*args: Any, **kwargs: Any) -> Any:
            event, started = begin()
            try:
                result = await handler(*args, **kwargs)
            except BaseException as exc:
                finish(event, started, failure=exc)
                raise tool_failure(exc, event) from None
            finish(event, started, result)
            return result
        return async_call

    @wraps(handler)
    def sync_call(*args: Any, **kwargs: Any) -> Any:
        event, started = begin()
        try:
            result = handler(*args, **kwargs)
        except BaseException as exc:
            finish(event, started, failure=exc)
            raise tool_failure(exc, event) from None
        finish(event, started, result)
        return result
    return sync_call
