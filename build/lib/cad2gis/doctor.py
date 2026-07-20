"""Structured dependency diagnostics for the canonical package."""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runtime import (
    BACKEND_PATH_ENV,
    backend_contract,
    load_backend_module,
)

ACCORECONSOLE_ENV = "CAD2GIS_ACCORECONSOLE"
ACCORECONSOLE_TIMEOUT_ENV = "CAD2GIS_ACCORECONSOLE_TIMEOUT"
_AUTOCAD_DIRECTORY = re.compile(r"(?i)^AutoCAD\s+(\d{4})$")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    required_for_conversion: bool = True
    remediation: str | None = None


def _module_check(name: str, label: str, *, deep: bool) -> Check:
    try:
        present = importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        present = False
    if not present:
        return Check(
            name=name,
            status="missing",
            detail=f"{label} is not installed",
            remediation="Create/update the pinned Conda environment from env/environment.yml.",
        )
    if not deep:
        return Check(name=name, status="ok", detail=f"{label} is discoverable")
    try:
        module = importlib.import_module(name)
    except (ImportError, OSError) as exc:
        return Check(
            name=name,
            status="error",
            detail=f"{label} cannot be imported: {exc}",
            remediation="Repair the pinned Conda environment and native library search path.",
        )
    version = getattr(module, "__version__", None)
    detail = f"{label} imports successfully"
    if isinstance(version, str) and version:
        detail += f" ({version})"
    return Check(name=name, status="ok", detail=detail)


def _autocad_version(path: Path) -> int:
    match = _AUTOCAD_DIRECTORY.fullmatch(path.parent.name)
    return int(match.group(1)) if match else -1


def _discover_accoreconsole() -> tuple[Path | None, str]:
    configured = os.environ.get(ACCORECONSOLE_ENV)
    if configured is not None:
        if not configured.strip():
            return None, f"{ACCORECONSOLE_ENV} is empty"
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve(), "environment"
        return None, f"{ACCORECONSOLE_ENV} points to a missing file: {candidate}"

    roots: list[Path] = []
    for name in ("ProgramW6432", "ProgramFiles", "PROGRAMFILES", "ProgramFiles(x86)"):
        value = os.environ.get(name)
        if value and value.strip():
            roots.append(Path(value) / "Autodesk")
    # A conventional root is harmless to inspect and avoids locking discovery
    # to a particular AutoCAD release.
    roots.append(Path("C:/Program Files/Autodesk"))

    candidates: dict[str, Path] = {}
    for root in roots:
        try:
            for candidate in root.glob("AutoCAD */accoreconsole.exe"):
                if candidate.is_file():
                    candidates[str(candidate.resolve()).casefold()] = candidate.resolve()
        except OSError:
            continue
    if candidates:
        selected = max(candidates.values(), key=lambda path: (_autocad_version(path), str(path)))
        return selected, "version_discovery"
    executable = shutil.which("accoreconsole.exe") or shutil.which("accoreconsole")
    if executable:
        return Path(executable).resolve(), "PATH"
    return None, "not_found"


def _timeout_check() -> Check:
    raw = os.environ.get(ACCORECONSOLE_TIMEOUT_ENV)
    if raw is None:
        return Check(
            name="autocad_timeout",
            status="ok",
            detail="reader default timeout is active",
            required_for_conversion=False,
        )
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        return Check(
            name="autocad_timeout",
            status="error",
            detail=f"{ACCORECONSOLE_TIMEOUT_ENV} must be a positive finite number",
            required_for_conversion=False,
            remediation=f"Unset {ACCORECONSOLE_TIMEOUT_ENV} or set a positive number of seconds.",
        )
    return Check(
        name="autocad_timeout",
        status="ok",
        detail=f"{value:g} seconds from {ACCORECONSOLE_TIMEOUT_ENV}",
        required_for_conversion=False,
    )


