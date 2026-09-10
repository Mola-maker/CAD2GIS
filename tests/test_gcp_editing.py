"""Ground-distance editing, source lineage and persisted concurrency contracts."""

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import threading

import pytest
from fastapi.testclient import TestClient
from pyproj import Geod

from cad2gis.gcp_workflow import GCP_UNDO_LIMIT, edit_web_control
from cad2gis.review_server import (
    ReviewConflictError, ReviewServerError, SQLiteReviewStore,
    _artifact_path, _registration_controls, create_review_app,
)


def _control(identity="gcp:one", target=(12.0, 60.0), cad=(100.0, 200.0), role="train"):
    return {
        "type": "Feature", "id": identity,
        "geometry": {"type": "Point", "coordinates": list(target)},
        "properties": {"_kind": "cad_map_gcp", "cad_x": cad[0], "cad_y": cad[1],
                       "cad_layer": "CABLE", "source_handle": "A12", "role": role},
    }


def _client(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    delivery = run / "delivery.gpkg"
    delivery.write_bytes(b"immutable delivery fixture")
    (run / "run_manifest.json").write_text(json.dumps({
        "source": {"sha256": "a" * 64},
        "crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857"},
        "artifacts": {"delivery": {"path": str(delivery),
            "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest()}},
    }))
    workspace = tmp_path / "review"
    return TestClient(create_review_app(run, workspace_dir=workspace)), run, workspace


@pytest.mark.parametrize("latitude", [0.0, 60.0, 80.0])
@pytest.mark.parametrize("step", [0.01, 0.1, 1.0])
@pytest.mark.parametrize("direction,azimuth", [("north", 0), ("east", 90), ("south", 180), ("west", -90)])
def test_nudges_are_ellipsoidal_ground_metres(latitude, step, direction, azimuth):
    original = edit_web_control(_control(target=(12, latitude)))
    moved = edit_web_control(original, original, action={
        "operation": "nudge", "direction": direction, "step_m": step,
    })
    a, _, distance = Geod(ellps="WGS84").inv(
        *original["geometry"]["coordinates"], *moved["geometry"]["coordinates"])
    assert distance == pytest.approx(step, abs=3e-9)
    angle_difference = (a - azimuth + 180) % 360 - 180
    assert angle_difference == pytest.approx(0, abs=1e-5)
    assert moved["properties"]["cad_x"] == original["properties"]["cad_x"]
    assert moved["properties"]["source_handle"] == "A12"
    assert original["properties"]["_gcp_edit"]["undo_targets"] == []


def test_nudge_crosses_antimeridian_without_out_of_range_longitude():
    original = edit_web_control(_control(target=(179.99999999, 1)))
    moved = edit_web_control(original, original, action={
        "operation": "nudge", "direction": "east", "step_m": 1,
    })
    assert -180 <= moved["geometry"]["coordinates"][0] < 0


def test_edit_history_and_initial_target_survive_process_restart(tmp_path):
    client, run, workspace = _client(tmp_path)
    original = _control()
    created = client.post("/api/review/features", json={"feature": original, "expected_revision": 0})
    assert created.status_code == 200, created.text
    endpoint = "/api/review/features/gcp:one/adjust"
    moved = client.post(endpoint, json={"operation": "nudge", "direction": "east",
                                        "step_m": 0.01, "expected_revision": 1})
    assert moved.status_code == 200, moved.text
    first_target = moved.json()["payload"]["geometry"]["coordinates"]
    stale = client.post(endpoint, json={"operation": "nudge", "direction": "north",
                                        "step_m": 1, "expected_revision": 1})
    assert stale.status_code == 409
    client = TestClient(create_review_app(run, workspace_dir=workspace))
    assert client.get("/api/review/features").json()["features"][0]["geometry"]["coordinates"] == first_target
    undone = client.post(endpoint, json={"operation": "undo", "expected_revision": 2})
    assert undone.status_code == 200, undone.text
    assert undone.json()["payload"]["geometry"] == original["geometry"]
    assert client.post(endpoint, json={"operation": "undo", "expected_revision": 3}).status_code == 400
    moved_again = client.post(endpoint, json={"operation": "nudge", "direction": "south",
                                              "step_m": 0.1, "expected_revision": 3})
    assert moved_again.status_code == 200
    reset = client.post(endpoint, json={"operation": "reset", "expected_revision": 4})
    assert reset.status_code == 200
    assert reset.json()["payload"]["geometry"] == original["geometry"]
    undo_reset = client.post(endpoint, json={"operation": "undo", "expected_revision": 5})
    assert undo_reset.json()["payload"]["geometry"] == moved_again.json()["payload"]["geometry"]
    assert len(client.get("/api/review/events").json()["events"]) == 6
    assert (run / "delivery.gpkg").read_bytes() == b"immutable delivery fixture"


@pytest.mark.parametrize("patch", [
    {"cad_x": None}, {"cad_y": True}, {"cad_x": "12"}, {"cad_x": float("inf")},
    {"cad_x": float("nan")}, {"role": []}, {"role": "test"},
    {"enabled": "true"}, {"weight": 0}, {"accuracy_m": -1},
])
def test_invalid_control_is_rejected_before_it_can_poison_registration(tmp_path, patch):
    client, _, _ = _client(tmp_path)
    feature = _control()
    feature["properties"].update(patch)
    response = client.post("/api/review/features", content=json.dumps({
        "feature": feature, "expected_revision": 0,
    }), headers={"Content-Type": "application/json"})
    assert response.status_code == 400, response.text
    assert client.get("/api/review/features").json()["features"] == []


@pytest.mark.parametrize("expected", [None, True, -1, "1", 1.5])
def test_gcp_mutations_require_explicit_integer_revision(tmp_path, expected):
    client, _, _ = _client(tmp_path)
    assert client.post("/api/review/features", json={"feature": _control(),
        "expected_revision": expected}).status_code == 400
    assert client.post("/api/review/features", json={"feature": _control(),
        "expected_revision": 0}).status_code == 200
    assert client.post("/api/review/features/gcp:one/adjust", json={
        "operation": "reset", "expected_revision": expected}).status_code == 400


@pytest.mark.parametrize("patch", [{"cad_x": 200}, {"cad_layer": "OTHER"},
                                   {"source_handle": "FAKE"}, {"_kind": "note"}])
def test_existing_control_source_is_immutable(tmp_path, patch):
    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="one")
    store.upsert(_control(), expected_revision=0, actor="test")
    changed = _control()
    changed["properties"].update(patch)
    with pytest.raises(ReviewServerError, match="immutable"):
        store.upsert(changed, expected_revision=1, actor="test")
    assert store.feature_collection()["features"][0]["properties"]["_review_revision"] == 1


def test_old_client_cannot_erase_undo_or_forge_initial_target(tmp_path):
    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="one")
    original = _control()
    original["properties"]["_gcp_edit"] = {"initial_target": [100, 20]}
    store.upsert(original, expected_revision=0, actor="test")
    changed = _control(target=(12.1, 60))
    changed["properties"]["_gcp_edit"] = {"initial_target": [100, 20]}
    changed["properties"].pop("source_handle")
    result = store.upsert(changed, expected_revision=1, actor="test")
    state = result["payload"]["properties"]["_gcp_edit"]
    assert state["initial_target"] == [12, 60]
    assert state["undo_targets"] == [[12, 60]]
    assert result["payload"]["properties"]["source_handle"] == "A12"


