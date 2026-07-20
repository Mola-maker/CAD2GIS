"""Atomic eight base-layer delivery writer with normalized CABLE_SEGMENT detail.

The ninth layer is a business delivery view of immutable parent CABLE source
segments; audit tables never enter this GeoPackage.
"""

from __future__ import annotations

import json
import gc
import hashlib
import math
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

from osgeo import ogr

from schema_config import BOITE, CABLE, IMB, INFRASTRUCTURE_FC, PTECH, SITE, ZNRO, ZPM

from .curve_geometry import delivery_points, delivery_segments
from .georef import DirectTransformer
from .gpkg_metadata import normalize_geopackage_metadata
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
# ``CABLE_SEGMENT`` is a delivery business layer, not a projection of the
# evidence ``cable_span_segments`` audit layer.  Its rows are normalised from
# the immutable source-segment order plus its versioned delivery path at
# publication time.  One source segment remains one row even when a bulge arc
# needs several delivery vertices.
CABLE_SEGMENT = {
    "fc_name": "CABLE_SEGMENT",
    "geometry_type": "LineString",
    "layer_name": "CABLE_SEGMENT",
    "fields": [
        {"full_name": "route_key", "type": "Text", "length": 120},
        {"full_name": "source_entity_key", "type": "Text", "length": 120},
        {"full_name": "source_handle", "type": "Text", "length": 80},
        {"full_name": "source_layer", "type": "Text", "length": 255},
        {"full_name": "segment_index", "type": "Integer"},
        {"full_name": "source_segment_key", "type": "Text", "length": 64},
        {"full_name": "source_segment_kind", "type": "Text", "length": 64},
        {"full_name": "native_length_source", "type": "Text", "length": 96},
        {"full_name": "source_native_length_m", "type": "Double"},
        {"full_name": "delivery_vertex_count", "type": "Integer"},
        {"full_name": "delivery_chord_length_native", "type": "Double"},
        {"full_name": "materialization_policy_version", "type": "Text", "length": 64},
        {"full_name": "measurement_state", "type": "Text", "length": 16},
        {"full_name": "dimension_entity_key", "type": "Text", "length": 120},
        {"full_name": "measurement_native_m", "type": "Double"},
        {"full_name": "measurement_delta_m", "type": "Double"},
        {"full_name": "delivery_grid_length_m", "type": "Double"},
        {"full_name": "geodesic_length_m", "type": "Double"},
        {"full_name": "length_value_m", "type": "Double"},
        {"full_name": "status", "type": "Text", "length": 64},
        {"full_name": "length_label", "type": "Text", "length": 64},
        {"full_name": "length_source", "type": "Text", "length": 80},
        {"full_name": "unit", "type": "Text", "length": 16},
        {"full_name": "schema_version", "type": "Text", "length": 64},
        {"full_name": "parent_cable_code", "type": "Text", "length": 120},
        {"full_name": "parent_display_label", "type": "Text", "length": 255},
        {"full_name": "parent_label_provenance", "type": "Text", "length": 255},
    ],
}
LAYER_CONFIGS["CABLE_SEGMENT"] = CABLE_SEGMENT
LAYER_ORDER = tuple(LAYER_CONFIGS)

CABLE_SEGMENT_SCHEMA_VERSION = "cad2gis.cable_segment.v1"
CABLE_SEGMENT_UNIT = "m"


def _ogr_field_type(field):
    return {
        "Integer": ogr.OFTInteger,
        "Double": ogr.OFTReal,
    }.get(field["type"], ogr.OFTString)


def _geometry(feature: Feature, transformer: DirectTransformer):
    native_points = (
        delivery_points(feature, require_materialized=False)
        if feature.feature_class == "CABLE" and feature.geometry_kind == "LineString"
        else feature.native_points
    )
    points = transformer.points(native_points)
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


def _source_length_m(transformer, native_length):
    converter = getattr(transformer, "source_length_to_m", None)
    return float(converter(native_length) if callable(converter) else native_length)


def _grid_length_m(transformer, target_points):
    converter = getattr(transformer, "grid_length_m", None)
    if callable(converter):
        return float(converter(target_points))
    return _polyline_length(target_points)


