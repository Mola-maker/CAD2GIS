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
    "hero-tube.js",
    "install.css",
    "install.js",
    "pointer.css",
    "pointer.js",
    "workspace-shell.css",
    "architecture.css",
)

DEMO_REQUIRED = (
    "index.html",
    "assets/app.js",
    "assets/styles.css",
    "assets/demo-catalog.json",
)

# Only these data-free assets are shared with the local, ten-case console.
# Public fixtures and their publication gate remain under original-demo/assets.
SHARED_DEMO_SCRIPTS = ("app.js", "demo-fixture.js")
SHARED_DEMO_ASSETS = (
    "cad2gis-hero-display.woff2",
    "hero-evidence-graph.svg",
    "hero-geometry.json",
    "hero-grid.svg",
    "hero-stickers.svg",
    "hero-tunnel.svg",
    "ibm-plex-mono-regular-subset.woff2",
    "ibm-plex-mono-semibold-subset.woff2",
    "noto-sans-sc-subset.woff2",
    "smiley-sans-subset.woff2",
    "space-grotesk-subset.woff2",
    "font-licenses/OFL-IBMPlexMono.txt",
    "font-licenses/OFL-NotoSansSC.txt",
    "font-licenses/OFL-SmileySans.txt",
    "font-licenses/OFL-SpaceGrotesk.txt",
)

FORBIDDEN_DEMO_SUFFIXES = {
    ".dwg",
    ".dxf",
    ".gpkg",
    ".qgz",
    ".sqlite",
    ".sqlite3",
}

CONDITIONAL_LEGACY_PROJECT_ID = "hutabohu"


def _publication_gate_passed(project: dict[str, object]) -> bool:
    if project.get("id") == CONDITIONAL_LEGACY_PROJECT_ID:
        return True
    gate = project.get("publication_gate")
    if not isinstance(gate, dict):
        return False
    return (
        gate.get("calibration_status") == "verified"
        and gate.get("control_authority") in {"surveyed", "official"}
        and isinstance(gate.get("independent_checkpoint_count"), int)
        and gate["independent_checkpoint_count"] >= 3
        and gate.get("spatial_coverage_passed") is True
        and gate.get("run_status") == "VERIFIED"
    )


def _rewrite_landing(source: str) -> str:
    return (
        source
        .replace('href="/assets/', 'href="./assets/')
        .replace('src="/assets/', 'src="./assets/')
        .replace('href="/install"', 'href="./install.html"')
        .replace('href="/workspace"', 'href="./workspace/"')
        .replace('href="/"', 'href="./"')
        .replace('>工作台</a>', '>演示台</a>')
        .replace('打开工作台', '打开演示台')
    )


def _rewrite_install(source: str) -> str:
    return (
        source
        .replace('href="/assets/', 'href="./assets/')
        .replace('src="/assets/', 'src="./assets/')
        .replace('href="/workspace"', 'href="./workspace/"')
        .replace('href="/"', 'href="./"')
        .replace('>工作台</a>', '>演示台</a>')
        .replace('打开审查工作台', '打开图纸定位演示台')
    )


def _validate_demo() -> None:
    missing = [name for name in DEMO_REQUIRED if not (DEMO_ROOT / name).is_file()]
    if missing:
        raise ValueError("Original WebDemo is incomplete: " + ", ".join(missing))
    shared = SHARED_DEMO_SCRIPTS + tuple(f"assets/{name}" for name in SHARED_DEMO_ASSETS)
    missing_shared = [name for name in shared if not (WEB_ROOT / name).is_file()]
    if missing_shared:
        raise ValueError("Shared WebDemo assets are incomplete: " + ", ".join(missing_shared))
    forbidden = sorted(
        path.relative_to(DEMO_ROOT).as_posix()
        for path in DEMO_ROOT.rglob("*")
        if path.is_file() and path.suffix.casefold() in FORBIDDEN_DEMO_SUFFIXES
    )
    if forbidden:
        raise ValueError(
            "Refusing to publish CAD/GIS source data: " + ", ".join(forbidden)
        )
    catalog = json.loads(
        (DEMO_ROOT / "assets" / "demo-catalog.json").read_text(encoding="utf-8")
    )
    projects = catalog.get("projects", [])
    if not projects or projects[0].get("id") != CONDITIONAL_LEGACY_PROJECT_ID:
        raise ValueError("Public demo must retain the conditional Hutabohu case")
    for project in projects:
        if not _publication_gate_passed(project):
            raise ValueError(
                f"Public demo calibration gate failed: {project.get('id')!r}"
            )
        fixture = project.get("fixture")
        if not isinstance(fixture, str) or not (DEMO_ROOT / "assets" / fixture).is_file():
            raise ValueError(f"Public demo fixture is missing: {fixture!r}")


