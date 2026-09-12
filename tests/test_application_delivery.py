"""Current-result download catalog and filesystem admission boundaries."""

import json
import hashlib
import sqlite3
import zipfile
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from cad2gis.application import ApplicationConfig, create_application


@pytest.fixture
def delivery_shell(tmp_path):
    root = tmp_path / "projects"
    run = root / "结果 with spaces"
    run.mkdir(parents=True)
    files = {
        "run_manifest.json": json.dumps({"run_status": "CONDITIONAL", "source": {"path": "/old/drawing.dwg"}}),
        "delivery.gpkg": "verified-test-bytes", "delivery.qgz": "qgis-test-bytes",
        "qgis/styles/CABLE.qml": "<qgis/>", "svg-candidates/index.html": "<h1>Symbols</h1>",
        "svg-candidates/symbols/中文 图例.svg": "<svg/>", "svg-candidates/symbols/中文 图例.png": "png-bytes",
        "reasoning/visual/overview.png": "overview-bytes", "reports/audit.html": "<h1>Audit</h1>",
        "secret.txt": "do-not-serve", "source.dwg": "do-not-serve", "arbitrary.json": "do-not-serve",
        "svg-candidates/symbols/deep/unrelated.svg": "do-not-serve",
    }
    for name, contents in files.items():
        path = run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    config = ApplicationConfig((root,), launch_token="delivery-token")
    with TestClient(create_application(config), base_url="http://localhost",
                    headers={"origin": "http://localhost"}) as client:
        yield client, root, run


def _login(client):
    assert client.post("/_app/api/login", json={"token": "delivery-token"}).status_code == 200


def test_delivery_catalog_requires_login_and_returns_existing_files_without_changing_status(delivery_shell):
    client, _, run = delivery_shell
    query = {"run": str(run)}
    assert client.get("/_app/api/delivery/artifacts", params=query).status_code == 401
    assert client.get("/_app/api/delivery/artifact", params={**query, "name": "delivery.gpkg"}).status_code == 401
    _login(client)
    before = (run / "run_manifest.json").read_bytes()
    response = client.get("/_app/api/delivery/artifacts", params=query)
    assert response.status_code == 200, response.text
    catalog = response.json()
    assert catalog["run_dir"] == str(run)
    assert catalog["run_status"] == "CONDITIONAL"
    assert {item["name"] for item in catalog["artifacts"]} == {
        "run_manifest.json", "delivery.gpkg", "delivery.qgz", "qgis/styles/CABLE.qml",
        "svg-candidates/index.html", "svg-candidates/symbols/中文 图例.svg",
        "svg-candidates/symbols/中文 图例.png", "reasoning/visual/overview.png", "reports/audit.html",
    }
    for item in catalog["artifacts"]:
        assert set(item) == {"name", "label", "kind", "size", "url"}
        download = client.get(item["url"])
        expected = (run / item["name"]).read_bytes()
        assert download.status_code == 200
        assert download.content == expected
        assert item["size"] == len(expected)
        assert download.headers["content-disposition"].startswith("attachment;")
    assert (run / "run_manifest.json").read_bytes() == before


@pytest.mark.parametrize("name", ["../secret.txt", "/etc/passwd", "delivery.gpkg/../secret.txt",
                                  "secret.txt", "source.dwg", "arbitrary.json", "qgis\\styles\\CABLE.qml",
                                  "svg-candidates/symbols/deep/unrelated.svg", "delivery.gpkg:secret"])
def test_delivery_download_rejects_paths_outside_allowlist(delivery_shell, name):
    client, _, run = delivery_shell
    _login(client)
    response = client.get("/_app/api/delivery/artifact", params={"run": str(run), "name": name})
    assert response.status_code == 400
    assert "do-not-serve" not in response.text


def test_delivery_catalog_rejects_outside_roots_and_non_run_directories(delivery_shell, tmp_path):
    client, root, run = delivery_shell
    _login(client)
    for folder in (tmp_path, root, root / ".cad2gis-app"):
        assert client.get("/_app/api/delivery/artifacts", params={"run": str(folder)}).status_code == 400
    (run / "run_manifest.json").write_text("[]")
    assert client.get("/_app/api/delivery/artifacts", params={"run": str(run)}).status_code == 400


