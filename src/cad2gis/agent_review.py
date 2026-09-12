"""Owned local review processes over the canonical review application."""

import argparse
import atexit
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.request
import uuid


_STARTUP_TIMEOUT = 30.0
_LOCK = threading.RLock()
_SERVERS: dict[str, "_OwnedReview"] = {}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class _OwnedReview:
    session_id: str
    process: subprocess.Popen
    binding: dict[str, Any]
    workspace: Path
    port: int
    log_path: Path
    owner_token: str = field(repr=False)
    stopping: bool = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _binding(run: Path) -> dict[str, Any]:
    from .agent_mcp import MCPServiceError, _json_object, _path

    manifest_path = run / "run_manifest.json"
    partition_path = run / "partition_manifest.json"
    partition = ""
    if not manifest_path.is_file():
        if not partition_path.is_file():
            raise MCPServiceError("Selected directory has no run or partition manifest")
        manifest_path = _path(run.parent / "run_manifest.json")
        partition = str(_json_object(_path(partition_path)).get("region_id", ""))
        if partition != run.name:
            raise MCPServiceError("Partition manifest does not identify the selected directory")
    manifest_path = _path(manifest_path)
    manifest = _json_object(manifest_path)
    partitions = manifest.get("delivery_partitions") or {}
    if partition and (not isinstance(partitions, dict) or not isinstance(partitions.get(partition), dict)):
        raise MCPServiceError("Partition is not bound to the parent run manifest")
    source = manifest.get("source")
    source_sha = str(source.get("sha256", "")) if isinstance(source, dict) else ""
    if re.fullmatch(r"[a-f0-9]{64}", source_sha) is None:
        raise MCPServiceError("Run manifest has no valid source SHA-256")
    artifacts = manifest.get("artifacts")
    artifact = partitions[partition] if partition else artifacts.get("delivery") if isinstance(artifacts, dict) else None
    delivery_sha = _sha(_path(run / "delivery.gpkg"))
    if not isinstance(artifact, dict) or artifact.get("sha256") != delivery_sha:
        raise MCPServiceError("Selected delivery does not match its authoritative manifest", code="ARTIFACT_BINDING_INVALID")
    counts = (partitions[partition] if partition else manifest).get("delivery_counts")
    if (not isinstance(counts, dict) or not counts or any(
            not isinstance(name, str) or not name or type(count) is not int or count < 0
            for name, count in counts.items())):
        raise MCPServiceError("Selected run has no valid authoritative layer counts", code="ARTIFACT_BINDING_INVALID")
    return {
        "run_dir": str(run), "source_sha256": source_sha,
        "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
        "partition": partition,
        "partition_manifest_sha256": _sha(partition_path) if partition else None,
        "delivery_sha256": delivery_sha,
        "delivery_counts": counts,
    }


def _json_url(url: str, *, owner_token: str = "") -> Any:
    request = urllib.request.Request(url, headers={"X-CAD2GIS-Review-Owner": owner_token} if owner_token else {})
    with _OPENER.open(request, timeout=2) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Review identity response exceeds its byte budget")
    return json.loads(raw)


