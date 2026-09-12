from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad2gis import doctor, native_diagnostics, native_runtime
from cad2gis.reader.contracts import ReaderCapability


@pytest.fixture
def affected_executable(tmp_path, monkeypatch):
    executable = tmp_path / "dwg2dxf.exe"
    executable.write_bytes(b"known-defect-fixture")
    # Exercise actual file hashing without redistributing a native executable.
    monkeypatch.setattr(native_diagnostics, "KNOWN_CQUOTE_DEFECT_SHA256",
                        hashlib.sha256(executable.read_bytes()).hexdigest())
    library = executable.with_name("libredwg-0.dll")
    library.write_bytes(b"known-defect-library-fixture")
    monkeypatch.setattr(native_diagnostics, "KNOWN_CQUOTE_LIBRARY_SHA256",
                        hashlib.sha256(library.read_bytes()).hexdigest())
    return executable


def test_known_executable_is_identified_by_bytes_and_never_executed(affected_executable, monkeypatch):
    def unexpected_execution(*args, **kwargs):
        pytest.fail("read-only diagnostics must not execute the binary")

    monkeypatch.setattr(native_runtime.subprocess, "run", unexpected_execution)
    report = native_diagnostics.libredwg_cli_text_fidelity(affected_executable)
    assert report["status"] == "known_defect"
    assert report["defect_id"] == "LIBREDWG_CQUOTE_MTEXT_TAIL"
    assert report["executable_sha256"] == hashlib.sha256(affected_executable.read_bytes()).hexdigest()
    assert report["runtime_library_sha256"] == hashlib.sha256(affected_executable.with_name("libredwg-0.dll").read_bytes()).hexdigest()
    assert report["text_fidelity_verified"] is False
    assert report["fixed_build_verified"] is False
    assert "baseline hashes unchanged" in report["remediation"]


def test_replacing_same_named_same_version_file_is_not_certified_fixed(affected_executable):
    first = native_diagnostics.libredwg_cli_text_fidelity(affected_executable)
    affected_executable.write_bytes(b"LibreDWG 0.14 replacement binary")
    second = native_diagnostics.libredwg_cli_text_fidelity(affected_executable)
    assert first["status"] == "known_defect"
    assert second["status"] == "not_evaluated"
    assert second["reason"] == "unknown_executable_digest"
    assert second["executable_sha256"] != first["executable_sha256"]
    assert second["defect_id"] is None
    assert second["fixed_build_verified"] is False


@pytest.mark.parametrize("library_bytes", [None, b"replacement library not evaluated"])
def test_same_executable_with_missing_or_replaced_library_is_not_assumed_defective(
    affected_executable, library_bytes,
):
    library = affected_executable.with_name("libredwg-0.dll")
    if library_bytes is None:
        library.unlink()
    else:
        library.write_bytes(library_bytes)
    report = native_diagnostics.libredwg_cli_text_fidelity(affected_executable)
    assert report["status"] == "not_evaluated"
    assert report["reason"] == "runtime_library_not_evaluated"
    assert report["defect_id"] is None
    assert report["fixed_build_verified"] is False
    assert not native_diagnostics.selected_cli_has_known_text_defect("libredwg-cli", report)


@pytest.mark.parametrize("name", ["dwg2dxf.exe", "dwg2dxf"])
def test_unknown_windows_or_linux_named_executable_remains_not_evaluated(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"unverified LibreDWG 0.14")
    report = native_diagnostics.libredwg_cli_text_fidelity(path)
    assert report["status"] == "not_evaluated"
    assert report["reason"] == "unknown_executable_digest"
    assert report["executable_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["text_fidelity_verified"] is False
    assert report["fixed_build_verified"] is False


def test_missing_unreadable_and_oversized_fingerprints_are_bounded(tmp_path, monkeypatch):
    assert native_diagnostics.libredwg_cli_text_fidelity(None)["status"] == "not_available"
    report = native_diagnostics.libredwg_cli_text_fidelity(tmp_path / "missing.exe")
    assert report["status"] == "not_evaluated" and report["executable_sha256"] is None
    assert report["reason"] == "fingerprint_unavailable"

    path = tmp_path / "reader.exe"
    path.write_bytes(b"exceeds test budget")
    monkeypatch.setattr(native_diagnostics, "_MAX_EXECUTABLE_BYTES", 2)
    assert native_diagnostics.libredwg_cli_text_fidelity(path)["reason"] == "fingerprint_unavailable"
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("private-detail")))
    monkeypatch.setattr(native_diagnostics, "_MAX_EXECUTABLE_BYTES", 128 * 1024 * 1024)
    report = native_diagnostics.libredwg_cli_text_fidelity(path)
    assert report["reason"] == "fingerprint_unavailable"
    assert "private-detail" not in report["detail"]


