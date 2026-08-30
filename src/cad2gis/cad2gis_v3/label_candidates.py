"""Deterministic label-attachment candidates for point features.

Delivery point features (poles, closures, sites) often carry no label while
the drawing still holds the matching annotation as a separate text entity
that was abstained as an unreviewed annotation carrier.  This module pairs
point features with nearby text entities using pure geometry:

* the distance reference is the percentile-trimmed coordinate span
  (``_robust_coordinate_span``), so paper-space outliers cannot inflate the
  search radius;
* every point feature reports up to ``_MAX_OPTIONS`` (six) nearest text entities
  within ``eps`` as *ranked options* — nearest-first ordering is geometry
  fact, but which option carries the instance identity is a semantic
  judgement left to the reviewer/AI (a closer text may be a generic marker
  while the real ID sits slightly farther away);
* a text entity that already became a feature itself (e.g. a homepass label
  delivered as an IMB point) is reported only as the ``self_text`` option of
  its own feature — it never competes for other features;
* each option records whether the text's nearest point feature is this
  feature (``feature_is_text_nearest``), so 1:1 assignment conflicts remain
  auditable.

The result is advisory: candidates change no facts.  Selection happens only
through the reviewed decision protocol (for example the registered
``attach_existing_label`` operation).  The rules contain no project names,
layer names, or hard-coded counts.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from typing import Any

from .model import Feature, SourceEntity
from .scene_partition import _robust_coordinate_span


LABEL_CANDIDATES_SCHEMA_VERSION = "cad2gis-label-candidates-v2"
_LABEL_TEXT_TYPES = frozenset({"TEXT", "MTEXT", "ATTRIB"})
_EPS_SPAN_FRACTION = 0.015
_MAX_OPTIONS = 6


def _centroid_xy(value: Any) -> tuple[float, float]:
    centroid = getattr(value, "centroid", None) or getattr(value, "native_centroid")
    return (float(centroid[0]), float(centroid[1]))


def generate_label_candidates(
    features: Iterable[Feature],
    entities: Iterable[SourceEntity],
) -> dict[str, Any]:
    """Return ranked label candidates for point features.

    Options are pure distance facts; the reviewer decides which option (if
    any) carries the instance identity.  Only point features with at least
    one option produce a candidate record; ``mutual_pairs`` keeps the
    high-precision mutual-nearest subset for consumers that only want
    unambiguous pairs.
    """

    feature_values = tuple(features)
    entity_pool = tuple(entities)
    span = _robust_coordinate_span(entity_pool)
    eps = span * _EPS_SPAN_FRACTION

    point_features = tuple(
        feature for feature in feature_values
        if str(feature.geometry_kind) == "Point"
    )
    self_text_of = {
        str(feature.source_entity_key): feature.feature_key
        for feature in feature_values
    }
    def label_carrier(entity: SourceEntity) -> bool:
        if entity.dwg_type.upper() not in _LABEL_TEXT_TYPES or not entity.text.strip():
            return False
        if entity.dwg_type.upper() == "ATTRIB":
            # Block attributes carry per-instance values; always eligible.
            return True
        # Static TEXT/MTEXT inside block definitions (and their derived
        # plan-domain instances) are symbol graphics, not instance
        # annotation: a pole glyph's "FM" caption would otherwise sit at
        # distance zero on every pole and win every nearest-text test.
        if str(entity.entity_key).startswith("plan:"):
            return False
        if str(entity.layout).upper().startswith("BLOCKDEF"):
            return False
        return True

    free_texts = tuple(
        entity for entity in entity_pool
        if label_carrier(entity)
        and entity.entity_key not in self_text_of
    )

    # Nearest point feature per free text (for the auditable 1:1 flag).
    text_nearest_feature: dict[str, str] = {}
    for entity in free_texts:
        ex, ey = _centroid_xy(entity)
        best_key = ""
        best_distance = math.inf
        for feature in point_features:
            fx, fy = _centroid_xy(feature)
            distance = math.hypot(fx - ex, fy - ey)
            if distance < best_distance:
                best_distance = distance
                best_key = feature.feature_key
        text_nearest_feature[entity.entity_key] = best_key

    candidates: list[dict[str, Any]] = []
    mutual_keys: set[tuple[str, str]] = set()
    for feature in point_features:
        fx, fy = _centroid_xy(feature)
        ranked: list[tuple[SourceEntity, float]] = []
        for entity in free_texts:
            ex, ey = _centroid_xy(entity)
            distance = math.hypot(fx - ex, fy - ey)
            if distance <= eps:
                ranked.append((entity, distance))
        ranked.sort(key=lambda item: (item[1], item[0].entity_key))

        options: list[dict[str, Any]] = []
        self_key = str(feature.source_entity_key)
        self_entity = next(
            (entity for entity in entity_pool if entity.entity_key == self_key),
            None,
        )
        if (
            self_entity is not None
            and self_entity.dwg_type.upper() in _LABEL_TEXT_TYPES
            and self_entity.text.strip()
        ):
            options.append({
                "rank": 0,
                "text_entity_key": self_entity.entity_key,
                "text": self_entity.text.strip(),
                "text_layer": self_entity.layer,
                "distance": 0.0,
                "self_text": True,
                "feature_is_text_nearest": True,
                "mutual_nearest": True,
            })
            mutual_keys.add((feature.feature_key, self_entity.entity_key))
        for free_rank, (entity, distance) in enumerate(ranked[:_MAX_OPTIONS]):
            mutual = (
                free_rank == 0
                and text_nearest_feature.get(entity.entity_key)
                == feature.feature_key
            )
            options.append({
                "rank": len(options),
                "text_entity_key": entity.entity_key,
                "text": entity.text.strip(),
                "text_layer": entity.layer,
                "distance": distance,
                "self_text": False,
                "feature_is_text_nearest": (
                    text_nearest_feature.get(entity.entity_key)
                    == feature.feature_key
                ),
                "mutual_nearest": mutual,
            })
            if mutual:
                mutual_keys.add((feature.feature_key, entity.entity_key))

        if not options:
            continue
        candidates.append({
            "candidate_id": hashlib.sha256(
                feature.feature_key.encode("utf-8")
            ).hexdigest()[:16],
            "feature_key": feature.feature_key,
            "feature_class": feature.feature_class,
            "feature_entity_key": str(feature.source_entity_key),
            "options": options,
        })

    candidates.sort(
        key=lambda item: (item["feature_class"], item["feature_key"]),
    )
    return {
        "schema_version": LABEL_CANDIDATES_SCHEMA_VERSION,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "mutual_pairs": [
            {"feature_key": feature_key, "text_entity_key": text_key}
            for feature_key, text_key in sorted(mutual_keys)
        ],
        "stats": {
            "eps": eps,
            "coordinate_span": span,
            "point_feature_count": len(point_features),
            "text_entity_count": len(free_texts),
            "max_options": _MAX_OPTIONS,
        },
    }


__all__ = [
    "LABEL_CANDIDATES_SCHEMA_VERSION",
    "generate_label_candidates",
]
