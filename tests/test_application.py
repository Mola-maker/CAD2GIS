"""Boundary and process-contract tests for the installable application shell."""

import io
import json
from pathlib import Path
import threading
import time

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cad2gis.application import ApplicationConfig, Jobs, create_application, main


@pytest.fixture
def shell(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    config = ApplicationConfig((root,), launch_token="launch-token")
    app = create_application(config)
    with TestClient(app, base_url="http://localhost", headers={"origin": "http://localhost"}) as client:
        yield app, client, root


def login(client, token="launch-token"):
    result = client.post("/_app/api/login", json={"token": token})
    assert result.status_code == 200, result.text
    return result


def test_public_shell_authentication_cookie_and_single_use_link(shell):
    app, client, root = shell
    assert client.get("/_app/").status_code == 200
    assert "手机" in client.get("/_app/").text
    assert client.get("/_app/api/status").status_code == 401
    result = login(client)
    cookie = result.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    status = client.get("/_app/api/status").json()
    assert status["roots"] == [str(root)]
    assert status["matching"] == "osm" and status["svg_mode"] == "candidate"
    assert client.post("/_app/api/login", json={"token": "launch-token"}).status_code == 401
    assert client.post("/_app/api/logout", json={}).status_code == 403
    assert client.get("/_app/api/status").status_code == 200
    assert client.post("/_app/api/shutdown", json={}).status_code == 200


def test_host_and_origin_are_enforced_before_actions(shell):
    _, client, _ = shell
    assert client.get("/_app/", headers={"host": "attacker.example"}).status_code == 400
    assert client.post("/_app/api/login", json={"token": "launch-token"},
                       headers={"origin": "https://attacker.example"}).status_code == 403
    login(client)
    client.headers.pop("origin")
    assert client.post("/_app/api/jobs", json={"action": "doctor"}).status_code == 403
    client.headers["origin"] = "null"
    assert client.post("/_app/api/logout", json={}).status_code == 403


def test_remote_requires_tls_and_password_and_has_secure_cookie(tmp_path):
    with pytest.raises(ValueError, match="requires TLS"):
        ApplicationConfig((tmp_path,), hosts=("cad.example",), remote=True, password="a-strong-password")
    with pytest.raises(ValueError, match="requires TLS"):
        ApplicationConfig((tmp_path,), hosts=("cad.example",), remote=True, secure=True, password="short")
    config = ApplicationConfig((tmp_path,), hosts=("cad.example",), remote=True, secure=True,
                               password="a-strong-password")
    app = create_application(config)
    with TestClient(app, base_url="https://cad.example", headers={"origin": "https://cad.example"}) as client:
        result = client.post("/_app/api/login", json={"password": "a-strong-password"})
        assert result.status_code == 200
        assert "secure" in result.headers["set-cookie"].lower()
        assert client.get("/_app/api/status").json()["remote"] is True
        assert client.post("/_app/api/shutdown", json={}).status_code == 403
        assert client.post("/_app/api/logout", json={}).status_code == 200
        assert client.get("/_app/api/status").status_code == 401
        assert client.post("/_app/api/login", json={"password": "a-strong-password"}).status_code == 200
    with TestClient(app, base_url="http://cad.example") as client:
        assert client.get("/_app/").status_code == 403


def test_remote_password_supports_chinese_text(tmp_path):
    config = ApplicationConfig((tmp_path,), hosts=("cad.example",), remote=True, secure=True,
                               password="这是服务器的安全密码123")
    with TestClient(create_application(config), base_url="https://cad.example",
                    headers={"origin": "https://cad.example"}) as client:
        assert client.post("/_app/api/login", json={"password": "错误密码"}).status_code == 401
        assert client.post("/_app/api/login", json={"password": config.password}).status_code == 200
        assert client.get("/_app/api/status").status_code == 200


def test_login_rate_limit_and_payload_limits(shell):
    _, client, _ = shell
    for _ in range(10):
        assert client.post("/_app/api/login", json={"token": "wrong"}).status_code == 401
    assert client.post("/_app/api/login", json={"token": "launch-token"}).status_code == 429


def test_file_admission_traversal_and_symlinks(shell, tmp_path):
    app, client, root = shell
    login(client)
    source = root / "survey.dwg"
    source.write_bytes(b"AC1032")
    (root / "notes.txt").write_text("not a CAD file")
    listing = client.get("/_app/api/files", params={"path": str(root)}).json()
    assert listing["parent"] is None
    assert [entry["name"] for entry in listing["entries"]] == ["survey.dwg"]
    assert client.get("/_app/api/files", params={"path": str(root / "..")}).status_code == 400
    with pytest.raises(ValueError, match="完整"):
        app.config.path("../survey.dwg")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "shortcut"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    assert client.get("/_app/api/files", params={"path": str(link)}).status_code == 400
    assert "shortcut" not in [item["name"] for item in client.get("/_app/api/files").json()["entries"]]


def test_upload_is_unique_atomic_and_does_not_accept_path_names(shell, monkeypatch):
    _, client, root = shell
    login(client)
    headers = {"content-type": "application/octet-stream"}
    uploaded = client.post("/_app/api/upload?name=survey.dwg", content=b"AC1032", headers=headers)
    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    source = Path(payload["source"])
    assert root in source.parents and source.read_bytes() == b"AC1032"
    assert not source.with_name(".uploading").exists()
    second = client.post("/_app/api/upload?name=survey.dwg", content=b"AC1015", headers=headers)
    assert second.json()["source"] != payload["source"]
    for name in ("../escape.dwg", "..\\escape.dwg", "C:\\escape.dwg", "CON.dwg", "script.py", "unsupported.dxf"):
        assert client.post("/_app/api/upload", params={"name": name}, content=b"data", headers=headers).status_code == 400
    assert client.post("/_app/api/upload?name=empty.dwg", content=b"", headers=headers).status_code == 400
    monkeypatch.setattr("cad2gis.application.MAX_UPLOAD_BYTES", 3)
    assert client.post("/_app/api/upload?name=big.dwg", content=b"1234", headers=headers).status_code == 413
    assert not list(root.rglob(".uploading"))


def test_command_contract_keeps_osm_svg_and_prevents_overwriting(shell):
    app, client, root = shell
    login(client)
    source = root / "drawing & echo bad.dwg"
    source.write_bytes(b"AC1032")
    project = root / "project"
    project.mkdir()
    run = root / "new result"
    args, result = app.jobs.command({"action": "convert", "source": str(source), "project": str(project),
                                     "run": str(run), "matching": "nominal", "svg_mode": "off"})
    assert args == ["convert", str(source), "--project", str(project), "--run-dir", str(run),
                    "--matching", "osm", "--svg-mode", "candidate", "--json"]
    assert result == str(run)
    run.mkdir()
    with pytest.raises(ValueError, match="已存在"):
        app.jobs.command({"action": "convert", "source": str(source), "project": str(project), "run": str(run)})
    auto, _ = app.jobs.command({"action": "auto-convert", "source": str(source), "project": str(root / "draft"),
                                "run": str(root / "another"), "provider": "new-api", "osm_place": "City"})
    assert auto[-5:] == ["--osm-place", "City", "--provider", "new-api", "--json"]


def test_owned_process_invocation_captures_failure_and_hides_secrets(tmp_path, monkeypatch):
    captured = {}
    finished = threading.Event()

    class Process:
        def __init__(self, args, **kwargs):
            captured.update(args=args, **kwargs)
            self.stdout = io.StringIO("provider secret-value failed\n")

        def wait(self):
            finished.set()
            return 2

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")
    monkeypatch.setattr("cad2gis.application.subprocess.Popen", Process)
    jobs = Jobs(ApplicationConfig((tmp_path,)))
    record = jobs.start({"action": "doctor"})
    assert finished.wait(3)
    for _ in range(100):
        result = jobs.list()[0]
        if result["status"] == "failed":
            break
        time.sleep(.01)
    assert result["id"] == record["id"] and result["exit_code"] == 2
    assert "secret-value" not in result["log"]
    assert captured["args"][1:9] == ["-I", "-B", "-u", "-X", "utf8", "-m", "cad2gis.cli", "doctor"]
    assert not captured.get("shell")
    assert captured["env"]["CAD2GIS_PROJECT_ROOTS"] == str(tmp_path)


def test_review_fallback_reuses_root_routes_and_authenticates_websockets(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    invoked = []
    review = FastAPI()

    @review.get("/api/run")
    async def summary():
        return {"run": "original-review"}

    @review.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"type": "connected"})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass

    def factory(path, **kwargs):
        invoked.append((path, kwargs))
        return review

    app = create_application(ApplicationConfig((tmp_path,), launch_token="launch-token"), review_factory=factory)
    with TestClient(app, base_url="http://localhost", headers={"origin": "http://localhost"}) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("ws://localhost/ws"):
                pass
        login(client)
        opened = client.post("/_app/api/review", json={"run": str(run)}).json()
        assert opened["url"] == f"/?review={opened['review_id']}#console-app"
        assert invoked == [(run, {"workspace_dir": tmp_path / "run.review"})]
        assert client.get("/api/run").json()["run"] == "original-review"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("ws://localhost/ws", headers={"origin": "https://attacker.example"}):
                pass
        with client.websocket_connect(f"ws://localhost/ws?review={opened['review_id']}") as websocket:
            assert websocket.receive_json() == {"type": "connected"}
        assert not list(run.iterdir())  # The immutable run was not modified.


