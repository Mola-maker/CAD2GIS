from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from cad2gis import mcp_diagnostics


class _Server:
    def get_context(self):
        return SimpleNamespace(request_id="request-1")


def test_packaged_manifest_version_drift_is_detected_without_expected_identity(monkeypatch):
    monkeypatch.setattr(mcp_diagnostics, "_plugin_manifests", lambda: {
        "status": "available", "manifests": {".codex-plugin/plugin.json": {"version": "0.0.1"}},
    })
    report = mcp_diagnostics.diagnose(scope="identity", tool_schemas=[])
    assert report["status"] == "VERSION_DRIFT"
    assert any(item["field"] == ".codex-plugin/plugin.json" for item in report["identity_differences"])


def test_trace_does_not_disclose_arguments_or_provider_failure(capsys):
    def provider_call(api_key: str) -> dict:
        raise ValueError(f"provider returned key {api_key} and password=hunter-test")

    wrapped = mcp_diagnostics.traced_tool("query_source_entities", provider_call, _Server())
    assert inspect.signature(wrapped) == inspect.signature(provider_call)
    with pytest.raises(Exception) as failure:
        wrapped("sk-fake-private-value")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sk-fake" not in captured.err + str(failure.value)
    assert "hunter-test" not in captured.err + str(failure.value)
    events = [json.loads(line) for line in captured.err.splitlines()]
    assert [event["phase"] for event in events] == ["started", "failed"]
    assert events[0]["trace_id"] == events[1]["trace_id"]
    assert json.loads(str(failure.value))["error_code"] == "VALIDATION_FAILED"


def test_committed_mutation_trace_survives_diagnostic_write_failure(monkeypatch):
    def commit() -> dict:
        return {"revision": 2, "committed": True}

    class BrokenStream:
        def write(self, _line):
            raise OSError("disk full")

    monkeypatch.setattr(mcp_diagnostics.sys, "stderr", BrokenStream())
    wrapped = mcp_diagnostics.traced_tool("commit_semantic_patch", commit, _Server())
    assert wrapped() == {"revision": 2, "committed": True}


def test_cancelled_call_has_one_terminal_event(capsys):
    async def query() -> dict:
        raise asyncio.CancelledError()

    wrapped = mcp_diagnostics.traced_tool("query_source_entities", query, _Server())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(wrapped())
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [event["phase"] for event in events] == ["started", "cancelled"]
    assert events[-1]["committed"] is False


def test_identity_detects_schema_drift_without_installing_runtime(monkeypatch):
    from cad2gis import agent_mcp

    monkeypatch.setattr(agent_mcp, "install_runtime", lambda: pytest.fail("diagnosis installed runtime"))
    original = [{"name": "test", "inputSchema": {"type": "object"}}]
    changed = [{"name": "test", "inputSchema": {"type": "object", "required": ["source"]}}]
    first = mcp_diagnostics.runtime_identity(original)
    result = mcp_diagnostics.diagnose(
        scope="identity", tool_schemas=changed,
        expected_identity={"tools_schema_sha256": first["tools_schema_sha256"]},
    )
    assert result["status"] == "VERSION_DRIFT"
    assert result["identity_differences"][0]["field"] == "tools_schema_sha256"
    assert result["redis"]["status"] == "not_configured"


def test_query_diagnostic_uses_path_boundary(tmp_path, monkeypatch):
    from cad2gis.agent_mcp import MCPServiceError
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(allowed))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    with pytest.raises(MCPServiceError) as error:
        mcp_diagnostics.diagnose(scope="query", tool_schemas=[],
                                 graph_path=str(tmp_path / "outside.json"))
    assert error.value.code == "PATH_OUTSIDE_ROOT"
