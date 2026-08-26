from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_builder():
    path = ROOT / "tools" / "build_webdemo.py"
    spec = importlib.util.spec_from_file_location("cad2gis_build_webdemo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_webdemo_build_matches_browser_asset_contract(tmp_path: Path) -> None:
    builder = _load_builder()
    destination = ROOT / f".pytest-webdemo-build-{tmp_path.name}"
    try:
        result = builder.build_webdemo(destination)
        assert result["contains_project_data"] is True
        assert result["contains_source_binaries"] is False
        assert result["local_assets"] == sorted([
            "assets/app.js",
            "assets/cad2gis-hero-display.woff2",
            "assets/demo-fixture.js",
            "assets/hero-evidence-graph.svg",
            "assets/hero-stickers.svg",
            "assets/hero-tunnel.svg",
            "assets/hero-motion.js",
            "assets/styles.css",
        ])
        assert result["files"] == sorted([
            ".cad2gis-webdemo-build",
            ".nojekyll",
            "assets/app.js",
            "assets/cad2gis-hero-display.woff2",
            "assets/demo-data.json",
            "assets/demo-fixture.js",
            "assets/hero-evidence-graph.svg",
            "assets/hero-geometry.json",
            "assets/hero-grid.svg",
            "assets/hero-motion.js",
            "assets/hero-stickers.svg",
            "assets/hero-tunnel.svg",
            "assets/styles.css",
            "index.html",
        ])
        assert (destination / "assets" / "app.js").is_file()
        assert (destination / "assets" / "demo-data.json").is_file()
        assert not any(
            path.suffix.casefold() in builder.FORBIDDEN_SUFFIXES
            for path in destination.rglob("*")
        )
    finally:
        if destination.exists():
            import shutil

            shutil.rmtree(destination)


def test_webdemo_build_refuses_to_delete_unowned_directory(tmp_path: Path) -> None:
    builder = _load_builder()
    destination = ROOT / f".pytest-webdemo-build-unsafe-{tmp_path.name}"
    destination.mkdir()
    preserved = destination / "production.py"
    preserved.write_text("must survive\n", encoding="utf-8")
    try:
        import pytest

        with pytest.raises(ValueError, match="not owned"):
            builder.build_webdemo(destination)
        assert preserved.read_text(encoding="utf-8") == "must survive\n"
    finally:
        if destination.exists():
            import shutil

            shutil.rmtree(destination)
