"""Deterministic multi-scale CAD renders and entity-ID hit maps.

Visible PNGs are secondary evidence for VLM reasoning.  Hit-map PNGs encode
each source entity with a stable, unique RGB value so a pixel observation can
be resolved back to an Evidence Graph node.  Neither output is an authority
for coordinates, topology, or measurements.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .curve_geometry import delivery_points
from .evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode


VISUAL_EVIDENCE_SCHEMA = "cad2gis.visual_evidence.v1"
VISUAL_RENDER_POLICY_ID = "cad2gis.visual-render.multiscale-2x2.v1"

_OVERVIEW_SIZE = (1280, 960)
_DETAIL_SIZE = (1024, 768)

_ACI_RGB = {
    1: (255, 0, 0),
    2: (255, 255, 0),
    3: (0, 180, 0),
    4: (0, 190, 190),
    5: (0, 90, 255),
    6: (210, 0, 210),
    7: (30, 30, 30),
    8: (110, 110, 110),
    9: (170, 170, 170),
}


class VisualEvidenceError(RuntimeError):
    """Visual evidence cannot be generated without changing its contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _finite_point(point: Any) -> tuple[float, float] | None:
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    try:
        x, y = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return x, y


def _entity_points(entity: Any, feature_by_source: dict[str, Any]) -> tuple[tuple[float, float], ...]:
    feature = feature_by_source.get(str(entity.entity_key))
    if feature is not None and str(feature.feature_class).upper() == "CABLE":
        try:
            materialized = delivery_points(feature, require_materialized=True)
        except (RuntimeError, ValueError):
            materialized = ()
        if materialized:
            return tuple(materialized)
    return tuple(
        point for point in (_finite_point(item) for item in entity.points)
        if point is not None
    )


def _bbox(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    values = tuple(points)
    if not values:
        return 0.0, 0.0, 1.0, 1.0
    xs = [point[0] for point in values]
    ys = [point[1] for point in values]
    return min(xs), min(ys), max(xs), max(ys)


def _expanded_bounds(
    bounds: tuple[float, float, float, float],
    *,
    ratio: float = 0.03,
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    fallback = max(width, height, 1.0)
    if width <= 0.0:
        min_x -= fallback * 0.5
        max_x += fallback * 0.5
        width = fallback
    if height <= 0.0:
        min_y -= fallback * 0.5
        max_y += fallback * 0.5
        height = fallback
    pad_x = max(width * ratio, 1e-9)
    pad_y = max(height * ratio, 1e-9)
    return min_x - pad_x, min_y - pad_y, max_x + pad_x, max_y + pad_y


def _detail_bounds(
    bounds: tuple[float, float, float, float],
) -> tuple[tuple[str, tuple[float, float, float, float]], ...]:
    min_x, min_y, max_x, max_y = bounds
    mid_x = (min_x + max_x) * 0.5
    mid_y = (min_y + max_y) * 0.5
    overlap_x = (max_x - min_x) * 0.04
    overlap_y = (max_y - min_y) * 0.04
    return (
        ("detail-nw", (min_x, mid_y - overlap_y, mid_x + overlap_x, max_y)),
        ("detail-ne", (mid_x - overlap_x, mid_y - overlap_y, max_x, max_y)),
        ("detail-sw", (min_x, min_y, mid_x + overlap_x, mid_y + overlap_y)),
        ("detail-se", (mid_x - overlap_x, min_y, max_x, mid_y + overlap_y)),
    )


def _intersects(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return not (
        left[2] < right[0] or left[0] > right[2]
        or left[3] < right[1] or left[1] > right[3]
    )


def _entity_color(entity: Any) -> tuple[int, int, int]:
    raw_true_color = str(getattr(entity.style, "true_color", "")).strip().lstrip("#")
    if len(raw_true_color) == 6:
        try:
            return tuple(
                int(raw_true_color[index:index + 2], 16)
                for index in (0, 2, 4)
            )
        except ValueError:
            pass
    aci = int(getattr(entity.style, "aci_color", 7))
    return _ACI_RGB.get(aci, (60, 60, 60))


def _hit_colors(node_ids: Iterable[str]) -> dict[str, tuple[int, int, int]]:
    used = {(0, 0, 0), (255, 255, 255)}
    result: dict[str, tuple[int, int, int]] = {}
    for node_id in sorted(node_ids):
        seed = int(hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:6], 16)
        for offset in range(1 << 24):
            value = (seed + offset) % (1 << 24)
            color = ((value >> 16) & 255, (value >> 8) & 255, value & 255)
            if color not in used:
                used.add(color)
                result[node_id] = color
                break
        else:  # pragma: no cover - impossible for practical CAD entity counts
            raise VisualEvidenceError("Entity hit-map color space is exhausted")
    return result


def _projector(
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
):
    min_x, min_y, max_x, max_y = bounds
    width, height = size
    span_x = max(max_x - min_x, 1e-12)
    span_y = max(max_y - min_y, 1e-12)
    scale = min((width - 20) / span_x, (height - 20) / span_y)
    draw_width = span_x * scale
    draw_height = span_y * scale
    offset_x = (width - draw_width) * 0.5
    offset_y = (height - draw_height) * 0.5

    def project(point: tuple[float, float]) -> tuple[int, int]:
        return (
            int(round(offset_x + (point[0] - min_x) * scale)),
            int(round(height - (offset_y + (point[1] - min_y) * scale))),
        )

    return project


def _png_bytes(image: Any) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False, compress_level=9)
    return stream.getvalue()


@dataclass(frozen=True)
class VisualEvidenceFile:
    relative_path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return _sha256(self.content)


@dataclass(frozen=True)
class VisualEvidenceBundle:
    graph: EvidenceGraph
    files: tuple[VisualEvidenceFile, ...]
    manifest_relative_path: str
    region_count: int

    @property
    def manifest_file(self) -> VisualEvidenceFile:
        return next(
            item for item in self.files
            if item.relative_path == self.manifest_relative_path
        )

    def write(self, root: str | Path) -> Path:
        destination = Path(root)
        for item in self.files:
            path = destination / item.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.content)
        return destination / self.manifest_relative_path


