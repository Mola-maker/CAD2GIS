"""Validate the user-authorized derived release before publishing any bytes."""
import hashlib
import json
import zipfile
from pathlib import Path


def verify(root: Path) -> dict:
    manifest = json.loads((root / "publication.json").read_text(encoding="utf-8"))
    if (manifest.get("authorization") != "user-request-2026-09-06-nine-drawing-qgis-pages"
            or manifest.get("raw_dwg_included") is not False
            or manifest.get("absolute_accuracy_verified") is not False
            or manifest.get("drawing_count") != 9):
        raise ValueError("Derived publication authorization or accuracy boundary missing")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != "publication.json"}
    if actual != set(manifest["files"]):
        raise ValueError("Release has missing or unlisted files")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or path.suffix.lower() in {".dwg", ".dxf"}:
            raise ValueError("Raw source or escaped release path")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"Release hash mismatch: {name}")
        if path.suffix.lower() in {".zip", ".qgz"}:
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if Path(member).suffix.lower() in {".dwg", ".dxf"} or ".." in Path(member).parts or member.startswith("/"):
                        raise ValueError("Unsafe/raw archive member")
    catalog = json.loads((root / "assets" / "catalog.json").read_text(encoding="utf-8"))
    primary = [p for p in catalog["projects"] if not p.get("parent_project_id")]
    if len(primary) != 9 or {p["id"] for p in primary} != {f"drawing-{i:02}" for i in range(1, 10)}:
        raise ValueError("Nine-drawing catalog is incomplete")
    children = [p for p in catalog["projects"] if p.get("parent_project_id")]
    if children and (len(children) != 2 or {p["id"] for p in children} != {"drawing-03-emr28560", "drawing-03-emr29619"}
                     or any(p["parent_project_id"] != "drawing-03" for p in children)):
        raise ValueError("Unexpected partition catalog")
    for project in catalog["projects"]:
        fixture = json.loads((root / "assets" / project["fixture"]).read_text(encoding="utf-8"))
        if fixture["provenance"]["source_sha256"] != project["source_sha256"]:
            raise ValueError("Fixture source binding mismatch")
        if fixture["run"]["run_status"] != "CONDITIONAL":
            raise ValueError("Historical release must retain CONDITIONAL status")
        if sum(len(v["features"]) for v in fixture["layers"].values()) != project["delivery_feature_count"]:
            raise ValueError("Incomplete fixture geometry")
    return catalog
