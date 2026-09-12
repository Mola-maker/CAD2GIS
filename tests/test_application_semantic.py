"""Exercise the shared application's extraction orchestration and artifact boundary."""
from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cad2gis.application import ApplicationConfig, Jobs, create_application
from cad2gis.cad2gis_v3.semantic_stage import prepare_semantics
from cad2gis.cad2gis_v3.source_export import export_source


def _export(drawing, output):
    return export_source(source=drawing, run_dir=output, records=[{
        "entity_key": "line-1", "handle": "line-1", "dwg_type_name": "LINE",
        "layout": "Model", "layout_role": "model", "cad_role": "model",
        "layer": "NETWORK", "points": [[10.125, 3.5], [20.25, 4.75]],
        "text": "原始光缆", "native_length": 10.201,
    }])


def _wait(jobs):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        if not jobs.active:
            return jobs.list()[0]
        time.sleep(.01)
    raise AssertionError("Application job did not terminate")


def _process_factory(monkeypatch, *, fail_stage=None, produce=True, tamper=False):
    calls = []

    class Process:
        def __init__(self, command, **kwargs):
            args = command[8:]
            calls.append(args)
            self.stdout = io.StringIO("stage completed\n")
            self.code = 2 if args[0] == fail_stage else 0
            if produce and self.code == 0:
                if args[0] == "export-source":
                    _export(Path(args[1]), Path(args[args.index("--run-dir") + 1]))
                else:
                    output = Path(args[args.index("--output-dir") + 1])
                    prepare_semantics(source_run=args[args.index("--source-run") + 1], output_dir=output)
                    if tamper:
                        manifest = output / "manifest.json"
                        data = json.loads(manifest.read_text())
                        data["source_sha256"] = "0" * 64
                        manifest.write_text(json.dumps(data))

        def wait(self):
            return self.code

    # Only intercept application jobs. Real exporters probe their own tools.
    monkeypatch.setattr("cad2gis.application.subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": Process}))
    return calls


def _payload(tmp_path):
    source = tmp_path / "source & literal.dwg"
    source.write_bytes(b"AC1032 controlled records for UI contract")
    return {"action": "semantic-extract", "source": str(source), "run": str(tmp_path / "extraction")}


def test_extraction_executes_existing_commands_and_verifies_real_artifacts(tmp_path, monkeypatch):
    calls = _process_factory(monkeypatch)
    jobs = Jobs(ApplicationConfig((tmp_path,)))
    payload = _payload(tmp_path)
    jobs.start(payload)
    result = _wait(jobs)
    assert [args[:2] if args[0] == "semantic" else args[:1] for args in calls] == [["export-source"], ["semantic", "prepare"]]
    assert all("--json" in args for args in calls)
    assert result["status"] == "succeeded" and result["completed_stages"] == 3
    assert result["summary"]["entity_count"] == 1
    assert result["summary"]["semantic_committed"] is False
    assert result["summary"]["candidate_count"] > 0
    assert len(result["artifacts"]) == 4
    assert not list(Path(payload["run"]).rglob("semantic.sqlite3"))
    assert not list(Path(payload["run"]).rglob("delivery.gpkg"))
    with pytest.raises(ValueError, match="已存在"):
        jobs.start(payload)


@pytest.mark.parametrize("fail_stage", ["export-source", "semantic"])
def test_stage_failure_never_looks_like_complete_extraction(tmp_path, monkeypatch, fail_stage):
    calls = _process_factory(monkeypatch, fail_stage=fail_stage)
    jobs = Jobs(ApplicationConfig((tmp_path,)))
    jobs.start(_payload(tmp_path))
    result = _wait(jobs)
    assert result["status"] == "failed" and result["exit_code"] == 2
    assert result["artifacts"] == []
    assert len(calls) == (1 if fail_stage == "export-source" else 2)


@pytest.mark.parametrize("produce,tamper", [(False, False), (True, True)])
def test_zero_exit_requires_complete_and_correctly_bound_outputs(tmp_path, monkeypatch, produce, tamper):
    _process_factory(monkeypatch, produce=produce, tamper=tamper)
    jobs = Jobs(ApplicationConfig((tmp_path,)))
    jobs.start(_payload(tmp_path))
    result = _wait(jobs)
    assert result["exit_code"] == 0
    assert result["status"] == "failed" and result["artifacts"] == []
    assert result["completed_stages"] == 2


