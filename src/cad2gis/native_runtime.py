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
import tarfile
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
LIBREDWG_SOURCE_URL = (
    "https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz"
)
LIBREDWG_SOURCE_SHA256 = (
    "cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351"
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
    return "dwg2dxf.exe" if sys_platform() == "win32" else "dwg2dxf"


def _managed_cli_candidates() -> tuple[Path, ...]:
    destination = managed_libredwg_dir()
    name = _cli_name()
    return destination / name, destination / "bin" / name


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

    for managed in _managed_cli_candidates():
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
            if (
                target != destination_resolved
                and destination_resolved not in target.parents
            ):
                raise RuntimeError(
                    f"Unsafe path in LibreDWG archive: {member.filename}"
                )
        bundle.extractall(destination)


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    """Extract a source archive after rejecting traversal and link entries."""

    destination_resolved = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if (
                target != destination_resolved
                and destination_resolved not in target.parents
            ):
                raise RuntimeError(f"Unsafe path in LibreDWG archive: {member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"Unsafe link in LibreDWG archive: {member.name}")
        try:
            bundle.extractall(destination, filter="fully_trusted")  # noqa: S202 - members validated above
        except TypeError:  # Python versions before extraction filters.
            bundle.extractall(destination)  # noqa: S202 - members validated above


def _download_pinned_asset(url: str, sha256: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "cad2gis-portable-runtime/1"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        digest = hashlib.sha256()
        with destination.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                stream.write(chunk)
    actual = digest.hexdigest()
    if actual != sha256:
        raise RuntimeError(
            f"LibreDWG archive checksum mismatch: expected {sha256}, got {actual}"
        )


def _install_posix_from_source(destination: Path) -> None:
    """Build the official release into the user cache without root access."""

    required = ("make", "cc")
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            "Portable LibreDWG source installation needs a C compiler and make; "
            f"missing: {', '.join(missing)}. Install the platform build tools or "
            "provide CAD2GIS_LIBREDWG_CLI. AutoCAD and Conda are not required."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cad2gis-libredwg-") as temp_name:
        temporary = Path(temp_name)
        archive = temporary / "libredwg.tar.gz"
        _download_pinned_asset(LIBREDWG_SOURCE_URL, LIBREDWG_SOURCE_SHA256, archive)
        extracted = temporary / "extracted"
        extracted.mkdir()
        _safe_extract_tar(archive, extracted)
        source_dirs = [entry for entry in extracted.iterdir() if entry.is_dir()]
        if len(source_dirs) != 1 or not (source_dirs[0] / "configure").is_file():
            raise RuntimeError("LibreDWG source archive has an unexpected layout")

        staging = destination.with_name(f"{destination.name}.staging-{os.getpid()}")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        configure = source_dirs[0] / "configure"
        commands = (
            [
                str(configure),
                f"--prefix={staging}",
                "--disable-bindings",
                "--disable-docs",
                "--disable-shared",
            ],
            ["make", "-j2"],
            ["make", "install"],
        )
        for command in commands:
            process = subprocess.run(
                command,
                cwd=source_dirs[0],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if process.returncode != 0:
                detail = (process.stderr or process.stdout)[-4000:].strip()
                shutil.rmtree(staging, ignore_errors=True)
                raise RuntimeError(
                    "LibreDWG source build failed at "
                    f"`{' '.join(command[:2])}` (exit={process.returncode}): {detail}"
                )
        if not (staging / "bin" / "dwg2dxf").is_file():
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError("LibreDWG source build did not produce bin/dwg2dxf")
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)

    receipt = {
        "schema_version": "cad2gis.portable_runtime_receipt.v1",
        "component": "libredwg-cli",
        "version": LIBREDWG_VERSION,
        "source_url": LIBREDWG_SOURCE_URL,
        "sha256": LIBREDWG_SOURCE_SHA256,
        "install_method": "source-build",
    }
    (destination / "cad2gis-runtime.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


def install_portable_runtime() -> dict[str, Any]:
    """Install the pinned official LibreDWG CLI into the user cache.

    Windows uses the checksum-pinned official release archive. macOS and Linux
    prefer Homebrew when it is already installed, then fall back to a rootless
    build of the checksum-pinned official source release. No Conda environment
    or AutoCAD installation is involved.
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
        if brew is not None:
            process = subprocess.run(
                [brew, "install", "libredwg"],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            executable, installed_source = discover_libredwg_cli()
            if process.returncode == 0 and executable is not None:
                return {
                    **portable_runtime_status(),
                    "install_status": "installed",
                    "libredwg_source": installed_source,
                }
        destination = managed_libredwg_dir()
        _install_posix_from_source(destination)
        executable, installed_source = discover_libredwg_cli()
        if executable is None:
            raise RuntimeError(
                "Portable LibreDWG installation completed without dwg2dxf"
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
        _download_pinned_asset(
            LIBREDWG_WINDOWS_X64_URL,
            LIBREDWG_WINDOWS_X64_SHA256,
            archive,
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
