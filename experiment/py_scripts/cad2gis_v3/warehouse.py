"""Atomic eight-layer delivery writer; no audit tables enter delivery."""

from __future__ import annotations

import json
import gc
import math
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


def _cable_span_payload(feature, target_points, grid_length, geodesic_length):
    metrics = feature.attributes.get("span_metrics")
    expected_count = max(0, len(feature.native_points) - 1)
    if not isinstance(metrics, list) or len(metrics) != expected_count:
        raise RuntimeError(
            f"Missing complete span metrics for {feature.feature_key}: "
            f"expected {expected_count} source segments"
        )
    span_count = feature.attributes.get("span_count")
    measured_count = feature.attributes.get("measured_span_count")
    unmeasured_count = feature.attributes.get("unmeasured_span_count")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (
        span_count, measured_count, unmeasured_count,
    )):
        raise RuntimeError(f"Invalid CABLE span counts for {feature.feature_key}")
    if (
        span_count != expected_count
        or measured_count + unmeasured_count != span_count
    ):
        raise RuntimeError(f"CABLE span count closure failed for {feature.feature_key}")

    measured = 0
    dimension_measurements = []
    for segment_index, metric in enumerate(metrics):
        if metric.get("segment_index") != segment_index:
            raise RuntimeError(
                f"CABLE span order mismatch for {feature.feature_key}:segment:{segment_index}"
            )
        required = {
            "source_native_length_m", "dimension_entity_key",
            "measurement_native_m", "measurement_delta_m", "status",
            "delivery_grid_length_m", "geodesic_length_m",
        }
        if not required <= set(metric):
            raise RuntimeError(
                f"Incomplete CABLE span metric for {feature.feature_key}:segment:{segment_index}"
            )
        native_length = math.dist(
            feature.native_points[segment_index], feature.native_points[segment_index + 1]
        )
        target_length = math.dist(
            target_points[segment_index], target_points[segment_index + 1]
        )
        if abs(float(metric["source_native_length_m"]) - native_length) > 1e-9:
            raise RuntimeError(
                f"Native CABLE span length mismatch for {feature.feature_key}:segment:{segment_index}"
            )
        if abs(float(metric["delivery_grid_length_m"]) - target_length) > 1e-6:
            raise RuntimeError(
                f"Delivery CABLE span length mismatch for {feature.feature_key}:segment:{segment_index}"
            )
        if not math.isfinite(float(metric["geodesic_length_m"])):
            raise RuntimeError(
                f"Non-finite geodesic CABLE span length for "
                f"{feature.feature_key}:segment:{segment_index}"
            )
        if metric["status"] == "measured":
            measurement = metric["measurement_native_m"]
            if metric["dimension_entity_key"] in (None, "") or measurement is None:
                raise RuntimeError(
                    f"Measured CABLE span lacks DIMENSION evidence for "
                    f"{feature.feature_key}:segment:{segment_index}"
                )
            measured += 1
            dimension_measurements.append(float(measurement))

    if measured != measured_count:
        raise RuntimeError(f"Measured CABLE span count mismatch for {feature.feature_key}")
    if abs(sum(float(item["delivery_grid_length_m"]) for item in metrics) - grid_length) > 1e-6:
        raise RuntimeError(f"CABLE projected span length closure failed for {feature.feature_key}")
    if abs(sum(float(item["geodesic_length_m"]) for item in metrics) - geodesic_length) > 1e-6:
        raise RuntimeError(f"CABLE geodesic span length closure failed for {feature.feature_key}")
    dimension_total = feature.attributes.get("dimension_length_m")
    if dimension_measurements:
        if dimension_total is None or abs(sum(dimension_measurements) - float(dimension_total)) > 1e-9:
            raise RuntimeError(f"CABLE DIMENSION total closure failed for {feature.feature_key}")
    elif dimension_total is not None:
        raise RuntimeError(
            f"CABLE has a DIMENSION total without measured spans: {feature.feature_key}"
        )
    return json.dumps(
        metrics,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


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
            ("source_segment_sum_m", ogr.OFTReal),
            ("source_native_length_delta_m", ogr.OFTReal),
            ("delivery_grid_length_m", ogr.OFTReal), ("geodesic_length_m", ogr.OFTReal),
        ):
            layer.CreateField(ogr.FieldDefn(name, field_type))
        if layer_name == "CABLE":
            for name, field_type in (
                ("span_count", ogr.OFTInteger),
                ("measured_span_count", ogr.OFTInteger),
                ("unmeasured_span_count", ogr.OFTInteger),
                ("dimension_measured_sum_m", ogr.OFTReal),
                ("dimension_measurement_status", ogr.OFTString),
                ("dimension_coverage_ratio", ogr.OFTReal),
                ("span_schema_version", ogr.OFTString),
                ("span_unit", ogr.OFTString),
                ("span_metrics_json", ogr.OFTString),
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
                enriched_point = (
                    float(feature.attributes["X"]), float(feature.attributes["Y"]),
                )
                if math.dist(enriched_point, points[0]) > 1e-6:
                    raise RuntimeError(
                        f"Projected coordinate enrichment mismatch for {feature.feature_key}: "
                        f"{enriched_point} != {points[0]}"
                    )
                row.SetField("X", enriched_point[0])
                row.SetField("Y", enriched_point[1])
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
                if feature.attributes.get("source_segment_sum_m") is not None:
                    row.SetField(
                        "source_segment_sum_m", float(feature.attributes["source_segment_sum_m"]),
                    )
                if feature.attributes.get("source_native_length_delta_m") is not None:
                    row.SetField(
                        "source_native_length_delta_m",
                        float(feature.attributes["source_native_length_delta_m"]),
                    )
                if feature.attributes.get("dimension_length_m") is not None:
                    row.SetField("dimension_length_m", float(feature.attributes["dimension_length_m"]))
                if layer_name == "CABLE":
                    span_payload = _cable_span_payload(
                        feature, points, float(grid_length), float(geodesic_length),
                    )
                    row.SetField("span_count", int(feature.attributes["span_count"]))
                    row.SetField(
                        "measured_span_count",
                        int(feature.attributes["measured_span_count"]),
                    )
                    row.SetField(
                        "unmeasured_span_count",
                        int(feature.attributes["unmeasured_span_count"]),
                    )
                    if feature.attributes.get("dimension_measured_sum_m") is not None:
                        row.SetField(
                            "dimension_measured_sum_m",
                            float(feature.attributes["dimension_measured_sum_m"]),
                        )
                    row.SetField(
                        "dimension_measurement_status",
                        str(feature.attributes["dimension_measurement_status"]),
                    )
                    row.SetField(
                        "dimension_coverage_ratio",
                        float(feature.attributes["dimension_coverage_ratio"]),
                    )
                    row.SetField(
                        "span_schema_version",
                        str(feature.attributes["span_schema_version"]),
                    )
                    row.SetField("span_unit", str(feature.attributes["span_unit"]))
                    row.SetField("span_metrics_json", span_payload)
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
            row.SetField(
                "style_qgis_rotation_deg",
                float(feature.attributes.get(
                    "delivery_style_qgis_rotation_deg", feature.style.qgis_rotation_degrees,
                )),
            )
            row.SetField(
                "style_render_key",
                str(feature.attributes.get("delivery_style_render_key", feature.style.render_key)),
            )
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
