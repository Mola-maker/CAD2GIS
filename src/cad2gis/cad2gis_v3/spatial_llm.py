"""LLM supervisor for spatial cluster disposition classification.

This module wraps the OpenAI-compatible provider so that unconfirmed
spatial clusters (from ``scene_partition`` and ``legend_detector``) can be
semantically judged at runtime when ``--llm observe`` or ``--llm assist``
is active.

The prompt template is generic FTTH domain knowledge.  Project-specific
overrides live in ``spatial_prompt.md`` or ``spatial_regions.json`` beside
the project config — never hardcoded here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .curation_providers import (
    OpenAICompatibleProvider,
    ProviderError,
    ReviewRequest,
    load_provider_config,
)

_logger = logging.getLogger(__name__)

# ── Generic FTTH spatial analysis prompt ───────────────────────────────────
_GENERIC_SPATIAL_PROMPT = """\
You are a CAD drawing spatial analyst for FTTH (Fibre To The Home) telecom \
infrastructure plans.

Model space in these drawings mixes the geographic network with non-subject \
elements.  Your job is to classify each detected spatial cluster into exactly \
one of these dispositions:

- subject: the main geographic cable network — keep and convert
- legend: a legend / symbol table / keterangan block — noise, exclude
- derived_noise: a sub-drawing whose content is already represented in the \
  converted output (e.g. a residential numbering panel that was split off the \
  main drawing) — noise, exclude
- technical_diagram: a technical structure diagram (e.g. FDT STRUCTURE, \
  splicing schematic, panel diagram) — not geographic, exclude
- annotation_frame: a boundary / frame that only contains label call-outs \
  from FDT/FAT/BOITE nodes — noise, exclude

Decision rules:
- Clusters far from the main body bbox (offset > 1.5x body span) with anchor \
  text like LEGEND, LEGENDA, SIMBOL, KETERANGAN are legend.
- Clusters with layer names like FDT STRUCTURE, FDT-Info, or containing \
  many INSERT blocks of type FAT/CLOSURE/OTB but no cable geometry are \
  technical_diagram.
- Clusters that sit beside the main body but contain only text labels \
  referencing infrastructure nodes are annotation_frame.
- Clusters whose layer names suggest residential / home-pass data but no \
  cable routes are derived_noise.
- Everything else that is clearly part of the geographic network is subject.

