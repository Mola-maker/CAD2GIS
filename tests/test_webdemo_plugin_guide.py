from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBDEMO = ROOT / "src" / "cad2gis" / "webdemo"


def test_plugin_guide_matches_published_plugin_contracts() -> None:
    page = (WEBDEMO / "index.html").read_text(encoding="utf-8")

    assert 'id="plugin-guide"' in page
    assert page.count('data-plugin-step=') == 5
    assert "uv tool install --python 3.12" in page
    assert "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git" in page
    assert "cad2gis doctor --deep --strict --json" in page
    assert "CAD2GIS_PROJECT_ROOTS" in page
    assert "codex plugin marketplace add Mola-maker/CAD2GIS --ref main" in page
    assert "codex plugin add cad2gis-agent@cad2gis" in page
    assert "claude plugin install cad2gis-agent@cad2gis-tools" in page
    assert '"command": "cad2gis-agent-mcp"' in page
    assert "http://127.0.0.1:8768/mcp" in page
    assert "get_capabilities" in page
    assert "audit_status=PASS" in page
    assert "GitHub Pages 不接收 DWG" in page


def test_plugin_guide_is_progressive_keyboard_and_copy_safe() -> None:
    motion = (WEBDEMO / "hero-motion.js").read_text(encoding="utf-8")
    styles = (WEBDEMO / "styles.css").read_text(encoding="utf-8")

    assert 'querySelector("[data-plugin-guide]")' in motion
    assert 'guide.classList.add("is-enhanced")' in motion
    assert 'button.setAttribute("aria-current", "step")' in motion
    assert 'button.setAttribute("aria-selected", String(active))' in motion
    assert '"ArrowLeft", "ArrowRight", "Home", "End"' in motion
    assert "navigator.clipboard?.writeText" in motion
    assert 'document.execCommand("copy")' in motion
    assert ".plugin-guide.is-enhanced .plugin-step-panel:not(.is-active)" in styles
    assert ".plugin-guide.is-enhanced .plugin-client-panel:not(.is-active)" in styles
    assert "@media (max-width: 600px)" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