def collect_checks(
    *, deep: bool = False, _contract: dict[str, Any] | None = None
) -> tuple[Check, ...]:
    """Collect diagnostics; native modules are imported only with ``deep``."""

    version = sys.version_info
    target_python = version.major == 3 and version.minor == 12
    supported_public_api = version.major == 3 and version.minor >= 11
    checks: list[Check] = [
        Check(
            name="python",
            status="ok" if target_python else ("warning" if supported_public_api else "error"),
            detail=f"{platform.python_version()} (pinned GIS runtime is Python 3.12)",
            required_for_conversion=True,
            remediation=(
                None
                if target_python
                else "Use the Conda environment defined by env/environment.yml for conversion."
            ),
        )
    ]

    contract = dict(backend_contract()) if _contract is None else _contract
    location = contract["location"]
    checks.append(
        Check(
            name="backend",
            status="ok" if location else "missing",
            detail=(
                f"{contract['selected_mode']}: {location}"
                if location
                else "no supported cad2gis_v3 backend deployment was found"
            ),
            remediation=(
                None
                if location
                else f"Install cad2gis_v3 or set {BACKEND_PATH_ENV} to its parent directory."
            ),
        )
    )
    if deep and location:
        try:
            load_backend_module("cad2gis_v3.pipeline")
        except Exception as exc:  # A diagnostic must report, not crash.
            checks.append(
                Check(
                    name="backend_import",
                    status="error",
                    detail=str(exc),
                    remediation="Repair backend dependencies, then rerun `cad2gis doctor --deep`.",
                )
            )
        else:
            checks.append(
                Check(
                    name="backend_import",
                    status="ok",
                    detail="cad2gis_v3.pipeline imports successfully",
                )
            )

    for module_name, label in (
        ("osgeo", "GDAL/OGR Python bindings"),
        ("pyproj", "PROJ Python bindings"),
        ("ezdxf", "ezdxf"),
    ):
        checks.append(_module_check(module_name, label, deep=deep))

    if platform.system() == "Windows":
        executable, source = _discover_accoreconsole()
        checks.append(
            Check(
                name="autocad",
                status="ok" if executable else "warning",
                detail=(
                    f"{executable} ({source})"
                    if executable
                    else f"AutoCAD Core Console was not found ({source})"
                ),
                required_for_conversion=False,
                remediation=(
                    None
                    if executable
                    else f"Install AutoCAD or set {ACCORECONSOLE_ENV} to accoreconsole.exe."
                ),
            )
        )
        checks.append(_timeout_check())
    return tuple(checks)


def build_report(*, deep: bool = False) -> dict[str, Any]:
    contract = dict(backend_contract())
    checks = collect_checks(deep=deep, _contract=contract)
    base_ready = all(
        check.status == "ok" for check in checks if check.required_for_conversion
    )
    autocad = next((check for check in checks if check.name == "autocad"), None)
    timeout = next((check for check in checks if check.name == "autocad_timeout"), None)
    dwg_ready = base_ready and (
        autocad is None
        or (autocad.status == "ok" and (timeout is None or timeout.status == "ok"))
    )
    return {
        "schema_version": "cad2gis.doctor.v1",
        "status": "ready" if base_ready else "limited",
        "conversion_ready": base_ready,
        "capabilities": {
            "cli": True,
            "configured_conversion": base_ready,
            "dwg_ingest": dwg_ready,
        },
        "deep_import_check": deep,
        "backend_contract": contract,
        "checks": [asdict(check) for check in checks],
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"CAD2GIS doctor: {report['status']}",
        f"configured conversion ready: {'yes' if report['conversion_ready'] else 'no'}",
        f"DWG ingest ready: {'yes' if report['capabilities']['dwg_ingest'] else 'no'}",
    ]
    for check in report["checks"]:
        lines.append(f"[{check['status']}] {check['name']}: {check['detail']}")
        if check.get("remediation") and check["status"] != "ok":
            lines.append(f"  next: {check['remediation']}")
    return "\n".join(lines)


def render_report(
    *, as_json: bool = False, deep: bool = False
) -> tuple[dict[str, Any], str]:
    report = build_report(deep=deep)
    if as_json:
        return report, json.dumps(report, ensure_ascii=False, indent=2)
    return report, format_report(report)
