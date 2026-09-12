"""Package a verified Linux runtime payload as a Debian installer and portable archive."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import tempfile
import tomllib

REPOSITORY = Path(__file__).resolve().parents[1]
PACKAGING = REPOSITORY / "packaging/linux"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Set safe, predictable package permissions even on a WSL Windows mount."""
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0
    if info.isdir():
        info.mode = 0o755
    elif info.issym() or info.islnk():
        info.mode = 0o777
    else:
        parts = Path(info.name).parts
        executable = "bin" in parts or info.name.endswith("/install.sh")
        info.mode = 0o755 if executable else 0o644
    return info


def _text(bundle: tarfile.TarFile, path: str, content: str, *, mode: int = 0o644) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(path)
    info.size = len(data)
    info.mode = mode
    info.uname = info.gname = "root"
    bundle.addfile(info, io.BytesIO(data))


def _link(bundle: tarfile.TarFile, path: str, target: str) -> None:
    info = tarfile.TarInfo(path)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    info.mode = 0o777
    info.uname = info.gname = "root"
    bundle.addfile(info)


SYSTEM_DIRECTORIES = (
    "./opt", "./usr", "./usr/bin", "./usr/share", "./usr/share/applications",
    "./usr/share/icons", "./usr/share/icons/hicolor", "./usr/share/icons/hicolor/scalable",
    "./usr/share/icons/hicolor/scalable/apps", "./usr/share/doc", "./usr/share/doc/cad2gis",
)


def _system_directories(bundle: tarfile.TarFile) -> None:
    # Unlike tar's generic extractor, dpkg does not create missing parent
    # directories for a file entry. Every package-owned parent must be listed.
    for name in SYSTEM_DIRECTORIES:
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.uname = info.gname = "root"
        bundle.addfile(info)


def _ar(destination: Path, members: list[Path]) -> None:
    """Write the standard Debian ar envelope without a host ar dependency."""
    with destination.open("wb") as stream:
        stream.write(b"!<arch>\n")
        for member in members:
            size = member.stat().st_size
            header = (f"{member.name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{size:<10}`\n")
            stream.write(header.encode("ascii"))
            with member.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    stream.write(chunk)
            if size % 2:
                stream.write(b"\n")


def validate_payload(payload: Path, manifest: dict) -> None:
    """Reject incomplete or changed bundles before producing installers."""
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Bundle has no file checksum inventory")
    for relative, digest in expected.items():
        path = payload / relative
        if (Path(relative).is_absolute() or ".." in Path(relative).parts
                or not path.resolve().is_relative_to(payload.resolve())):
            raise ValueError(f"Bundle inventory redirects outside the payload: {relative}")
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Bundle file is missing or changed: {relative}")
    for path in payload.rglob("*"):
        if path.is_symlink() and (not path.exists() or not path.resolve().is_relative_to(payload.resolve())):
            raise ValueError(f"Bundle contains an invalid symlink: {path.relative_to(payload)}")
        if path.is_file() and path != payload / "bundle-manifest.json":
            if path.relative_to(payload).as_posix() not in expected:
                raise ValueError(f"Bundle file is not in the inventory: {path.relative_to(payload)}")


