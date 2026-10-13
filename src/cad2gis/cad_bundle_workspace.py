"""Source-preserving CAD workspaces with hash-bound, immutable revisions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .cad_bundle_policy import MAX_DXF_BYTES, UPSTREAM_SHA

SCHEMA = "cad2gis.cad_bundle_workspace.v1"


def safe_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("CAD workspace paths cannot contain a symlink")
    path = path.resolve()
    if "," in str(path):
        raise ValueError("CAD workspace paths cannot contain a comma (ALLOWED_PATHS limitation)")
    return path


def sha256(path: Path) -> str:
    with safe_path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_dxf(path: Path) -> Path:
    import ezdxf
    path = safe_path(path)
    if not path.is_file() or path.suffix.lower() != ".dxf":
        raise ValueError("Expected an existing DXF; export DWG with the CAD2GIS reader first")
    if path.stat().st_size > MAX_DXF_BYTES:
        raise ValueError("DXF exceeds the 512 MiB size limit")
    try:
        ezdxf.readfile(path)
    except (OSError, ValueError, ezdxf.DXFError) as exc:
        raise ValueError(f"Invalid DXF: {exc}") from exc
    return path


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path = safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def create_workspace(source: Path, workspace: Path) -> dict[str, Any]:
    source = validate_dxf(source)
    workspace = safe_path(workspace)
    source_hash = sha256(source)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir()  # Exclusive creation; never adopt an existing directory.
    (workspace / "revisions").mkdir()
    (workspace / "evidence").mkdir()
    shutil.copyfile(source, workspace / "source.dxf")
    if sha256(workspace / "source.dxf") != source_hash or sha256(source) != source_hash:
        raise ValueError("Source changed while preparing the CAD workspace")
    shutil.copyfile(workspace / "source.dxf", workspace / "revisions/000000.dxf")
    for file in (workspace / "source.dxf", workspace / "revisions/000000.dxf"):
        file.chmod(0o444)
    manifest = {
        "schema_version": SCHEMA, "workspace_id": str(workspace),
        "source": str(source), "source_sha256": source_hash,
        "current_revision": 0, "current_sha256": source_hash,
        "upstream_sha": UPSTREAM_SHA,
    }
    _write_json(workspace / "manifest.json", manifest)
    return manifest


def load_workspace(workspace: Path) -> dict[str, Any]:
    workspace = safe_path(workspace)
    manifest_path = safe_path(workspace / "manifest.json")
    if manifest_path.stat().st_size > 64 * 1024:
        raise ValueError("CAD workspace manifest exceeds its size limit")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
        raise ValueError("Not a CAD2GIS CAD workspace")
    revision = data.get("current_revision")
    if (data.get("workspace_id") != str(workspace) or data.get("upstream_sha") != UPSTREAM_SHA
            or type(revision) is not int or not 0 <= revision < 1_000_000):
        raise ValueError("Invalid CAD workspace identity or revision")
    if sha256(workspace / "source.dxf") != data.get("source_sha256"):
        raise ValueError("CAD source snapshot hash mismatch")
    if sha256(workspace / f"revisions/{revision:06d}.dxf") != data.get("current_sha256"):
        raise ValueError("CAD current revision hash mismatch")
    return data


def commit_revision(workspace: Path, staged_dxf: Path, expected_revision: int,
                    evidence: dict[str, Any]) -> dict[str, Any]:
    workspace = safe_path(workspace)
    staged_dxf = validate_dxf(staged_dxf)
    if not staged_dxf.is_relative_to(workspace):
        raise ValueError("Staged DXF must belong to the workspace")
    lock = workspace / ".revision-lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("CAD workspace revision is busy") from exc
    try:
        data = load_workspace(workspace)
        if data["current_revision"] != expected_revision:
            raise ValueError("CAD workspace revision changed; retry from the latest revision")
        revision = expected_revision + 1
        destination = safe_path(workspace / f"revisions/{revision:06d}.dxf")
        # The exclusive write preserves old evidence even after an interrupted publication.
        with staged_dxf.open("rb") as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        destination.chmod(0o444)
        data.update(current_revision=revision, current_sha256=sha256(destination))
        _write_json(workspace / f"evidence/{revision:06d}.json", {
            **evidence, "before_sha256": load_workspace(workspace)["current_sha256"],
            "after_sha256": data["current_sha256"], "revision": revision,
        })
        _write_json(workspace / "manifest.json", data)
        return data
    finally:
        lock.rmdir()
