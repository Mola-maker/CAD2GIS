"""Replace only the application wheel in an existing development payload.

Native dependencies are kept at the versions already recorded in its lock.
Use the full builder whenever dependency declarations change.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess

from build_runtime_bundle import REPOSITORY, clean_environment, create_launchers, write_manifest


def refresh(output: Path, wheel: Path) -> None:
    output = output.resolve(strict=True)
    wheel = wheel.resolve(strict=True)
    if not output.is_relative_to(REPOSITORY / "tmp") or not (output / "bundle-manifest.json").is_file():
        raise ValueError("Refresh only a known temporary build payload, never an installed application")
    python = output / "runtime" / ("python.exe" if os.name == "nt" else "bin/python3")
    env = clean_environment()
    report_path = output / "wheel-refresh.json"
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "install", "--disable-pip-version-check",
                    "--no-index", "--no-deps", "--no-compile", "--force-reinstall", "--report", str(report_path),
                    str(wheel)], check=True, env=env)
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "check"], check=True, env=env)
    full_path = output / "dependency-install.json"
    full = json.loads(full_path.read_text(encoding="utf-8"))
    update = json.loads(report_path.read_text(encoding="utf-8"))
    full["install"] = [item for item in full["install"] if item["metadata"]["name"].casefold() != "cad2gis"] + update["install"]
    full_path.write_text(json.dumps(full, indent=2), encoding="utf-8")
    create_launchers(output)
    shutil.copytree(REPOSITORY / "plugins/cad2gis-agent", output / "plugins/cad2gis-agent", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    write_manifest(output, wheel)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    refresh(args.payload, args.wheel)
