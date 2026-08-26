"""Build and verify the data-free static CAD2GIS review demonstration."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "cad2gis" / "webdemo"
PUBLIC_FILES = ("index.html", "styles.css", "app.js", "demo-fixture.js")
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
        "公开页面仅含合成证据",
        "RELATIVE_OSM_REFERENCE_ONLY",
    )
    missing_markers = [value for value in required_markers if value not in fixture]
    if missing_markers:
        raise ValueError(
            "Synthetic demo boundary markers are missing: "
            + ", ".join(missing_markers)
        )


def build_webdemo(destination: Path) -> dict[str, object]:
    """Create the exact directory layout consumed by GitHub Pages."""

    _assert_source_boundary()
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
    for name in ("styles.css", "app.js", "demo-fixture.js"):
        shutil.copy2(SOURCE_ROOT / name, assets / name)
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
        + [f"assets/{name}" for name in ("styles.css", "app.js", "demo-fixture.js")]
    )
    if files != expected:
        raise ValueError(f"Unexpected WebDemo artifact set: {files}")
    return {
        "schema_version": "cad2gis.webdemo_build.v1",
        "destination": str(target),
        "files": files,
        "local_assets": local_assets,
        "contains_project_data": False,
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
