"""Typed loopback control for a dedicated, visible QGIS desktop session.

The normal CAD2GIS process never imports QGIS.  Instead it launches QGIS with
``qgis_session_bootstrap.py`` and talks to the in-process bridge over a
token-authenticated localhost socket.  Only the fixed commands implemented by
the bootstrap are available; arbitrary Python execution is intentionally not
part of the protocol.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


QGIS_EXECUTABLE_ENV = "CAD2GIS_QGIS_EXECUTABLE"
SESSION_SCHEMA = "cad2gis.qgis_session.v1"
DEFAULT_SESSION_FILE = "qgis-session.json"
_LOOPBACK_HOST = "127.0.0.1"
_QGIS_EXECUTABLE_NAMES = {"qgis-bin.exe", "qgis.exe"}
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class QgisSessionError(RuntimeError):
    """The dedicated QGIS automation session could not satisfy a request."""


def _resolved_roots(values: Iterable[str | Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for value in values:
        root = Path(value).expanduser().resolve()
        if root not in roots:
            roots.append(root)
    if not roots:
        raise QgisSessionError("At least one allowed project root is required")
    return tuple(roots)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _descriptor(path: str | Path) -> tuple[Path, dict[str, Any]]:
    descriptor_path = Path(path).expanduser().resolve()
    if not descriptor_path.is_file():
        raise QgisSessionError(f"QGIS session descriptor does not exist: {descriptor_path}")
    try:
        payload = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QgisSessionError(f"Invalid QGIS session descriptor: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SESSION_SCHEMA:
        raise QgisSessionError("Unsupported QGIS session descriptor schema")
    if payload.get("host") != _LOOPBACK_HOST:
        raise QgisSessionError("QGIS session descriptor is not loopback-only")
    port = payload.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise QgisSessionError("QGIS session descriptor has an invalid port")
    token = payload.get("token")
    if not isinstance(token, str) or len(token) < 32:
        raise QgisSessionError("QGIS session descriptor has no valid session token")
    return descriptor_path, payload


def _public_descriptor(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in payload.items() if key != "token"}
    result["session_file"] = str(path)
    return result


def _start_menu_qgis_targets() -> tuple[Path, ...]:
    """Resolve QGIS Start Menu shortcuts when the executable is not on PATH."""

    if os.name != "nt":
        return ()
    roots: list[Path] = []
    program_data = os.environ.get("ProgramData")
    app_data = os.environ.get("APPDATA")
    if program_data:
        roots.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
    if app_data:
        roots.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
    shortcuts: list[Path] = []
    for root in roots:
        try:
            shortcuts.extend(root.glob("QGIS*/QGIS Desktop*.lnk"))
        except OSError:
            continue
    if not shortcuts:
        return ()
    try:
        import win32com.client  # type: ignore[import-not-found]

        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        return ()
    targets: dict[str, Path] = {}
    for shortcut in shortcuts:
        try:
            target = Path(shell.CreateShortcut(str(shortcut)).TargetPath).resolve()
        except Exception:
            continue
        if target.is_file() and target.name.casefold() in _QGIS_EXECUTABLE_NAMES:
            targets[str(target).casefold()] = target
    return tuple(sorted(targets.values(), key=lambda item: str(item).casefold()))


def discover_qgis_executable(*, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the selected QGIS desktop executable and its discovery source."""

    environment = os.environ if environ is None else environ
    configured = environment.get(QGIS_EXECUTABLE_ENV, "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not candidate.is_file():
            raise QgisSessionError(
                f"{QGIS_EXECUTABLE_ENV} points to a missing file: {candidate}"
            )
        if candidate.name.casefold() not in _QGIS_EXECUTABLE_NAMES:
            raise QgisSessionError(
                f"{QGIS_EXECUTABLE_ENV} must name qgis-bin.exe or qgis.exe"
            )
        return {"path": str(candidate), "source": "environment"}

    for name in ("qgis-bin.exe", "qgis.exe", "qgis-bin", "qgis"):
        executable = shutil.which(name)
        if executable:
            candidate = Path(executable).resolve()
            if candidate.name.casefold() in _QGIS_EXECUTABLE_NAMES:
                return {"path": str(candidate), "source": "PATH"}

    start_menu = _start_menu_qgis_targets()
    if start_menu:
        return {"path": str(start_menu[-1]), "source": "start_menu"}
    raise QgisSessionError(
        "QGIS Desktop was not found; install QGIS or set "
        f"{QGIS_EXECUTABLE_ENV} to qgis-bin.exe"
    )


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((_LOOPBACK_HOST, 0))
        return int(listener.getsockname()[1])


def _qgis_environment(
    executable: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], str]:
    """Build the launch environment from the installed QGIS launcher metadata.

    OSGeo4W installs place a ``qgis-bin.env`` file beside ``qgis-bin.exe``.
    Directly launching the executable without that file can make Qt, GDAL, or
    the embedded Python runtime fail before QGIS can write a useful log.  Only
    the adjacent, fixed-name file is read; it is treated as installation
    metadata rather than as a general-purpose dotenv file.
    """

    environment = dict(os.environ if environ is None else environ)
    metadata = executable.with_suffix(".env")
    if metadata.is_file():
        try:
            lines = metadata.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise QgisSessionError(
                f"Could not read the QGIS launch environment: {metadata}: {exc}"
            ) from exc
        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            name, separator, value = line.partition("=")
            name = name.strip()
            if not separator or not _ENVIRONMENT_NAME.fullmatch(name):
                raise QgisSessionError(
                    f"Invalid QGIS environment entry at {metadata}:{line_number}"
                )
            # OSGeo4W writes Windows paths with doubled backslashes.
            environment[name] = value.strip().replace("\\\\", "\\")
        return environment, str(metadata)

    for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QGIS_PREFIX_PATH"):
        environment.pop(name, None)
    return environment, "inherited_clean"


