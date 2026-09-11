"""Pre-semantic, layout-aware visual grounding for CAD scene understanding.

The visible raster helps a VLM recognise drawing conventions.  A parallel hit
map and JSON context bind every visible primitive back to immutable CAD Scene
Graph node IDs.  The bundle covers every layout independently and never
changes, classifies, or excludes a source entity.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cad_scene_graph import CadSceneGraph


SCENE_VISUAL_SCHEMA = "cad2gis.scene_visual_bundle.v1"
SCENE_VISUAL_POLICY_ID = "cad2gis.scene-visual.adaptive-layout.v1"

_OVERVIEW_SIZE = (1536, 1152)
_DETAIL_SIZE = (1280, 960)
_DETAIL_ENTITY_THRESHOLD = 250
_MAX_DETAIL_DEPTH = 3
_MAX_DETAIL_REGIONS_PER_LAYOUT = 24

_ACI_RGB = {
    1: (230, 30, 30),
    2: (210, 170, 0),
    3: (0, 170, 50),
    4: (0, 170, 180),
    5: (35, 80, 230),
    6: (190, 20, 190),
    7: (35, 35, 35),
    8: (100, 100, 100),
    9: (160, 160, 160),
}


class SceneVisualError(RuntimeError):
    """The scene visual bundle cannot satisfy its grounding contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _finite_point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(item) for item in point) else None


def _entity_points(entity: Any) -> tuple[tuple[float, float], ...]:
    points = tuple(
        point
        for point in (_finite_point(value) for value in entity.points)
        if point is not None
    )
    if points:
        return points
    centroid = _finite_point(entity.centroid)
    return () if centroid is None else (centroid,)


