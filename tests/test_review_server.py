from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from cad2gis.agent_mcp import audit_run, prepare_review_workspace
from cad2gis import review_server
from cad2gis.review_server import (
    ReviewConflictError,
    ReviewServerError,
    SQLiteReviewStore,
    create_review_app,
)


def _feature(feature_id: str = "review:1") -> dict:
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": "LineString",
            "coordinates": [[123.0, 0.5], [123.001, 0.501]],
        },
        "properties": {
            "review_status": "needs_correction",
            "review_note": "Observed offset",
        },
    }


def _run_fixture(tmp_path) -> tuple:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "fixture.dwg"
    source.write_bytes(b"immutable-source-fixture")
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"immutable-delivery-fixture")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "modes": {"domain": "auto", "llm": "assist"},
        "source_entity_count": 1538,
        "delivery_counts": {"SITE": 1, "CABLE": 33},
        "source": {
            "path": "fixture.dwg",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "artifacts": {
            "delivery": {"path": str(delivery), "sha256": "fixture"},
        },
        "crs": {
            "source_crs": "EPSG:32749",
            "target_crs": "EPSG:32749",
        },
        "validation": {},
        "reasoning": {},
    }
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir, delivery, manifest_path


def test_canonical_registration_preview_uses_target_crs_and_excludes_checks(tmp_path, monkeypatch):
    from cad2gis.native_runtime import ensure_osgeo_runtime
    ensure_osgeo_runtime()
    from cad2gis.cad2gis_v3.georef import DirectTransformer
    from pyproj import Transformer

    run, _, _ = _run_fixture(tmp_path)
    nominal = DirectTransformer("EPSG:32749", "EPSG:32749")
    monkeypatch.setattr(review_server, "_registration_transformer", lambda _: nominal)
    monkeypatch.setattr(review_server.GeoPackageProvider, "geojson", lambda *a, **k: {
        "type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
        "geometry": {"type": "Point", "coordinates": [500000., 9200000.]}}]})
    client = TestClient(create_review_app(run, workspace_dir=tmp_path / "web"))
    to_lonlat = Transformer.from_crs(32749, 4326, always_xy=True)
    for i, (x, y, role, dx) in enumerate([(500000, 9200000, "train", 10),
            (500100, 9200010, "train", 10), (500020, 9200100, "train", 10),
            (500050, 9200050, "check", 110)]):
        lon, lat = to_lonlat.transform(x+dx, y-5)
        response = client.post("/api/review/features", json={"expected_revision": 0, "actor": "test",
            "feature": {"type": "Feature", "id": f"gcp:{i}",
            "geometry": {"type": "Point", "coordinates": [lon,lat]},
            "properties": {"_kind": "cad_map_gcp", "cad_x": x, "cad_y": y, "role": role}}})
        assert response.status_code == 200, response.text
    capture = client.get("/api/registration").json()
    fit = capture["preview_fit"]
    assert fit["selected_model"] == "translation"
    assert fit["target_crs"] == "EPSG:32749"
    assert fit["train_metrics"]["rmse_m"] < 1e-6
    assert fit["check_metrics"]["rmse_m"] == pytest.approx(100, abs=1e-6)
    assert fit["validation"]["passed"] is None  # preview is not engineering acceptance
    endpoint = "/api/registration/preview/CABLE"
    response = client.get(endpoint, params={"expected_controls_sha256": capture["controls_sha256"]})
    assert response.status_code == 200, response.text
    point = response.json()["features"][0]["geometry"]["coordinates"]
    assert point == pytest.approx(to_lonlat.transform(500010, 9199995), abs=1e-8)
    assert client.get(endpoint, params={"expected_controls_sha256": "stale"}).status_code == 409


