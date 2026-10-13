"""Verify the release wheel before installation; stdout is reserved for MCP."""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.request import urlopen

MAX_WHEEL_BYTES = 128 * 1024 * 1024


def download_verified_wheel(url: str, destination: Path, expected_sha256: str) -> Path:
    if len(expected_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in expected_sha256
    ):
        raise ValueError("Invalid wheel SHA-256")
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError("Wheel cache cannot contain symlinks")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_file():
        with destination.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == expected_sha256:
                return destination
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as target:
            temporary = Path(target.name)
            digest = hashlib.sha256()
            size = 0
            with urlopen(url, timeout=60) as response:
                while chunk := response.read(512 * 1024):
                    size += len(chunk)
                    if size > MAX_WHEEL_BYTES:
                        raise ValueError("Wheel exceeds size limit")
                    digest.update(chunk)
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if digest.hexdigest() != expected_sha256:
            raise ValueError("Wheel SHA-256 mismatch; refusing installation")
        if destination.exists():
            destination.chmod(0o600)
        os.replace(temporary, destination)
        destination.chmod(0o444)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    if sys.argv[1:] == ["--installed"]:
        from importlib.metadata import version

        if version("cad2gis") != "0.8.0":
            raise ValueError("Installed CAD2GIS version differs from MCPB")
        from cad2gis.agent_mcp import main as serve

        serve(["--transport", "stdio"])
        return
    config = json.loads(Path(__file__).with_name("wheel.json").read_text())
    cache_root = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_CACHE_HOME")
        or str(Path.home() / ".cache")
    )
    destination = (
        cache_root / "cad2gis-agent" / "wheels" / config["sha256"] / config["filename"]
    )
    wheel = download_verified_wheel(config["url"], destination, config["sha256"])
    uv = shutil.which("uv")
    if not uv:
        raise ValueError(
            "Install uv and make it available on PATH before starting MCPB"
        )
    args = [
        uv,
        "run",
        "--no-project",
        "--python",
        "3.12",
        "--with",
        f"cad2gis[agent] @ {wheel.as_uri()}",
        "python",
        str(Path(__file__).resolve()),
        "--installed",
    ]
    os.execvpe(uv, args, os.environ)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CAD2GIS MCPB startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
