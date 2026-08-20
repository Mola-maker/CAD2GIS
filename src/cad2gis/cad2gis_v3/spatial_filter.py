"""Reusable spatial denoising: detectors + LLM supervisor + auto-exclude.

Called by both admission (``compile_onboarding_proposal``) and convert
(``convert()``) so that feature counts are consistent.  LLM decisions are
cached in ``spatial_regions.json`` per project.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .legend_detector import filter_legend_entities
from .model import SourceEntity
from .project_profile import _atomic_write_json

_NOISE_DISPOSITIONS = frozenset({
    "legend", "derived_noise", "technical_diagram", "annotation_frame",
    "layer_non_subject",
})

# A topology-abstract subdrawing is a translated copy of a reviewed cable
# route.  The copy repeats the main cable geometry but has no PTECH/SITE pole
# INSERT anchors near it; the original drawing keeps the network anchors.
_TOPOLOGY_ANCHOR_RADIUS_M = 50.0
_TOPOLOGY_MIN_LENGTH_M = 10.0
_TOPOLOGY_SHAPE_TOLERANCE_FRACTION = 0.005
_TOPOLOGY_MIN_TRANSLATION_M = 10.0

# Materialized block-definition frames (title-block/FDT specimens) are
# evidence-only unless they sit inside the deployment-anchor neighbourhood.
# The anchor radius is scale-free (10 x median pole spacing) with this
# absolute floor for drawings whose poles are very dense.
_MATERIALIZED_FRAME_GAP_MIN = 100.0


def _is_materialized_block_entity(entity: Any) -> bool:
    plan_domain = getattr(entity, "raw_properties", {}).get("plan_domain")
    return (
        isinstance(plan_domain, Mapping)
        and plan_domain.get("materialization") == "nested-insert-affine"
    )


def _segment_distance_m(point: Sequence[float], start: Sequence[float], end: Sequence[float]) -> float:
    """Perpendicular/endpoint distance from a point to one 2-D segment."""
    px, py = float(point[0]), float(point[1])
    ax, ay = float(start[0]), float(start[1])
    bx, by = float(end[0]), float(end[1])
    dx, dy = bx - ax, by - ay
    segment_sq = dx * dx + dy * dy
    if segment_sq <= 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / segment_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _centroid_near_polyline(
    point: Sequence[float], polyline: Sequence[Sequence[float]], radius_m: float,
) -> bool:
    for start, end in zip(polyline, polyline[1:]):
        if _segment_distance_m(point, start, end) <= radius_m:
            return True
    return False


def _polyline_bbox(polyline: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in polyline]
    ys = [float(point[1]) for point in polyline]
    return min(xs), min(ys), max(xs), max(ys)


def _translated_duplicate_route_pairs(
    routes: Sequence[Any],
) -> tuple[list[tuple[Any, Any, float]], list[dict[str, Any]]]:
    """Find same-layer polylines that are near-exact translations of each other.

    APD sheets put the real cable route and its topology-abstract copy side by
    side.  Both copies keep the same layer and vertex count; the abstract copy
    is an exact translation of the real route.  Short legend swatches are
    excluded by the caller's length floor, and a minimum translation distance
    keeps collocated duplicates (CAD double-drawn lines) out of this detector.
    """
    by_shape: dict[tuple[str, int], list[Any]] = {}
    for route in routes:
        by_shape.setdefault(
            (str(route.layer).strip().casefold(), len(route.points)),
            [],
        ).append(route)

    pairs: list[tuple[Any, Any, float]] = []
    records: list[dict[str, Any]] = []
    for group in by_shape.values():
        if len(group) < 2:
            continue
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                left_points = left.points
                right_points = right.points
                dx = float(right_points[0][0] - left_points[0][0])
                dy = float(right_points[0][1] - left_points[0][1])
                translation = math.hypot(dx, dy)
                min_x, min_y, max_x, max_y = _polyline_bbox(left_points)
                span = max(max_x - min_x, max_y - min_y)
                if span <= 0.0:
                    continue
                if translation < max(_TOPOLOGY_MIN_TRANSLATION_M, 0.01 * span):
                    continue
                max_deviation = max(
                    math.hypot(
                        float(right_point[0]) - (float(left_point[0]) + dx),
                        float(right_point[1]) - (float(left_point[1]) + dy),
                    )
                    for left_point, right_point in zip(left_points, right_points)
                )
                tolerance = max(0.5, _TOPOLOGY_SHAPE_TOLERANCE_FRACTION * span)
                if max_deviation > tolerance:
                    continue
                pairs.append((left, right, max_deviation))
                records.append({
                    "left_handle": left.handle,
                    "right_handle": right.handle,
                    "layer": left.layer,
                    "point_count": len(left_points),
                    "translation_dx_m": dx,
                    "translation_dy_m": dy,
                    "max_deviation_m": max_deviation,
                })
    return pairs, records


def _topology_subdrawing_keys(
    route_entities: Sequence[Any],
    anchor_inserts: Sequence[Any],
) -> tuple[frozenset[str], list[dict[str, Any]]]:
    """Detect unanchored translated copies of reviewed cable routes.

    The drawing-side copy whose route has no PTECH/SITE INSERT within the
    anchor radius is the topology abstraction; the copy corroborated by pole
    INSERTs is the deployment geometry.  When neither or both copies have
    anchors the detector stays fail-closed and keeps both routes.
    """
    pairs, pair_records = _translated_duplicate_route_pairs(route_entities)

    def support_count(route: Any) -> int:
        return sum(
            1
            for insert in anchor_inserts
            if _centroid_near_polyline(
                insert.centroid, route.points, _TOPOLOGY_ANCHOR_RADIUS_M,
            )
        )

    subdrawing_keys: set[str] = set()
    records: list[dict[str, Any]] = []
    for left, right, max_deviation in pairs:
        left_support = support_count(left)
        right_support = support_count(right)
        subdrawing = None
        if left_support == 0 and right_support > 0:
            subdrawing = left
        elif right_support == 0 and left_support > 0:
            subdrawing = right
        record = {
            "left_handle": left.handle,
            "right_handle": right.handle,
            "layer": left.layer,
            "left_anchor_support": left_support,
            "right_anchor_support": right_support,
            "max_deviation_m": max_deviation,
            "disposition": (
                "subdrawing" if subdrawing is not None else "ambiguous"
            ),
            "excluded_handle": subdrawing.handle if subdrawing is not None else None,
        }
        records.append(record)
        if subdrawing is not None:
            subdrawing_keys.add(subdrawing.entity_key)
    return frozenset(subdrawing_keys), records


def _deployment_anchor_radius(anchor_points: Sequence[Sequence[float]]) -> float | None:
    """Scale-free "main drawing" radius from pole (PTECH) insert spacing.

    The legend detector body bbox is not safe for materialized-frame noise:
    singleton outliers stay in its body bbox because only accepted clusters
    are removed, so a title-block frame can sit "inside" a body that spans the
    whole sheet.  Pole inserts are reviewed deployment anchors; the median
    nearest-neighbour distance between them is a local density estimate, and
    ten such spacings (at least 100 m) is a generous deployed-device
    neighbourhood that works across CRS scales and multi-cluster drawings.
    """
    distances: list[float] = []
    for index, point in enumerate(anchor_points):
        neighbours = [
            math.hypot(
                float(point[0]) - float(other[0]),
                float(point[1]) - float(other[1]),
            )
            for other_index, other in enumerate(anchor_points)
            if other_index != index
        ]
        if neighbours:
            distances.append(min(neighbours))
    positive = sorted(distance for distance in distances if distance > 0.01)
    if not positive:
        return None
    median = positive[len(positive) // 2]
    return max(_MATERIALIZED_FRAME_GAP_MIN, 10.0 * median)


def _materialized_frame_outlier(
    entity: Any,
    anchor_points: Sequence[Sequence[float]],
    anchor_radius: float | None,
) -> bool:
    """True for a materialized frame with no deployment anchor nearby.

    Materialized block-definition frames on reviewed BOITE layers are title/
    specimen geometry more often than deployed devices.  A frame is only a
    device candidate when it sits inside the deployment-anchor neighbourhood;
    otherwise it is deterministic noise.  With no reviewed pole anchors the
    guard fails closed and keeps every frame.
    """
    if anchor_radius is None:
        return False
    if not _is_materialized_block_entity(entity):
        return False
    if entity.dwg_type.upper() != "LWPOLYLINE":
        return False
    x, y = (float(value) for value in entity.centroid[:2])
    return min(
        math.hypot(x - float(point[0]), y - float(point[1]))
        for point in anchor_points
    ) > anchor_radius


def _layer_statistics(
    entities: Sequence[SourceEntity],
) -> list[dict[str, Any]]:
    from collections import Counter
    by_layer: dict[str, dict[str, int]] = {}
    type_counts: dict[str, Counter] = {}
    for entity in entities:
        layer = entity.layer.strip()
        if not layer:
            continue
        by_layer.setdefault(layer, {"entity_count": 0})
        by_layer[layer]["entity_count"] += 1
        type_counts.setdefault(layer, Counter())[entity.dwg_type] += 1
    stats = []
    for layer, counts in sorted(by_layer.items()):
        stats.append({
            "layer": layer,
            "entity_count": counts["entity_count"],
            "entity_types": dict(type_counts.get(layer, {}).most_common(6)),
        })
    return stats

_BOUNDARY_BAND_FRACTION = 0.03
_ANNOTATION_FRAME_TYPES = frozenset({
    "TEXT", "MTEXT", "LEADER", "MLEADER", "MULTILEADER",
    "LINE", "LWPOLYLINE",
})


def detect_annotation_frames(
    entities: Sequence[SourceEntity],
    body_bbox: Sequence[float] | None,
) -> tuple[frozenset[str], dict[str, Any]]:
    """Third detector: entities in a band around the body bounding box.

    Annotation call-out boxes (labels drawn from BOITE/FAT nodes out to the
    drawing boundary) cluster along the perimeter of the main body rectangle.
    Entities whose centroids fall inside a thin boundary band — and which are
    text/leader/frame primitives — are flagged as annotation-frame noise.

    Returns ``(candidate_keys, diagnostics)``.
    """
    if body_bbox is None or len(body_bbox) != 4:
        return frozenset(), {
            "status": "no_body_bbox", "candidate_count": 0,
        }
    min_x, min_y, max_x, max_y = (float(v) for v in body_bbox)
    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x <= 0.0 or span_y <= 0.0:
        return frozenset(), {
            "status": "degenerate_body_bbox", "candidate_count": 0,
        }
    band_x = span_x * _BOUNDARY_BAND_FRACTION
    band_y = span_y * _BOUNDARY_BAND_FRACTION

    candidates: set[str] = set()
    band_records: list[dict[str, Any]] = []
    for entity in entities:
        if entity.dwg_type.upper() not in _ANNOTATION_FRAME_TYPES:
            continue
        if not entity.points:
            continue
        centroid = entity.centroid
        x, y = float(centroid[0]), float(centroid[1])
        near_x_edge = x <= min_x + band_x or x >= max_x - band_x
        near_y_edge = y <= min_y + band_y or y >= max_y - band_y
        if not (near_x_edge or near_y_edge):
            continue
        inside = min_x <= x <= max_x and min_y <= y <= max_y
        if not inside:
            continue
        candidates.add(entity.entity_key)
        band_records.append({
            "entity_key": entity.entity_key,
            "layer": entity.layer,
            "dwg_type": entity.dwg_type,
            "near_x_edge": near_x_edge,
            "near_y_edge": near_y_edge,
        })

    return frozenset(candidates), {
        "status": "complete",
        "candidate_count": len(candidates),
        "band_fraction": _BOUNDARY_BAND_FRACTION,
        "body_bbox": [min_x, min_y, max_x, max_y],
        "sample_records": band_records[:20],
    }


_PLACEHOLDER_RE = re.compile(r"(.)\1{2,}")
_DENOISE_LABEL_RADIUS_M = 50.0

# Structural shape shared by the four reviewed APD (As Plan Drawing)
# pole-identifier families: dot-separated fields ending in ``P<digits>``.
# Used only for unclaimed text on POLE-semantic layers so legend notes like
# ``SLACK - 2 EXT`` never count.
# DOMAIN-SCOPED: valid for the APD pole-label convention, not a universal
# CAD pole shape.  Non-APD drawings should use their own reviewed families.
_POLE_IDENTIFIER_SHAPE = re.compile(r"(?i)\.\s*P\d+$")


def is_placeholder_text(text: str) -> bool:
    """AI/annotation placeholders repeat a single character (XXX, NNN, ...).

    Placeholder pole identifiers (``MR.XXX.P001``) must not count as real
    labels: they would otherwise protect legend specimens from the
    unlabelled-asset rule or wrongly survive boundary-band denoising.
    """
    return _PLACEHOLDER_RE.search(text) is not None


def is_pole_identifier_shape(text: str) -> bool:
    """True for the reviewed APD (As Plan Drawing) pole-label shape
    (e.g. ``MR.KLDYA.P017``)."""
    return _POLE_IDENTIFIER_SHAPE.search(str(text).strip()) is not None


def apply_spatial_denoising(
    *,
    entities: list[SourceEntity],
    catalog_roots: frozenset[str],
    plan_domain: Any,
    project_config_dir: Path | None,
    llm_mode: str = "off",
    route_regex: str | None = None,
    boundary_exempt_layers: Sequence[str] = (),
    label_text_patterns: Sequence[str] = (),
    reviewed_insert_layers: Sequence[str] = (),
    cable_protect_layers: Sequence[str] = (),
    dimension_protect_layers: Sequence[str] = (),
    boite_frame_layers: Sequence[str] = (),
    topology_anchor_insert_layers: Sequence[str] = (),
) -> dict[str, Any]:
    """Run both spatial detectors, optionally call LLM, and exclude noise.

    Args:
        entities: Plan-domain entity list (mutated in-place in assist mode).
        catalog_roots: From ``PlanDomainView.catalog_roots``.
        plan_domain: The ``PlanDomainView`` (used for diagnostics reference).
        project_config_dir: Per-project ``config/`` directory.
        llm_mode: ``"off"``, ``"observe"``, or ``"assist"``.
        boundary_exempt_layers: Layer names whose entities are real
            deployment geometry even when hugging the body perimeter (e.g.
            reviewed ZPM boundary layers); the annotation-frame band never
            excludes them.
        label_text_patterns: Reviewed asset-identifier regexes (from the
            project registry ``annotation_families``, every target class).
            Text entities matching one of these patterns are real deployed
            labels — never annotation-frame noise — and INSERT assets within
            ``_DENOISE_LABEL_RADIUS_M`` of such a label are protected from
            cached-region exclusion.  Placeholder texts (``MR.XXX.P001``)
            never count.
        reviewed_insert_layers: Layer names reviewed as INSERT targets in
            ``mapping_registry.insert_layer_families`` (all feature classes).
            An INSERT on one of these layers is deployed infrastructure and
            is never removed by noise dispositions — a lone FAT/CLOSURE box
            is a real device even when no nearby reviewed label or cable
            exists to corroborate it.
        cable_protect_layers: Sling-wire layer names (registry).  INSERT
            assets within ``_DENOISE_LABEL_RADIUS_M`` of a route/sling cable
            polyline are deployed infrastructure and are protected from
            cluster-based exclusion even when reviewed label patterns are
            unavailable (e.g. an AI onboarding pass generated placeholder-only
            patterns).
        dimension_protect_layers: Reviewed span-dimension layer names
            (registry).  DIMENSION entities on these layers are independent
            measurement evidence and are never removed by spatial denoising,
            even inside a cached legend cluster.
        boite_frame_layers: Reviewed BOITE target layers.  Materialized
            block-definition rectangles on these layers are excluded when far
            outside the drawing body (title-block/FDT specimens), while
            drawing-space FAT frames remain intact.
        topology_anchor_insert_layers: Reviewed PTECH (pole) INSERT layers.
            A translated duplicate cable-route copy with no such anchor near
            it is a topology-abstract subdrawing and is excluded.

    Returns:
        ``{"entities": list, "flag_map": dict, "diagnostics": dict}``
    """
    diagnostics: dict[str, Any] = {
        "schema_version": "cad2gis-spatial-filter-v1",
        "llm_mode": llm_mode,
        "status": "no_clusters_detected",
        "catalog_roots_count": len(catalog_roots),
        "flagged_count": 0,
        "auto_excluded_count": 0,
        "llm_decisions": [],
    }

    def _is_materialized_block_entity(entity: Any) -> bool:
        plan_domain = getattr(entity, "raw_properties", {}).get("plan_domain")
        return (
            isinstance(plan_domain, Mapping)
            and plan_domain.get("materialization") == "nested-insert-affine"
        )

    # Pre-denoise snapshot: label proximity checks must see entities that a
    # later stage is about to exclude — otherwise removing a real label would
    # cascade into removing the INSERT it identifies (unlabelled-asset rule /
    # cached-region exclusion would then treat the asset as a legend sample).
    original_entities = list(entities)
    # Drawing-space roots drive body/perimeter geometry.  Materialized
    # block-definition members (issue 4) are evidence-bearing candidates and
    # must not shift the annotation-frame body box: expanding title/frame
    # blocks would otherwise make real perimeter labels look interior and
    # vice versa.
    drawing_entities = [
        entity for entity in original_entities
        if not _is_materialized_block_entity(entity)
    ]
    diagnostics["drawing_entity_count"] = len(drawing_entities)
    diagnostics["materialized_entity_count"] = len(original_entities) - len(
        drawing_entities
    )
    label_patterns = [
        re.compile(str(pattern))
        for pattern in label_text_patterns
        if str(pattern).strip()
    ]
    label_centroids: list[tuple[float, float]] = []
    label_evidence_keys: set[str] = set()
    for entity in original_entities:
        if entity.dwg_type not in _ANNOTATION_FRAME_TYPES:
            continue
        text = (entity.text or "").strip()
        if not text or is_placeholder_text(text):
            continue
        reviewed_label = any(
            pattern.fullmatch(text) for pattern in label_patterns
        )
        pole_shape_label = (
            "POLE" in str(entity.layer).upper()
            and is_pole_identifier_shape(text)
        )
        if reviewed_label or pole_shape_label:
            label_centroids.append(
                (float(entity.centroid[0]), float(entity.centroid[1]))
            )
            label_evidence_keys.add(entity.entity_key)
    if label_patterns:
        diagnostics["label_text_pattern_count"] = len(label_patterns)
        diagnostics["label_text_centroid_count"] = len(label_centroids)
    diagnostics["label_evidence_key_count"] = len(label_evidence_keys)

    # Route/sling polylines: deployed assets hug the cable; a legend column
    # sits far from it.  Proximity to a cable polyline protects an INSERT
    # from cluster-based exclusion regardless of label-pattern availability.
    protect_layer_upper = {str(layer).strip().upper() for layer in cable_protect_layers}
    route_pattern = re.compile(route_regex) if route_regex else None
    cable_lines = [
        entity
        for entity in drawing_entities
        if entity.dwg_type.upper() in ("LINE", "LWPOLYLINE", "POLYLINE")
        and len(entity.points) >= 2
        and (
            (route_pattern is not None and route_pattern.search(entity.layer.strip()))
            or str(entity.layer).strip().upper() in protect_layer_upper
        )
    ]
    if cable_lines:
        diagnostics["cable_protect_line_count"] = len(cable_lines)

    dimension_layer_upper = {
        str(layer).strip().upper() for layer in dimension_protect_layers
    }
    dimension_evidence_keys: set[str] = {
        entity.entity_key
        for entity in drawing_entities
        if entity.dwg_type.upper() == "DIMENSION"
        and str(entity.layer).strip().upper() in dimension_layer_upper
    }
    diagnostics["dimension_evidence_key_count"] = len(dimension_evidence_keys)
    reviewed_insert_layer_upper = {
        str(layer).strip().upper() for layer in reviewed_insert_layers
    }
    boite_frame_layer_upper = {
        str(layer).strip().upper() for layer in boite_frame_layers
    }
    topology_anchor_layer_upper = {
        str(layer).strip().upper() for layer in topology_anchor_insert_layers
    }
    reviewed_insert_evidence_keys: set[str] = {
        entity.entity_key
        for entity in drawing_entities
        if entity.dwg_type.upper() == "INSERT"
        and str(entity.layer).strip().upper() in reviewed_insert_layer_upper
    }
    diagnostics["reviewed_insert_evidence_key_count"] = len(
        reviewed_insert_evidence_keys
    )

    def _near_cable(point: tuple[float, float]) -> bool:
        x, y = point
        for line in cable_lines:
            pts = line.points
            min_x = min(p[0] for p in pts) - _DENOISE_LABEL_RADIUS_M
            max_x = max(p[0] for p in pts) + _DENOISE_LABEL_RADIUS_M
            min_y = min(p[1] for p in pts) - _DENOISE_LABEL_RADIUS_M
            max_y = max(p[1] for p in pts) + _DENOISE_LABEL_RADIUS_M
            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                continue
            for left, right in zip(pts, pts[1:]):
                dx, dy = right[0] - left[0], right[1] - left[1]
                seg_sq = dx * dx + dy * dy
                if seg_sq <= 0.0:
                    continue
                t = max(
                    0.0,
                    min(1.0, ((x - left[0]) * dx + (y - left[1]) * dy) / seg_sq),
                )
                px, py = left[0] + t * dx, left[1] + t * dy
                if math.hypot(x - px, y - py) <= _DENOISE_LABEL_RADIUS_M:
                    return True
        return False

    # ── 1. Load cached regions ──────────────────────────────────────────
    confirmed_regions: list[dict[str, Any]] = []
    cached_layer_verdicts: list[dict[str, Any]] = []
    spatial_regions_path = project_config_dir / "spatial_regions.json" if project_config_dir else None
    if spatial_regions_path and spatial_regions_path.is_file():
        try:
            config = json.loads(spatial_regions_path.read_text(encoding="utf-8"))
            if isinstance(config, dict):
                confirmed_regions = list(config.get("clusters", ()))
                cached_layer_verdicts = list(config.get("layer_verdicts", ()))
                diagnostics["cached_regions_count"] = len(confirmed_regions)
                diagnostics["cached_layer_verdict_count"] = len(
                    cached_layer_verdicts
                )
        except (OSError, json.JSONDecodeError):
            pass

    # ── 2. Run legend detector ──────────────────────────────────────────
    # Body geometry is derived from drawing-space roots only so materialized
    # block specimens cannot shift the body bbox used by the annotation-frame
    # detector below (issue 4: INSERT expansion must not change reviewed
    # spatial-filter verdicts).
    legend_result = filter_legend_entities(
        drawing_entities,
        confirmed_regions=confirmed_regions,
        auto_exclude=False,
    )
    legend_diag = legend_result["diagnostics"]
    legend_flagged_keys: frozenset[str] = legend_result["legend_flagged_keys"]

    # ── 3. Aggregate catalog_roots + legend_flagged ─────────────────────
    flag_map: dict[str, str] = {}
    for key in catalog_roots:
        flag_map[key] = "scene_partition"
    for key in legend_flagged_keys:
        flag_map[key] = (
            "legend_detector"
            if flag_map.get(key) != "scene_partition"
            else "scene_partition+legend_detector"
        )
    # ── 3b. Topology-abstract subdrawing detector ───────────────────────
    # Some APD sheets add a right-side copy of the cable topology as an
    # abstract schematic.  It is an exact translation of a real cable route
    # but has no pole (PTECH) INSERTs along it.  Detect the duplicate pair
    # and mark the unanchored copy as deterministic noise; a legend gap
    # detector cannot see it because the two copies are close enough to the
    # main body and the schematic follows the reviewed cable layer.
    topology_route_lines = [
        entity
        for entity in drawing_entities
        if entity.dwg_type.upper() in ("LINE", "LWPOLYLINE", "POLYLINE")
        and len(entity.points) >= 2
        and route_pattern is not None
        and route_pattern.search(entity.layer.strip())
        and (
            entity.native_length is None
            or entity.native_length >= _TOPOLOGY_MIN_LENGTH_M
        )
    ]
    topology_anchor_inserts = [
        entity
        for entity in drawing_entities
        if entity.dwg_type.upper() == "INSERT"
        and str(entity.layer).strip().upper() in topology_anchor_layer_upper
    ]
    topology_subdrawing_keys, topology_subdrawing_records = (
        _topology_subdrawing_keys(topology_route_lines, topology_anchor_inserts)
    )
    for key in topology_subdrawing_keys:
        flag_map[key] = "topology_subdrawing"
    diagnostics["topology_subdrawing"] = {
        "status": "complete",
        "route_line_count": len(topology_route_lines),
        "anchor_insert_count": len(topology_anchor_inserts),
        "anchor_radius_m": _TOPOLOGY_ANCHOR_RADIUS_M,
        "excluded_count": len(topology_subdrawing_keys),
        "pairs": topology_subdrawing_records,
    }

    diagnostics["flagged_count"] = len(flag_map)
    diagnostics["body_bbox"] = legend_diag.get("body_bbox")
    diagnostics["clusters"] = legend_diag.get("clusters", ())
    if legend_diag.get("clusters"):
        diagnostics["status"] = "clusters_detected"

    # ── 4. LLM supervisor + layer semantics ────────────────────────────
    # When cached spatial regions exist, the decisions are already frozen:
    # re-calling the LLM would be non-deterministic and could diverge from
    # the admission-time exclusions.  Deterministic cache application happens
    # in the auto-exclude stage below.
    from .spatial_llm import classify_layer_semantics, classify_spatial_clusters

    unconfirmed = [
        c for c in legend_diag.get("clusters", ())
        if not c.get("confirmed", False)
    ]
    semantics: dict[str, Any] | None = None
    if confirmed_regions and llm_mode == "assist":
        llm_result = {
            "decisions": [],
            "flag_map_entries": {},
            "diagnostics": {
                "schema_version": "cad2gis-spatial-llm-v1",
                "llm_mode": llm_mode,
                "status": "cache_reused",
                "clusters_evaluated": 0,
                "decisions_accepted": 0,
            },
        }
        if cached_layer_verdicts:
            semantics = {
                "schema_version": "cad2gis-layer-semantics-v1",
                "status": "cache_reused",
                "verdicts": list(cached_layer_verdicts),
            }
        else:
            # Backfill one deterministic call for caches written before
            # layer verdicts were persisted; afterwards the verdict is reused
            # exactly like cluster dispositions.
            layer_stats = _layer_statistics(entities)
            if layer_stats:
                semantics = classify_layer_semantics(
                    layer_stats=layer_stats,
                    project_config_dir=project_config_dir,
                )
                cached_layer_verdicts = list(semantics.get("verdicts", ()))
    else:
        llm_result = classify_spatial_clusters(
            clusters=unconfirmed,
            entities=entities,
            body_bbox=legend_diag.get("body_bbox"),
            total_entities=len(entities),
            flag_map=flag_map,
            legend_flagged_keys=legend_flagged_keys,
            project_config_dir=project_config_dir,
            llm_mode=llm_mode,
        )
        flag_map.update(llm_result["flag_map_entries"])
        if llm_mode in ("observe", "assist"):
            layer_stats = _layer_statistics(entities)
            if layer_stats:
                semantics = classify_layer_semantics(
                    layer_stats=layer_stats,
                    project_config_dir=project_config_dir,
                )
                cached_layer_verdicts = list(semantics.get("verdicts", ()))
    if semantics is not None:
        diagnostics["layer_semantics"] = semantics
        if semantics.get("status") in {"complete", "cache_reused"}:
            non_subject_layers = {
                str(v["layer"]).strip()
                for v in semantics.get("verdicts", ())
                if v.get("verdict") == "non_subject"
            }
            for entity in entities:
                if entity.layer.strip() in non_subject_layers:
                    flag_map.setdefault(entity.entity_key, "llm_layer_non_subject")
    diagnostics["llm_spatial"] = llm_result["diagnostics"]
    diagnostics["llm_decisions"] = llm_result["decisions"]

    frame_keys, frame_diag = detect_annotation_frames(
        entities, legend_diag.get("body_bbox"),
    )
    for key in frame_keys:
        flag_map.setdefault(key, "boundary_band")
    diagnostics["annotation_frame_band"] = frame_diag

    # ── 6. Save decisions to spatial_regions.json ──────────────────────
    _save_spatial_regions(
        spatial_regions_path,
        llm_result,
        legend_diag,
        flag_map,
        llm_mode,
        layer_semantics=semantics,
    )

    # ── 6. Auto-exclude deterministic and LLM noise ─────────────────────
    # Boundary-band candidates are deterministic detector output and are
    # excluded in every mode (the LLM only supervises legend clusters).  This
    # keeps ``--llm off`` runs reproducible and keeps the reviewed feature
    # gate meaningful after INSERT expansion adds block-definition evidence.
    import re as _re
    route_pattern = _re.compile(route_regex) if route_regex else None
    route_exempt: set[str] = set()
    if route_pattern is not None:
        # Route exemption: entities whose layer matches the reviewed route
        # regex are never excluded — LLM noise decisions cannot kill cables.
        # A length floor keeps legend sample lines (0.4-9 m coloured swatches
        # drawn on FO CORE layers) outside the exemption: only real route
        # segments (hundreds of metres) are protected.
        route_exempt = {
            e.entity_key
            for e in drawing_entities
            if route_pattern.search(e.layer.strip())
            and (e.native_length is None or e.native_length >= 10.0)
        }
        diagnostics["route_exempt_length_floor_m"] = 10.0

    noise_keys: set[str] = set()
    evidence_exempt = (
        route_exempt
        | label_evidence_keys
        | dimension_evidence_keys
        | reviewed_insert_evidence_keys
    )
    exempt_layers_upper = {str(layer).upper() for layer in boundary_exempt_layers}
    entity_by_key = {entity.entity_key: entity for entity in entities}

    def _is_reviewed_device_frame(entity: Any) -> bool:
        if entity is None or entity.dwg_type.upper() != "LWPOLYLINE":
            return False
        if str(entity.layer).strip().upper() not in reviewed_insert_layer_upper:
            return False
        points = entity.points
        if len(points) < 4:
            return False
        if math.dist(points[0], points[-1]) <= 1.0:
            area = abs(sum(
                points[i][0] * points[(i + 1) % len(points)][1]
                - points[(i + 1) % len(points)][0] * points[i][1]
                for i in range(len(points))
            )) / 2.0
            return 0.05 <= area <= 400.0
        if len(points) != 4:
            return False
        p0, p1, p2, p3 = points
        v0 = (p1[0] - p0[0], p1[1] - p0[1])
        v1 = (p2[0] - p1[0], p2[1] - p1[1])
        v2 = (p3[0] - p2[0], p3[1] - p2[1])
        missing = (p3[0] - p0[0], p3[1] - p0[1])
        norms = [math.hypot(*v) for v in (v0, v1, v2, missing)]
        if min(norms) <= 0.0:
            return False
        units = [(v[0] / n, v[1] / n) for v, n in zip((v0, v1, v2, missing), norms)]
        parallel_long = abs(units[0][0] * units[2][1] - units[0][1] * units[2][0]) <= 0.15
        same_direction_long = abs(units[0][0] * units[2][0] + units[0][1] * units[2][1]) >= 0.7
        perpendicular = abs(units[0][0] * units[1][0] + units[0][1] * units[1][1]) <= 0.3
        missing_parallel = abs(units[1][0] * units[3][1] - units[1][1] * units[3][0]) <= 0.15
        return parallel_long and same_direction_long and perpendicular and missing_parallel

    reviewed_device_frames = [
        entity for entity in entities if _is_reviewed_device_frame(entity)
    ]

    # Deterministic noise discovered after body geometry is known.  These keys
    # bypass LLM/cached disposition processing on purpose: route exemption and
    # label protection exist for legend verdicts, not for a geometric
    # duplicate or a materialized frame specimen outside the drawing body.
    topology_subdrawing_noise = set(topology_subdrawing_keys)
    noise_keys.update(topology_subdrawing_noise)
    diagnostics["topology_subdrawing"]["excluded_route_keys"] = sorted(
        topology_subdrawing_noise
    )
    materialized_frame_outlier_keys: set[str] = set()
    deployment_anchor_points = [
        (float(entity.centroid[0]), float(entity.centroid[1]))
        for entity in topology_anchor_inserts
    ]
    deployment_anchor_radius = _deployment_anchor_radius(
        deployment_anchor_points
    )
    for entity in entities:
        if str(entity.layer).strip().upper() not in boite_frame_layer_upper:
            continue
        if not _is_reviewed_device_frame(entity):
            continue
        if _is_materialized_block_entity(entity) is False:
            continue
        # Real deployed frames are corroborated by a reviewed label or a
        # cable within the same radius the label-protection rule uses; the
        # far-outlier guard must never kill such a frame.
        ctr = entity.centroid
        if any(
            math.dist(ctr, text_centroid) <= _DENOISE_LABEL_RADIUS_M
            for text_centroid in label_centroids
        ):
            continue
        if cable_lines and _near_cable(ctr):
            continue
        if _materialized_frame_outlier(
            entity, deployment_anchor_points, deployment_anchor_radius,
        ):
            materialized_frame_outlier_keys.add(entity.entity_key)
    noise_keys.update(materialized_frame_outlier_keys)
    diagnostics["materialized_frame_outlier"] = {
        "status": "complete",
        "anchor_count": len(deployment_anchor_points),
        "anchor_radius_m": deployment_anchor_radius,
        "label_cable_protected_radius_m": _DENOISE_LABEL_RADIUS_M,
        "excluded_count": len(materialized_frame_outlier_keys),
        "excluded_keys": sorted(materialized_frame_outlier_keys),
    }

    def _label_protected_insert(key: str) -> bool:
        # A deployed asset (INSERT) carrying a reviewed identifier within
        # the label radius — or hugging a route/sling cable — is real
        # infrastructure; noise verdicts (LLM clusters, cached regions,
        # boundary band) never kill it.
        entity = entity_by_key.get(key)
        if entity is None or entity.dwg_type.upper() != "INSERT":
            return False
        ctr = entity.centroid
        if any(
            math.dist(ctr, text_centroid) <= _DENOISE_LABEL_RADIUS_M
            for text_centroid in label_centroids
        ):
            return True
        if cable_lines and _near_cable(ctr):
            return True
        return False

    for key, source in flag_map.items():
        if key in evidence_exempt:
            continue
        disp = source
        for prefix in ("llm_",):
            if disp.startswith(prefix):
                disp = disp[len(prefix):]
        disp = disp.replace("_fallback", "")
        if disp == "boundary_band":
            # Deterministic annotation-frame detector: frame text/leaders
            # hugging the body perimeter are noise by construction —
            # unless the reviewed boundary layer says otherwise.
            entity = entity_by_key.get(key)
            if entity is not None and str(entity.layer).strip().upper() in exempt_layers_upper:
                continue
            if _is_reviewed_device_frame(entity):
                # Small closed LWPOLYLINE frames on a reviewed BOITE target
                # layer (e.g. the yellow FAT rectangle) are device geometry,
                # not sheet-border annotation noise.
                continue
            if (
                entity is not None
                and entity.dwg_type in _ANNOTATION_FRAME_TYPES
                and str(entity.text or "").strip().isdigit()
                and (
                    "LABEL" in str(entity.layer).upper()
                    or str(entity.layer).strip().upper() in dimension_layer_upper
                )
                and cable_lines
                and _near_cable(entity.centroid)
            ):
                # Bare integer TEXT labels near a cable are span lengths,
                # not sheet-border annotation noise.
                continue
            if (
                entity is not None
                and entity.dwg_type in _ANNOTATION_FRAME_TYPES
                and str(entity.text or "").strip().isdigit()
                and reviewed_device_frames
                and any(
                    math.dist(entity.centroid, frame.centroid) <= 20.0
                    for frame in reviewed_device_frames
                )
            ):
                # Integer labels next to a reviewed BOITE frame (the FAT
                # sequence number) belong to the device, not the sheet
                # border, even when the label itself hugs the perimeter band.
                continue
            if (
                entity is not None
                and (entity.text or "").strip()
                and not is_placeholder_text(entity.text)
                and any(
                    pattern.fullmatch(entity.text.strip())
                    for pattern in label_patterns
                )
            ):
                # A reviewed asset identifier (e.g. pole label) hugging
                # the perimeter is deployment, not an annotation frame.
                continue
            noise_keys.add(key)
            continue
        if llm_mode != "assist":
            continue
        if disp in _NOISE_DISPOSITIONS:
            if not _label_protected_insert(key):
                noise_keys.add(key)
            continue
        if disp == "llm_layer_non_subject":
            if not _label_protected_insert(key):
                noise_keys.add(key)
            continue

    # Apply cached LLM decisions from spatial_regions.json in every mode: on
    # later runs the clusters are already confirmed so the LLM is not called
    # again, but the stored dispositions must still exclude noise.  The same
    # label/cable proximity protection as assist mode applies so an ``--llm
    # off`` replay cannot kill deployed assets that a legend cluster touched.
    for region in confirmed_regions:
        if not isinstance(region, Mapping):
            continue
        if str(region.get("disposition", "")) not in _NOISE_DISPOSITIONS:
            continue
        for mid in region.get("member_ids", ()):
            if str(mid) in evidence_exempt:
                continue
            if _label_protected_insert(str(mid)):
                # A cached legend verdict must not kill a deployed asset:
                # an INSERT carrying a reviewed identifier is real
                # infrastructure regardless of which cluster (possibly
                # legend+mixed) the LLM grouped it into.
                continue
            noise_keys.add(mid)

    if route_exempt:
        diagnostics["route_exempt_count"] = len(route_exempt)
    diagnostics["evidence_exempt_count"] = len(evidence_exempt)
    diagnostics["label_evidence_protected_count"] = len(label_evidence_keys)
    diagnostics["dimension_evidence_protected_count"] = len(dimension_evidence_keys)
    if noise_keys:
        pre_count = len(entities)
        entities[:] = [e for e in entities if e.entity_key not in noise_keys]
        diagnostics["auto_excluded_count"] = pre_count - len(entities)
        diagnostics["auto_excluded_keys"] = sorted(noise_keys)
        diagnostics["status"] = "denoised"

    return {
        "entities": list(entities),
        "flag_map": flag_map,
        "diagnostics": diagnostics,
        "catalog_roots": catalog_roots,
    }


def _save_spatial_regions(
    path: Path | None,
    llm_result: dict[str, Any],
    legend_diag: dict[str, Any],
    flag_map: dict[str, str],
    llm_mode: str,
    *,
    layer_semantics: Mapping[str, Any] | None = None,
) -> None:
    if path is None or llm_mode == "off":
        return
    now = datetime.now(timezone.utc).isoformat()
    # Persist exactly the members the admission run will exclude: the LLM
    # decision skips members already flagged as scene_partition (those are
    # style-catalog roots, not legend noise) — the cache must mirror that.
    cluster_members = {
        str(cluster.get("cluster_id", "")): [
            str(mid)
            for mid in cluster.get("member_ids", ())
            if "scene_partition" not in str(flag_map.get(str(mid), ""))
        ]
        for cluster in legend_diag.get("clusters", ())
        if isinstance(cluster, Mapping)
    }
    regions: list[dict[str, Any]] = []
    for dec in llm_result.get("decisions", []):
        if not dec.get("applied", True):
            continue
        regions.append({
            "cluster_id": dec["cluster_id"],
            "member_count": dec.get("member_count", 0),
            "member_ids": cluster_members.get(str(dec["cluster_id"]), []),
            "disposition": dec["disposition"],
            "confidence": dec["confidence"],
            "justification": dec.get("justification", ""),
            "provenance": {
                "detector": dec.get("detector_source", ""),
                "llm_model": llm_result["diagnostics"].get("llm_model", ""),
                "llm_decision": dec["disposition"],
                "llm_confidence": dec["confidence"],
                "confirmed_by": f"llm-{llm_mode}",
                "confirmed_at": now,
            },
        })
    layer_verdicts: list[dict[str, Any]] = []
    if llm_mode == "assist" and isinstance(layer_semantics, Mapping):
        for verdict in layer_semantics.get("verdicts", ()):
            if not isinstance(verdict, Mapping):
                continue
            if not str(verdict.get("layer", "")).strip():
                continue
            layer_verdicts.append({
                "layer": str(verdict.get("layer", "")).strip(),
                "verdict": str(verdict.get("verdict", "subject")).strip(),
                "confidence": float(verdict.get("confidence", 0.0)),
                "justification": str(verdict.get("justification", "")),
                "provenance": {
                    "confirmed_by": f"llm-{llm_mode}",
                    "confirmed_at": now,
                },
            })
    if regions or layer_verdicts:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, {
            "schema_version": "cad2gis-spatial-regions-v1",
            "generated": now,
            "llm_mode": llm_mode,
            "clusters": regions,
            "layer_verdicts": layer_verdicts,
        })