def _polyline_length(points):
    return math.fsum(math.dist(start, end) for start, end in zip(points, points[1:]))


def _cable_span_payload(feature, transformer, grid_length, geodesic_length):
    metrics = feature.attributes.get("span_metrics")
    source_segments = delivery_segments(feature, require_materialized=False)
    expected_count = len(source_segments)
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
    for segment_index, (metric, source_segment) in enumerate(zip(metrics, source_segments)):
        if metric.get("segment_index") != segment_index:
            raise RuntimeError(
                f"CABLE span order mismatch for {feature.feature_key}:segment:{segment_index}"
            )
        required = {
            "source_native_length_m", "dimension_entity_key",
            "measurement_native_m", "measurement_delta_m", "status",
            "delivery_grid_length_m", "geodesic_length_m",
            "source_segment_kind", "native_length_source",
        }
        if not required <= set(metric):
            raise RuntimeError(
                f"Incomplete CABLE span metric for {feature.feature_key}:segment:{segment_index}"
            )
        native_length = _source_length_m(
            transformer, source_segment["source_native_length"],
        )
        segment_native_points = source_segment["delivery_native_points"]
        segment_target_points = transformer.points(segment_native_points)
        target_length = _grid_length_m(transformer, segment_target_points)
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
        expected_geodesic = float(transformer.geodesic_length(segment_native_points))
        if abs(float(metric["geodesic_length_m"]) - expected_geodesic) > 1e-6:
            raise RuntimeError(
                f"Geodesic CABLE span length mismatch for "
                f"{feature.feature_key}:segment:{segment_index}"
            )
        if metric.get("source_segment_kind") not in {
            None, source_segment["source_segment_kind"],
        }:
            raise RuntimeError(
                f"CABLE span kind mismatch for {feature.feature_key}:segment:{segment_index}"
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


def _source_segment_key(route_key, segment_index):
    """Return the stable occurrence key shared with source-segment evidence."""
    return hashlib.sha256(
        f"{route_key}|segment|{segment_index}".encode("utf-8")
    ).hexdigest()


def _finite_metric(value, name, *, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise RuntimeError(f"CABLE_SEGMENT {name} must be finite numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"CABLE_SEGMENT {name} must be finite numeric") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"CABLE_SEGMENT {name} must be finite numeric")
    return result


def _cable_segment_records(features, transformer):
    """Build and validate normalised delivery rows for every CABLE occurrence.

    This function intentionally consumes only the parent CABLE's versioned
    source-segment materialization and its already-enriched span metrics.  It
    never reads the evidence GeoPackage and never creates a synthetic/default
    length.
    """
    records = []
    seen_routes = set()
    total_measured = 0
    total_unmeasured = 0
    for feature in sorted(
        (item for item in features if item.feature_class == "CABLE"),
        key=lambda item: item.feature_key,
    ):
        route_key = str(feature.feature_key)
        if route_key in seen_routes:
            raise RuntimeError(f"Duplicate CABLE route key for CABLE_SEGMENT: {route_key}")
        seen_routes.add(route_key)
        source_segments = delivery_segments(feature, require_materialized=False)
        expected_count = len(source_segments)
        metrics = feature.attributes.get("span_metrics")
        if not isinstance(metrics, list) or len(metrics) != expected_count:
            raise RuntimeError(
                f"CABLE_SEGMENT span count mismatch for {route_key}: "
                f"expected {expected_count}, got "
                f"{len(metrics) if isinstance(metrics, list) else type(metrics).__name__}"
            )
        for name in ("span_count", "measured_span_count", "unmeasured_span_count"):
            value = feature.attributes.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise RuntimeError(f"CABLE_SEGMENT invalid parent {name} for {route_key}")
        if feature.attributes["span_count"] != expected_count:
            raise RuntimeError(f"CABLE_SEGMENT parent span_count mismatch for {route_key}")
        if (
            feature.attributes["measured_span_count"]
            + feature.attributes["unmeasured_span_count"]
            != expected_count
        ):
            raise RuntimeError(f"CABLE_SEGMENT parent measured/unmeasured closure failed for {route_key}")

        route_grid_sum = 0.0
        route_geodesic_sum = 0.0
        route_dimension_sum = 0.0
        route_measured = 0
        route_records = []
        for segment_index, source_segment in enumerate(source_segments):
            metric = metrics[segment_index]
            if not isinstance(metric, dict):
                raise RuntimeError(
                    f"CABLE_SEGMENT metric must be an object for "
                    f"{route_key}:segment:{segment_index}"
                )
            if metric.get("segment_index") != segment_index:
                raise RuntimeError(
                    f"CABLE_SEGMENT source index mismatch for "
                    f"{route_key}:segment:{segment_index}"
                )
            source_length = _finite_metric(
                metric.get("source_native_length_m"), "source_native_length_m"
            )
            if str(metric.get("source_segment_kind", "")) != str(
                source_segment["source_segment_kind"]
            ):
                raise RuntimeError(
                    f"CABLE_SEGMENT source kind mismatch for "
                    f"{route_key}:segment:{segment_index}"
                )
            if str(metric.get("native_length_source", "")) != str(
                source_segment["native_length_source"]
            ):
                raise RuntimeError(
                    f"CABLE_SEGMENT native length provenance mismatch for "
                    f"{route_key}:segment:{segment_index}"
                )
            expected_source_length = _source_length_m(
                transformer, source_segment["source_native_length"],
            )
            if abs(source_length - expected_source_length) > 1e-9:
                raise RuntimeError(
                    f"CABLE_SEGMENT source length closure failed for "
                    f"{route_key}:segment:{segment_index}"
                )
            grid_length = _finite_metric(
                metric.get("delivery_grid_length_m"), "delivery_grid_length_m"
            )
            segment_native_points = source_segment["delivery_native_points"]
            segment_target_points = tuple(transformer.points(segment_native_points))
            actual_grid_length = _grid_length_m(transformer, segment_target_points)
            if abs(grid_length - actual_grid_length) > 1e-6:
                raise RuntimeError(
                    f"CABLE_SEGMENT geometry length closure failed for "
                    f"{route_key}:segment:{segment_index}"
                )
            geodesic_length = _finite_metric(
                metric.get("geodesic_length_m"), "geodesic_length_m"
            )
            expected_geodesic_length = float(
                transformer.geodesic_length(segment_native_points)
            )
            if abs(geodesic_length - expected_geodesic_length) > 1e-6:
                raise RuntimeError(
                    f"CABLE_SEGMENT geodesic length closure failed for "
                    f"{route_key}:segment:{segment_index}"
                )
            status = str(metric.get("status", "")).strip()
            if status != "measured" and not status.startswith("unmeasured_"):
                raise RuntimeError(
                    f"CABLE_SEGMENT status is unsupported for "
                    f"{route_key}:segment:{segment_index}: {status!r}"
                )
            measurement = _finite_metric(
                metric.get("measurement_native_m"), "measurement_native_m", allow_none=True
            )
            measurement_delta = _finite_metric(
                metric.get("measurement_delta_m"), "measurement_delta_m", allow_none=True
            )
            dimension_key = metric.get("dimension_entity_key")
            if dimension_key is not None:
                dimension_key = str(dimension_key)
                if not dimension_key:
                    dimension_key = None
            if status == "measured":
                if dimension_key is None or measurement is None:
                    raise RuntimeError(
                        f"CABLE_SEGMENT measured span lacks DWG DIMENSION evidence for "
                        f"{route_key}:segment:{segment_index}"
                    )
                expected_delta = measurement - source_length
                if measurement_delta is None or abs(measurement_delta - expected_delta) > 1e-9:
                    raise RuntimeError(
                        f"CABLE_SEGMENT measurement delta closure failed for "
                        f"{route_key}:segment:{segment_index}"
                    )
                length_value = measurement
                length_source = "dwg_dimension"
                total_measured += 1
                route_measured += 1
                route_dimension_sum += measurement
            else:
                # An unmeasured segment must be explicit.  Silently carrying a
                # stale or default DIMENSION value would make the delivery
                # numerically unauditable, so fail closed instead.
                if measurement is not None or measurement_delta is not None:
                    raise RuntimeError(
                        f"CABLE_SEGMENT unmeasured span contains DIMENSION evidence for "
                        f"{route_key}:segment:{segment_index}"
                    )
                length_value = grid_length
                length_source = "delivery_grid_fallback_unmeasured"
                total_unmeasured += 1
            route_grid_sum += grid_length
            route_geodesic_sum += geodesic_length
            source_segment_key = _source_segment_key(route_key, segment_index)
            segment_lineage = list(feature.lineage)
            segment_lineage.append({
                "operation": "segment_occurrence",
                "parent_feature_key": route_key,
                "segment_index": segment_index,
                "source_segment_key": source_segment_key,
                "source_segment_kind": str(source_segment["source_segment_kind"]),
                "geometry_policy": "versioned-source-segment-delivery-path",
            })
            route_records.append({
                "route_key": route_key,
                "source_entity_key": str(feature.source_entity_key),
                "source_handle": str(feature.source_handle),
                "source_layer": str(feature.source_layer),
                "segment_index": segment_index,
                "source_segment_key": source_segment_key,
                "source_segment_kind": str(source_segment["source_segment_kind"]),
                "native_length_source": str(source_segment["native_length_source"]),
                "source_native_length_m": source_length,
                "delivery_vertex_count": len(segment_native_points),
                "delivery_chord_length_native": float(
                    source_segment["delivery_chord_length_native"]
                ),
                "materialization_policy_version": str(
                    feature.attributes.get(
                        "curve_materialization_policy_version", "legacy-straight-v1",
                    )
                ),
                "measurement_state": (
                    "measured" if status == "measured" else "unmeasured"
                ),
                "dimension_entity_key": dimension_key,
                "measurement_native_m": measurement,
                "measurement_delta_m": measurement_delta,
                "delivery_grid_length_m": grid_length,
                "geodesic_length_m": geodesic_length,
                "length_value_m": length_value,
                "status": status,
                "length_label": (
                    f"{length_value:.3f} m"
                    if status == "measured"
                    else f"{length_value:.3f} m [grid; unmeasured]"
                ),
                "length_source": length_source,
                "unit": CABLE_SEGMENT_UNIT,
                "schema_version": CABLE_SEGMENT_SCHEMA_VERSION,
                "parent_cable_code": feature.attributes.get("CODE"),
                "parent_display_label": feature.display_label,
                "parent_label_provenance": feature.label_provenance,
                "display_label": (
                    f"{length_value:.3f} m"
                    if status == "measured"
                    else f"{length_value:.3f} m [grid; unmeasured]"
                ),
                "label_provenance": (
                    "DWG_DIRECT:SPAN-CABLE-DIMENSION;length_source=dwg_dimension"
                    if status == "measured"
                    else (
                        "DWG_DERIVED:delivery-grid-length-unmeasured;"
                        "length_source=delivery_grid_fallback_unmeasured"
                    )
                ),
                "geometry_role": "SOURCE_ROUTE_SEGMENT",
                "lineage_json": json.dumps(
                    segment_lineage, ensure_ascii=False, separators=(",", ":")
                ),
                "style_aci": feature.style.aci_color,
                "style_truecolor": feature.style.true_color,
                "style_linetype": feature.style.linetype,
                "style_lineweight": feature.style.lineweight,
                "style_rotation": feature.style.rotation,
                "style_rotation_deg": feature.style.rotation_degrees,
                "style_qgis_rotation_deg": float(feature.attributes.get(
                    "delivery_style_qgis_rotation_deg", feature.style.qgis_rotation_degrees,
                )),
                "style_render_key": str(feature.attributes.get(
                    "delivery_style_render_key", feature.style.render_key,
                )),
                "target_points": segment_target_points,
            })
        if route_measured != feature.attributes["measured_span_count"]:
            raise RuntimeError(f"CABLE_SEGMENT measured count closure failed for {route_key}")
        if expected_count - route_measured != feature.attributes["unmeasured_span_count"]:
            raise RuntimeError(f"CABLE_SEGMENT unmeasured count closure failed for {route_key}")
        parent_grid = _finite_metric(
            feature.attributes.get("delivery_grid_length_m"), "parent delivery_grid_length_m"
        )
        parent_geodesic = _finite_metric(
            feature.attributes.get("geodesic_length_m"), "parent geodesic_length_m"
        )
        if abs(route_grid_sum - parent_grid) > 1e-6:
            raise RuntimeError(f"CABLE_SEGMENT parent grid total closure failed for {route_key}")
        if abs(route_geodesic_sum - parent_geodesic) > 1e-6:
            raise RuntimeError(f"CABLE_SEGMENT parent geodesic total closure failed for {route_key}")
        parent_dimension = feature.attributes.get("dimension_length_m")
        if route_measured:
            parent_dimension = _finite_metric(parent_dimension, "parent dimension_length_m")
            if abs(route_dimension_sum - parent_dimension) > 1e-9:
                raise RuntimeError(f"CABLE_SEGMENT parent DIMENSION total closure failed for {route_key}")
        elif parent_dimension is not None:
            raise RuntimeError(
                f"CABLE_SEGMENT parent has a DIMENSION total without measured spans: {route_key}"
            )
        records.extend(route_records)
    return records


def _line_geometry(points):
    geometry = ogr.Geometry(ogr.wkbLineString)
    for point in points:
        geometry.AddPoint_2D(*point)
    return geometry


def _populate_dataset(dataset, features, transformer):
    by_class = defaultdict(list)
    for feature in features:
        if feature.feature_class in LAYER_CONFIGS:
            by_class[feature.feature_class].append(feature)
    cable_segment_records = _cable_segment_records(features, transformer)
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
            # A few normalised segment fields intentionally use the same names
            # as the common lineage/style fields below.  Create each field only
            # once while retaining the schema's deterministic order.
            if name in schema_fields:
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
            if name not in schema_fields:
                layer.CreateField(ogr.FieldDefn(name, field_type))
        if layer_name == "CABLE":
            for name, field_type in (
                ("curve_materialization_schema_version", ogr.OFTString),
                ("curve_materialization_policy_version", ogr.OFTString),
                ("curve_source_segment_count", ogr.OFTInteger),
                ("curve_delivery_vertex_count", ogr.OFTInteger),
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
        items = (
            cable_segment_records
            if layer_name == "CABLE_SEGMENT"
            else sorted(by_class[layer_name], key=lambda item: item.feature_key)
        )
        for feature in items:
            if layer_name == "CABLE_SEGMENT":
                geometry = _line_geometry(feature["target_points"])
                points = list(feature["target_points"])
                row = ogr.Feature(layer.GetLayerDefn())
                row.SetGeometry(geometry)
                for name, value in feature.items():
                    if name in {"target_points", "geometry"} or value is None:
                        continue
                    field_defn = layer.GetLayerDefn().GetFieldIndex(name)
                    if field_defn < 0:
                        continue
                    try:
                        row.SetField(name, value)
                    except (TypeError, ValueError):
                        raise RuntimeError(
                            f"Could not set CABLE_SEGMENT field {name} "
                            f"for {feature['route_key']}:segment:{feature['segment_index']}"
                        )
                actual_grid_length = _grid_length_m(transformer, points)
                if abs(actual_grid_length - float(feature["delivery_grid_length_m"])) > 1e-6:
                    raise RuntimeError(
                        f"CABLE_SEGMENT geometry length closure failed for "
                        f"{feature['route_key']}:segment:{feature['segment_index']}"
                    )
                row.SetField("LONGUEUR", float(feature["delivery_grid_length_m"]))
                if layer.CreateFeature(row) != 0:
                    raise RuntimeError(
                        f"Could not write CABLE_SEGMENT feature "
                        f"{feature['route_key']}:segment:{feature['segment_index']}"
                    )
                count += 1
                continue

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
                actual_grid_length = _grid_length_m(transformer, points)
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
                        feature, transformer, float(grid_length), float(geodesic_length),
                    )
                    for name in (
                        "curve_materialization_schema_version",
                        "curve_materialization_policy_version",
                        "curve_source_segment_count",
                        "curve_delivery_vertex_count",
                    ):
                        value = feature.attributes.get(name)
                        if value is not None:
                            row.SetField(name, value)
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
            normalize_geopackage_metadata(connection)
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
