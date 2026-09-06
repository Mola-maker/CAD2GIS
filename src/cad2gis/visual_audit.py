"""Read-only onsite CAD/GeoPackage comparison in the drawing coordinate frame.

This verifier does not reuse the production geometry materializer or transformer.
It inverts the manifest-declared CRS/GEODATA operation with pyproj, reconstructs
source curves analytically, and compares every delivered source group with the
independent source snapshot plan. Raster previews are secondary visual evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, box as geometry_box, shape
from shapely.ops import transform, unary_union

from cad2gis.native_runtime import ensure_osgeo_runtime


class UnsupportedAuditTransform(ValueError):
    """A transform is outside this independent verifier's declared scope."""


GEOMETRY_CHANGE_OPERATIONS = {
    "collocate_with_support", "bridge_cable_endpoint_to_pole",
    "repair_boundary_polygon", "rectangular_frame_centroid",
}


def verify_number_relation(row, source_by_key, metric_scale):
    detail = json.loads(row["detail"] or "{}")
    label = source_by_key.get(row["source_entity_key"], {})
    target = source_by_key.get(detail.get("target_source_entity_key"), {})
    value = str(detail.get("selected_value", ""))
    text_matches = value.isascii() and value.isdecimal() and str(label.get("text", "")).strip() == value
    measured = None
    if label.get("centroid") and target.get("centroid"):
        measured = math.dist(label["centroid"][:2], target["centroid"][:2]) * metric_scale
    recorded = detail.get("distance_native_m")
    distance_matches = isinstance(recorded, (int, float)) and measured is not None and measured <= 20 * metric_scale and abs(measured - recorded * metric_scale) <= 1e-6
    contract_matches = detail.get("geometry_changed") is False and detail.get("field_name") == "DEVICE_NUMBER" and detail.get("review_status") == "required" and detail.get("method") == "nearest_unused_integer_label_within_20_native_m"
    verified = bool(label and target and text_matches and distance_matches and contract_matches)
    return {"feature_key": row["feature_key"], "label_entity_key": row["source_entity_key"],
            "target_source_entity_key": detail.get("target_source_entity_key"), "value": value,
            "text_matches_independent_source": text_matches, "measured_distance_m": measured,
            "recorded_distance_native_m": recorded, "distance_matches": distance_matches,
            "relation_contract_matches": contract_matches, "source_relation_verified": verified,
            "engineering_review_required": True,
            "interpretation": "Exact selected source text and distance are traceable; nearest integer semantics are not independently accepted."}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalized(value):
    return " ".join(str(value).split()).casefold()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_db(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def write_csv(path, rows):
    if not rows:
        Path(path).write_text("", encoding="utf-8-sig")
        return
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def arc_points(start, end, bulge, tolerance=0.001):
    if abs(bulge) < 1e-14:
        return [start, end]
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(dx, dy)
    if not chord:
        raise ValueError("nonzero bulge on a zero-length chord")
    theta = 4 * math.atan(bulge)
    offset = chord * (1 - bulge * bulge) / (4 * bulge)
    center = ((start[0] + end[0]) / 2 - dy / chord * offset,
              (start[1] + end[1]) / 2 + dx / chord * offset)
    radius = math.dist(start, center)
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - tolerance / radius)))
    count = max(2, math.ceil(abs(theta) / max(step, 1e-8)))
    if count > 100000:
        raise ValueError("curve sample count exceeds audit bound")
    angle = math.atan2(start[1] - center[1], start[0] - center[0])
    return [start] + [
        (center[0] + radius * math.cos(angle + theta * i / count),
         center[1] + radius * math.sin(angle + theta * i / count))
        for i in range(1, count)
    ] + [end]


def source_shape(entity, expected_type=None):
    if entity.get("dwg_type") in {"SPLINE", "ELLIPSE", "IMAGE", "OLE2FRAME", "HATCH", "WIPEOUT"}:
        # Control points, centres or insertion anchors do not describe these
        # complete curves/images/regions; never turn them into fake geometry.
        return None
    points = [tuple(map(float, p[:2])) for p in entity.get("points", [])]
    if not points:
        return None
    if len(points) == 1 and entity.get("dwg_type") != "CIRCLE":
        return Point(points[0])
    facts = entity.get("curve_facts") or {}
    normal = facts.get("normal")
    if normal and (abs(float(normal[0])) > 1e-12 or abs(float(normal[1])) > 1e-12):
        return None
    if entity.get("dwg_type") == "CIRCLE":
        parameters = facts.get("primitive_parameters") or {}
        center, radius = parameters.get("center"), parameters.get("radius")
        if center is None or radius is None:
            return None
        radius = float(radius)
        if radius <= 0:
            return None
        step = 2 * math.acos(max(-1, min(1, 1 - .001 / radius)))
        count = max(32, math.ceil(2 * math.pi / max(step, 1e-8)))
        if count > 100000:
            return None
        points = [(center[0] + radius * math.cos(i * 2 * math.pi / count),
                   center[1] + radius * math.sin(i * 2 * math.pi / count))
                  for i in range(count + 1)]
        return Polygon(points) if expected_type == "Polygon" else LineString(points)
    bulges = facts.get("bulges") or [0] * len(points)
    closed = bool(entity.get("closed"))
    dense = []
    segment_count = len(points) if closed and points[-1] != points[0] else len(points) - 1
    for index in range(segment_count):
        section = arc_points(points[index], points[(index + 1) % len(points)],
                             float(bulges[index]) if index < len(bulges) else 0)
        dense.extend(section if not dense else section[1:])
    if len(dense) < 2:
        return Point(points[0])
    if expected_type == "Polygon" and closed and len(dense) >= 4:
        return Polygon(dense)
    return LineString(dense)


def inverse_operation(manifest):
    crs = manifest["crs"]
    if crs.get("calibration", {}).get("status") not in {None, "not_provided"}:
        raise UnsupportedAuditTransform("This independent audit only supports uncalibrated nominal runs")
    if crs.get("osm_anchor"):
        raise UnsupportedAuditTransform("OSM anchor inversion is not implemented; refuse inferred registration")
    contract = crs["unit_crs_contract"]
    coordinate_scale = float(contract["source_coordinate_scale_to_m"])
    if not contract.get("source_coordinate_scale_reviewed") or not math.isfinite(coordinate_scale) or coordinate_scale <= 0:
        raise UnsupportedAuditTransform("A finite positive reviewed coordinate scale is required")
    registration = contract.get("coordinate_registration")
    projection = Transformer.from_crs(crs["target_crs"], crs["source_crs"], always_xy=True)
    factor = float(contract["source_to_crs_axis_factor"])
    if registration:
        factor = float(registration["horizontal_unit_scale"]) * float(registration["user_scale_factor"])
        nx, ny = registration["north_direction"]
        design_x, design_y = registration["design_point"]
        reference_x, reference_y = registration["reference_point"]

    def inverse(x, y, z=None):
        # Shapely may first pass coordinate arrays, then retry scalar points.
        if not isinstance(x, (int, float)):
            converted = [inverse(float(a), float(b)) for a, b in zip(x, y)]
            return tuple(p[0] for p in converted), tuple(p[1] for p in converted)
        grid_x, grid_y = projection.transform(x, y)
        if registration:
            dx, dy = grid_x - reference_x, grid_y - reference_y
            return design_x + (dx * ny + dy * nx) / factor, design_y + (-dx * nx + dy * ny) / factor
        return grid_x / factor, grid_y / factor

    return inverse