def _bounds(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    values = tuple(points)
    if not values:
        return 0.0, 0.0, 1.0, 1.0
    xs = [point[0] for point in values]
    ys = [point[1] for point in values]
    return min(xs), min(ys), max(xs), max(ys)


def _expanded(
    value: tuple[float, float, float, float],
    ratio: float = 0.025,
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = value
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
    return (
        min_x - width * ratio,
        min_y - height * ratio,
        max_x + width * ratio,
        max_y + height * ratio,
    )


def _intersects(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return not (
        left[2] < right[0]
        or left[0] > right[2]
        or left[3] < right[1]
        or left[1] > right[3]
    )


def _quadrants(
    value: tuple[float, float, float, float],
) -> tuple[tuple[str, tuple[float, float, float, float]], ...]:
    min_x, min_y, max_x, max_y = value
    mid_x = (min_x + max_x) * 0.5
    mid_y = (min_y + max_y) * 0.5
    return (
        ("nw", (min_x, mid_y, mid_x, max_y)),
        ("ne", (mid_x, mid_y, max_x, max_y)),
        ("sw", (min_x, min_y, mid_x, mid_y)),
        ("se", (mid_x, min_y, max_x, mid_y)),
    )


def _projector(
    value: tuple[float, float, float, float],
    size: tuple[int, int],
):
    min_x, min_y, max_x, max_y = value
    width, height = size
    span_x = max(max_x - min_x, 1e-12)
    span_y = max(max_y - min_y, 1e-12)
    scale = min((width - 24) / span_x, (height - 24) / span_y)
    offset_x = (width - span_x * scale) * 0.5
    offset_y = (height - span_y * scale) * 0.5

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


def _entity_color(entity: Any) -> tuple[int, int, int]:
    true_color = str(getattr(entity.style, "true_color", "")).strip().lstrip("#")
    if len(true_color) == 6:
        try:
            return tuple(
                int(true_color[index:index + 2], 16) for index in (0, 2, 4)
            )
        except ValueError:
            pass
    try:
        aci = int(getattr(entity.style, "aci_color", 7))
    except (TypeError, ValueError):
        aci = 7
    return _ACI_RGB.get(aci, (50, 50, 50))


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
        else:  # pragma: no cover
            raise SceneVisualError("Scene hit-map colour space is exhausted")
    return result


def _text_values(entity: Any) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    text = str(entity.text).strip()
    if text:
        values.append({"source": "entity_text", "key": "text", "value": text})
    attributes = entity.block_attributes
    if isinstance(attributes, Mapping):
        for key, value in sorted(attributes.items(), key=lambda item: str(item[0])):
            rendered = str(value).strip()
            if rendered:
                values.append({
                    "source": "block_attribute",
                    "key": str(key),
                    "value": rendered,
                })
    return values


def _ascii_preview(entity: Any) -> str:
    values = _text_values(entity)
    if not values:
        return ""
    return values[0]["value"].encode("ascii", errors="replace").decode("ascii")[:80]


def _context_entry(item: "_Renderable") -> dict[str, Any]:
    entity = item.entity
    return {
        "node_id": item.node_id,
        "logical_id": item.logical_id,
        "entity_key": str(entity.entity_key),
        "handle": str(entity.handle),
        "layout": str(entity.layout),
        "layout_role": str(entity.layout_role),
        "cad_role": str(entity.cad_role),
        "layer": str(entity.layer),
        "dwg_type": str(entity.dwg_type),
        "block_name": str(entity.block_name),
        "closed": bool(entity.closed),
        "text_values": _text_values(entity),
        "style": {
            "aci_color": getattr(entity.style, "aci_color", None),
            "true_color": getattr(entity.style, "true_color", None),
            "linetype": getattr(entity.style, "linetype", None),
            "lineweight": getattr(entity.style, "lineweight", None),
        },
        "native_bounds": list(item.bounds),
    }


@dataclass(frozen=True)
class SceneVisualFile:
    relative_path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return _sha256(self.content)


@dataclass(frozen=True)
class SceneVisualBundle:
    files: tuple[SceneVisualFile, ...]
    manifest_relative_path: str
    region_count: int
    layout_count: int
    render_conserved: bool

    @property
    def manifest_file(self) -> SceneVisualFile:
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


@dataclass(frozen=True)
class _Renderable:
    entity: Any
    node_id: str
    logical_id: str
    points: tuple[tuple[float, float], ...]
    bounds: tuple[float, float, float, float]


def _adaptive_regions(
    layout_bounds: tuple[float, float, float, float],
    items: Sequence[_Renderable],
) -> list[tuple[str, tuple[float, float, float, float], tuple[int, int]]]:
    regions = [("overview", layout_bounds, _OVERVIEW_SIZE)]
    if len(items) <= _DETAIL_ENTITY_THRESHOLD:
        return regions
    queue = deque(
        (name, bounds, 1) for name, bounds in _quadrants(layout_bounds)
    )
    details = 0
    while queue and details < _MAX_DETAIL_REGIONS_PER_LAYOUT:
        name, bounds, depth = queue.popleft()
        visible_count = sum(_intersects(item.bounds, bounds) for item in items)
        if visible_count == 0:
            continue
        regions.append((f"detail-{name}", bounds, _DETAIL_SIZE))
        details += 1
        if (
            visible_count > _DETAIL_ENTITY_THRESHOLD
            and depth < _MAX_DETAIL_DEPTH
            and details + len(queue) < _MAX_DETAIL_REGIONS_PER_LAYOUT
        ):
            queue.extend(
                (f"{name}-{child_name}", child_bounds, depth + 1)
                for child_name, child_bounds in _quadrants(bounds)
            )
    return regions


def build_scene_visual_bundle(
    *,
    graph: CadSceneGraph,
    entities: Iterable[Any],
    scene_candidates: Mapping[str, Any] | None = None,
) -> SceneVisualBundle:
    """Build a complete per-layout visual and structural grounding bundle."""

    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover
        raise SceneVisualError(
            "Pillow is required for CAD scene visual grounding"
        ) from exc

    source_nodes = {
        node.logical_id.removeprefix("source:"): node
        for node in graph.nodes
        if node.kind == "source_entity" and node.logical_id.startswith("source:")
    }
    by_layout: dict[str, list[_Renderable]] = defaultdict(list)
    unrendered_node_ids: list[str] = []
    for entity in sorted(entities, key=lambda item: str(item.entity_key)):
        node = source_nodes.get(str(entity.entity_key))
        if node is None:
            raise SceneVisualError(
                f"Source entity is absent from CAD Scene Graph: {entity.entity_key}"
            )
        points = _entity_points(entity)
        if not points:
            unrendered_node_ids.append(node.node_id)
            continue
        by_layout[str(entity.layout)].append(_Renderable(
            entity=entity,
            node_id=node.node_id,
            logical_id=node.logical_id,
            points=points,
            bounds=_bounds(points),
        ))

    colors = _hit_colors(node.node_id for node in source_nodes.values())
    files: list[SceneVisualFile] = []
    layout_manifest: list[dict[str, Any]] = []
    region_manifest: list[dict[str, Any]] = []
    rendered_node_ids: set[str] = set()

    for layout_index, layout in enumerate(sorted(by_layout, key=str.casefold)):
        items = by_layout[layout]
        layout_bounds = _expanded(_bounds(
            point for item in items for point in item.points
        ))
        layout_slug = f"layout-{layout_index:03d}-{_sha256(layout.encode('utf-8'))[:10]}"
        layout_context_file = SceneVisualFile(
            f"reasoning/scene_visual/{layout_slug}/layout-context.json",
            _canonical_bytes({
                "schema_version": "cad2gis.scene_layout_context.v1",
                "source_sha256": graph.source_sha256,
                "cad_scene_graph_sha256": graph.graph_sha256,
                "layout": layout,
                "entity_count": len(items),
                "entities": sorted(
                    (_context_entry(item) for item in items),
                    key=lambda item: item["node_id"],
                ),
            }),
        )
        files.append(layout_context_file)
        regions = _adaptive_regions(layout_bounds, items)
        layout_region_ids: list[str] = []
        for region_name, region_bounds, size in regions:
            region_id = f"{layout_slug}:{region_name}"
            layout_region_ids.append(region_id)
            visible = tuple(
                item for item in items if _intersects(item.bounds, region_bounds)
            )
            image = Image.new("RGB", size, (255, 255, 255))
            hit_image = Image.new("RGB", size, (0, 0, 0))
            draw = ImageDraw.Draw(image)
            hit_draw = ImageDraw.Draw(hit_image)
            project = _projector(region_bounds, size)
            hit_entries: dict[str, Any] = {}
            context_node_ids: list[str] = []

            for item in visible:
                screen = [project(point) for point in item.points]
                color = _entity_color(item.entity)
                hit_color = colors[item.node_id]
                rendered_node_ids.add(item.node_id)
                if len(screen) >= 2:
                    draw.line(screen, fill=color, width=2)
                    hit_draw.line(screen, fill=hit_color, width=7)
                    if bool(item.entity.closed) and screen[0] != screen[-1]:
                        draw.line((screen[-1], screen[0]), fill=color, width=2)
                        hit_draw.line(
                            (screen[-1], screen[0]), fill=hit_color, width=7,
                        )
                else:
                    x, y = screen[0]
                    draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
                    hit_draw.ellipse(
                        (x - 7, y - 7, x + 7, y + 7), fill=hit_color,
                    )
                preview = _ascii_preview(item.entity)
                if preview:
                    x, y = screen[0]
                    draw.text((x + 4, y - 11), preview, fill=color)
                color_hex = "".join(f"{channel:02X}" for channel in hit_color)
                hit_entries[color_hex] = {
                    "node_id": item.node_id,
                    "logical_id": item.logical_id,
                    "source_entity_key": str(item.entity.entity_key),
                }
                context_node_ids.append(item.node_id)

            prefix = f"reasoning/scene_visual/{layout_slug}/{region_name}"
            render_file = SceneVisualFile(f"{prefix}.png", _png_bytes(image))
            hit_file = SceneVisualFile(f"{prefix}.hit.png", _png_bytes(hit_image))
            index_file = SceneVisualFile(
                f"{prefix}.hit-index.json",
                _canonical_bytes({
                    "schema_version": "cad2gis.scene_visual_hit_index.v1",
                    "source_sha256": graph.source_sha256,
                    "cad_scene_graph_sha256": graph.graph_sha256,
                    "region_id": region_id,
                    "background_rgb": "000000",
                    "entries": hit_entries,
                }),
            )
            context_file = SceneVisualFile(
                f"{prefix}.context.json",
                _canonical_bytes({
                    "schema_version": "cad2gis.scene_region_context.v1",
                    "source_sha256": graph.source_sha256,
                    "cad_scene_graph_sha256": graph.graph_sha256,
                    "region_id": region_id,
                    "layout": layout,
                    "native_bounds": list(region_bounds),
                    "entity_count": len(context_node_ids),
                    "entity_node_ids": sorted(context_node_ids),
                    "layout_context_path": layout_context_file.relative_path,
                    "layout_context_sha256": layout_context_file.sha256,
                }),
            )
            files.extend((render_file, hit_file, index_file, context_file))
            region_manifest.append({
                "region_id": region_id,
                "layout": layout,
                "native_bounds": list(region_bounds),
                "pixel_width": size[0],
                "pixel_height": size[1],
                "visible_entity_count": len(visible),
                "render_path": render_file.relative_path,
                "render_sha256": render_file.sha256,
                "hit_map_path": hit_file.relative_path,
                "hit_map_sha256": hit_file.sha256,
                "hit_index_path": index_file.relative_path,
                "hit_index_sha256": index_file.sha256,
                "context_path": context_file.relative_path,
                "context_sha256": context_file.sha256,
                "layout_context_path": layout_context_file.relative_path,
                "layout_context_sha256": layout_context_file.sha256,
                "authority": "secondary_visual_evidence_only",
            })

        types = Counter(str(item.entity.dwg_type) for item in items)
        layers = Counter(str(item.entity.layer) for item in items)
        layout_manifest.append({
            "layout": layout,
            "entity_count": len(items),
            "native_bounds": list(layout_bounds),
            "region_ids": layout_region_ids,
            "entity_types": dict(sorted(types.items())),
            "layers": dict(sorted(layers.items(), key=lambda item: item[0].casefold())),
            "text_carrier_count": sum(bool(_text_values(item.entity)) for item in items),
            "context_path": layout_context_file.relative_path,
            "context_sha256": layout_context_file.sha256,
        })

    expected_node_ids = {node.node_id for node in source_nodes.values()}
    render_conserved = rendered_node_ids | set(unrendered_node_ids) == expected_node_ids
    if not render_conserved:
        raise SceneVisualError("Scene visual source-node conservation failed")
    manifest_path = "reasoning/scene_visual/manifest.json"
    manifest_file = SceneVisualFile(
        manifest_path,
        _canonical_bytes({
            "schema_version": SCENE_VISUAL_SCHEMA,
            "policy_id": SCENE_VISUAL_POLICY_ID,
            "source_sha256": graph.source_sha256,
            "cad_scene_graph_sha256": graph.graph_sha256,
            "authority": "secondary_visual_and_structural_evidence_only",
            "layout_count": len(layout_manifest),
            "region_count": len(region_manifest),
            "source_entity_node_count": len(expected_node_ids),
            "rendered_source_entity_node_count": len(rendered_node_ids),
            "unrendered_source_entity_node_ids": sorted(unrendered_node_ids),
            "render_conserved": render_conserved,
            "layouts": layout_manifest,
            "regions": region_manifest,
            "scene_candidates": dict(scene_candidates or {}),
            "model_contract": {
                "images_must_be_grounded_to_region_and_node_ids": True,
                "unassigned_nodes_default_to_unknown_scene": True,
                "scene_role_does_not_delete_source_entities": True,
            },
        }),
    )
    files.append(manifest_file)
    return SceneVisualBundle(
        files=tuple(files),
        manifest_relative_path=manifest_path,
        region_count=len(region_manifest),
        layout_count=len(layout_manifest),
        render_conserved=render_conserved,
    )


__all__ = [
    "SCENE_VISUAL_POLICY_ID",
    "SCENE_VISUAL_SCHEMA",
    "SceneVisualBundle",
    "SceneVisualError",
    "SceneVisualFile",
    "build_scene_visual_bundle",
]