Respond with a JSON object in exactly this format:
```json
{
  "decisions": [
    {
      "cluster_id": "<string>",
      "disposition": "legend|derived_noise|technical_diagram|annotation_frame|subject",
      "confidence": <0.0-1.0>,
      "justification": "<one-sentence reason>"
    }
  ]
}
```
Include one entry per cluster."""

# JSON schema for structured output
_CLUSTER_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    "disposition": {
                        "type": "string",
                        "enum": [
                            "subject",
                            "legend",
                            "derived_noise",
                            "technical_diagram",
                            "annotation_frame",
                        ],
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "justification": {"type": "string"},
                },
                "required": ["cluster_id", "disposition", "confidence", "justification"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


_LAYER_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "layer": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["subject", "non_subject"],
                    },
                    "confidence": {
                        "type": "number", "minimum": 0.0, "maximum": 1.0,
                    },
                    "justification": {"type": "string"},
                },
                "required": ["layer", "verdict", "confidence", "justification"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def classify_layer_semantics(
    *,
    layer_stats: list[dict[str, Any]],
    project_config_dir: Path | None = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    """Ask the LLM which layers are subject vs non-subject (legend/schematic).

    A single call classifies every layer with entity statistics.  The result
    is cached to ``spatial_regions.json`` so later runs reuse the verdicts
    without another model call.
    """
    result: dict[str, Any] = {
        "schema_version": "cad2gis-layer-semantics-v1",
        "status": "not_called",
        "verdicts": [],
    }
    if not layer_stats:
        return result
    try:
        config = load_provider_config(provider=provider_id)
    except ProviderError as exc:
        result["status"] = "provider_unavailable"
        result["error"] = str(exc)
        return result

    prompt = (
        "You are classifying CAD drawing layers of an FTTH telecom plan. "
        "For each layer, decide whether it carries SUBJECT network content "
        "(cables, poles, closures, splitters, route geometry) or NON_SUBJECT "
        "content (legends, symbol tables, DESIGN SUMMARY diagrams, title "
        "blocks, annotation frames, notes). Layers whose names clearly "
        "indicate schematic/legend content (e.g. DESIGN SUMMARY, TITLE BLOCK, "
        "LEGENDA, KETERANGAN) are non_subject. Device layers (FAT, FDT, POLE, "
        "CABLE, FO * CORE, HOME) are subject. Respond in JSON."
    )
    request = ReviewRequest(
        system_prompt=prompt,
        context={
            "schema_version": "cad2gis-layer-semantics-v1",
            "layers": layer_stats,
        },
        json_schema=_LAYER_VERDICT_SCHEMA,
        schema_name="cad2gis_layer_semantics",
    )
    try:
        response = provider.review(request)
        parsed = json.loads(response.content)
        verdicts = list(parsed.get("verdicts", ()))
    except Exception as exc:
        result["status"] = "llm_error"
        result["error"] = str(exc)
        return result
    result["status"] = "complete"
    result["verdicts"] = [
        {
            "layer": str(v.get("layer", "")),
            "verdict": str(v.get("verdict", "subject")),
            "confidence": float(v.get("confidence", 0.0)),
            "justification": str(v.get("justification", "")),
        }
        for v in verdicts
        if isinstance(v, dict) and str(v.get("layer", "")).strip()
    ]
    return result


def _load_prompt_template(project_config_dir: Path | None) -> str:
    """Load per-project prompt override; fall back to the generic template."""
    if project_config_dir is not None:
        md_path = project_config_dir / "spatial_prompt.md"
        if md_path.is_file():
            try:
                return md_path.read_text(encoding="utf-8")
            except OSError:
                pass
        regions_path = project_config_dir / "spatial_regions.json"
        if regions_path.is_file():
            try:
                config = json.loads(regions_path.read_text(encoding="utf-8"))
                if isinstance(config, dict) and isinstance(config.get("prompt"), str):
                    return config["prompt"]
            except (OSError, json.JSONDecodeError):
                pass
    return _GENERIC_SPATIAL_PROMPT


def _build_cluster_context(
    clusters: Iterable[Mapping[str, Any]],
    body_bbox: list[float] | None,
    total_entities: int,
    flag_map: Mapping[str, str],
) -> dict[str, Any]:
    """Build the context dict sent to the LLM for each unconfirmed cluster."""
    if body_bbox and len(body_bbox) == 4:
        body_w = max(body_bbox[2] - body_bbox[0], 1.0)
        body_h = max(body_bbox[3] - body_bbox[1], 1.0)
    else:
        body_w = body_h = 1.0

    cluster_ctxs: list[dict[str, Any]] = []
    for cluster in clusters:
        bbox = cluster.get("bbox")
        centroid_x = (bbox[0] + bbox[2]) / 2.0 if bbox and len(bbox) == 4 else 0.0
        centroid_y = (bbox[1] + bbox[3]) / 2.0 if bbox and len(bbox) == 4 else 0.0

        x_offset = centroid_x - ((body_bbox[0] + body_bbox[2]) / 2.0) if body_bbox else 0.0
        y_offset = centroid_y - ((body_bbox[1] + body_bbox[3]) / 2.0) if body_bbox else 0.0

        # Sample entity keys and their flag sources for context
        member_ids = cluster.get("member_ids", ())
        sampled_sources: dict[str, int] = {}
        for mid in member_ids[:30]:
            src = flag_map.get(str(mid), "unknown")
            sampled_sources[src] = sampled_sources.get(src, 0) + 1

        cluster_ctxs.append({
            "cluster_id": cluster.get("cluster_id", ""),
            "detector": cluster.get("detector", "legend_detector"),
            "member_count": cluster.get("member_count", 0),
            "bbox": bbox,
            "sampled_text": cluster.get("sampled_text", ())[:30],
            "anchor_hits": cluster.get("anchor_hits", ()),
            "layer_distribution": cluster.get("layer_distribution", {}),
            "centroid": [round(centroid_x, 2), round(centroid_y, 2)],
            "body_bbox_offset": {
                "x_offset": round(x_offset, 2),
                "y_offset": round(y_offset, 2),
                "x_offset_ratio": round(abs(x_offset) / body_w, 2) if body_w > 0 else 0,
                "y_offset_ratio": round(abs(y_offset) / body_h, 2) if body_h > 0 else 0,
            },
            "detector_confidence": cluster.get("confidence", 0.0),
            "flag_source_distribution": sampled_sources,
        })

    return {
        "clusters": cluster_ctxs,
        "body_bbox": body_bbox,
        "total_entities": total_entities,
        "flagged_entity_count": len(flag_map),
    }


def _extract_cluster_layer_distribution(
    clusters: list[dict[str, Any]],
    legend_flagged_keys: frozenset[str],
    entities: list[Any],
) -> None:
    """Mutate cluster dicts in-place to add layer_distribution."""
    for cluster in clusters:
        member_ids = set(str(mid) for mid in cluster.get("member_ids", ()))
        layers: dict[str, int] = {}
        sampled_text: list[str] = []
        for entity in entities:
            key = str(getattr(entity, "entity_key", ""))
            if key in member_ids:
                layer = str(getattr(entity, "layer", "") or "")
                layers[layer] = layers.get(layer, 0) + 1
                text = str(getattr(entity, "text", "") or "").strip()
                if text and len(sampled_text) < 30:
                    sampled_text.append(text)
        cluster["layer_distribution"] = dict(
            sorted(layers.items(), key=lambda item: -item[1])
        )
        cluster["sampled_text"] = sampled_text


def classify_spatial_clusters(
    clusters: list[dict[str, Any]],
    *,
    entities: list[Any],
    body_bbox: list[float] | None,
    total_entities: int,
    flag_map: dict[str, str],
    legend_flagged_keys: frozenset[str],
    provider_id: str | None = None,
    project_config_dir: Path | None = None,
    llm_mode: str = "off",
) -> dict[str, Any]:
    """Ask an LLM supervisor to classify unconfirmed spatial clusters.

    Args:
        clusters: List of cluster dicts from ``detect_legend_clusters``
            (already augmented with layer_distribution by the caller).
        entities: Plan-domain entity list (used for text/layer sampling).
        body_bbox: The body bbox from legend detection.
        total_entities: Total plan-domain entity count.
        flag_map: Existing ``{entity_key: source}`` map; LLM decisions
            that add keys with an ``llm_*`` prefix.
        legend_flagged_keys: Keys flagged by ``filter_legend_entities``.
        provider_id: ``"deepseek"`` or ``"new_api"`` (passed to
            ``load_provider_config``).
        project_config_dir: Per-project ``config/`` directory for prompt
            template overrides.
        llm_mode: ``"observe"`` (recommend only) or ``"assist"``
            (auto-apply decisions).

    Returns:
        ``{"decisions": [...], "flag_map_entries": {...}, "diagnostics": {...}}``
    """
    result: dict[str, Any] = {
        "decisions": [],
        "flag_map_entries": {},
        "diagnostics": {
            "schema_version": "cad2gis-spatial-llm-v1",
            "llm_mode": llm_mode,
            "clusters_evaluated": 0,
            "decisions_accepted": 0,
            "status": "not_called",
            "error": None,
        },
    }

    if llm_mode not in ("observe", "assist"):
        return result

    if not clusters:
        result["diagnostics"]["status"] = "no_unconfirmed_clusters"
        return result

    # Augment clusters with layer/text data from entities
    _extract_cluster_layer_distribution(clusters, legend_flagged_keys, entities)

    try:
        config = load_provider_config(provider=provider_id)
    except ProviderError as exc:
        result["diagnostics"]["status"] = "provider_unavailable"
        result["diagnostics"]["error"] = str(exc)
        return result

    provider = OpenAICompatibleProvider(config)
    prompt = _load_prompt_template(project_config_dir)
    context = _build_cluster_context(clusters, body_bbox, total_entities, flag_map)

    request = ReviewRequest(
        system_prompt=prompt,
        context=context,
        json_schema=_CLUSTER_CLASSIFICATION_SCHEMA,
        schema_name="spatial_cluster_classification",
    )

    result["diagnostics"]["status"] = "called"
    result["diagnostics"]["clusters_evaluated"] = len(clusters)
    result["diagnostics"]["llm_model"] = config.model
    result["diagnostics"]["llm_provider"] = getattr(config, "provider_id", getattr(config, "provider", "unknown"))

    try:
        response = provider.review(request)
    except ProviderError as exc:
        result["diagnostics"]["status"] = "llm_error"
        result["diagnostics"]["error"] = str(exc)
        _logger.warning("LLM spatial classification failed: %s", exc)
        # Fallback: use detector confidence for high-confidence clusters
        return _detector_confidence_fallback(result, clusters)

    result["diagnostics"]["llm_response_preview"] = (
        response.content[:500] if response.content else ""
    )
    try:
        parsed = json.loads(response.content)
        llm_decisions = list(parsed.get("decisions", ()))
    except (json.JSONDecodeError, TypeError) as exc:
        result["diagnostics"]["status"] = "llm_parse_error"
        result["diagnostics"]["error"] = str(exc)
        return _detector_confidence_fallback(result, clusters)

    result["diagnostics"]["llm_response_sha256"] = response.response_sha256
    result["diagnostics"]["llm_request_sha256"] = response.request_sha256

    for decision in llm_decisions:
        cluster_id = str(decision.get("cluster_id", ""))
        disposition = str(decision.get("disposition", "")).strip().casefold()
        confidence = float(decision.get("confidence", 0.0))
        justification = str(decision.get("justification", ""))

        if disposition not in {
            "subject", "legend", "derived_noise",
            "technical_diagram", "annotation_frame",
        }:
            disposition = "subject"

        # Find matching cluster and its member_ids
        member_ids: list[str] = []
        detector_source = "legend_detector"
        for cluster in clusters:
            if cluster.get("cluster_id") == cluster_id:
                member_ids = [str(mid) for mid in cluster.get("member_ids", ())]
                detector_source = cluster.get("detector", "legend_detector")
                break

        llm_source = f"llm_{disposition}"
        decision_record = {
            "cluster_id": cluster_id,
            "disposition": disposition,
            "confidence": confidence,
            "justification": justification,
            "member_count": len(member_ids),
            "detector_source": detector_source,
        }

        if llm_mode == "assist":
            # In assist mode, auto-apply LLM decisions
            for key in member_ids:
                # Don't override scene_partition detections
                existing = flag_map.get(key, "")
                if "scene_partition" not in existing:
                    result["flag_map_entries"][key] = llm_source
            decision_record["applied"] = True
            result["diagnostics"]["decisions_accepted"] += 1
        else:
            # observe mode: record but don't apply
            decision_record["applied"] = False

        result["decisions"].append(decision_record)

    result["diagnostics"]["status"] = "complete"
    result["diagnostics"]["total_decisions"] = len(result["decisions"])
    return result


def _detector_confidence_fallback(
    result: dict[str, Any],
    clusters: list[dict[str, Any]],
) -> dict[str, Any]:
    """When LLM fails, auto-flag high-confidence detector clusters."""
    result["diagnostics"]["status"] = "detector_fallback"
    result["diagnostics"]["fallback_applied"] = 0

    for cluster in clusters:
        confidence = float(cluster.get("confidence", 0.0))
        if confidence <= 0.7:
            continue
        disposition = "legend"  # high-confidence detector = likely legend
        member_ids = [str(mid) for mid in cluster.get("member_ids", ())]
        llm_source = f"llm_{disposition}_fallback"
        for key in member_ids:
            existing = result["flag_map_entries"].get(key, "")
            if "scene_partition" not in existing:
                result["flag_map_entries"][key] = llm_source
        result["decisions"].append({
            "cluster_id": cluster.get("cluster_id", ""),
            "disposition": disposition,
            "confidence": confidence,
            "justification": "Detector confidence fallback (LLM unavailable)",
            "member_count": len(member_ids),
            "detector_source": cluster.get("detector", "legend_detector"),
            "applied": True,
        })
        result["diagnostics"]["fallback_applied"] += 1

    return result
