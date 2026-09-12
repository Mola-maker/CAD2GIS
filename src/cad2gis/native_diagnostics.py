"""Read-only, executable-bound native reader defect diagnostics.

A known digest is evidence about that executable, not about every build with
the same version label. Unknown builds are not certified fixed by this module.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
from typing import Any, Mapping


KNOWN_CQUOTE_DEFECT_SHA256 = (
    "955f5f2855bc0c5a8bd7a93424ea259ab14405799638b889b2b3c5918c529e3e"
)
KNOWN_CQUOTE_LIBRARY_SHA256 = (
    "cc86e25e755ac225eadc839eaa232c2f2233c7a1b60fcd77b2cd1915761aef12"
)
_MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
_DEFECT_ID = "LIBREDWG_CQUOTE_MTEXT_TAIL"
_REMEDIATION = (
    "Keep original DWGs, source snapshots and baseline hashes unchanged. "
    "Validate a replacement reader against original text and repeated extraction, "
    "then select its executable with CAD2GIS_LIBREDWG_CLI and inspect into a new "
    "output directory. Reinstalling the same executable does not repair this defect; "
    "a different hash alone does not establish that a build is fixed."
)


def _identity(value: Any) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _executable_sha256(path: Path) -> str:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_EXECUTABLE_BYTES:
        raise ValueError("executable is not a bounded regular file")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_EXECUTABLE_BYTES:
                raise ValueError("executable exceeds diagnostic byte budget")
            digest.update(chunk)
    if total != before.st_size or _identity(path.stat()) != _identity(before):
        raise ValueError("executable changed while fingerprinting")
    return digest.hexdigest()


def libredwg_cli_text_fidelity(executable: Path | None) -> dict[str, Any]:
    """Fingerprint the discovered executable without executing it or reading CAD."""
    result: dict[str, Any] = {
        "schema_version": "cad2gis.native_reader_text_fidelity.v1",
        "executable": str(executable) if executable is not None else None,
        "executable_sha256": None,
        "runtime_library": None,
        "runtime_library_sha256": None,
        "assessment_scope": "known_executable_and_adjacent_runtime_library_digests_only",
        "text_fidelity_verified": False,
        "fixed_build_verified": False,
        "defect_id": None,
    }
    if executable is None:
        return {**result, "status": "not_available", "detail": "No LibreDWG CLI executable was discovered."}
    try:
        digest = _executable_sha256(executable)
    except (OSError, ValueError) as exc:
        return {
            **result, "status": "not_evaluated", "reason": "fingerprint_unavailable",
            "detail": f"Executable fingerprint unavailable ({type(exc).__name__}); text fidelity was not evaluated.",
            "remediation": "Check the selected executable path and read access, then rerun diagnostics.",
        }
    result["executable_sha256"] = digest
    if digest == KNOWN_CQUOTE_DEFECT_SHA256:
        # This verified executable imports libredwg-0.dll. The defect resides in
        # that library: replacing only the DLL must not retain a known-bad claim.
        library = executable.with_name("libredwg-0.dll")
        result["runtime_library"] = str(library)
        try:
            result["runtime_library_sha256"] = _executable_sha256(library)
        except (OSError, ValueError):
            pass
        if result["runtime_library_sha256"] != KNOWN_CQUOTE_LIBRARY_SHA256:
            return {
                **result, "status": "not_evaluated", "reason": "runtime_library_not_evaluated",
                "detail": "The executable matches the recorded build, but its adjacent runtime library is missing, unreadable or has a different digest; this combination has not been evaluated.",
                "remediation": "Verify the actual libredwg-0.dll loaded by this executable and retain its build/patch provenance; do not infer a fixed or defective runtime from the executable alone.",
            }
        return {
            **result, "status": "known_defect", "defect_id": _DEFECT_ID,
            "detail": "This exact Windows dwg2dxf executable and adjacent libredwg-0.dll combination has a confirmed cquote MTEXT heap-tail defect; zero exit status does not establish text fidelity.",
            "evidence": {
                "scope": "Windows executable tested on Darat and Manado; native C reproducer",
                "reference": "docs/NATIVE_READER_DIAGNOSTICS_2026-09-11.md",
                "source_archive_sha256": "cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351",
            },
            "remediation": _REMEDIATION,
        }
    return {
        **result, "status": "not_evaluated", "reason": "unknown_executable_digest",
        "detail": "This executable digest has not been evaluated for text fidelity; a different hash or version label is not proof of a fixed build.",
        "remediation": "Retain build and patch provenance; verify source text and repeated extraction before treating this binary as validated.",
    }


def selected_cli_has_known_text_defect(
    selected_backend: str, assessment: Mapping[str, Any] | None,
) -> bool:
    """Keep an unused CLI defect from invalidating bindings or AutoCAD readiness."""
    return (
        selected_backend == "libredwg-cli"
        and isinstance(assessment, Mapping)
        and assessment.get("status") == "known_defect"
        and assessment.get("executable_sha256") == KNOWN_CQUOTE_DEFECT_SHA256
        and assessment.get("runtime_library_sha256") == KNOWN_CQUOTE_LIBRARY_SHA256
    )
