"""Background conversion jobs for MCP clients.

Large drawings can convert for several minutes, longer than many MCP clients
wait for one tool call. ``start_conversion_job`` validates paths, starts the
canonical ``run_conversion`` in a separate process and returns at once; the
client polls ``get_conversion_job`` and may ``cancel_conversion_job``.

The job directory sits beside the requested run directory
(``<run_dir>.job``) and holds ``job.json`` (atomically replaced) and
``worker.log``. Conversion publication is already atomic, so a cancelled or
crashed job never leaves a half-published run.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from typing import Any
import uuid

JOB_SCHEMA = "cad2gis.conversion_job.v1"
HEARTBEAT_SECONDS = 5.0
LOST_AFTER_SECONDS = 60.0
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})

# Processes started by this MCP server instance; only these may be cancelled.
_OWNED: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_state(job_dir: Path, state: dict[str, Any]) -> None:
    target = job_dir / "job.json"
    temporary = job_dir / f".job.{os.getpid()}.{threading.get_ident()}.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for attempt in range(50):
        try:
            os.replace(temporary, target)
            return
        except PermissionError:  # Windows: a reader holds the file briefly
            if attempt == 49:
                raise
            time.sleep(0.05)


def _read_state(job_dir: Path) -> dict[str, Any]:
    for attempt in range(50):
        try:
            return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


def _job_dir(value: str) -> Path:
    from .agent_mcp import MCPServiceError, _path

    job_dir = _path(value, must_exist=False)
    if not (job_dir / "job.json").is_file():
        raise MCPServiceError(f"No conversion job at {job_dir}", code="JOB_NOT_FOUND")
    return job_dir


def start_conversion_job(
    source: str,
    run_dir: str,
    project_dir: str,
    *,
    llm: str = "off",
    decision_pack: str = "",
    source_run: str = "",
    semantic_store: str = "",
    semantic_job: str = "",
    geometry_repairs: str = "legacy",
    svg_mode: str = "candidate",
    svg_font_dirs: list[str] | None = None,
    matching: str = "osm",
    osm_data: str = "",
    osm_place: str = "",
    match_profile: str = "",
    gcp_profile: str = "",
) -> dict[str, Any]:
    """Start run_conversion in the background; poll with get_conversion_job."""

    from .agent_mcp import MCPServiceError, _path

    request = {
        "source": source, "run_dir": run_dir, "project_dir": project_dir, "llm": llm,
        "decision_pack": decision_pack, "source_run": source_run,
        "semantic_store": semantic_store, "semantic_job": semantic_job,
        "geometry_repairs": geometry_repairs, "svg_mode": svg_mode,
        "svg_font_dirs": list(svg_font_dirs or []), "matching": matching,
        "osm_data": osm_data, "osm_place": osm_place,
        "match_profile": match_profile, "gcp_profile": gcp_profile,
    }
    # Fail synchronously on path problems; the worker validates everything again.
    _path(source)
    _path(project_dir)
    for name in ("decision_pack", "source_run", "semantic_store", "osm_data", "match_profile", "gcp_profile"):
        if request[name]:
            _path(request[name])
    run_path = _path(run_dir, must_exist=False)
    if run_path.exists():
        raise MCPServiceError(f"Run directory already exists: {run_path}", code="OUTPUT_EXISTS")
    job_dir = _path(str(run_path) + ".job", must_exist=False)
    try:
        job_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MCPServiceError(f"A job already exists for this run: {job_dir}", code="OUTPUT_EXISTS") from exc
    job_id = "job-" + uuid.uuid4().hex
    state = {
        "schema_version": JOB_SCHEMA, "job_id": job_id, "status": "queued",
        "job_dir": str(job_dir), "run_dir": str(run_path), "request": request,
        "created_at": _now(), "started_at": None, "finished_at": None,
        "heartbeat_at": None, "pid": None, "result": None, "error": None,
    }
    _write_state(job_dir, state)
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    command = [sys.executable, "-I", "-B", "-u", "-X", "utf8", "-m", "cad2gis.agent_jobs",
               "--worker", str(job_dir)]
    with (job_dir / "worker.log").open("xb") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, cwd=str(job_dir), **hidden)
    with _LOCK:
        _OWNED[job_id] = process
    return {"job_id": job_id, "job_dir": str(job_dir), "status": "queued",
            "poll_with": "get_conversion_job", "poll_interval_seconds": HEARTBEAT_SECONDS}


def _elapsed(state: dict[str, Any]) -> float | None:
    if not state.get("started_at"):
        return None
    end = datetime.fromisoformat(state["finished_at"]) if state.get("finished_at") else datetime.now(UTC)
    return round((end - datetime.fromisoformat(state["started_at"])).total_seconds(), 1)


def _log_tail(job_dir: Path, limit: int = 2000) -> str:
    try:
        with (job_dir / "worker.log").open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - limit))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def get_conversion_job(job_dir: str) -> dict[str, Any]:
    """Report a conversion job: queued, running, succeeded, failed, cancelled or lost."""

    path = _job_dir(job_dir)
    state = _read_state(path)
    status = state["status"]
    if status not in TERMINAL:
        with _LOCK:
            process = _OWNED.get(state["job_id"])
        exited = process is not None and process.poll() is not None
        beat = state.get("heartbeat_at") or state.get("created_at")
        stale = (datetime.now(UTC) - datetime.fromisoformat(beat)).total_seconds() > LOST_AFTER_SECONDS
        if exited:
            # Re-read: the worker may have written its terminal state as it exited.
            state = _read_state(path)
            status = state["status"]
            if status not in TERMINAL:
                status = "lost"
        elif stale and process is None:
            status = "lost"
    report = {key: state.get(key) for key in (
        "schema_version", "job_id", "job_dir", "run_dir", "created_at", "started_at",
        "finished_at", "result", "error")}
    report.update(status=status, elapsed_seconds=_elapsed(state))
    if status != "succeeded":
        report["log_tail"] = _log_tail(path)
    if status == "lost":
        report["recovery"] = ("The worker stopped without a terminal state; inspect worker.log, "
                              "then start a new job with a new run_dir")
    return report


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                       check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def cancel_conversion_job(job_dir: str) -> dict[str, Any]:
    """Stop a running job started by this MCP server; finished jobs are unchanged."""

    from .agent_mcp import MCPServiceError

    path = _job_dir(job_dir)
    state = _read_state(path)
    if state["status"] in TERMINAL:
        return get_conversion_job(job_dir)
    with _LOCK:
        process = _OWNED.get(state["job_id"])
    if process is None:
        raise MCPServiceError("This job was not started by the current MCP server; it cannot be cancelled here",
                              code="JOB_NOT_OWNED")
    _terminate(process)
    state = _read_state(path)
    if state["status"] not in TERMINAL:
        state.update(status="cancelled", finished_at=_now(),
                     error={"code": "CANCELLED", "message": "Cancelled by the MCP client"})
        _write_state(path, state)
    return get_conversion_job(job_dir)


def _worker(job_dir: Path) -> int:
    state = _read_state(job_dir)
    state.update(status="running", started_at=_now(), heartbeat_at=_now(), pid=os.getpid())
    _write_state(job_dir, state)
    lock = threading.Lock()
    done = threading.Event()

    def heartbeat() -> None:
        while not done.wait(HEARTBEAT_SECONDS):
            with lock:
                if done.is_set():
                    return
                state["heartbeat_at"] = _now()
                _write_state(job_dir, state)

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        from .agent_mcp import run_conversion

        request = dict(state["request"])
        result = run_conversion(request.pop("source"), request.pop("run_dir"), request.pop("project_dir"),
                                **request)
        outcome = {"status": "succeeded", "result": result}
    except BaseException as exc:  # report every failure, including SystemExit
        print(traceback.format_exc(), flush=True)
        outcome = {"status": "failed", "error": {
            "code": getattr(exc, "code", None) or type(exc).__name__, "message": str(exc)}}
    with lock:
        done.set()
        state.update(outcome, finished_at=_now(), heartbeat_at=_now())
        _write_state(job_dir, state)
    return 0 if outcome["status"] == "succeeded" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CAD2GIS background conversion worker (internal)")
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args(argv)
    return _worker(args.worker.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
