"""Project-root authorization for optional CAD bundle tools."""
from pathlib import Path
from typing import Any

from .cad_bundles import get_bundle_status, discover_bundle, invoke_bundle
from .cad_bundle_workspace import create_workspace, safe_path


def get_cad_bundle_status() -> dict[str, Any]:
    """Inspect the optional pinned CAD sidecar without downloading or starting it."""
    return get_bundle_status()


def prepare_cad_bundle_workspace(source: str, workspace: str) -> dict[str, Any]:
    """Create a hash-bound DXF editing copy inside the authorized project roots."""
    from .agent_mcp import _path
    source_path = _path(safe_path(Path(source)))
    destination = _path(safe_path(Path(workspace)), must_exist=False)
    return create_workspace(source_path, destination)


async def list_cad_bundle_tools(workspace_id: str) -> dict[str, Any]:
    """Discover only tested CAD tools and their path-free argument schemas."""
    from .agent_mcp import _path
    tools = await discover_bundle(_path(safe_path(Path(workspace_id))))
    return {"schema_version": "cad2gis.cad_bundle_tools.v1", "tools": tools,
            "tool_count": len(tools), "backend": "ezdxf"}


async def call_cad_bundle_tool(workspace_id: str, tool_name: str,
                              arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an allowed CAD tool on a workspace copy, preserving source and old revisions."""
    from .agent_mcp import _path
    return await invoke_bundle(_path(safe_path(Path(workspace_id))), tool_name, arguments)