def test_executable_changed_during_fingerprinting_is_not_evaluated(tmp_path, monkeypatch):
    executable = tmp_path / "changing.exe"
    executable.write_bytes(b"changing reader")
    original = executable.stat()
    calls = 0

    def changed_stat(self):
        nonlocal calls
        calls += 1
        return SimpleNamespace(st_mode=original.st_mode, st_dev=original.st_dev,
                               st_ino=original.st_ino, st_size=original.st_size,
                               st_mtime_ns=original.st_mtime_ns + (calls > 1))

    monkeypatch.setattr(Path, "stat", changed_stat)
    report = native_diagnostics.libredwg_cli_text_fidelity(executable)
    assert report["status"] == "not_evaluated"
    assert report["reason"] == "fingerprint_unavailable"
    assert report["executable_sha256"] is None


@pytest.mark.parametrize("selected_backend,limited", [
    ("libredwg-cli", True), ("libredwg", False), ("autocad", False),
])
def test_doctor_only_limits_the_actually_selected_defective_cli(
    affected_executable, monkeypatch, selected_backend, limited,
):
    assessment = native_diagnostics.libredwg_cli_text_fidelity(affected_executable)
    check = doctor._native_text_check(selected_backend, assessment)
    assert check.required_for_conversion is limited
    assert check.status == "warning"
    monkeypatch.setattr(doctor, "collect_checks", lambda **kwargs: (check,))
    monkeypatch.setattr(doctor, "portable_runtime_status", lambda: {})
    report = doctor.build_report(profile="full")
    assert report["conversion_ready"] is (not limited)
    assert report["status"] == ("limited" if limited else "ready")
    assert "known_defect" in doctor.format_report(report)


@pytest.mark.parametrize("selected_backend,known", [
    ("libredwg-cli", True), ("libredwg", True), ("autocad", True),
    ("libredwg-cli", False),
])
def test_mcp_runtime_reports_fingerprint_and_selection_sensitive_readiness(
    affected_executable, monkeypatch, selected_backend, known,
):
    from cad2gis import agent_mcp, mcp_diagnostics
    from cad2gis.reader import resolver

    if not known:
        affected_executable.write_bytes(b"untested replacement executable")
    monkeypatch.setattr(native_runtime, "discover_libredwg_cli", lambda: (affected_executable, "environment"))
    monkeypatch.setattr(native_runtime, "ensure_osgeo_runtime", lambda: {"available": True})
    # 'libredwg' configuration can resolve either bindings or the CLI fallback.
    monkeypatch.setattr(resolver, "configured_reader", lambda: "libredwg" if selected_backend != "autocad" else "autocad")
    capability = ReaderCapability(selected_backend, True, "available", "none")
    monkeypatch.setattr(resolver, "selected_reader_capability", lambda: capability)
    monkeypatch.setattr(resolver, "reader_capabilities", lambda: {selected_backend: capability})
    report = agent_mcp.get_runtime_status()
    assert report["conversion_ready"] is (not (known and selected_backend == "libredwg-cli"))
    assert report["libredwg"]["available"] is True
    assert report["libredwg"]["text_fidelity"]["status"] == ("known_defect" if known else "not_evaluated")
    assert report["libredwg"]["text_fidelity"]["fixed_build_verified"] is False
    # Existing debug_mcp runtime scope carries the same structured assessment.
    debug = mcp_diagnostics.diagnose(scope="runtime", tool_schemas=[])
    assert debug["runtime"]["libredwg"]["text_fidelity"] == report["libredwg"]["text_fidelity"]


def test_runtime_without_new_assessment_still_accepts_legacy_test_fixtures(monkeypatch):
    from cad2gis import agent_mcp
    from cad2gis.reader import resolver

    monkeypatch.setattr(native_runtime, "portable_runtime_status", lambda: {"gdal": {"available": True}})
    monkeypatch.setattr(resolver, "configured_reader", lambda: "libredwg")
    monkeypatch.setattr(resolver, "reader_capabilities", lambda: {})
    monkeypatch.setattr(resolver, "selected_reader_capability", lambda: ReaderCapability("libredwg-cli", True, "fixture", "none"))
    assert agent_mcp.get_runtime_status()["conversion_ready"] is True
