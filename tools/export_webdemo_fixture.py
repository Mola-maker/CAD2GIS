"""Export a reviewed delivery run into a browser-safe WebDemo fixture.

The public page receives derived delivery geometry and selected evidence only.
The source DWG, GeoPackages, absolute workstation paths, and full evidence graph
are intentionally excluded from the exported JSON.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from osgeo import ogr, osr


LAYER_NAMES = (
    "SITE",
    "BOITE",
    "PTECH",
    "IMB",
    "INFRASTRUCTURE",
    "CABLE",
    "ZPM",
    "ZNRO",
)
FIXTURE_KIND = "CAD2GIS_DERIVED_FIXTURE"
PUBLICATION_BOUNDARY = (
    "公开页面仅含真实转换的筛选派生证据，不包含任何 DWG/GPKG 原始文件"
)
SAFE_FIELDS = (
    "CODE",
    "NOM",
    "TYPE",
    "STATUT",
    "REF_NRO",
    "REF_PM",
    "CAPACITE",
    "NB_PRISES",
    "LONGUEUR",
    "display_label",
    "label_provenance",
    "source_entity_key",
    "source_handle",
    "source_layer",
    "geometry_role",
    "route_key",
    "segment_index",
    "source_segment_key",
    "source_segment_kind",
    "native_length_source",
    "source_native_length_m",
    "measurement_state",
    "measurement_native_m",
    "measurement_delta_m",
    "length_value_m",
    "length_label",
    "length_source",
    "unit",
    "parent_cable_code",
    "parent_display_label",
    "delivery_grid_length_m",
    "geodesic_length_m",
)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, str, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _public_value(value: Any) -> Any:
    """Remove workstation paths from nested manifest evidence."""

    if isinstance(value, dict):
        return {
            key: _public_value(item)
            for key, item in value.items()
            if key not in {"source_path", "delivery_path", "evidence_path", "run_dir"}
        }
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, str) and any(
        marker in value for marker in ("E:\\", "C:\\", "/home/")
    ):
        return "[private path removed]"
    return value


def _feature_id(properties: dict[str, Any], layer_name: str, fid: int) -> str:
    value = properties.get("source_entity_key") or properties.get("CODE")
    return str(value) if value not in (None, "") else f"{layer_name.lower()}:{fid}"


def _export_layer(layer: ogr.Layer, layer_name: str) -> list[dict[str, Any]]:
    definition = layer.GetLayerDefn()
    available = {
        definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())
    }
    features: list[dict[str, Any]] = []
    for item in layer:
        geometry = item.GetGeometryRef()
        if geometry is None or geometry.IsEmpty():
            continue
        properties = {
            name: _json_value(item.GetField(name))
            for name in SAFE_FIELDS
            if name in available and item.GetField(name) is not None
        }
        properties["feature_class"] = layer_name
        feature_id = _feature_id(properties, layer_name, item.GetFID())
        properties.setdefault("source_entity_key", feature_id)
        features.append(
            {
                "type": "Feature",
                "id": feature_id,
                "geometry": json.loads(
                    geometry.ExportToJson(options=["COORDINATE_PRECISION=3"])
                ),
                "properties": properties,
            }
        )
    return sorted(features, key=lambda feature: str(feature["id"]))


def _center_lon_lat(dataset: ogr.DataSource, extent: list[float]) -> list[float]:
    source = dataset.GetLayerByIndex(0).GetSpatialRef()
    if source is None:
        source = osr.SpatialReference()
        source.ImportFromEPSG(3857)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)
    x = (extent[0] + extent[1]) / 2
    y = (extent[2] + extent[3]) / 2
    lon, lat, _ = transform.TransformPoint(x, y)
    return [round(lon, 6), round(lat, 6)]


def _map_anchor(
    anchor_path: Path | None,
    delivery_extent: list[float],
) -> dict[str, Any] | None:
    if anchor_path is None:
        return None
    anchor = json.loads(anchor_path.expanduser().resolve().read_text(encoding="utf-8"))
    center = anchor.get("target_epsg3857_centre")
    if not isinstance(center, list) or len(center) < 2:
        raise ValueError("map anchor must define target_epsg3857_centre")
    source = osr.SpatialReference()
    source.ImportFromEPSG(3857)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    lon, lat, _ = osr.CoordinateTransformation(source, target).TransformPoint(
        float(center[0]), float(center[1])
    )
    delivery_centre = [
        (delivery_extent[0] + delivery_extent[1]) / 2,
        (delivery_extent[2] + delivery_extent[3]) / 2,
    ]
    local_centre = anchor.get("local_centre")
    already_positioned = False
    if isinstance(local_centre, list) and len(local_centre) >= 2:
        distance_to_target = math.hypot(
            delivery_centre[0] - float(center[0]),
            delivery_centre[1] - float(center[1]),
        )
        distance_to_local = math.hypot(
            delivery_centre[0] - float(local_centre[0]),
            delivery_centre[1] - float(local_centre[1]),
        )
        already_positioned = distance_to_target <= distance_to_local
    nominal_transform = (
        {"a": 1.0, "b": 0.0, "tx": 0.0, "ty": 0.0}
        if already_positioned
        else {
            "a": 1.0,
            "b": 0.0,
            "tx": float(anchor.get("translation_dx", 0.0)),
            "ty": float(anchor.get("translation_dy", 0.0)),
        }
    )
    return {
        "place_name": anchor.get("place_name"),
        "display_name": anchor.get("display_name"),
        "precision": anchor.get("precision"),
        "source": anchor.get("source"),
        "map_center_lonlat": [round(lon, 6), round(lat, 6)],
        "geometry_positioning": (
            "delivery_already_in_epsg3857"
            if already_positioned
            else "coarse_anchor_translation"
        ),
        "nominal_transform": nominal_transform,
    }


def _run_summary(
    manifest: dict[str, Any],
    source_name: str,
    center: list[float],
    layer_count: int,
    *,
    project_id: str,
    project_name: str,
    anchor: dict[str, Any] | None,
) -> dict[str, Any]:
    crs = manifest.get("crs", {})
    validation = manifest.get("validation", {})
    source = manifest.get("source", {})
    source_sha256 = str(source.get("sha256", ""))
    return {
        "schema_version": manifest.get("schema_version", "cad2gis-run-manifest-v4"),
        "run_status": manifest.get("run_status", "CONDITIONAL"),
        "pipeline": manifest.get("pipeline"),
        "source": {
            "path": f"demo://{source_name}",
            "sha256": source_sha256,
        },
        "source_available": False,
        "source_blocker": (
            "公开页面包含该 DWG 的筛选派生交付数据，但不上传源 DWG、GeoPackage "
            "或本机路径；真实转换仍需在本地运行"
        ),
        "crs": {
            "source_crs": crs.get("source_crs"),
            "target_crs": crs.get("target_crs"),
            "operation": crs.get("operation"),
            "coordinate_domain": _public_value(crs.get("coordinate_domain")),
            "lineage": _public_value(crs.get("lineage")),
        },
        "artifacts": {"delivery": f"demo://{project_id}-derived-delivery"},
        "delivery_counts": manifest.get("delivery_counts", {}),
        "delivery_contract_gate": manifest.get("delivery_contract_gate"),
        "source_entity_count": manifest.get("source_entity_count"),
        "source_route_components": manifest.get("source_route_components"),
        "unresolved_count": manifest.get("unresolved_count"),
        "validation": {
            key: _public_value(validation.get(key))
            for key in (
                "source_geometry",
                "topology",
                "measurements",
                "segment_delivery",
                "coordinate_accuracy",
            )
            if key in validation
        },
        "reasoning": {
            key: _public_value(manifest.get("reasoning", {}).get(key))
            for key in (
                "architecture",
                "graph_schema_version",
                "graph_sha256",
                "node_count",
                "edge_count",
                "visual_evidence",
            )
            if key in manifest.get("reasoning", {})
        },
        "review_store": "browser-memory",
        "demo": {
            "fixture_kind": FIXTURE_KIND,
            "project_id": project_id,
            "project_name": project_name,
            "nominal_map_preview": True,
            "map_center_lonlat": (
                anchor["map_center_lonlat"] if anchor is not None else center
            ),
            "nominal_transform": (
                anchor["nominal_transform"]
                if anchor is not None
                else {"a": 1.0, "b": 0.0, "tx": 0.0, "ty": 0.0}
            ),
            "map_anchor": {
                key: anchor.get(key)
                for key in (
                    "place_name",
                    "display_name",
                    "precision",
                    "source",
                    "geometry_positioning",
                )
            } if anchor is not None else None,
            "layer_count": layer_count,
            "publication_boundary": PUBLICATION_BOUNDARY,
        },
    }


def export_fixture(
    run_dir: Path,
    output: Path,
    *,
    project_id: str = "hutabohu",
    project_name: str = "Hutabohu",
    map_anchor: Path | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    manifest_path = run_dir / "run_manifest.json"
    delivery_path = run_dir / "delivery.gpkg"
    if not manifest_path.is_file() or not delivery_path.is_file():
        raise FileNotFoundError("run_dir must contain run_manifest.json and delivery.gpkg")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = ogr.Open(str(delivery_path), 0)
    if dataset is None:
        raise RuntimeError(f"Unable to open delivery GeoPackage: {delivery_path}")

    layers: dict[str, dict[str, Any]] = {}
    extent: list[float] | None = None
    for name in LAYER_NAMES:
        layer = dataset.GetLayerByName(name)
        if layer is None:
            raise ValueError(f"Required delivery layer is missing: {name}")
        features = _export_layer(layer, name)
        layers[name] = {"type": "FeatureCollection", "features": features}
        layer_extent = layer.GetExtent()
        if features:
            if extent is None:
                extent = list(layer_extent)
            else:
                extent = [
                    min(extent[0], layer_extent[0]),
                    max(extent[1], layer_extent[1]),
                    min(extent[2], layer_extent[2]),
                    max(extent[3], layer_extent[3]),
                ]
    if extent is None:
        raise ValueError("The delivery run contains no public geometry")

    source_name = Path(str(manifest.get("source", {}).get("path", "drawing.dwg"))).name
    anchor = _map_anchor(map_anchor, extent)
    fixture = {
        "schema_version": "cad2gis.webdemo_fixture.v1",
        "provenance": {
            "fixture_kind": FIXTURE_KIND,
            "project_id": project_id,
            "project_name": project_name,
            "source_name": source_name,
            "source_sha256": manifest.get("source", {}).get("sha256"),
            "run_schema_version": manifest.get("schema_version"),
            "publication_boundary": PUBLICATION_BOUNDARY,
        },
        "run": _run_summary(
            manifest,
            source_name,
            _center_lon_lat(dataset, extent),
            len(layers),
            project_id=project_id,
            project_name=project_name,
            anchor=anchor,
        ),
        "layers": layers,
    }
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {
        "fixture_kind": fixture["provenance"]["fixture_kind"],
        "project_id": project_id,
        "project_name": project_name,
        "source_sha256": fixture["provenance"]["source_sha256"],
        "layers": {name: len(value["features"]) for name, value in layers.items()},
        "output": str(output),
        "bytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--project-id", default="hutabohu")
    parser.add_argument("--project-name", default="Hutabohu")
    parser.add_argument("--map-anchor", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src/cad2gis/webdemo/demo-data.json",
    )
    args = parser.parse_args()
    print(json.dumps(export_fixture(
        args.run_dir,
        args.output,
        project_id=args.project_id,
        project_name=args.project_name,
        map_anchor=args.map_anchor,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
