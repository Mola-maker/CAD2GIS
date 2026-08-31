from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBDEMO = ROOT / "src" / "cad2gis" / "webdemo"
RUNTIME_INSTALL = (
    'uv tool install --python 3.12 --force "cad2gis[agent] @ '
    'https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"'
)

MARKETPLACE_COMMANDS = (
    "codex plugin marketplace add Mola-maker/CAD2GIS --ref main",
    "codex plugin marketplace upgrade cad2gis",
    "claude plugin marketplace add Mola-maker/CAD2GIS",
    "claude plugin marketplace update cad2gis-tools",
)

CANONICAL_COMMANDS = (
    "winget install --id=astral-sh.uv -e",
    "brew install uv",
    "curl -LsSf https://astral.sh/uv/install.sh | sh",
    RUNTIME_INSTALL,
    "cad2gis-agent-mcp --help",
    "cad2gis doctor --deep --json",
    "cad2gis doctor --deep --strict --json",
    "codex plugin add cad2gis-agent@cad2gis",
    "codex plugin list",
    "codex plugin remove cad2gis-agent@cad2gis",
    "claude plugin install cad2gis-agent@cad2gis-tools",
    "claude plugin list",
    "claude plugin update cad2gis-agent@cad2gis-tools",
    "claude plugin uninstall cad2gis-agent@cad2gis-tools",
    "uv tool uninstall cad2gis",
    "plugins/cad2gis-agent/clients/cursor.mcp.json",
    "plugins/cad2gis-agent/clients/vscode.mcp.json",
)


def test_readme_and_pages_share_the_install_and_marketplace_commands() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    page = (WEBDEMO / "install.html").read_text(encoding="utf-8")

    for command in (*MARKETPLACE_COMMANDS, *CANONICAL_COMMANDS):
        assert command in readme
        assert command in page


def test_install_page_lists_every_platform_client_and_lifecycle_block() -> None:
    page = (WEBDEMO / "install.html").read_text(encoding="utf-8")

    for label in (
        "Windows PowerShell",
        "macOS",
        "Linux",
        "WSL 限制",
        "Codex",
        "Claude Code",
        "Cursor",
        "VS Code / Copilot",
    ):
        assert label in page
    for section_id in (
        'id="install-commands"',
        'id="verify-commands"',
        'id="update-commands"',
        'id="repair-commands"',
        'id="uninstall-commands"',
    ):
        assert section_id in page
    assert "get_capabilities" in page
    assert "WSL → Windows GUI" in page
    assert "CONNECTION_CLOSED" in page
    assert "ModuleNotFoundError: No module named 'cad2gis'" in page
    assert "editable" in page


def test_runtime_install_is_self_healing_and_not_git_history_bound() -> None:
    surfaces = (
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "plugins" / "cad2gis-agent" / "README.md").read_text(encoding="utf-8"),
        (WEBDEMO / "install.html").read_text(encoding="utf-8"),
        (WEBDEMO / "index.html").read_text(encoding="utf-8"),
        (WEBDEMO / "original-demo" / "index.html").read_text(encoding="utf-8"),
    )

    for value in surfaces:
        assert RUNTIME_INSTALL in value
        assert "git+https://github.com/Mola-maker/CAD2GIS.git" not in value
        assert "uv tool upgrade cad2gis" not in value


def test_install_page_terminal_and_copy_controls_are_accessible() -> None:
    page = (WEBDEMO / "install.html").read_text(encoding="utf-8")
    motion = (WEBDEMO / "install.js").read_text(encoding="utf-8")
    styles = (WEBDEMO / "install.css").read_text(encoding="utf-8")

    assert 'id="terminal-output"' in page
    assert 'aria-live="polite"' in page
    assert page.count("data-copy=") >= 14
    assert "navigator.clipboard.writeText" in motion
    assert "prefers-reduced-motion: reduce" in motion
    assert "@media (prefers-reduced-motion: reduce)" in styles
