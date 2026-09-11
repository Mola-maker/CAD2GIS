"""Rebase a copied source snapshot after verifying every sealed artifact.

Run on an extracted, trusted CAD2GIS source bundle. Native facts, artifact
hashes, reader identity and snapshot identity remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import os
import tempfile

from cad2gis.cad2gis_v3.source_export import SOURCE_EXPORT_SCHEMA_VERSION, snapshot_digest
from cad2gis.cad2gis_v3.source_query import validate_source_snapshot


ARTIFACTS = {
    "reader_records": "reader_records.jsonl",
    "source_gpkg": "source.gpkg",
    "source_inventory": "review/source_inventory.json",
    "cad_scene_graph": "review/cad_scene_graph.json",
    "plan_entities": "plan_entities.jsonl",
}


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relocate(root: Path) -> dict:
    root = root.resolve()
    manifest_path = root / "source_manifest.json"
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    if (manifest.get("schema_version") != SOURCE_EXPORT_SCHEMA_VERSION
            or manifest.get("status") != "SOURCE_EXPORTED"
            or manifest.get("snapshot_sha256") != snapshot_digest(manifest)):
        raise ValueError("Invalid source snapshot seal")
    paths = dict(ARTIFACTS)
    if "scene_visual_manifest" in manifest["artifacts"]:
        paths["scene_visual_manifest"] = "reasoning/scene_visual/manifest.json"
    if set(manifest["artifacts"]) != set(paths):
        raise ValueError("Unknown or missing source artifacts; use a compatible relocator")
    changes = []
    for name, relative in paths.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Artifact is missing or outside snapshot: {name}")
        entry = manifest["artifacts"][name]
        if file_sha(path) != entry["sha256"]:
            raise ValueError(f"Artifact hash mismatch: {name}")
        if entry["path"] != str(path):
            changes.append({"artifact": name, "old_path": entry["path"], "new_path": str(path)})
        entry["path"] = str(path)
    if manifest["snapshot_sha256"] != snapshot_digest(manifest):
        raise ValueError("Relocation must not alter the sealed snapshot identity")
    receipt = {"schema_version": "cad2gis.source_relocation.v1", "root": str(root),
               "snapshot_sha256": manifest["snapshot_sha256"], "artifact_hashes_verified": True,
               "facts_changed": False, "changes": changes}
    if changes:
        prior_hash = hashlib.sha256(original).hexdigest()
        backup = root / f"source_manifest.before-relocation-{prior_hash}.json"
        if backup.exists():
            if backup.read_bytes() != original:
                raise ValueError("Existing relocation backup does not match")
        else:
            with backup.open("xb") as stream:
                stream.write(original)
        fd, name = tempfile.mkstemp(prefix=".source-manifest-", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, manifest_path)
        finally:
            temporary.unlink(missing_ok=True)
        receipt["previous_manifest_sha256"] = prior_hash
        (root / f"relocation-{prior_hash}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_source_snapshot(root)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(relocate(args.run_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