def build_visual_evidence_bundle(
    *,
    graph: EvidenceGraph,
    entities: Iterable[Any],
    features: Iterable[Any],
) -> VisualEvidenceBundle:
    """Render model-space entities and return a graph augmented with regions."""

    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - dependency preflight
        raise VisualEvidenceError(
            "Pillow is required for deterministic visual evidence rendering"
        ) from exc

    graph_nodes = graph.logical_index
    feature_by_source = {
        str(feature.source_entity_key): feature for feature in features
        if str(feature.source_entity_key) not in {"", "None"}
    }
    renderables = []
    all_points: list[tuple[float, float]] = []
    for entity in sorted(entities, key=lambda item: str(item.entity_key)):
        if str(entity.cad_role).casefold() != "model":
            continue
        node = graph_nodes.get(str(entity.entity_key))
        if node is None or node.kind != "source_entity":
            continue
        points = _entity_points(entity, feature_by_source)
        if not points:
            continue
        bounds = _bbox(points)
        renderables.append((entity, node, points, bounds))
        all_points.extend(points)
    drawing_bounds = _expanded_bounds(_bbox(all_points))
    regions = (
        ("overview", drawing_bounds, _OVERVIEW_SIZE),
        *((name, bounds, _DETAIL_SIZE) for name, bounds in _detail_bounds(drawing_bounds)),
    )
    colors = _hit_colors(node.node_id for _, node, _, _ in renderables)
    files: list[VisualEvidenceFile] = []
    region_nodes: list[EvidenceNode] = []
    region_edges: list[EvidenceEdge] = []
    region_manifest: list[dict[str, Any]] = []

    for region_name, region_bounds, size in regions:
        visible = tuple(
            item for item in renderables if _intersects(item[3], region_bounds)
        )
        visible_image = Image.new("RGB", size, (255, 255, 255))
        hit_image = Image.new("RGB", size, (0, 0, 0))
        visible_draw = ImageDraw.Draw(visible_image)
        hit_draw = ImageDraw.Draw(hit_image)
        project = _projector(region_bounds, size)
        hit_index: dict[str, Any] = {}

        for entity, node, points, entity_bounds in visible:
            screen_points = [project(point) for point in points]
            visible_color = _entity_color(entity)
            hit_color = colors[node.node_id]
            if len(screen_points) >= 2:
                visible_draw.line(screen_points, fill=visible_color, width=2)
                hit_draw.line(screen_points, fill=hit_color, width=7)
                if bool(entity.closed) and screen_points[0] != screen_points[-1]:
                    visible_draw.line(
                        (screen_points[-1], screen_points[0]),
                        fill=visible_color,
                        width=2,
                    )
                    hit_draw.line(
                        (screen_points[-1], screen_points[0]),
                        fill=hit_color,
                        width=7,
                    )
            else:
                x, y = screen_points[0]
                visible_draw.ellipse(
                    (x - 3, y - 3, x + 3, y + 3),
                    fill=visible_color,
                )
                hit_draw.ellipse(
                    (x - 6, y - 6, x + 6, y + 6),
                    fill=hit_color,
                )
            text = str(entity.text).strip()
            if text:
                x, y = screen_points[0]
                visible_draw.text((x + 4, y - 10), text, fill=visible_color)
            color_hex = "".join(f"{channel:02X}" for channel in hit_color)
            hit_index[color_hex] = {
                "node_id": node.node_id,
                "logical_id": node.logical_id,
                "source_entity_key": str(entity.entity_key),
                "source_handle": str(entity.handle),
                "source_layer": str(entity.layer),
                "entity_native_bounds": list(entity_bounds),
            }

        prefix = f"reasoning/visual/{region_name}"
        render_path = f"{prefix}.png"
        hit_path = f"{prefix}.hit.png"
        index_path = f"{prefix}.hit-index.json"
        render_bytes = _png_bytes(visible_image)
        hit_bytes = _png_bytes(hit_image)
        index_bytes = _canonical_bytes({
            "schema_version": "cad2gis.visual_hit_index.v1",
            "region_id": region_name,
            "background_rgb": "000000",
            "entries": hit_index,
        })
        files.extend((
            VisualEvidenceFile(render_path, render_bytes),
            VisualEvidenceFile(hit_path, hit_bytes),
            VisualEvidenceFile(index_path, index_bytes),
        ))
        region_node = EvidenceNode.create(
            source_sha256=graph.source_sha256,
            logical_id=f"render_region:{region_name}",
            kind="render_region",
            facts={
                "policy_id": VISUAL_RENDER_POLICY_ID,
                "region_id": region_name,
                "native_bounds": list(region_bounds),
                "pixel_width": size[0],
                "pixel_height": size[1],
                "visible_entity_count": len(visible),
                "visible_entity_node_ids": [
                    node.node_id for _, node, _, _ in visible
                ],
                "render_path": render_path,
                "render_sha256": _sha256(render_bytes),
                "hit_map_path": hit_path,
                "hit_map_sha256": _sha256(hit_bytes),
                "hit_index_path": index_path,
                "hit_index_sha256": _sha256(index_bytes),
                "authority": "secondary_visual_evidence_only",
            },
        )
        region_nodes.append(region_node)
        for _, entity_node, _, _ in visible:
            region_edges.append(EvidenceEdge.create(
                source_sha256=graph.source_sha256,
                kind="renders",
                source_node_id=region_node.node_id,
                target_node_id=entity_node.node_id,
                evidence_node_ids=(entity_node.node_id,),
            ))
        region_manifest.append(region_node.facts)

    augmented_graph = EvidenceGraph.create(
        source_sha256=graph.source_sha256,
        nodes=(*graph.nodes, *region_nodes),
        edges=(*graph.edges, *region_edges),
    )
    manifest_path = "reasoning/visual/manifest.json"
    manifest_bytes = _canonical_bytes({
        "schema_version": VISUAL_EVIDENCE_SCHEMA,
        "policy_id": VISUAL_RENDER_POLICY_ID,
        "source_sha256": graph.source_sha256,
        "evidence_graph_sha256": augmented_graph.graph_sha256,
        "authority": "secondary_visual_evidence_only",
        "model_space_only": True,
        "paper_space_excluded": True,
        "region_count": len(region_manifest),
        "regions": region_manifest,
        "files": [
            {
                "path": item.relative_path,
                "sha256": item.sha256,
                "size_bytes": len(item.content),
            }
            for item in files
        ],
    })
    files.append(VisualEvidenceFile(manifest_path, manifest_bytes))
    return VisualEvidenceBundle(
        graph=augmented_graph,
        files=tuple(files),
        manifest_relative_path=manifest_path,
        region_count=len(region_manifest),
    )


__all__ = [
    "VISUAL_EVIDENCE_SCHEMA",
    "VISUAL_RENDER_POLICY_ID",
    "VisualEvidenceBundle",
    "VisualEvidenceError",
    "VisualEvidenceFile",
    "build_visual_evidence_bundle",
]
