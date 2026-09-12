"""Build the bounded LibreDWG 0.14 MTEXT fix in a fresh Linux/cross stage.

Run inside the retained Ubuntu builder. Reuses the pinned source archive;
does not rebuild Python, change the original native runtime, or fetch a nightly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351"
PATCH_SHA = "e47037ab526f7d5adadcdadda34153764aedcbdc76f970a65137cd4d7f32ad1b"
REPRO_SHA = "d8041b1352a7fb214584693993882437c775b196c5c269b3dd40c71704891c38"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(target, resume=False, jobs=2, work_root=Path('/tmp')):
    assert os.uname().sysname == "Linux"
    archive = ROOT / "tmp/packaging-linux/downloads/libredwg-0.14.tar.gz"
    patch = ROOT / "packaging/patches/libredwg-0.14-cquote-termination.patch"
    reproducer = ROOT / "packaging/patches/cquote_minimal.c"
    for path, expected in ((archive, SOURCE_SHA), (patch, PATCH_SHA), (reproducer, REPRO_SHA)):
        assert sha(path) == expected, f"Pinned input changed: {path}"
    work = work_root.expanduser().resolve() / ("cad2gis-libredwg-cquote-" + target)
    output = ROOT / "tmp/packaging-native-patched" / target
    if resume:
        assert work.is_dir() and output.is_dir(), "No interrupted stage to resume"
    else:
        work.mkdir(parents=True, exist_ok=False)
        output.mkdir(parents=True, exist_ok=False)
        with tarfile.open(archive) as package:
            package.extractall(work, filter="data")
    source = work / "libredwg-0.14"
    prefix = work / "installed"
    runtime = output / "libredwg"
    binaries = runtime if target == "windows" else runtime / "bin"
    binaries.mkdir(parents=True, exist_ok=resume)
    compiler = "x86_64-w64-mingw32-gcc" if target == "windows" else "gcc"
    strip = "x86_64-w64-mingw32-strip" if target == "windows" else "strip"
    suffix = ".exe" if target == "windows" else ""
    environment = dict(os.environ, CC=compiler, CFLAGS="-O2 -std=gnu11")
    configure = [str(source / "configure"), f"--prefix={prefix}",
                 "--disable-bindings", "--disable-python", "--disable-docs", "--disable-shared"]
    if target == "windows":
        configure.append("--host=x86_64-w64-mingw32")
        environment["CFLAGS"] += " -D__USE_MINGW_ANSI_STDIO=1"
        environment.update(LDFLAGS="-static", PKG_CONFIG_LIBDIR="/nonexistent")
    commands = []
    with (output / "build.log").open("a" if resume else "w", encoding="utf-8") as log:
        def run(command):
            commands.append(command)
            log.write("Command: " + json.dumps(command) + "\n")
            log.flush()
            print(json.dumps({"target": target, "command": command}), flush=True)
            subprocess.run(command, cwd=source, env=environment, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
        if not resume:
            run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)])
        else:
            with tarfile.open(archive) as package:
                original = package.extractfile("libredwg-0.14/src/out_dxf.c").read()
            old = b"  d[len - 1] = '\\0'; // add final delim, skipped above"
            new = b"  if (dest < dend)\n    *dest = '\\0';\n  else\n    d[len - 1] = '\\0';"
            assert original.count(old) == 1
            assert (source / "src/out_dxf.c").read_bytes() == original.replace(old, new)
            assert (source / "Makefile").is_file(), "Interrupted before configure finished"
            log.write("Resuming interrupted stage; pinned source and exact patch verified.\n")
        patched_source_sha = sha(source / "src/out_dxf.c")
        repro_binary = output / ("cquote_minimal" + suffix)
        run([compiler, "-D__USE_MINGW_ANSI_STDIO=1", "-O2", "-Wall", "-Wextra",
             *(["-static"] if target == "windows" else []), str(reproducer), "-o", str(repro_binary)])
        if target == "linux":
            run([str(repro_binary)])
        run(configure)
        run(["make", f"-j{jobs}"])
        if target == "windows":
            # Libtool's -static only forbids shared libtool libraries. Its
            # -all-static is required to include GCC's stack protector runtime.
            # Force only these three links; retain compiled objects unchanged.
            run(["make", "-C", "programs", "LDFLAGS=-all-static",
                 "-W", "dwgread.o", "-W", "dwg2dxf.o", "-W", "dxf2dwg.o",
                 "dwgread.exe", "dwg2dxf.exe", "dxf2dwg.exe"])
        run(["make", "install"])
        for name in ("dwg2dxf", "dwgread", "dxf2dwg"):
            binary = binaries / (name + suffix)
            shutil.copy2(prefix / "bin" / binary.name, binary)
            run([strip, "--strip-unneeded", str(binary)])
            if target == "windows":
                imports = subprocess.check_output(["x86_64-w64-mingw32-objdump", "-p", str(binary)], text=True)
                (output / (name + "-pe-imports.txt")).write_text(imports, encoding="utf-8")
                dlls = [line.split("DLL Name:", 1)[1].strip().lower() for line in imports.splitlines() if "DLL Name:" in line]
                assert dlls and all(dll in {"kernel32.dll", "msvcrt.dll", "user32.dll", "advapi32.dll", "shell32.dll"}
                                    or dll.startswith("api-ms-win-") for dll in dlls), dlls
            else:
                dependencies = subprocess.check_output(["ldd", str(binary)], text=True)
                (output / (name + "-ldd.txt")).write_text(dependencies, encoding="utf-8")
                assert "not found" not in dependencies, dependencies
    source_delivery = runtime / "source"
    source_delivery.mkdir(exist_ok=resume)
    for path in (archive, patch, reproducer):
        shutil.copy2(path, source_delivery / path.name)
    shutil.copy2(ROOT / "packaging/patches/README.md", source_delivery / "README.md")
    shutil.copy2(Path(__file__), source_delivery / "build_patched_libredwg.py")
    shutil.copy2(source / "COPYING", runtime / "COPYING")
    if target == "windows":
        shutil.copy2("/usr/share/doc/gcc-mingw-w64-base/copyright", runtime / "COPYING-GCC-RUNTIME.txt")
    receipt = {"schema": "cad2gis.patched_libredwg_runtime.v1", "target": target,
               "version": "0.14", "patch_id": "cquote-output-cursor-termination",
               "source_sha256": SOURCE_SHA, "patch_sha256": PATCH_SHA,
               "c_reproducer_sha256": REPRO_SHA, "patched_out_dxf_sha256": patched_source_sha,
               "compiler": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
               "commands": commands, "configure_command": configure, "resumed_interrupted_build": resume,
               "flags": {key: environment.get(key) for key in ("CC", "CFLAGS", "LDFLAGS", "PKG_CONFIG_LIBDIR")},
               "build_os": Path("/etc/os-release").read_text(),
               "native_hashes": {name + suffix: sha(binaries / (name + suffix)) for name in ("dwg2dxf", "dwgread", "dxf2dwg")},
               "reproducer_execution": "passed" if target == "linux" else "requires Windows execution",
               "real_dwg_acceptance": "pending", "created_at_utc": datetime.now(timezone.utc).isoformat()}
    (runtime / "build-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"status": "built_pending_acceptance", "runtime": str(runtime), "hashes": receipt["native_hashes"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("linux", "windows"), required=True)
    parser.add_argument("--resume", action="store_true", help="Continue verified interrupted stage without cleaning objects")
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--work-root", type=Path, default=Path('/tmp'))
    args = parser.parse_args()
    build(args.target, args.resume, args.jobs, args.work_root)
