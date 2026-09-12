"""Reject stale or damaged Windows payloads before Inno compiles an installer."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def package(monkeypatch):
    tools = Path(__file__).resolve().parents[1] / "tools"
    monkeypatch.syspath_prepend(str(tools))
    spec = importlib.util.spec_from_file_location("windows_package_under_test", tools / "build_windows_package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release(tmp_path, package):
    payload = tmp_path / "payload"
    site = payload / "runtime/Lib/site-packages"
    wheel = tmp_path / "cad2gis-0.7.5-py3-none-any.whl"
    entries = {
        "cad2gis/__init__.py": b"__version__ = '0.7.5'\n",
        "cad2gis/desktop/app.js": b"window.latest = true;\n",
        "cad2gis-0.7.5.dist-info/METADATA": b"Metadata-Version: 2.4\nName: cad2gis\nVersion: 0.7.5\n",
    }
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, content in entries.items():
            archive.writestr(relative, content)
            path = site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    binaries = ["runtime/python.exe", *(f"bin/{name}.exe" for name in package.ENTRIES)]
    for relative in binaries:
        path = payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture executable")
    for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        path = payload / "plugins/cad2gis-agent" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": "0.7.5"}))
    manifest = {
        "platform": "win32", "runtime_is_bundled": True,
        "wheel_sha256": package.sha256(wheel), "entrypoints": package.ENTRIES,
    }

    def seal():
        manifest["files"] = {
            path.relative_to(payload).as_posix(): package.sha256(path)
            for path in payload.rglob("*") if path.is_file() and path.name != "bundle-manifest.json"
        }
        (payload / "bundle-manifest.json").write_text(json.dumps(manifest))

    seal()
    return payload, wheel, manifest, seal


def test_verified_payload_matches_frozen_wheel(package, release):
    payload, wheel, manifest, _ = release
    assert package.verify_payload(payload, wheel, "0.7.5") == manifest


@pytest.mark.parametrize("damage", ["changed", "missing", "extra"])
def test_rejects_payload_drift_before_packaging(package, release, damage):
    payload, wheel, _, _ = release
    path = payload / "bin/cad2gis-desktop.exe"
    if damage == "changed":
        path.write_bytes(b"old launcher")
    elif damage == "missing":
        path.unlink()
    else:
        (payload / "unexpected-private-file.txt").write_bytes(b"must not ship")
    with pytest.raises(ValueError, match="missing or changed|not in the inventory"):
        package.verify_payload(payload, wheel, "0.7.5")


def test_rejects_old_wheel_under_new_installer_version(package, release):
    payload, wheel, _, _ = release
    with pytest.raises(ValueError, match="wheel version"):
        package.verify_payload(payload, wheel, "0.7.6")


@pytest.mark.parametrize("damage", ["source", "asset", "extra_module", "missing_module", "metadata", "old_metadata", "plugin"])
def test_resealing_manifest_cannot_claim_a_different_wheel(package, release, damage):
    payload, wheel, _, seal = release
    site = payload / "runtime/Lib/site-packages"
    if damage in {"source", "asset"}:
        path = site / ("cad2gis/__init__.py" if damage == "source" else "cad2gis/desktop/app.js")
        path.write_bytes(b"old code with the same version")
    elif damage == "extra_module":
        (site / "cad2gis/obsolete.py").write_bytes(b"old module")
    elif damage == "missing_module":
        (site / "cad2gis/__init__.py").unlink()
    elif damage == "metadata":
        (site / "cad2gis-0.7.5.dist-info/METADATA").write_bytes(b"Name: cad2gis\nVersion: 0.7.0\n")
    elif damage == "old_metadata":
        path = site / "cad2gis-0.7.0.dist-info/METADATA"
        path.parent.mkdir()
        path.write_bytes(b"Name: cad2gis\nVersion: 0.7.0\n")
    else:
        (payload / "plugins/cad2gis-agent/.codex-plugin/plugin.json").write_text('{"version":"0.7.0"}')
    seal()
    with pytest.raises(ValueError, match="does not match|version does not match"):
        package.verify_payload(payload, wheel, "0.7.5")


@pytest.mark.parametrize("relative", ["../outside", "C:/outside", "bin\\cad2gis.exe", "bin/../outside", "bin/cad2gis.exe:stream", "BIN/CAD2GIS.EXE"])
def test_rejects_unsafe_or_ambiguous_windows_inventory(package, release, relative):
    payload, wheel, manifest, _ = release
    manifest["files"][relative] = hashlib.sha256(b"data").hexdigest()
    (payload / "bundle-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Invalid Windows bundle inventory"):
        package.verify_payload(payload, wheel, "0.7.5")


def test_rejects_stale_command_registration(package, release):
    payload, wheel, manifest, _ = release
    manifest["entrypoints"] = {"cad2gis": "cad2gis.cli"}
    (payload / "bundle-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="entrypoint registration is stale"):
        package.verify_payload(payload, wheel, "0.7.5")


@pytest.mark.parametrize("damage", [None, "asset", "version"])
def test_cli_verifies_reused_payload_before_invoking_compiler(package, release, tmp_path, monkeypatch, damage):
    payload, wheel, _, seal = release
    repository = tmp_path / "repository"
    compiler = repository / "tmp/packaging-windows/inno/ISCC.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"compiler test double")
    version = "0.7.6" if damage == "version" else "0.7.5"
    (repository / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    if damage == "asset":
        (payload / "runtime/Lib/site-packages/cad2gis/desktop/app.js").write_bytes(b"stale")
        seal()
    monkeypatch.setattr(package, "REPOSITORY", repository)
    monkeypatch.setattr(package.sys, "platform", "win32")
    monkeypatch.setattr(package.sys, "argv", ["build_windows_package", "--wheel", str(wheel),
                                             "--reuse-payload", "--payload", str(payload)])
    monkeypatch.setattr(package, "verify_native", lambda path: {"native_hashes": {}})
    invocations = []

    def compile_package(arguments, **kwargs):
        invocations.append(arguments)
        (repository / f"dist/windows/CAD2GIS-{version}-windows-x64-setup.exe").write_bytes(b"compiled")

    monkeypatch.setattr(package.subprocess, "run", compile_package)
    if damage:
        with pytest.raises(ValueError, match="does not match"):
            package.main()
        assert not invocations
        assert not (repository / "dist").exists()
    else:
        assert package.main() == 0
        assert len(invocations) == 1
        assert f"/DAppVersion={version}" in invocations[0]
        report = json.loads((repository / "dist/windows/windows-build.json").read_text())
        assert report["installed_wheel_bytes_verified"] is True
        assert report["payload_files_verified"] > 0


@pytest.mark.skipif(os.name != "nt", reason="Windows junction policy")
@pytest.mark.parametrize("root_link", [False, True])
def test_rejects_payload_junction_before_following_it(package, release, tmp_path, monkeypatch, root_link):
    import subprocess

    payload, wheel, _, _ = release
    target = payload if root_link else tmp_path / "private"
    target.mkdir(exist_ok=True)
    protected = target / "keep.txt"
    protected.write_text("preserve")
    link = tmp_path / "payload-link" if root_link else payload / "unexpected-link"
    script = tmp_path / "junction.ps1"
    script.write_text("param([string]$Link,[string]$Target)\nNew-Item -ItemType Junction -Path $Link -Value $Target | Out-Null\n")
    subprocess.run(["powershell.exe", "-NoProfile", "-File", str(script), str(link), str(target)],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        if root_link:
            repository = tmp_path / "repository"
            compiler = repository / "tmp/packaging-windows/inno/ISCC.exe"
            compiler.parent.mkdir(parents=True)
            compiler.write_bytes(b"compiler test double")
            (repository / "pyproject.toml").write_text('[project]\nversion = "0.7.5"\n')
            monkeypatch.setattr(package, "REPOSITORY", repository)
            monkeypatch.setattr(package.sys, "argv", ["build_windows_package", "--wheel", str(wheel),
                                                     "--reuse-payload", "--payload", str(link)])
            with pytest.raises(ValueError, match="redirected directory"):
                package.main()
            assert not (repository / "dist").exists()
        else:
            with pytest.raises(ValueError, match="redirected path"):
                package.verify_payload(payload, wheel, "0.7.5")
        assert protected.read_text() == "preserve"
    finally:
        assert link.resolve() == target.resolve() and target.is_relative_to(tmp_path)
        link.rmdir()  # Remove only the task-owned junction, never its target.
