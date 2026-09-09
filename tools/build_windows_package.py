"""Build the Windows x64 offline application installer using pinned tools."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from build_runtime_bundle import REPOSITORY, build, download, extract

PYTHON_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PYTHON_SHA = "7c45c9622400d578709a9b2cddbe8124cc21d382409d9f13406d706d28e31b14"
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe"
INNO_SHA = "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f"
LIBREDWG_URL = "https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14-win64.zip"
LIBREDWG_SHA = "1ad7e15344d20b3426c3435b078d82fb84b35062815946b2cca9c5fc9810fea8"


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
    parser.add_argument("--payload", type=Path, default=REPOSITORY / "tmp/packaging-windows/payload")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build this package on Windows")
    cache = REPOSITORY / "tmp/packaging-windows"
    runtime, libredwg, compiler = prepare(cache)
    if args.prepare_only:
        print(f"Build tools ready: {cache}")
        return 0
    if args.wheel is None:
        parser.error("--wheel is required unless --prepare-only")
    payload = build(runtime, args.wheel, libredwg, args.payload, wheelhouse=args.wheelhouse)
    output = REPOSITORY / "dist/windows"
    output.mkdir(parents=True, exist_ok=True)
    import tomllib
    version = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    subprocess.run([
        str(compiler), f"/DPayloadDir={payload}", f"/DOutputDir={output}",
        f"/DAppVersion={version}", str(REPOSITORY / "packaging/windows/cad2gis.iss"),
    ], check=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
