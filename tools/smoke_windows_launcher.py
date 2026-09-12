"""Verify the actual installed Windows desktop EXE with isolated state and logs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import parse_qs, urlsplit


def run(bundle: Path, output: Path) -> dict:
    import httpx

    bundle, output = bundle.resolve(strict=True), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    state, project = output / "state", output / "project"
    project.mkdir()
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "CAD2GIS_BACKEND_PATH", "CAD2GIS_LIBREDWG_CLI"):
        environment.pop(name, None)
    launcher = bundle / "bin/cad2gis-desktop.exe"
    process = subprocess.Popen([str(launcher), "--no-browser", "--state-dir", str(state),
                                "--project-root", str(project)], cwd=output, env=environment,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 60
        launch_url = None
        while time.monotonic() < deadline:
            for log in (state / "logs").glob("desktop-*.log"):
                for line in log.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                    if line.startswith("CAD2GIS: "):
                        launch_url = line.removeprefix("CAD2GIS: ").strip()
            if launch_url or process.poll() is not None:
                break
            time.sleep(.2)
        assert launch_url, "Desktop EXE did not announce its service in the isolated log directory"
        parsed = urlsplit(launch_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        token = parse_qs(parsed.fragment)["token"][0]
        asset_hashes = {}
        with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=60, trust_env=False) as client:
            assert client.get("/_app/").status_code == 200
            assert client.get("/_app/api/status").status_code == 401
            assert client.post("/_app/api/login", json={"token": token}).status_code == 200
            status = client.get("/_app/api/status")
            assert status.status_code == 200
            assert status.json()["semantic_extraction"]["available"] is True
            assert client.post("/_app/api/login", json={"token": token}).status_code == 401
            for name in ("app.js", "style.css", "assets/molamaker-actions-v1.png"):
                served = client.get("/_app/assets/" + name)
                assert served.status_code == 200
                local = bundle / "runtime/Lib/site-packages/cad2gis/desktop" / name
                actual = hashlib.sha256(served.content).hexdigest()
                assert actual == hashlib.sha256(local.read_bytes()).hexdigest(), name
                asset_hashes[name] = actual
            assert client.post("/_app/api/shutdown", json={}).status_code == 200
        process.wait(timeout=30)
        assert process.returncode == 0, process.returncode
        report = {"schema": "cad2gis.windows_desktop_launcher_smoke.v1", "status": "passed",
                  "launcher": str(launcher), "isolated_state_and_logs": str(state),
                  "authentication": "passed", "one_use_token": True,
                  "served_installed_asset_hashes": asset_hashes, "shutdown": "passed",
                  "system_browser_auto_open": "not exercised; --no-browser used"}
        (output / "launcher-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        if process.poll() is None:
            # This launcher starts exactly one task-owned Python service. Stop
            # that process tree on failure; never match by global process name.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.bundle, args.output), indent=2))