def _probe(record: _OwnedReview) -> dict[str, Any]:
    from .agent_mcp import MCPServiceError

    identity = _json_url(f"{record.url}/api/agent-review", owner_token=record.owner_token)
    health = _json_url(f"{record.url}/api/health")
    current = _json_url(f"{record.url}/api/run")
    if (not isinstance(identity, dict) or identity.get("session_id") != record.session_id
            or identity.get("binding") != record.binding
            or _binding(Path(record.binding["run_dir"])) != record.binding
            or not isinstance(health, dict) or health.get("status") != "ok"
            or health.get("schema_version") != "cad2gis.review_workspace.v1"
            or not isinstance(current, dict)
            or current.get("run_dir") != record.binding["run_dir"]
            or current.get("workspace_dir") != str(record.workspace)
            or (current.get("source") or {}).get("sha256") != record.binding["source_sha256"]
            or (current.get("delivery_partition") or {}).get("region_id", "") != record.binding["partition"]):
        raise MCPServiceError("Review service does not display the requested immutable run identity",
                              code="ARTIFACT_BINDING_INVALID")
    # A live HTML/health response alone cannot prove the GIS runtime opened its data.
    layers = _json_url(f"{record.url}/api/layers")
    rows = layers.get("layers") if isinstance(layers, dict) else None
    if (not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str)
            or type(row.get("feature_count")) is not int or row["feature_count"] < 0
            for row in rows)
            or len({row["name"] for row in rows}) != len(rows)
            or {row["name"]: row["feature_count"] for row in rows} != record.binding["delivery_counts"]
            or current.get("delivery_counts") != record.binding["delivery_counts"]):
        raise MCPServiceError("Review GIS layers do not match the authoritative delivery counts",
                              code="ARTIFACT_BINDING_INVALID")
    if record.process.poll() is not None:
        raise MCPServiceError("Owned review process exited during verification", code="REVIEW_START_FAILED")
    return {
        "schema_version": "cad2gis.agent_review_server.v1", "status": "ready",
        "session_id": record.session_id, "review_session_id": health.get("session_id"),
        "url": record.url, "pid": record.process.pid,
        **record.binding, "workspace_dir": str(record.workspace), "log_path": str(record.log_path),
        "run_status": current.get("run_status"), "svg_candidates": current.get("svg_candidates"),
        "identity_verified": True, "layers_available": True,
        "browser_opened": False, "immutable_delivery": True,
        "lifetime": "owned by the current MCP process; stop before that process exits",
    }


def _stop(record: _OwnedReview, *, failed_start: bool = False) -> None:
    record.stopping = True
    process = record.process
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if not failed_start:
            return  # Uvicorn finishes in-flight confirmed review actions before exiting.
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _shutdown_owned() -> None:
    for record in list(_SERVERS.values()):
        try:
            if record.process.stdin is not None and not record.process.stdin.closed:
                record.process.stdin.close()
        except OSError:
            pass


atexit.register(_shutdown_owned)


def start_review_server(run_dir: str, workspace_dir: str = "", port: int = 8765) -> dict[str, Any]:
    """Start and verify an owned loopback review service; this does not open a browser."""
    from .agent_mcp import MCPServiceError, _path

    run = _path(run_dir)
    if not run.is_dir():
        raise MCPServiceError("run_dir must be a conversion or partition directory")
    if type(port) is not int or not 1 <= port <= 65535:
        raise MCPServiceError("port must be an integer between 1 and 65535")
    binding = _binding(run)
    immutable_root = Path(binding["manifest_path"]).parent
    default_name = immutable_root.name + (f"-{binding['partition']}" if binding["partition"] else "") + ".review"
    workspace = _path(workspace_dir or immutable_root.parent / default_name, must_exist=False)
    if workspace.is_relative_to(immutable_root):
        raise MCPServiceError("Review workspace must remain outside the immutable parent run")
    _require_mutable_path(workspace)
    if workspace.exists() and not workspace.is_dir():
        raise MCPServiceError("workspace_dir must be a directory")
    with _LOCK:
        for existing in _SERVERS.values():
            if existing.process.poll() is None and existing.port == port:
                if existing.stopping:
                    raise MCPServiceError("Review service is completing its graceful shutdown", code="REVIEW_STOPPING")
                if existing.binding != binding or existing.workspace != workspace:
                    raise MCPServiceError("Port is owned by a different review run", code="REVIEW_PORT_IN_USE")
                return {**_probe(existing), "reused": True}
        # Never adopt a listener not created by this process. The private child
        # identity endpoint also closes the bind/start race after this probe.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise MCPServiceError(f"Local review port {port} is already occupied",
                                      code="REVIEW_PORT_IN_USE") from exc
        session_id = "review-" + uuid.uuid4().hex
        owner_token = secrets.token_urlsafe(32)
        log_path = _path(workspace / ".agent-review" / session_id / "server.log", must_exist=False)
        _require_mutable_path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=False)
        hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        command = [sys.executable, "-I", "-B", "-u", "-X", "utf8", "-m", "cad2gis.agent_review",
                   "--serve", str(run), "--workspace", str(workspace), "--port", str(port),
                   "--session-id", session_id]
        try:
            with log_path.open("xb") as log:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                           cwd=str(workspace),
                                           env={**os.environ, "CAD2GIS_AGENT_REVIEW_TOKEN": owner_token}, **hidden)
        except OSError as exc:
            failure = MCPServiceError(f"Cannot start review service; inspect {log_path}", code="REVIEW_START_FAILED")
            failure.review_recovery = {"session_id": session_id, "log_path": str(log_path)}
            raise failure from exc
        record = _OwnedReview(session_id, process, binding, workspace, port, log_path, owner_token)
        _SERVERS[session_id] = record
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise MCPServiceError(f"Review process exited; inspect {log_path}", code="REVIEW_START_FAILED")
                try:
                    return {**_probe(record), "reused": False}
                except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
                    time.sleep(0.1)
            raise MCPServiceError(f"Review startup timed out; inspect {log_path}", code="REVIEW_START_FAILED")
        except BaseException as exc:
            _stop(record, failed_start=True)
            if isinstance(exc, MCPServiceError):
                exc.review_recovery = {"session_id": session_id, "log_path": str(log_path)}
            raise


