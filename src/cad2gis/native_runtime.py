"""Portable native-runtime discovery and installation helpers.

The agent control plane must stay importable before optional CAD/GIS native
components are present.  This module centralizes two portable bridges:

* the official LibreDWG command-line release used by the DWG reader; and
* ``pyramids-gis`` wheels, which expose a bundled ``osgeo`` runtime after the
  package initializes its private native-library path.

No machine-specific project directory is stored here.  Managed binaries live
under the platform user cache and every downloaded asset is checksum-pinned.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

LIBREDWG_CLI_ENV = "CAD2GIS_LIBREDWG_CLI"
LIBREDWG_VERSION = "0.14"
LIBREDWG_WINDOWS_X64_URL = (
    "https://github.com/LibreDWG/libredwg/releases/download/0.14/"
    "libredwg-0.14-win64.zip"
)
LIBREDWG_WINDOWS_X64_SHA256 = (
    "1ad7e15344d20b3426c3435b078d82fb84b35062815946b2cca9c5fc9810fea8"
)


def _user_cache_root() -> Path:
    override = os.environ.get("CAD2GIS_CACHE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return (Path(base) / "cad2gis").resolve()
    if sys_platform() == "darwin":
        return (Path.home() / "Library" / "Caches" / "cad2gis").resolve()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return (base / "cad2gis").resolve()


def sys_platform() -> str:
    """Small seam for deterministic platform tests."""

    import sys

    return sys.platform


def managed_libredwg_dir() -> Path:
    return _user_cache_root() / "runtime" / f"libredwg-{LIBREDWG_VERSION}"


def _cli_name() -> str:
    return "dwg2dxf.exe" if os.name == "nt" else "dwg2dxf"


def discover_libredwg_cli() -> tuple[Path | None, str]:
    """Find ``dwg2dxf`` without relying on a repository or project path."""

    configured = os.environ.get(LIBREDWG_CLI_ENV, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            candidate = candidate / _cli_name()
        if candidate.is_file():
            return candidate.resolve(), "environment"
        return None, f"{LIBREDWG_CLI_ENV} points to a missing executable: {candidate}"

    executable = shutil.which("dwg2dxf.exe") or shutil.which("dwg2dxf")
    if executable:
        return Path(executable).resolve(), "PATH"

    managed = managed_libredwg_dir() / _cli_name()
    if managed.is_file():
        return managed.resolve(), "managed_cache"
    return None, "not_found"


def ensure_osgeo_runtime() -> dict[str, Any]:
    """Expose an installed OSGeo runtime, including a bundled wheel runtime.

    ``pyramids-gis`` deliberately vendors its GDAL bindings below a private
    package directory.  Importing it once adds that directory to ``sys.path``.
    Native backend imports can then continue to use the established
    ``from osgeo import ...`` API without Conda.
    """

    try:
        spec = importlib.util.find_spec("osgeo")
        if spec is not None:
            origin = str(spec.origin or "")
            provider = "pyramids-gis" if "pyramids" in origin.casefold() else "system"
            return {
                "available": True,
                "provider": provider,
                "detail": (
                    "pyramids-gis bundled GDAL runtime is active"
                    if provider == "pyramids-gis"
                    else "osgeo is importable"
                ),
            }
    except (ImportError, AttributeError, ValueError):
        pass

    try:
        import pyramids  # noqa: F401 - initializes the bundled native runtime
    except (ImportError, OSError) as exc:
        return {
            "available": False,
            "provider": "missing",
            "detail": f"no importable osgeo runtime ({type(exc).__name__})",
        }

    try:
        available = importlib.util.find_spec("osgeo") is not None
    except (ImportError, AttributeError, ValueError):
        available = False
    return {
        "available": available,
        "provider": "pyramids-gis" if available else "missing",
        "detail": (
            "pyramids-gis bundled GDAL runtime is active"
            if available
            else "pyramids-gis did not expose osgeo"
        ),
    }


def portable_runtime_status() -> dict[str, Any]:
    executable, source = discover_libredwg_cli()
    return {
        "schema_version": "cad2gis.portable_runtime.v1",
        "platform": platform.system(),
        "machine": platform.machine(),
        "cache_root": str(_user_cache_root()),
        "libredwg": {
            "version": LIBREDWG_VERSION,
            "available": executable is not None,
            "executable": str(executable) if executable is not None else None,
            "source": source,
        },
        "gdal": ensure_osgeo_runtime(),
        "autocad_required": False,
        "conda_required": False,
    }


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if target != destination_resolved and destination_resolved not in target.parents:
                raise RuntimeError(f"Unsafe path in LibreDWG archive: {member.filename}")
        bundle.extractall(destination)


def install_portable_runtime() -> dict[str, Any]:
    """Install the pinned official LibreDWG CLI into the user cache.

    Windows uses the checksum-pinned official release archive. macOS and Linux
    use Homebrew's signed/bottled LibreDWG formula when Homebrew is available.
    No Conda environment or AutoCAD installation is involved.
    """

    existing, source = discover_libredwg_cli()
    if existing is not None:
        return {
            **portable_runtime_status(),
            "install_status": "already_available",
            "libredwg_source": source,
        }

    if sys_platform() != "win32":
        brew = shutil.which("brew")
        if brew is None:
            raise RuntimeError(
                "Automatic LibreDWG installation on macOS/Linux requires Homebrew. "
                "Install `libredwg` with the platform package manager (for example "
                "`brew install libredwg`) so dwg2dxf is on PATH; AutoCAD and Conda "
                "are not required."
            )
        process = subprocess.run(
            [brew, "install", "libredwg"],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        executable, installed_source = discover_libredwg_cli()
        if process.returncode != 0 or executable is None:
            detail = (process.stderr or process.stdout)[-2000:].strip()
            raise RuntimeError(
                "Homebrew could not install an importable LibreDWG CLI "
                f"(exit={process.returncode}): {detail}"
            )
        return {
            **portable_runtime_status(),
            "install_status": "installed",
            "libredwg_source": installed_source,
        }

    if platform.machine().casefold() not in {
        "amd64",
        "x86_64",
    }:
        raise RuntimeError(
            "Automatic LibreDWG installation currently supports Windows x64. "
            "Install the official dwg2dxf package on PATH for this architecture; "
            "AutoCAD and Conda are not required."
        )

    destination = managed_libredwg_dir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cad2gis-libredwg-") as temp_name:
        temporary = Path(temp_name)
        archive = temporary / "libredwg.zip"
        request = urllib.request.Request(
            LIBREDWG_WINDOWS_X64_URL,
            headers={"User-Agent": "cad2gis-portable-runtime/1"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            digest = hashlib.sha256()
            with archive.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    stream.write(chunk)
        actual = digest.hexdigest()
        if actual != LIBREDWG_WINDOWS_X64_SHA256:
            raise RuntimeError(
                "LibreDWG archive checksum mismatch: "
                f"expected {LIBREDWG_WINDOWS_X64_SHA256}, got {actual}"
            )
        extracted = temporary / "extracted"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        if not (extracted / "dwg2dxf.exe").is_file():
            raise RuntimeError("LibreDWG archive does not contain dwg2dxf.exe")
        staging = destination.with_name(f"{destination.name}.staging-{os.getpid()}")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(extracted, staging)
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)

    receipt = {
        "schema_version": "cad2gis.portable_runtime_receipt.v1",
        "component": "libredwg-cli",
        "version": LIBREDWG_VERSION,
        "source_url": LIBREDWG_WINDOWS_X64_URL,
        "sha256": LIBREDWG_WINDOWS_X64_SHA256,
    }
    (destination / "cad2gis-runtime.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return {**portable_runtime_status(), "install_status": "installed"}


__all__ = [
    "LIBREDWG_CLI_ENV",
    "LIBREDWG_VERSION",
    "discover_libredwg_cli",
    "ensure_osgeo_runtime",
    "install_portable_runtime",
    "managed_libredwg_dir",
    "portable_runtime_status",
]
