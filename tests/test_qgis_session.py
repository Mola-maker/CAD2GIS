from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from cad2gis.qgis_session import (
    QGIS_EXECUTABLE_ENV,
    QgisSessionError,
    discover_qgis_executable,
    inspect_qgis_session,
    load_qgis_run,
    _qgis_environment,
    start_qgis_session,
)


def _descriptor(tmp_path: Path, listener: socket.socket, token: str) -> Path:
    path = tmp_path / "qgis-session.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "cad2gis.qgis_session.v1",
                "status": "ready",
                "host": "127.0.0.1",
                "port": listener.getsockname()[1],
                "token": token,
                "allowed_roots": [str(tmp_path)],
                "pid": 1234,
            }
        ),
        encoding="utf-8",
    )
    return path


def _serve_once(listener: socket.socket, expected: dict, result: dict) -> threading.Thread:
    def run() -> None:
        connection, _ = listener.accept()
        with connection:
            line = b""
            while b"\n" not in line:
                line += connection.recv(65536)
            request = json.loads(line.partition(b"\n")[0].decode("utf-8"))
            for key, value in expected.items():
                assert request[key] == value
            connection.sendall(
                json.dumps({"ok": True, "result": result}).encode("utf-8") + b"\n"
            )
        listener.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener


def test_discover_qgis_executable_honors_explicit_environment(tmp_path: Path) -> None:
    executable = tmp_path / "qgis-bin.exe"
    executable.write_bytes(b"MZ")

    result = discover_qgis_executable(
        environ={QGIS_EXECUTABLE_ENV: str(executable)}
    )

    assert result == {"path": str(executable.resolve()), "source": "environment"}


def test_discover_qgis_executable_rejects_arbitrary_executable(tmp_path: Path) -> None:
    executable = tmp_path / "python.exe"
    executable.write_bytes(b"MZ")

    with pytest.raises(QgisSessionError, match="qgis-bin.exe or qgis.exe"):
        discover_qgis_executable(environ={QGIS_EXECUTABLE_ENV: str(executable)})


def test_qgis_environment_uses_adjacent_osgeo_metadata(tmp_path: Path) -> None:
    executable = tmp_path / "qgis-bin.exe"
    executable.write_bytes(b"MZ")
    metadata = executable.with_suffix(".env")
    metadata.write_text(
        "PATH=E:\\\\apps\\\\qgis\\\\bin;E:\\\\bin\n"
        "PYTHONHOME=E:\\\\apps\\\\Python312\n",
        encoding="utf-8",
    )

    environment, source = _qgis_environment(
        executable,
        environ={"PATH": "C:\\Windows", "PYTHONHOME": "wrong"},
    )

    assert source == str(metadata)
    assert environment["PATH"] == r"E:\apps\qgis\bin;E:\bin"
    assert environment["PYTHONHOME"] == r"E:\apps\Python312"


def test_qgis_environment_cleans_conflicting_runtime_without_metadata(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "qgis-bin.exe"
    executable.write_bytes(b"MZ")

    environment, source = _qgis_environment(
        executable,
        environ={
            "PATH": "C:\\Windows",
            "PYTHONHOME": "wrong",
            "PYTHONPATH": "wrong",
            "QT_PLUGIN_PATH": "wrong",
            "QGIS_PREFIX_PATH": "wrong",
        },
    )

    assert source == "inherited_clean"
    assert environment == {"PATH": "C:\\Windows"}


def test_inspect_qgis_session_uses_token_authenticated_typed_request(
    tmp_path: Path,
) -> None:
    listener = _listener()
    token = "t" * 48
    session_file = _descriptor(tmp_path, listener, token)
    result = {"schema_version": "cad2gis.qgis_session_status.v1", "layer_count": 0}
    thread = _serve_once(
        listener,
        {
            "schema_version": "cad2gis.qgis_session.v1",
            "token": token,
            "command": "status",
            "parameters": {},
        },
        result,
    )

    assert inspect_qgis_session(session_file) == result
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_start_reuses_live_session_without_returning_token(tmp_path: Path) -> None:
    listener = _listener()
    token = "s" * 48
    session_file = _descriptor(tmp_path, listener, token)
    thread = _serve_once(
        listener,
        {"command": "status", "token": token},
        {"schema_version": "cad2gis.qgis_session_status.v1", "layer_count": 0},
    )

    result = start_qgis_session(tmp_path, allowed_roots=[tmp_path])

    assert result["reused"] is True
    assert result["session_file"] == str(session_file)
    assert "token" not in result
    thread.join(timeout=2)


def test_load_qgis_run_selects_delivery_and_styles(tmp_path: Path) -> None:
    listener = _listener()
    token = "r" * 48
    session_file = _descriptor(tmp_path, listener, token)
    run_dir = tmp_path / "run"
    styles = run_dir / "styles"
    styles.mkdir(parents=True)
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"SQLite format 3\x00")
    thread = _serve_once(
        listener,
        {
            "command": "load_layers",
            "token": token,
            "parameters": {
                "path": str(delivery.resolve()),
                "styles_dir": str(styles.resolve()),
                "clear_existing": True,
            },
        },
        {"loaded_count": 3},
    )

    result = load_qgis_run(session_file, run_dir)

    assert result["delivery"] == str(delivery.resolve())
    assert result["styles_dir"] == str(styles.resolve())
    assert result["qgis"] == {"loaded_count": 3}
    thread.join(timeout=2)


def test_load_qgis_run_prefers_canonical_qgis_styles(tmp_path: Path) -> None:
    listener = _listener()
    token = "q" * 48
    session_file = _descriptor(tmp_path, listener, token)
    run_dir = tmp_path / "run"
    legacy_styles = run_dir / "styles"
    canonical_styles = run_dir / "qgis" / "styles"
    legacy_styles.mkdir(parents=True)
    canonical_styles.mkdir(parents=True)
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"SQLite format 3\x00")
    thread = _serve_once(
        listener,
        {
            "command": "load_layers",
            "token": token,
            "parameters": {
                "path": str(delivery.resolve()),
                "styles_dir": str(canonical_styles.resolve()),
                "clear_existing": True,
            },
        },
        {"loaded_count": 2},
    )

    result = load_qgis_run(session_file, run_dir)

    assert result["styles_dir"] == str(canonical_styles.resolve())
    thread.join(timeout=2)