def test_delivery_catalog_does_not_follow_file_directory_or_run_symlinks(delivery_shell, tmp_path):
    client, root, run = delivery_shell
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "private.svg").write_text("outside-private-data")
    try:
        (run / "linked.svg").symlink_to(outside / "private.svg")
        (root / "linked-run").symlink_to(run, target_is_directory=True)
        (run / "visual").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    _login(client)
    catalog = client.get("/_app/api/delivery/artifacts", params={"run": str(run)}).json()
    assert "linked.svg" not in {item["name"] for item in catalog["artifacts"]}
    assert "visual/private.svg" not in {item["name"] for item in catalog["artifacts"]}
    for name in ("linked.svg", "visual/private.svg"):
        assert client.get("/_app/api/delivery/artifact", params={"run": str(run), "name": name}).status_code == 400
    assert client.get("/_app/api/delivery/artifacts", params={"run": str(root / "linked-run")}).status_code == 400
    # Downloads recheck a formerly valid catalog item after it is replaced.
    (run / "delivery.gpkg").unlink()
    (run / "delivery.gpkg").symlink_to(outside / "private.svg")
    assert client.get("/_app/api/delivery/artifact", params={"run": str(run), "name": "delivery.gpkg"}).status_code == 400


def test_partition_delivery_reads_status_without_promoting_it(delivery_shell):
    client, _, run = delivery_shell
    _login(client)
    (run / "run_manifest.json").unlink()
    (run / "partition_manifest.json").write_text(json.dumps({"run_status": "CONDITIONAL"}))
    response = client.get("/_app/api/delivery/artifacts", params={"run": str(run)})
    assert response.status_code == 200
    assert response.json()["run_status"] == "CONDITIONAL"
    assert "partition_manifest.json" in {item["name"] for item in response.json()["artifacts"]}


def _portable_package(root):
    from cad2gis.delivery import package_delivery

    source = root / "archive-source"
    source.mkdir()
    with closing(sqlite3.connect(source / "delivery.gpkg")) as db, db:
        db.executescript("""CREATE TABLE gpkg_geometry_columns(table_name,column_name,geometry_type_name,srs_id);
          INSERT INTO gpkg_geometry_columns VALUES('CABLE','geom','LINESTRING',3857);
          CREATE TABLE gpkg_contents(table_name,min_x,min_y,max_x,max_y);
          INSERT INTO gpkg_contents VALUES('CABLE',0,0,10,20);
          CREATE TABLE gpkg_spatial_ref_sys(srs_id,definition);
          INSERT INTO gpkg_spatial_ref_sys VALUES(3857,'');
          CREATE TABLE CABLE(fid INTEGER,geom BLOB,display_label TEXT);
          INSERT INTO CABLE VALUES(1,NULL,'12.5m');""")
    (source / "run_manifest.json").write_text(json.dumps({
        "run_status": "CONDITIONAL", "source": {"sha256": "a" * 64}, "delivery_counts": {"CABLE": 1},
        "artifacts": {"delivery": {"sha256": hashlib.sha256((source / "delivery.gpkg").read_bytes()).hexdigest()}},
    }))
    output = root / "portable"
    package_delivery(source, output)
    return output


def test_portable_delivery_lists_complete_zip_and_checks_its_manifest(delivery_shell):
    client, root, _ = delivery_shell
    _login(client)
    run = _portable_package(root)
    manifest = run / 'delivery-manifest.json'
    archive = run.with_suffix('.zip')
    response = client.get('/_app/api/delivery/artifacts', params={'run': str(run)})
    assert response.status_code == 200
    data = response.json()
    assert data['run_status'] == 'CONDITIONAL'
    assert data['archive']['size'] == archive.stat().st_size
    download = client.get(data['archive']['url'])
    assert download.status_code == 200
    assert download.content == archive.read_bytes()
    assert download.headers['content-type'] == 'application/zip'
    assert download.headers['content-disposition'].startswith('attachment;')
    manifest.write_text('{}')
    assert client.get(data['archive']['url']).status_code == 400


