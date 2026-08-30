"""Reader-authoritative source export with no semantic or GIS conversion."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .project_profile import _inspect_source_products, inventory_sha256
from .scene_visual import build_scene_visual_bundle
from .source_gpkg import write_source_gpkg


SOURCE_EXPORT_SCHEMA_VERSION = "cad2gis.source_export.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_source(
    *,
    source: str | Path,
    run_dir: str | Path,
    source_crs: str | None = None,
    force: bool = False,
    records: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Materialize CAD facts and stop before interpretation or registration."""

    root = Path(run_dir).expanduser().resolve()
    paths = {
        "source_gpkg": root / "source.gpkg",
        "source_inventory": root / "review" / "source_inventory.json",
        "cad_scene_graph": root / "review" / "cad_scene_graph.json",
        "source_manifest": root / "source_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "Source export already contains managed files; pass force=True to replace only "
            + ", ".join(str(path) for path in existing)
        )
    root.mkdir(parents=True, exist_ok=True)
    for path in existing:
        path.unlink()

    inventory, graph, entities = _inspect_source_products(
        source=source,
        records=records,
    )
    scene_visual = build_scene_visual_bundle(
        graph=graph,
        entities=entities,
        scene_candidates=(inventory.get("plan_domain") or {}).get(
            "scene_partition", {}
        ),
    )
    inventory["scene_visual"] = {
        "schema_version": "cad2gis.scene_visual_bundle.v1",
        "manifest_sha256": scene_visual.manifest_file.sha256,
        "relative_path": scene_visual.manifest_relative_path,
        "region_count": scene_visual.region_count,
        "layout_count": scene_visual.layout_count,
        "render_conserved": scene_visual.render_conserved,
        "cad_scene_graph_sha256": graph.graph_sha256,
    }
    inventory["inventory_sha256"] = inventory_sha256(inventory)

    result = write_source_gpkg(paths["source_gpkg"], entities, source_crs)
    _write_json(paths["source_inventory"], inventory)
    _write_json(paths["cad_scene_graph"], graph.to_dict())
    scene_manifest_path = scene_visual.write(root)
    manifest = {
        "schema_version": SOURCE_EXPORT_SCHEMA_VERSION,
        "status": "SOURCE_EXPORTED",
        "pipeline_boundary": "source_facts_only",
        "source": dict(inventory["source"]),
        "reader_protocol": dict(inventory.get("reader_protocol") or {}),
        "coordinate_reference": {
            "state": "declared" if source_crs else "native_cad_unregistered",
            "source_crs": source_crs,
            "transformed": False,
            "warning": None if source_crs else (
                "Coordinates are preserved in native CAD space and are not map-registered."
            ),
        },
        "entity_count": result.entity_count,
        "layer_counts": dict(result.layer_counts),
        "conservation": {
            "reader_records": len(entities),
            "gpkg_entities": result.entity_count,
            "difference": result.entity_count - len(entities),
            "passed": result.entity_count == len(entities),
        },
        "excluded_stages": [
            "semantic_mapping",
            "topology_repair",
            "length_inference",
            "crs_transformation",
            "gcp_registration",
            "delivery_publication",
        ],
        "artifacts": {
            "source_gpkg": {
                "path": str(paths["source_gpkg"]),
                "sha256": _sha256(paths["source_gpkg"]),
                "logical_sha256": result.logical_sha256,
            },
            "source_inventory": str(paths["source_inventory"]),
            "cad_scene_graph": str(paths["cad_scene_graph"]),
            "scene_visual_manifest": str(scene_manifest_path),
        },
    }
    _write_json(paths["source_manifest"], manifest)
    return manifest
