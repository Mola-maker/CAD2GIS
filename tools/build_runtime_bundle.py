"""Build a relocatable, platform-native runtime from a clean standalone Python.

No project drawings, user environment, or development virtualenv is copied.
Run on the target OS. The resulting payload is consumed by platform installers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

REPOSITORY = Path(__file__).resolve().parents[1]
ENTRIES = {
    "cad2gis": "cad2gis.cli",
    "cad2gis-agent-mcp": "cad2gis.agent_mcp",
    "cad2gis-desktop": "cad2gis.application",
    "cad2gis-symbols": "cad2gis.symbol_assets",
    "cad2gis-qgis-package": "cad2gis.qgis_package",
    "cad2gis-qgis-verify": "cad2gis.qgis_verify",
    "cad2gis-qgis-review": "cad2gis.qgis_review",
    "cad2gis-qgis-review-verify": "cad2gis.qgis_review_verify",
    "cad2gis-svg-delivery": "cad2gis.svg_delivery",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, expected: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256(destination) == expected:
        return destination
    temporary = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "CAD2GIS-package-builder/1"})
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    if sha256(temporary) != expected:
        raise RuntimeError(f"Checksum mismatch: {url}")
    temporary.replace(destination)
    return destination


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                if not (root / item.filename).resolve().is_relative_to(root):
                    raise ValueError(f"Unsafe archive path: {item.filename}")
            bundle.extractall(root)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(root, filter="data")


def clean_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CAD2GIS_BACKEND_PATH"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def create_launchers(output: Path) -> None:
    bin_dir = output / "bin"
    bin_dir.mkdir(exist_ok=True)
    if os.name == "nt":
        compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        if not compiler.is_file():
            raise RuntimeError("The Windows .NET Framework C# compiler is required")
        for name in ENTRIES:
            subprocess.run([
                str(compiler), "/nologo", "/optimize+", "/platform:x64",
                "/target:" + ("winexe" if name == "cad2gis-desktop" else "exe"),
                "/reference:System.Windows.Forms.dll", f"/out:{bin_dir / (name + '.exe')}",
                f"/win32icon:{REPOSITORY / 'packaging/windows/molamaker.ico'}",
                str(REPOSITORY / "packaging/windows/Launcher.cs"),
            ], check=True)
    else:
        for name, module in ENTRIES.items():
            launcher = bin_dir / name
            launcher.write_text(
                '#!/bin/sh\nset -eu\n'
                'resolved=$(readlink -f "$0")\n'
                'root=$(CDPATH= cd -- "$(dirname -- "$resolved")/.." && pwd)\n'
                'export CAD2GIS_BUNDLE_ROOT="$root"\n'
                ': "${CAD2GIS_LIBREDWG_CLI:=$root/libredwg/bin/dwg2dxf}"\n'
                'export CAD2GIS_LIBREDWG_CLI\n'
                'export PATH="$root/bin:$PATH"\n'
                'unset PYTHONPATH PYTHONHOME\n'
                f'exec "$root/runtime/bin/python3" -I -B -X utf8 -m {module} "$@"\n',
                encoding="utf-8", newline="\n",
            )
            launcher.chmod(0o755)


def build(runtime_source: Path, wheel: Path, libredwg: Path, output: Path,
          *, wheelhouse: Path | None = None, requirements_lock: Path | None = None) -> Path:
    runtime_source = runtime_source.resolve(strict=True)
    wheel = wheel.resolve(strict=True)
    libredwg = libredwg.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Output must be a new directory: {output}")
    if not output.is_relative_to(REPOSITORY):
        raise ValueError("Build output must stay inside this repository")
    output.mkdir(parents=True)
    shutil.copytree(runtime_source, output / "runtime", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    python = output / "runtime" / ("python.exe" if os.name == "nt" else "bin/python3")
    if not python.is_file():
        raise ValueError(f"Standalone Python is missing: {python}")
    env = clean_environment()
    subprocess.run([str(python), "-I", "-m", "ensurepip"], env=env, check=True)
    sources = (["--no-index", "--find-links", str(wheelhouse.resolve())] if wheelhouse else [])
    lock = requirements_lock or REPOSITORY / "packaging/locks" / f"{sys.platform}-py312.txt"
    if lock.is_file():
        subprocess.run([
            str(python), "-I", "-B", "-m", "pip", "install", "--disable-pip-version-check",
            "--only-binary=:all:", "--no-compile", "--require-hashes", "-r", str(lock),
            "--report", str(output / "locked-dependencies.json"), *sources,
        ], env=env, check=True)
    subprocess.run([
        str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
        "--only-binary=:all:", "--no-compile", "--report", str(output / "dependency-install.json"),
        *sources, *(["--no-deps"] if lock.is_file() else []),
        str(wheel) + "[agent]",
    ], env=env, check=True)
    subprocess.run([str(python), "-I", "-m", "pip", "check"], env=env, check=True)
    # Native helpers stay separate from Python, selected explicitly by launchers.
    shutil.copytree(libredwg, output / "libredwg", symlinks=True)
    cli = output / "libredwg" / ("dwg2dxf.exe" if os.name == "nt" else "bin/dwg2dxf")
    if not cli.is_file():
        raise ValueError(f"LibreDWG payload must include {cli}")
    shutil.copytree(REPOSITORY / "plugins/cad2gis-agent", output / "plugins/cad2gis-agent",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    create_launchers(output)
    # Keep installed package license files intact and provide a searchable inventory.
    inventory_code = (
        "import importlib.metadata as m,json; "
        "print(json.dumps(sorted([{'name':d.metadata['Name'],'version':d.version,"
        "'license':d.metadata.get('License-Expression') or d.metadata.get('License') or "
        "'; '.join(d.metadata.get_all('Classifier', [])),"
        "'license_files':[str(f) for f in d.files or [] if 'license' in str(f).lower() "
        "or 'copying' in str(f).lower()]} for d in m.distributions()],key=lambda d:d['name'].lower()),"
        "ensure_ascii=True,indent=2))"
    )
    inventory = subprocess.check_output([str(python), "-I", "-B", "-c", inventory_code], env=env)
    (output / "THIRD_PARTY_COMPONENTS.json").write_bytes(inventory)
    shutil.copyfile(REPOSITORY / "packaging/THIRD_PARTY_NOTICES.md", output / "THIRD_PARTY_NOTICES.md")
    report = json.loads((output / "dependency-install.json").read_text(encoding="utf-8"))
    if lock.is_file():
        locked_report = json.loads((output / "locked-dependencies.json").read_text(encoding="utf-8"))
        report["install"] = locked_report["install"] + report["install"]
        (output / "dependency-install.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lock_lines = []
    for item in report["install"]:
        meta = item["metadata"]
        if meta["name"].casefold() == "cad2gis":
            continue
        digest = item["download_info"]["archive_info"]["hashes"]["sha256"]
        lock_lines.append(f"{meta['name']}=={meta['version']} --hash=sha256:{digest}")
    (output / "requirements.lock").write_text("\n".join(sorted(lock_lines)) + "\n", encoding="utf-8")
    write_manifest(output, wheel)
    return output


def write_manifest(output: Path, wheel: Path) -> None:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPOSITORY, text=True).strip())
    manifest = {
        "schema": "cad2gis.installable_bundle.v1", "platform": sys.platform,
        "source_revision": revision, "source_tree_dirty": dirty,
        "wheel": wheel.name, "wheel_sha256": sha256(wheel),
        "entrypoints": ENTRIES, "runtime_is_bundled": True,
        "private_project_data_included": False,
        "derived_web_demo_included": True,
        "files": {str(path.relative_to(output)).replace("\\", "/"): sha256(path)
                  for path in sorted(output.rglob("*")) if path.is_file()
                  and path != output / "bundle-manifest.json"},
    }
    (output / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-source", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--libredwg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--requirements-lock", type=Path)
    args = parser.parse_args()
    print(build(args.runtime_source, args.wheel, args.libredwg, args.output,
                wheelhouse=args.wheelhouse, requirements_lock=args.requirements_lock))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
