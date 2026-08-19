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
