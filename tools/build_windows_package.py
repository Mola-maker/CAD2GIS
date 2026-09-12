"""Build the Windows x64 offline application installer using pinned tools."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile

from build_runtime_bundle import ENTRIES, REPOSITORY, build, download, extract, sha256

PYTHON_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PYTHON_SHA = "7c45c9622400d578709a9b2cddbe8124cc21d382409d9f13406d706d28e31b14"
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe"
INNO_SHA = "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f"
LIBREDWG_URL = "https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14-win64.zip"
LIBREDWG_SHA = "1ad7e15344d20b3426c3435b078d82fb84b35062815946b2cca9c5fc9810fea8"


def _redirected(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def verify_payload(payload: Path, wheel: Path, version: str) -> dict:
    """Bind every packaged file and installed application byte to the release input."""
    if _redirected(payload):
        raise ValueError("Windows payload must not be a redirected directory")
    payload = payload.resolve(strict=True)
    manifest = json.loads((payload / "bundle-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("platform") != "win32" or manifest.get("runtime_is_bundled") is not True:
        raise ValueError("Input must be a native Windows runtime bundle")
    if manifest.get("wheel_sha256") != sha256(wheel):
        raise ValueError("Payload does not contain the requested frozen wheel")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Bundle has no file checksum inventory")
    folded = set()
    for relative, digest in expected.items():
        path = PurePosixPath(relative)
        if (not relative or path.as_posix() != relative or path.is_absolute()
                or PureWindowsPath(relative).drive or "\\" in relative
                or ":" in relative or ".." in path.parts
                or relative.casefold() in folded
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError(f"Invalid Windows bundle inventory entry: {relative}")
        folded.add(relative.casefold())
    actual = set()
    # Reject reparse directories before walking into them. Inno recursively
    # packages the directory, so checking only manifest entries is insufficient.
    for directory, directories, files in os.walk(payload, followlinks=False):
        for name in directories + files:
            path = Path(directory) / name
            if _redirected(path):
                raise ValueError(f"Windows bundle contains a redirected path: {path.relative_to(payload)}")
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(payload).as_posix()
            if relative == "bundle-manifest.json":
                continue
            actual.add(relative)
            if relative not in expected:
                raise ValueError(f"Bundle file is not in the inventory: {relative}")
            if sha256(path) != expected[relative]:
                raise ValueError(f"Bundle file is missing or changed: {relative}")
    missing = set(expected) - actual
    if missing:
        raise ValueError(f"Bundle file is missing or changed: {sorted(missing)[0]}")
    if manifest.get("entrypoints") != ENTRIES:
        raise ValueError("Windows bundle entrypoint registration is stale")
    for relative in ("runtime/python.exe", *(f"bin/{name}.exe" for name in ENTRIES)):
        if relative not in actual:
            raise ValueError(f"Missing Windows bundle entry: {relative}")
    site = payload / "runtime/Lib/site-packages"
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("Release wheel must have exactly one distribution metadata entry")
        metadata = archive.read(metadata_names[0])
        identity = BytesParser().parsebytes(metadata)
        if identity.get("Name", "").casefold() != "cad2gis" or identity.get("Version") != version:
            raise ValueError("Release wheel version does not match the Windows installer version")
        installed_metadata = list(site.glob("cad2gis-*.dist-info/METADATA"))
        if len(installed_metadata) != 1 or installed_metadata[0].read_bytes() != metadata:
            raise ValueError("Installed CAD2GIS metadata does not match the release wheel")
        application_files = {name for name in archive.namelist() if name.startswith("cad2gis/") and not name.endswith("/")}
        installed_files = {path.relative_to(site).as_posix() for path in (site / "cad2gis").rglob("*") if path.is_file()}
        if not application_files or installed_files != application_files:
            raise ValueError("Installed CAD2GIS file inventory does not match the release wheel")
        for name in sorted(application_files):
            if sha256(site / name) != hashlib.sha256(archive.read(name)).hexdigest():
                raise ValueError(f"Installed CAD2GIS file does not match the release wheel: {name}")
    for name in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        plugin = json.loads((payload / "plugins/cad2gis-agent" / name).read_text(encoding="utf-8"))
        if plugin.get("version", "").split("+", 1)[0] != version:
            raise ValueError("Bundled agent plugin version does not match the release wheel")
    return manifest


def prepare(cache: Path) -> tuple[Path, Path, Path]:
    python_archive = download(PYTHON_URL, PYTHON_SHA, cache / "python.tar.gz")
    if not (cache / "python/python.exe").is_file():
        extract(python_archive, cache)
    libredwg_archive = download(LIBREDWG_URL, LIBREDWG_SHA, cache / "libredwg.zip")
    if not (cache / "libredwg/dwg2dxf.exe").is_file():
        extracted = cache / "libredwg-extracted"
        extract(libredwg_archive, extracted)
        candidates = list(extracted.rglob("dwg2dxf.exe"))
        if len(candidates) != 1:
            raise RuntimeError("LibreDWG archive did not have one dwg2dxf.exe")
        shutil.copytree(candidates[0].parent, cache / "libredwg", dirs_exist_ok=True)
    inno_installer = download(INNO_URL, INNO_SHA, cache / "innosetup.exe")
    compiler = cache / "inno/ISCC.exe"
    if not compiler.is_file():
        # Private build-tool installation, no PATH/desktop/start menu integration.
        subprocess.run([
            str(inno_installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/CURRENTUSER", "/NOICONS", "/TASKS=", f"/DIR={cache / 'inno'}",
            f"/LOG={cache / 'inno-install.log'}",
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return cache / "python", cache / "libredwg", compiler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--libredwg", type=Path, help="Qualified patched native runtime with build and acceptance receipts")
    parser.add_argument("--reuse-payload", action="store_true", help="Package an already refreshed and verified payload without rebuilding its runtime")
    parser.add_argument("--payload", type=Path, default=REPOSITORY / "tmp/packaging-windows/payload")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build this package on Windows")
    cache = REPOSITORY / "tmp/packaging-windows"
    if args.prepare_only:
        prepare(cache)
        print(f"Build tools ready: {cache}")
        return 0
    if args.wheel is None:
        parser.error("--wheel is required unless --prepare-only")
    if args.reuse_payload:
        # Preserve the caller's final path component for the reparse-point
        # check; resolve() here would hide a top-level Windows junction.
        payload = args.payload.absolute()
        compiler = cache / "inno/ISCC.exe"
        if not compiler.is_file():
            parser.error("Prepare the pinned Inno compiler first")
    else:
        runtime, _, compiler = prepare(cache)
        libredwg = args.libredwg or REPOSITORY / "tmp/packaging-native-patched/windows/libredwg"
        verify_native(libredwg)
        payload = build(runtime, args.wheel, libredwg, args.payload, wheelhouse=args.wheelhouse)
    version = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest = verify_payload(payload, args.wheel.resolve(strict=True), version)
    native_receipt = verify_native(payload / "libredwg")
    for name, expected in native_receipt["native_hashes"].items():
        if manifest["files"].get("libredwg/" + name) != expected:
            raise ValueError("Regenerate payload manifest after replacing native tools")
    output = REPOSITORY / "dist/windows"
    output.mkdir(parents=True, exist_ok=True)
    installer = output / f"CAD2GIS-{version}-windows-x64-setup.exe"
    if installer.exists():
        raise FileExistsError("Archive the previous installer before building a new candidate")
    subprocess.run([
        str(compiler), f"/DPayloadDir={payload}", f"/DOutputDir={output}",
        f"/DAppVersion={version}", str(REPOSITORY / "packaging/windows/cad2gis.iss"),
    ], check=True)
    report = {"schema": "cad2gis.windows_package_build.v1", "version": version,
              "installer": installer.name, "bytes": installer.stat().st_size,
              "sha256": sha256(installer), "wheel_sha256": manifest["wheel_sha256"],
              "payload_manifest_sha256": sha256(payload / "bundle-manifest.json"),
              "payload_files_verified": len(manifest["files"]), "installed_wheel_bytes_verified": True,
              "native_hashes": native_receipt["native_hashes"], "reused_payload": args.reuse_payload,
              "installation_acceptance": "pending", "signing": "unsigned development distribution"}
    (output / "windows-build.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "SHA256SUMS").write_text(f"{report['sha256']}  {installer.name}\n", encoding="utf-8")
    print(output)
    return 0


def verify_native(native: Path) -> dict:
    """Do not let the historical upstream archive silently re-enter a release."""
    receipt = json.loads((native / "build-receipt.json").read_text())
    acceptance = json.loads((native / "acceptance.json").read_text())
    if receipt.get("patch_sha256") != "e47037ab526f7d5adadcdadda34153764aedcbdc76f970a65137cd4d7f32ad1b":
        raise ValueError("Qualified LibreDWG MTEXT termination patch required")
    if acceptance.get("status") != "passed" or acceptance.get("repetitions_per_drawing", 0) < 5:
        raise ValueError("Repeated real-DWG native acceptance required")
    for name in ("dwg2dxf.exe", "dwgread.exe", "dxf2dwg.exe"):
        expected = receipt["native_hashes"][name]
        if sha256(native / name) != expected or acceptance["native_hashes"].get(name) != expected:
            raise ValueError(f"Native receipt mismatch: {name}")
    if (native / "libredwg-0.dll").exists():
        raise ValueError("Obsolete shared LibreDWG DLL must not be included with this static runtime")
    return receipt


if __name__ == "__main__":
    raise SystemExit(main())