def test_review_manifest_cannot_grant_outside_file_access(shell, tmp_path):
    app, client, root = shell
    login(client)
    run = root / "run"
    run.mkdir()
    (tmp_path / "private.dwg").write_bytes(b"private")
    (run / "run_manifest.json").write_text(json.dumps({"source": {"path": str(tmp_path / "private.dwg")}}))
    result = client.post("/_app/api/review", json={"run": str(run)})
    assert result.status_code == 400 and "超出" in result.text
    assert not app.review_cache


def test_copied_manifest_can_keep_missing_old_paths_with_local_fallback(shell, tmp_path):
    app, _, root = shell
    run = root / "copied-run"
    run.mkdir()
    (run / "delivery.gpkg").write_bytes(b"portable data")
    (run / "drawing.dwg").write_bytes(b"AC1032")
    (run / "run_manifest.json").write_text(json.dumps({
        "run_dir": str(tmp_path / "old-run"),
        "source": {"path": str(tmp_path / "old-run" / "drawing.dwg")},
        "artifacts": {"delivery": {"path": str(tmp_path / "old-run" / "delivery.gpkg")}},
    }))
    # This admits paths only. Canonical review still verifies SHA-256 and GPKG.
    app.config.admit_run(run, root / "copied-run.review")


def test_relative_artifact_cannot_resolve_from_unapproved_process_cwd(shell, tmp_path, monkeypatch):
    app, client, root = shell
    login(client)
    run = root / "run"
    run.mkdir()
    outside = tmp_path / "outside-cwd"
    outside.mkdir()
    (outside / "private.gpkg").write_bytes(b"private database")
    monkeypatch.chdir(outside)
    (run / "run_manifest.json").write_text(json.dumps({
        "artifacts": {"delivery": {"path": "private.gpkg"}},
    }))
    result = client.post("/_app/api/review", json={"run": str(run)})
    assert result.status_code == 400 and "超出" in result.text
    assert not app.review_cache


