"""Complete evidence GeoPackage, conservation ledger, and lineage writer."""

from __future__ import annotations

import json
import hashlib
import math
import os
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from osgeo import ogr

from .model import Feature, Relation, SourceEntity
from .warehouse import LAYER_CONFIGS


def _table(dataset, name, fields):
    layer = dataset.CreateLayer(name, None, ogr.wkbNone)
    for field_name, field_type in fields:
        layer.CreateField(ogr.FieldDefn(field_name, field_type))
    return layer


def _set(row, values):
    for key, value in values.items():
        if value is not None:
            row.SetField(key, value)


def _line_layer(dataset, name, srs):
    layer = dataset.CreateLayer(name, srs, ogr.wkbLineString)
    for field_name in ("entity_key", "cad_handle", "dwg_layer", "status"):
        layer.CreateField(ogr.FieldDefn(field_name, ogr.OFTString))
    layer.CreateField(ogr.FieldDefn("measurement_native_m", ogr.OFTReal))
    return layer


def _span_dimension_statuses(relations):
    """Collapse endpoint evidence using the least-certain endpoint status."""
    endpoint_statuses = defaultdict(dict)
    for relation in relations:
        if relation.relation_kind != "supported_by":
            continue
        dimension_key, separator, endpoint = relation.source_key.rpartition(":endpoint:")
        if not separator or endpoint not in {"0", "1"}:
            continue
        endpoint_statuses[dimension_key][endpoint] = relation.status

    statuses = {}
    for dimension_key, endpoints in endpoint_statuses.items():
        if set(endpoints) != {"0", "1"}:
            statuses[dimension_key] = "unresolved_endpoint"
        elif all(status == "accepted" for status in endpoints.values()):
            statuses[dimension_key] = "accepted_endpoints"
        elif any(status == "candidate" for status in endpoints.values()):
            statuses[dimension_key] = "candidate_endpoint"
        else:
            statuses[dimension_key] = "unresolved_endpoint"
    return statuses


def _line(points):
    geometry = ogr.Geometry(ogr.wkbLineString)
    for x, y in points:
        geometry.AddPoint_2D(float(x), float(y))
    return geometry


def _validate_label_provenance(features):
    """Reject deliverable labels that cannot be traced to explicit evidence."""
    for feature in features:
        if feature.display_label in {None, ""}:
            continue
        provenance = feature.label_provenance
        normalized = "" if provenance is None else str(provenance).strip()
        if not normalized or normalized.upper() == "UNAVAILABLE":
            raise RuntimeError(
                "Non-empty display_label lacks explicit label provenance: "
                f"{feature.feature_class} ({feature.feature_key})"
            )