def test_cancel_during_validation_cannot_publish_success_or_duplicate_job(tmp_path, monkeypatch):
    _process_factory(monkeypatch)
    jobs = Jobs(ApplicationConfig((tmp_path,)))
    entered, release = threading.Event(), threading.Event()
    validate = jobs.semantic_artifacts

    def paused(run):
        entered.set()
        assert release.wait(5)
        return validate(run)

    monkeypatch.setattr(jobs, "semantic_artifacts", paused)
    record = jobs.start(_payload(tmp_path))
    try:
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="正在执行"):
            jobs.start({"action": "doctor"})
        jobs.cancel(record["id"])
        assert jobs.list()[0]["status"] == "cancelling"
    finally:
        release.set()
    result = _wait(jobs)
    assert result["status"] == "cancelled" and result["artifacts"] == []
    assert result["completed_stages"] == 2
    assert (tmp_path / "extraction/source/source_manifest.json").is_file()


def test_cancelled_before_worker_starts_creates_no_output(tmp_path):
    jobs = Jobs(ApplicationConfig((tmp_path,)))
    payload = _payload(tmp_path)
    args, run = jobs.command(payload)
    jobs.active = "cancelled"
    jobs.records["cancelled"] = {"action": "semantic-extract", "status": "cancelling", "run_dir": run, "log": ""}
    jobs._run("cancelled", args)
    assert not Path(run).exists()
    assert jobs.records["cancelled"]["status"] == "cancelled"


def test_artifact_route_rechecks_bindings_after_restart_and_rejects_outside_paths(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    drawing = approved / "source.dwg"
    drawing.write_bytes(b"controlled fixture")
    output = approved / "extraction"
    _export(drawing, output / "source")
    prepare_semantics(source_run=output / "source", output_dir=output / "candidates")
    app = create_application(ApplicationConfig((approved,), launch_token="test"))
    query = {"run": str(output), "name": "prepare_manifest"}
    with TestClient(app, base_url="http://localhost", headers={"origin": "http://localhost"}) as client:
        assert client.get("/_app/api/semantic/artifact", params=query).status_code == 401
        client.post("/_app/api/login", json={"token": "test"})
        assert client.get("/_app/api/status").json()["semantic_extraction"]["automatic_commit"] is False
        response = client.get("/_app/api/semantic/artifact", params=query)
        assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
        assert response.json()["status"] == "READY_FOR_SEMANTIC_DECISIONS"
        assert client.get("/_app/api/semantic/artifact", params={**query, "name": "../../secret"}).status_code == 400
        assert client.get("/_app/api/semantic/artifacts", params={"run": str(tmp_path)}).status_code == 400
        manifest = output / "source/source_manifest.json"
        data = json.loads(manifest.read_text())
        data["artifacts"]["source_gpkg"]["path"] = str(tmp_path / "unapproved.gpkg")
        manifest.write_text(json.dumps(data))
        assert client.get("/_app/api/semantic/artifact", params=query).status_code == 400
        for malformed in [[], {"artifacts": []}, {"artifacts": {"source_gpkg": "not an object"}}]:
            manifest.write_text(json.dumps(malformed))
            assert client.get("/_app/api/semantic/artifact", params=query).status_code == 400


def test_companion_presses_never_fabricate_task_success_and_disabled_is_silent():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    app = Path(__file__).resolve().parents[1] / "src/cad2gis/desktop/app.js"
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const model=source.split('// Companion feedback uses task state;')[1].split('// End companion feedback model.')[0];
const body=model.slice(model.indexOf('class CompanionFeedback'));
const events=[],timers=new Map();let next=0;
const context={events,timers,actionNames:{'semantic-extract':'语义提取'},statusNames:{running:'正在执行',succeeded:'已完成',failed:'未完成',cancelled:'已停止',cancelling:'正在停止'}};
vm.runInNewContext(body+'\nglobalThis.Companion=CompanionFeedback;',context);
const companion=new context.Companion((state,label)=>events.push({state,label}),fn=>{timers.set(++next,fn);return next},id=>timers.delete(id));
companion.press(true);assert.equal(events.length,0);
companion.press();assert.equal(events.at(-1).state,'press');
companion.task({action:'semantic-extract',status:'running'});assert.equal(events.at(-1).state,'working');assert.equal(timers.size,0);
companion.press();for(const fn of timers.values())fn();assert.equal(events.at(-1).state,'working');
for(const [status,state] of [['cancelling','working'],['cancelled','cancelled'],['failed','error'],['succeeded','success']]){companion.task({action:'semantic-extract',status});assert.equal(events.at(-1).state,state)}
assert.equal(events.filter(item=>item.state==='success').length,1);
'''
    subprocess.run([node, "-e", script, str(app)], check=True, capture_output=True, text=True)