@pytest.mark.parametrize("tamper", ["missing", "changed", "extra", "duplicate", "unsafe", "directory-changed",
                                  "directory-extra", "checksum-missing", "secret-in-seal", "database-resealed", "database-missing-resealed"])
def test_portable_archive_rejects_incomplete_or_changed_bytes(delivery_shell, tamper):
    from cad2gis.delivery import seal_delivery

    client, root, _ = delivery_shell
    run = _portable_package(root)
    archive = run.with_suffix(".zip")
    _login(client)
    url = "/_app/api/delivery/archive"
    assert client.get(url, params={"run": str(run)}).status_code == 200
    if tamper == "database-missing-resealed":
        (run / "delivery.gpkg").unlink()
        seal_delivery(run, replace=True)
    elif tamper in {"directory-changed", "database-resealed"}:
        (run / "delivery.gpkg").write_bytes(b"changed database")
        if tamper == "database-resealed":
            seal_delivery(run, replace=True)
    elif tamper in {"directory-extra", "secret-in-seal"}:
        (run / "secret.txt").write_bytes(b"do-not-serve")
        if tamper == "secret-in-seal":
            seal_delivery(run, replace=True)
    elif tamper == "checksum-missing":
        (run / "checksums.json").unlink()
    else:
        with zipfile.ZipFile(archive) as bundle:
            entries = [(name, bundle.read(name)) for name in bundle.namelist()]
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in entries:
                if tamper == "missing" and name == "delivery.gpkg":
                    continue
                bundle.writestr(name, b"changed" if tamper == "changed" and name == "delivery.gpkg" else data)
            if tamper in {"extra", "unsafe"}:
                bundle.writestr("secret.txt" if tamper == "extra" else "../secret.txt", b"do-not-serve")
            elif tamper == "duplicate":
                with pytest.warns(UserWarning, match="Duplicate"):
                    bundle.writestr("delivery.gpkg", b"duplicate")
    response = client.get(url, params={"run": str(run)})
    assert response.status_code == 400, response.text
    assert "do-not-serve" not in response.text


def test_delivery_archive_requires_auth_and_refuses_symlinks(delivery_shell, tmp_path):
    client, _, run = delivery_shell
    query = {'run': str(run)}
    assert client.get('/_app/api/delivery/archive', params=query).status_code == 401
    _login(client)
    assert client.get('/_app/api/delivery/archive', params=query).status_code == 400
    (run / 'delivery-manifest.json').write_text('{}')
    archive = run.with_suffix('.zip')
    try:
        archive.symlink_to(tmp_path / 'private.zip')
    except OSError:
        pytest.skip('Symlinks unavailable')
    assert client.get('/_app/api/delivery/archive', params=query).status_code == 400
    assert client.get('/_app/api/delivery/archive', params={'run': str(tmp_path)}).status_code == 400