def test_active_run_references_are_admitted_before_review_factory(shell, tmp_path):
    app, client, root = shell
    login(client)
    run = root / "old-run"
    run.mkdir()
    workspace = root / "old-run.review"
    workspace.mkdir()
    outside = tmp_path / "outside-run"
    outside.mkdir()
    active = workspace / "matching-active.json"
    active.write_text(json.dumps({"source_run_dir": str(outside), "run_dir": str(outside)}))
    assert client.post("/_app/api/review", json={"run": str(run)}).status_code == 400
    next_run = root / "next-run"
    next_run.mkdir()
    outside_source = tmp_path / "outside.dwg"
    outside_source.write_bytes(b"private")
    (next_run / "run_manifest.json").write_text(json.dumps({"source": {"path": str(outside_source)}}))
    active.write_text(json.dumps({"source_run_dir": str(next_run), "run_dir": str(next_run)}))
    assert client.post("/_app/api/review", json={"run": str(run)}).status_code == 400
    assert not app.review_cache


def test_review_write_paths_resolve_before_factory_even_if_the_file_is_missing(shell, tmp_path, monkeypatch):
    app, client, root = shell
    login(client)
    run = root / "run"
    run.mkdir()
    original = Path.resolve
    escaped = root / "run.review" / "review.sqlite3"

    def resolve(path, *args, **kwargs):
        # Simulate a junction/symlink on Windows accounts that cannot create one.
        if path == escaped:
            return tmp_path / "private.sqlite3"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    result = client.post("/_app/api/review", json={"run": str(run)})
    assert result.status_code == 400 and not app.review_cache