def geometries(geometry):
    if geometry.geom_type == "Point":
        return [("point", list(geometry.coords))]
    if geometry.geom_type in {"LineString", "LinearRing"}:
        return [("line", list(geometry.coords))]
    if geometry.geom_type == "Polygon":
        return [("line", list(geometry.exterior.coords))] + [
            ("line", list(ring.coords)) for ring in geometry.interiors]
    return [item for child in geometry.geoms for item in geometries(child)]


def vertex_graph(values):
    """Coordinate graph only: do not insert intersections or infer connections."""
    adjacency = defaultdict(set)
    edges = Counter()
    for geometry in values:
        for kind, points in geometries(geometry):
            if kind != "line":
                continue
            for left, right in zip(points, points[1:]):
                left = tuple(round(float(v), 6) for v in left[:2])
                right = tuple(round(float(v), 6) for v in right[:2])
                if left == right:
                    continue
                edges[tuple(sorted((left, right)))] += 1
                adjacency[left].add(right)
                adjacency[right].add(left)
    remaining = set(adjacency)
    components = 0
    while remaining:
        stack = [remaining.pop()]
        components += 1
        while stack:
            for neighbor in adjacency[stack.pop()] & remaining:
                remaining.remove(neighbor)
                stack.append(neighbor)
    return {"vertex_count": len(adjacency), "edge_count": len(edges),
            "edge_multiplicity_total": sum(edges.values()), "component_count": components,
            "degree_histogram": dict(Counter(len(x) for x in adjacency.values())),
            "edge_multiset_sha256": hashlib.sha256(json.dumps(sorted(edges.items()), separators=(",", ":")).encode()).hexdigest()}


def render(path, source, delivered, comparisons, report, focus=False):
    image = Image.new("RGB", (2160, 1400), "#f6f8fc")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 18) if font_path.exists() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 13) if font_path.exists() else font
    title = ImageFont.truetype(str(font_path), 28) if font_path.exists() else font
    heading = f"{report['drawing_name']} | Independent source and delivery audit"
    while font_path.exists() and draw.textlength(heading, font=title) > 2085 and title.size > 14:
        title = ImageFont.truetype(str(font_path), title.size - 1)
    draw.text((35, 22), heading, fill="#152b4a", font=title)
    draw.text((35, 64), f"Common frame: original CAD coordinates; inverse {report['target_crs']} + declared registration; distances use reviewed coordinate scale", fill="#475569", font=font)
    draw.text((35, 92), f"Source census {report['source_plan_count']:,}; rendered {report['source_plan_geometries_rendered']:,}. Secondary geometry render; no original-font/style or survey accuracy claim.", fill="#475569", font=font)
    bounds = unary_union([x["local_geometry"] for x in delivered] if focus else [g for _, g in source if g is not None]).bounds
    if focus:
        margin = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1) * .05
        bounds = (bounds[0] - margin, bounds[1] - margin, bounds[2] + margin, bounds[3] + margin)
    palette = {"CABLE": "#146edc", "BOITE": "#e24b34", "PTECH": "#137344", "EMR": "#bd1e79", "IMB": "#7c3dad", "SITE": "#ff9100", "INFRASTRUCTURE": "#0097a7", "ZPM": "#3d9a71", "ZNRO": "#bc7b17"}

    def pane(box, extent, heading, layers, labels=False):
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=12, fill="white", outline="#d0d7e2", width=2)
        draw.text((x0 + 15, y0 + 10), heading, fill="#183653", font=font)
        minx, miny, maxx, maxy = extent
        scale = min((x1 - x0 - 50) / max(maxx - minx, 1), (y1 - y0 - 80) / max(maxy - miny, 1))
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        def pixel(point):
            return ((x0 + x1) / 2 + (point[0] - cx) * scale,
                    (y0 + y1 + 35) / 2 - (point[1] - cy) * scale)
        for geom, color, width in layers:
            if geom is None:
                continue
            geom = geom.intersection(geometry_box(*extent))
            if geom.is_empty:
                continue
            for kind, coords in geometries(geom):
                mapped = [pixel(p) for p in coords]
                if kind == "point":
                    x, y = mapped[0]
                    draw.ellipse((x - width - 1, y - width - 1, x + width + 1, y + width + 1), fill=color)
                elif len(mapped) >= 2:
                    draw.line(mapped, fill=color, width=width)
        if labels:
            for entity, geom in source:
                text = entity.get("text", "")
                if text and geom is not None and geom.geom_type == "Point":
                    x, y = pixel(geom.coords[0])
                    if x0 < x < x1 - 40 and y0 + 45 < y < y1 - 20:
                        draw.text((x + 2, y + 1), text[:40], fill="#787f88", font=small)
        return pixel

    pane((25, 135, 1065, 950), bounds,
         "A. Source at delivery extent; see full-source image for remote entities" if focus else "A. Full source plan: business + annotation + graphic geometry",
         [(g, "#727c89", 1) for _, g in source], labels=focus)
    pane((1090, 135, 2135, 950), bounds, "B. Overlay: source gray; delivery by class; includes derived ZNRO",
         [(g, "#c5cbd3", 1) for _, g in source] + [(x["local_geometry"], palette.get(x["layer"], "#146edc"), 2) for x in delivered])
    offsets = sorted([x for x in comparisons if x.get("hausdorff_native_m") is not None], key=lambda x: x["hausdorff_native_m"], reverse=True)
    point_offsets = [x for x in offsets if x["layer"] == "BOITE"]
    if point_offsets:
        worst = point_offsets[0]
        actual = [x for x in delivered if x["source_key"] == worst["source_entity_key"] and x["layer"] == "BOITE"][0]["local_geometry"]
        original = next(g for e, g in source if e["entity_key"] == worst["source_entity_key"])
        center = actual.centroid
        extent = (center.x - 18, center.y - 13, center.x + 18, center.y + 13)
        pixel = pane((25, 970, 1065, 1340), extent,
                     f"C. BOITE {worst['source_handle']}: {worst['hausdorff_native_m']:.6f} m source-relative geometry change",
                     [(g, "#c5cbd3", 1) for _, g in source if g is not None and g.intersects(Polygon([(extent[0],extent[1]),(extent[2],extent[1]),(extent[2],extent[3]),(extent[0],extent[3])]))] + [(original, "#202832", 4), (actual, "#e24b34", 4)])
        draw.line([pixel(original.centroid.coords[0]), pixel(actual.centroid.coords[0])], fill="#f18a19", width=3)
    x, y = 1110, 990
    lines = ["D. Numerical and field checks", f"Delivery: {report['delivery_feature_count']} features; {report['source_plan_count']} source plan entities",
             f"CABLE source groups: {report['cable_group_count']}; max Hausdorff {report['cable_max_hausdorff_native_m']:.9f} m",
             f"CABLE max absolute length difference: {report['cable_max_length_difference_native_m']:.9f} m",
             f"Explicit geometry changes: {report['explicit_relocation_count']} groups; no direct source: {report['derived_without_source_count']} groups",
             f"Present fields lacking provenance: {report['present_fields_without_provenance']}",
             f"Audited blank field values: {report['blank_delivery_field_count']}; unexpected value mismatch: {report['unexpected_field_value_mismatches']}",
             "See geometry-comparison.csv, field-lineage.csv and source-dispositions.csv.",
             "No surveyed GCP supplied: absolute positional accuracy remains unverified."]
    for index, line in enumerate(lines):
        draw.text((x, y + index * 34), line, fill="#183653" if index == 0 else "#475569", font=font)
    image.save(path)


