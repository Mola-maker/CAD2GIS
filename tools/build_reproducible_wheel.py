"""Build a source-isolated, reproducible CAD2GIS wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


MINIMUM_ZIP_EPOCH = 315532800  # 1980-01-01 UTC
ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_date_epoch() -> str:
    configured = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if configured:
        value = int(configured)
    else:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%ct"],
            check=True,
            capture_output=True,
            text=True,
        )
        value = int(completed.stdout.strip())
    return str(max(MINIMUM_ZIP_EPOCH, value))


def _copy_source(destination: Path) -> None:
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", destination / "README.md")
    shutil.copytree(
        ROOT / "src",
        destination / "src",
        ignore=shutil.ignore_patterns(
            "*.egg-info",
            "__pycache__",
            "*.pyc",
            "*.pyo",
        ),
    )


def _build_once(output_directory: Path, epoch: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="cad2gis-wheel-source-") as temp_name:
        source = Path(temp_name) / "source"
        source.mkdir()
        _copy_source(source)
        environment = dict(os.environ)
        environment["SOURCE_DATE_EPOCH"] = epoch
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                str(source),
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(output_directory),
            ],
            check=True,
            cwd=temp_name,
            env=environment,
        )
    wheels = sorted(output_directory.glob("cad2gis-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"expected exactly one CAD2GIS wheel, found {len(wheels)}"
        )
    return wheels[0]


def build(output_directory: Path, *, verify: bool) -> dict[str, object]:
    output_directory = output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    for existing in output_directory.glob("cad2gis-*.whl"):
        existing.unlink()
    epoch = _source_date_epoch()
    wheel = _build_once(output_directory, epoch)
    digest = _sha256(wheel)
    result: dict[str, object] = {
        "wheel": str(wheel),
        "sha256": digest,
        "source_date_epoch": epoch,
        "reproducible": None,
    }
    if verify:
        with tempfile.TemporaryDirectory(prefix="cad2gis-wheel-verify-") as name:
            second = _build_once(Path(name), epoch)
            second_digest = _sha256(second)
        result["verification_sha256"] = second_digest
        result["reproducible"] = digest == second_digest
        if not result["reproducible"]:
            raise RuntimeError("consecutive isolated wheel builds are not identical")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--verify", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            build(arguments.output_dir, verify=arguments.verify),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