def build(payload: Path, output: Path, version: str) -> dict:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Build and validate Linux x86_64 packages on Linux x86_64")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+~-]*", version):
        raise ValueError("Invalid Debian package version")
    payload = payload.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to(REPOSITORY):
        raise ValueError("Output must stay inside this repository")
    manifest = json.loads((payload / "bundle-manifest.json").read_text())
    if manifest.get("platform") != "linux" or not manifest.get("runtime_is_bundled"):
        raise ValueError("Input must be a native Linux runtime bundle")
    validate_payload(payload, manifest)
    for relative in ("bin/cad2gis", "bin/cad2gis-agent-mcp", "bin/cad2gis-desktop",
                     "runtime/bin/python3", "libredwg/bin/dwg2dxf"):
        if not (payload / relative).is_file():
            raise ValueError(f"Missing bundle entry: {relative}")
    entrypoints = manifest.get("entrypoints", {})
    if not isinstance(entrypoints, dict) or any(
        not re.fullmatch(r"cad2gis(?:-[a-z0-9]+)*", name) for name in entrypoints
    ):
        raise ValueError("Bundle entrypoints contain an invalid command name")
    output.mkdir(parents=True, exist_ok=True)
    deb = output / f"cad2gis_{version}_amd64.deb"
    portable = output / f"cad2gis-{version}-linux-x86_64.tar.gz"
    if deb.exists() or portable.exists():
        raise FileExistsError("Use a new output directory or a new package version")
    package_size = sum(p.stat().st_size for p in payload.rglob("*") if p.is_file()) // 1024
    control = (
        f"Package: cad2gis\nVersion: {version}\nArchitecture: amd64\nSection: science\n"
        "Priority: optional\nMaintainer: CAD2GIS contributors\n"
        f"Installed-Size: {package_size}\n"
        "Depends: libc6 (>= 2.39), libstdc++6, libgomp1, ca-certificates, fonts-dejavu-core\n"
        "Recommends: xdg-utils\nHomepage: https://github.com/Mola-maker/CAD2GIS\n"
        "Description: CAD conversion, geospatial matching and review workbench\n"
        " Bundled Python 3.12, GDAL and LibreDWG. Local browser workbench and MCP\n"
        " interfaces share one conversion engine. Built for Ubuntu 24.04 x86_64.\n"
    )
    with tempfile.TemporaryDirectory(prefix="deb-", dir=output) as temporary:
        temporary = Path(temporary)
        (temporary / "debian-binary").write_text("2.0\n", encoding="ascii")
        with tarfile.open(temporary / "control.tar.gz", "w:gz", compresslevel=6,
                          format=tarfile.GNU_FORMAT) as bundle:
            _text(bundle, "./control", control)
        # dpkg's filesystem unpacker accepts GNU long-name records, but rejects
        # PAX extended headers emitted by Python's default tar format.
        with tarfile.open(temporary / "data.tar.gz", "w:gz", compresslevel=6,
                          format=tarfile.GNU_FORMAT) as bundle:
            _system_directories(bundle)
            bundle.add(payload, arcname="./opt/cad2gis", filter=_tar_filter)
            for name in entrypoints:
                _link(bundle, f"./usr/bin/{name}", f"/opt/cad2gis/bin/{name}")
            bundle.add(PACKAGING / "cad2gis.desktop", "./usr/share/applications/cad2gis.desktop", filter=_tar_filter)
            bundle.add(PACKAGING / "cad2gis.svg", "./usr/share/icons/hicolor/scalable/apps/cad2gis.svg", filter=_tar_filter)
            bundle.add(REPOSITORY / "docs/INSTALL_LINUX.md", "./usr/share/doc/cad2gis/INSTALL_LINUX.md", filter=_tar_filter)
        _ar(deb, [temporary / "debian-binary", temporary / "control.tar.gz", temporary / "data.tar.gz"])
        # Reuse the exact payload bytes already archived for Debian. This also
        # avoids a second slow filesystem traversal on a Windows/WSL mount.
        root = f"cad2gis-{version}"
        prefix = "./opt/cad2gis"
        with tarfile.open(temporary / "data.tar.gz", "r:gz") as source, \
             tarfile.open(portable, "w:gz", compresslevel=6, format=tarfile.GNU_FORMAT) as bundle:
            for item in source:
                if item.name != prefix and not item.name.startswith(prefix + "/"):
                    continue
                contents = source.extractfile(item) if item.isfile() else None
                item.name = root + item.name[len(prefix):]
                if item.islnk() and item.linkname.startswith(prefix + "/"):
                    item.linkname = root + item.linkname[len(prefix):]
                bundle.addfile(item, contents)
                if contents:
                    contents.close()
            bundle.add(REPOSITORY / "docs/INSTALL_LINUX.md", f"{root}/INSTALL_LINUX.md", filter=_tar_filter)
    subprocess.run(["dpkg-deb", "--info", str(deb)], check=True)
    result = {
        "schema": "cad2gis.linux_installers.v1", "version": version,
        "target": "Ubuntu 24.04 x86_64; glibc 2.39 or newer",
        "payload_manifest_sha256": sha256(payload / "bundle-manifest.json"),
        "artifacts": [{"file": p.name, "sha256": sha256(p), "bytes": p.stat().st_size}
                      for p in (deb, portable)],
        "public_distribution_license_review": "required; see THIRD_PARTY_NOTICES.md",
    }
    (output / "linux-build.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (output / "SHA256SUMS").write_text(
        "".join(f"{item['sha256']}  {item['file']}\n" for item in result["artifacts"]), encoding="ascii")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "dist/linux")
    parser.add_argument("--version", default=tomllib.loads(
        (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
    args = parser.parse_args()
    print(json.dumps(build(args.payload, args.output, args.version), indent=2))
