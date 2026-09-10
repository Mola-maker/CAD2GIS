"""Discover every declared GIS district without treating print layouts as districts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return value


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _counts(value: object) -> dict:
    if (not isinstance(value, dict) or not value
            or any(not isinstance(k, str) or not k or type(v) is not int or v < 0
                   for k, v in value.items())):
        raise ValueError("District feature counts are invalid")
    return value


def delivery_scopes(directory: Path) -> dict:
    """Return source-bound sibling choices, retaining unavailable declarations.

    Only manifest-declared children are offered. Opening a choice still goes
    through the application's ordinary path, artifact and session admission.
    Historical absolute paths may be relocated, but never redirect this scan.
    """
    directory = directory.resolve()
    parent = directory
    selected = "MAIN"
    if not (parent / "run_manifest.json").is_file():
        if not (parent / "partition_manifest.json").is_file():
            return {"selected_scope": "MAIN", "scopes": []}
        selected = directory.name
        parent = directory.parent
    manifest = _read(parent / "run_manifest.json")
    own_partition = manifest.get("delivery_partition") or {}
    main_id = own_partition.get("region_id") or "MAIN"
    if parent == directory:
        selected = main_id
    scopes = [{"region_id": main_id,
               "label": own_partition.get("label") or ("主图" if main_id == "MAIN" else main_id),
               "run_dir": str(parent), "available": True,
               "delivery_counts": manifest.get("delivery_counts", {}),
               "run_status": manifest.get("run_status", "UNKNOWN")}]
    partitions = manifest.get("delivery_partitions") or {}
    if not isinstance(partitions, dict):
        raise ValueError("Run delivery_partitions must be an object")
    seen = {str(main_id).casefold()}
    for region, entry in partitions.items():
        if (not isinstance(region, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", region)
                or region.casefold() in seen):
            raise ValueError("Run contains an unsafe or duplicate district identifier")
        seen.add(region.casefold())
        child = parent / region
        item = {"region_id": region, "label": region, "run_dir": str(child),
                "available": False, "delivery_counts": {}}
        try:
            if not isinstance(entry, dict):
                raise ValueError("District entry must be an object")
            item["delivery_counts"] = _counts(entry.get("delivery_counts"))
            declared = str(entry.get("path", ""))
            path = PureWindowsPath(declared) if "\\" in declared else PurePosixPath(declared)
            if path.name != "delivery.gpkg" or path.parent.name != region:
                raise ValueError("District path does not match its identifier")
            if child.resolve() != child:
                raise ValueError("District directory redirects outside its declared location")
            part = _read(child / "partition_manifest.json")
            if part.get("region_id") != region or _counts(part.get("delivery_counts")) != item["delivery_counts"]:
                raise ValueError("District manifest does not match the parent counts or identifier")
            digest = entry.get("sha256", "")
            if (not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
                    or _digest(child / "delivery.gpkg") != digest.lower()):
                raise ValueError("District GeoPackage does not match its recorded SHA-256")
            item.update(available=True, label=part.get("label") or region,
                        run_status=part.get("run_status", manifest.get("run_status", "UNKNOWN")))
        except (OSError, ValueError) as exc:
            item["error"] = str(exc)
        scopes.append(item)
    if selected not in {item["region_id"] for item in scopes}:
        raise ValueError("Selected district is not declared by its parent run")
    return {"selected_scope": selected, "scopes": scopes,
            "source_sha256": (manifest.get("source") or {}).get("sha256"),
            "parent_run_dir": str(parent)}
