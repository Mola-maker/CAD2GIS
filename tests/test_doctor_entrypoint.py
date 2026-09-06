from __future__ import annotations

import os
from pathlib import Path

import pytest

from cad2gis import doctor


def _entrypoint(directory: Path) -> Path:
    directory.mkdir()
    name = "cad2gis-agent-mcp.exe" if os.name == "nt" else "cad2gis-agent-mcp"
    entrypoint = directory / name
    entrypoint.write_bytes(b"test launcher; must not be executed")
    entrypoint.chmod(0o755)
    return entrypoint


@pytest.mark.parametrize("path_mode", ["current", "other", "absent"])
def test_current_interpreter_entrypoint_is_independent_of_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_mode: str,
) -> None:
    current = _entrypoint(tmp_path / "current")
    other = _entrypoint(tmp_path / "old")
    monkeypatch.setattr(doctor.sysconfig, "get_path", lambda name: str(current.parent))
    monkeypatch.setenv(
        "PATH",
        str(current.parent) if path_mode == "current"
        else str(other.parent) if path_mode == "other"
        else str(tmp_path / "absent"),
    )
    checks = doctor._mcp_entrypoint_checks()
    installation, path_check = checks

    assert installation.status == "ok"
    assert str(current) in installation.detail
    assert str(other) not in installation.detail
    assert installation.required_for_profiles == ("agent", "full")
    assert path_check.status == ("ok" if path_mode == "current" else "warning")
    assert path_check.required_for_profiles == ()
    assert path_check.required_for_conversion is False
    if path_mode == "other":
        assert str(other).casefold() in path_check.detail.casefold()
    if path_mode != "current":
        assert str(current) in path_check.remediation

    # PATH warnings describe client configuration without invalidating a working
    # installation launched by its absolute entrypoint.
    monkeypatch.setattr(doctor, "collect_checks", lambda **kwargs: checks)
    report = doctor.build_report(profile="full")
    assert report["status"] == "ready"


def test_other_environment_on_path_does_not_make_current_installation_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _entrypoint(tmp_path / "old")
    current_scripts = tmp_path / "current"
    current_scripts.mkdir()
    monkeypatch.setattr(doctor.sysconfig, "get_path", lambda name: str(current_scripts))
    monkeypatch.setenv("PATH", str(other.parent))
    # On Windows, a command in the working directory can participate in PATH
    # resolution; it must never count as a current-interpreter installation.
    monkeypatch.chdir(other.parent)
    checks = doctor._mcp_entrypoint_checks()

    installation, path_check = checks
    assert installation.status == "missing"
    assert str(current_scripts) in installation.detail
    assert path_check.status == "warning"
    assert str(other).casefold() in path_check.detail.casefold()
    monkeypatch.setattr(doctor, "collect_checks", lambda **kwargs: checks)
    report = doctor.build_report(profile="agent")
    assert report["status"] == "limited"
    assert report["profile_ready"]["conversion"] is True
    assert report["profile_ready"]["agent"] is False
    assert report["profile_ready"]["full"] is False


def test_missing_entrypoint_reports_missing_without_spurious_path_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.sysconfig, "get_path", lambda name: str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    checks = doctor._mcp_entrypoint_checks()
    assert len(checks) == 1
    assert checks[0].status == "missing"
