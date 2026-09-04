from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import build_pages


ROOT = Path(__file__).resolve().parents[1]
WEBDEMO = ROOT / "src" / "cad2gis" / "webdemo"


def _page_config(page: str) -> dict:
    match = re.search(
        r'<script id="cad2gis-page-config" type="application/json">(.*?)</script>',
        page, re.DOTALL,
    )
    assert match is not None
    return json.loads(match[1])


def _node(script: str, payload: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for browser-script behavior checks")
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(payload), text=True,
        encoding="utf-8", capture_output=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("public", [False, True])
def test_shared_app_preserves_each_pages_initial_map_behavior(public: bool) -> None:
    page = WEBDEMO / "original-demo/index.html" if public else WEBDEMO / "index.html"
    config = _page_config(page.read_text(encoding="utf-8"))
    # Execute map initialization with an OpenLayers recording double. This checks
    # control construction and View options rather than JS spelling.
    result = _node(r"""
const fs = require("node:fs");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
class Options { constructor(options) { Object.assign(this, options); } }
class Projection extends Options { getExtent() { return this.extent; } }
const ol = {
  format: { GeoJSON: Options }, extent: { createEmpty: () => [] },
  source: { Vector: Options, OSM: Options },
  layer: { Vector: Options, Tile: Options }, Map: Options, View: Options,
  proj: { Projection, fromLonLat: (value) => value },
  control: { ZoomToExtent: Options, defaults: { defaults: () => ({
    items: [], extend(items) { this.items.push(...items); return this; },
  }) } },
};
const context = { ol, document: { getElementById: () => ({
  textContent: JSON.stringify(input.config),
}) } };
const script = fs.readFileSync(input.app, "utf8").split("const fitSimilarity =")[0];
const maps = vm.runInNewContext(script + "\n({local: localMap, world: worldMap})", context);
process.stdout.write(JSON.stringify(maps));
""", {"config": config, "app": str(WEBDEMO / "app.js")})
    assert result["local"]["view"]["center"] == [0, 0]
    assert result["local"]["view"]["zoom"] == 2
    for name in ("local", "world"):
        assert result[name]["view"]["maxZoom"] == 50
        assert result[name]["view"]["constrainResolution"] is False
    assert result["world"]["view"]["center"] == ([112.7, -7.45] if public else [0, 0])
    assert result["world"]["view"]["zoom"] == (5 if public else 2)
    assert result["local"]["controls"]["items"] == (
        [{"extent": [-1e12, -1e12, 1e12, 1e12]}] if public else []
    )
    assert result["world"]["controls"]["items"] == (
        [{"extent": [12541000, -824000, 12547000, -818000]}] if public else []
    )


@pytest.mark.parametrize("surface", ["local", "pages", "original"])
def test_shared_fixture_keeps_catalog_scope_cache_urls_and_project_state(surface: str) -> None:
    public = surface != "local"
    source = WEBDEMO / "original-demo/index.html" if public else WEBDEMO / "index.html"
    page = source.read_text(encoding="utf-8")
    if surface == "pages":
        page = build_pages._workspace_page(page)
    asset_root = WEBDEMO / "original-demo/assets" if public else WEBDEMO
    catalog = json.loads((asset_root / "demo-catalog.json").read_text(encoding="utf-8"))
    fixtures = {
        item["fixture"]: json.loads((asset_root / item["fixture"]).read_text(encoding="utf-8"))
        for item in catalog["projects"]
    }
    prefix = {"local": "/repo/", "pages": "/repo/workspace/", "original": "/repo/original-demo/"}[surface]
    script_path = "/repo/demo-fixture.js" if surface == "original" else prefix + "assets/demo-fixture.js"
    result = _node(r"""
const fs = require("node:fs");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const requested = [];
const context = {
  URL, URLSearchParams, window: {},
  location: new URL("https://example.test" + input.prefix + "?demo=1&project=unknown"),
  history: { replaceState() {} },
  document: {
    currentScript: { src: "https://example.test" + input.scriptPath },
    getElementById: () => ({ textContent: JSON.stringify(input.config) }),
  },
  fetch: async (url) => {
    requested.push(String(url));
    const name = new URL(url).pathname.split("/").pop();
    const data = name === "demo-catalog.json" ? input.catalog : input.fixtures[name];
    if (!data) throw new Error("Unexpected asset: " + url);
    return { ok: true, json: async () => data };
  },
};
vm.runInNewContext(fs.readFileSync(input.script, "utf8"), context);
(async () => {
  const demo = context.window.CAD2GIS_DEMO;
  const run = await demo.request("/api/run");
  const ids = (await demo.catalog()).projects.map((project) => project.id);
  const feature = { id: "saved", properties: {}, geometry: { type: "Point", coordinates: [0, 0] } };
  await demo.request("/api/review/features", { method: "POST", body: JSON.stringify({ feature }) });
  let switchedReviewCount;
  for (const id of ids.slice(1)) {
    await demo.selectProject(id);
    const nextRun = await demo.request("/api/run");
    if (nextRun.demo.project_id !== id) throw new Error("Wrong fixture for " + id);
    switchedReviewCount = (await demo.request("/api/review/features")).features.length;
    if (switchedReviewCount !== 0) throw new Error("Review state crossed projects");
  }
  await demo.selectProject("hutabohu");
  const saved = await demo.request("/api/review/features");
  process.stdout.write(JSON.stringify({ ids, requested, initial: run.demo.project_id,
    restoredReviewCount: saved.features.length, switchedReviewCount }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
""", {
        "script": str(WEBDEMO / "demo-fixture.js"), "config": _page_config(page),
        "prefix": prefix, "scriptPath": script_path, "catalog": catalog, "fixtures": fixtures,
    })
    assert result["ids"] == [item["id"] for item in catalog["projects"]]
    assert len(result["ids"]) == (1 if public else 10)
    assert result["initial"] == "hutabohu"
    assert result["restoredReviewCount"] == 1
    if not public:
        assert result["switchedReviewCount"] == 0
    assert result["requested"][0] == (
        f"https://example.test{prefix}assets/demo-catalog.json?v=multi-demo-20260829"
    )
    version = "multi-demo-20260829" if public else "runtime-fix-20260901"
    assert set(result["requested"][1:]) == {
        f"https://example.test{prefix}assets/{name}?v={version}" for name in fixtures
    }


def test_pages_copies_shared_assets_without_expanding_public_fixtures(tmp_path: Path) -> None:
    destination = ROOT / f".pytest-pages-shared-{tmp_path.name}"
    try:
        build_pages.build(destination)
        assets = destination / "workspace/assets"
        for name in build_pages.SHARED_DEMO_ASSETS:
            assert (assets / name).read_bytes() == (WEBDEMO / "assets" / name).read_bytes()
            assert not (WEBDEMO / "original-demo/assets" / name).exists()
        for name in build_pages.SHARED_DEMO_SCRIPTS:
            assert (assets / name).read_bytes() == (WEBDEMO / name).read_bytes()
        assert sorted(path.name for path in assets.glob("demo-data*.json")) == ["demo-data.json"]
        assert (assets / "demo-catalog.json").read_bytes() == (
            WEBDEMO / "original-demo/assets/demo-catalog.json"
        ).read_bytes()
        assert len(list((assets / "font-licenses").glob("*.txt"))) == 4
        original_page = (WEBDEMO / "original-demo/index.html").read_text(encoding="utf-8")
        built_page = (destination / "workspace/index.html").read_text(encoding="utf-8")
        assert _page_config(built_page) == _page_config(original_page)
        assert 'id="hero-page"' in original_page
        assert 'id="hero-page"' not in built_page
        # Follow URLs from both the source page and generated page, including
        # CSS-relative fonts and the standalone app's module import.
        paths = [WEBDEMO / "original-demo/index.html", destination / "workspace/index.html"]
        checked = set()
        while paths:
            path = paths.pop().resolve()
            if path.is_dir():
                path /= "index.html"
            if path in checked:
                continue
            checked.add(path)
            assert path.is_file(), path
            if path.suffix not in {".html", ".css", ".js"}:
                continue
            text = path.read_text(encoding="utf-8")
            references = re.findall(r'(?:src=|href=|url\(|import )["\'](\.[^"\')]+)', text)
            paths.extend(path.parent / value.split("?")[0] for value in references)
    finally:
        if destination.exists():
            shutil.rmtree(destination)
