"""Portable native-runtime discovery and installation helpers.

The agent control plane must stay importable before optional CAD/GIS native
components are present.  This module centralizes two portable bridges:

* the official LibreDWG command-line release used by the DWG reader; and
* ``pyramids-gis`` wheels, which expose a bundled ``osgeo`` runtime after the
  package initializes its private native-library path. PyPI no longer serves
  the pinned platform wheels (only the sdist, whose build omits the vendored
  runtime), so the wheel is provisioned into the managed cache from the
  upstream GitHub release instead; see ``install_pyramids_wheel``.

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

PYRAMIDS_VERSION = "0.58.1"
PYRAMIDS_WHEEL_BASE_URL = (
    "https://github.com/serapeum-org/pyramids/releases/download/"
    f"{PYRAMIDS_VERSION}/"
)
# The vendored ``osgeo`` runtime ships only inside the pyramids-gis platform
# wheels (``pyramids/_vendor/``). PyPI now serves only the 0.58.1 sdist, whose
# build omits that directory, so the wheels are fetched from the upstream
# GitHub release instead and pinned here by (python tag, platform tag), the
# same checksum discipline as the LibreDWG assets above. Wheel filenames are
# derived from the tags; Python tags outside cp311/cp312 are unsupported
# because the project requires >=3.11,<3.13.
PYRAMIDS_WHEEL_SHA256 = {
    ("cp311", "macosx_11_0_arm64"): (
        "abc8232dc9c486b5a72497e01052a174bdba82423b5e8430cffa9ff3aeecafcc"
    ),
    ("cp311", "macosx_11_0_x86_64"): (
        "0b1aa14cefc1ec37d0628903f612cf6fb1dd12ea09d9e6da7c84da03ed2b2adf"
    ),
    ("cp311", "manylinux_2_28_aarch64"): (
        "8fce945fc57340e562122c8497e1d50e6093546a2fef6203c4fc512398502820"
    ),
    ("cp311", "manylinux_2_28_x86_64"): (
        "2bf310b05b10bc3b011da9d9ce38fcc805e155bfdad472bd2e4a2ee41a19bdf1"
    ),
    ("cp311", "win_amd64"): (
        "d64682753b5d69ddec5ba5be993ec9b55ceda8728cf1de705b4daca448ab0e72"
    ),
    ("cp312", "macosx_11_0_arm64"): (
        "f8beb529b17c4b4f07cb1735b94963fdc339b0e63982108c5ebecb3b02b91b5f"
    ),
    ("cp312", "macosx_11_0_x86_64"): (
        "d76525f9be85c93b09ac88ebbc24bf67d7ac7b536006faf4da3177f1b754b00c"
    ),
    ("cp312", "manylinux_2_28_aarch64"): (
        "a5965704f387e95526853c7ebbe5000f4361f7acfaf8b2e7c91b18aefb65b0b0"
    ),
    ("cp312", "manylinux_2_28_x86_64"): (
        "f3a38f6a95d208844ddaaff2fe63f96c4cc23dff542b9735e8e9cd6690005f8a"
    ),
    ("cp312", "win_amd64"): (
        "c95feb0d2f2ce68cc3eb010371fa8ea8a8f786d2abcd0db5e02143c8467a63a0"
    ),
    ("cp312", "win_arm64"): (
        "3c326aadc6c6d1487e7ffa1d5ceb48af73660053561896d22596ee3899991cd2"
    ),
}


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


def _python_tag() -> str:
    """Small seam for deterministic platform tests."""

    import sys

    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def managed_pyramids_wheel_dir() -> Path:
    return _user_cache_root() / "runtime" / f"pyramids-gis-{PYRAMIDS_VERSION}-wheel"


def _pyramids_wheel_asset() -> tuple[str, str] | None:
    """Return the pinned wheel (filename, sha256) for this interpreter/platform."""

    machine = platform.machine().casefold()
    system = sys_platform()
    if system == "linux":
        platform_tag = {
            "x86_64": "manylinux_2_28_x86_64",
            "aarch64": "manylinux_2_28_aarch64",
            "arm64": "manylinux_2_28_aarch64",
        }.get(machine)
    elif system == "darwin":
        platform_tag = {
            "x86_64": "macosx_11_0_x86_64",
            "arm64": "macosx_11_0_arm64",
        }.get(machine)
    elif system == "win32":
        platform_tag = {
            "amd64": "win_amd64",
            "x86_64": "win_amd64",
            "arm64": "win_arm64",
        }.get(machine)
    else:
        platform_tag = None
    if platform_tag is None:
        return None
    python_tag = _python_tag()
    sha256 = PYRAMIDS_WHEEL_SHA256.get((python_tag, platform_tag))
    if sha256 is None:
        return None
    filename = (
        f"pyramids_gis-{PYRAMIDS_VERSION}-{python_tag}-{python_tag}-"
        f"{platform_tag}.whl"
    )
    return filename, sha256


def _pyramids_wheel_site() -> Path | None:
    """The extracted wheel tree, when it carries the vendored osgeo runtime."""

    destination = managed_pyramids_wheel_dir()
    if (destination / "pyramids" / "_vendor" / "osgeo").is_dir():
        return destination
    return None


def _activate_managed_pyramids_wheel() -> bool:
    """Put the managed wheel's package tree ahead of site-packages.

    The wheel build of ``pyramids`` is the same pinned version as the
    installed one; only the wheel carries ``pyramids/_vendor``. Activating it
    before ``import pyramids`` lets the wheel's own bootstrap expose the
    vendored ``osgeo`` runtime exactly as upstream intended.
    """

    import sys

    site = _pyramids_wheel_site()
    if site is None:
        return False
    site_text = str(site)
    if site_text not in sys.path:
        sys.path.insert(0, site_text)
    return True


def pyramids_wheel_status() -> dict[str, Any]:
    import sys

    asset = _pyramids_wheel_asset()
    site = _pyramids_wheel_site()
    return {
        "version": PYRAMIDS_VERSION,
        "asset": asset[0] if asset is not None else None,
        "managed_dir": str(managed_pyramids_wheel_dir()),
        "extracted": site is not None,
        "active": site is not None and str(site) in sys.path,
    }


def install_pyramids_wheel() -> dict[str, Any]:
    """Install the checksum-pinned pyramids-gis platform wheel into the cache.

    The wheel is extracted under the managed cache (never written into
    site-packages) and activated per-process by ``ensure_osgeo_runtime``.
    """

    if _pyramids_wheel_site() is not None:
        return {**pyramids_wheel_status(), "install_status": "already_available"}

    asset = _pyramids_wheel_asset()
    if asset is None:
        return {**pyramids_wheel_status(), "install_status": "unsupported_platform"}
    filename, sha256 = asset
    url = PYRAMIDS_WHEEL_BASE_URL + filename

    destination = managed_pyramids_wheel_dir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cad2gis-pyramids-") as temp_name:
        temporary = Path(temp_name)
        archive = temporary / filename
        _download_pinned_asset(url, sha256, archive, label="pyramids-gis wheel")
        extracted = temporary / "extracted"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        if not (extracted / "pyramids" / "_vendor" / "osgeo").is_dir():
            raise RuntimeError(
                "pyramids-gis wheel does not contain the vendored osgeo runtime"
            )
        staging = destination.with_name(f"{destination.name}.staging-{os.getpid()}")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(extracted, staging)
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)

    receipt = {
        "schema_version": "cad2gis.portable_runtime_receipt.v1",
        "component": "pyramids-gis-wheel",
        "version": PYRAMIDS_VERSION,
        "source_url": url,
        "sha256": sha256,
        "install_method": "wheel-extract",
    }
    (destination / "cad2gis-runtime.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return {**pyramids_wheel_status(), "install_status": "installed"}


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


def _configure_pyramids_windows_paths() -> None:
    """Pass existing data paths through GDAL's UTF-8 API on Windows.

    Pyramids initializes these environment variables with Unicode paths, but
    GDAL's Windows getenv bridge reads the ANSI bytes as UTF-8.  A non-ASCII
    installation directory then hides an existing proj.db from GDAL.  The
    setter preserves the environment's path values and precedence; explicit
    GDAL configuration must retain precedence over the environment as well.
    """

    if sys_platform() != "win32":
        return
    from osgeo import gdal

    configured = gdal.GetConfigOptions()
    for key in ("GDAL_DATA", "PROJ_DATA", "PROJ_LIB"):
        value = os.environ.get(key)
        if value is not None and key not in configured:
            gdal.SetConfigOption(key, value)


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
            if provider == "pyramids-gis":
                _configure_pyramids_windows_paths()
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

    # An sdist-installed pyramids has no vendored runtime to initialize; if
    # the managed platform wheel was provisioned into the cache, activate it
    # first so the import below loads the wheel build of the same version.
    _activate_managed_pyramids_wheel()

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
    if available:
        _configure_pyramids_windows_paths()
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
    from .native_diagnostics import libredwg_cli_text_fidelity

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
            "text_fidelity": libredwg_cli_text_fidelity(executable),
        },
        "gdal": ensure_osgeo_runtime(),
        "pyramids_wheel": pyramids_wheel_status(),
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
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f"Unsafe special entry in LibreDWG archive: {member.name}")
        try:
            bundle.extractall(destination, filter="data")
        except TypeError:  # Python versions before extraction filters.
            bundle.extractall(destination)  # noqa: S202 - members validated above
        # Some extraction environments intentionally omit archive mode bits.
        # Restore only ordinary executable/read permissions, never ownership,
        # setuid/setgid or world-write flags, for this checksum-pinned source.
        for member in bundle.getmembers():
            target = destination / member.name
            target.chmod(0o755 if member.isdir() or member.mode & 0o111 else 0o644)


def _download_pinned_asset(
    url: str, sha256: str, destination: Path, *, label: str = "LibreDWG"
) -> None:
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
            f"{label} archive checksum mismatch: expected {sha256}, got {actual}"
        )


def _install_posix_from_source(destination: Path) -> None:
    """Build the official release into the user cache without root access."""

    required = ("make", "cc", "pkg-config")
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            "Portable LibreDWG source installation needs a C compiler, make and pkg-config; "
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


def _install_libredwg_runtime() -> dict[str, Any]:
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


def install_portable_runtime() -> dict[str, Any]:
    """Install the full checksum-pinned portable runtime into the user cache.

    Two components: the official LibreDWG CLI (``_install_libredwg_runtime``)
    and the pyramids-gis platform wheel carrying the vendored osgeo (GDAL)
    runtime (``install_pyramids_wheel``).
    """

    result = _install_libredwg_runtime()
    result["pyramids_wheel"] = install_pyramids_wheel()
    return result


__all__ = [
    "LIBREDWG_CLI_ENV",
    "LIBREDWG_VERSION",
    "PYRAMIDS_VERSION",
    "discover_libredwg_cli",
    "ensure_osgeo_runtime",
    "install_portable_runtime",
    "install_pyramids_wheel",
    "managed_libredwg_dir",
    "managed_pyramids_wheel_dir",
    "portable_runtime_status",
    "pyramids_wheel_status",
]
