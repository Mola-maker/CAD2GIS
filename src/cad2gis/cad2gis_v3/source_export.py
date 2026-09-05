"""Immutable source-facts publication, independent of semantic conversion."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .model import SourceEntity
from .project_profile import _extract_records, _inspect_source_facts
from .source_gpkg import _lossless_json_value, write_source_gpkg

SOURCE_EXPORT_SCHEMA_VERSION = "cad2gis.source_export.v1"


def _json(value: Any) -> str:
    return json.dumps(_lossless_json_value(value), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(_json(payload) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def snapshot_digest(manifest: Mapping[str, Any]) -> str:
    """Bind authority, coordinates and every artifact, independently of location."""
    payload = {
        "schema_version": manifest["schema_version"],
        "source_sha256": manifest["source"]["sha256"],
        "reader_provenance": manifest["reader_provenance"],
        "coordinate_reference": manifest["coordinate_reference"],
        "entity_count": manifest["entity_count"],
        "artifacts": {key: value["sha256"] for key, value in manifest["artifacts"].items()},
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def export_source(
    *, source: str | Path, run_dir: str | Path,
    source_crs: str | None = None, records: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Publish a complete snapshot by one directory rename; never overwrite runs.

    ``records`` is an explicit simulation/import port. Its provenance is kept
    distinct from the native reader, even if it supplies complete diagnostics.
    Failed staging directories remain as audit evidence, outside the run path.
    """
    source_path = Path(source).expanduser().resolve()
    root = Path(run_dir).expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(f"Source snapshot destination must be absent or empty: {root}")
    if not source_path.is_file() or source_path.suffix.lower() != ".dwg":
        raise ValueError(f"Source must be an existing DWG: {source_path}")
    if source_crs is not None and not str(source_crs).strip():
        raise ValueError("source_crs must be None or a nonblank declaration")
    source_hash = _sha256(source_path)
    authoritative = _extract_records(source_path) if records is None else records
    diagnostics = dict(getattr(authoritative, "diagnostics", {}) or {})
    if records is None and (diagnostics.get("inventory_complete") is not True
                            or int(diagnostics.get("skipped_rows", 0) or 0)):
        raise ValueError("Native reader inventory is not authoritative/complete")
    materialized = list(authoritative)
    # The original reader rows remain separately persisted, including fields
    # which the typed GIS projection does not currently understand.
    raw_rows = [asdict(row) if isinstance(row, SourceEntity) else dict(row)
                for row in materialized]
    entities = []
    for row in materialized:
        entity = row if isinstance(row, SourceEntity) else SourceEntity.from_record(dict(row))
        if not entity.entity_key:
            raise ValueError("Every source entity requires a stable entity_key")
        if entity.source_sha256 and entity.source_sha256 != source_hash:
            raise ValueError("Reader entity is bound to another source SHA-256")
        entities.append(replace(entity, source_sha256=source_hash))
    inventory, graph, entities, plan_entities = _inspect_source_facts(source=source_path, records=entities)
    provenance = {
        "mode": "native_reader" if records is None else "injected_records",
        "authority": "reader_complete" if records is None else "simulation_or_caller_supplied",
        "diagnostics": diagnostics,
        "records_schema": "cad2gis.reader_record_jsonl.v1",
        "record_representation": "reader_mapping_or_source_entity_dataclass",
    }
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.staged-", dir=root.parent))
    try:
        review = staging / "review"
        review.mkdir()
        rows_path = staging / "reader_records.jsonl"
        with rows_path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in raw_rows:
                stream.write(_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        with (staging / "plan_entities.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for entity in plan_entities:
                stream.write(_json(asdict(entity)) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        result = write_source_gpkg(staging / "source.gpkg", entities, source_crs)
        _write_json(review / "source_inventory.json", inventory)
        _write_json(review / "cad_scene_graph.json", graph.to_dict())
        artifacts = {}
        for name, relative in {
            "reader_records": "reader_records.jsonl", "source_gpkg": "source.gpkg",
            "plan_entities": "plan_entities.jsonl",
            "source_inventory": "review/source_inventory.json",
            "cad_scene_graph": "review/cad_scene_graph.json",
        }.items():
            artifacts[name] = {"path": str(root / relative), "sha256": _sha256(staging / relative)}
        artifacts["source_gpkg"]["logical_sha256"] = result.logical_sha256
        manifest = {
            "schema_version": SOURCE_EXPORT_SCHEMA_VERSION,
            "status": "SOURCE_EXPORTED", "pipeline_boundary": "source_facts_only",
            "source": dict(inventory["source"]), "reader_provenance": provenance,
            "reader_protocol": diagnostics,
            "coordinate_reference": {
                "state": "declared" if source_crs is not None else "native_cad_unregistered",
                "source_crs": source_crs, "units": "not_inferred", "transformed": False,
            },
            "entity_count": result.entity_count, "layer_counts": result.layer_counts,
            "conservation": {"reader_records": len(entities), "gpkg_entities": result.entity_count,
                             "difference": result.entity_count - len(entities),
                             "passed": result.entity_count == len(entities)},
            "scene": {"graph_sha256": graph.graph_sha256,
                      "plan_entity_count": len(plan_entities),
                      "plan_domain_status": inventory.get("inspection_status"),
                      "authority": graph.diagnostics["authority"]},
            "artifacts": artifacts,
        }
        if not manifest["conservation"]["passed"]:
            raise RuntimeError("Source entity conservation failed")
        if _sha256(source_path) != source_hash:
            raise ValueError("Source DWG changed during export")
        manifest["snapshot_sha256"] = snapshot_digest(manifest)
        _write_json(staging / "source_manifest.json", manifest)
        # No destination files are visible before all products and manifest
        # have been closed, hashed and validated. rmdir fails on a raced write.
        if root.exists():
            root.rmdir()
        os.rename(staging, root)
        return manifest
    except Exception as exc:
        _write_json(staging / "_failure.json", {
            "status": "FAILED", "error_type": type(exc).__name__, "message": str(exc),
            "source_sha256": source_hash, "published": False,
        })
        raise
