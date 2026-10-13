"""Optional, pinned CAD tool bundles; normal conversion never installs them."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import uuid

from .cad_bundle_policy import (
    ALLOWED_TOOLS,
    MUTATING_TOOLS,
    UPSTREAM_SHA,
    UPSTREAM_VERSION,
    UPSTREAM_REPOSITORY,
    CALL_TIMEOUT,
    MAX_RESULT_BYTES,
)
from .cad_bundle_workspace import (
    load_workspace,
    commit_revision,
    safe_path,
    sha256,
    _write_json,
)
from .cad_bundle_transport import open_session

COMMAND_ENV = "CAD2GIS_CAD_BUNDLE_COMMAND"
PATH_ARGUMENTS = {
    "path",
    "old_path",
    "new_path",
    "file",
    "filename",
    "file_path",
    "output",
    "output_dir",
    "output_path",
    "directory",
    "cwd",
}


def install_bundle(prefix: Path) -> dict[str, Any]:
    prefix = safe_path(prefix)
    if prefix.exists():
        raise FileExistsError(
            "CAD bundle prefix must be new; existing installs are preserved"
        )
    subprocess.run(
        [sys.executable, "-m", "venv", str(prefix)],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    scripts = prefix / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            f"git+{UPSTREAM_REPOSITORY}.git@{UPSTREAM_SHA}",
        ],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as m,json; d=m.distribution('autocad-mcp-pro'); "
            "print(json.dumps({'version':d.version,'source':json.loads(d.read_text('direct_url.json'))}))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    identity = json.loads(probe.stdout)
    if (
        identity["version"] != UPSTREAM_VERSION
        or identity["source"].get("vcs_info", {}).get("commit_id") != UPSTREAM_SHA
    ):
        raise ValueError(
            "CAD bundle installed identity differs from the pinned upstream"
        )
    command = scripts / ("autocad-mcp.exe" if os.name == "nt" else "autocad-mcp")
    receipt = {
        "schema_version": "cad2gis.cad_bundle_install.v1",
        "upstream_sha": UPSTREAM_SHA,
        "upstream_version": UPSTREAM_VERSION,
        "command": str(command),
        "command_sha256": sha256(command),
        **identity,
    }
    _write_json(prefix / "receipt.json", receipt)
    return {
        **receipt,
        "environment_variable": COMMAND_ENV,
        "instructions": f"Set {COMMAND_ENV} to the command path, then restart your MCP client.",
    }


def get_bundle_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "cad2gis.cad_bundle_status.v1",
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_sha": UPSTREAM_SHA,
        "upstream_version": UPSTREAM_VERSION,
        "backend": "ezdxf",
        "installed": False,
        "status": "not_installed",
        "optional": True,
        "allowed_tools": sorted(ALLOWED_TOOLS),
        "source_editing": "workspace copies only",
    }
    raw = os.environ.get(COMMAND_ENV, "").strip()
    if not raw:
        return result
    try:
        if not Path(raw).is_absolute():
            raise ValueError("CAD bundle command must be one absolute executable path")
        command = safe_path(Path(raw))
        receipt_path = safe_path(command.parent.parent / "receipt.json")
        if receipt_path.stat().st_size > 64 * 1024:
            raise ValueError("CAD bundle receipt is too large")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("CAD bundle receipt must be a JSON object")
        if (
            receipt.get("upstream_sha") != UPSTREAM_SHA
            or receipt.get("upstream_version") != UPSTREAM_VERSION
            or receipt.get("command") != str(command)
            or receipt.get("command_sha256") != sha256(command)
            or not os.access(command, os.X_OK)
        ):
            raise ValueError(
                "CAD bundle install receipt or executable identity does not match"
            )
        result.update(status="ready", installed=True, command=str(command))
    except (OSError, ValueError, TypeError) as exc:
        result.update(status="invalid_install", detail=str(exc))
    return result


def _reject_paths(arguments: Any) -> None:
    if isinstance(arguments, dict):
        for key, value in arguments.items():
            if (
                not isinstance(key, str)
                or key.lower() in PATH_ARGUMENTS
                or key.lower().endswith(("_path", "_file", "_dir"))
            ):
                raise ValueError(
                    "File paths are managed by the CAD workspace, not tool arguments"
                )
            _reject_paths(value)
    elif isinstance(arguments, list):
        for value in arguments:
            _reject_paths(value)


def _tool_schema(tool) -> dict[str, Any]:
    schema = deepcopy(tool.inputSchema)
    properties = schema.get("properties", {})
    for name in list(properties):
        if name.lower() in PATH_ARGUMENTS or name.lower().endswith(
            ("_path", "_file", "_dir")
        ):
            properties.pop(name)
    if tool.name == "drawing_diff":
        properties.pop("markup", None)
        properties.pop("rev", None)
        properties.pop("description", None)
    schema["required"] = [
        key for key in schema.get("required", []) if key in properties
    ]
    schema["additionalProperties"] = False
    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": schema,
        "mutates_workspace": tool.name in MUTATING_TOOLS,
    }


def _error_text(exc: BaseException) -> str:
    children = getattr(exc, "exceptions", ())
    return "; ".join(_error_text(e) for e in children) if children else str(exc)


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or any(
        _is_timeout(child) for child in getattr(exc, "exceptions", ())
    )


async def _session_operation(staging: Path, operation):
    status = get_bundle_status()
    if status["status"] != "ready":
        raise ValueError(f"Optional CAD bundle is not ready: {status['status']}")
    deadline = asyncio.timeout(CALL_TIMEOUT)
    try:
        async with deadline:
            async with open_session(status["command"], staging) as session:
                return await operation(session)
    except TimeoutError as exc:
        raise ValueError(
            "CAD sidecar timed out; current revision is unchanged"
        ) from exc
    except Exception as exc:
        if deadline.expired() or _is_timeout(exc):
            raise ValueError(
                "CAD sidecar timed out; current revision is unchanged"
            ) from exc
        raise ValueError(f"CAD sidecar failed: {_error_text(exc)}") from exc


@contextmanager
def _call_staging(workspace: Path, prefix: str):
    staging = Path(tempfile.mkdtemp(prefix=prefix, dir=workspace))
    try:
        yield staging
    finally:
        # The session is closed before this context exits. Retain a bounded
        # diagnostic tail, but never retain full transient drawing copies.
        log = staging / "stderr.log"
        tail = ""
        if log.is_file():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 64 * 1024))
                tail = stream.read(64 * 1024).decode("utf-8", errors="replace")
        shutil.rmtree(staging)
        if tail:
            _write_json(
                workspace / "evidence" / f"stderr-{uuid.uuid4().hex}.json",
                {"stderr_tail": tail, "max_retained_bytes": 64 * 1024},
            )


async def discover_bundle(workspace: Path) -> list[dict[str, Any]]:
    workspace = safe_path(workspace)
    load_workspace(workspace)
    with _call_staging(workspace, ".discover-") as staging:

        async def discover(session):
            tools = (await session.list_tools()).tools
            return [_tool_schema(tool) for tool in tools if tool.name in ALLOWED_TOOLS]

        return await _session_operation(staging, discover)


async def _checked_call(session, tool: str, arguments: dict[str, Any]) -> Any:
    response = await session.call_tool(tool, arguments)
    raw = response.model_dump(mode="json", exclude_none=True)
    if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("CAD sidecar result exceeds the 4 MiB limit")
    if response.isError:
        raise ValueError(
            "; ".join(item.text for item in response.content if hasattr(item, "text"))
        )
    if response.structuredContent is not None:
        return response.structuredContent
    texts = [item.text for item in response.content if hasattr(item, "text")]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            pass
    return raw


async def invoke_bundle(
    workspace: Path, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if tool_name not in ALLOWED_TOOLS:
        raise ValueError("CAD tool is outside the allowed bundle surface")
    if not isinstance(arguments, dict):
        raise ValueError("CAD tool arguments must be a JSON object")
    _reject_paths(arguments)
    workspace = safe_path(workspace)
    manifest = load_workspace(workspace)
    with _call_staging(workspace, ".call-") as staging:
        current = staging / "current.dxf"
        shutil.copyfile(
            workspace / f"revisions/{manifest['current_revision']:06d}.dxf", current
        )
        original = staging / "original.dxf"
        shutil.copyfile(workspace / "source.dxf", original)

        async def invoke(session):
            from jsonschema import Draft202012Validator

            discovered = {
                tool.name: tool for tool in (await session.list_tools()).tools
            }
            if tool_name not in discovered:
                raise ValueError(
                    "The installed CAD bundle does not advertise the requested tool"
                )
            Draft202012Validator(
                _tool_schema(discovered[tool_name])["inputSchema"]
            ).validate(arguments)
            await _checked_call(session, "drawing_open", {"path": str(current)})
            params = deepcopy(arguments)
            if tool_name in {"drawing_understand", "drawing_topology_check"}:
                params["path"] = str(current)
            if tool_name == "drawing_diff":
                params.update(
                    old_path=str(original), new_path=str(current), markup=False
                )
            result = await _checked_call(session, tool_name, params)
            if tool_name in MUTATING_TOOLS:
                await _checked_call(
                    session, "drawing_save", {"path": str(staging / "edited.dxf")}
                )
            return result

        result = await _session_operation(staging, invoke)
        evidence = {
            "tool": tool_name,
            "arguments": arguments,
            "result": result,
            "upstream_sha": UPSTREAM_SHA,
            "transient_drawings_retained": False,
        }
        if tool_name in MUTATING_TOOLS:
            manifest = commit_revision(
                workspace,
                staging / "edited.dxf",
                manifest["current_revision"],
                evidence,
            )
        else:
            _write_json(
                workspace / "evidence" / f"read-{uuid.uuid4().hex}.json", evidence
            )
        return {
            **manifest,
            "result": result,
            "tool": tool_name,
            "edited_source": str(
                workspace / f"revisions/{manifest['current_revision']:06d}.dxf"
            ),
        }