def _workspace_page(source: str) -> str:
    config_start = source.index('  <script id="cad2gis-page-config" type="application/json">')
    config_end = source.index("</script>", config_start) + len("</script>")
    page_config = source[config_start:config_end]
    fragment_start = source.index('  <div id="console-app">')
    fragment_end = source.index('  <script src="https://cdn.jsdelivr.net/npm/ol@10.6.1/dist/ol.js">')
    fragment = source[fragment_start:fragment_end]
    fragment = fragment.replace(
        '<div class="brand-lockup">',
        '<a class="brand-lockup" href="../" aria-label="返回 CAD2GIS 产品首页">',
        1,
    ).replace(
        '    </div>\n    <div class="run-state" aria-label="服务状态">',
        '    </a>\n    <nav class="workspace-product-nav" aria-label="产品导航">'
        '<a href="../">产品首页</a><a href="../install.html">安装</a></nav>\n'
        '    <div class="run-state" aria-label="服务状态">',
        1,
    )
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#f5f8f7">
  <title>CAD2GIS — 图纸理解与交付控制台</title>
{page_config}
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ol@10.6.1/ol.css">
  <link rel="preload" href="./assets/cad2gis-hero-display.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="preload" href="./assets/noto-sans-sc-subset.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="./assets/styles.css">
  <link rel="stylesheet" href="../assets/workspace-shell.css">
  <link rel="stylesheet" href="../assets/pointer.css">
</head>
<body class="workspace-subpage">
{fragment}
  <script src="https://cdn.jsdelivr.net/npm/ol@10.6.1/dist/ol.js"></script>
  <script src="./assets/demo-fixture.js"></script>
  <script type="module" src="./assets/app.js"></script>
  <script type="module" src="../assets/pointer.js"></script>
</body>
</html>
'''


def _demo_redirect() -> str:
    return '''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url=../workspace/">
  <title>正在打开图纸理解与交付控制台</title>
</head>
<body><p>正在前往<a href="../workspace/">图纸理解与交付控制台</a>…</p></body>
</html>
'''


def build(output: Path, *, release_root: Path | None = None) -> None:
    _validate_demo()
    release_root = release_root or PROJECT_ROOT / "pages-delivery" / "nine-drawings"
    release_catalog = None
    if release_root.exists():
        try:
            from scripts.verify_derived_release import verify
        except ModuleNotFoundError:
            from verify_derived_release import verify
        release_catalog = verify(release_root)
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
    architecture = (WEB_ROOT / "architecture.html").read_text(encoding="utf-8")
    if release_catalog is None:
        architecture = architecture.replace('href="./deliveries/', 'href="https://mola-maker.github.io/CAD2GIS/deliveries/')
    (output / "architecture.html").write_text(architecture, encoding="utf-8")
    workspace_dir = output / "workspace"
    workspace_assets = workspace_dir / "assets"
    shutil.copytree(DEMO_ROOT / "assets", workspace_assets)
    if release_catalog is not None:
        shutil.copytree(release_root, output / "deliveries")
        catalog_path = workspace_assets / "demo-catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["projects"].extend(release_catalog["projects"])
        catalog["publication_boundary"] = "含授权九图完整派生成果；不含 DWG；CONDITIONAL，绝对 GCP 精度未验收。"
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        for project in release_catalog["projects"]:
            shutil.copy2(release_root / "assets" / project["fixture"], workspace_assets / project["fixture"])
    for filename in SHARED_DEMO_SCRIPTS:
        shutil.copy2(WEB_ROOT / filename, workspace_assets / filename)
    for filename in SHARED_DEMO_ASSETS:
        destination = workspace_assets / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WEB_ROOT / "assets" / filename, destination)
    # Source HTML/CSS can be served in place; the Pages artifact is self-contained.
    stylesheet = (workspace_assets / "styles.css").read_text(encoding="utf-8")
    (workspace_assets / "styles.css").write_text(
        stylesheet.replace('url("../../assets/', 'url("./'), encoding="utf-8",
    )
    demo_source = (DEMO_ROOT / "index.html").read_text(encoding="utf-8")
    if release_catalog is not None:
        demo_source = demo_source.replace('multi-demo-20260829', 'nine-delivery-20260906')
        demo_source = demo_source.replace('左图始终保留原始 CAD 坐标，图层开关同步作用于右侧预览。', '左图显示交付坐标几何，右图按声明 CRS 投影。原 CAD 对比见九图审查叠图。')
        demo_source = demo_source.replace('<b>CAD</b> 原始坐标', '<b>GIS</b> 交付坐标')
        demo_source = demo_source.replace('源几何 ↔ 地理预览', '交付几何 ↔ 地理预览')
    (workspace_dir / "index.html").write_text(
        _workspace_page(demo_source).replace('<a href="../install.html">安装</a>', '<a href="../install.html">安装</a><a href="../architecture.html">架构</a>' + ('<a href="../deliveries/">九图交付与过程</a>' if release_catalog else '')), encoding="utf-8",
    )
    demo_redirect_dir = output / "demo"
    demo_redirect_dir.mkdir()
    (demo_redirect_dir / "index.html").write_text(
        _demo_redirect(), encoding="utf-8",
    )
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "build-manifest.json").write_text(
        json.dumps({
            "site": "CAD2GIS Agent",
            "pages": [
                "index.html",
                "install.html",
                "architecture.html",
                "workspace/index.html",
            ],
            "mode": "static-github-pages",
            "workspace": "authorized-derived-delivery-console" if release_catalog else "data-free-original-console",
            "contains_derived_qgis_deliveries": release_catalog is not None,
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