def test_root_review_is_rechecked_after_a_path_changes(shell, tmp_path):
    app, client, root = shell
    login(client)
    run = root / "run"
    run.mkdir()
    manifest = run / "run_manifest.json"
    manifest.write_text("{}")
    review = FastAPI()

    @review.get("/api/run")
    async def summary():
        return {"ok": True}

    app.review_factory = lambda *_args, **_kwargs: review
    assert client.post("/_app/api/review", json={"run": str(run)}).status_code == 200
    assert client.get("/api/run").status_code == 200
    private = tmp_path / "private.gpkg"
    private.write_bytes(b"data")
    manifest.write_text(json.dumps({"artifacts": {"delivery": {"path": str(private)}}}))
    assert client.get("/api/run").status_code == 400


def test_expired_cookie_is_rejected(shell):
    app, client, _ = shell
    login(client)
    token = client.cookies.get(app.cookie_name)
    app.sessions[token].expires = time.monotonic() - 1
    assert client.get("/_app/api/status").status_code == 401


@pytest.mark.parametrize("reason", ["logout", "expiry"])
def test_open_websocket_is_closed_when_session_ends(tmp_path, reason):
    root = tmp_path
    run = root / "run"
    run.mkdir()
    review = FastAPI()

    @review.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"type": "connected"})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass

    config = ApplicationConfig((root,), hosts=("localhost",), remote=True, secure=True,
                               password="a-strong-password")
    app = create_application(config, review_factory=lambda *_args, **_kwargs: review)
    with TestClient(app, base_url="https://localhost", headers={"origin": "https://localhost"}) as client:
        assert client.post("/_app/api/login", json={"password": config.password}).status_code == 200
        opened = client.post("/_app/api/review", json={"run": str(run)})
        assert opened.status_code == 200
        if reason == "expiry":
            app.sessions[client.cookies.get(app.cookie_name)].expires = time.monotonic() + .2
        with client.websocket_connect(f"wss://localhost/ws?review={opened.json()['review_id']}") as ws:
            assert ws.receive_json() == {"type": "connected"}
            if reason == "logout":
                assert client.post("/_app/api/logout", json={}).status_code == 200
            with pytest.raises(WebSocketDisconnect) as disconnected:
                ws.receive_json()
            assert disconnected.value.code == 1008


def test_remote_cli_rejects_insecure_binding_before_runtime_import(tmp_path, monkeypatch):
    monkeypatch.delenv("CAD2GIS_APP_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as raised:
        main(["--host", "0.0.0.0", "--public-host", "cad.example", "--project-root", str(tmp_path), "--no-browser"])
    assert raised.value.code == 2