def test_registration_preview_rejects_modified_profile(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    with pytest.raises(ReviewServerError, match="hash mismatch"):
        review_server._registration_transformer({"profiles": {"source_profile": {"path": str(profile), "sha256": "0"*64}}})


def test_sqlite_review_store_is_revisioned_and_conflict_safe(tmp_path) -> None:
    store = SQLiteReviewStore(
        tmp_path / "review.sqlite3", session_id="run:source",
    )
    created = store.upsert(
        _feature(), expected_revision=0, actor="tester",
    )
    assert created["revision"] == 1
    collection = store.feature_collection()
    assert collection["features"][0]["properties"]["_review_revision"] == 1

    with pytest.raises(ReviewConflictError, match="revision is 1"):
        store.upsert(_feature(), expected_revision=0, actor="stale-client")

    deleted = store.delete(
        "review:1", expected_revision=1, actor="tester",
    )
    assert deleted["revision"] == 2
    assert store.feature_collection()["features"] == []
    events = store.events()
    assert [event["operation"] for event in events] == [
        "upsert", "delete",
    ]
    assert [event["operation"] for event in store.events(after=events[0]["event_id"])] == [
        "delete",
    ]


def test_review_store_rejects_non_wgs84_or_nonfinite_geometry(tmp_path) -> None:
    store = SQLiteReviewStore(
        tmp_path / "review.sqlite3", session_id="run:source",
    )
    invalid = _feature()
    invalid["geometry"]["coordinates"][0][0] = 500.0
    with pytest.raises(ReviewServerError, match="EPSG:4326"):
        store.upsert(invalid, expected_revision=0, actor="tester")


def test_review_relocates_copied_run_artifact_only_by_matching_hash(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir = tmp_path / "copied-run"
    run_dir.mkdir()
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"portable-delivery")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "source": {"path": "/old-host/drawing.dwg", "sha256": "a" * 64},
        "artifacts": {
            "delivery": {
                "path": "/old-host/run/delivery.gpkg",
                "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(),
            },
        },
        "crs": {"source_crs": "EPSG:32749", "target_crs": "EPSG:32749"},
        "validation": {},
        "reasoning": {},
    }
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    client = TestClient(create_review_app(run_dir, workspace_dir=tmp_path / "review"))
    assert client.get("/api/health").json()["status"] == "ok"
    run = client.get("/api/run").json()
    assert run["source_available"] is False
    assert "SHA-256" in run["source_blocker"]

    manifest["artifacts"]["delivery"]["sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReviewServerError, match="SHA-256 mismatch"):
        create_review_app(run_dir, workspace_dir=tmp_path / "review-2")


def test_mcp_run_audit_verifies_hashes_counts_and_source_replay(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "drawing.dwg"
    source.write_bytes(b"source")
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"delivery")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "source": {
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "artifacts": {
            "delivery": {
                "path": str(delivery),
                "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(),
            },
        },
        "delivery_counts": {"CABLE": 2},
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )

    class FakeProvider:
        def __init__(self, _path):
            pass

        def layers(self):
            return [{"name": "CABLE", "feature_count": 2}]

    monkeypatch.setattr(review_server, "GeoPackageProvider", FakeProvider)
    passed = audit_run(str(run_dir))
    assert passed["audit_status"] == "PASS"
    assert passed["source_replay"]["available"] is True

    delivery.write_bytes(b"tampered")
    failed = audit_run(str(run_dir))
    assert failed["audit_status"] == "FAIL"
    assert "artifact:delivery" in failed["failures"]


def test_review_app_keeps_run_artifacts_immutable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir, delivery, manifest_path = _run_fixture(tmp_path)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (delivery, manifest_path)
    }
    workspace = tmp_path / "review-workspace"
    client = TestClient(
        create_review_app(run_dir, workspace_dir=workspace),
    )

    assert client.get("/api/health").json()["status"] == "ok"
    run_summary = client.get("/api/run").json()
    assert run_summary["source_entity_count"] == 1538
    assert run_summary["delivery_counts"] == {"SITE": 1, "CABLE": 33}
    page = client.get("/").text
    assert "图纸理解、配准与交付审查" in page
    assert "把不可信的" in page
    assert "DWG" in page
    assert 'id="hero-page"' in page
    assert 'data-count="9717"' in page
    assert "assets/hero-evidence-graph.svg" in page
    assert 'id="process-terminal"' in page
    assert 'data-process-terminal' in page
    assert "run-label-provenance-fix-003" in page
    assert "proper interior crossings 1 · promoted connections 0" in page
    assert page.count('data-terminal-line data-stage=') == 14
    assert "DERIVED REPLAY · NOT LIVE EXECUTION" in page
    assert "9,717 immutable source entities" in page
    assert "281 entities unresolved" in page
    assert "170 / 179 spans" in page
    assert "max |error| &lt; 0.001 m" in page
    assert "1,150 delivery features" in page
    assert 'id="plugin-guide"' in page
    assert 'data-plugin-guide' in page
    assert page.count('data-plugin-step=') == 5
    assert "codex plugin marketplace add Mola-maker/CAD2GIS --ref main" in page
    assert "cad2gis plugin marketplace" not in page
    response = client.post("/api/review/features", json={
        "feature": _feature(),
        "expected_revision": 0,
        "actor": "test-client",
    })
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    features = client.get("/api/review/features").json()
    assert len(features["features"]) == 1
    conflict = client.post("/api/review/features", json={
        "feature": _feature(),
        "expected_revision": 0,
        "actor": "stale-client",
    })
    assert conflict.status_code == 409

    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (delivery, manifest_path)
    }
    assert after == before
    assert (workspace / "review.sqlite3").is_file()


