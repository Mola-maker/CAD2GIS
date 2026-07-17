"""Atomic eight-layer delivery writer; no audit tables enter delivery."""

from __future__ import annotations

import json
import gc
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

from osgeo import ogr

from schema_config import BOITE, CABLE, IMB, INFRASTRUCTURE_FC, PTECH, SITE, ZNRO, ZPM

from .georef import DirectTransformer
from .model import Feature

LAYER_CONFIGS = {
    "BOITE": BOITE,
    "CABLE": CABLE,
    "PTECH": PTECH,
    "INFRASTRUCTURE": INFRASTRUCTURE_FC,
    "SITE": SITE,
    "ZNRO": ZNRO,
    "ZPM": ZPM,
    "IMB": IMB,
}
LAYER_ORDER = tuple(LAYER_CONFIGS)


def _ogr_field_type(field):
    return {
        "Integer": ogr.OFTInteger,
        "Double": ogr.OFTReal,
    }.get(field["type"], ogr.OFTString)


def _geometry(feature: Feature, transformer: DirectTransformer):
    points = transformer.points(feature.native_points)
    if feature.geometry_kind == "Point":
        geometry = ogr.Geometry(ogr.wkbPoint)
        geometry.AddPoint_2D(*points[0])
        return geometry, points
    if feature.geometry_kind == "LineString":
        geometry = ogr.Geometry(ogr.wkbLineString)
        for point in points:
            geometry.AddPoint_2D(*point)
        return geometry, points
    if feature.geometry_kind == "Polygon":
        ring = ogr.Geometry(ogr.wkbLinearRing)
        closed = list(points)
        if closed and closed[0] != closed[-1]:
            closed.append(closed[0])
        for point in closed:
            ring.AddPoint_2D(*point)
        geometry = ogr.Geometry(ogr.wkbPolygon)
        geometry.AddGeometry(ring)
        return geometry, closed
    raise ValueError(f"Unsupported geometry kind: {feature.geometry_kind}")


def _contract_geometry_kind(value):
    if value.startswith("Point"):
        return "Point"
    if value.startswith("LineString"):
        return "LineString"
    if value.startswith("Polygon"):
        return "Polygon"
    raise ValueError(f"Unsupported contract geometry type: {value}")


