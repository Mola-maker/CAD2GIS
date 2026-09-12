"""Current-result download catalog and filesystem admission boundaries."""

import json

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