def render_changes(path, source, delivered, comparisons, report):
    """Show the largest recorded differences, including repaired region spikes."""
    changes = sorted((row for row in comparisons if (row.get("hausdorff_native_m") or 0) > .01),
                     key=lambda row: row["hausdorff_native_m"], reverse=True)[:6]
    if not changes:
        return
    source_by_key = {entity["entity_key"]: entity for entity, _ in source}
    canvas = Image.new("RGB", (2160, 1750), "#f6f8fc")
    draw = ImageDraw.Draw(canvas)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 20) if font_path.exists() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 16) if font_path.exists() else font
    draw.text((30, 20), "Largest source-relative geometry changes | gray: source; blue: delivered", fill="#183653", font=font)
    draw.text((30, 54), "Declared derivation is a review item, not an acceptance. Distances include representation changes for line/polygon to point.", fill="#475569", font=small)
    for index, comparison in enumerate(changes):
        x0, y0 = 25 + (index % 2) * 1070, 100 + (index // 2) * 545
        x1, y1 = x0 + 1045, y0 + 525
        draw.rounded_rectangle((x0, y0, x1, y1), radius=10, fill="white", outline="#ccd7e5", width=2)
        key, layer = comparison["source_entity_key"], comparison["layer"]
        actual = unary_union([item["local_geometry"] for item in delivered if item["source_key"] == key and item["layer"] == layer])
        original = source_shape(source_by_key[key], actual.geom_type)
        bounds = (min(original.bounds[0], actual.bounds[0]), min(original.bounds[1], actual.bounds[1]),
                  max(original.bounds[2], actual.bounds[2]), max(original.bounds[3], actual.bounds[3]))
        margin = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1) * .1
        bounds = (bounds[0] - margin, bounds[1] - margin, bounds[2] + margin, bounds[3] + margin)
        cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
        scale = min((x1 - x0 - 60) / (bounds[2] - bounds[0]), (y1 - y0 - 145) / (bounds[3] - bounds[1]))
        def pixel(point):
            return ((x0 + x1) / 2 + (point[0] - cx) * scale,
                    (y0 + y1 + 45) / 2 - (point[1] - cy) * scale)
        draw.text((x0 + 16, y0 + 12), f"{layer} {comparison['source_handle']} | {comparison['hausdorff_native_m']:.6f} m", fill="#183653", font=font)
        draw.text((x0 + 16, y0 + 42), comparison["status"], fill="#9a4a05", font=small)
        operations = comparison["operations"]
        draw.text((x0 + 16, y0 + 67), operations[:110], fill="#526779", font=small)
        for geometry, color, width in [(original, "#7e8793", 5), (actual, "#087bcc", 2)]:
            for kind, points in geometries(geometry):
                mapped = [pixel(point) for point in points]
                if kind == "point":
                    x, y = mapped[0]
                    draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color)
                elif len(mapped) > 1:
                    draw.line(mapped, fill=color, width=width)
        draw.text((x0 + 16, y1 - 30), f"Common source coordinate frame | {report['source_crs']} registration inverted", fill="#526779", font=small)
    canvas.save(path)


def verified_semantic_labels(properties, source_by_key, manifest, source_manifest, target_source_key):
    """Independently bind a delivered label to the pinned revision and raw text."""
    receipt = manifest.get("semantic_revision")
    if not isinstance(receipt, dict) or receipt.get("snapshot_sha256") != source_manifest.get("snapshot_sha256"):
        return []
    required = {"schema_version", "job_id", "generation", "revision", "snapshot_sha256",
                "manifest_sha256", "semantic_gpkg_sha256", "decisions_sha256", "authority"}
    if not required <= receipt.keys():
        return []
    labels = []
    for operation in json.loads(properties.get("lineage_json") or "[]"):
        if operation.get("operation") != "apply_committed_semantic_revision":
            continue
        if any(operation.get(key) != value for key, value in receipt.items()):
            continue
        key = operation.get("label_entity_key")
        text = source_by_key.get(key, {}).get("text")
        expected_provenance = f"SEMANTIC_REVISION:{receipt['job_id']}:{receipt['revision']}:{key}"
        if (operation.get("source_entity_key") == target_source_key and text
                and operation.get("geometry_changed") is False
                and properties.get("label_provenance") == expected_provenance):
            labels.append({"entity_key": key, "text": text})
    return labels


