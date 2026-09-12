"""Install, exercise and uninstall a Windows release candidate in an isolated path."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{FACCB633-6461-443A-90B2-F7AC74343244}_is1"


def user_path() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "Path")[0]
    except FileNotFoundError:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-wheel-sha256", help="Require the installed manifest to match the frozen release wheel")
    parser.add_argument("--wait-for-ui-review", action="store_true",
                        help="Keep this test installation available for bounded browser review before uninstall")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "tmp") or output.exists():
        raise ValueError("Use a new directory under repository tmp for installation verification")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
            raise RuntimeError("An existing CAD2GIS installation must not be overwritten by this test")
    except FileNotFoundError:
        pass
    output.mkdir(parents=True)
    installation = output / "Installed CAD2GIS 空格"
    project_data = output / "User projects"
    project_data.mkdir()
    marker = project_data / "keep-my-project.txt"
    marker.write_text("User projects must survive uninstall", encoding="utf-8")
    path_before = user_path()
    installer = args.installer.resolve(strict=True)
    subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
                    "/TASKS=addpath", f"/DIR={installation}", f"/LOG={output / 'install.log'}"],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=300)
    assert (installation / "bin/cad2gis-desktop.exe").is_file()
    path_added = str(installation / "bin")
    assert path_added.casefold() in [p.casefold() for p in user_path().split(";")]
    (output / "path-before.json").write_text(json.dumps(path_before), encoding="utf-8")
    python = installation / "runtime/python.exe"
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "CAD2GIS_BACKEND_PATH", "CAD2GIS_LIBREDWG_CLI"):
        environment.pop(name, None)
    results = {}
    manifest = {}
    try:
        manifest_path = installation / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shutil.copy2(manifest_path, output / "installed-bundle-manifest.json")
        if args.expected_wheel_sha256:
            assert manifest["wheel_sha256"] == args.expected_wheel_sha256.lower(), "Installed wheel differs from release input"
        for relative, expected in manifest["files"].items():
            installed_file = (installation / relative).resolve(strict=True)
            assert installed_file.is_relative_to(installation.resolve()), relative
            with installed_file.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            assert actual == expected, f"Installed file hash mismatch: {relative}"
        (output / "installed-integrity.json").write_text(json.dumps({
            "status": "passed", "wheel_sha256": manifest["wheel_sha256"],
            "manifest_files_verified": len(manifest["files"]),
        }, indent=2), encoding="utf-8")
        for script, directory in (("smoke_runtime_bundle.py", "native"), ("smoke_synthetic_conversion.py", "conversion"),
                                  ("smoke_application_bundle.py", "application"),
                                  ("smoke_windows_launcher.py", "desktop-launcher")):
            with (output / f"{directory}.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run([str(python), "-I", "-B", str(ROOT / "tools" / script),
                    "--bundle", str(installation), "--output", str(project_data / directory),
                    *(["--conversion", str(project_data / "conversion")] if directory == "application" else [])],
                    env=environment, cwd=project_data, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=300)
            results[directory] = completed.returncode
            if completed.returncode:
                raise RuntimeError(f"{script} failed; inspect {output / (directory + '.log')}")
        if args.wait_for_ui_review:
            (output / "awaiting-ui-review.json").write_text(json.dumps({
                "installation": str(installation), "wheel_sha256": manifest["wheel_sha256"],
                "review_result": str(output / "installed-ui-review.json"),
            }, indent=2), encoding="utf-8")
            deadline = time.monotonic() + 900
            review_path = output / "installed-ui-review.json"
            while not review_path.is_file() and time.monotonic() < deadline:
                time.sleep(1)
            review = json.loads(review_path.read_text(encoding="utf-8"))
            assert review["status"] == "passed", review
            assert review["wheel_sha256"] == manifest["wheel_sha256"], "UI review used a stale bundle"
            results["installed_ui_review"] = 0
    finally:
        subprocess.run([str(installation / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                        f"/LOG={output / 'uninstall.log'}"], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=180)
    # Inno's uninstall bootstrap can exit while its temporary worker is still
    # removing files and restoring PATH. Observe completion before asserting.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        log = (output / "uninstall.log").read_text(encoding="utf-8-sig", errors="replace")
        if "Uninstallation process succeeded." in log and user_path() == path_before:
            break
        time.sleep(.2)
    assert marker.is_file() and marker.read_text(encoding="utf-8") == "User projects must survive uninstall"
    assert not (installation / "bin/cad2gis.exe").exists()
    assert user_path() == path_before, "Uninstall did not restore the original user PATH exactly"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
            raise AssertionError("Uninstall registration was not removed")
    except FileNotFoundError:
        pass
    with installer.open("rb") as stream:
        installer_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"schema": "cad2gis.windows_install_verification.v1", "installer": str(installer),
              "installer_sha256": installer_hash,
              "installation": "passed", "relocated_path_with_spaces_and_unicode": True,
              "wheel_sha256": manifest["wheel_sha256"], "manifest_files_verified": len(manifest["files"]),
              "tests": results, "uninstall": "passed", "user_data_preserved": True, "user_path_restored": True}
    (output / "installation-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run on Windows")
    main()