def _write_calibration_evidence(dataset, audit, target_srs):
    """Write target-space calibration evidence without touching native layers."""
    if audit is None:
        return
    if target_srs is None:
        raise ValueError("target_srs is required when calibration audit is present")

    result = dict(audit.get("result") or {})
    validation = dict(result.get("validation") or {})
    model_table = _table(dataset, "georef_models", [
        ("profile_path", ogr.OFTString), ("profile_sha256", ogr.OFTString),
        ("source_sha256", ogr.OFTString), ("status", ogr.OFTString),
        ("model", ogr.OFTString), ("parameters", ogr.OFTString),
        ("train_metrics", ogr.OFTString), ("check_metrics", ogr.OFTString),
        ("spatial_coverage", ogr.OFTString),
        ("validation_passed", ogr.OFTInteger), ("validation_failures", ogr.OFTString),
    ])
    row = ogr.Feature(model_table.GetLayerDefn())
    _set(row, {
        "profile_path": str(audit.get("profile_path", "")),
        "profile_sha256": str(audit.get("profile_sha256", "")),
        "source_sha256": str(audit.get("source_sha256", "")),
        "status": str(audit.get("status", "")),
        "model": str(result.get("selected_model", result.get("model", "identity"))),
        "parameters": json.dumps(result.get("parameters", {}), ensure_ascii=False, sort_keys=True),
        "train_metrics": json.dumps(result.get("train_metrics", {}), ensure_ascii=False, sort_keys=True),
        "check_metrics": json.dumps(result.get("check_metrics", {}), ensure_ascii=False, sort_keys=True),
        "spatial_coverage": json.dumps(
            audit.get("spatial_coverage", {}), ensure_ascii=False, sort_keys=True,
        ),
        "validation_passed": int(bool(validation.get("passed", False))),
        "validation_failures": json.dumps(
            validation.get("failures", ()), ensure_ascii=False, sort_keys=True,
        ),
    })
    model_table.CreateFeature(row)

    observation_fields = [
        ("point_id", ogr.OFTString), ("role", ogr.OFTString), ("source", ogr.OFTString),
        ("accuracy_m", ogr.OFTReal), ("weight", ogr.OFTReal),
        ("enabled", ogr.OFTInteger), ("inlier", ogr.OFTInteger),
        ("cad_x", ogr.OFTReal), ("cad_y", ogr.OFTReal),
        ("nominal_easting", ogr.OFTReal), ("nominal_northing", ogr.OFTReal),
        ("predicted_easting", ogr.OFTReal), ("predicted_northing", ogr.OFTReal),
        ("observed_easting", ogr.OFTReal), ("observed_northing", ogr.OFTReal),
        ("residual_dx_m", ogr.OFTReal), ("residual_dy_m", ogr.OFTReal),
        ("residual_m", ogr.OFTReal), ("status", ogr.OFTString),
    ]
    observation_table = _table(dataset, "gcp_observations", observation_fields)
    residual_layer = dataset.CreateLayer(
        "gcp_residual_vectors", target_srs, ogr.wkbLineString,
    )
    for field_name, field_type in observation_fields:
        residual_layer.CreateField(ogr.FieldDefn(field_name, field_type))
    for observation in audit.get("observations", ()):
        values = dict(observation)
        values["enabled"] = int(bool(values.get("enabled", True)))
        if values.get("inlier") is not None:
            values["inlier"] = int(bool(values["inlier"]))
        row = ogr.Feature(observation_table.GetLayerDefn())
        _set(row, values)
        observation_table.CreateFeature(row)

        predicted = (values.get("predicted_easting"), values.get("predicted_northing"))
        observed = (values.get("observed_easting"), values.get("observed_northing"))
        if None in predicted or None in observed:
            continue
        row = ogr.Feature(residual_layer.GetLayerDefn())
        row.SetGeometry(_line((predicted, observed)))
        _set(row, values)
        residual_layer.CreateFeature(row)

    displacement_table = _table(dataset, "georef_feature_lineage", [
        ("feature_key", ogr.OFTString), ("feature_class", ogr.OFTString),
        ("source_entity_key", ogr.OFTString), ("native_fingerprint", ogr.OFTString),
        ("nominal_fingerprint", ogr.OFTString), ("adjusted_fingerprint", ogr.OFTString),
        ("native_points_json", ogr.OFTString),
        ("nominal_points_json", ogr.OFTString),
        ("adjusted_points_json", ogr.OFTString),
        ("model", ogr.OFTString),
        ("nominal_centroid_easting", ogr.OFTReal),
        ("nominal_centroid_northing", ogr.OFTReal),
        ("adjusted_centroid_easting", ogr.OFTReal),
        ("adjusted_centroid_northing", ogr.OFTReal),
        ("centroid_dx_m", ogr.OFTReal), ("centroid_dy_m", ogr.OFTReal),
        ("mean_displacement_m", ogr.OFTReal), ("max_displacement_m", ogr.OFTReal),
    ])
    for displacement in audit.get("feature_displacements", ()):
        row = ogr.Feature(displacement_table.GetLayerDefn())
        _set(row, displacement)
        displacement_table.CreateFeature(row)


