"""Exercise the real Inno preflight with a running EXE in an isolated test app."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]


def verify(output: Path) -> dict:
    output = output.resolve()
    if not output.is_relative_to(ROOT / "tmp") or output.exists():
        raise ValueError("Use a fresh workspace tmp directory")
    app = output / "Installed app"
    runtime = app / "runtime"
    runtime.mkdir(parents=True)
    creation = {"creationflags": subprocess.CREATE_NO_WINDOW}

    def run(arguments):
        return subprocess.run([str(arg) for arg in arguments], timeout=60,
                              capture_output=True, **creation)

    source = output / "hold.cs"
    source.write_text("class Hold { static void Main() { System.Threading.Thread.Sleep(120000); } }")
    binary = runtime / "python.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    result = run([compiler, "/nologo", "/target:exe", f"/out:{binary}", source])
    assert result.returncode == 0, result.stderr + result.stdout
    (output / "payload.txt").write_text("new")
    script = output / "test.iss"
    script.write_text(f'''[Setup]
AppId=CAD2GIS-upgrade-guard-isolated-test
AppName=CAD2GIS upgrade guard isolated test
AppVersion=1
DefaultDirName={app}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir={output}
OutputBaseFilename=guard-test
Compression=none
[Files]
Source: "{output / 'payload.txt'}"; DestDir: "{{app}}"; DestName: "marker.txt"
[InstallDelete]
Type: files; Name: "{{app}}\\old-metadata.txt"
[Code]
#include "{ROOT / 'packaging/windows/upgrade_guard.iss'}"
''', encoding="utf-8-sig")
    result = run([ROOT / "tmp/packaging-windows/inno/ISCC.exe", script])
    assert result.returncode == 0, result.stderr + result.stdout
    # A runtime root junction must not let installation touch its target.
    redirected = output / "redirected-app"
    outside = output / "external-runtime"
    redirected.mkdir()
    outside.mkdir()
    protected = outside / "keep.txt"
    protected.write_text("preserve")
    junction = redirected / "runtime"
    junction_script = output / "junction.ps1"
    junction_script.write_text("param([string]$Link,[string]$Target)\n"
                              "$ErrorActionPreference='Stop'\n"
                              "New-Item -ItemType Junction -Path $Link -Value $Target | Out-Null\n")
    result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                  junction_script, junction, outside])
    assert result.returncode == 0, result.stderr + result.stdout
    assert junction.resolve() == outside.resolve() and outside.is_relative_to(output)
    result = run([output / "guard-test.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES",
                  "/NORESTART", f"/DIR={redirected}", f"/LOG={output / 'junction.log'}"])
    assert result.returncode != 0 and protected.read_text() == "preserve"
    assert not (redirected / "marker.txt").exists()
    junction.rmdir()  # Remove only this verified task-owned junction, not its target.
    marker, metadata = app / "marker.txt", app / "old-metadata.txt"
    marker.write_text("old")
    metadata.write_text("keep")
    args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", f"/DIR={app}"]
    holder = subprocess.Popen([str(binary)], **creation)
    try:
        result = run([output / "guard-test.exe", *args, f"/LOG={output / 'blocked.log'}"])
        assert result.returncode != 0
        assert marker.read_text() == "old" and metadata.read_text() == "keep"
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    result = run([output / "guard-test.exe", *args, f"/LOG={output / 'installed.log'}"])
    assert result.returncode == 0, result.stderr + result.stdout
    assert marker.read_text() == "new" and not metadata.exists()
    holder = subprocess.Popen([str(binary)], **creation)
    try:
        result = run([app / "unins000.exe", *args, f"/LOG={output / 'blocked-uninstall.log'}"])
        assert result.returncode != 0 and marker.exists()
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    result = run([app / "unins000.exe", *args, f"/LOG={output / 'uninstalled.log'}"])
    assert result.returncode == 0
    deadline = time.monotonic() + 15
    while marker.exists() and time.monotonic() < deadline:
        time.sleep(.1)
    assert not marker.exists()
    report = {"status": "passed", "busy_install_preserves_old_files": True,
              "idle_install": "passed", "busy_uninstall_preserves_runtime": True,
              "idle_uninstall": "passed", "root_junction_blocked": True, "isolated_app_id": True}
    (output / "report.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().output), indent=2))