def test_review_app_transfers_coordinates_and_exports_active_profile(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir, _, _ = _run_fixture(tmp_path)
    workspace = tmp_path / "review-workspace"
    client = TestClient(
        create_review_app(run_dir, workspace_dir=workspace),
    )
    controls = [
        ("train", 0.0, 0.0, 112.70, -7.45),
        ("train", 100.0, 0.0, 112.71, -7.45),
        ("train", 0.0, 100.0, 112.70, -7.44),
        ("train", 100.0, 100.0, 112.71, -7.44),
        ("check", 20.0, 20.0, 112.702, -7.448),
        ("check", 80.0, 20.0, 112.708, -7.448),
        ("check", 50.0, 80.0, 112.705, -7.442),
    ]
    for index, (role, x, y, lon, lat) in enumerate(controls):
        response = client.post("/api/review/features", json={
            "expected_revision": 0,
            "actor": "test-client",
            "feature": {
                "type": "Feature",
                "id": f"gcp:{index}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": {
                    "_kind": "cad_map_gcp",
                    "role": role,
                    "cad_x": x,
                    "cad_y": y,
                },
            },
        })
        assert response.status_code == 200

    capture = client.get("/api/registration")
    assert capture.status_code == 200
    assert capture.json()["activation_ready"] is True
    assert capture.json()["controls"][0]["target_crs"] == "EPSG:32749"
    assert capture.json()["controls"][0]["target_easting"] > 100_000

    exported = client.post(
        "/api/registration/export", json={"activate": True},
    )
    assert exported.status_code == 200, exported.text
    result = exported.json()
    assert result["profile"]["enabled"] is True
    assert result["absolute_accuracy_verified"] is False
    assert "--gcp-profile" in result["conversion_command"]
    assert "--llm assist" in result["conversion_command"]
    assert "--domain auto" in result["conversion_command"]
    assert result["source_run_modes"] == {"domain": "auto", "llm": "assist"}
    assert (workspace / "web_gcp_profile.json").is_file()

    (run_dir / "fixture.dwg").unlink()
    archived = client.post(
        "/api/registration/export", json={"activate": True},
    ).json()
    assert archived["source_available"] is False
    assert archived["conversion_command"] is None
    assert "SHA-256" in archived["source_blocker"]


def test_mcp_prepares_separate_review_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "source": {"sha256": "b" * 64},
    }), encoding="utf-8")

    result = prepare_review_workspace(str(run_dir), port=9876)

    assert result["immutable_delivery"] is True
    assert result["url"] == "http://127.0.0.1:9876"
    assert "cad2gis review" in result["launch_command"]
    assert (tmp_path / "run.review" / "review.sqlite3").is_file()


def test_review_console_exposes_toc_copy_and_accessibility_contracts(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir, _, _ = _run_fixture(tmp_path)
    client = TestClient(create_review_app(run_dir, workspace_dir=tmp_path / "review"))

    page = client.get("/")
    assert page.status_code == 200
    assert '<meta name="theme-color" content="#f5f8f7">' in page.text
    assert 'aria-label="转换目录"' in page.text
    assert 'data-tab="console"' in page.text
    assert 'id="copy-command"' in page.text
    assert 'id="copy-coordinate"' in page.text
    assert './assets/demo-fixture.js' in page.text

    script = client.get("/assets/app.js")
    assert script.status_code == 200
    assert "navigator.clipboard" in script.text
    assert "terminalEvent" in script.text
    assert "/geojson`" in script.text
    assert 'dataProjection: "EPSG:4326"' in script.text
    assert 'featureProjection: "EPSG:3857"' in script.text
    assert "CAD geometry; no DIMENSION" not in script.text
    assert 'feature.get("display_label")' in script.text
    assert "run.source_entity_count" in script.text
    assert "run.delivery_counts" in script.text
    assert "declutter: true" in script.text

    demo = client.get("/assets/demo-fixture.js")
    assert demo.status_code == 200
    assert "CAD2GIS_DERIVED_FIXTURE" in demo.text
    assert "selectProject" in demo.text
    assert "window.CAD2GIS_DEMO" in demo.text

    stylesheet = client.get("/assets/styles.css")
    assert stylesheet.status_code == 200
    assert "color-scheme: light" in stylesheet.text
    assert "--accent: #006c67" in stylesheet.text
    assert "color-scheme: dark" not in stylesheet.text
    assert "prefers-reduced-motion" in stylesheet.text

    for packaged_asset in (
        "hero-evidence-graph.svg",
        "noto-sans-sc-subset.woff2",
    ):
        response = client.get(f"/assets/{packaged_asset}")
        assert response.status_code == 200
        assert response.content

    traversal = client.get("/assets/../pyproject.toml")
    assert traversal.status_code == 404


def _partition_fixture(tmp_path):
    run, _, manifest_path = _run_fixture(tmp_path)
    partition = run / "EMR28560"
    partition.mkdir()
    delivery = partition / "delivery.gpkg"
    delivery.write_bytes(b"partition-specific-delivery")
    evidence = run / "evidence.gpkg"
    evidence.write_bytes(b"shared-parent-evidence")
    counts = {"BOITE": 6, "PTECH": 6, "EMR": 1, "INFRASTRUCTURE": 1, "CABLE": 6}
    parent = json.loads(manifest_path.read_text())
    parent["artifacts"]["evidence"] = {
        "path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    parent["delivery_partitions"] = {"EMR28560": {
        "path": str(delivery), "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(),
        "delivery_counts": counts}}
    manifest_path.write_text(json.dumps(parent), encoding="utf-8")
    partition_manifest = partition / "partition_manifest.json"
    partition_manifest.write_text(json.dumps({
        "schema_version": "cad2gis-delivery-partition-v1", "region_id": "EMR28560",
        "delivery_counts": counts}), encoding="utf-8")
    return partition, parent, manifest_path, partition_manifest