def _write_staged(
    path, entities, features, relations, unresolved, diagnostics, source_srs,
    calibration_audit=None, target_srs=None, delivery_transformer=None,
):
    _validate_label_provenance(features)
    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    if dataset is None or dataset.StartTransaction() != 0:
        raise RuntimeError(f"Could not create evidence GeoPackage: {path}")

    entity_table = _table(dataset, "cad_entities", [
        ("entity_key", ogr.OFTString), ("source_sha256", ogr.OFTString),
        ("source_file", ogr.OFTString), ("cad_handle", ogr.OFTString),
        ("cad_layout", ogr.OFTString), ("layout_role", ogr.OFTString),
        ("cad_role", ogr.OFTString), ("dwg_layer", ogr.OFTString),
        ("dwg_type", ogr.OFTString), ("block_name", ogr.OFTString),
        ("block_attributes", ogr.OFTString), ("text", ogr.OFTString),
        ("owner_handle", ogr.OFTString),
        ("dimension_text_override", ogr.OFTString),
        ("native_points", ogr.OFTString), ("dimension_value", ogr.OFTReal),
        ("native_length", ogr.OFTReal),
        ("extraction_backend", ogr.OFTString),
        ("reader_backend_status", ogr.OFTString),
        ("raw_properties", ogr.OFTString),
        ("aci_color", ogr.OFTInteger), ("true_color", ogr.OFTString),
        ("linetype", ogr.OFTString), ("lineweight", ogr.OFTInteger),
        ("rotation", ogr.OFTReal), ("disposition", ogr.OFTString),
        ("entity_aci_color", ogr.OFTInteger), ("layer_aci_color", ogr.OFTInteger),
        ("entity_true_color", ogr.OFTString), ("layer_true_color", ogr.OFTString),
        ("entity_linetype", ogr.OFTString), ("layer_linetype", ogr.OFTString),
        ("entity_lineweight", ogr.OFTInteger), ("layer_lineweight", ogr.OFTInteger),
        ("scale_x", ogr.OFTReal), ("scale_y", ogr.OFTReal), ("scale_z", ogr.OFTReal),
    ])
    block_instance_table = _table(dataset, "block_instances", [
        ("entity_key", ogr.OFTString), ("cad_handle", ogr.OFTString),
        ("owner_handle", ogr.OFTString), ("cad_layout", ogr.OFTString),
        ("cad_role", ogr.OFTString), ("dwg_layer", ogr.OFTString),
        ("raw_block_name", ogr.OFTString), ("effective_block_name", ogr.OFTString),
        ("block_attributes", ogr.OFTString), ("dynamic_properties", ogr.OFTString),
        ("insertion_point_native", ogr.OFTString),
        ("scale_x", ogr.OFTReal), ("scale_y", ogr.OFTReal), ("scale_z", ogr.OFTReal),
        ("rotation", ogr.OFTReal), ("extraction_backend", ogr.OFTString),
        ("reader_backend_status", ogr.OFTString), ("raw_properties", ogr.OFTString),
    ])
    annotation_carrier_table = _table(dataset, "annotation_carriers", [
        ("entity_key", ogr.OFTString), ("cad_handle", ogr.OFTString),
        ("owner_handle", ogr.OFTString), ("carrier_type", ogr.OFTString),
        ("cad_layout", ogr.OFTString), ("cad_role", ogr.OFTString),
        ("dwg_layer", ogr.OFTString), ("text", ogr.OFTString),
        ("text_source", ogr.OFTString), ("anchor_native", ogr.OFTString),
        ("extraction_backend", ogr.OFTString),
        ("reader_backend_status", ogr.OFTString), ("raw_properties", ogr.OFTString),
    ])
    feature_sources = {feature.source_entity_key for feature in features}
    annotation_sources = {
        relation.source_key for relation in relations if relation.relation_kind == "labels"
    }
    dispositions = Counter()
    annotation_carrier_types = {
        "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "MLEADER", "MULTILEADER",
        "TABLE", "TABLE_CELL", "DIMENSION",
    }
    for entity in entities:
        if entity.entity_key in feature_sources:
            disposition = "mapped"
        elif entity.entity_key in annotation_sources or entity.dwg_type in annotation_carrier_types:
            disposition = "annotation"
        elif entity.cad_role == "style_legend":
            disposition = "legend"
        elif entity.cad_role not in {"model", "plan"}:
            disposition = "out_of_scope"
        else:
            disposition = "graphic_only"
        dispositions[disposition] += 1
        raw_properties = dict(entity.raw_properties or {})
        raw_properties_json = json.dumps(
            raw_properties, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        row = ogr.Feature(entity_table.GetLayerDefn())
        _set(row, {
            "entity_key": entity.entity_key, "source_sha256": entity.source_sha256,
            "source_file": entity.source_file, "cad_handle": entity.handle,
            "cad_layout": entity.layout, "layout_role": entity.layout_role,
            "cad_role": entity.cad_role, "dwg_layer": entity.layer,
            "dwg_type": entity.dwg_type, "block_name": entity.block_name,
            "block_attributes": json.dumps(entity.block_attributes, ensure_ascii=False, sort_keys=True),
            "text": entity.text,
            "owner_handle": entity.owner_handle,
            "dimension_text_override": entity.dimension_text_override,
            "native_points": json.dumps(entity.points, separators=(",", ":")),
            "dimension_value": entity.dimension_value,
            "native_length": entity.native_length,
            "extraction_backend": entity.extraction_backend,
            "reader_backend_status": entity.reader_backend_status,
            "raw_properties": raw_properties_json,
            "aci_color": entity.style.aci_color, "true_color": entity.style.true_color,
            "linetype": entity.style.linetype, "lineweight": entity.style.lineweight,
            "rotation": entity.style.rotation, "disposition": disposition,
            "entity_aci_color": entity.style.entity_aci_color,
            "layer_aci_color": entity.style.layer_aci_color,
            "entity_true_color": entity.style.entity_true_color,
            "layer_true_color": entity.style.layer_true_color,
            "entity_linetype": entity.style.entity_linetype,
            "layer_linetype": entity.style.layer_linetype,
            "entity_lineweight": entity.style.entity_lineweight,
            "layer_lineweight": entity.style.layer_lineweight,
            "scale_x": entity.scale[0], "scale_y": entity.scale[1], "scale_z": entity.scale[2],
        })
        entity_table.CreateFeature(row)

        if entity.dwg_type == "INSERT":
            row = ogr.Feature(block_instance_table.GetLayerDefn())
            _set(row, {
                "entity_key": entity.entity_key, "cad_handle": entity.handle,
                "owner_handle": entity.owner_handle, "cad_layout": entity.layout,
                "cad_role": entity.cad_role, "dwg_layer": entity.layer,
                "raw_block_name": str(raw_properties.get("block_reference_name", "")),
                "effective_block_name": str(
                    raw_properties.get("block_effective_name", "") or entity.block_name
                ),
                "block_attributes": json.dumps(
                    entity.block_attributes, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ),
                "dynamic_properties": json.dumps(
                    raw_properties.get("dynamic_block_properties", {}),
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    allow_nan=False,
                ),
                "insertion_point_native": json.dumps(
                    entity.points[0] if entity.points else None, separators=(",", ":")
                ),
                "scale_x": entity.scale[0], "scale_y": entity.scale[1],
                "scale_z": entity.scale[2], "rotation": entity.style.rotation,
                "extraction_backend": entity.extraction_backend,
                "reader_backend_status": entity.reader_backend_status,
                "raw_properties": raw_properties_json,
            })
            block_instance_table.CreateFeature(row)

        if entity.dwg_type in annotation_carrier_types:
            row = ogr.Feature(annotation_carrier_table.GetLayerDefn())
            _set(row, {
                "entity_key": entity.entity_key, "cad_handle": entity.handle,
                "owner_handle": entity.owner_handle, "carrier_type": entity.dwg_type,
                "cad_layout": entity.layout, "cad_role": entity.cad_role,
                "dwg_layer": entity.layer, "text": entity.text,
                "text_source": str(raw_properties.get("text_source", "")),
                "anchor_native": json.dumps(
                    entity.points[0] if entity.points else None, separators=(",", ":")
                ),
                "extraction_backend": entity.extraction_backend,
                "reader_backend_status": entity.reader_backend_status,
                "raw_properties": raw_properties_json,
            })
            annotation_carrier_table.CreateFeature(row)

    candidate_table = _table(dataset, "feature_candidates", [
        ("feature_key", ogr.OFTString), ("feature_class", ogr.OFTString),
        ("geometry_kind", ogr.OFTString), ("geometry_role", ogr.OFTString),
        ("source_entity_key", ogr.OFTString), ("source_handle", ogr.OFTString),
        ("source_layer", ogr.OFTString), ("attributes", ogr.OFTString),
        ("display_label", ogr.OFTString), ("label_provenance", ogr.OFTString),
    ])
    for feature in features:
        row = ogr.Feature(candidate_table.GetLayerDefn())
        _set(row, {
            "feature_key": feature.feature_key, "feature_class": feature.feature_class,
            "geometry_kind": feature.geometry_kind, "geometry_role": feature.geometry_role,
            "source_entity_key": feature.source_entity_key, "source_handle": feature.source_handle,
            "source_layer": feature.source_layer,
            "attributes": json.dumps(feature.attributes, ensure_ascii=False, sort_keys=True),
            "display_label": feature.display_label,
            "label_provenance": feature.label_provenance,
        })
        candidate_table.CreateFeature(row)

    annotation_table = _table(dataset, "annotation_assignment_candidates", [
        ("annotation_key", ogr.OFTString), ("family_id", ogr.OFTString),
        ("text", ogr.OFTString), ("source_layer", ogr.OFTString),
        ("target_class", ogr.OFTString), ("target_key", ogr.OFTString),
        ("target_handle", ogr.OFTString), ("target_layer", ogr.OFTString),
        ("distance_native_m", ogr.OFTReal),
        ("selected", ogr.OFTInteger), ("status", ogr.OFTString),
        ("rule_id", ogr.OFTString), ("provenance", ogr.OFTString),
    ])
    for candidate in diagnostics.get("semantics", {}).get("annotation_candidates", ()):
        row = ogr.Feature(annotation_table.GetLayerDefn())
        _set(row, {**candidate, "selected": int(bool(candidate.get("selected")))})
        annotation_table.CreateFeature(row)

    relation_table = _table(dataset, "topology_relations", [
        ("relation_key", ogr.OFTString), ("relation_kind", ogr.OFTString),
        ("source_key", ogr.OFTString), ("target_key", ogr.OFTString),
        ("status", ogr.OFTString), ("method", ogr.OFTString),
        ("distance_native_m", ogr.OFTReal), ("evidence_keys", ogr.OFTString),
    ])
    for relation in relations:
        row = ogr.Feature(relation_table.GetLayerDefn())
        _set(row, {
            "relation_key": relation.relation_key, "relation_kind": relation.relation_kind,
            "source_key": relation.source_key, "target_key": relation.target_key,
            "status": relation.status, "method": relation.method,
            "distance_native_m": relation.distance_native_m,
            "evidence_keys": json.dumps(relation.evidence_keys, separators=(",", ":")),
        })
        relation_table.CreateFeature(row)

    segment_table = _table(dataset, "source_segment_occurrences", [
        ("occurrence_key", ogr.OFTString), ("source_role", ogr.OFTString),
        ("parent_key", ogr.OFTString), ("source_entity_key", ogr.OFTString),
        ("source_handle", ogr.OFTString), ("segment_index", ogr.OFTInteger),
        ("start_native", ogr.OFTString), ("end_native", ogr.OFTString),
        ("native_length_m", ogr.OFTReal),
    ])
    route_by_source = {
        feature.source_entity_key: feature
        for feature in features if feature.feature_class == "CABLE"
    }
    segment_parents = []
    for entity in entities:
        if entity.entity_key in route_by_source:
            segment_parents.append(("OPTICAL_CABLE", route_by_source[entity.entity_key].feature_key, entity))
        elif entity.cad_role == "model" and entity.dwg_type in {"LWPOLYLINE", "POLYLINE"} and entity.layer.upper() == "SLING WIRE":
            segment_parents.append(("SLING_SUPPORT", entity.entity_key, entity))
    segment_lookup = {}
    for source_role, parent_key, entity in segment_parents:
        for segment_index, (start, end) in enumerate(zip(entity.points, entity.points[1:])):
            occurrence_key = hashlib.sha256(
                f"{parent_key}|segment|{segment_index}".encode("utf-8")
            ).hexdigest()
            segment_lookup[f"{parent_key}:segment:{segment_index}"] = (source_role, occurrence_key, entity, start, end)
            row = ogr.Feature(segment_table.GetLayerDefn())
            _set(row, {
                "occurrence_key": occurrence_key, "source_role": source_role,
                "parent_key": parent_key, "source_entity_key": entity.entity_key,
                "source_handle": entity.handle, "segment_index": segment_index,
                "start_native": json.dumps(start, separators=(",", ":")),
                "end_native": json.dumps(end, separators=(",", ":")),
                "native_length_m": math.dist(start, end),
            })
            segment_table.CreateFeature(row)

    route_walk_table = _table(dataset, "route_walks", [
        ("route_key", ogr.OFTString), ("source_entity_key", ogr.OFTString),
        ("source_handle", ogr.OFTString), ("segment_occurrences", ogr.OFTInteger),
        ("capacity", ogr.OFTInteger), ("display_label", ogr.OFTString),
        ("geometry_policy", ogr.OFTString),
    ])
    for feature in sorted(route_by_source.values(), key=lambda item: item.source_handle):
        row = ogr.Feature(route_walk_table.GetLayerDefn())
        _set(row, {
            "route_key": feature.feature_key, "source_entity_key": feature.source_entity_key,
            "source_handle": feature.source_handle,
            "segment_occurrences": max(0, len(feature.native_points) - 1),
            "capacity": feature.attributes.get("CAPACITE"),
            "display_label": feature.display_label,
            "geometry_policy": "immutable-source-polyline",
        })
        route_walk_table.CreateFeature(row)

    cable_span_table = _table(dataset, "cable_span_metrics", [
        ("route_key", ogr.OFTString), ("source_entity_key", ogr.OFTString),
        ("source_handle", ogr.OFTString), ("segment_index", ogr.OFTInteger),
        ("source_segment_key", ogr.OFTString),
        ("source_native_length_m", ogr.OFTReal),
        ("dimension_entity_key", ogr.OFTString),
        ("measurement_native_m", ogr.OFTReal),
        ("measurement_delta_m", ogr.OFTReal),
        ("delivery_grid_length_m", ogr.OFTReal),
        ("geodesic_length_m", ogr.OFTReal),
        ("status", ogr.OFTString),
        ("schema_version", ogr.OFTString), ("unit", ogr.OFTString),
    ])
    cable_span_layer = None
    if delivery_transformer is not None:
        cable_span_layer = dataset.CreateLayer(
            "cable_span_segments", delivery_transformer.target, ogr.wkbLineString,
        )
        for field_name, field_type in (
            ("route_key", ogr.OFTString), ("source_entity_key", ogr.OFTString),
            ("source_handle", ogr.OFTString), ("segment_index", ogr.OFTInteger),
            ("source_segment_key", ogr.OFTString),
            ("source_native_length_m", ogr.OFTReal),
            ("dimension_entity_key", ogr.OFTString),
            ("measurement_native_m", ogr.OFTReal),
            ("measurement_delta_m", ogr.OFTReal),
            ("delivery_grid_length_m", ogr.OFTReal),
            ("geodesic_length_m", ogr.OFTReal), ("status", ogr.OFTString),
            ("length_label", ogr.OFTString), ("schema_version", ogr.OFTString),
            ("unit", ogr.OFTString),
        ):
            cable_span_layer.CreateField(ogr.FieldDefn(field_name, field_type))
    for feature in sorted(route_by_source.values(), key=lambda item: item.source_handle):
        metrics = feature.attributes.get("span_metrics")
        expected = max(0, len(feature.native_points) - 1)
        if not isinstance(metrics, list) or len(metrics) != expected:
            raise RuntimeError(
                f"Evidence requires one span metric per CABLE segment for {feature.feature_key}"
            )
        target_points = (
            delivery_transformer.points(feature.native_points)
            if delivery_transformer is not None else None
        )
        for segment_index, metric in enumerate(metrics):
            if metric.get("segment_index") != segment_index:
                raise RuntimeError(
                    f"CABLE span metrics are not source ordered for {feature.feature_key}"
                )
            segment = segment_lookup.get(f"{feature.feature_key}:segment:{segment_index}")
            if segment is None:
                raise RuntimeError(
                    f"CABLE span metric lacks source segment evidence: "
                    f"{feature.feature_key}:segment:{segment_index}"
                )
            row = ogr.Feature(cable_span_table.GetLayerDefn())
            values = {
                "route_key": feature.feature_key,
                "source_entity_key": feature.source_entity_key,
                "source_handle": feature.source_handle,
                "segment_index": segment_index,
                "source_segment_key": segment[1],
                "source_native_length_m": metric.get("source_native_length_m"),
                "dimension_entity_key": metric.get("dimension_entity_key"),
                "measurement_native_m": metric.get("measurement_native_m"),
                "measurement_delta_m": metric.get("measurement_delta_m"),
                "delivery_grid_length_m": metric.get("delivery_grid_length_m"),
                "geodesic_length_m": metric.get("geodesic_length_m"),
                "status": str(metric.get("status", "")),
                "schema_version": "cad2gis.cable_span_metrics.v1",
                "unit": "m",
            }
            _set(row, values)
            cable_span_table.CreateFeature(row)
            if cable_span_layer is not None:
                start, end = target_points[segment_index:segment_index + 2]
                grid_length = math.dist(start, end)
                if abs(grid_length - float(metric["delivery_grid_length_m"])) > 1e-6:
                    raise RuntimeError(
                        f"CABLE span spatial geometry closure failed: "
                        f"{feature.feature_key}:segment:{segment_index}"
                    )
                spatial_row = ogr.Feature(cable_span_layer.GetLayerDefn())
                spatial_row.SetGeometry(_line((start, end)))
                spatial_values = dict(values)
                displayed = (
                    metric.get("measurement_native_m")
                    if metric.get("status") == "measured"
                    else metric.get("delivery_grid_length_m")
                )
                spatial_values["length_label"] = (
                    "" if displayed is None else f"{float(displayed):.3f} m"
                )
                _set(spatial_row, spatial_values)
                cable_span_layer.CreateFeature(spatial_row)

    membership_table = _table(dataset, "route_span_memberships", [
        ("route_key", ogr.OFTString),
        ("segment_index", ogr.OFTInteger), ("source_segment_key", ogr.OFTString),
        ("physical_span_keys", ogr.OFTString), ("dimension_entity_keys", ogr.OFTString),
        ("status", ogr.OFTString), ("method", ogr.OFTString),
    ])
    measure_relations = {
        relation.source_key: relation
        for relation in relations if relation.relation_kind == "measures"
    }
    measures_by_target = defaultdict(list)
    for relation in measure_relations.values():
        measures_by_target[relation.target_key].append(relation)
    support_relations = {
        relation.source_key: relation
        for relation in relations
        if relation.relation_kind == "supported_by"
        and ":endpoint:" in relation.source_key
    }
    route_keys = {feature.feature_key for feature in route_by_source.values()}
    for segment_target, segment in sorted(segment_lookup.items()):
        source_role, occurrence_key, _, _, _ = segment
        if source_role != "OPTICAL_CABLE":
            continue
        owner, _, segment_index_text = segment_target.rpartition(":segment:")
        if owner not in route_keys:
            continue
        matched = sorted(measures_by_target.get(segment_target, ()), key=lambda item: item.source_key)
        physical_keys = set()
        for relation in matched:
            left = support_relations.get(f"{relation.source_key}:endpoint:0")
            right = support_relations.get(f"{relation.source_key}:endpoint:1")
            if left is None or right is None:
                continue
            support_pair = tuple(sorted((left.target_key, right.target_key)))
            physical_keys.add(hashlib.sha256(
                f"OPTICAL_CABLE|{support_pair[0]}|{support_pair[1]}".encode("utf-8")
            ).hexdigest())
        row = ogr.Feature(membership_table.GetLayerDefn())
        _set(row, {
            "route_key": owner,
            "segment_index": int(segment_index_text),
            "source_segment_key": occurrence_key,
            "physical_span_keys": json.dumps(sorted(physical_keys), separators=(",", ":")),
            "dimension_entity_keys": json.dumps(
                [relation.source_key for relation in matched], separators=(",", ":")
            ),
            "status": "accepted" if matched else "unresolved",
            "method": (
                matched[0].method if matched
                else "no-exact-span-dimension"
            ),
        })
        membership_table.CreateFeature(row)

    physical_layer = dataset.CreateLayer("physical_span_evidence", source_srs, ogr.wkbLineString)
    for field_name, field_type in (
        ("physical_span_key", ogr.OFTString), ("span_role", ogr.OFTString),
        ("status", ogr.OFTString), ("source_segment_key", ogr.OFTString),
        ("support_a", ogr.OFTString), ("support_b", ogr.OFTString),
        ("dimension_keys", ogr.OFTString), ("native_length_m", ogr.OFTReal),
    ):
        physical_layer.CreateField(ogr.FieldDefn(field_name, field_type))
    physical_groups = defaultdict(list)
    for dimension_key, measure in measure_relations.items():
        left = support_relations.get(f"{dimension_key}:endpoint:0")
        right = support_relations.get(f"{dimension_key}:endpoint:1")
        segment = segment_lookup.get(measure.target_key)
        if left is None or right is None or segment is None:
            continue
        support_pair = tuple(sorted((left.target_key, right.target_key)))
        physical_groups[(segment[0], support_pair)].append((dimension_key, measure, left, right, segment))
    for (span_role, support_pair), group in sorted(physical_groups.items()):
        dimension_keys = sorted(item[0] for item in group)
        _, _, left, right, segment = group[0]
        if support_pair[0] == support_pair[1]:
            status = "evidence_only_self_loop"
        elif all(item[2].status == "accepted" and item[3].status == "accepted" for item in group):
            status = "accepted"
        else:
            status = "candidate"
        physical_key = hashlib.sha256(
            f"{span_role}|{support_pair[0]}|{support_pair[1]}".encode("utf-8")
        ).hexdigest()
        _, occurrence_key, _, start, end = segment
        row = ogr.Feature(physical_layer.GetLayerDefn())
        row.SetGeometry(_line((start, end)))
        _set(row, {
            "physical_span_key": physical_key, "span_role": span_role,
            "status": status, "source_segment_key": occurrence_key,
            "support_a": support_pair[0], "support_b": support_pair[1],
            "dimension_keys": json.dumps(dimension_keys, separators=(",", ":")),
            "native_length_m": math.dist(start, end),
        })
        physical_layer.CreateFeature(row)

    attachment_table = _table(dataset, "device_attachments", [
        ("relation_key", ogr.OFTString), ("relation_kind", ogr.OFTString),
        ("source_key", ogr.OFTString), ("target_key", ogr.OFTString),
        ("status", ogr.OFTString), ("method", ogr.OFTString),
        ("distance_native_m", ogr.OFTReal),
    ])
    for relation in relations:
        if relation.relation_kind not in {"connects", "identifies"}:
            continue
        row = ogr.Feature(attachment_table.GetLayerDefn())
        _set(row, {
            "relation_key": relation.relation_key, "relation_kind": relation.relation_kind,
            "source_key": relation.source_key, "target_key": relation.target_key,
            "status": relation.status, "method": relation.method,
            "distance_native_m": relation.distance_native_m,
        })
        attachment_table.CreateFeature(row)

    section_table = _table(dataset, "logical_cable_sections", [
        ("route_key", ogr.OFTString), ("status", ogr.OFTString),
        ("reason", ogr.OFTString),
    ])
    for feature in sorted(route_by_source.values(), key=lambda item: item.source_handle):
        row = ogr.Feature(section_table.GetLayerDefn())
        _set(row, {
            "route_key": feature.feature_key, "status": "abstained",
            "reason": "device connection ports are not yet reviewed; source route retained",
        })
        section_table.CreateFeature(row)

    provenance_table = _table(dataset, "field_provenance", [
        ("feature_key", ogr.OFTString), ("feature_class", ogr.OFTString),
        ("field_name", ogr.OFTString), ("field_value", ogr.OFTString),
        ("provenance", ogr.OFTString),
    ])
    for feature in features:
        mandatory = set(LAYER_CONFIGS[feature.feature_class].get("mandatory_fields", ()))
        for field_name in sorted(mandatory | set(feature.attributes) | {"display_label"}):
            if field_name == "display_label":
                value = feature.display_label
                provenance = feature.label_provenance
            else:
                value = feature.attributes.get(field_name)
                provenance = feature.field_provenance.get(field_name)
            if value is None or value == "":
                provenance = provenance or "UNAVAILABLE"
            elif provenance is None:
                raise RuntimeError(
                    f"Non-empty field lacks explicit provenance: "
                    f"{feature.feature_class}.{field_name} ({feature.feature_key})"
                )
            row = ogr.Feature(provenance_table.GetLayerDefn())
            _set(row, {
                "feature_key": feature.feature_key, "feature_class": feature.feature_class,
                "field_name": field_name,
                "field_value": (
                    "" if value is None else
                    json.dumps(
                        value, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    ) if isinstance(value, (dict, list, tuple)) else
                    str(value)
                ),
                "provenance": provenance,
            })
            provenance_table.CreateFeature(row)

    lineage_table = _table(dataset, "feature_lineage", [
        ("feature_key", ogr.OFTString), ("source_entity_key", ogr.OFTString),
        ("operation", ogr.OFTString), ("max_displacement_m", ogr.OFTReal),
        ("detail", ogr.OFTString),
    ])
    for feature in features:
        for lineage in feature.lineage:
            row = ogr.Feature(lineage_table.GetLayerDefn())
            _set(row, {
                "feature_key": feature.feature_key,
                "source_entity_key": lineage.get("source_entity_key", feature.source_entity_key),
                "operation": lineage.get("operation", "identity"),
                "max_displacement_m": float(lineage.get("max_displacement_m", 0.0)),
                "detail": json.dumps(lineage, ensure_ascii=False, sort_keys=True),
            })
            lineage_table.CreateFeature(row)

    unresolved_table = _table(dataset, "unresolved_items", [
        ("kind", ogr.OFTString), ("status", ogr.OFTString),
        ("entity_key", ogr.OFTString), ("family_id", ogr.OFTString),
        ("text", ogr.OFTString), ("source_layer", ogr.OFTString),
        ("detail", ogr.OFTString),
    ])
    for item in unresolved:
        row = ogr.Feature(unresolved_table.GetLayerDefn())
        _set(row, {
            "kind": str(item.get("kind", "")),
            "status": str(item.get("status", "")),
            "entity_key": str(item.get("entity_key", "")),
            "family_id": str(item.get("family_id", "")),
            "text": str(item.get("text", "")),
            "source_layer": str(item.get("source_layer", "")),
            "detail": json.dumps(item, ensure_ascii=False, sort_keys=True),
        })
        unresolved_table.CreateFeature(row)

    ledger = _table(dataset, "conservation_ledger", [
        ("disposition", ogr.OFTString), ("entity_count", ogr.OFTInteger),
    ])
    for disposition, count in sorted(dispositions.items()):
        row = ogr.Feature(ledger.GetLayerDefn())
        _set(row, {"disposition": disposition, "entity_count": count})
        ledger.CreateFeature(row)

    diagnostic_table = _table(dataset, "run_diagnostics", [
        ("stage", ogr.OFTString), ("payload", ogr.OFTString),
    ])
    for stage, payload in diagnostics.items():
        row = ogr.Feature(diagnostic_table.GetLayerDefn())
        _set(row, {"stage": stage, "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True)})
        diagnostic_table.CreateFeature(row)

    port_table = _table(dataset, "connection_port_candidates", [
        ("route_key", ogr.OFTString), ("route_source_handle", ogr.OFTString),
        ("vertex_index", ogr.OFTInteger), ("asset_key", ogr.OFTString),
        ("asset_source_handle", ogr.OFTString), ("block_name", ogr.OFTString),
        ("attachment_kind", ogr.OFTString), ("route_point_native", ogr.OFTString),
        ("port_point_native", ogr.OFTString), ("center_distance_m", ogr.OFTReal),
        ("footprint_distance_m", ogr.OFTReal), ("status", ogr.OFTString),
        ("transform_basis", ogr.OFTString),
    ])
    for candidate in diagnostics.get("topology", {}).get("connection_port_candidates", ()):
        row = ogr.Feature(port_table.GetLayerDefn())
        _set(row, {
            **candidate,
            "route_point_native": json.dumps(candidate["route_point_native"], separators=(",", ":")),
            "port_point_native": json.dumps(candidate["port_point_native"], separators=(",", ":")),
        })
        port_table.CreateFeature(row)

    dimension_statuses = _span_dimension_statuses(relations)
    spans = _line_layer(dataset, "span_dimension_evidence", source_srs)
    routes = _line_layer(dataset, "source_route_evidence", source_srs)
    for entity in entities:
        target = None
        status = "source"
        if entity.dwg_type == "DIMENSION" and entity.layer.upper() == "SPAN CABLE" and len(entity.points) == 2:
            target = spans
            status = dimension_statuses.get(entity.entity_key, "unresolved_endpoint")
        elif entity.entity_key in feature_sources:
            feature = next((item for item in features if item.source_entity_key == entity.entity_key), None)
            if feature is not None and feature.feature_class == "CABLE":
                target = routes
        if target is None:
            continue
        row = ogr.Feature(target.GetLayerDefn())
        row.SetGeometry(_line(entity.points))
        _set(row, {
            "entity_key": entity.entity_key, "cad_handle": entity.handle,
            "dwg_layer": entity.layer, "status": status,
            "measurement_native_m": (
                entity.dimension_value if target is spans
                else sum(math.dist(start, end) for start, end in zip(entity.points, entity.points[1:]))
            ),
        })
        target.CreateFeature(row)

    _write_calibration_evidence(dataset, calibration_audit, target_srs)

    if dataset.CommitTransaction() != 0:
        raise RuntimeError("Could not commit evidence GeoPackage")
    dataset.Close()
    dataset = None


def write_evidence(
    path, entities, features, relations, unresolved, diagnostics, source_srs,
    calibration_audit=None, target_srs=None, delivery_transformer=None,
):
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    staged = stage_dir / destination.name
    try:
        _write_staged(
            staged, entities, features, relations, unresolved, diagnostics, source_srs,
            calibration_audit=calibration_audit, target_srs=target_srs,
            delivery_transformer=delivery_transformer,
        )
        connection = sqlite3.connect(staged)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            conserved = connection.execute("SELECT SUM(entity_count) FROM conservation_ledger").fetchone()[0]
        finally:
            connection.close()
        if integrity != "ok" or conserved != len(entities):
            raise RuntimeError(f"Evidence validation failed: integrity={integrity}, conserved={conserved}")
        os.replace(staged, destination)
    finally:
        if staged.exists():
            staged.unlink()
        shutil.rmtree(stage_dir, ignore_errors=True)