def _populate_dataset(dataset, features, transformer):
    by_class = defaultdict(list)
    for feature in features:
        if feature.feature_class in LAYER_CONFIGS:
            by_class[feature.feature_class].append(feature)
    counts = {}
    geom_types = {
        "Point": ogr.wkbPoint, "LineString": ogr.wkbLineString, "Polygon": ogr.wkbPolygon,
    }
    for layer_name in LAYER_ORDER:
        config = LAYER_CONFIGS[layer_name]
        geometry_kind = _contract_geometry_kind(config["geometry_type"])
        layer = dataset.CreateLayer(layer_name, transformer.target, geom_types[geometry_kind])
        schema_fields = {}
        for field in config["fields"]:
            name = field["full_name"]
            if name in {"X", "Y", "LONGUEUR"}:
                continue
            definition = ogr.FieldDefn(name, _ogr_field_type(field))
            if field.get("length"):
                definition.SetWidth(int(field["length"]))
            layer.CreateField(definition)
            schema_fields[name] = field
        if geometry_kind == "Point":
            layer.CreateField(ogr.FieldDefn("X", ogr.OFTReal))
            layer.CreateField(ogr.FieldDefn("Y", ogr.OFTReal))
        if geometry_kind == "LineString":
            layer.CreateField(ogr.FieldDefn("LONGUEUR", ogr.OFTReal))
        for name, field_type in (
            ("display_label", ogr.OFTString), ("label_provenance", ogr.OFTString),
            ("source_entity_key", ogr.OFTString), ("source_handle", ogr.OFTString),
            ("source_layer", ogr.OFTString), ("geometry_role", ogr.OFTString),
            ("style_aci", ogr.OFTInteger), ("style_truecolor", ogr.OFTString),
            ("style_linetype", ogr.OFTString), ("style_lineweight", ogr.OFTInteger),
            ("style_rotation", ogr.OFTReal), ("style_rotation_deg", ogr.OFTReal),
            ("style_qgis_rotation_deg", ogr.OFTReal),
            ("style_render_key", ogr.OFTString), ("lineage_json", ogr.OFTString),
            ("source_cad_length_m", ogr.OFTReal), ("dimension_length_m", ogr.OFTReal),
            ("delivery_grid_length_m", ogr.OFTReal), ("geodesic_length_m", ogr.OFTReal),
        ):
            layer.CreateField(ogr.FieldDefn(name, field_type))
        count = 0
        for feature in sorted(by_class[layer_name], key=lambda item: item.feature_key):
            geometry, points = _geometry(feature, transformer)
            row = ogr.Feature(layer.GetLayerDefn())
            row.SetGeometry(geometry)
            for name, field in schema_fields.items():
                value = feature.attributes.get(name)
                if value is None or value == "":
                    continue
                try:
                    if field["type"] == "Integer":
                        row.SetField(name, int(value))
                    elif field["type"] == "Double":
                        row.SetField(name, float(value))
                    else:
                        row.SetField(name, str(value))
                except (TypeError, ValueError):
                    continue
            if geometry_kind == "Point":
                if feature.attributes.get("X") is None or feature.attributes.get("Y") is None:
                    raise RuntimeError(f"Missing projected coordinates for {feature.feature_key}")
                row.SetField("X", float(feature.attributes["X"]))
                row.SetField("Y", float(feature.attributes["Y"]))
            if geometry_kind == "LineString":
                actual_grid_length = float(geometry.Length())
                grid_length = feature.attributes.get("delivery_grid_length_m")
                geodesic_length = feature.attributes.get("geodesic_length_m")
                if grid_length is None or geodesic_length is None:
                    raise RuntimeError(f"Missing enriched length metrics for {feature.feature_key}")
                if abs(actual_grid_length - float(grid_length)) > 1e-6:
                    raise RuntimeError(
                        f"Projected length enrichment mismatch for {feature.feature_key}: "
                        f"{grid_length} != {actual_grid_length}"
                    )
                row.SetField("LONGUEUR", float(grid_length))
                row.SetField("delivery_grid_length_m", float(grid_length))
                row.SetField("geodesic_length_m", float(geodesic_length))
                if feature.attributes.get("source_cad_length_m") is not None:
                    row.SetField("source_cad_length_m", float(feature.attributes["source_cad_length_m"]))
                if feature.attributes.get("dimension_length_m") is not None:
                    row.SetField("dimension_length_m", float(feature.attributes["dimension_length_m"]))
            row.SetField("display_label", feature.display_label)
            row.SetField("label_provenance", feature.label_provenance)
            row.SetField("source_entity_key", feature.source_entity_key)
            row.SetField("source_handle", feature.source_handle)
            row.SetField("source_layer", feature.source_layer)
            row.SetField("geometry_role", feature.geometry_role)
            row.SetField("style_aci", feature.style.aci_color)
            row.SetField("style_truecolor", feature.style.true_color)
            row.SetField("style_linetype", feature.style.linetype)
            row.SetField("style_lineweight", feature.style.lineweight)
            row.SetField("style_rotation", feature.style.rotation)
            row.SetField("style_rotation_deg", feature.style.rotation_degrees)
            row.SetField("style_qgis_rotation_deg", feature.style.qgis_rotation_degrees)
            row.SetField("style_render_key", feature.style.render_key)
            row.SetField("lineage_json", json.dumps(feature.lineage, ensure_ascii=False, separators=(",", ":")))
            if layer.CreateFeature(row) != 0:
                raise RuntimeError(f"Could not write {layer_name} feature {feature.feature_key}")
            count += 1
        counts[layer_name] = count
    return counts


def _write_staged(path, features, transformer):
    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    if dataset is None:
        raise RuntimeError(f"Could not create delivery GeoPackage: {path}")
    try:
        if dataset.StartTransaction() != 0:
            raise RuntimeError(f"Could not start delivery transaction: {path}")
        counts = _populate_dataset(dataset, features, transformer)
        if dataset.CommitTransaction() != 0:
            raise RuntimeError("Could not commit delivery GeoPackage")
        dataset.FlushCache()
        return counts
    except Exception:
        try:
            dataset.RollbackTransaction()
        except Exception:
            pass
        raise
    finally:
        if dataset is not None:
            try:
                dataset.Close()
            except Exception:
                pass
        dataset = None
        gc.collect()


def write_delivery(path, features, transformer):
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    staged = stage_dir / destination.name
    try:
        counts = _write_staged(staged, features, transformer)
        connection = sqlite3.connect(staged)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            layers = {
                row[0] for row in connection.execute(
                    "SELECT table_name FROM gpkg_contents WHERE data_type='features'"
                )
            }
        finally:
            connection.close()
        if integrity != "ok" or layers != set(LAYER_ORDER):
            raise RuntimeError(f"Delivery validation failed: integrity={integrity}, layers={sorted(layers)}")
        gc.collect()
        os.replace(staged, destination)
        return counts
    finally:
        if staged.exists():
            staged.unlink(missing_ok=True)
        shutil.rmtree(stage_dir, ignore_errors=True)