def make_standard_scope(directory, *, status="matched", source_sha="a" * 64, manifest_sha="b" * 64):
    """Actual ZIP/XML/database bytes with canonical per-project hash receipts."""
    directory.mkdir(parents=True)
    cad, osm, embedded_osm = b"CAD database bytes", b"external OSM database", b"rebuilt embedded OSM database"
    def digest(data):
        return hashlib.sha256(data).hexdigest()
    (directory / "delivery.gpkg").write_bytes(cad)
    (directory / "osm-basemap.gpkg").write_bytes(osm)
    matching = {"schema_version": "cad2gis.osm-matching.v1", "source_sha256": source_sha,
                "revision": 1, "status": status, "authority": "relative_osm", "absolute_accuracy_verified": False}
    matching["match_sha256"] = digest(json.dumps(matching, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    context = {"schema": "cad2gis.qgis-matching-context.v1", "source_sha256": source_sha,
               "run_manifest_sha256": manifest_sha, "delivery_sha256": digest(cad), "matching": matching}
    for name, standalone in (("delivery.qgz", False), ("view-with-source-SVG.qgz", True)):
        sources = ["attachment:cad_delivery.gpkg", "attachment:osm_basemap.gpkg"] if standalone else ["./delivery.gpkg", "./osm-basemap.gpkg"]
        xml = '<qgis><projectlayers>' + ''.join(
            f'<maplayer><provider>ogr</provider><datasource>{source}|layername={layer}</datasource></maplayer>'
            for source, layer in zip(sources, ("CABLE", "roads"))) + '</projectlayers></qgis>'
        with zipfile.ZipFile(directory / name, "w") as bundle:
            bundle.writestr("delivery.qgs", xml)
            if standalone:
                bundle.writestr("cad_delivery.gpkg", cad)
                bundle.writestr("osm_basemap.gpkg", embedded_osm)
        receipt = {"schema": "cad2gis.qgis-osm-review.v1", "sha256": digest((directory / name).read_bytes()),
                   "standalone": standalone, "cad_database_hashes": [digest(cad)],
                   "osm_database_sha256": digest(embedded_osm if standalone else osm),
                   "osm_source_sha256": "c" * 64, "canonical_matching": context,
                   "geometry_and_attributes_unchanged": True, "absolute_accuracy_verified": False}
        (directory / name).with_suffix(".review.json").write_text(json.dumps(receipt))
    return {"scope": "main", "directory": ".", "gpkg_sha256": digest(cad), "matching": matching}


@pytest.mark.parametrize("status", ["matched", "abstained", "unavailable"])
def test_standard_six_file_http_catalog_checks_bindings_and_preserves_status(delivery_shell, status):
    client, root, _ = delivery_shell
    scope = root / "standard"
    make_standard_scope(scope, status=status)
    before = {p.name: p.read_bytes() for p in scope.iterdir()}
    _login(client)
    response = client.get("/_app/api/delivery/artifacts", params={"run": str(scope)})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["run_status"] == "UNKNOWN"
    assert data["matching"]["status"] == status
    assert data["delivery_kind"] == ("standard_matched" if status == "matched" else "standard_reference")
    assert data["absolute_accuracy_verified"] is False
    assert len(data["artifacts"]) == 6 and "archive" not in data
    assert {item["name"] for item in data["artifacts"]} == set(before)
    for item in data["artifacts"]:
        response = client.get(item["url"])
        assert response.status_code == 200
        assert response.content == before[item["name"]]
    assert {p.name: p.read_bytes() for p in scope.iterdir()} == before


@pytest.mark.parametrize("tamper", ["delivery.qgz", "view-with-source-SVG.qgz", "delivery.gpkg", "osm-basemap.gpkg",
                                  "missing-receipt", "receipt-only", "cad-attachment", "osm-attachment", "snapshot", "matching"])
def test_standard_scope_rejects_tamper_before_catalog_and_download(delivery_shell, tamper):
    client, root, _ = delivery_shell
    scope = root / "standard"
    make_standard_scope(scope)
    _login(client)
    query = {"run": str(scope)}
    assert client.get("/_app/api/delivery/artifacts", params=query).status_code == 200
    receipt_path = scope / "view-with-source-SVG.review.json"
    receipt = json.loads(receipt_path.read_text())
    if tamper in {"delivery.qgz", "view-with-source-SVG.qgz", "delivery.gpkg", "osm-basemap.gpkg"}:
        (scope / tamper).write_bytes(b"changed")
    elif tamper == "missing-receipt":
        receipt_path.unlink()
    elif tamper == "receipt-only":
        receipt["sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt))
    elif tamper in {"snapshot", "matching"}:
        if tamper == "snapshot":
            receipt["osm_source_sha256"] = "f" * 64
        else:
            receipt["canonical_matching"]["matching"]["status"] = "matched-tampered"
        receipt_path.write_text(json.dumps(receipt))
    else:
        project = scope / "view-with-source-SVG.qgz"
        with zipfile.ZipFile(project) as bundle:
            entries = [(name, bundle.read(name)) for name in bundle.namelist()]
        target = "cad_delivery.gpkg" if tamper == "cad-attachment" else "osm_basemap.gpkg"
        with zipfile.ZipFile(project, "w") as bundle:
            for name, data in entries:
                bundle.writestr(name, b"changed attachment" if name == target else data)
        receipt["sha256"] = hashlib.sha256(project.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt))
    for endpoint, params in (("artifacts", query), ("artifact", {**query, "name": "delivery.gpkg"})):
        response = client.get("/_app/api/delivery/" + endpoint, params=params)
        assert response.status_code == 400, response.text
