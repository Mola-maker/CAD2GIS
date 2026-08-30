from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from cad2gis.agent_mcp import prepare_review_workspace
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
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"immutable-delivery-fixture")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "source": {"path": "fixture.dwg", "sha256": "a" * 64},
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
    landing = client.get("/").text
    install = client.get("/install").text
    workspace_page = client.get("/workspace").text
    assert "让 CAD 证据" in landing
    assert "我们一步一步来" in install
    assert "配准、坐标传送与叠加审查" in workspace_page
    assert all("/assets/pointer.js" in page for page in (
        landing, install, workspace_page,
    ))
    assert client.get("/assets/pointer.js").status_code == 200
    assert client.get("/assets/pointer.css").status_code == 200
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
    assert (workspace / "web_gcp_profile.json").is_file()


def test_mcp_prepares_separate_review_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOTS", raising=False)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "source": {"sha256": "b" * 64},
    }), encoding="utf-8")

    result = prepare_review_workspace(str(run_dir), port=9876)

    assert result["immutable_delivery"] is True
    assert result["url"] == "http://127.0.0.1:9876/workspace"
    assert result["landing_url"] == "http://127.0.0.1:9876/"
    assert result["install_url"] == "http://127.0.0.1:9876/install"
    assert "cad2gis review" in result["launch_command"]
    assert (tmp_path / "run.review" / "review.sqlite3").is_file()
