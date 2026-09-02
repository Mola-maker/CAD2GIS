from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cross_platform_ci_uses_portable_agent_runtime() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "actions/setup-python@v6" in workflow
    assert 'python -m pip install -e ".[agent,test]"' in workflow
    assert "cad2gis runtime install" in workflow
    assert "cad2gis doctor --deep --strict --profile full --json" in workflow
    assert "micromamba" not in workflow
    assert "conda" not in workflow.casefold()
    assert "shell: ${{ matrix.shell }}" not in workflow
    assert "python tools/build_hero_font.py" in workflow
    assert "node --check src/cad2gis/webdemo/hero-motion.js" in workflow
    for value in (
        'os: ubuntu-latest\n            python: "3.11"',
        'os: ubuntu-latest\n            python: "3.12"',
        'os: windows-latest\n            python: "3.12"',
        'os: macos-latest\n            python: "3.12"',
    ):
        assert value in workflow


def test_pages_cd_builds_only_the_verified_site_directory() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )
    assert "python scripts/build_pages.py --output _site" in workflow
    assert "branches: [main]" in workflow
    assert "feature/land-robustness" not in workflow
    assert workflow.count("github.ref == 'refs/heads/main'") == 2
    assert '- "README.md"' in workflow
    assert '- ".agents/plugins/marketplace.json"' in workflow
    assert '- ".claude-plugin/marketplace.json"' in workflow
    assert '- "plugins/cad2gis-agent/**"' in workflow
    assert '- "tests/test_plugin_contracts.py"' in workflow
    assert '- "tests/test_webdemo_plugin_guide.py"' in workflow
    assert '- "scripts/build_pages.py"' in workflow
    assert "tests/test_ci_contracts.py tests/test_webdemo_plugin_guide.py tests/test_pages_publication_gate.py" in workflow
    assert "node --check src/cad2gis/webdemo/install.js" in workflow
    assert "path: _site" in workflow
    assert "path: src/cad2gis/webdemo" not in workflow
    assert "actions/upload-pages-artifact@v5" in workflow
    assert "actions/deploy-pages@v5" in workflow


def test_project_root_overrides_are_combined_for_local_and_ci_runs() -> None:
    source = (ROOT / "src" / "cad2gis" / "agent_mcp.py").read_text(
        encoding="utf-8"
    )
    assert '"CAD2GIS_PROJECT_ROOTS", "CAD2GIS_PROJECT_ROOT"' in source
    assert "if root not in roots" in source
