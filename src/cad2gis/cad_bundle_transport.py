"""Bounded stdio MCP sessions in an independent CAD runtime."""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path


def child_environment(staging: Path) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
               "LANG", "LC_ALL", "COMSPEC", "PATHEXT"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(AUTOCAD_MCP_BACKEND="ezdxf", DISCOVERY_MODE="off", TOOL_PACKS="core",
               ENABLE_3D="false", DANGEROUS_COMMANDS_ENABLED="false",
               ALLOWED_PATHS=str(staging), EZDXF_CALL_TIMEOUT="120",
               MAX_DXF_BYTES=str(512 * 1024 * 1024), MAX_LIST_LIMIT="5000")
    return env


@asynccontextmanager
async def open_session(command: str, staging: Path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=command, args=[], cwd=staging,
                                   env=child_environment(staging))
    with (staging / "stderr.log").open("w", encoding="utf-8") as stderr:
        async with stdio_client(params, errlog=stderr) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield session

