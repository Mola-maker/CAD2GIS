"""Source-bound semantic compilation between CAD facts and GIS delivery.

The model-facing side may select only observed candidate, entity, class and
label identifiers.  Geometry, coordinates, lengths and label text are copied
from ``source.gpkg`` by this deterministic compiler.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


PREPARE_SCHEMA = "cad2gis.semantic_prepare.v2"
DECISION_SCHEMA = "cad2gis.semantic_decisions.v1"
SEMANTIC_SCHEMA = "cad2gis.semantic_gpkg.v1"
TERMINAL_STATES = (
    "CONSUMED_BY_FEATURE",
    "RETAINED_AS_REFERENCE",
    "EXCLUDED_AS_DOCUMENTATION",
    "UNRESOLVED",
)
SEMANTIC_CLASSES = (
    "NETWORK_ROUTE",
    "NETWORK_SEGMENT",
    "SUPPORT",
    "DISTRIBUTION_NODE",
    "ACCESS_NODE",
    "CENTRAL_SITE",
    "HANDHOLE",
    "PREMISE",
    "BUILDING",
    "ZONE",
    "ADDRESS",
    "REFERENCE_ROAD",
    "GENERIC_ASSET",
)
SOURCE_LAYERS = (
    "source_points",
    "source_lines",
    "source_polygons",
    "source_text",
    "source_blocks",
    "source_metadata",
)


class SemanticContractError(ValueError):
    """A semantic proposal escaped its source-bound evidence contract."""


def semantic_decision_json_schema() -> dict[str, Any]:
    """Strict model-output schema; geometry and numeric CAD facts are absent."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://cad2gis.local/schema/semantic-decisions-v1.json",
        "title": DECISION_SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions", "batch_decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["assembly_id", "terminal_state"],
                    "properties": {
                        "assembly_id": {"type": "string", "minLength": 64, "maxLength": 64},
                        "terminal_state": {"type": "string", "enum": list(TERMINAL_STATES)},
                        "semantic_class": {"type": "string", "enum": ["", *SEMANTIC_CLASSES]},
                        "semantic_subtype": {"type": "string", "maxLength": 128},
                        "source_entity_keys": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
                        "source_label_entity_key": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "evidence": {"type": "array", "uniqueItems": True, "items": {"type": "string", "maxLength": 512}},
                        "evidence_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 24, "maxLength": 24}},
                    },
                },
            },
            "batch_decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["batch_id", "terminal_state"],
                    "properties": {
                        "batch_id": {"type": "string", "minLength": 24, "maxLength": 24},
                        "terminal_state": {"type": "string", "enum": ["RETAINED_AS_REFERENCE", "EXCLUDED_AS_DOCUMENTATION", "UNRESOLVED"]},
                        "evidence": {"type": "array", "uniqueItems": True, "items": {"type": "string", "maxLength": 512}},
                    },
                },
            },
        },
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
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


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SemanticContractError(f"{label} must be a JSON object")
    return value


def _source_run(value: str | Path) -> tuple[Path, Path, dict[str, Any]]:
    root = Path(value).expanduser().resolve()
    gpkg = root / "source.gpkg"
    manifest_path = root / "source_manifest.json"
    if not gpkg.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(
            f"source run must contain source.gpkg and source_manifest.json: {root}"
        )
    manifest = _object(json.loads(manifest_path.read_text(encoding="utf-8")), "source manifest")
    if manifest.get("status") != "SOURCE_EXPORTED":
        raise SemanticContractError("source manifest is not SOURCE_EXPORTED")
    expected = ((manifest.get("artifacts") or {}).get("source_gpkg") or {}).get("sha256")
    actual = _sha256_path(gpkg)
    if expected != actual:
        raise SemanticContractError("source.gpkg digest does not match source_manifest.json")
    return root, gpkg, manifest


