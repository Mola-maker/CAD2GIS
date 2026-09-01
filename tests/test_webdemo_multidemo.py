from __future__ import annotations

import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBDEMO = ROOT / "src" / "cad2gis" / "webdemo"


def test_catalog_exposes_ten_source_bound_real_runs() -> None:
    catalog = json.loads((WEBDEMO / "demo-catalog.json").read_text(encoding="utf-8"))

    assert catalog["schema_version"] == "cad2gis.webdemo_catalog.v1"
    assert catalog["default_project"] == "hutabohu"
    assert [project["id"] for project in catalog["projects"]] == [
        "hutabohu",
        "lamteh-main",
        "lamteh-sf",
        "kletek",
        "darat-sekip-sf",
        "manado-tomohon-uplink",
        "semarang-sf",
        "taipa",
        "tinggar",
        "tinggede",
    ]
    assert all(len(project["source_sha256"]) == 64 for project in catalog["projects"])

    for project in catalog["projects"]:
        fixture = json.loads((WEBDEMO / project["fixture"]).read_text(encoding="utf-8"))
        assert fixture["provenance"]["fixture_kind"] == "CAD2GIS_DERIVED_FIXTURE"
        assert fixture["provenance"]["project_id"] == project["id"]
        assert fixture["provenance"]["source_sha256"] == project["source_sha256"]
        assert fixture["run"]["source"]["path"].startswith("demo://")
        assert fixture["run"]["demo"]["project_id"] == project["id"]
        assert len(fixture["layers"]) == 8
        assert sum(len(layer["features"]) for layer in fixture["layers"].values()) == (
            project["delivery_feature_count"]
        )
        serialized = json.dumps(fixture, ensure_ascii=False)
        assert "E:\\" not in serialized
        assert "/home/" not in serialized
        assert "delivery.gpkg" not in serialized


def test_map_previews_stay_near_their_declared_real_locations() -> None:
    catalog = json.loads((WEBDEMO / "demo-catalog.json").read_text(encoding="utf-8"))
    for project in catalog["projects"]:
        fixture_name = project["fixture"]
        fixture = json.loads((WEBDEMO / fixture_name).read_text(encoding="utf-8"))
        demo = fixture["run"]["demo"]
        transform = demo["nominal_transform"]
        assert transform["a"] == 1.0
        assert transform["b"] == 0.0
        if project["map_precision"] == "coarse_bbox_centre":
            assert demo["map_anchor"]["precision"] == "coarse_bbox_centre"
            assert demo["map_anchor"]["geometry_positioning"] in {
                "delivery_already_in_epsg3857",
                "coarse_anchor_translation",
            }
        else:
            assert demo["map_anchor"] is None

        coordinates: list[tuple[float, float]] = []

        def collect(value: object) -> None:
            if not isinstance(value, list):
                return
            if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
                coordinates.append((float(value[0]), float(value[1])))
                return
            for item in value:
                collect(item)

        for collection in fixture["layers"].values():
            for feature in collection["features"]:
                collect(feature["geometry"]["coordinates"])
        source_x = (min(x for x, _ in coordinates) + max(x for x, _ in coordinates)) / 2
        source_y = (min(y for _, y in coordinates) + max(y for _, y in coordinates)) / 2
        preview_x = transform["a"] * source_x - transform["b"] * source_y + transform["tx"]
        preview_y = transform["b"] * source_x + transform["a"] * source_y + transform["ty"]
        anchor_lon, anchor_lat = demo["map_center_lonlat"]
        anchor_x = math.radians(anchor_lon) * 6_378_137
        anchor_y = 6_378_137 * math.log(math.tan(math.pi / 4 + math.radians(anchor_lat) / 2))
        assert math.hypot(preview_x - anchor_x, preview_y - anchor_y) < 10_000, project["id"]

    app = (WEBDEMO / "app.js").read_text(encoding="utf-8")
    assert "nominalTransform.a * input[i]" in app
    assert "nominalTransform.tx" in app
    assert "clearProjectState" in app
    assert "window.CAD2GIS_DEMO.selectProject" in app
    assert "ZoomToExtent" not in app
    assert "ol.proj.fromLonLat([0, 0]), zoom: 2" in app
    assert "12541000" not in app


def test_demo_selector_and_typography_assets_are_self_hosted() -> None:
    page = (WEBDEMO / "index.html").read_text(encoding="utf-8")
    fixture_script = (WEBDEMO / "demo-fixture.js").read_text(encoding="utf-8")
    styles = (WEBDEMO / "styles.css").read_text(encoding="utf-8")

    assert 'id="demo-project-select"' in page
    assert 'id="map-reference-note"' in page
    assert "app.js?v=live-geodata-declutter-20260901" in page
    assert "demo-fixture.js?v=runtime-fix-20260901" in page
    assert "demo-catalog.json" in fixture_script
    assert "?v=runtime-fix-20260901" in fixture_script
    assert "history.replaceState" in fixture_script
    assert "reviewByProject" in fixture_script

    font_assets = (
        "smiley-sans-subset.woff2",
        "noto-sans-sc-subset.woff2",
        "space-grotesk-subset.woff2",
        "ibm-plex-mono-regular-subset.woff2",
        "ibm-plex-mono-semibold-subset.woff2",
    )
    for name in font_assets:
        path = WEBDEMO / "assets" / name
        assert path.is_file()
        assert 1_000 < path.stat().st_size < 300_000
        assert name in styles

    assert '--font-display: "Smiley Sans"' in styles
    assert '--font-body: "Noto Sans SC"' in styles
    assert '--font-ui: "Space Grotesk"' in styles
    assert '--font-code: "IBM Plex Mono"' in styles
    license_dir = WEBDEMO / "assets" / "font-licenses"
    assert {path.name for path in license_dir.glob("*.txt")} == {
        "OFL-IBMPlexMono.txt",
        "OFL-NotoSansSC.txt",
        "OFL-SmileySans.txt",
        "OFL-SpaceGrotesk.txt",
    }
