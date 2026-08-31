from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBDEMO = ROOT / "src" / "cad2gis" / "webdemo"


def test_plugin_guide_matches_published_plugin_contracts() -> None:
    page = (WEBDEMO / "index.html").read_text(encoding="utf-8")

    assert 'id="plugin-guide"' in page
    assert page.count('data-plugin-step=') == 5
    assert page.count('data-plugin-platform=') == 4
    assert page.count('data-plugin-client=') == 5
    assert "winget install --id=astral-sh.uv -e" in page
    assert "brew install uv" in page
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in page
    assert "uv tool install --python 3.12" in page
    assert "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git" in page
    assert "cad2gis doctor --deep --strict --json" in page
    assert "CAD2GIS_PROJECT_ROOTS" in page
    assert "codex plugin marketplace add Mola-maker/CAD2GIS --ref main" in page
    assert "codex plugin add cad2gis-agent@cad2gis" in page
    assert "claude plugin install cad2gis-agent@cad2gis-tools" in page
    assert "plugins/cad2gis-agent/clients/cursor.mcp.json" in page
    assert "plugins/cad2gis-agent/clients/vscode.mcp.json" in page
    assert "codex plugin marketplace upgrade cad2gis" in page
    assert "codex plugin remove cad2gis-agent@cad2gis" in page
    assert "claude plugin update cad2gis-agent@cad2gis-tools" in page
    assert "WSL" in page
    assert "Windows QGIS GUI" in page
    assert "cad2gis-agent-mcp" in page
    assert "http://127.0.0.1:8768/mcp" in page
    assert "get_capabilities" in page
    assert "audit_status=PASS" in page
    assert "GitHub Pages 不接收 DWG" in page


def test_plugin_guide_is_progressive_keyboard_and_copy_safe() -> None:
    motion = (WEBDEMO / "hero-motion.js").read_text(encoding="utf-8")
    styles = (WEBDEMO / "styles.css").read_text(encoding="utf-8")

    assert 'querySelector("[data-plugin-guide]")' in motion
    assert 'querySelectorAll("[data-plugin-platform]")' in motion
    assert 'guide.classList.add("is-enhanced")' in motion
    assert 'button.setAttribute("aria-current", "step")' in motion
    assert 'button.setAttribute("aria-selected", String(active))' in motion
    assert '"ArrowLeft", "ArrowRight", "Home", "End"' in motion
    assert "navigator.clipboard?.writeText" in motion
    assert 'document.execCommand("copy")' in motion
    assert ".plugin-guide.is-enhanced .plugin-step-panel:not(.is-active)" in styles
    assert ".plugin-guide.is-enhanced .plugin-platform-panel:not(.is-active)" in styles
    assert ".plugin-guide.is-enhanced .plugin-client-panel:not(.is-active)" in styles
    assert "@media (max-width: 600px)" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_readme_and_pages_share_canonical_install_commands() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    page = (WEBDEMO / "index.html").read_text(encoding="utf-8")

    for command in (
        "winget install --id=astral-sh.uv -e",
        "brew install uv",
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        'uv tool install --python 3.12 "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git"',
        "codex plugin marketplace add Mola-maker/CAD2GIS --ref main",
        "codex plugin add cad2gis-agent@cad2gis",
        "codex plugin remove cad2gis-agent@cad2gis",
        "claude plugin marketplace add Mola-maker/CAD2GIS",
        "claude plugin install cad2gis-agent@cad2gis-tools",
        "plugins/cad2gis-agent/clients/cursor.mcp.json",
        "plugins/cad2gis-agent/clients/vscode.mcp.json",
    ):
        assert command in readme
        assert command in page
