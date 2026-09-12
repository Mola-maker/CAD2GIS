"""Verify a relocated Linux installation with its own interpreter and native tools."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

REPOSITORY = Path(__file__).resolve().parents[2]


def verify(bundle: Path, output: Path) -> dict:
    import httpx

    bundle = bundle.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to(REPOSITORY / "tmp"):
        raise ValueError("Verification output must stay inside repository tmp")
    output.mkdir(parents=True, exist_ok=False)
    python = bundle / "runtime/bin/python3"
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "CAD2GIS_BACKEND_PATH", "CAD2GIS_LIBREDWG_CLI"):
        env.pop(name, None)
    manifest = json.loads((bundle / "bundle-manifest.json").read_text())
    package_status = None
    desktop_entry = bundle / "bin/cad2gis-desktop"
    if bundle == Path("/opt/cad2gis"):
        package_status = subprocess.check_output(["dpkg-query", "--status", "cad2gis"], text=True)
        assert "Status: install ok installed" in package_status, package_status
        for name in manifest["entrypoints"]:
            command = Path("/usr/bin") / name
            assert command.is_symlink() and command.resolve() == bundle / "bin" / name, command
        desktop_entry = Path("/usr/bin/cad2gis-desktop")
        doctor = subprocess.run([
            "/usr/bin/cad2gis", "doctor", "--deep", "--strict", "--profile", "full", "--json",
        ], cwd=output, env=env, capture_output=True, text=True, timeout=120)
        (output / "installed-command-doctor.json").write_text(doctor.stdout, encoding="utf-8")
        assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    for script, folder in (("smoke_runtime_bundle.py", "native"),
                           ("smoke_synthetic_conversion.py", "conversion")):
        with (output / (folder + ".log")).open("w", encoding="utf-8") as log:
            subprocess.run([str(python), "-I", "-B", str(REPOSITORY / "tools" / script),
                            "--bundle", str(bundle), "--output", str(output / folder)],
                           env=env, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                           timeout=300, check=True)
    project = output / "application-project"
    project.mkdir()
    announcements = queue.Queue()
    with (output / "application.stderr.log").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen([str(desktop_entry), "--no-browser",
                                    "--project-root", str(project),
                                    "--state-dir", str(output / "application-state")], cwd=output, env=env,
                                   stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8")
        def reader():
            for line in process.stdout:
                if line.startswith("CAD2GIS: "):
                    announcements.put(line.removeprefix("CAD2GIS: ").strip())
                    return
        threading.Thread(target=reader, daemon=True).start()
        try:
            launch_url = announcements.get(timeout=60)
            parsed = urlsplit(launch_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            token = parse_qs(parsed.fragment)["token"][0]
            with httpx.Client(base_url=origin, timeout=60, trust_env=False) as client:
                assert client.get("/_app/").status_code == 200
                assert client.get("/_app/assets/app.js").status_code == 200
                assert client.get("/_app/assets/style.css").status_code == 200
                assert client.get("/_app/api/status").status_code == 401
                login = client.post("/_app/api/login", json={"token": token}, headers={"Origin": origin})
                assert login.status_code == 200, login.text
                health = client.get("/_app/api/status")
                assert health.status_code == 200, health.text
                repeated = client.post("/_app/api/login", json={"token": token}, headers={"Origin": origin})
                assert repeated.status_code == 401, repeated.text
                stopped = client.post("/_app/api/shutdown", json={}, headers={"Origin": origin})
                assert stopped.status_code == 200, stopped.text
            process.wait(timeout=20)
            assert process.returncode == 0
            with (output / "application-workflow.log").open("w", encoding="utf-8") as log:
                subprocess.run([
                    str(python), "-I", "-B", str(REPOSITORY / "tools/smoke_application_bundle.py"),
                    "--bundle", str(bundle), "--output", str(output / "application-workflow"),
                    "--conversion", str(output / "conversion"),
                ], cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300, check=True)
            workflow = json.loads((output / "application-workflow/application-report.json").read_text())
            assert workflow["real_semantic_extraction"] == "passed", workflow
            assert workflow["semantic_stages_completed"] == 3, workflow
            assert workflow["semantic_artifact_download_hashes_verified"] is True, workflow
            assert workflow["automatic_semantic_commit"] is False, workflow
            result = {
                "schema": "cad2gis.linux_install_verification.v1", "bundle": str(bundle),
                "wheel_sha256": manifest["wheel_sha256"],
                "dpkg_status": package_status, "desktop_entry": str(desktop_entry),
                "python": sys.executable, "native_smoke": "passed", "canonical_conversion": "passed",
                "application": {"assets": "passed", "unauthenticated_denied": True,
                                "login_status_shutdown": "passed", "one_use_launch_token": True,
                                "upload_inspect_svg_gcp_rematch": "passed",
                                "real_semantic_extraction": "passed", "semantic_stages_completed": 3,
                                "semantic_artifact_download_hashes_verified": True,
                                "automatic_semantic_commit": False,
                                "state_dir": str(output / "application-state")},
                "host": Path("/etc/os-release").read_text(), "verified_at": time.time(),
                "desktop_browser_window": "HTTP workflow verified; browser auto-open not exercised",
            }
            (output / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle, args.output), indent=2))
