"""Reader capability resolution without silent proprietary fallbacks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .contracts import ReaderCapability, ReaderUnavailableError

READER_ENV = "CAD2GIS_READER_BACKEND"
DEFAULT_READER = "libredwg"
SUPPORTED_READERS = frozenset({"libredwg", "libredwg-cli", "autocad"})


def configured_reader() -> str:
    backend = os.environ.get(READER_ENV, DEFAULT_READER).strip().lower()
    aliases = {"libredwg_cli": "libredwg-cli", "cli": "libredwg-cli"}
    backend = aliases.get(backend, backend)
    if backend not in SUPPORTED_READERS:
        choices = ", ".join(sorted(SUPPORTED_READERS))
        raise ValueError(f"unknown reader backend {backend!r}; expected one of: {choices}")
    return backend


def reader_capabilities() -> dict[str, ReaderCapability]:
    from .libredwg import libredwg_capability
    from .libredwg_cli import libredwg_cli_capability

    bindings = libredwg_capability()
    capabilities = {
        "libredwg-bindings": ReaderCapability(
            backend=bindings.backend,
            available=bindings.available,
            detail=bindings.detail,
            remediation=(
                "No remediation required."
                if bindings.available
                else "Optional binding interface unavailable; run `cad2gis runtime install` "
                "to enable the supported LibreDWG CLI interface."
            ),
        ),
        "libredwg-cli": libredwg_cli_capability(),
    }
    if os.name == "nt":
        try:
            from .autocad import preflight_autocad_reader

            preflight: dict[str, Any] = preflight_autocad_reader()
            available = bool(preflight.get("ok"))
            detail = str(preflight.get("detail") or preflight.get("status") or preflight)
        except Exception as exc:
            available = False
            detail = f"AutoCAD preflight failed ({type(exc).__name__})"
        capabilities["autocad"] = ReaderCapability(
            backend="autocad",
            available=available,
            detail=detail,
            remediation=(
                "No remediation required."
                if available
                else "Install a supported AutoCAD Core Console only if this explicit fallback is required."
            ),
        )
    else:
        capabilities["autocad"] = ReaderCapability(
            backend="autocad",
            available=False,
            detail="AutoCAD Core Console is a Windows-only optional fallback.",
            remediation="Use the portable LibreDWG reader on this platform.",
        )
    return capabilities


def selected_reader_capability() -> ReaderCapability:
    backend = configured_reader()
    capabilities = reader_capabilities()
    if backend == "libredwg":
        bindings = capabilities["libredwg-bindings"]
        if bindings.available:
            return bindings
        return capabilities["libredwg-cli"]
    return capabilities[backend]


def extract_records(source_path: str | Path):
    """Extract with the configured reader.

    ``libredwg`` means the best available LibreDWG interface: native bindings
    first, then the official CLI adapter.  AutoCAD is never selected by this
    fallback and remains an explicit user choice.
    """

    backend = configured_reader()
    if backend == "autocad":
        from .autocad import extract_dwg_records

        return extract_dwg_records(source_path)
    if backend == "libredwg-cli":
        from .libredwg_cli import extract_dwg_records

        return extract_dwg_records(source_path)

    from .libredwg import extract_dwg_records as extract_bindings
    from .libredwg import libredwg_capability

    bindings = libredwg_capability()
    if bindings.available:
        return extract_bindings(source_path)
    from .libredwg_cli import extract_dwg_records as extract_cli
    from .libredwg_cli import libredwg_cli_capability

    cli = libredwg_cli_capability()
    if cli.available:
        return extract_cli(source_path)
    raise ReaderUnavailableError(
        "LibreDWG reader unavailable. "
        f"Bindings: {bindings.detail} CLI: {cli.detail} "
        "Run `cad2gis runtime install`; AutoCAD is an explicit optional fallback, not a requirement."
    )


__all__ = [
    "DEFAULT_READER",
    "READER_ENV",
    "SUPPORTED_READERS",
    "configured_reader",
    "extract_records",
    "reader_capabilities",
    "selected_reader_capability",
]
