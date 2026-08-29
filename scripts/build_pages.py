from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "src" / "cad2gis" / "webdemo"
DEMO_ROOT = WEB_ROOT / "original-demo"

ASSETS = (
    "landing.css",
    "landing.js",
    "install.css",
    "install.js",
    "pointer.css",
    "pointer.js",
)

DEMO_REQUIRED = (
    "index.html",
    "assets/app.js",
    "assets/styles.css",
    "assets/demo-fixture.js",
    "assets/demo-catalog.json",
)

FORBIDDEN_DEMO_SUFFIXES = {
    ".dwg",
    ".dxf",
    ".gpkg",
    ".qgz",
    ".sqlite",
    ".sqlite3",
}


def _rewrite_landing(source: str) -> str:
    return (
        source
        .replace('href="/assets/', 'href="./assets/')
        .replace('src="/assets/', 'src="./assets/')
        .replace('href="/install"', 'href="./install.html"')
        .replace('href="/workspace"', 'href="./demo/"')
        .replace('href="/"', 'href="./"')
        .replace('>工作台</a>', '>演示台</a>')
        .replace('打开工作台', '打开演示台')
    )


def _rewrite_install(source: str) -> str:
    return (
        source
        .replace('href="/assets/', 'href="./assets/')
        .replace('src="/assets/', 'src="./assets/')
        .replace('href="/workspace"', 'href="./demo/"')
        .replace('href="/"', 'href="./"')
        .replace('>工作台</a>', '>演示台</a>')
        .replace('打开审查工作台', '打开图纸定位演示台')
    )


def _validate_demo() -> None:
    missing = [name for name in DEMO_REQUIRED if not (DEMO_ROOT / name).is_file()]
    if missing:
        raise ValueError("Original WebDemo is incomplete: " + ", ".join(missing))
    forbidden = sorted(
        path.relative_to(DEMO_ROOT).as_posix()
        for path in DEMO_ROOT.rglob("*")
        if path.is_file() and path.suffix.casefold() in FORBIDDEN_DEMO_SUFFIXES
    )
    if forbidden:
        raise ValueError(
            "Refusing to publish CAD/GIS source data: " + ", ".join(forbidden)
        )


def build(output: Path) -> None:
    _validate_demo()
    output = output.resolve()
    if output == PROJECT_ROOT or PROJECT_ROOT not in output.parents:
        raise ValueError("Pages output must be a child directory of the project root")
    if output.exists():
        shutil.rmtree(output)

    assets_dir = output / "assets"
    assets_dir.mkdir(parents=True)
    for filename in ASSETS:
        shutil.copy2(WEB_ROOT / filename, assets_dir / filename)

    landing = (WEB_ROOT / "landing.html").read_text(encoding="utf-8")
    install = (WEB_ROOT / "install.html").read_text(encoding="utf-8")
    (output / "index.html").write_text(
        _rewrite_landing(landing), encoding="utf-8",
    )
    (output / "install.html").write_text(
        _rewrite_install(install), encoding="utf-8",
    )
    shutil.copytree(DEMO_ROOT, output / "demo")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "build-manifest.json").write_text(
        json.dumps({
            "site": "CAD2GIS Agent",
            "pages": ["index.html", "install.html", "demo/index.html"],
            "mode": "static-github-pages",
            "demo": "data-free-original-workspace",
            "contains_source_binaries": False,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the CAD2GIS static GitHub Pages artifact.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "_site",
    )
    args = parser.parse_args()
    build(args.output)
    print(f"Built GitHub Pages artifact: {args.output.resolve()}")


if __name__ == "__main__":
    main()
