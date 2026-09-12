"""Protocol fixtures: failed/structured MCP results, cancellation and write replay."""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import UsageLimitExceeded

from cad2gis.agent_tool_bridge import MCPToolBridge, ToolAttemptBudget


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        result = next(self.responses)
        if isinstance(result, BaseException):
            raise result
        return result


def bridge(session, *, limit=150, admitted=None):
    events = []
    adapter = MCPToolBridge(
        session, admitted=admitted or (lambda value, _exists: value),
        clean=lambda value: str(value).replace("fixture-secret", "[hidden]"),
        event=lambda *args: events.append(args), budget=ToolAttemptBudget(limit),
        provider="new-api",
    )
    return adapter, events


def failure(*, committed="unknown"):
    return CallToolResult(isError=True, content=[TextContent(
        type="text", text="Error executing tool: " + json.dumps({
            "error_code": "EXECUTION_FAILED", "committed": committed,
        }),
    )])


@pytest.mark.parametrize("payload", [{}, {"nodes": [1, 2], "source": "fixture-secret"}])
def test_structured_only_result_is_preserved_and_redacted(payload):
    adapter, events = bridge(Session(CallToolResult(content=[], structuredContent=payload)))
    result = asyncio.run(adapter.call("get_capabilities", {}))
    assert json.loads(result["result"]) == json.loads(json.dumps(payload).replace("fixture-secret", "[hidden]"))
    assert result["is_error"] is False and result["truncated"] is False
    assert [event[0] for event in events] == ["tool_call", "tool_result"]


def test_is_error_takes_priority_over_success_looking_structured_data():
    response = CallToolResult(isError=True, content=[TextContent(type="text", text="fixture-secret: missing source")],
                              structuredContent={"status": "accepted"})
    adapter, events = bridge(Session(response))
    with pytest.raises(ModelRetry, match="missing source") as exc:
        asyncio.run(adapter.call("inspect_source", {}))
    assert "fixture-secret" not in str(exc.value) + str(events)
    assert [event[0] for event in events] == ["tool_call", "tool_error"]


def test_long_onboarding_report_keeps_parseable_conversion_receipt():
    payload = {'onboarding': {'detail': 'x' * 100000}, 'run_manifest': '/run/run_manifest.json',
               'delivery': '/run/delivery.gpkg', 'run_status': 'CONDITIONAL', 'counts': {'CABLE': 2}}
    adapter, _ = bridge(Session(CallToolResult(content=[], structuredContent=payload)))
    result = asyncio.run(adapter.call('auto_onboard_and_convert', {}))
    assert result['truncated']
    assert result['conversion'] == {key: value for key, value in payload.items() if key != 'onboarding'}


def test_unknown_mutation_is_never_replayed_with_reordered_arguments():
    session = Session(failure())
    adapter, events = bridge(session)
    for arguments in ({"run_dir": "/run", "source": "/input"}, {"source": "/input", "run_dir": "/run"}):
        with pytest.raises(ModelRetry):
            asyncio.run(adapter.call("run_conversion", arguments))
    assert len(session.calls) == 1
    assert adapter.budget.attempts == 2
    assert events[-1][0] == "tool_error"


def test_explicit_no_commit_error_allows_corrective_retry():
    session = Session(failure(committed=False), CallToolResult(content=[], structuredContent={"created": True}))
    adapter, _ = bridge(session)
    with pytest.raises(ModelRetry):
        asyncio.run(adapter.call("bootstrap_project", {"project_dir": "/project"}))
    assert asyncio.run(adapter.call("bootstrap_project", {"project_dir": "/project"}))["is_error"] is False
    assert len(session.calls) == 2


def test_attempt_limit_counts_failures_before_the_next_rpc():
    session = Session(failure(), failure())
    adapter, _ = bridge(session, limit=2)
    for _ in range(2):
        with pytest.raises(ModelRetry):
            asyncio.run(adapter.call("inspect_run", {}))
    with pytest.raises(UsageLimitExceeded):
        asyncio.run(adapter.call("inspect_run", {}))
    assert len(session.calls) == 2 and adapter.budget.attempts == 2


@pytest.mark.parametrize("error", [asyncio.CancelledError(), TimeoutError("fixture-secret")])
def test_interrupted_write_keeps_unknown_outcome_and_propagates(error):
    session = Session(error)
    adapter, events = bridge(session)
    with pytest.raises(type(error)):
        asyncio.run(adapter.call("commit_semantic_patch", {"semantic_store": "/db"}))
    with pytest.raises(ModelRetry):
        asyncio.run(adapter.call("commit_semantic_patch", {"semantic_store": "/db"}))
    assert len(session.calls) == 1
    assert "fixture-secret" not in str(events)
    assert not any(event[0] == "tool_result" for event in events)


def test_rejected_path_does_not_reach_mcp():
    def reject(*_):
        raise ValueError("outside root")

    session = Session()
    adapter, events = bridge(session, admitted=reject)
    with pytest.raises(ModelRetry, match="outside root"):
        asyncio.run(adapter.call("inspect_source", {"source": "/private/input.dwg"}))
    assert session.calls == []
    assert events[0][0] == "tool_error"


def test_large_result_has_explicit_truncation_marker():
    adapter, _ = bridge(Session(CallToolResult(content=[TextContent(type="text", text="x" * 70000)])))
    result = asyncio.run(adapter.call("get_capabilities", {}))
    assert result["truncated"] is True
    assert "已截断" in result["result"] and len(result["result"]) < 61000