def _request(
    session_file: str | Path,
    command: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    _, descriptor = _descriptor(session_file)
    request = {
        "schema_version": SESSION_SCHEMA,
        "token": descriptor["token"],
        "command": command,
        "parameters": dict(parameters or {}),
    }
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"
    try:
        with socket.create_connection(
            (_LOOPBACK_HOST, int(descriptor["port"])), timeout=timeout
        ) as connection:
            connection.settimeout(timeout)
            connection.sendall(encoded)
            chunks = bytearray()
            while b"\n" not in chunks:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > 8 * 1024 * 1024:
                    raise QgisSessionError("QGIS session response exceeded 8 MiB")
    except (OSError, TimeoutError) as exc:
        raise QgisSessionError(f"QGIS session is unavailable: {exc}") from exc
    line = bytes(chunks).partition(b"\n")[0]
    try:
        response = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QgisSessionError(f"Invalid QGIS session response: {exc}") from exc
    if not isinstance(response, dict):
        raise QgisSessionError("QGIS session response must be an object")
    if response.get("ok") is not True:
        raise QgisSessionError(str(response.get("error") or "QGIS session request failed"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise QgisSessionError("QGIS session result must be an object")
    return result


def start_qgis_session(
    session_dir: str | Path,
    *,
    allowed_roots: Iterable[str | Path],
    project_path: str | Path | None = None,
    startup_timeout: float = 45.0,
) -> dict[str, Any]:
    """Launch a visible QGIS process with the typed localhost bridge enabled."""

    if os.name != "nt":
        raise QgisSessionError("The managed QGIS desktop session currently requires Windows")
    directory = Path(session_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    roots = _resolved_roots(allowed_roots)
    if not any(directory == root or root in directory.parents for root in roots):
        raise QgisSessionError("QGIS session directory is outside the allowed project roots")
    session_file = directory / DEFAULT_SESSION_FILE

    if session_file.is_file():
        try:
            status = inspect_qgis_session(session_file, timeout=1.0)
        except QgisSessionError:
            status = None
        if status is not None:
            return {
                **_public_descriptor(session_file, _descriptor(session_file)[1]),
                "qgis": status,
                "reused": True,
            }

    discovery = discover_qgis_executable()
    executable = Path(discovery["path"])
    bootstrap = Path(__file__).with_name("qgis_session_bootstrap.py").resolve()
    if not bootstrap.is_file():
        raise QgisSessionError(f"QGIS bridge bootstrap is missing: {bootstrap}")
    token = secrets.token_urlsafe(32)
    descriptor: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA,
        "status": "launching",
        "host": _LOOPBACK_HOST,
        "port": _reserve_port(),
        "token": token,
        "allowed_roots": [str(root) for root in roots],
        "qgis_executable": str(executable),
        "qgis_executable_source": discovery["source"],
        "created_at_unix": time.time(),
    }
    _atomic_json(session_file, descriptor)

    environment, environment_source = _qgis_environment(executable)
    environment["CAD2GIS_QGIS_SESSION_DESCRIPTOR"] = str(session_file)
    stdout_path = directory / "qgis-session.stdout.log"
    stderr_path = directory / "qgis-session.stderr.log"
    creation_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [
                str(executable),
                "--nologo",
                "--profiles-path",
                str(directory / "qgis-profiles"),
                "--profile",
                "cad2gis-managed",
                "--code",
                str(bootstrap),
            ],
            cwd=str(directory),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creation_flags,
        )
    descriptor.update(
        {
            "pid": int(process.pid),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "qgis_environment_source": environment_source,
        }
    )
    _atomic_json(session_file, descriptor)

    deadline = time.monotonic() + float(startup_timeout)
    last_error = "QGIS bridge has not reported ready"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise QgisSessionError(
                f"QGIS exited before the bridge became ready (exit {process.returncode}); "
                f"see {stderr_path}"
            )
        try:
            qgis = inspect_qgis_session(session_file, timeout=1.0)
        except QgisSessionError as exc:
            last_error = str(exc)
            time.sleep(0.25)
            continue
        if project_path is not None:
            qgis = open_qgis_project(session_file, project_path)
        return {
            **_public_descriptor(session_file, _descriptor(session_file)[1]),
            "qgis": qgis,
            "reused": False,
        }
    try:
        process.terminate()
    except OSError:
        pass
    raise QgisSessionError(
        f"Timed out waiting for the QGIS bridge: {last_error}; see {stderr_path}"
    )


def inspect_qgis_session(
    session_file: str | Path, *, timeout: float = 20.0
) -> dict[str, Any]:
    return _request(session_file, "status", timeout=timeout)


def open_qgis_project(
    session_file: str | Path, project_path: str | Path
) -> dict[str, Any]:
    return _request(
        session_file,
        "open_project",
        {"path": str(Path(project_path).expanduser().resolve())},
    )


def load_qgis_layers(
    session_file: str | Path,
    path: str | Path,
    *,
    styles_dir: str | Path | None = None,
    clear_existing: bool = False,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "path": str(Path(path).expanduser().resolve()),
        "clear_existing": bool(clear_existing),
    }
    if styles_dir is not None:
        parameters["styles_dir"] = str(Path(styles_dir).expanduser().resolve())
    return _request(session_file, "load_layers", parameters)


def load_qgis_run(session_file: str | Path, run_dir: str | Path) -> dict[str, Any]:
    """Load the unique delivery GeoPackage and matching QML files from a run."""

    directory = Path(run_dir).expanduser().resolve()
    if not directory.is_dir():
        raise QgisSessionError(f"CAD2GIS run directory does not exist: {directory}")
    candidates = [directory / "delivery.gpkg"]
    candidates.extend(sorted(directory.glob("*_delivery.gpkg")))
    delivery = next((path for path in candidates if path.is_file()), None)
    if delivery is None:
        raise QgisSessionError(f"No delivery GeoPackage was found under {directory}")
    style_candidates = (directory / "qgis" / "styles", directory / "styles")
    styles = next((path for path in style_candidates if path.is_dir()), None)
    result = load_qgis_layers(
        session_file,
        delivery,
        styles_dir=styles,
        clear_existing=True,
    )
    return {
        "schema_version": "cad2gis.qgis_run_loaded.v1",
        "run_dir": str(directory),
        "delivery": str(delivery),
        "styles_dir": str(styles) if styles is not None else None,
        "qgis": result,
    }


def set_qgis_layer_visibility(
    session_file: str | Path, layer: str, visible: bool
) -> dict[str, Any]:
    return _request(
        session_file,
        "set_layer_visibility",
        {"layer": str(layer), "visible": bool(visible)},
    )


def zoom_qgis_full_extent(session_file: str | Path) -> dict[str, Any]:
    return _request(session_file, "zoom_full_extent")


def export_qgis_view(
    session_file: str | Path,
    output_path: str | Path,
    *,
    width: int = 1600,
    height: int = 1000,
) -> dict[str, Any]:
    if isinstance(width, bool) or not 320 <= int(width) <= 8192:
        raise QgisSessionError("width must be between 320 and 8192 pixels")
    if isinstance(height, bool) or not 240 <= int(height) <= 8192:
        raise QgisSessionError("height must be between 240 and 8192 pixels")
    return _request(
        session_file,
        "export_view",
        {
            "path": str(Path(output_path).expanduser().resolve()),
            "width": int(width),
            "height": int(height),
        },
        timeout=60.0,
    )


def stop_qgis_session(session_file: str | Path) -> dict[str, Any]:
    return _request(session_file, "shutdown")


__all__ = [
    "DEFAULT_SESSION_FILE",
    "QGIS_EXECUTABLE_ENV",
    "QgisSessionError",
    "discover_qgis_executable",
    "export_qgis_view",
    "inspect_qgis_session",
    "load_qgis_layers",
    "load_qgis_run",
    "open_qgis_project",
    "set_qgis_layer_visibility",
    "start_qgis_session",
    "stop_qgis_session",
    "zoom_qgis_full_extent",
]