def test_partition_api_reports_its_own_counts_and_retains_parent_source_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    partition, parent, manifest_path, partition_manifest = _partition_fixture(tmp_path)
    original = {path: path.read_bytes() for path in (
        manifest_path, partition_manifest, partition / "delivery.gpkg")}
    original_provider = review_server.GeoPackageProvider
    assert original_provider(partition / "delivery.gpkg")._evidence_path() == partition.parent / "evidence.gpkg"
    observed = []

    class Provider:
        def __init__(self, path):
            observed.append(path)

        def layers(self):
            return [{"name": name, "feature_count": count}
                    for name, count in parent["delivery_partitions"]["EMR28560"]["delivery_counts"].items()]

    monkeypatch.setattr(review_server, "GeoPackageProvider", Provider)
    client = TestClient(create_review_app(partition, workspace_dir=tmp_path / "review-partition"))
    summary = client.get("/api/run").json()
    counts = parent["delivery_partitions"]["EMR28560"]["delivery_counts"]
    assert summary["delivery_counts"] == counts
    assert sum(summary["delivery_counts"].values()) == 20
    assert summary["delivery_counts"] != parent["delivery_counts"]
    assert sum(layer["feature_count"] for layer in client.get("/api/layers").json()["layers"]) == 20
    assert summary["run_dir"] == str(partition)
    assert summary["source"] == parent["source"]
    assert summary["source_available"] is True
    assert summary["source_resolved_path"] == str(partition.parent / "fixture.dwg")
    assert summary["parent_run_provenance"] == {
        "run_dir": str(partition.parent), "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "run_status": "CONDITIONAL", "source_entity_count_scope": "parent_run",
        "validation_scope": "parent_run"}
    assert observed == [partition / "delivery.gpkg"]
    assert all(path.read_bytes() == value for path, value in original.items())


@pytest.mark.parametrize("mutation", ["count", "boolean_count", "missing_entry", "wrong_region", "wrong_path", "wrong_sha", "missing_sha"])
def test_partition_review_rejects_unbound_or_inconsistent_manifest(tmp_path, mutation):
    partition, parent, manifest_path, partition_manifest = _partition_fixture(tmp_path)
    child = json.loads(partition_manifest.read_text())
    entry = parent["delivery_partitions"]["EMR28560"]
    if mutation == "count":
        child["delivery_counts"]["CABLE"] = 99
    elif mutation == "boolean_count":
        child["delivery_counts"]["EMR"] = True
    elif mutation == "missing_entry":
        parent["delivery_partitions"] = {}
    elif mutation == "wrong_region":
        child["region_id"] = "OTHER"
    elif mutation == "wrong_path":
        entry["path"] = str(partition.parent / "OTHER/delivery.gpkg")
    elif mutation == "wrong_sha":
        entry["sha256"] = "a" * 64
    elif mutation == "missing_sha":
        entry["sha256"] = ""
    manifest_path.write_text(json.dumps(parent), encoding="utf-8")
    partition_manifest.write_text(json.dumps(child), encoding="utf-8")
    with pytest.raises(ReviewServerError, match="(?i)partition"):
        create_review_app(partition, workspace_dir=tmp_path / "review-invalid")
