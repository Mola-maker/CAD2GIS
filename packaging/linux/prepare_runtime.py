"""Prepare checksum-pinned Linux build inputs without modifying system packages."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools"))
from build_runtime_bundle import download, extract, sha256  # noqa: E402

PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/"
    "cpython-3.12.14%2B20260901-x86_64-unknown-linux-gnu-install_only.tar.gz"
)
PYTHON_SHA256 = "936c246dfdbbfa7cb22dd01814a21f582a892689fae96b06071a5e433baffa22"
LIBREDWG_URL = "https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz"
LIBREDWG_SHA256 = "cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351"
# Ubuntu 24.04 signed package index, make 4.3-4.1build2 amd64.
MAKE_SHA256 = "1fe6a815b56c7b6e9ce4086a363f09444bbd0a0d30e230c453d0b78e44b57a99"


def prepare(work: Path) -> None:
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("This recipe requires native Linux x86_64 (WSL is supported)")
    work = work.resolve()
    if not work.is_relative_to(REPOSITORY / "tmp"):
        raise ValueError("Preparation must stay under the repository tmp directory")
    downloads = work / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    python_archive = download(PYTHON_URL, PYTHON_SHA256, downloads / "python-3.12.14.tar.gz")
    python_root = work / "standalone"
    if not (python_root / "complete.json").is_file():
        python_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(python_archive) as archive:
            # The optional ncurses terminal database has case-only symlink
            # aliases, which become cycles on case-insensitive Windows/WSL
            # mounts. CAD2GIS uses no terminal emulation; omit this database.
            def runtime_filter(member, destination):
                if member.name.startswith("python/share/terminfo"):
                    return None
                return tarfile.data_filter(member, destination)
            archive.extractall(python_root, filter=runtime_filter)
            for member in archive.getmembers():
                if member.name.startswith("python/share/terminfo"):
                    continue
                if member.isfile() or member.isdir():
                    (python_root / member.name).chmod(
                        0o755 if member.isdir() or member.mode & 0o111 else 0o644)
        (python_root / "complete.json").write_text(json.dumps({
            "archive_sha256": PYTHON_SHA256, "excluded": ["python/share/terminfo"],
            "reason": "Optional terminal database has case-only aliases on Windows mounts",
        }), encoding="utf-8")
    source_archive = download(LIBREDWG_URL, LIBREDWG_SHA256, downloads / "libredwg-0.14.tar.gz")
    destination = work / "libredwg"
    if not (destination / "bin/dwg2dxf").is_file():
        make = shutil.which("make")
        if not make:
            # Ubuntu's signed package index authenticates this build-only helper.
            package = downloads / "make_4.3-4.1build2_amd64.deb"
            if not package.is_file():
                subprocess.run(["apt-get", "download", "make=4.3-4.1build2"], cwd=downloads, check=True)
            if sha256(package) != MAKE_SHA256:
                raise ValueError("Ubuntu make helper checksum does not match the pinned package")
            subprocess.run(["dpkg-deb", "--extract", str(package), str(work / "build-tools")], check=True)
            make = str(work / "build-tools/usr/bin/make")
        if not shutil.which("cc"):
            raise RuntimeError("A C compiler is required to build the pinned LibreDWG release")
        if not shutil.which("pkg-config"):
            raise RuntimeError("pkg-config is required to build the pinned LibreDWG release")
        source_root = work / "source"
        source = source_root / "libredwg-0.14"
        if not (source / "configure").is_file():
            extract(source_archive, source_root)
        commands = [
            [str(source / "configure"), f"--prefix={destination}", "--disable-bindings",
             "--disable-docs", "--disable-shared"],
            [make, "-j2"], [make, "install"],
        ]
        environment = dict(os.environ)
        environment["PATH"] = str(Path(make).parent) + os.pathsep + environment.get("PATH", "")
        with (work / "libredwg-build.log").open("w", encoding="utf-8") as log:
            for command in commands:
                log.write("Command: " + json.dumps(command) + "\n")
                log.flush()
                subprocess.run(command, cwd=source, env=environment,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
        if not (destination / "bin/dwg2dxf").is_file():
            raise RuntimeError("LibreDWG build did not produce bin/dwg2dxf")
    runtime = work / "libredwg-runtime"
    (runtime / "bin").mkdir(parents=True, exist_ok=True)
    strip = shutil.which("strip")
    if not strip:
        raise RuntimeError("binutils strip is required to prepare release binaries")
    for name in ("dwg2dxf", "dwgread", "dxf2dwg"):
        shutil.copy2(destination / "bin" / name, runtime / "bin" / name)
        subprocess.run([strip, "--strip-unneeded", str(runtime / "bin" / name)], check=True)
    source_dir = runtime / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_archive, source_dir / source_archive.name)
    shutil.copy2(work / "source/libredwg-0.14/COPYING", runtime / "COPYING")
    receipt = {
        "schema": "cad2gis.linux_runtime_inputs.v1", "architecture": "x86_64",
        "python": {"url": PYTHON_URL, "sha256": PYTHON_SHA256, "version": "3.12.14",
                   "excluded": ["share/terminfo"],
                   "exclusion_reason": "Unused terminal emulation database; case-only aliases on WSL"},
        "libredwg": {"url": LIBREDWG_URL, "sha256": LIBREDWG_SHA256, "version": "0.14",
                     "configure": ["--disable-bindings", "--disable-docs", "--disable-shared"]},
        "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
        "build_os": Path("/etc/os-release").read_text(),
        "included_commands": ["dwg2dxf", "dwgread", "dxf2dwg"],
        "strip": "--strip-unneeded",
        "native_hashes": {p.name: sha256(p) for p in sorted((runtime / "bin").iterdir()) if p.is_file()},
    }
    (runtime / "build-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"python": str(python_root / "python"), "libredwg": str(runtime)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=REPOSITORY / "tmp/packaging-linux")
    prepare(parser.parse_args().work)
