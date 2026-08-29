from __future__ import annotations

import importlib.util
import json
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
            "assets/noto-sans-sc-subset.woff2",
            "assets/smiley-sans-subset.woff2",
            "assets/styles.css",
        ])
        assert result["files"] == sorted([
            ".cad2gis-webdemo-build",
            ".nojekyll",
            "assets/app.js",
            "assets/cad2gis-hero-display.woff2",
            "assets/demo-catalog.json",
            "assets/demo-data-darat-sekip-sf.json",
            "assets/demo-data.json",
            "assets/demo-data-kletek.json",
            "assets/demo-data-lamteh-main.json",
            "assets/demo-data-lamteh-sf.json",
            "assets/demo-data-manado-tomohon-uplink.json",
            "assets/demo-data-semarang-sf.json",
            "assets/demo-data-taipa.json",
            "assets/demo-data-tinggar.json",
            "assets/demo-data-tinggede.json",
            "assets/demo-fixture.js",
            "assets/font-licenses/OFL-IBMPlexMono.txt",
            "assets/font-licenses/OFL-NotoSansSC.txt",
            "assets/font-licenses/OFL-SmileySans.txt",
            "assets/font-licenses/OFL-SpaceGrotesk.txt",
            "assets/hero-evidence-graph.svg",
            "assets/hero-geometry.json",
            "assets/hero-grid.svg",
            "assets/hero-motion.js",
            "assets/hero-stickers.svg",
            "assets/hero-tunnel.svg",
            "assets/ibm-plex-mono-regular-subset.woff2",
            "assets/ibm-plex-mono-semibold-subset.woff2",
            "assets/noto-sans-sc-subset.woff2",
            "assets/smiley-sans-subset.woff2",
            "assets/space-grotesk-subset.woff2",
            "assets/styles.css",
            "index.html",
        ])
        assert (destination / "assets" / "app.js").is_file()
        assert (destination / "assets" / "demo-catalog.json").is_file()
        assert (destination / "assets" / "demo-data.json").is_file()
        catalog = json.loads(
            (destination / "assets" / "demo-catalog.json").read_text(encoding="utf-8")
        )
        assert all(
            (destination / "assets" / project["fixture"]).is_file()
            for project in catalog["projects"]
        )
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
