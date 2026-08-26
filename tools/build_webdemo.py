"""Build and verify the data-free static CAD2GIS review demonstration."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "cad2gis" / "webdemo"
PUBLIC_FILES = (
    "index.html",
    "styles.css",
    "app.js",
    "hero-motion.js",
    "demo-fixture.js",
    "demo-data.json",
)
PUBLIC_ASSET_DIR = SOURCE_ROOT / "assets"
BUILD_SENTINEL = ".cad2gis-webdemo-build"
BUILD_SENTINEL_VALUE = "cad2gis.webdemo_build.v1\n"
FORBIDDEN_SUFFIXES = {
    ".dwg",
    ".dxf",
    ".gpkg",
    ".qgz",
    ".sqlite",
    ".sqlite3",
}
LOCAL_ASSET_PATTERN = re.compile(
    r"(?:href|src)=[\"'](?P<path>\./assets/[^\"'#?]+)",
    re.IGNORECASE,
)


def _assert_source_boundary() -> None:
    missing = [name for name in PUBLIC_FILES if not (SOURCE_ROOT / name).is_file()]
    if missing:
        raise ValueError(f"WebDemo source is incomplete: {', '.join(missing)}")
    forbidden = sorted(
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*")
        if path.is_file() and path.suffix.casefold() in FORBIDDEN_SUFFIXES
    )
    if forbidden:
        raise ValueError(
            "Refusing to publish CAD/GIS project data: " + ", ".join(forbidden)
        )
    fixture = (SOURCE_ROOT / "demo-fixture.js").read_text(encoding="utf-8")
    required_markers = (
        "window.CAD2GIS_DEMO",
        "HUTABOHU_DERIVED_FIXTURE",
        "公开页面仅含 Hutabohu 真实转换的筛选派生证据",
        "RELATIVE_OSM_REFERENCE_ONLY",
    )
    missing_markers = [value for value in required_markers if value not in fixture]
    if missing_markers:
        raise ValueError(
            "Synthetic demo boundary markers are missing: "
            + ", ".join(missing_markers)
        )
    try:
        data = json.loads((SOURCE_ROOT / "demo-data.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Hutabohu WebDemo data is not valid JSON") from exc
    if data.get("provenance", {}).get("fixture_kind") != "HUTABOHU_DERIVED_FIXTURE":
        raise ValueError("WebDemo data must be the reviewed Hutabohu fixture")
    serialized = json.dumps(data, ensure_ascii=False)
    forbidden_markers = (
        "E:\\",
        "C:\\",
        "/home/",
        "delivery.gpkg",
        "evidence.gpkg",
        "source.gpkg",
    )
    leaked = [value for value in forbidden_markers if value in serialized]
    if leaked:
        raise ValueError("WebDemo data contains private source markers: " + ", ".join(leaked))
    if not PUBLIC_ASSET_DIR.is_dir():
        raise ValueError("WebDemo hero assets are missing")
    hero_assets = sorted(
        path for path in PUBLIC_ASSET_DIR.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".svg", ".json"}
    )
    if not hero_assets:
        raise ValueError("WebDemo hero asset directory is empty")


def build_webdemo(destination: Path) -> dict[str, object]:
    """Create the exact directory layout consumed by GitHub Pages."""

    _assert_source_boundary()
    hero_assets = sorted(
        path for path in PUBLIC_ASSET_DIR.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".svg", ".json"}
    )
    target = destination.expanduser().resolve()
    if target == REPOSITORY_ROOT or REPOSITORY_ROOT not in target.parents:
        raise ValueError("WebDemo destination must be inside the repository")
    if target.exists():
        sentinel = target / BUILD_SENTINEL
        if not target.is_dir() or not sentinel.is_file():
            raise ValueError(
                "Refusing to replace an existing directory not owned by the "
                "WebDemo builder"
            )
        if sentinel.read_text(encoding="utf-8") != BUILD_SENTINEL_VALUE:
            raise ValueError("WebDemo build sentinel is invalid")
        shutil.rmtree(target)
    assets = target / "assets"
    assets.mkdir(parents=True)
    (target / BUILD_SENTINEL).write_text(
        BUILD_SENTINEL_VALUE,
        encoding="utf-8",
    )

    shutil.copy2(SOURCE_ROOT / "index.html", target / "index.html")
    for name in ("styles.css", "app.js", "hero-motion.js", "demo-fixture.js", "demo-data.json"):
        shutil.copy2(SOURCE_ROOT / name, assets / name)
    for source in PUBLIC_ASSET_DIR.rglob("*"):
        if source.is_file():
            asset_target = assets / source.relative_to(PUBLIC_ASSET_DIR)
            asset_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, asset_target)
    (target / ".nojekyll").write_text("", encoding="utf-8")

    page = (target / "index.html").read_text(encoding="utf-8")
    local_assets = sorted(
        {match.group("path").removeprefix("./") for match in LOCAL_ASSET_PATTERN.finditer(page)}
    )
    missing_assets = [value for value in local_assets if not (target / value).is_file()]
    if missing_assets:
        raise ValueError(
            "Built WebDemo has unresolved local assets: " + ", ".join(missing_assets)
        )

    files = sorted(
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    )
    expected = sorted(
        [BUILD_SENTINEL, ".nojekyll", "index.html"]
        + [
            f"assets/{name}"
            for name in ("styles.css", "app.js", "hero-motion.js", "demo-fixture.js", "demo-data.json")
        ]
        + [f"assets/{path.relative_to(PUBLIC_ASSET_DIR).as_posix()}" for path in hero_assets]
    )
    if files != expected:
        raise ValueError(f"Unexpected WebDemo artifact set: {files}")
    return {
        "schema_version": "cad2gis.webdemo_build.v1",
        "destination": str(target),
        "files": files,
        "local_assets": local_assets,
        "contains_project_data": True,
        "contains_source_binaries": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "_site",
        help="Repository-local output directory (default: ./_site).",
    )
    args = parser.parse_args(argv)
    print(json.dumps(build_webdemo(args.output), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
