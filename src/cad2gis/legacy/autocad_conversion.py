"""Historical AutoCAD records-to-GIS conversion, kept for API compatibility.

These semantics belong to the original single-drawing experiment. Shared CAD
record extraction and role partitioning remain in :mod:`cad2gis.reader.autocad`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from cad2gis.reader import autocad as _reader

from .apd_rules import is_telecom_block, link_apd_annotations


def _point_wkt(x, y):
    return f"POINT ({x:.12g} {y:.12g})"


def _line_wkt(points):
    return "LINESTRING (" + ", ".join(f"{x:.12g} {y:.12g}" for x, y in points) + ")"


def _polygon_wkt(points):
    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return "POLYGON ((" + ", ".join(f"{x:.12g} {y:.12g}" for x, y in ring) + "))"


def _evidence_item(record, source_name, kind):
    return {
        "output_kind": kind,
        "source_file": source_name,
        "layout": record["layout"],
        "cad_role": record["cad_role"],
        "handle": record["handle"],
        "layer": record["layer"],
        "dwg_type_name": record["dwg_type_name"],
        "text": record["text"],
        "block_name": record["block_name"],
        "aci_color": record["aci_color"],
        "true_color": record["true_color"],
        "linetype": record["linetype"],
        "lineweight": record["lineweight"],
        "rotation": record["rotation"],
        "dimension_value": record.get("dimension_value"),
        "native_points": json.dumps(record.get("points", []), separators=(",", ":")),
        "terminal_disposition": "unresolved",
    }


def _feature_item(record, source_name, reproject_point, assign_fc, classify_block, extract_attributes):
    points = [reproject_point(float(x), float(y)) for x, y in record["points"]]
    if not points:
        return None
    cx = sum(point[0] for point in points) / len(points)
    cy = sum(point[1] for point in points) / len(points)
    if not (-180 <= cx <= 180 and -90 <= cy <= 90):
        return None
    is_insert = record["object_name"] in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}
    if is_insert:
        if not is_telecom_block(record["block_name"]):
            return None
        fc_name = classify_block(record["block_name"])
        fc_geom_type, confidence, method = "Point", 1.0, "apd_block_family"
    else:
        fc_name, fc_geom_type, confidence, method = assign_fc(record["layer"], record["text"])
        if fc_name == "fc_misc":
            return None
        if fc_name == "IMB" and record["layer"].strip().casefold() != "home number":
            return None
        if fc_name == "CABLE" and record["object_name"] not in {
            "ACDBLINE", "ACDBLWPOLYLINE", "ACDBPOLYLINE", "ACDB2DPOLYLINE", "ACDB3DPOLYLINE",
        }:
            return None
        if fc_name in {"BOITE", "PTECH", "SITE"} and record["object_name"] != "ACDBPOINT":
            return None
    attrs = extract_attributes(record["text"], fc_name)
    for key, value in record["block_attributes"].items():
        if key in {"CODE", "REF_PM", "REF_SRO", "ORIGINE", "EXTREMITE", "TYPE", "TYPE_CABLE", "STATUT"}:
            attrs.setdefault(key, value)
    direct_label = str(attrs.get("CODE", "")).strip()
    polygon_class = fc_name in {"ZNRO", "ZPM"}
    endpoints_closed = len(points) >= 3 and (
        abs(points[0][0] - points[-1][0]) <= 1e-10
        and abs(points[0][1] - points[-1][1]) <= 1e-10
    )
    if polygon_class and len(points) < 3:
        return None
    if polygon_class and not (record["closed"] or endpoints_closed):
        return None
    is_closed = record["closed"] or (polygon_class and endpoints_closed)
    if is_closed and len(points) >= 3:
        wkt = _polygon_wkt(points)
    elif len(points) >= 2:
        wkt = _line_wkt(points)
    else:
        wkt = _point_wkt(cx, cy)
    return {
        "output_kind": "feature",
        "global_id": -1,
        "source_file": source_name,
        "layer": record["layer"],
        "layout": record["layout"],
        "cad_role": record["cad_role"],
        "cad_handle": record["handle"],
        "dwg_type": -1,
        "dwg_type_name": record["dwg_type_name"],
        "wkt": wkt,
        "points": points,
        "native_points": list(record["points"]),
        "native_centroid": record["centroid"],
        "centroid": (cx, cy),
        "is_closed": is_closed,
        "fc_name": fc_name,
        "fc_geom_type": fc_geom_type,
        "classification_confidence": confidence,
        "classification_method": method,
        "text": record["text"],
        "annotation_text": "",
        "display_label": direct_label,
        "label_method": "DWG_DIRECT" if direct_label else "UNAVAILABLE",
        "attrs": attrs,
        "is_insert_node": is_insert,
        "block_name": record["block_name"],
        "aci_color": record["aci_color"],
        "true_color": record["true_color"],
        "linetype": record["linetype"],
        "lineweight": record["lineweight"],
        "rotation": record["rotation"],
        "geographic_outlier": False,
    }


def build_items_from_records(records, source_name, reproject_point, assign_fc, classify_block, extract_attributes):
    """Turn neutral COM records into GIS features and non-spatial evidence."""
    items, annotations = [], []
    for record in records:
        role = record["cad_role"]
        source_evidence = _evidence_item(record, source_name, "source_evidence")
        items.append(source_evidence)
        if role in {"topology", "splicing"}:
            source_evidence["terminal_disposition"] = "annotation"
            items.append(_evidence_item(record, source_name, "topology_evidence"))
            continue
        if role == "style_legend":
            source_evidence["terminal_disposition"] = "legend"
            items.append(_evidence_item(record, source_name, "style_evidence"))
            continue
        if role == "design_summary":
            source_evidence["terminal_disposition"] = "annotation"
            if record["text"]:
                items.append(_evidence_item(record, source_name, "summary_evidence"))
            continue
        if role not in {"model", "plan"}:
            source_evidence["terminal_disposition"] = "out_of_scope"
            continue
        if record["object_name"] == "ACDBDIMENSION":
            source_evidence["terminal_disposition"] = "annotation"
            items.append(_evidence_item(record, source_name, "dimension_evidence"))
            continue
        if record["object_name"] in _reader._TEXT_OBJECTS:
            fc_name, _, _, _ = assign_fc(record["layer"], record["text"])
            if fc_name == "IMB" and record["layer"].strip().casefold() == "home number":
                feature = _feature_item(
                    record, source_name, reproject_point, assign_fc, classify_block, extract_attributes,
                )
                if feature is not None:
                    code = record["text"].strip()
                    if code:
                        feature["attrs"].setdefault("CODE", code)
                        feature["code_source"] = "dwg_text"
                        feature["display_label"] = code
                        feature["label_method"] = "DWG_DIRECT"
                    items.append(feature)
                    source_evidence["terminal_disposition"] = "mapped"
                continue
            point = reproject_point(*record["centroid"])
            if -180 <= point[0] <= 180 and -90 <= point[1] <= 90:
                annotations.append({
                    "text": record["text"],
                    "centroid": point,
                    "native_centroid": record["centroid"],
                    "attrs": extract_attributes(record["text"], None),
                    "layer": record["layer"],
                    "layout": record["layout"],
                })
            source_evidence["terminal_disposition"] = "annotation"
            continue
        feature = _feature_item(record, source_name, reproject_point, assign_fc, classify_block, extract_attributes)
        if feature is not None:
            items.append(feature)
            source_evidence["terminal_disposition"] = "mapped"
        else:
            source_evidence["terminal_disposition"] = "graphic_only"

    features = [item for item in items if item.get("output_kind") == "feature"]
    leftovers = link_apd_annotations(annotations, features, sigma_native=15.0)
    for annotation in leftovers:
        ax, ay = annotation["centroid"]
        items.append({
            "output_kind": "annotation_evidence",
            "source_file": source_name,
            "layout": annotation["layout"],
            "cad_role": "plan_annotation",
            "handle": "",
            "layer": annotation["layer"],
            "dwg_type_name": "TEXT",
            "text": annotation["text"],
            "block_name": "",
            "aci_color": 256,
            "true_color": "",
            "linetype": "ByLayer",
            "lineweight": -1,
            "rotation": 0.0,
        })
    return items


def _bind_entity_keys(items, source):
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    for item in items:
        handle = str(item.get("handle", item.get("cad_handle", "")))
        layout = str(item.get("layout", ""))
        if handle:
            item["entity_key"] = hashlib.sha256(
                f"{source_hash}|{handle}|{layout}".encode("utf-8")
            ).hexdigest()
        item["source_sha256"] = source_hash
    return items


def _items_from_grouped(
    grouped, source, reproject_point, assign_fc, classify_block, extract_attributes,
):
    model_has_entities = False
    for _, role, records in grouped:
        if role != "model":
            continue
        for record in records:
            if record["cad_role"] != "model" or record["object_name"] in _reader._TEXT_OBJECTS:
                continue
            if is_telecom_block(record.get("block_name", "")):
                model_has_entities = True
                break
            fc_name, _, _, _ = assign_fc(record.get("layer", ""), record.get("text", ""))
            if fc_name != "fc_misc":
                model_has_entities = True
                break
        if model_has_entities:
            break

    selected = []
    for _, role, records in grouped:
        if role == "plan" and model_has_entities:
            # Paper layouts repeat the model through viewports.  Their legend
            # and summary remain evidence, but plan geometry is not duplicated.
            for record in records:
                if record["cad_role"] == "plan":
                    _reader._reclassify(
                        record,
                        "layout",
                        "paper_layout_duplicate_model",
                        "model-space telecom entities make this paper layout duplicative",
                    )
        selected.extend(records)
    return build_items_from_records(
        selected, source.name, reproject_point, assign_fc, classify_block, extract_attributes,
    )


def read_dwg_with_autocad(
    dwg_path,
    reproject_point,
    assign_fc,
    classify_block,
    extract_attributes,
    *,
    accoreconsole=None,
    timeout=None,
    compatibility_policy=_reader.BULK_POLICY_STRICT,
):
    """Read a DWG directly through AutoCAD; never export or create a DXF."""
    if os.name != "nt":
        raise RuntimeError("Direct AutoCAD DWG reading requires Windows")
    source = Path(dwg_path).resolve()
    if source.suffix.casefold() != ".dwg":
        raise ValueError("The direct AutoCAD reader accepts DWG input only")
    try:
        grouped = _reader._extract_records_with_core_console(
            source,
            accoreconsole=accoreconsole,
            timeout=timeout,
            compatibility_policy=compatibility_policy,
        )
        items = _items_from_grouped(
            grouped, source, reproject_point, assign_fc, classify_block, extract_attributes,
        )
        return _bind_entity_keys(items, source)
    except Exception as bulk_error:
        _reader._authorize_com_fallback(bulk_error)

    pythoncom, application, created, database, opened_document = _reader._open_autocad_database(source)
    try:
        grouped = _reader._collect_records(
            database,
            assign_fc=assign_fc,
            reader_backend_status="fallback_after_core_console_failure",
        )
        items = _items_from_grouped(
            grouped, source, reproject_point, assign_fc, classify_block, extract_attributes,
        )
        return _bind_entity_keys(items, source)
    finally:
        if opened_document is not None:
            try:
                _reader._retry_com(lambda: opened_document.Close(False))
            except Exception:
                pass
        database = None
        if created:
            try:
                _reader._retry_com(application.Quit)
            except Exception:
                pass
        application = None
        pythoncom.CoUninitialize()