def _centroid(row: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        value = json.loads(str(row.get("native_centroid") or "null"))
        if isinstance(value, list) and len(value) >= 2:
            x, y = float(value[0]), float(value[1])
            if math.isfinite(x) and math.isfinite(y):
                return x, y
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


def _native_points(row: Mapping[str, Any]) -> list[tuple[float, float]]:
    try:
        values = json.loads(str(row.get("native_points") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    result: list[tuple[float, float]] = []
    if not isinstance(values, list):
        return result
    for value in values:
        try:
            if not isinstance(value, list) or len(value) < 2:
                continue
            point = float(value[0]), float(value[1])
        except (TypeError, ValueError, OverflowError):
            continue
        if all(math.isfinite(coordinate) for coordinate in point):
            result.append(point)
    return result


def _rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    result: list[dict[str, Any]] = []
    for table in SOURCE_LAYERS:
        for raw in connection.execute(
            f'SELECT *, ? AS source_table FROM "{table}" ORDER BY entity_key',
            (table,),
        ):
            row = dict(raw)
            row.pop("geom", None)
            result.append(row)
    return result


def _label_index(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, str, int, int], list[Mapping[str, Any]]],
    dict[tuple[str, str], float],
]:
    rows = list(rows)
    values = [row for row in rows if row["source_table"] == "source_text" and str(row.get("text") or "").strip()]
    points = [(row, _centroid(row)) for row in values]
    points = [(row, point) for row, point in points if point is not None]
    if not points:
        return {}, {}
    partitions: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        point = _centroid(row)
        if point is not None:
            partitions[(str(row.get("cad_layout") or ""), str(row.get("cad_role") or ""))].append(point)
    cells: dict[tuple[str, str], float] = {}
    for partition, values in partitions.items():
        xs = sorted({point[0] for point in values})
        ys = sorted({point[1] for point in values})
        gaps = [
            b - a
            for axis in (xs, ys)
            for a, b in zip(axis, axis[1:])
            if b > a
        ]
        if gaps:
            typical = statistics.median(gaps)
            cells[partition] = max(typical * 8.0, 1e-9)
        else:
            cells[partition] = 1.0
    grid: dict[tuple[str, str, int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row, point in points:
        partition = (str(row.get("cad_layout") or ""), str(row.get("cad_role") or ""))
        cell = cells[partition]
        grid[(
            *partition,
            math.floor(point[0] / cell),
            math.floor(point[1] / cell),
        )].append(row)
    return grid, cells


def _nearby_labels(
    row: Mapping[str, Any],
    grid: Mapping[tuple[str, str, int, int], list[Mapping[str, Any]]],
    cells: Mapping[tuple[str, str], float],
) -> list[dict[str, Any]]:
    point = _centroid(row)
    if point is None:
        return []
    layout = str(row.get("cad_layout") or "")
    cad_role = str(row.get("cad_role") or "")
    cell = cells.get((layout, cad_role), 1.0)
    gx, gy = math.floor(point[0] / cell), math.floor(point[1] / cell)
    candidates: list[tuple[float, Mapping[str, Any]]] = []
    for x in range(gx - 2, gx + 3):
        for y in range(gy - 2, gy + 3):
            for label in grid.get((layout, cad_role, x, y), ()):
                label_point = _centroid(label)
                if label_point is None:
                    continue
                distance = math.hypot(point[0] - label_point[0], point[1] - label_point[1])
                if distance <= cell * 2.5:
                    candidates.append((distance, label))
    return [
        {
            "entity_key": str(label["entity_key"]),
            "text": str(label.get("text") or ""),
            "distance_native": distance,
            "layer": str(label.get("dwg_layer") or ""),
        }
        for distance, label in sorted(candidates, key=lambda item: (item[0], str(item[1]["entity_key"])))[:5]
    ]


def _style_facts(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "aci_color": row.get("aci_color"),
        "true_color": str(row.get("true_color") or ""),
        "linetype": str(row.get("linetype") or ""),
        "lineweight": row.get("lineweight"),
    }


def _layer_tokens(value: object) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", str(value or ""))
        if not token.isdigit()
    }


def _layer_similarity(left: object, right: object) -> float:
    left_tokens, right_tokens = _layer_tokens(left), _layer_tokens(right)
    common = left_tokens & right_tokens
    if len(common) < 2:
        return 0.0
    return 2.0 * len(common) / (len(left_tokens) + len(right_tokens))


def _grid_query(
    grid: Mapping[tuple[int, int], list[tuple[tuple[float, float], Mapping[str, Any]]]],
    point: tuple[float, float],
    cell: float,
) -> Iterable[tuple[tuple[float, float], Mapping[str, Any]]]:
    gx, gy = math.floor(point[0] / cell), math.floor(point[1] / cell)
    for x_coordinate in range(gx - 1, gx + 2):
        for y_coordinate in range(gy - 1, gy + 2):
            yield from grid.get((x_coordinate, y_coordinate), ())


def _line_relationship_evidence(
    rows: Iterable[Mapping[str, Any]],
    label_grid: Mapping[tuple[str, str, int, int], list[Mapping[str, Any]]],
    label_cells: Mapping[tuple[str, str], float],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Build source-only endpoint, topology and legend-style evidence.

    Every distance is measured in native CAD units.  The graph reports exact
    connectivity and bounded proximity separately; it never snaps geometry or
    promotes a line to a business feature.
    """

    values = list(rows)
    lines_by_partition: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    nodes_by_partition: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in values:
        partition = (str(row.get("cad_layout") or ""), str(row.get("cad_role") or ""))
        if row["source_table"] == "source_lines" and len(_native_points(row)) >= 2:
            lines_by_partition[partition].append(row)
        elif row["source_table"] in {"source_blocks", "source_points"} and _centroid(row):
            nodes_by_partition[partition].append(row)

    ignored_legend_layers = {"", "0", "DEFPOINTS", "TITLE BLOCK"}
    legend_lines = [
        row
        for row in values
        if row["source_table"] == "source_lines"
        and str(row.get("cad_role") or "") == "style_legend"
        and str(row.get("dwg_layer") or "").strip().upper() not in ignored_legend_layers
    ]
    legend_entries = []
    for row in legend_lines:
        payload = {
            "entity_key": str(row["entity_key"]),
            "layer": str(row.get("dwg_layer") or ""),
            "style": _style_facts(row),
            "label_candidates": _nearby_labels(row, label_grid, label_cells)[:3],
        }
        payload["evidence_id"] = _sha256_bytes(
            f"legend-style-v1|{_json(payload)}".encode("utf-8")
        )[:24]
        legend_entries.append(payload)

    evidence: dict[str, dict[str, Any]] = {}
    tolerance_manifest: dict[str, Any] = {}
    exact_edges: set[tuple[str, str]] = set()
    matched_style_count = 0
    node_endpoint_count = 0
    for partition, lines in lines_by_partition.items():
        endpoints = [
            (point, row, endpoint_index)
            for row in lines
            for endpoint_index, point in enumerate(
                (_native_points(row)[0], _native_points(row)[-1])
            )
        ]
        coordinates = [coordinate for point, _, _ in endpoints for coordinate in point]
        xs = coordinates[0::2]
        ys = coordinates[1::2]
        diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) if xs else 0.0
        positive_lengths = sorted(
            float(row.get("native_length"))
            for row in lines
            if isinstance(row.get("native_length"), (int, float))
            and float(row["native_length"]) > 0.0
            and math.isfinite(float(row["native_length"]))
        )
        typical_length = statistics.median(positive_lengths) if positive_lengths else diagonal
        exact_tolerance = max(diagonal * 1e-9, 1e-9)
        proximity_radius = max(typical_length * 0.25, exact_tolerance * 10.0)
        cell = max(proximity_radius, exact_tolerance)
        endpoint_grid: dict[
            tuple[int, int],
            list[tuple[tuple[float, float], Mapping[str, Any]]],
        ] = defaultdict(list)
        for point, row, endpoint_index in endpoints:
            endpoint_grid[(math.floor(point[0] / cell), math.floor(point[1] / cell))].append(
                (point, {"row": row, "endpoint_index": endpoint_index})
            )
        node_grid: dict[
            tuple[int, int],
            list[tuple[tuple[float, float], Mapping[str, Any]]],
        ] = defaultdict(list)
        for node in nodes_by_partition.get(partition, ()):
            point = _centroid(node)
            assert point is not None
            node_grid[(math.floor(point[0] / cell), math.floor(point[1] / cell))].append(
                (point, node)
            )

        parent = {str(row["entity_key"]): str(row["entity_key"]) for row in lines}

        def find(key: str) -> str:
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[max(left_root, right_root)] = min(left_root, right_root)

        endpoint_payloads: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for point, row, endpoint_index in endpoints:
            key = str(row["entity_key"])
            line_relations = []
            for other_point, wrapper in _grid_query(endpoint_grid, point, cell):
                other = wrapper["row"]
                other_key = str(other["entity_key"])
                if other_key == key:
                    continue
                distance = math.dist(point, other_point)
                if distance > proximity_radius:
                    continue
                connection_state = (
                    "EXACT_SOURCE_ENDPOINT" if distance <= exact_tolerance else "PROXIMATE_ONLY"
                )
                relation = {
                    "entity_key": other_key,
                    "endpoint_index": int(wrapper["endpoint_index"]),
                    "distance_native": distance,
                    "connection_state": connection_state,
                }
                relation["evidence_id"] = _sha256_bytes(
                    f"line-endpoint-v1|{key}|{endpoint_index}|{_json(relation)}".encode("utf-8")
                )[:24]
                line_relations.append(relation)
                if connection_state == "EXACT_SOURCE_ENDPOINT":
                    edge = tuple(sorted((key, other_key)))
                    exact_edges.add(edge)
                    union(*edge)
            line_relations.sort(
                key=lambda item: (item["distance_native"], item["entity_key"], item["endpoint_index"])
            )

            node_relations = []
            for node_point, node in _grid_query(node_grid, point, cell):
                distance = math.dist(point, node_point)
                if distance > proximity_radius:
                    continue
                relation = {
                    "entity_key": str(node["entity_key"]),
                    "source_table": str(node["source_table"]),
                    "layer": str(node.get("dwg_layer") or ""),
                    "block_name": str(node.get("block_name") or ""),
                    "distance_native": distance,
                    "connection_state": (
                        "EXACT_SOURCE_ENDPOINT" if distance <= exact_tolerance else "PROXIMATE_ONLY"
                    ),
                    "label_candidates": _nearby_labels(node, label_grid, label_cells)[:2],
                }
                relation["evidence_id"] = _sha256_bytes(
                    f"line-node-v1|{key}|{endpoint_index}|{_json(relation)}".encode("utf-8")
                )[:24]
                node_relations.append(relation)
            node_relations.sort(key=lambda item: (item["distance_native"], item["entity_key"]))
            if node_relations:
                node_endpoint_count += 1
            endpoint_payloads[key].append({
                "endpoint_index": endpoint_index,
                "point_native": [point[0], point[1]],
                "connected_or_nearby_lines": line_relations[:5],
                "connected_or_nearby_nodes": node_relations[:5],
            })

        component_members: dict[str, list[str]] = defaultdict(list)
        for key in parent:
            component_members[find(key)].append(key)
        for row in lines:
            key = str(row["entity_key"])
            style_matches = []
            row_layer = str(row.get("dwg_layer") or "").strip().casefold()
            row_style = _style_facts(row)
            if partition[1] == "model":
                for legend in legend_entries:
                    legend_style = legend["style"]
                    basis = []
                    score = 0.0
                    if row_layer and row_layer == str(legend["layer"]).strip().casefold():
                        basis.append("layer")
                        score += 0.6
                    elif (layer_similarity := _layer_similarity(row_layer, legend["layer"])):
                        basis.append("layer_tokens")
                        score += 0.45 * layer_similarity
                    if row_style["aci_color"] == legend_style["aci_color"]:
                        basis.append("resolved_color")
                        score += 0.25
                    if row_style["true_color"] and row_style["true_color"] == legend_style["true_color"]:
                        basis.append("true_color")
                        score += 0.05
                    if row_style["linetype"].casefold() == legend_style["linetype"].casefold():
                        basis.append("linetype")
                        score += 0.1
                    if row_style["lineweight"] == legend_style["lineweight"]:
                        basis.append("lineweight")
                        score += 0.05
                    if score < 0.6:
                        continue
                    style_matches.append({**legend, "match_score": min(score, 1.0), "match_basis": basis})
            style_matches.sort(key=lambda item: (-item["match_score"], item["entity_key"]))
            if style_matches:
                matched_style_count += 1
            members = sorted(component_members[find(key)])
            component_id = _sha256_bytes(
                f"line-component-v1|{'|'.join(members)}".encode("utf-8")
            )[:24]
            payload = {
                "schema_version": "cad2gis.source_relationship_evidence.v1",
                "topology": {
                    "component_id": component_id,
                    "component_source_line_count": len(members),
                    "exact_tolerance_native": exact_tolerance,
                    "proximity_radius_native": proximity_radius,
                    "endpoints": endpoint_payloads[key],
                },
                "style_legend_matches": style_matches[:5],
            }
            payload["evidence_id"] = _sha256_bytes(
                f"line-relationships-v1|{key}|{_json(payload)}".encode("utf-8")
            )[:24]
            evidence[key] = payload
        tolerance_manifest[f"{partition[0]}|{partition[1]}"] = {
            "source_line_count": len(lines),
            "typical_native_length": typical_length,
            "exact_tolerance_native": exact_tolerance,
            "proximity_radius_native": proximity_radius,
        }
    return evidence, {
        "schema_version": "cad2gis.relationship_evidence_summary.v1",
        "line_candidate_count": len(evidence),
        "exact_source_topology_edge_count": len(exact_edges),
        "style_legend_entry_count": len(legend_entries),
        "style_matched_line_count": matched_style_count,
        "endpoints_with_node_candidates": node_endpoint_count,
        "partition_tolerances": tolerance_manifest,
    }


def _assembly(
    row: Mapping[str, Any],
    members: Iterable[Mapping[str, Any]],
    labels: list[dict[str, Any]],
    relationship_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entity_key = str(row["entity_key"])
    member_values = list(members)
    assembly_id = _sha256_bytes(f"semantic-assembly-v1|{entity_key}".encode("utf-8"))
    batch_key = "|".join((
        str(row.get("cad_layout") or ""),
        str(row.get("cad_role") or ""),
        str(row["source_table"]),
    ))
    payload = {
        "assembly_id": assembly_id,
        "batch_id": _sha256_bytes(f"semantic-batch-v1|{batch_key}".encode("utf-8"))[:24],
        "primary_entity_key": entity_key,
        "member_entity_keys": [str(item["entity_key"]) for item in member_values],
        "members": [
            {
                "entity_key": str(item["entity_key"]),
                "source_table": str(item["source_table"]),
                "layer": str(item.get("dwg_layer") or ""),
                "dwg_type": str(item.get("dwg_type") or ""),
                "text": str(item.get("text") or ""),
                "block_name": str(item.get("block_name") or ""),
                "owner_handle": str(item.get("owner_handle") or ""),
            }
            for item in member_values
        ],
        "source_table": str(row["source_table"]),
        "cad_role": str(row.get("cad_role") or ""),
        "layout": str(row.get("cad_layout") or ""),
        "layer": str(row.get("dwg_layer") or ""),
        "dwg_type": str(row.get("dwg_type") or ""),
        "block_name": str(row.get("block_name") or ""),
        "text": str(row.get("text") or ""),
        "native_length": row.get("native_length"),
        "style": {
            "aci_color": row.get("aci_color"),
            "true_color": row.get("true_color"),
            "linetype": row.get("linetype"),
            "lineweight": row.get("lineweight"),
        },
        "label_candidates": labels,
        "relationship_evidence": dict(relationship_evidence or {}),
        "allowed_terminal_states": list(TERMINAL_STATES),
        "allowed_semantic_classes": list(SEMANTIC_CLASSES),
    }
    payload["candidate_evidence_id"] = _sha256_bytes(
        (
            "semantic-candidate-evidence-v1|"
            + _json({
                "primary_entity_key": entity_key,
                "member_entity_keys": payload["member_entity_keys"],
                "source_table": payload["source_table"],
                "cad_role": payload["cad_role"],
                "layout": payload["layout"],
                "layer": payload["layer"],
                "dwg_type": payload["dwg_type"],
                "block_name": payload["block_name"],
                "native_length": payload["native_length"],
                "style": payload["style"],
            })
        ).encode("utf-8")
    )[:24]
    return payload


def _assemblies(
    rows: Iterable[Mapping[str, Any]],
    label_grid: Mapping[tuple[str, str, int, int], list[Mapping[str, Any]]],
    label_cells: Mapping[tuple[str, str], float],
    relationship_evidence: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    values = list(rows)
    owned: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for item in values:
        owner = str(item.get("owner_handle") or "").strip().upper()
        if owner:
            owned[(
                str(item.get("cad_layout") or ""),
                str(item.get("cad_role") or ""),
                owner,
            )].append(item)
    claimed: set[str] = set()
    result: list[dict[str, Any]] = []
    ordered = sorted(
        values,
        key=lambda item: (
            item["source_table"] != "source_blocks",
            str(item["entity_key"]),
        ),
    )
    for row in ordered:
        key = str(row["entity_key"])
        if key in claimed:
            continue
        members: list[Mapping[str, Any]] = [row]
        handle = str(row.get("cad_handle") or "").strip().upper()
        if row["source_table"] == "source_blocks" and handle:
            relationship = (
                str(row.get("cad_layout") or ""),
                str(row.get("cad_role") or ""),
                handle,
            )
            members.extend(
                item
                for item in owned.get(relationship, ())
                if str(item["entity_key"]) != key
            )
        for member in members:
            claimed.add(str(member["entity_key"]))
        nearby = _nearby_labels(row, label_grid, label_cells)
        exact_labels = [
            {
                "entity_key": str(member["entity_key"]),
                "text": str(member.get("text") or ""),
                "distance_native": 0.0,
                "layer": str(member.get("dwg_layer") or ""),
                "relationship": "owned_annotation",
            }
            for member in members
            if member["source_table"] == "source_text"
            and str(member.get("text") or "").strip()
        ]
        labels_by_key = {
            str(item["entity_key"]): item for item in [*nearby, *exact_labels]
        }
        result.append(_assembly(
            row,
            members,
            sorted(
                labels_by_key.values(),
                key=lambda item: (float(item["distance_native"]), str(item["entity_key"])),
            )[:8],
            relationship_evidence.get(key),
        ))
    return result


def prepare_semantics(
    *, source_run: str | Path, output_dir: str | Path | None = None, force: bool = False
) -> dict[str, Any]:
    """Create pagable, source-bound candidates without making semantic claims."""

    root, gpkg, source_manifest = _source_run(source_run)
    output = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else root / "semantic_prepare"
    )
    manifest_path = output / "manifest.json"
    candidates_path = output / "candidates.jsonl"
    if (manifest_path.exists() or candidates_path.exists()) and not force:
        raise FileExistsError(f"semantic prepare output exists; pass force=True: {output}")
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(gpkg) as connection:
        rows = _rows(connection)
    label_grid, label_cells = _label_index(rows)
    relationship_evidence, relationship_summary = _line_relationship_evidence(
        rows, label_grid, label_cells
    )
    candidates = _assemblies(rows, label_grid, label_cells, relationship_evidence)
    candidates.sort(key=lambda item: (item["batch_id"], item["assembly_id"]))
    assembled_keys = [
        str(key)
        for candidate in candidates
        for key in candidate["member_entity_keys"]
    ]
    source_keys = [str(row["entity_key"]) for row in rows]
    if len(assembled_keys) != len(set(assembled_keys)):
        raise RuntimeError("semantic assembly assigned a source entity more than once")
    if set(assembled_keys) != set(source_keys):
        missing = sorted(set(source_keys) - set(assembled_keys))
        extra = sorted(set(assembled_keys) - set(source_keys))
        raise RuntimeError(
            "semantic assembly conservation failed: "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )
    batch_values: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidates):
        batch_id = str(candidate["batch_id"])
        batch = batch_values.setdefault(batch_id, {
            "batch_id": batch_id,
            "layout": candidate["layout"],
            "cad_role": candidate["cad_role"],
            "source_table": candidate["source_table"],
            "start": index,
            "count": 0,
        })
        batch["count"] += 1
    payload = "".join(_json(candidate) + "\n" for candidate in candidates)
    temporary = candidates_path.with_name(f".{candidates_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, candidates_path)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "schema_version": PREPARE_SCHEMA,
        "status": "READY_FOR_SEMANTIC_DECISIONS",
        "source_run": str(root),
        "source_sha256": (source_manifest.get("source") or {}).get("sha256"),
        "source_gpkg_sha256": _sha256_path(gpkg),
        "candidate_count": len(candidates),
        "assembly_conservation": {
            "source_entity_count": len(source_keys),
            "assembled_entity_count": len(assembled_keys),
            "difference": len(assembled_keys) - len(source_keys),
            "passed": True,
        },
        "batch_count": len(batch_values),
        "batches": sorted(
            batch_values.values(),
            key=lambda item: (
                item["cad_role"] != "model",
                item["layout"],
                item["source_table"],
                item["batch_id"],
            ),
        ),
        "entity_count": len(rows),
        "candidates": {
            "path": str(candidates_path),
            "sha256": _sha256_path(candidates_path),
            "format": "jsonl",
        },
        "decision_contract": {
            "schema_version": DECISION_SCHEMA,
            "terminal_states": list(TERMINAL_STATES),
            "semantic_classes": list(SEMANTIC_CLASSES),
            "geometry_policy": "source_entity_identity_only",
            "label_policy": "existing_label_entity_text_only",
            "unmentioned_entities": "UNRESOLVED",
            "json_schema": semantic_decision_json_schema(),
        },
        "relationship_evidence": relationship_summary,
        "spatial_candidate_radii_native": {
            f"{layout}|{cad_role}": value * 2.5
            for (layout, cad_role), value in sorted(label_cells.items())
        },
    }
    _atomic_json(manifest_path, manifest)
    return manifest


def _load_prepare(path: str | Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = Path(path).expanduser().resolve()
    manifest = _object(json.loads(manifest_path.read_text(encoding="utf-8")), "prepare manifest")
    if manifest.get("schema_version") != PREPARE_SCHEMA:
        raise SemanticContractError("unsupported semantic prepare schema")
    candidates_path = Path(str((manifest.get("candidates") or {}).get("path", ""))).resolve()
    if _sha256_path(candidates_path) != (manifest.get("candidates") or {}).get("sha256"):
        raise SemanticContractError("semantic candidates digest mismatch")
    candidates: dict[str, dict[str, Any]] = {}
    for line in candidates_path.read_text(encoding="utf-8").splitlines():
        candidate = _object(json.loads(line), "semantic candidate")
        candidates[str(candidate["assembly_id"])] = candidate
    return manifest, candidates


def list_semantic_candidates(
    *,
    prepare_manifest: str | Path,
    cursor: int = 0,
    limit: int = 100,
    batch_id: str | None = None,
) -> dict[str, Any]:
    if cursor < 0 or not 1 <= limit <= 200:
        raise SemanticContractError("cursor/limit are outside the allowed range")
    manifest, candidates = _load_prepare(prepare_manifest)
    values = [candidates[key] for key in sorted(candidates)]
    if batch_id:
        known = {str(item["batch_id"]) for item in manifest.get("batches", [])}
        if batch_id not in known:
            raise SemanticContractError(f"unknown semantic batch_id: {batch_id}")
        values = [item for item in values if item.get("batch_id") == batch_id]
    page = values[cursor : cursor + limit]
    return {
        "schema_version": "cad2gis.semantic_candidate_page.v1",
        "source_sha256": manifest.get("source_sha256"),
        "cursor": cursor,
        "next_cursor": cursor + len(page) if cursor + len(page) < len(values) else None,
        "total": len(values),
        "batch_id": batch_id,
        "items": page,
    }


def list_semantic_batches(
    *,
    prepare_manifest: str | Path,
    cursor: int = 0,
    limit: int = 100,
    cad_role: str | None = None,
) -> dict[str, Any]:
    if cursor < 0 or not 1 <= limit <= 200:
        raise SemanticContractError("cursor/limit are outside the allowed range")
    manifest, _ = _load_prepare(prepare_manifest)
    values = list(manifest.get("batches", []))
    if cad_role:
        values = [item for item in values if item.get("cad_role") == cad_role]
    page = values[cursor : cursor + limit]
    return {
        "schema_version": "cad2gis.semantic_batch_page.v1",
        "source_sha256": manifest.get("source_sha256"),
        "cursor": cursor,
        "next_cursor": cursor + len(page) if cursor + len(page) < len(values) else None,
        "total": len(values),
        "cad_role": cad_role,
        "items": page,
    }


def summarize_semantic_candidates(
    *, prepare_manifest: str | Path, batch_id: str
) -> dict[str, Any]:
    """Return compact observed facts for one batch without dropping IDs."""

    manifest, candidates = _load_prepare(prepare_manifest)
    batch = next(
        (item for item in manifest.get("batches", []) if item.get("batch_id") == batch_id),
        None,
    )
    if batch is None:
        raise SemanticContractError(f"unknown semantic batch_id: {batch_id}")
    values = [item for item in candidates.values() if item.get("batch_id") == batch_id]
    return {
        "schema_version": "cad2gis.semantic_batch_summary.v1",
        "source_sha256": manifest.get("source_sha256"),
        "candidates_sha256": (manifest.get("candidates") or {}).get("sha256"),
        "batch": batch,
        "observed": {
            "candidate_count": len(values),
            "layers": dict(sorted(Counter(str(item["layer"]) for item in values).items())),
            "dwg_types": dict(sorted(Counter(str(item["dwg_type"]) for item in values).items())),
            "block_names": dict(sorted(Counter(str(item["block_name"]) for item in values if str(item["block_name"])).items())),
            "text_count": sum(bool(str(item["text"]).strip()) for item in values),
            "candidate_label_count": sum(len(item["label_candidates"]) for item in values),
            "native_length_count": sum(item["native_length"] is not None for item in values),
        },
        "decision_schema": semantic_decision_json_schema(),
    }


def _decision_pack(value: str | Path) -> dict[str, Any]:
    path = Path(value).expanduser().resolve()
    pack = _object(json.loads(path.read_text(encoding="utf-8")), "decision pack")
    if pack.get("schema_version") != DECISION_SCHEMA:
        raise SemanticContractError("unsupported semantic decision schema")
    if not isinstance(pack.get("decisions"), list):
        raise SemanticContractError("decisions must be an array")
    return pack


def _candidate_evidence_ids(candidate: Mapping[str, Any]) -> set[str]:
    result = {str(candidate.get("candidate_evidence_id") or "")}

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if value.get("evidence_id"):
                result.add(str(value["evidence_id"]))
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(candidate.get("relationship_evidence", {}))
    result.discard("")
    return result


def _validate_decisions(
    pack: Mapping[str, Any], manifest: Mapping[str, Any], candidates: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    if pack.get("source_sha256") != manifest.get("source_sha256"):
        raise SemanticContractError("decision pack source_sha256 mismatch")
    if pack.get("candidates_sha256") != (manifest.get("candidates") or {}).get("sha256"):
        raise SemanticContractError("decision pack candidates_sha256 mismatch")
    used: dict[str, str] = {}
    labels: dict[str, str] = {}
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(pack["decisions"]):
        decision = _object(raw, f"decisions[{index}]")
        assembly_id = str(decision.get("assembly_id") or "")
        candidate = candidates.get(assembly_id)
        if candidate is None:
            raise SemanticContractError(f"unknown assembly_id: {assembly_id}")
        state = str(decision.get("terminal_state") or "")
        if state not in TERMINAL_STATES:
            raise SemanticContractError(f"invalid terminal_state for {assembly_id}")
        primary = str(candidate["primary_entity_key"])
        consumed = decision.get("source_entity_keys", [primary])
        if not isinstance(consumed, list) or not consumed:
            raise SemanticContractError(f"source_entity_keys must be a non-empty array: {assembly_id}")
        allowed = set(candidate["member_entity_keys"]) | {
            str(item["entity_key"]) for item in candidate["label_candidates"]
        }
        keys = [str(key) for key in consumed]
        if not set(keys) <= allowed:
            raise SemanticContractError(f"decision references entities outside candidate: {assembly_id}")
        for key in keys:
            if key in used:
                raise SemanticContractError(f"entity assigned more than once: {key}")
            used[key] = state
        semantic_class = str(decision.get("semantic_class") or "")
        subtype = str(decision.get("semantic_subtype") or "").strip()
        label_key = str(decision.get("source_label_entity_key") or "")
        raw_evidence_ids = decision.get("evidence_ids", [])
        if not isinstance(raw_evidence_ids, list):
            raise SemanticContractError(f"evidence_ids must be an array: {assembly_id}")
        evidence_ids = [str(value) for value in raw_evidence_ids]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise SemanticContractError(f"evidence_ids must be unique: {assembly_id}")
        if state == "CONSUMED_BY_FEATURE":
            if semantic_class not in SEMANTIC_CLASSES:
                raise SemanticContractError(f"invalid semantic_class for {assembly_id}")
            if not evidence_ids:
                raise SemanticContractError(
                    f"feature decision must cite observed evidence_ids: {assembly_id}"
                )
            unknown_evidence = set(evidence_ids) - _candidate_evidence_ids(candidate)
            if unknown_evidence:
                raise SemanticContractError(
                    f"feature decision cites unknown evidence_ids: {sorted(unknown_evidence)}"
                )
            if semantic_class in {"NETWORK_ROUTE", "NETWORK_SEGMENT"}:
                if candidate.get("source_table") != "source_lines":
                    raise SemanticContractError(
                        f"{semantic_class} must reference source_lines: {assembly_id}"
                    )
                native_length = candidate.get("native_length")
                if (
                    not isinstance(native_length, (int, float))
                    or not math.isfinite(float(native_length))
                    or float(native_length) <= 0.0
                ):
                    raise SemanticContractError(
                        f"{semantic_class} requires positive source native_length: {assembly_id}"
                    )
                relationship_id = str(
                    (candidate.get("relationship_evidence") or {}).get("evidence_id") or ""
                )
                if not relationship_id or relationship_id not in evidence_ids:
                    raise SemanticContractError(
                        f"{semantic_class} must cite relationship evidence: {assembly_id}"
                    )
            if label_key:
                match = next(
                    (item for item in candidate["label_candidates"] if item["entity_key"] == label_key),
                    None,
                )
                if match is None:
                    raise SemanticContractError(f"label is not an observed candidate: {label_key}")
                labels[assembly_id] = str(match["text"])
        elif semantic_class or subtype or label_key:
            raise SemanticContractError(
                f"non-feature decision cannot carry class/subtype/label: {assembly_id}"
            )
        validated.append(
            {
                **decision,
                "assembly_id": assembly_id,
                "terminal_state": state,
                "primary_entity_key": primary,
                "source_entity_keys": keys,
                "semantic_class": semantic_class,
                "semantic_subtype": subtype,
                "source_label_entity_key": label_key,
                "evidence_ids": evidence_ids,
            }
        )
    return validated, used, labels


def write_semantic_decision_pack(
    *,
    prepare_manifest: str | Path,
    output: str | Path,
    decisions: list[dict[str, Any]],
    batch_decisions: list[dict[str, Any]] | None = None,
    host: str,
    model: str,
    force: bool = False,
) -> dict[str, Any]:
    manifest, candidates = _load_prepare(prepare_manifest)
    destination = Path(output).expanduser().resolve()
    if destination.exists() and not force:
        raise FileExistsError(f"semantic decision pack exists; pass force=True: {destination}")
    explicit_ids = {
        str(item.get("assembly_id") or "")
        for item in decisions
        if isinstance(item, Mapping)
    }
    explicitly_assigned_keys = {
        str(key)
        for item in decisions
        if isinstance(item, Mapping)
        for key in item.get("source_entity_keys", [])
    }
    expanded = list(decisions)
    batches = {
        str(item["batch_id"]): item for item in manifest.get("batches", [])
    }
    for index, raw in enumerate(batch_decisions or []):
        batch = _object(raw, f"batch_decisions[{index}]")
        batch_id = str(batch.get("batch_id") or "")
        if batch_id not in batches:
            raise SemanticContractError(f"unknown semantic batch_id: {batch_id}")
        state = str(batch.get("terminal_state") or "")
        if state not in {
            "RETAINED_AS_REFERENCE",
            "EXCLUDED_AS_DOCUMENTATION",
            "UNRESOLVED",
        }:
            raise SemanticContractError(
                "batch decisions may only retain, exclude documentation, or abstain"
            )
        for candidate in candidates.values():
            if candidate.get("batch_id") != batch_id:
                continue
            assembly_id = str(candidate["assembly_id"])
            if assembly_id in explicit_ids:
                continue
            remaining_keys = [
                str(key)
                for key in candidate["member_entity_keys"]
                if str(key) not in explicitly_assigned_keys
            ]
            if not remaining_keys:
                continue
            expanded.append({
                "assembly_id": assembly_id,
                "terminal_state": state,
                "source_entity_keys": remaining_keys,
                "evidence": list(batch.get("evidence", [])),
                "batch_id": batch_id,
            })
    pack = {
        "schema_version": DECISION_SCHEMA,
        "source_sha256": manifest.get("source_sha256"),
        "candidates_sha256": (manifest.get("candidates") or {}).get("sha256"),
        "provenance": {"host": str(host), "model": str(model)},
        "decisions": expanded,
        "batch_decisions": list(batch_decisions or []),
    }
    validated, states, _ = _validate_decisions(pack, manifest, candidates)
    pack["decisions"] = validated
    _atomic_json(destination, pack)
    return {
        "schema_version": "cad2gis.semantic_decision_pack_result.v1",
        "path": str(destination),
        "sha256": _sha256_path(destination),
        "decision_count": len(validated),
        "batch_decision_count": len(batch_decisions or []),
        "assigned_entity_count": len(states),
        "source_sha256": manifest.get("source_sha256"),
        "candidates_sha256": pack["candidates_sha256"],
    }


def _install_semantic_tables(
    connection: sqlite3.Connection,
    decisions: Iterable[Mapping[str, Any]],
    entity_states: Mapping[str, str],
    labels: Mapping[str, str],
    candidates: Mapping[str, Mapping[str, Any]],
) -> None:
    connection.executescript(
        """
        CREATE TABLE semantic_features (
          feature_id TEXT PRIMARY KEY NOT NULL,
          assembly_id TEXT UNIQUE NOT NULL,
          semantic_class TEXT NOT NULL,
          semantic_subtype TEXT,
          display_label TEXT,
          source_label_entity_key TEXT,
          primary_entity_key TEXT NOT NULL,
          source_table TEXT NOT NULL,
          confidence REAL,
          provenance_json TEXT NOT NULL
        );
        CREATE TABLE semantic_entity_ledger (
          entity_key TEXT PRIMARY KEY NOT NULL,
          terminal_state TEXT NOT NULL,
          assembly_id TEXT,
          feature_id TEXT,
          reason TEXT NOT NULL
        );
        CREATE TABLE semantic_candidate_evidence (
          assembly_id TEXT PRIMARY KEY NOT NULL,
          primary_entity_key TEXT UNIQUE NOT NULL,
          candidate_evidence_id TEXT UNIQUE NOT NULL,
          evidence_json TEXT NOT NULL
        );
        CREATE TABLE semantic_manifest (
          schema_version TEXT NOT NULL,
          created_utc TEXT NOT NULL,
          source_entity_count INTEGER NOT NULL,
          feature_count INTEGER NOT NULL
        );
        """
    )
    entity_table = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT entity_key, materialization_layer FROM source_entity_accounting"
        )
    }
    decision_by_entity: dict[str, tuple[str, str | None]] = {}
    features = []
    for decision in decisions:
        if decision["terminal_state"] != "CONSUMED_BY_FEATURE":
            for key in decision["source_entity_keys"]:
                decision_by_entity[str(key)] = (str(decision["assembly_id"]), None)
            continue
        feature_id = _sha256_bytes(
            f"semantic-feature-v1|{decision['assembly_id']}|{decision['semantic_class']}".encode("utf-8")
        )
        primary = str(decision["primary_entity_key"])
        features.append((
            feature_id,
            decision["assembly_id"],
            decision["semantic_class"],
            decision["semantic_subtype"],
            labels.get(str(decision["assembly_id"])),
            decision["source_label_entity_key"] or None,
            primary,
            entity_table[primary],
            decision.get("confidence"),
            _json({
                "decision_evidence": decision.get("evidence", []),
                "decision_evidence_ids": decision.get("evidence_ids", []),
                "geometry": "source_entity_identity",
                "label": "source_entity_text" if decision["source_label_entity_key"] else "unassigned",
            }),
        ))
        for key in decision["source_entity_keys"]:
            decision_by_entity[str(key)] = (str(decision["assembly_id"]), feature_id)
    connection.executemany(
        "INSERT INTO semantic_features VALUES (?,?,?,?,?,?,?,?,?,?)", features
    )
    ledger = []
    for key in sorted(entity_table):
        state = entity_states.get(key, "UNRESOLVED")
        assembly, feature = decision_by_entity.get(key, (None, None))
        reason = "semantic_decision" if key in entity_states else "no_semantic_decision"
        ledger.append((key, state, assembly, feature, reason))
    connection.executemany(
        "INSERT INTO semantic_entity_ledger VALUES (?,?,?,?,?)", ledger
    )
    connection.executemany(
        "INSERT INTO semantic_candidate_evidence VALUES (?,?,?,?)",
        (
            (
                str(candidate["assembly_id"]),
                str(candidate["primary_entity_key"]),
                str(candidate["candidate_evidence_id"]),
                _json({
                    "candidate_evidence_id": candidate["candidate_evidence_id"],
                    "label_candidates": candidate.get("label_candidates", []),
                    "relationship_evidence": candidate.get("relationship_evidence", {}),
                }),
            )
            for candidate in candidates.values()
        ),
    )
    connection.execute(
        "INSERT INTO semantic_manifest VALUES (?,?,?,?)",
        (SEMANTIC_SCHEMA, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), len(ledger), len(features)),
    )
    _create_semantic_views(connection)


def _create_semantic_views(connection: sqlite3.Connection) -> None:
    geometry = {
        row[0]: (row[1], row[2], row[3], row[4], row[5])
        for row in connection.execute(
            "SELECT table_name,column_name,geometry_type_name,srs_id,z,m FROM gpkg_geometry_columns"
        )
        if row[0] in SOURCE_LAYERS
    }
    pairs = connection.execute(
        "SELECT DISTINCT semantic_class, source_table FROM semantic_features ORDER BY 1,2"
    ).fetchall()
    for semantic_class, source_table in pairs:
        if source_table not in geometry:
            continue
        safe_class = "".join(ch.casefold() if ch.isalnum() else "_" for ch in semantic_class).strip("_")
        suffix = source_table.removeprefix("source_")
        view = f"semantic_{safe_class}_{suffix}"
        connection.execute(
            f'''CREATE VIEW "{view}" AS
                SELECT s.*, f.feature_id, f.semantic_class, f.semantic_subtype,
                       f.display_label, f.source_label_entity_key, f.confidence,
                       f.provenance_json
                FROM "{source_table}" s
                JOIN semantic_features f ON f.primary_entity_key = s.entity_key
                WHERE f.semantic_class = '{str(semantic_class).replace("'", "''")}' ''',
        )
        connection.execute(
            "INSERT INTO gpkg_contents(table_name,data_type,identifier,description,last_change,srs_id) "
            "VALUES (?, 'features', ?, 'CAD2GIS semantic projection', strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?)",
            (view, view, geometry[source_table][2]),
        )
        column, kind, srs_id, z, m = geometry[source_table]
        connection.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
            (view, column, kind, srs_id, z, m),
        )


def compile_semantics(
    *,
    source_run: str | Path,
    prepare_manifest: str | Path,
    decision_pack: str | Path,
    output: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Compile validated semantic decisions into a source-preserving GeoPackage."""

    root, source_gpkg, source_manifest = _source_run(source_run)
    prepare, candidates = _load_prepare(prepare_manifest)
    if prepare.get("source_gpkg_sha256") != _sha256_path(source_gpkg):
        raise SemanticContractError("prepare manifest is not bound to this source.gpkg")
    pack = _decision_pack(decision_pack)
    decisions, states, labels = _validate_decisions(pack, prepare, candidates)
    destination = (
        Path(output).expanduser().resolve() if output is not None else root / "semantic.gpkg"
    )
    manifest_path = destination.with_name("semantic_manifest.json")
    if (destination.exists() or manifest_path.exists()) and not force:
        raise FileExistsError(f"semantic output exists; pass force=True: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    shutil.copy2(source_gpkg, temporary)
    try:
        connection = sqlite3.connect(temporary)
        try:
            _install_semantic_tables(connection, decisions, states, labels, candidates)
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("semantic GeoPackage integrity check failed")
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    validation = validate_semantics(destination)
    manifest = {
        "schema_version": SEMANTIC_SCHEMA,
        "status": "SEMANTIC_COMPILED" if validation["valid"] else "SEMANTIC_INVALID",
        "source": dict(source_manifest.get("source") or {}),
        "source_gpkg_sha256": _sha256_path(source_gpkg),
        "semantic_gpkg": str(destination),
        "semantic_gpkg_sha256": _sha256_path(destination),
        "decision_pack_sha256": _sha256_path(Path(decision_pack).resolve()),
        "validation": validation,
        "pipeline_boundary": "semantic_native_cad_space",
        "excluded_stages": ["topology_repair", "length_inference", "crs_transformation", "gcp_registration", "delivery_publication"],
    }
    _atomic_json(manifest_path, manifest)
    return manifest


def validate_semantics(semantic_gpkg: str | Path) -> dict[str, Any]:
    path = Path(semantic_gpkg).expanduser().resolve()
    with sqlite3.connect(path) as connection:
        source_count = connection.execute(
            "SELECT COUNT(*) FROM source_entity_accounting"
        ).fetchone()[0]
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM semantic_entity_ledger"
        ).fetchone()[0]
        distinct_count = connection.execute(
            "SELECT COUNT(DISTINCT entity_key) FROM semantic_entity_ledger"
        ).fetchone()[0]
        invalid_states = connection.execute(
            "SELECT COUNT(*) FROM semantic_entity_ledger WHERE terminal_state NOT IN (?,?,?,?)",
            TERMINAL_STATES,
        ).fetchone()[0]
        feature_count = connection.execute("SELECT COUNT(*) FROM semantic_features").fetchone()[0]
        candidate_evidence_count = connection.execute(
            "SELECT COUNT(*) FROM semantic_candidate_evidence"
        ).fetchone()[0]
        feature_evidence_missing = connection.execute(
            "SELECT COUNT(*) FROM semantic_features f "
            "LEFT JOIN semantic_candidate_evidence e ON e.assembly_id=f.assembly_id "
            "WHERE e.assembly_id IS NULL"
        ).fetchone()[0]
        state_counts = dict(connection.execute(
            "SELECT terminal_state,COUNT(*) FROM semantic_entity_ledger GROUP BY terminal_state"
        ).fetchall())
        view_counts = dict(connection.execute(
            "SELECT source_table,COUNT(*) FROM semantic_features GROUP BY source_table"
        ).fetchall())
        duplicate_labels = [
            {"semantic_class": row[0], "display_label": row[1], "count": row[2]}
            for row in connection.execute(
                "SELECT semantic_class,display_label,COUNT(*) FROM semantic_features "
                "WHERE display_label IS NOT NULL AND trim(display_label) <> '' "
                "GROUP BY semantic_class,display_label HAVING COUNT(*) > 1 "
                "ORDER BY COUNT(*) DESC,semantic_class,display_label"
            )
        ]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        length_audit = connection.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN s.native_length IS NOT NULL AND s.native_length > 0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN s.native_length IS NOT NULL AND s.native_length > 0 THEN s.native_length ELSE 0 END) "
            "FROM semantic_features f JOIN source_lines s ON s.entity_key=f.primary_entity_key "
            "WHERE f.semantic_class IN ('NETWORK_ROUTE','NETWORK_SEGMENT')"
        ).fetchone()
    valid = (
        integrity == "ok"
        and source_count == ledger_count == distinct_count
        and invalid_states == 0
        and sum(state_counts.values()) == source_count
        and candidate_evidence_count > 0
        and feature_evidence_missing == 0
        and int(length_audit[0] or 0) == int(length_audit[1] or 0)
    )
    return {
        "schema_version": "cad2gis.semantic_validation.v1",
        "valid": valid,
        "integrity": integrity,
        "source_entity_count": source_count,
        "ledger_entity_count": ledger_count,
        "feature_count": feature_count,
        "candidate_evidence_count": candidate_evidence_count,
        "feature_evidence_missing_count": feature_evidence_missing,
        "terminal_state_counts": state_counts,
        "feature_source_table_counts": view_counts,
        "conservation_difference": ledger_count - source_count,
        "conflicts": {
            "duplicate_display_labels": duplicate_labels,
            "duplicate_display_label_count": len(duplicate_labels),
        },
        "network_length_audit": {
            "feature_count": int(length_audit[0] or 0),
            "with_positive_source_native_length": int(length_audit[1] or 0),
            "missing_or_nonpositive_count": int(length_audit[0] or 0)
            - int(length_audit[1] or 0),
            "source_native_length_total": float(length_audit[2] or 0.0),
            "passed": int(length_audit[0] or 0) == int(length_audit[1] or 0),
        },
    }


__all__ = [
    "DECISION_SCHEMA",
    "PREPARE_SCHEMA",
    "SEMANTIC_CLASSES",
    "SEMANTIC_SCHEMA",
    "TERMINAL_STATES",
    "SemanticContractError",
    "compile_semantics",
    "list_semantic_batches",
    "list_semantic_candidates",
    "prepare_semantics",
    "semantic_decision_json_schema",
    "summarize_semantic_candidates",
    "validate_semantics",
    "write_semantic_decision_pack",
]