def _require_mutable_path(path: Path) -> None:
    from .agent_mcp import MCPServiceError

    for parent in (path, *path.parents):
        if any((parent / name).is_file() for name in ("run_manifest.json", "source_manifest.json", "partition_manifest.json")):
            raise MCPServiceError("Review output must be outside immutable conversion and source runs")


def get_review_server_status(session_id: str) -> dict[str, Any]:
    """Verify a service owned by this MCP process; never discover or adopt another listener."""
    from .agent_mcp import MCPServiceError

    with _LOCK:
        record = _SERVERS.get(session_id)
        if record is None:
            raise MCPServiceError("Unknown review session; only this process's owned sessions are addressable")
        if record.process.poll() is not None:
            return {"schema_version": "cad2gis.agent_review_server.v1", "status": "stopped",
                    "session_id": session_id, "exit_code": record.process.returncode,
                    "log_path": str(record.log_path), "identity_verified": False}
        if record.stopping:
            return {"schema_version": "cad2gis.agent_review_server.v1", "status": "stopping",
                    "session_id": session_id, "log_path": str(record.log_path), "identity_verified": False}
        return _probe(record)


def stop_review_server(session_id: str) -> dict[str, Any]:
    """Stop only the subprocess owned by this MCP session; never accept an arbitrary PID."""
    from .agent_mcp import MCPServiceError

    with _LOCK:
        record = _SERVERS.get(session_id)
        if record is None:
            raise MCPServiceError("Unknown review session; no process was stopped")
        _stop(record)
        return get_review_server_status(session_id)


def _serve(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args(argv)
    if re.fullmatch(r"review-[a-f0-9]{32}", args.session_id) is None or not 1 <= args.port <= 65535:
        parser.error("Invalid owned review session or port")
    from .review_server import create_review_app
    from fastapi import HTTPException, Request
    import uvicorn

    run = args.serve.resolve()
    before = _binding(run)
    app = create_review_app(run, workspace_dir=args.workspace)
    if _binding(run) != before:
        raise ValueError("Run binding changed during review startup")

    owner_token = os.environ.pop("CAD2GIS_AGENT_REVIEW_TOKEN", "")
    if not owner_token:
        raise ValueError("Owned review service requires a private parent token")

    @app.get("/api/agent-review", include_in_schema=False)
    def identity(request: Request):
        if not secrets.compare_digest(request.headers.get("X-CAD2GIS-Review-Owner", ""), owner_token):
            raise HTTPException(status_code=404, detail="Not found")
        return {"session_id": args.session_id, "binding": _binding(run)}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                           log_level="warning", access_log=False, proxy_headers=False))

    def parent_closed():
        # The anonymous pipe closes on normal exit and crashes on Windows/Linux.
        # No PID lookup, process-name guessing, or unrelated process termination.
        sys.stdin.buffer.read(1)
        server.should_exit = True

    threading.Thread(target=parent_closed, daemon=True).start()
    server.run()


if __name__ == "__main__":
    _serve()
