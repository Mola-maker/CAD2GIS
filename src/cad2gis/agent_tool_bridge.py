"""Bounded MCP results and retry policy for the desktop's existing agent.

This module does not retry an RPC. It turns execution errors into model-visible
feedback, while the SDK enforces the per-tool retry and model request limits.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Callable

from .mcp_diagnostics import MUTATING_TOOLS


MAX_TOOL_ATTEMPTS = 150
MAX_RESULT_CHARS = 60000


class ToolAttemptBudget:
    """Count failed attempts too; the SDK tool_calls_limit counts successes."""

    def __init__(self, limit: int = MAX_TOOL_ATTEMPTS):
        self.limit = limit
        self.attempts = 0

    def consume(self) -> None:
        from pydantic_ai.exceptions import UsageLimitExceeded

        if self.attempts >= self.limit:
            raise UsageLimitExceeded(f"Tool attempt limit ({self.limit}) reached")
        self.attempts += 1


def _result_text(response: Any) -> str:
    parts = [part.text for part in response.content if getattr(part, "type", "") == "text"]
    structured = getattr(response, "structuredContent", None)
    # Errors may explain the failure only in content, even when structured data
    # exists. A successful structured result is authoritative (including {}).
    if structured is not None:
        structured_text = json.dumps(structured, ensure_ascii=False, allow_nan=False)
        if not response.isError or not parts:
            return structured_text
        if structured_text not in parts:
            parts.append(structured_text)
    if not parts and response.content:
        kinds = sorted({getattr(part, "type", "unknown") for part in response.content})
        return json.dumps({"unread_content_types": kinds, "note": "Use a text or structured query to inspect this result."})
    return "\n".join(parts)


def _error_metadata(response: Any) -> dict[str, Any]:
    structured = getattr(response, "structuredContent", None)
    if isinstance(structured, dict) and "error_code" in structured:
        return structured
    # FastMCP prefixes ToolError text with 'Error executing tool ...: '. Decode
    # only our server's terminal JSON envelope; do not interpret its text as code.
    for part in response.content:
        if getattr(part, "type", "") != "text":
            continue
        start = part.text.find("{")
        if start >= 0:
            try:
                value = json.loads(part.text[start:])
            except ValueError:
                continue
            if isinstance(value, dict) and "error_code" in value:
                return value
    return {}


class MCPToolBridge:
    def __init__(self, session: Any, *, admitted: Callable[..., Any],
                 clean: Callable[[Any], str], event: Callable[..., None],
                 budget: ToolAttemptBudget, provider: str):
        self.session, self.admitted = session, admitted
        self.clean, self.event, self.budget = clean, event, budget
        self.provider = provider
        self.uncertain_mutations: set[str] = set()

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from pydantic_ai import ModelRetry

        self.budget.consume()
        kwargs = dict(arguments)
        if name == "auto_onboard_and_convert":
            kwargs.setdefault("provider", self.provider)
        for key, value in kwargs.items():
            if isinstance(value, str) and value and (
                key.endswith(("_path", "_dir", "_store"))
                or key in {"source", "source_run", "semantic_store", "osm_data", "match_profile", "registration_manifest", "source_manifest"}
            ):
                try:
                    kwargs[key] = str(self.admitted(value, False))
                except (ValueError, OSError) as exc:
                    detail = self.clean(str(exc))[:1200]
                    self.event("tool_error", name, detail)
                    raise ModelRetry(detail) from None

        fingerprint = hashlib.sha256(json.dumps(
            [name, kwargs], sort_keys=True, ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        if fingerprint in self.uncertain_mutations:
            inspector = {"reverse_convert": "reverse_verify", "reverse_source_convert": "reverse_source_verify"}.get(
                name, "inspect_semantic_store / inspect_run")
            detail = f"上一写入的结果尚不确定，本轮不会重复提交相同操作。请检查现有产物或 {inspector}，再说明恢复步骤。"
            self.event("tool_error", name, detail)
            raise ModelRetry(detail)

        self.event("tool_call", name, self.clean(json.dumps(kwargs, ensure_ascii=False))[:1200])
        mutating = name in MUTATING_TOOLS
        if mutating:
            # Keep the marker if the RPC is cancelled, times out, loses its
            # transport, or fails before we can decode the outcome.
            self.uncertain_mutations.add(fingerprint)
        try:
            response = await self.session.call_tool(name, kwargs)
            result = self.clean(_result_text(response))
        except asyncio.CancelledError:
            self.event("tool_interrupted", name, "操作已取消；写入结果需要检查。" if mutating else "查询已取消。")
            raise
        except Exception:
            # A lost response is not evidence of failure before commit. Abort
            # the run rather than replaying an RPC with an unknown outcome.
            self.event("tool_error", name, "未能确认工具结果；写入可能已完成，请先检查产物。" if mutating else "工具调用未完成。")
            raise

        if response.isError:
            metadata = _error_metadata(response)
            if mutating and metadata.get("committed") is False:
                self.uncertain_mutations.discard(fingerprint)
            detail = result[:2400] or "MCP 工具返回了失败状态，未提供详细原因。"
            if mutating and fingerprint in self.uncertain_mutations:
                detail += "\n写入结果未知；禁止直接重复提交相同操作，先检查产物或操作状态。"
            self.event("tool_error", name, detail)
            raise ModelRetry(detail)

        self.uncertain_mutations.discard(fingerprint)
        self.event("tool_result", name, result)
        truncated = len(result) > MAX_RESULT_CHARS
        if truncated:
            result = result[:MAX_RESULT_CHARS] + "\n[结果已截断，请用分页或更具体的查询继续]"
        return {"is_error": False, "result": result, "truncated": truncated}
