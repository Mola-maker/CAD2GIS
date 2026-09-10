"""A multi-panel drawing must expose every declared district to the operator."""
import hashlib
import json
from pathlib import Path

import pytest

from cad2gis.delivery_scopes import delivery_scopes
from cad2gis.cad2gis_v3.pipeline import _load_delivery_partitions


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def three_districts(tmp_path):
    run = tmp_path / "run"
    entries = {}
    for region, count in (("NORTH", 3), ("SOUTH", 7)):
        child = run / region
        child.mkdir(parents=True)
        data = (region + " immutable fixture").encode()
        (child / "delivery.gpkg").write_bytes(data)
        counts = {"CABLE": count}
        entries[region] = {"path": str(child / "delivery.gpkg"), "delivery_counts": counts,
                           "sha256": hashlib.sha256(data).hexdigest()}
        write(child / "partition_manifest.json", {"region_id": region, "label": region.title(),
                                                   "delivery_counts": counts})
    write(run / "run_manifest.json", {"source": {"sha256": "a" * 64}, "run_status": "CONDITIONAL",
                                        "delivery_counts": {"CABLE": 11}, "delivery_partitions": entries})
    return run


def test_parent_and_child_offer_all_three_districts(three_districts):
    for relative, selected in (("", "MAIN"), ("NORTH", "NORTH"), ("SOUTH", "SOUTH")):
        result = delivery_scopes(three_districts / relative)
        assert result["selected_scope"] == selected
        assert [x["region_id"] for x in result["scopes"]] == ["MAIN", "NORTH", "SOUTH"]
        assert [x["delivery_counts"]["CABLE"] for x in result["scopes"]] == [11, 3, 7]
        assert all(x["available"] for x in result["scopes"])


@pytest.mark.parametrize("failure", ["missing", "tampered", "counts", "identity"])
def test_broken_district_is_visible_but_unavailable(three_districts, failure):
    child = three_districts / "SOUTH"
    if failure == "missing":
        (child / "delivery.gpkg").unlink()
    elif failure == "tampered":
        (child / "delivery.gpkg").write_bytes(b"other district")
    else:
        path = child / "partition_manifest.json"
        value = json.loads(path.read_text())
        value["delivery_counts" if failure == "counts" else "region_id"] = {"CABLE": 70} if failure == "counts" else "OTHER"
        write(path, value)
    result = delivery_scopes(three_districts)
    assert len(result["scopes"]) == 3
    assert result["scopes"][1]["available"]
    assert not result["scopes"][2]["available"]
    assert result["scopes"][2]["error"]


def test_unlisted_folders_are_not_promoted_and_copied_paths_work(three_districts):
    (three_districts / "NOT_A_DISTRICT").mkdir()
    path = three_districts / "run_manifest.json"
    manifest = json.loads(path.read_text())
    for region, entry in manifest["delivery_partitions"].items():
        entry["path"] = f"Z:\\old-machine\\run\\{region}\\delivery.gpkg"
    write(path, manifest)
    assert len(delivery_scopes(three_districts)["scopes"]) == 3
    assert all(x["available"] for x in delivery_scopes(three_districts)["scopes"])


@pytest.mark.parametrize("region", ["../OTHER", "MAIN", "NORTH/child", "north"])
def test_unsafe_or_colliding_district_ids_are_rejected(three_districts, region):
    path = three_districts / "run_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["delivery_partitions"][region] = manifest["delivery_partitions"]["SOUTH"]
    write(path, manifest)
    with pytest.raises(ValueError, match="identifier"):
        delivery_scopes(three_districts)


def test_application_switch_returns_catalog_and_retires_previous_page(three_districts):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cad2gis.application import ApplicationConfig, create_application

    def factory(run: Path, *, workspace_dir: Path):
        app = FastAPI()

        @app.get("/api/selected-district")
        def selected():
            return {"directory": run.name}

        return app

    config = ApplicationConfig((three_districts.parent,), launch_token="test-token")
    with TestClient(create_application(config, review_factory=factory), base_url="http://localhost",
                    headers={"Origin": "http://localhost"}) as client:
        assert client.post("/_app/api/login", json={"token": "test-token"}).status_code == 200
        parent = client.post("/_app/api/review", json={"run": str(three_districts)}).json()
        assert len(parent["districts"]["scopes"]) == 3
        previous = parent["review_id"]
        for region in ("NORTH", "SOUTH", "MAIN"):
            choice = next(x for x in parent["districts"]["scopes"] if x["region_id"] == region)
            response = client.post("/_app/api/review", json={"run": choice["run_dir"]})
            assert response.status_code == 200, response.text
            opened = response.json()
            assert opened["districts"]["selected_scope"] == region
            assert len(opened["districts"]["scopes"]) == 3
            assert client.get("/api/selected-district", headers={"X-CAD2GIS-Review": previous}).status_code == 409
            current = client.get("/api/selected-district", headers={"X-CAD2GIS-Review": opened["review_id"]})
            assert current.json()["directory"] == ("run" if region == "MAIN" else region)
            previous = opened["review_id"]


