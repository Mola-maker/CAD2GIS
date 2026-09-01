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
    page = client.get("/").text
    assert "图纸理解、配准与交付审查" in page
    assert "把不可信的" in page
    assert "DWG" in page
    assert 'id="hero-page"' in page
    assert 'data-count="9717"' in page
    assert "assets/hero-evidence-graph.svg" in page
    assert 'id="process-terminal"' in page
    assert 'data-process-terminal' in page
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