def test_undo_history_is_bounded_and_reset_keeps_initial_pick():
    control = edit_web_control(_control())
    original = copy.deepcopy(control)
    for _ in range(GCP_UNDO_LIMIT + 5):
        control = edit_web_control(control, control, action={
            "operation": "nudge", "direction": "east", "step_m": 1,
        })
    assert len(control["properties"]["_gcp_edit"]["undo_targets"]) == GCP_UNDO_LIMIT
    assert edit_web_control(control, control, action={"operation": "reset"})["geometry"] == original["geometry"]


def test_two_process_stores_cannot_both_apply_the_same_revision(tmp_path):
    path = tmp_path / "review.sqlite3"
    first = SQLiteReviewStore(path, session_id="one")
    second = SQLiteReviewStore(path, session_id="one")
    feature = first.upsert(_control(), expected_revision=0, actor="setup")["payload"]
    barrier = threading.Barrier(2)

    def edit(store):
        barrier.wait()
        try:
            return store.upsert(feature, expected_revision=1, actor="racer", gcp_action={
                "operation": "nudge", "direction": "east", "step_m": 1,
            })["revision"]
        except ReviewConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, (first, second)))
    assert sorted(map(str, results)) == ["2", "conflict"]
    assert len(first.events()) == 2


def test_diagonal_collinearity_cannot_pass_distribution_preflight(tmp_path):
    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="one")
    for i in range(7):
        store.upsert(_control(f"gcp:{i}", (12 + i * 0.001, 60 + i * 0.001),
            cad=(100 * i, 100 * i), role="train" if i < 4 else "check"),
            expected_revision=0, actor="test")
    capture = _registration_controls(store, {"crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857"}})
    assert capture["train_count"] == 4 and capture["check_count"] == 3
    assert capture["activation_ready"] is False
    assert capture["distribution_checks"]["train_two_dimensional"] is False
    assert capture["precision_contract"]["nudge_step_is_accuracy_claim"] is False