@pytest.mark.parametrize("contents", [
    "{broken", "[]", '{}', '{"partitions":{}}',
    '{"partitions":[{"region_id":"A","bbox":[0,0,1]}]}',
    '{"partitions":[{"region_id":"A","bbox":[0,0,1,NaN]}]}',
    '{"partitions":[{"region_id":"A","bbox":[0,0,-1,1]}]}',
    '{"partitions":[{"region_id":"../A","bbox":[0,0,1,1]}]}',
    '{"partitions":[{"region_id":"MAIN","bbox":[0,0,1,1]}]}',
    '{"partitions":[{"region_id":"A","bbox":[0,0,1,1]},{"region_id":"a","bbox":[2,2,3,3]}]}',
    '{"partitions":[{"region_id":"A","bbox":[0,0,2,2]},{"region_id":"B","bbox":[1,1,3,3]}]}',
])
def test_invalid_partition_file_cannot_silently_collapse_to_main(tmp_path, contents):
    (tmp_path / "delivery_partitions.json").write_text(contents)
    with pytest.raises(ValueError):
        _load_delivery_partitions(tmp_path)


def test_valid_two_panels_remain_independent(tmp_path):
    write(tmp_path / "delivery_partitions.json", {"partitions": [
        {"region_id": "A", "bbox": [0, 0, 100, 100]},
        {"region_id": "B", "bbox": [200, 0, 300, 100]},
    ]})
    assert [p["region_id"] for p in _load_delivery_partitions(tmp_path)] == ["A", "B"]


def test_packaging_cannot_drop_missing_declared_district(three_districts, tmp_path):
    from cad2gis.delivery import package_delivery

    (three_districts / "SOUTH" / "delivery.gpkg").unlink()
    with pytest.raises(ValueError, match="Cannot package district SOUTH"):
        package_delivery(three_districts, tmp_path / "package")
    assert not (tmp_path / "package").exists()


def test_combined_qgis_keeps_three_databases_groups_and_extents(tmp_path):
    import sqlite3
    import xml.etree.ElementTree as ET
    import zipfile
    from cad2gis.delivery import package_delivery

    run = tmp_path / "run"
    manifest = {"source": {"sha256": "b" * 64}, "delivery_counts": {"CABLE": 1}, "delivery_partitions": {}}
    hashes = {}
    for index, region in enumerate(("MAIN", "NORTH", "SOUTH")):
        folder = run if region == "MAIN" else run / region
        folder.mkdir(parents=True)
        with sqlite3.connect(folder / "delivery.gpkg") as db:
            db.executescript('''CREATE TABLE gpkg_geometry_columns(table_name,column_name,geometry_type_name,srs_id);
              INSERT INTO gpkg_geometry_columns VALUES('CABLE','geom','LINESTRING',3857);
              CREATE TABLE gpkg_contents(table_name,min_x,min_y,max_x,max_y);
              CREATE TABLE gpkg_spatial_ref_sys(srs_id,definition);
              INSERT INTO gpkg_spatial_ref_sys VALUES(3857,'');
              CREATE TABLE CABLE(fid INTEGER,geom BLOB,display_label TEXT);
              INSERT INTO CABLE VALUES(1,NULL,'source span');''')
            db.execute("INSERT INTO gpkg_contents VALUES('CABLE',?,?,?,?)", (index * 100, 0, index * 100 + 10, 10))
        digest = hashlib.sha256((folder / "delivery.gpkg").read_bytes()).hexdigest()
        hashes[region] = digest
        if region == "MAIN":
            manifest["artifacts"] = {"delivery": {"sha256": digest}}
        else:
            manifest["delivery_partitions"][region] = {"path": str(folder / "delivery.gpkg"), "sha256": digest, "delivery_counts": {"CABLE": 1}}
            write(folder / "partition_manifest.json", {"region_id": region, "delivery_counts": {"CABLE": 1}})
    write(run / "run_manifest.json", manifest)
    output = tmp_path / "package"
    report = package_delivery(run, output)
    assert report["overview_project"] == "all-districts.qgz"
    with zipfile.ZipFile(output / "all-districts.qgz") as archive:
        root = ET.fromstring(archive.read("all-districts.qgs"))
    assert [g.get("name") for g in root.findall("layer-tree-group/layer-tree-group")] == ["main", "NORTH", "SOUTH"]
    layers = root.findall("projectlayers/maplayer")
    assert len({layer.findtext("id") for layer in layers}) == 3
    assert {layer.findtext("datasource") for layer in layers} == {"./delivery.gpkg|layername=CABLE", "./NORTH/delivery.gpkg|layername=CABLE", "./SOUTH/delivery.gpkg|layername=CABLE"}
    assert float(root.findtext("mapcanvas/extent/xmax")) == 210
    assert float(root.findtext("ProjectViewSettings/DefaultViewExtent/xmax")) == 210
    for region, digest in hashes.items():
        path = output / "delivery.gpkg" if region == "MAIN" else output / region / "delivery.gpkg"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    readme = (output / "README.md").read_text()
    assert all(path in readme for path in ("all-districts.qgz", "NORTH/delivery.qgz", "SOUTH/delivery.qgz"))
