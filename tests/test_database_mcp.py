"""Integration checks at the actual MCP wire and CLI boundaries."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from test_semantic_transactions import semantic_fixture  # noqa: F401


def test_mcp_stdio_semantic_roundtrip_and_protocol_budget(semantic_fixture, tmp_path):  # noqa: F811
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from cad2gis.contracts import MCP_TOOL_NAMES

    f = semantic_fixture
    environment = {**os.environ, "CAD2GIS_PROJECT_ROOT": str(tmp_path),
                   "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    context = {"source_run": str(f["source"]),
               "prepare_manifest": str(tmp_path / "wire-prepare" / "manifest.json"),
               "semantic_store": str(tmp_path / "wire-store.sqlite3")}

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=["-c", "import faulthandler; faulthandler.dump_traceback_later(20); from cad2gis.agent_mcp import main; main()"], env=environment)
        with (tmp_path / "mcp-stderr.jsonl").open("w", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    handshake = await session.initialize()

                    async def call(name, arguments):
                        response = await asyncio.wait_for(session.call_tool(name, arguments), 25)
                        assert not response.isError, response
                        return response.structuredContent or json.loads(response.content[0].text)

                    assert len((await session.list_tools()).tools) == len(MCP_TOOL_NAMES)
                    debug = await call("debug_mcp", {})
                    assert debug["status"] == "ok"
                    assert debug["protocol_version"] == handshake.protocolVersion
                    assert debug["transport"] == "stdio"
                    assert debug["redis"] == {"status": "not_configured", "required": False}
                    page = await session.call_tool("query_source_entities", {"run_dir": str(f["source"]), "max_bytes": 8192})
                    assert not page.isError
                    assert len(page.model_dump_json(exclude_none=True).encode()) < 8192
                    assert page.structuredContent["metadata"]["snapshot_sha256"] == f["initialized"]["snapshot_sha256"]
                    await call("prepare_semantic_batches", {"source_run": str(f["source"]), "output_dir": str(tmp_path / "wire-prepare")})
                    initial = await call("initialize_semantic_store", context)
                    candidates = await call("query_relationship_candidates", {"prepare_manifest": context["prepare_manifest"], "entity_ids": ["line-1"]})
                    item = candidates["items"][0]
                    patch = {key: initial[key] for key in ("source_sha256", "snapshot_sha256", "candidates_sha256", "policy_sha256", "ontology_sha256")}
                    patch.update(base_revision=0, operations=[{
                        "operation": "attach_existing_label", "entity_key": item["entity_key"],
                        "label_entity_key": item["target_id"], "candidate_id": item["candidate_id"], "policy_id": item["policy_id"],
                    }])
                    preview = await call("preview_semantic_patch", {**context, "patch": patch})
                    arguments = {**context, "patch": patch, "preview_hash": preview["preview_hash"], "idempotency_key": "wire-patch"}
                    committed = await call("commit_semantic_patch", arguments)
                    assert (await call("commit_semantic_patch", arguments))["revision"] == committed["revision"] == 1
                    state = await call("inspect_semantic_store", {"semantic_store": context["semantic_store"], "idempotency_key": "wire-patch"})
                    assert state["key_found"]
                    compiled = await call("compile_semantic_revision", {**context, "revision": 1, "output_dir": str(tmp_path / "wire-output"), "idempotency_key": "wire-compile"})
                    assert compiled["validation"]["source_facts_unchanged"]
                    assert compiled["accepted_run_id"] is None
                    invalid = await session.call_tool("preview_semantic_patch", {**context, "patch": {**patch, "coordinates": [1, 2]}})
                    assert invalid.isError
                    assert "VALIDATION_FAILED" in invalid.content[0].text
                    state = await call("inspect_semantic_store", {"semantic_store": context["semantic_store"]})
                    assert state["revision"] == 1

    asyncio.run(exercise())
    events = [json.loads(line) for line in (tmp_path / "mcp-stderr.jsonl").read_text(encoding="utf-8").splitlines() if line.startswith('{"schema_version": "cad2gis.mcp_trace.v1"')]
    traces = {}
    for event in events:
        traces.setdefault(event["trace_id"], []).append(event["phase"])
    assert traces and all(phases[0] == "started" and len(phases) == 2 for phases in traces.values())
    for name in ("commit_semantic_patch", "compile_semantic_revision"):
        assert all(event["committed"] is True for event in events if event["tool_name"] == name and event["phase"] == "succeeded")
    assert any(event["committed"] is False for event in events if event["tool_name"] == "query_source_entities" and event["phase"] == "succeeded")
    assert "管线-Ａ１２" not in (tmp_path / "mcp-stderr.jsonl").read_text(encoding="utf-8")


def test_database_worker_keeps_loop_responsive_after_rpc_cancel():
    from cad2gis.mcp_diagnostics import database_tool
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        release.wait(5)
        finished.set()
        return {"committed": True}

    async def exercise():
        task = asyncio.create_task(database_tool("compile_semantic_revision", work)())
        assert await asyncio.to_thread(started.wait, 2)
        # A different RPC/task gets loop time while a compile is still running.
        assert await asyncio.wait_for(asyncio.sleep(0, result="responsive"), .2) == "responsive"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)

    asyncio.run(exercise())


def test_cli_source_query_and_semantic_status(semantic_fixture):  # noqa: F811
    f = semantic_fixture
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    for arguments in (
        ["query-source", str(f["source"]), "--text-query", "管线", "--json"],
        ["semantic", "status", "--semantic-store", str(f["context"]["semantic_store"]), "--json"],
    ):
        result = subprocess.run([sys.executable, "-m", "cad2gis", *arguments], env=environment, capture_output=True, encoding="utf-8", timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert isinstance(json.loads(result.stdout), dict)


def test_query_diagnostics_do_not_create_an_index(semantic_fixture, monkeypatch):  # noqa: F811
    from cad2gis.cad2gis_v3.source_query import source_index_path
    from cad2gis.mcp_diagnostics import diagnose
    source = semantic_fixture["source"]
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(source.parent))
    index = source_index_path(source)
    assert not index.exists()
    report = diagnose(scope="query", tool_schemas=[], run_dir=str(source))
    assert report["query"]["status"] == "index_not_built"
    assert not index.exists()
