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
    assert workflow.count("shell: micromamba-shell {0}") == 7
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
    assert "path: _site" in workflow
    assert "path: src/cad2gis/webdemo" not in workflow
    assert "actions/upload-pages-artifact@v5" in workflow
    assert "actions/deploy-pages@v5" in workflow
