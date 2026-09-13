"""A shared browser login must never move an old page's edits to another run."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from cad2gis.application import ApplicationConfig, create_application
from cad2gis.review_server import SQLiteReviewStore


@pytest.fixture
def review_session(tmp_path):
    stores = {}

    def factory(run: Path, *, workspace_dir: Path):
        store = SQLiteReviewStore(workspace_dir / "review.sqlite3", session_id=run.name)
        stores[run.name] = store
        review = FastAPI()

        @review.get("/api/review/features")
        async def features():
            return store.feature_collection()

        @review.post("/api/review/features")
        async def create(request: Request):
            body = await request.json()
            return store.upsert(body["feature"], expected_revision=0, actor="browser")

        @review.websocket("/ws")
        async def websocket(socket: WebSocket):
            await socket.accept()
            await socket.send_json({"run": run.name})
            try:
                while True:
                    await socket.receive_text()
            except WebSocketDisconnect:
                pass

        return review

    app = create_application(ApplicationConfig((tmp_path,), launch_token="start"), review_factory=factory)
    with TestClient(app, base_url="http://localhost", headers={"Origin": "http://localhost"}) as client:
        assert client.post("/_app/api/login", json={"token": "start"}).status_code == 200

        def open_run(name):
            directory = tmp_path / name
            directory.mkdir(exist_ok=True)
            response = client.post("/_app/api/review", json={"run": str(directory)})
            assert response.status_code == 200, response.text
            return response.json()

        yield app, client, stores, open_run


def test_old_tab_cannot_write_to_new_run_or_read_its_controls(review_session):
    _, client, stores, open_run = review_session
    first = open_run("run-a")
    first_headers = {"X-CAD2GIS-Review": first["review_id"]}
    second = open_run("run-b")
    second_headers = {"X-CAD2GIS-Review": second["review_id"]}
    assert first["review_id"] != second["review_id"]
    feature = {"type": "Feature", "id": "gcp-from-tab-a",
               "geometry": {"type": "Point", "coordinates": [120, 30]},
               "properties": {"_kind": "cad_map_gcp", "cad_x": 1, "cad_y": 2, "role": "train"}}
    # A tagless old client is rejected too, rather than silently writing into B.
    for headers in ({}, first_headers):
        response = client.post("/api/review/features", json={"feature": feature}, headers=headers)
        assert response.status_code == 409
    assert client.get("/api/review/features", headers=first_headers).status_code == 409
    assert not stores["run-a"].feature_collection()["features"]
    assert not stores["run-b"].feature_collection()["features"]
    current = client.post("/api/review/features", json={"feature": feature}, headers=second_headers)
    assert current.status_code == 200, current.text
    assert len(stores["run-b"].feature_collection()["features"]) == 1
    assert not stores["run-a"].feature_collection()["features"]
    # Copying or reloading the root URL receives an explicit page binding.
    redirect = client.get("/", follow_redirects=False)
    assert redirect.headers["location"] == second["url"]


def test_run_switch_closes_old_socket_but_same_run_tabs_keep_identity(review_session):
    _, client, _, open_run = review_session
    first = open_run("run-a")
    with client.websocket_connect(f"ws://localhost/ws?review={first['review_id']}") as old:
        assert old.receive_json() == {"run": "run-a"}
        assert open_run("run-a")["review_id"] == first["review_id"]
        second = open_run("run-b")
        with pytest.raises(WebSocketDisconnect) as closed:
            old.receive_json()
        assert closed.value.code == 1008
    for suffix in ("", f"?review={first['review_id']}"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("ws://localhost/ws" + suffix):
                pass
    with client.websocket_connect(f"ws://localhost/ws?review={second['review_id']}") as current:
        assert current.receive_json() == {"run": "run-b"}


def test_local_logout_preserves_access_and_shutdown_still_honors_active_jobs(review_session):
    app, client, _, _ = review_session
    assert client.post("/_app/api/logout", json={}).status_code == 403
    assert client.get("/_app/api/status").status_code == 200
    app.jobs.active = "running-job"
    assert client.post("/_app/api/shutdown", json={}).status_code == 409
    app.jobs.active = ""
    stopped = []
    app.shutdown_callback = lambda: stopped.append(True)
    assert client.post("/_app/api/shutdown", json={}).status_code == 200
    client.portal.call(asyncio.sleep, .4)
    assert stopped == [True]


def test_review_admission_keeps_status_responsive_and_rechecks_run_switch(review_session, monkeypatch):
    app, client, _, open_run = review_session
    first = open_run("run-a")
    entered, release = threading.Event(), threading.Event()
    original = ApplicationConfig.admit_run

    def slow_admission(config, directory, workspace):
        if directory.name == "run-a":
            entered.set()
            assert release.wait(5), "Test did not release the pending admission"
        return original(config, directory, workspace)

    monkeypatch.setattr(ApplicationConfig, "admit_run", slow_admission)
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(client.get, "/api/review/features",
                                 headers={"X-CAD2GIS-Review": first["review_id"]})
        try:
            assert entered.wait(2)
            status = workers.submit(client.get, "/_app/api/status")
            assert status.result(timeout=1).status_code == 200
            open_run("run-b")
        finally:
            release.set()
        assert pending.result(timeout=2).status_code == 409


@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("revocation", ["logout", "expiry"])
def test_pending_review_admission_cannot_outlive_authentication(tmp_path, monkeypatch, transport, revocation):
    """Admission must not create a socket after logout already closed its session."""
    def factory(run, **kwargs):
        review = FastAPI()

        @review.get("/api/review/features")
        async def features():
            return {"protected": True}

        @review.websocket("/ws")
        async def websocket(socket: WebSocket):
            await socket.accept()
            await socket.send_json({"protected": True})
            try:
                await socket.receive_text()
            except WebSocketDisconnect:
                pass

        return review

    config = ApplicationConfig((tmp_path,), remote=True, secure=True, password="fixture-password-only")
    app = create_application(config, review_factory=factory)
    with TestClient(app, base_url="https://localhost", headers={"Origin": "https://localhost"}) as client:
        assert client.post("/_app/api/login", json={"password": "fixture-password-only"}).status_code == 200
        directory = tmp_path / "run"
        directory.mkdir()
        opened = client.post("/_app/api/review", json={"run": str(directory)}).json()
        entered, release = threading.Event(), threading.Event()
        original = ApplicationConfig.admit_run

        def slow_admission(config, directory, workspace):
            entered.set()
            assert release.wait(5), "Test did not release the pending admission"
            return original(config, directory, workspace)

        monkeypatch.setattr(ApplicationConfig, "admit_run", slow_admission)

        def request():
            if transport == "http":
                return client.get("/api/review/features", headers={"X-CAD2GIS-Review": opened["review_id"]}).status_code
            try:
                with client.websocket_connect(f"wss://localhost/ws?review={opened['review_id']}") as socket:
                    socket.receive_json()
                    return 200
            except WebSocketDisconnect as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(request)
            try:
                assert entered.wait(2)
                if revocation == "logout":
                    assert client.post("/_app/api/logout", json={}).status_code == 200
                    assert not app.sessions
                else:
                    session = next(iter(app.sessions.values()))
                    session.expires = time.monotonic() - 1
            finally:
                release.set()
            assert pending.result(timeout=2) == (401 if transport == "http" else 1008)
