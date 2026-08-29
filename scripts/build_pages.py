from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "src" / "cad2gis" / "webdemo"

ASSETS = (
    "landing.css",
    "landing.js",
    "install.css",
    "install.js",
    "pointer.css",
    "pointer.js",
)


def _rewrite_landing(source: str) -> str:
    return (
        source
        .replace('href="/assets/', 'href="./assets/')
        .replace('src="/assets/', 'src="./assets/')
        .replace('href="/install"', 'href="./install.html"')
        .replace('href="/workspace"', 'href="./install.html#first-run-title"')
        .replace('href="/"', 'href="./"')
        .replace('>工作台</a>', '>本地工作台</a>')
        .replace('进入双地图审查工作台', '安装后打开双地图审查工作台')
        .replace('打开工作台', '本地启动工作台')
    )


def _rewrite_install(source: str) -> str:
    return (
        source
        .replace('href="/assets/', 'href="./assets/')
        .replace('src="/assets/', 'src="./assets/')
        .replace('href="/workspace"', 'href="#first-run-title"')
        .replace('href="/"', 'href="./"')
        .replace('>工作台</a>', '>本地工作台</a>')
        .replace('打开审查工作台', '查看本地启动方法')
    )


def build(output: Path) -> None:
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
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "build-manifest.json").write_text(
        json.dumps({
            "site": "CAD2GIS Agent",
            "pages": ["index.html", "install.html"],
            "mode": "static-github-pages",
            "workspace": "local-only",
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
