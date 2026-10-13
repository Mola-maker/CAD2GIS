"""Build version-checked, hash-listed CAD2GIS Agent deliverables."""

from __future__ import annotations
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile
from cad2gis.contracts import MCP_TOOL_NAMES, PLUGIN_CONTRACT_VERSION

ROOT = Path(__file__).resolve().parents[1]
OWNER = "CAD2GIS-agent-delivery-v1"


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _zip(path, items):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(items.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)


def _files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Refusing symlink: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def build_agent_delivery(
    destination: Path, *, wheel: Path, sdist: Path, site: Path
) -> dict:
    destination = destination.resolve()
    if destination == ROOT or destination in ROOT.parents:
        raise ValueError("Unsafe delivery directory")
    marker = destination / ".cad2gis-delivery-owner"
    if destination.exists() and (not marker.is_file() or marker.read_text() != OWNER):
        raise ValueError("Refusing to overwrite a directory not owned by this builder")
    with zipfile.ZipFile(wheel) as z:
        metadata = BytesParser().parsebytes(
            z.read(next(n for n in z.namelist() if n.endswith(".dist-info/METADATA")))
        )
    version = PLUGIN_CONTRACT_VERSION
    if metadata["Version"] != version or metadata["License-Expression"] != "Apache-2.0":
        raise ValueError("Wheel version/license differs from release contract")
    if not sdist.is_file() or sdist.name != f"cad2gis-{version}.tar.gz":
        raise ValueError("Missing matching source distribution")
    if not site.is_dir():
        raise ValueError("Missing built site")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    marker.write_text(OWNER)
    common = {
        "LICENSE.md": (ROOT / "LICENSE.md").read_bytes(),
        "NOTICE.md": (ROOT / "NOTICE.md").read_bytes(),
        "THIRD_PARTY_NOTICES.md": (
            ROOT / "packaging/THIRD_PARTY_NOTICES.md"
        ).read_bytes(),
    }
    manifest = json.loads((ROOT / "packaging/mcpb/manifest.json").read_text())
    if manifest["version"] != version:
        raise ValueError("MCPB version mismatch")
    url = f"https://github.com/Mola-maker/CAD2GIS/releases/download/v{version}/{wheel.name}"
    manifest["tools"] = [
        {"name": name, "description": name.replace("_", " ")} for name in MCP_TOOL_NAMES
    ]
    bundle = {
        **common,
        "manifest.json": (json.dumps(manifest, indent=2) + "\n").encode(),
        "server/wheel.json": (
            json.dumps({"url": url, "filename": wheel.name, "sha256": _hash(wheel)})
            + "\n"
        ).encode(),
        "server/entrypoint.py": (
            ROOT / "packaging/mcpb/server/entrypoint.py"
        ).read_bytes(),
        "README.md": (ROOT / "plugins/cad2gis-agent/README.md").read_bytes(),
    }
    _zip(destination / f"cad2gis-agent-{version}.mcpb", bundle)
    _zip(
        destination / f"cad2gis-agent-{version}-plugin.zip",
        {**_files(ROOT / "plugins/cad2gis-agent"), **common},
    )
    _zip(destination / f"cad2gis-{version}-pages.zip", _files(site))
    shutil.copy2(wheel, destination / wheel.name)
    shutil.copy2(sdist, destination / sdist.name)
    _zip(
        destination / f"cad2gis-{version}-agent-delivery.zip",
        {
            **{
                p.name: p.read_bytes()
                for p in destination.iterdir()
                if p.is_file() and not p.name.startswith(".")
            },
            "CAD_BUNDLES.md": (ROOT / "docs/CAD_BUNDLES.md").read_bytes(),
            **common,
        },
    )
    artifacts = [
        {"file": p.name, "sha256": _hash(p), "bytes": p.stat().st_size}
        for p in sorted(destination.iterdir())
        if p.is_file() and not p.name.startswith(".")
    ]
    result = {
        "schema": "cad2gis-release-v1",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "validation_scope": {
            "platform": "Linux x86_64 / Python 3.12",
            "desktop_binaries": "not_rebuilt",
            "evidence": [
                "audit/pytest-full.txt",
                "audit/final-real-sidecar.txt",
                "audit/mcpb-isolation-tests.txt",
                "audit/review-fixes-green.txt",
            ],
            "external_corpus": "not_available",
            "absolute_accuracy_verified": False,
        },
        "git_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip()
        ),
        "version": version,
        "license": "Apache-2.0",
        "wheel_url": url,
        "artifacts": artifacts,
        "mcpb_mode": "hash-pinned-release-wheel",
        "contains_new_nine_report": False,
    }
    (destination / "release-manifest.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    (destination / "SHA256SUMS").write_text(
        "".join(f"{item['sha256']}  {item['file']}\n" for item in artifacts)
        + f"{_hash(destination / 'release-manifest.json')}  release-manifest.json\n"
    )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("destination", type=Path)
    p.add_argument("--wheel", type=Path, required=True)
    p.add_argument("--sdist", type=Path, required=True)
    p.add_argument("--site", type=Path, required=True)
    a = p.parse_args()
    print(
        json.dumps(
            build_agent_delivery(
                a.destination,
                wheel=a.wheel.resolve(),
                sdist=a.sdist.resolve(),
                site=a.site.resolve(),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
