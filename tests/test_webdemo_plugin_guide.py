from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBDEMO = ROOT / "src" / "cad2gis" / "webdemo"

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
    'uv tool install --python 3.12 "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git"',
    "cad2gis doctor --deep --strict --json",
    "codex plugin add cad2gis-agent@cad2gis",
    "codex plugin list",
    "codex plugin remove cad2gis-agent@cad2gis",
    "claude plugin install cad2gis-agent@cad2gis-tools",
    "claude plugin list",
    "claude plugin update cad2gis-agent@cad2gis-tools",
    "claude plugin uninstall cad2gis-agent@cad2gis-tools",
    "uv tool upgrade cad2gis",
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
        'id="uninstall-commands"',
    ):
        assert section_id in page
    assert "get_capabilities" in page
    assert "WSL → Windows GUI" in page


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