def test_checks_reusing_training_locations_cannot_pass_as_independent(tmp_path):
    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="one")
    for i, (x, y, role) in enumerate([(0, 0, "train"), (100, 0, "train"),
            (0, 100, "train"), (100, 100, "train"), (0, 0, "check"),
            (20, 80, "check"), (80, 20, "check")]):
        store.upsert(_control(f"gcp:{i}", (12 + x * 0.001, 60 + y * 0.001),
            cad=(x, y), role=role), expected_revision=0, actor="test")
    capture = _registration_controls(store, {"crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857"}})
    assert capture["activation_ready"] is False
    assert capture["distribution_checks"]["checks_reusing_training_locations"] == ["gcp:4"]


def test_disabled_controls_are_excluded_from_counts_and_fit_input(tmp_path):
    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="one")
    feature = _control()
    feature["properties"]["enabled"] = False
    store.upsert(feature, expected_revision=0, actor="test")
    capture = _registration_controls(store, {"crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857"}})
    assert capture["train_count"] == 0
    assert capture["controls"][0]["enabled"] is False


def test_main_run_cannot_start_from_a_tampered_existing_delivery(tmp_path):
    _, run, _ = _client(tmp_path)
    (run / "delivery.gpkg").write_bytes(b"wrong drawing")
    with pytest.raises(ReviewServerError, match="delivery SHA-256 mismatch"):
        create_review_app(run, workspace_dir=tmp_path / "second-review")


def test_review_prefers_hash_bound_current_run_over_a_stale_existing_original(tmp_path):
    original = tmp_path / "old-delivery.gpkg"
    original.write_bytes(b"stale or replaced file")
    run = tmp_path / "run"
    run.mkdir()
    local = run / "delivery.gpkg"
    local.write_bytes(b"correct copied run")
    manifest = {"artifacts": {"delivery": {"path": str(original),
        "sha256": hashlib.sha256(local.read_bytes()).hexdigest()}}}
    assert _artifact_path(manifest, "delivery", run_dir=run) == local
    with pytest.raises(ReviewServerError, match="SHA-256 mismatch"):
        _artifact_path(manifest, "delivery")
    local.write_bytes(b"also corrupt")
    with pytest.raises(ReviewServerError, match="SHA-256 mismatch"):
        _artifact_path(manifest, "delivery", run_dir=run)


def test_repeated_points_do_not_inflate_distribution_counts(tmp_path):
    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="one")
    for i, (x, y, role) in enumerate([(0, 0, "train"), (100, 0, "train"),
            (0, 100, "train"), (0, 100, "train"), (10, 10, "check"),
            (20, 80, "check"), (80, 20, "check")]):
        store.upsert(_control(f"gcp:{i}", (12 + x * 0.001, 60 + y * 0.001),
            cad=(x, y), role=role), expected_revision=0, actor="test")
    capture = _registration_controls(store, {"crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857"}})
    assert capture["train_count"] == 4
    assert capture["activation_ready"] is False
