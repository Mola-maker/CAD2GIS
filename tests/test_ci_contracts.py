from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cross_platform_ci_uses_native_gis_runtime() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "mamba-org/setup-micromamba@v3" in workflow
    assert "gdal=3.10.*" in workflow
    assert "pyproj=3.7.*" in workflow
    assert "init-shell: none" in workflow
    assert "shell: ${{ matrix.shell }}" not in workflow
    assert workflow.count("micromamba run -n cad2gis-ci") == 7
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
    assert "python tools/build_webdemo.py --output _site" in workflow
    assert '- "pyproject.toml"' in workflow
    assert '- "env/environment.yml"' in workflow
    assert "mamba-org/setup-micromamba@v3" in workflow
    assert "environment-file: env/environment.yml" in workflow
    assert "shell: bash -el {0}" in workflow
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