def audit(source_run, run, output, partition=None):
    source_run, run, output = map(lambda p: Path(p).resolve(), (source_run, run, output))
    output.mkdir(parents=True, exist_ok=True)
    manifest = read_json(run / "run_manifest.json")
    delivery_artifact = manifest["delivery_partitions"][partition] if partition else manifest["artifacts"]["delivery"]
    delivery_path = Path(delivery_artifact["path"]).resolve()
    if not delivery_path.is_relative_to(run):
        raise ValueError("Manifest delivery path must stay inside its canonical run")
    if digest(delivery_path) != delivery_artifact["sha256"]:
        raise ValueError("Manifest-bound delivery artifact hash mismatch")
    source_manifest = read_json(source_run / "source_manifest.json")
    plan = [json.loads(line) for line in (source_run / "plan_entities.jsonl").read_text(encoding="utf-8").splitlines()]
    records = [json.loads(line) for line in (source_run / "reader_records.jsonl").read_text(encoding="utf-8").splitlines()]
    source_sha = manifest["source"]["sha256"]
    if any(x["source_sha256"] != source_sha for x in plan + records):
        raise ValueError("Independent source plan has a different source hash")
    if source_manifest["source"]["sha256"] != source_sha:
        raise ValueError("Independent source snapshot has a different source hash")
    source_artifacts = {"plan_entities": source_run / "plan_entities.jsonl",
                        "reader_records": source_run / "reader_records.jsonl"}
    verified_source_hashes = {name: digest(path) for name, path in source_artifacts.items()}
    for name, actual in verified_source_hashes.items():
        if actual != source_manifest["artifacts"][name]["sha256"]:
            raise ValueError(f"Independent source artifact hash mismatch: {name}")
    before = {name: digest(run / name) for name in ("delivery.gpkg", "evidence.gpkg", "run_manifest.json")}
    if partition:
        before[delivery_path.relative_to(run).as_posix()] = digest(delivery_path)
    inverse = inverse_operation(manifest)
    metric_scale = float(manifest["crs"]["unit_crs_contract"]["source_coordinate_scale_to_m"])
    ensure_osgeo_runtime()
    from osgeo import ogr
    content_db = read_db(delivery_path)
    spatial_tables = {row["table_name"] for row in content_db.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")}
    content_db.close()
    dataset = ogr.Open(str(delivery_path), 0)
    delivered, groups, missing_geometries, nonspatial_layers = [], defaultdict(list), [], []
    for layer in dataset:
        if layer.GetName() not in spatial_tables:
            nonspatial_layers.append({"table": layer.GetName(), "row_count": layer.GetFeatureCount()})
            continue
        for row in layer:
            if row.GetGeometryRef() is None:
                missing_geometries.append({"layer": layer.GetName(), "fid": row.GetFID()})
                continue
            properties = {row.GetFieldDefnRef(i).GetName(): row.GetField(i) for i in range(row.GetFieldCount())}
            geometry = shape(json.loads(row.GetGeometryRef().ExportToJson()))
            item = {"layer": layer.GetName(), "fid": row.GetFID(), "source_key": properties["source_entity_key"], "properties": properties, "local_geometry": transform(inverse, geometry)}
            delivered.append(item)
            groups[(item["layer"], item["source_key"])].append(item)
    dataset = None
    by_key = {x["entity_key"]: x for x in plan}
    all_source_by_key = {x["entity_key"]: x for x in records}
    all_source_by_key.update(by_key)
    evidence = read_db(run / "evidence.gpkg")
    source_evidence = {x["entity_key"]: dict(x) for x in evidence.execute("SELECT entity_key,disposition,text,block_attributes,raw_properties FROM cad_entities")}
    comparisons = []
    for (layer, key), values in sorted(groups.items()):
        actual = unary_union([x["local_geometry"] for x in values])
        source = by_key.get(key)
        expected = source_shape(source, actual.geom_type) if source else None
        operations = sorted({x.get("operation", "") for value in values for x in json.loads(value["properties"].get("lineage_json") or "[]")})
        distance = expected.hausdorff_distance(actual) * metric_scale if expected is not None else None
        length_delta = (actual.length - expected.length) * metric_scale if expected is not None and actual.geom_type in {"LineString", "MultiLineString"} else None
        comparisons.append({"layer": layer, "source_entity_key": key, "source_handle": values[0]["properties"].get("source_handle"), "delivery_count": len(values), "source_in_independent_plan": source is not None, "source_type": source.get("dwg_type", "") if source else "", "operations": "|".join(operations), "hausdorff_native_m": distance, "delivery_union_length_native_m": actual.length * metric_scale if length_delta is not None else None, "source_curve_length_native_m": expected.length * metric_scale if length_delta is not None else None, "length_difference_native_m": length_delta, "status": "derived_without_direct_source" if source is None else "unsupported_source_geometry" if expected is None else "explicit_derived_displacement" if distance > .01 and set(operations) & GEOMETRY_CHANGE_OPERATIONS else "unexplained_difference" if distance > .01 else "within_numeric_and_curve_sampling_tolerance"})
        comparisons[-1].update({
            "distance_basis": f"source_{expected.geom_type}_to_delivery_{actual.geom_type}" if expected is not None else "no_complete_source_geometry",
            "point_anchor_displacement_m": distance if expected is not None and expected.geom_type == actual.geom_type == "Point" else None,
        })
    provenance = defaultdict(dict)
    for row in evidence.execute("SELECT * FROM field_provenance"):
        provenance[row["feature_key"]][row["field_name"]] = dict(row)
    source_features = defaultdict(set)
    for row in evidence.execute("SELECT feature_key,source_entity_key FROM feature_lineage"):
        source_features[row["source_entity_key"]].add(row["feature_key"])
    number_relations = defaultdict(list)
    for row in evidence.execute("SELECT * FROM feature_lineage WHERE operation='select_device_number_label'"):
        number_relations[row["feature_key"]].append(verify_number_relation(row, all_source_by_key, metric_scale))
    selected_annotations = defaultdict(list)
    for row in evidence.execute("SELECT * FROM annotation_assignment_candidates WHERE selected=1"):
        selected_annotations[row["target_key"]].append(dict(row))
    fields = []
    for item in delivered:
        props = item["properties"]
        candidates = [key for key in source_features[item["source_key"]] if any(x["feature_class"] == item["layer"] for x in provenance[key].values())]
        if props.get("route_key"):
            candidates = [props["route_key"]]
        source = by_key.get(item["source_key"], {})
        raw = source.get("raw_properties") or {}
        text_values = [source.get("text", "")] + [str(x) for x in (source.get("block_attributes") or {}).values()] + [str(x) for x in raw.get("owned_attribute_texts", [])]
        links = [link for key in set(candidates + [item["source_key"]]) for link in selected_annotations[key]]
        verified_link_texts = [link["text"] for link in links if all_source_by_key.get(link["annotation_key"], {}).get("text") == link["text"]]
        semantic_labels = verified_semantic_labels(props, all_source_by_key, manifest, source_manifest, item["source_key"])
        integer_relations = [relation for key in candidates for relation in number_relations[key] if relation["target_source_entity_key"] == item["source_key"]]
        verified_integer_texts = [relation["value"] for relation in integer_relations if relation["source_relation_verified"]]
        for name, value in props.items():
            rows = [provenance[key][name] for key in candidates if name in provenance[key]]
            if not rows and name not in {"display_label", "CODE"}:
                continue
            record = rows[0] if rows else {}
            present = value is not None and value != ""
            source_text_exact = str(value) in text_values if present else False
            matches = present and any(normalized(value) == normalized(t) for t in text_values if t)
            linked_match = normalized(value) in {normalized(t) for t in verified_link_texts} if present else False
            linked_match = linked_match or (present and str(value) in {label["text"] for label in semantic_labels})
            parts = str(value).split(" \u00b7 ")
            compound_match = len(parts) > 1 and all(normalized(part) in {normalized(t) for t in text_values + verified_link_texts} for part in parts)
            heuristic_match = bool(verified_integer_texts) and all(normalized(part) in {normalized(t) for t in text_values + verified_link_texts + verified_integer_texts} for part in parts) and any(normalized(part) in {normalized(t) for t in verified_integer_texts} for part in parts)
            comparison = "missing_value" if not present else "exact" if str(value) == record.get("field_value") else "not_in_parent_provenance" if not rows else "segment_derived_value" if item["layer"] == "CABLE" and name in {"LONGUEUR", "source_cad_length_m", "delivery_grid_length_m", "geodesic_length_m"} else "unexpected_mismatch"
            fields.append({"layer": item["layer"], "delivery_fid": item["fid"], "source_entity_key": item["source_key"], "source_handle": props.get("source_handle"), "field_name": name, "delivery_value": value, "recorded_value": record.get("field_value", ""), "provenance": record.get("provenance", props.get("label_provenance", "") if name == "display_label" else ""), "missing_value": not present, "recorded_comparison": comparison, "direct_source_text_match": "exact" if source_text_exact else "normalized" if matches else "verified_selected_annotation" if linked_match else "verified_annotation_and_attribute_composition" if compound_match else "verified_nearby_integer_relation_requires_review" if heuristic_match else "no_exact_match_requires_relation_or_derivation" if name in {"display_label", "CODE"} and present else "not_a_text_check", "source_texts": json.dumps(text_values, ensure_ascii=False), "selected_annotation_keys": "|".join(link["annotation_key"] for link in links), "verified_selected_annotation_texts": json.dumps(verified_link_texts, ensure_ascii=False), "heuristic_number_label_keys": "|".join(relation["label_entity_key"] for relation in integer_relations), "verified_heuristic_number_texts": json.dumps(verified_integer_texts, ensure_ascii=False), "replacement_character_in_delivery": "\ufffd" in str(value), "replacement_character_in_source_text": any("\ufffd" in t for t in text_values)})
    disposition_rows = []
    delivery_by_source = defaultdict(Counter)
    for item in delivered:
        delivery_by_source[item["source_key"]][item["layer"]] += 1
    for entity in plan:
        key = entity["entity_key"]
        instance = (entity.get("raw_properties") or {}).get("plan_domain") or {}
        definition_present = instance.get("definition_entity_key") in source_evidence
        parents = instance.get("instance_path", [])
        parents_present = bool(parents) and all(parent in source_evidence for parent in parents)
        meaning = "delivered" if delivery_by_source[key] else "expanded_instance_with_definition_and_parents_in_ledger" if key not in source_evidence and definition_present and parents_present else "expanded_instance_or_source_absent_from_ledger" if key not in source_evidence else "not_delivered_not_automatically_missing"
        disposition_rows.append({"entity_key": key, "source_handle": entity["handle"], "dwg_type": entity["dwg_type"], "source_layer": entity["layer"], "text": entity.get("text", ""), "canonical_disposition": source_evidence.get(key, {}).get("disposition", "absent_from_canonical_evidence"), "delivery_feature_count": sum(delivery_by_source[key].values()), "delivery_layers": json.dumps(delivery_by_source[key], sort_keys=True), "meaning": meaning, "definition_entity_key": instance.get("definition_entity_key", ""), "definition_retained_in_canonical_ledger": definition_present, "instance_parent_keys": "|".join(parents), "all_instance_parents_retained_in_canonical_ledger": parents_present, "instance_parent_dispositions": "|".join(source_evidence.get(parent, {}).get("disposition", "absent") for parent in parents)})
    evidence.close()
    cables = [x for x in comparisons if x["layer"] == "CABLE"]
    cable_sources = [by_key[x["source_entity_key"]] for x in cables if x["source_entity_key"] in by_key]
    straight_cables = len(cable_sources) == len(cables) and all(
        x["dwg_type"] in {"LINE", "POLYLINE", "LWPOLYLINE"}
        and not any(abs(float(b)) > 1e-14 for b in x.get("curve_facts", {}).get("bulges", []))
        for x in cable_sources)
    vertex_topology = {"scope": "Coordinate vertex graph at 1e-6 native-unit quantization; no crossing nodes inserted; not equipment connectivity", "status": "not_compared_curve_sampling_or_missing_source"}
    if straight_cables:
        source_graph = vertex_graph([source_shape(x) for x in cable_sources])
        delivery_graph = vertex_graph([x["local_geometry"] for x in delivered if x["layer"] == "CABLE"])
        vertex_topology.update({"status": "equal" if source_graph == delivery_graph else "different", "source": source_graph, "delivery": delivery_graph})
    report = {"schema_version": "cad2gis.independent-visual-audit.v1", "run_dir": str(run), "source_run": str(source_run), "source_sha256": source_sha, "source_snapshot_schema": source_manifest.get("schema_version"), "coordinate_frame": "native CAD metres", "comparison_basis": "Independent pyproj inverse CRS plus manifest GEODATA inverse; source snapshot plan; analytic bulges sampled at <= 0.001 native-unit chord error. No production geometry/transformer reuse.", "absolute_accuracy_verified": False, "source_plan_count": len(plan), "delivery_feature_count": len(delivered), "delivery_layer_counts": dict(Counter(x["layer"] for x in delivered)), "geometry_status_counts": dict(Counter(x["status"] for x in comparisons)), "cable_group_count": len(cables), "cable_max_hausdorff_native_m": max((x["hausdorff_native_m"] or 0 for x in cables), default=0), "cable_max_length_difference_native_m": max((abs(x["length_difference_native_m"] or 0) for x in cables), default=0), "explicit_relocation_count": sum(x["status"] == "explicit_derived_displacement" for x in comparisons), "derived_without_source_count": sum(x["status"] == "derived_without_direct_source" for x in comparisons), "present_fields_without_provenance": sum(not x["missing_value"] and not x["provenance"] for x in fields), "blank_delivery_field_count": sum(x["missing_value"] for x in fields), "labels_with_replacement_characters": sum(x["field_name"] == "display_label" and x["replacement_character_in_delivery"] for x in fields), "labels_replacement_already_in_source": sum(x["field_name"] == "display_label" and x["replacement_character_in_delivery"] and x["replacement_character_in_source_text"] for x in fields), "source_disposition_counts": dict(Counter(x["canonical_disposition"] for x in disposition_rows)), "source_not_directly_delivered_count": sum(x["delivery_feature_count"] == 0 for x in disposition_rows), "immutable_artifacts_before": before}
    report.update({"drawing_name": Path(manifest["source"]["path"]).stem,
                   "coordinate_frame": "native CAD coordinates; reported distances scaled to metres by reviewed coordinate scale",
                   "source_crs": manifest["crs"]["source_crs"], "target_crs": manifest["crs"]["target_crs"],
                   "reviewed_coordinate_unit_m": metric_scale,
                   "dwg_insertion_unit_m": manifest["crs"]["unit_crs_contract"]["source_geometry_unit"]["metres_per_unit"],
                   "source_reader_record_count": len(records),
                   "field_audit_scope": "Business/source-derived fields represented in evidence.field_provenance plus CODE/display_label; parent cable lengths and exported segment lengths are explicitly distinct",
                   "unexpected_field_value_mismatches": sum(x["recorded_comparison"] == "unexpected_mismatch" for x in fields),
                   "label_source_check_counts": dict(Counter(x["direct_source_text_match"] for x in fields if x["field_name"] == "display_label")),
                   "source_plan_entities_absent_from_canonical_ledger": sum(x["canonical_disposition"] == "absent_from_canonical_evidence" for x in disposition_rows)})
    if partition:
        report["drawing_name"] += f" | partition {partition}"
    report["cable_coordinate_vertex_graph"] = vertex_topology
    report.update({
        "verifier_implementation_sha256": digest(Path(__file__)),
        "verifier_implementation_path": str(Path(__file__).resolve()),
        "delivery_scope": partition or "main",
        "delivery_gpkg_path": str(delivery_path),
        "delivery_gpkg_sha256_verified": delivery_artifact["sha256"],
        "declared_delivery_counts": delivery_artifact.get("delivery_counts", manifest["delivery_counts"]),
        "canonical_run_status": manifest.get("run_status"),
        "delivery_null_geometry_features": missing_geometries,
        "delivery_nonspatial_tables_excluded_from_feature_geometry_audit": nonspatial_layers,
        "source_disposition_meaning_counts": dict(Counter(x["meaning"] for x in disposition_rows)),
        "unexplained_geometry_changes": [x for x in comparisons if x["status"] == "unexplained_difference"],
        "explicit_geometry_changes": [x for x in comparisons if x["status"] == "explicit_derived_displacement"],
        "derived_without_direct_source": [x for x in comparisons if x["status"] == "derived_without_direct_source"],
        "source_coverage_limit": "All independent source plan entities are censused. Unexported graphic/annotation entities and expanded instances are not automatically missing business assets; exact definitions and parent retention are reported separately. No automatic claim of complete semantic coverage.",
        "geometry_comparison_tolerance_m": .01,
        "geometry_comparison_limit": "The 0.01 m threshold distinguishes numerical/sampling differences from material changes in source-relative geometry; it is not a survey accuracy tolerance or acceptance threshold for intentional changes.",
        "labels_without_verified_direct_or_selected_text": sum(x["field_name"] == "display_label" and x["direct_source_text_match"] == "no_exact_match_requires_relation_or_derivation" for x in fields),
        "labels_with_verified_heuristic_relation_requiring_review": sum(x["field_name"] == "display_label" and x["direct_source_text_match"] == "verified_nearby_integer_relation_requires_review" for x in fields),
        "device_number_source_relation_checks": [relation for relations in number_relations.values() for relation in relations],
    })
    write_csv(output / "geometry-comparison.csv", comparisons)
    write_csv(output / "field-lineage.csv", fields)
    write_csv(output / "source-dispositions.csv", disposition_rows)
    source_geometries = [(x, source_shape(x)) for x in plan]
    report["source_artifacts_sha256_verified"] = verified_source_hashes
    report["source_plan_geometries_rendered"] = sum(g is not None for _, g in source_geometries)
    report["source_plan_geometries_unavailable"] = [x["entity_key"] for x, g in source_geometries if g is None]
    render(output / "source-delivery-overlay.png", source_geometries, delivered, comparisons, report)
    render(output / "source-delivery-focus.png", source_geometries, delivered, comparisons, report, focus=True)
    render_changes(output / "geometry-change-details.png", source_geometries, delivered, comparisons, report)
    report["immutable_artifacts_after"] = {name: digest(run / name) for name in before}
    report["immutable_artifacts_unchanged"] = report["immutable_artifacts_before"] == report["immutable_artifacts_after"]
    report["source_artifacts_unchanged"] = verified_source_hashes == {
        name: digest(path) for name, path in source_artifacts.items()}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


def write_index(output):
    """Create a local, script-free overview of the latest audited run per DWG."""
    output = Path(output).resolve()
    reports, partition_reports = [], []
    for path in output.rglob("report.json"):
        value = read_json(path)
        if value.get("schema_version") == "cad2gis.independent-visual-audit.v1" and value.get("source_sha256"):
            (reports if value.get("delivery_scope", "main") == "main" else partition_reports).append((path, value))
    latest = {}
    for path, value in sorted(reports, key=lambda item: item[0].stat().st_mtime_ns):
        latest[value["source_sha256"]] = (path, value)
    corpus_path = output.parent / "corpus.json"
    corpus = read_json(corpus_path) if corpus_path.exists() else []
    servers_path = output.parent / "web-review" / "servers.json"
    servers = read_json(servers_path) if servers_path.exists() else {}
    partition_servers_path = output.parent / "web-review" / "partition-servers.json"
    partition_servers = read_json(partition_servers_path) if partition_servers_path.exists() else {}
    case_ids = {case["sha256"]: case["id"] for case in corpus}
    status_rows, matrix, pending = [], [], []
    for case in corpus:
        case_id, source_sha = case["id"], case["sha256"]
        title = html.escape(Path(case["source"]).name)
        source_path = output.parent / "sources-v2" / case_id / "source" / "source_manifest.json"
        source_url = Path(os.path.relpath(source_path, output)).as_posix()
        attempts = sorted((output.parent / "canonical").glob(f"*/{case_id}/report.json"), key=lambda p: p.stat().st_mtime_ns)
        attempt_path, attempt = None, {}
        if attempts:
            attempt_path = attempts[-1]
            attempt = read_json(attempt_path)
        attempt_status = attempt.get("status", "尚无结束记录")
        attempt_url = Path(os.path.relpath(attempt_path, output)).as_posix() if attempt_path else ""
        audit_path, reviewed = latest.get(source_sha, (None, {}))
        if reviewed:
            source_path = Path(reviewed["source_run"]) / "source_manifest.json"
            source_url = Path(os.path.relpath(source_path, output)).as_posix()
        delivery_count = reviewed.get("delivery_feature_count", "尚无已核验成果")
        audit_url = audit_path.parent.relative_to(output).as_posix() if audit_path else ""
        delivery_path = Path(reviewed["run_dir"]) / "delivery.gpkg" if reviewed else None
        delivery_url = Path(os.path.relpath(delivery_path, output)).as_posix() if delivery_path else ""
        attempt_link = f'<a href="{attempt_url}">{html.escape(str(attempt_status))}</a>' if attempt_url else html.escape(str(attempt_status))
        visual_link = f'<a href="#{case_id}">查看已核验版本</a>' if reviewed else f'<a href="#{case_id}">待核验 / 阻塞说明</a>'
        download = f'<a href="{delivery_url}">成果 GPKG</a>' if delivery_url else "尚无已核验成果文件"
        server = servers.get(case_id, {})
        server_matches = reviewed and server.get("status") == "ready" and Path(server.get("run_dir", "")).resolve() == Path(reviewed["run_dir"]).resolve()
        review_url = server.get("url", "") if server_matches else ""
        gcp = f'<br><a href="{html.escape(review_url, quote=True)}">本图网页 / 人工 GCP</a>' if review_url else "<br>本图 GCP 网页待启动或待绑定当前成果"
        source_gpkg_url = Path(os.path.relpath(source_path.parent / "source.gpkg", output)).as_posix()
        run_status = reviewed.get("canonical_run_status", "CONDITIONAL（详见成果清单）") if reviewed else "待转换 / 核验"
        matrix.append(f'<tr><td>{case_id}<br>{title}</td><td><a href="{source_url}">已完成原图提取</a><br><a href="{source_gpkg_url}">原图 GPKG</a></td><td>{delivery_count}<br>{html.escape(str(run_status))}</td><td>{attempt_link}</td><td>{visual_link}<br>{download}{gcp}</td></tr>')
        status_rows.append({"id": case_id, "drawing": Path(case["source"]).name, "source_sha256": source_sha,
                            "source_snapshot_manifest": source_url, "last_canonical_attempt_status": attempt_status,
                            "last_canonical_attempt_report": attempt_url,
                            "latest_audited_delivery_features": delivery_count,
                            "latest_visual_audit": f"{audit_url}/report.json" if audit_url else "",
                            "audited_delivery_gpkg": delivery_url, "review_and_gcp_url": review_url,
                            "canonical_run_status": run_status, "absolute_accuracy_verified": False})
        if not reviewed:
            error = attempt.get("error", "当前尚无已完成的独立视觉核验。处理过程与历史失败保留在执行记录中。")
            pending.append(f'<article id="{case_id}"><h2>{case_id} · {title}</h2><p class="state">尚无已完成的成果视觉核验；最新正式转换记录：{attempt_link}</p><p>{html.escape(str(error))}</p><p><a href="{source_url}">原图提取清单及证据路径</a>。原图提取成功不能替代正式成果验收；此处不展示其他图纸的结果。</p></article>')
    write_csv(output / "drawing-status.csv", status_rows)
    cards, rows = [], []
    for path, value in sorted(latest.values(), key=lambda item: item[1]["drawing_name"]):
        folder = path.parent.relative_to(output).as_posix()
        title = html.escape(value["drawing_name"])
        changes = value.get("geometry_status_counts", {}).get("unexplained_difference", 0)
        missing = value.get("source_disposition_meaning_counts", {}).get("expanded_instance_or_source_absent_from_ledger", "未分类")
        expanded = value.get("source_disposition_meaning_counts", {}).get("expanded_instance_with_definition_and_parents_in_ledger", "未分类")
        labels = value.get("label_source_check_counts", {})
        row = {"drawing": value["drawing_name"], "source_sha256": value["source_sha256"],
               "delivery_features": value["delivery_feature_count"], "source_plan_entities": value["source_plan_count"],
               "source_rendered_entities": value["source_plan_geometries_rendered"],
               "unexplained_geometry_groups": changes, "explicit_change_groups": value["explicit_relocation_count"],
               "cable_max_displacement_m": value["cable_max_hausdorff_native_m"],
               "field_mismatches": value["unexpected_field_value_mismatches"],
               "present_fields_without_provenance": value["present_fields_without_provenance"],
               "blank_field_values": value["blank_delivery_field_count"],
               "expanded_instances_with_retained_definition_and_parents": expanded,
               "source_or_instance_without_ledger_binding": missing,
               "absolute_accuracy_verified": False, "audit_report": str(path), "canonical_run": value["run_dir"]}
        rows.append(row)
        figure = "source-delivery-focus.png" if (path.parent / "source-delivery-focus.png").exists() else "source-delivery-overlay.png"
        case_id = case_ids.get(value["source_sha256"], value["source_sha256"][:12])
        cards.append(f'''<article id="{case_id}"><h2>{case_id} · {title}</h2><p class="state">{html.escape(str(value.get('canonical_run_status', 'CONDITIONAL（详见成果清单）')))} · 未解释的几何变化：{changes} 组 · 已声明派生变化：{value['explicit_relocation_count']} 组（仍须工程审查，可能改变原图精度）</p>
<a href="{folder}/{figure}"><img loading="lazy" src="{folder}/{figure}" alt="{title} 原 CAD 坐标系下源图与成果叠加" /></a>
<p>成果 {value['delivery_feature_count']} 个要素；原图计划域 {value['source_plan_count']} 项，绘出 {value['source_plan_geometries_rendered']} 项。CABLE 最大源图相对偏差 {value['cable_max_hausdorff_native_m']:.9f} m。</p>
<p>字段与记录来源不一致 {value['unexpected_field_value_mismatches']} 项；有值但无来源声明 {value['present_fields_without_provenance']} 项；已检查的空字段值 {value['blank_delivery_field_count']} 项。标签未能直接核对到原文字或已选注记 {value.get('labels_without_verified_direct_or_selected_text', labels.get('no_exact_match_requires_relation_or_derivation', 0))} 项，需关系来源证据或人工审查。另有 {value.get('labels_with_verified_heuristic_relation_requiring_review', 0)} 项邻近数字关系已核对原文、实体键与距离，但数字的工程含义仍须人工审查。来源声明不等于原文链路完整。标签检查：{html.escape(json.dumps(labels, ensure_ascii=False))}。</p>
<p>未单独物化但定义与所有 INSERT 父项仍在证据库的图块实例：{expanded}；缺少该绑定：{missing}。这些计数不能直接解释为遗漏业务资产。</p>
<nav><a href="{folder}/source-delivery-overlay.png">全图范围</a>{f'<a href="{folder}/geometry-change-details.png">最大几何差异放大图</a>' if (path.parent / 'geometry-change-details.png').exists() else ''}<a href="{folder}/geometry-comparison.csv">几何逐项表</a><a href="{folder}/field-lineage.csv">字段逐项表</a><a href="{folder}/source-dispositions.csv">源图去向表</a><a href="{folder}/report.json">证据与 SHA</a></nav></article>''')
    write_csv(output / "summary.csv", rows)
    latest_partitions = {}
    for path, value in sorted(partition_reports, key=lambda item: item[0].stat().st_mtime_ns):
        latest_partitions[(value["source_sha256"], value["delivery_scope"])] = (path, value)
    partition_rows = []
    for path, value in latest_partitions.values():
        folder = path.parent.relative_to(output).as_posix()
        delivery_url = Path(os.path.relpath(value["delivery_gpkg_path"], output)).as_posix()
        title = html.escape(value["drawing_name"])
        partition_server = partition_servers.get(value["delivery_scope"], {})
        server_matches = partition_server.get("status") == "ready" and Path(partition_server.get("delivery_gpkg", "")).resolve() == Path(value["delivery_gpkg_path"]).resolve()
        review_url = partition_server.get("url", "") if server_matches else ""
        web_link = f'<a href="{html.escape(review_url, quote=True)}">本分区网页 / GCP</a>' if review_url else ""
        metadata_note = "网页部分概要计数仍沿用主成果，当前分区实际数量以本核验表和图层计数为准。" if server_matches and partition_server.get("metadata_warning") else ""
        partition_rows.append({"drawing_id": case_ids.get(value["source_sha256"], ""), "partition": value["delivery_scope"], "delivery_features": value["delivery_feature_count"], "delivery_gpkg": delivery_url, "review_and_gcp_url": review_url, "audit_report": f"{folder}/report.json", "unexplained_geometry_groups": value["geometry_status_counts"].get("unexplained_difference", 0), "field_mismatches": value["unexpected_field_value_mismatches"]})
        cards.append(f'''<article id="partition-{html.escape(value['delivery_scope'])}"><h2>独立分区成果 · {title}</h2><p class="state">{html.escape(str(value.get('canonical_run_status')))} · 本分区 {value['delivery_feature_count']} 个导出要素；与主成果或其他分区可能有重复资产，不能直接相加为唯一资产数量。</p><a href="{folder}/source-delivery-focus.png"><img loading="lazy" src="{folder}/source-delivery-focus.png" alt="{title} 分区源图叠加" /></a><p>图层数量：{html.escape(json.dumps(value['delivery_layer_counts'], ensure_ascii=False))}。未解释几何变化 {value['geometry_status_counts'].get('unexplained_difference', 0)} 组；字段与来源台账不一致 {value['unexpected_field_value_mismatches']} 项；绝对 GCP 精度未验收。{metadata_note}</p><nav><a href="{delivery_url}">本分区 GPKG</a>{web_link}<a href="{folder}/geometry-comparison.csv">几何逐项表</a><a href="{folder}/field-lineage.csv">字段逐项表</a><a href="{folder}/report.json">分区核验与 SHA</a></nav></article>''')
    write_csv(output / "partition-summary.csv", partition_rows)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CAD2GIS 现场视觉核验</title>
<style>body{margin:0;background:#edf2f7;color:#19324b;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1260px;margin:auto;padding:30px 24px}h1{font-size:30px}h2{font-size:19px;overflow-wrap:anywhere}article,.intro{padding:22px;background:white;border:1px solid #cbd5e1;border-radius:12px;margin:24px 0}img{width:100%;height:auto;border:1px solid #d5dde5}a{color:#075ea8}nav{display:flex;flex-wrap:wrap;gap:18px}.state{font-weight:650;color:#8a4200}p{overflow-wrap:anywhere}small{color:#526779}table{width:100%;border-collapse:collapse;font-size:14px;background:white}td,th{padding:12px;text-align:left;border:1px solid #ccd8e4;overflow-wrap:anywhere}td:first-child{max-width:420px}th{background:#e0ebf5}</style><main>
<h1>CAD2GIS 现场视觉核验</h1><section class="intro"><p>各图以原始 CAD 坐标为共同基准，将成果按清单中的 CRS 与 GEODATA 逆变换，独立比较源几何、线路长度、坐标顶点图及字段来源。点击图片查看清晰大图；图表下方可打开每一条差异和源实体去向。</p>
<p>这里是二次几何绘图，未复现 AutoCAD 原字体、线型、遮挡与全部实体显示。保留全图范围，同时提供成果范围放大图，远处图形仍在全图与源实体台账中。0.01 m 仅作为数值/采样差异区分阈值，不能替代工程验收精度。</p>
<p><strong>尚无独立测量 GCP，绝对定位精度未验证。</strong>点击下表“本图网页 / 人工 GCP”，进入对应图纸的“空间配准 / GCP 与独立检查点”。当前入口不等于已完成校准，预览与最终拟合模型尚需统一。</p>
<p><a href="drawing-status.csv">全部图纸状态 CSV</a> · <a href="summary.csv">主成果核验汇总 CSV</a> · <a href="partition-summary.csv">独立分区核验 CSV</a>。表格覆盖全部输入图纸，明确区分历史已核验成果和最新重放状态；下方同一原 DWG 显示最后一次已执行的主成果核验，独立分区另列图片及下载，旧版报告仍保留在相应目录。Manado 还包括 <a href="#partition-EMR28560">EMR28560 分区</a> 和 <a href="#partition-EMR29619">EMR29619 分区</a>，不能仅查看主成果。各图均未完成独立 GCP 验收；表格只列出已经启动并绑定当前已核验成果的各图网页入口。</p></section>
'''+('<table><thead><tr><th>工程图纸</th><th>源图提取</th><th>最近已核验要素数</th><th>最新正式转换尝试</th><th>视觉核验与成果</th></tr></thead><tbody>'+"\n".join(matrix)+'</tbody></table>' if matrix else '')+"\n".join(cards+pending)+"<small>核验检查成果、证据及原图快照 SHA；未修改上述输入文件。完整性和语义正确性仍需结合图层映射、原始图纸与工程解释逐项审查。</small></main></html>"
    (output / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps({"index": str(output / "index.html"), "drawing_count": len(corpus) or len(rows), "audited_drawing_count": len(rows)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run")
    parser.add_argument("--run")
    parser.add_argument("--partition", help="Exact partition identifier recorded in the canonical run manifest")
    parser.add_argument("--output", required=True)
    parser.add_argument("--index", action="store_true", help="Build an index from existing audit reports under --output")
    args = parser.parse_args()
    if args.index:
        write_index(args.output)
        raise SystemExit(0)
    if not args.source_run or not args.run:
        parser.error("--source-run and --run are required for an audit")
    try:
        audit(args.source_run, args.run, args.output, partition=args.partition)
    except UnsupportedAuditTransform as exc:
        output = Path(args.output).resolve()
        output.mkdir(parents=True, exist_ok=True)
        report = {"schema_version": "cad2gis.independent-visual-audit.v1", "status": "SKIPPED_UNSUPPORTED_TRANSFORM", "run_dir": str(Path(args.run).resolve()), "source_run": str(Path(args.source_run).resolve()), "reason": str(exc), "absolute_accuracy_verified": False}
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
