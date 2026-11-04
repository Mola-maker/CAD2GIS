from __future__ import annotations

import json
import time

import pytest

from cad2gis import agent_jobs
from cad2gis.agent_mcp import MCPServiceError


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"not a real dwg")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return tmp_path, source, project_dir


def _wait_terminal(job_dir: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        report = agent_jobs.get_conversion_job(job_dir)
        if report["status"] in agent_jobs.TERMINAL | {"lost"}:
            return report
        time.sleep(0.2)
    raise AssertionError(f"job did not finish: {agent_jobs.get_conversion_job(job_dir)}")


def test_job_runs_in_background_and_reports_worker_failure(project):
    root, source, project_dir = project
    started = agent_jobs.start_conversion_job(str(source), str(root / "run"), str(project_dir))
    assert started["status"] == "queued"
    assert started["job_dir"].endswith("run.job")
    report = _wait_terminal(started["job_dir"])
    # An unconfigured project cannot convert; the failure is captured, not raised.
    assert report["status"] == "failed"
    assert report["error"]["code"]
    assert report["elapsed_seconds"] is not None
    assert not (root / "run").exists()
    state = json.loads((root / "run.job" / "job.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == agent_jobs.JOB_SCHEMA and state["finished_at"]


def test_cancel_stops_an_owned_job_and_keeps_finished_jobs(project):
    import subprocess
    import sys

    root, _, _ = project
    job_dir = root / "slow.job"
    job_dir.mkdir()
    agent_jobs._write_state(job_dir, {
        "schema_version": agent_jobs.JOB_SCHEMA, "job_id": "job-slow", "status": "running",
        "created_at": agent_jobs._now(), "started_at": agent_jobs._now(),
        "heartbeat_at": agent_jobs._now(), "finished_at": None})
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    agent_jobs._OWNED["job-slow"] = process
    try:
        cancelled = agent_jobs.cancel_conversion_job(str(job_dir))
        assert cancelled["status"] == "cancelled"
        assert cancelled["error"]["code"] == "CANCELLED"
        assert process.poll() is not None
        # A terminal job is reported unchanged by a second cancel.
        assert agent_jobs.cancel_conversion_job(str(job_dir))["status"] == "cancelled"
    finally:
        agent_jobs._OWNED.pop("job-slow", None)
        if process.poll() is None:
            process.kill()


def test_job_paths_are_validated_before_starting(project, tmp_path_factory):
    root, source, project_dir = project
    outside = tmp_path_factory.mktemp("outside")
    with pytest.raises(MCPServiceError) as error:
        agent_jobs.start_conversion_job(str(source), str(outside / "run"), str(project_dir))
    assert error.value.code == "PATH_OUTSIDE_ROOT"
    (root / "taken").mkdir()
    with pytest.raises(MCPServiceError) as error:
        agent_jobs.start_conversion_job(str(source), str(root / "taken"), str(project_dir))
    assert error.value.code == "OUTPUT_EXISTS"
    with pytest.raises(MCPServiceError) as error:
        agent_jobs.get_conversion_job(str(root / "missing.job"))
    assert error.value.code == "JOB_NOT_FOUND"


def test_unowned_running_job_cannot_be_cancelled(project):
    root, _, _ = project
    job_dir = root / "other.job"
    job_dir.mkdir()
    agent_jobs._write_state(job_dir, {
        "schema_version": agent_jobs.JOB_SCHEMA, "job_id": "job-foreign", "status": "running",
        "created_at": agent_jobs._now(), "started_at": agent_jobs._now(),
        "heartbeat_at": agent_jobs._now(), "finished_at": None})
    assert agent_jobs.get_conversion_job(str(job_dir))["status"] == "running"
    with pytest.raises(MCPServiceError) as error:
        agent_jobs.cancel_conversion_job(str(job_dir))
    assert error.value.code == "JOB_NOT_OWNED"
