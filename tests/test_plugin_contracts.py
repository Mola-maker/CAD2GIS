from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad2gis import doctor
from cad2gis.agent_mcp import get_capabilities


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "cad2gis-agent"


def test_clean_runner_dependencies_are_explicit_and_lint_is_pinned() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    test_dependencies = project["project"]["optional-dependencies"]["test"]
    assert any(re.match(r"pyproj(?:[<>=!~].*)?$", item) for item in dependencies)
    assert "ruff==0.12.12" in test_dependencies


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_plugin_manifests_and_stdio_entrypoint_are_portable() -> None:
    codex = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    claude = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert codex["name"] == claude["name"] == PLUGIN.name
    assert codex["version"] == claude["version"]
    assert codex["mcpServers"] == "./.mcp.json"

    server = _json(PLUGIN / ".mcp.json")["mcpServers"]["cad2gis"]
    assert server["command"] == "cad2gis-agent-mcp"
    assert server["args"] == ["--transport", "stdio"]
    assert "cwd" not in server
    assert "CAD2GIS_PROJECT_ROOTS" in server["env_vars"]


def test_codex_and_claude_marketplaces_publish_the_same_plugin() -> None:
    codex = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    codex_entry = codex["plugins"][0]
    assert codex["name"] == "cad2gis"
    assert codex_entry["name"] == PLUGIN.name
    assert codex_entry["source"] == {
        "source": "local",
        "path": "./plugins/cad2gis-agent",
    }
    assert codex_entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }

    claude = _json(ROOT / ".claude-plugin" / "marketplace.json")
    claude_entry = claude["plugins"][0]
    assert claude["name"] == "cad2gis-tools"
    assert claude_entry["name"] == PLUGIN.name
    assert claude_entry["source"] == "./plugins/cad2gis-agent"


def test_mainstream_client_templates_share_the_canonical_entrypoint() -> None:
    clients = PLUGIN / "clients"
    for name in (
        "claude-code.mcp.json",
        "cursor.mcp.json",
    ):
        server = _json(clients / name)["mcpServers"]["cad2gis"]
        assert server["command"] == "cad2gis-agent-mcp"
        assert server["args"] == ["--transport", "stdio"]
        assert server["env"]["CAD2GIS_PROJECT_ROOTS"] == "<ABSOLUTE_PROJECT_ROOT>"

    vscode = _json(clients / "vscode.mcp.json")["servers"]["cad2gis"]
    assert vscode["type"] == "stdio"
    assert vscode["command"] == "cad2gis-agent-mcp"
    assert vscode["args"] == ["--transport", "stdio"]
    assert vscode["env"]["CAD2GIS_PROJECT_ROOTS"] == "<ABSOLUTE_PROJECT_ROOT>"

    codex = tomllib.loads((clients / "codex.config.toml").read_text(encoding="utf-8"))
    server = codex["mcp_servers"]["cad2gis"]
    assert server["command"] == "cad2gis-agent-mcp"
    assert server["args"] == ["--transport", "stdio"]
    assert server["env"]["CAD2GIS_PROJECT_ROOTS"] == "<ABSOLUTE_PROJECT_ROOT>"

    http = _json(clients / "streamable-http.json")["mcpServers"]["cad2gis"]
    assert http == {"type": "http", "url": "http://127.0.0.1:8768/mcp"}


def test_agent_prompt_contract_is_versioned_and_fail_closed() -> None:
    contract = get_capabilities()["prompt_contract"]
    assert contract["version"] == "cad2gis.agent_prompt.v2"
    assert contract["proposal_mode"] == "typed JSON tool arguments"
    assert contract["required_claims"] == [
        "source_geometry",
        "topology",
        "length",
        "coordinate_accuracy",
    ]
    assert "never invent" in contract["failure_rule"]


def test_plugin_documents_cross_platform_runtime_bootstrap() -> None:
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    plugin_readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    for value in (root_readme, plugin_readme):
        assert "uv tool install --python 3.12" in value
        assert "cad2gis-agent-mcp" in value


@pytest.mark.parametrize("minor", [11, 12])
def test_doctor_accepts_every_declared_python_minor(
    minor: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor.sys,
        "version_info",
        SimpleNamespace(major=3, minor=minor),
    )
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    checks = doctor.collect_checks(
        _contract={"location": str(ROOT), "selected_mode": "test"},
    )
    python_check = next(check for check in checks if check.name == "python")
    assert python_check.status == "ok"
    assert python_check.required_for_conversion is True


def test_doctor_rejects_undeclared_python_minor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor.sys,
        "version_info",
        SimpleNamespace(major=3, minor=13),
    )
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    checks = doctor.collect_checks(
        _contract={"location": str(ROOT), "selected_mode": "test"},
    )
    python_check = next(check for check in checks if check.name == "python")
    assert python_check.status == "error"
