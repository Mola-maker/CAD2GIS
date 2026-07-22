#!/usr/bin/env python3
"""
Legend Detector — F-component: parameterized legend/annotation-block cluster
detection inside CAD Model space
=============================================================================

Model space of APD sheets mixes the geographic network with non-subject
element blocks (legend samples, symbol tables, DESIGN SUMMARY, splicing
diagrams). This module detects such clusters so they can be quarantined
for human review (reason=LEGEND_CANDIDATE) and, once confirmed via an
exclusion config, excluded from the 8-FC delivery.

Algorithm (all knobs parameterized — no hardcoded sheet ratios, no
"legend is always rightmost" assumption; both are documented overfits of
newmodel/autocad_reader.py:551-608):

  1. Robust main-body span estimate per axis: the P10-P90 span of feature
     centroids. Robust to the outlying legend cluster itself as long as it
     holds < ~10% of features (Hutabohu: ~10 legend samples vs ~6900 model
     entities).
  2. X AND Y direction gap clustering: sorted centroid coordinates are
     split wherever a gap exceeds
         threshold = max(gap_min, gap_k * robust_span_axis).
     The most populous segment is the main body; every other segment is an
     outlier group. Groups from both axes that share members are merged
     (a diagonally offset cluster shows up on both axes as one cluster).
  3. Anchor-text fence: a cluster containing (or fenced by, within
     fence = max(fence_min, fence_k * cluster_diagonal)) an anchor term
     (LEGEND / SYMBOL / DESIGN SUMMARY / SPLICING / CABLE TYPE /
     KETERANGAN / FDT / LAYOUT, configurable) gains confidence.
  4. Confidence = w_separation * sep_score + w_anchor * anchor_score,
     where sep_score saturates at 2x the split threshold.
  5. Exclusion list: clusters matching a confirmed entry (bbox or
     member_ids) in the exclusion config are flagged confirmed=True so
     the caller can set disposition=legend.

Default value rationale (override per site/CLI as needed):
  gap_min=100.0        absolute gap floor in input CRS units (metres for
                       this pipeline). Aerial FTTH pole spans run ~30-50 m,
                       so 100 clears the largest legitimate intra-body void
                       by ~2x while any legend offset is typically a large
                       fraction of the sheet span.
  gap_k=0.15           relative gap floor as a fraction of the P10-P90 body
                       span; keeps the threshold meaningful on sheets whose
                       units are not metres.
  min_features=10      below this the gap statistics are meaningless
                       (same guard as newmodel, kept as a parameter).
  min_cluster_size=3   a legend block group has several samples; 1-2
                       isolated features are handled by geometry QC, not F.
  max_cluster_fraction=0.5   structural bound, not a tuned ratio: the body
                       is by construction the most populous segment, so any
                       candidate is already smaller than the body; 0.5 only
                       rejects a mass that would rival the body itself.
                       (An earlier 0.2 guess silently rejected Hutabohu's
                       schematic panels — napf grid / FDT STRUCTURE — which
                       hold ~25% of features but are plainly non-subject.)
  fence_min=10.0, fence_k=0.25   anchor titles sit just outside the sample
                       group's bbox; fence grows with cluster size.
  w_separation=0.6, w_anchor=0.4, min_confidence=0.25
                       a bare threshold-level split scores 0.30, so purely
                       spatial candidates still surface for human review
                       (first-run policy: quarantine, never silent-drop).

Input features: dicts with "centroid": (x, y); optional "id", "text",
"layer". Pure functions — no OGR dependency in the detection path.

CLI:
  python3 legend_detector.py --synthetic
      Synthetic unit test: main body grid + far-offset legend cluster with
      anchor text; checks detection, anchor hit, confidence, exclusions.
  python3 legend_detector.py --gpkg <path.gpkg> [--gap-min N --gap-k F ...]
      Loads centroids/annotation_text from all feature layers of a GPKG
      (majority-CRS group) and prints the candidate cluster report.
"""

import argparse
import json
import os
import re
import sys

# ── Defaults (see module docstring for rationale) ─────────────────────────────

DEFAULT_GAP_MIN = 100.0
DEFAULT_GAP_K = 0.15
DEFAULT_MIN_FEATURES = 10
DEFAULT_MIN_CLUSTER_SIZE = 3
DEFAULT_MAX_CLUSTER_FRACTION = 0.5
DEFAULT_FENCE_MIN = 10.0
DEFAULT_FENCE_K = 0.25
DEFAULT_W_SEPARATION = 0.6
DEFAULT_W_ANCHOR = 0.4
DEFAULT_MIN_CONFIDENCE = 0.25
DEFAULT_EXCLUSION_COVERAGE = 0.9

DEFAULT_ANCHOR_TERMS = (
    "LEGEND", "LEGENDA", "SYMBOL", "SIMBOL", "DESIGN SUMMARY",
    "SPLICING", "CABLE TYPE", "KETERANGAN",
    "FDT", "LAYOUT",
)

DEFAULT_EXCLUSIONS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "legend_exclusions.json")


# ── Small numeric helpers ─────────────────────────────────────────────────────

def _quantile(sorted_values, q):
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _normalize_text(text):
    return re.sub(r"\s+", " ", str(text)).strip().upper()


# ── Axis gap clustering ───────────────────────────────────────────────────────

def _axis_outlier_groups(centroids, axis, gap_min, gap_k):
    """
    Split the sorted axis coordinates at gaps > max(gap_min, gap_k * robust
    span); return (groups, threshold) where each group is
    {"members": set(indices), "separation": distance-to-body-segment}.
    """
    order = sorted(range(len(centroids)), key=lambda i: centroids[i][axis])
    values = [centroids[i][axis] for i in order]
    robust_span = _quantile(values, 0.9) - _quantile(values, 0.1)
    threshold = max(gap_min, gap_k * robust_span)

    segments = [[order[0]]]
    for prev, curr in zip(order, order[1:]):
        if centroids[curr][axis] - centroids[prev][axis] > threshold:
            segments.append([])
        segments[-1].append(curr)

    if len(segments) == 1:
        return [], threshold

    body = max(segments, key=len)
    body_lo = centroids[body[0]][axis]
    body_hi = centroids[body[-1]][axis]

    groups = []
    for segment in segments:
        if segment is body:
            continue
        seg_lo = centroids[segment[0]][axis]
        seg_hi = centroids[segment[-1]][axis]
        separation = body_lo - seg_hi if seg_hi < body_lo else seg_lo - body_hi
        groups.append({"members": set(segment), "separation": max(0.0, separation)})
    return groups, threshold


def _merge_overlapping(groups):
    """Union groups (across axes) that share any member."""
    merged = []
    for group in groups:
        absorbed = dict(group)
        remaining = []
        for existing in merged:
            if existing["members"] & absorbed["members"]:
                absorbed["members"] |= existing["members"]
                absorbed["separation"] = max(absorbed["separation"], existing["separation"])
                absorbed["axes"] = sorted(set(absorbed.get("axes", [])) | set(existing["axes"]))
                absorbed["threshold"] = max(absorbed["threshold"], existing["threshold"])
            else:
                remaining.append(existing)
        merged = remaining + [absorbed]
    return merged


# ── Exclusion config ──────────────────────────────────────────────────────────

def load_exclusions(path=None):
    """
    Load the confirmed-cluster exclusion config. Missing file -> empty.
    Format: {"confirmed_clusters": [{"bbox": [minx,miny,maxx,maxy]} |
                                    {"member_ids": [...]}, ...]}
    """
    path = path or DEFAULT_EXCLUSIONS_PATH
    if not os.path.isfile(path):
        return {"confirmed_clusters": []}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("confirmed_clusters", [])
    return data


def _is_confirmed(cluster, member_centroids, exclusions, coverage):
    for entry in exclusions.get("confirmed_clusters", ()):
        bbox = entry.get("bbox")
        if bbox is not None:
            minx, miny, maxx, maxy = bbox
            inside = sum(1 for x, y in member_centroids
                         if minx <= x <= maxx and miny <= y <= maxy)
            if inside >= coverage * len(member_centroids):
                return True
        ids = entry.get("member_ids")
        if ids:
            id_set = {str(i) for i in ids}
            hits = sum(1 for mid in cluster["member_ids"] if str(mid) in id_set)
            if cluster["member_ids"] and hits >= coverage * len(cluster["member_ids"]):
                return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def detect_legend_clusters(features,
                           gap_min=DEFAULT_GAP_MIN,
                           gap_k=DEFAULT_GAP_K,
                           min_features=DEFAULT_MIN_FEATURES,
                           min_cluster_size=DEFAULT_MIN_CLUSTER_SIZE,
                           max_cluster_fraction=DEFAULT_MAX_CLUSTER_FRACTION,
                           anchor_terms=DEFAULT_ANCHOR_TERMS,
                           fence_min=DEFAULT_FENCE_MIN,
                           fence_k=DEFAULT_FENCE_K,
                           w_separation=DEFAULT_W_SEPARATION,
                           w_anchor=DEFAULT_W_ANCHOR,
                           min_confidence=DEFAULT_MIN_CONFIDENCE,
                           exclusions=None,
                           exclusion_coverage=DEFAULT_EXCLUSION_COVERAGE):
    """
    Detect legend/non-subject clusters among feature centroids.

    Args:
        features: iterable of dicts with "centroid": (x, y); optional
            "id" (any hashable; defaults to the list index), "text", "layer".
        exclusions: dict from load_exclusions(), or None.
        (remaining args: see module docstring for defaults and rationale)

    Returns:
        {"clusters": [ {cluster_id, bbox, member_count, member_ids,
                        anchor_hits, confidence, separation_distance,
                        separation_axes, confirmed}, ... ],   # by confidence desc
         "body_bbox": [...] or None,
         "feature_count": N,
         "params": {...}}
    """
    features = list(features)
    params = {
        "gap_min": gap_min, "gap_k": gap_k, "min_features": min_features,
        "min_cluster_size": min_cluster_size,
        "max_cluster_fraction": max_cluster_fraction,
        "anchor_terms": list(anchor_terms), "fence_min": fence_min,
        "fence_k": fence_k, "w_separation": w_separation,
        "w_anchor": w_anchor, "min_confidence": min_confidence,
    }
    result = {"clusters": [], "body_bbox": None,
              "feature_count": len(features), "params": params}
    if len(features) < min_features:
        return result

    centroids = [tuple(f["centroid"]) for f in features]
    ids = [f.get("id", index) for index, f in enumerate(features)]
    texts = [_normalize_text(f.get("text") or "") for f in features]
    anchors = [_normalize_text(term) for term in anchor_terms]

    raw_groups = []
    for axis in (0, 1):
        groups, threshold = _axis_outlier_groups(centroids, axis, gap_min, gap_k)
        for group in groups:
            group["axes"] = ["x" if axis == 0 else "y"]
            group["threshold"] = threshold
            raw_groups.append(group)
    candidates = _merge_overlapping(raw_groups)

    accepted = [
        group for group in candidates
        if min_cluster_size <= len(group["members"]) <= max_cluster_fraction * len(features)
    ]

    candidate_members = set().union(*(g["members"] for g in accepted)) if accepted else set()
    body_indices = [i for i in range(len(features)) if i not in candidate_members]
    if body_indices:
        result["body_bbox"] = _bbox([centroids[i] for i in body_indices])

    exclusions = exclusions or {"confirmed_clusters": []}
    clusters = []
    for group in accepted:
        members = sorted(group["members"])
        member_centroids = [centroids[i] for i in members]
        bbox = _bbox(member_centroids)
        diagonal = ((bbox[2] - bbox[0]) ** 2 + (bbox[3] - bbox[1]) ** 2) ** 0.5
        fence = max(fence_min, fence_k * diagonal)
        fenced_box = (bbox[0] - fence, bbox[1] - fence, bbox[2] + fence, bbox[3] + fence)

        hit_terms = set()
        for index, (x, y) in enumerate(centroids):
            if not texts[index]:
                continue
            in_members = index in group["members"]
            in_fence = (fenced_box[0] <= x <= fenced_box[2]
                        and fenced_box[1] <= y <= fenced_box[3])
            if not (in_members or in_fence):
                continue
            for term in anchors:
                if term in texts[index]:
                    hit_terms.add(term)
        # Keep only the most specific hit when terms nest (LEGENDA ⊃ LEGEND).
        hit_terms = {term for term in hit_terms
                     if not any(term != other and term in other for other in hit_terms)}

        # sep_score saturates at 2x the split threshold: a gap at exactly the
        # threshold scores 0.5, an unambiguous far offset scores 1.0.
        sep_score = min(1.0, group["separation"] / (2.0 * group["threshold"])) \
            if group["threshold"] > 0 else 0.0
        anchor_score = 1.0 if hit_terms else 0.0
        confidence = w_separation * sep_score + w_anchor * anchor_score
        if confidence < min_confidence:
            continue

        cluster = {
            "bbox": bbox,
            "member_count": len(members),
            "member_ids": [ids[i] for i in members],
            "anchor_hits": sorted(hit_terms),
            "confidence": round(confidence, 4),
            "separation_distance": round(group["separation"], 4),
            "separation_axes": group["axes"],
        }
        cluster["confirmed"] = _is_confirmed(
            cluster, member_centroids, exclusions, exclusion_coverage)
        clusters.append(cluster)

    clusters.sort(key=lambda c: (-c["confidence"], -c["member_count"]))
    for index, cluster in enumerate(clusters, start=1):
        cluster["cluster_id"] = f"LC-{index:03d}"
    result["clusters"] = clusters
    return result


# ── GPKG loading (CLI only) ───────────────────────────────────────────────────

def _load_gpkg_features(gpkg_path):
    """Load centroids + annotation text from all feature layers, grouped by CRS."""
    from osgeo import ogr

    ds = ogr.Open(gpkg_path, 0)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage: {gpkg_path}")
    by_crs = {}
    try:
        for i in range(ds.GetLayerCount()):
            layer = ds.GetLayerByIndex(i)
            if layer.GetGeomType() == ogr.wkbNone:
                continue
            srs = layer.GetSpatialRef()
            crs = srs.GetAuthorityCode(None) if srs is not None else "none"
            defn = layer.GetLayerDefn()
            text_idx = defn.GetFieldIndex("annotation_text")
            code_idx = defn.GetFieldIndex("CODE")
            layer.ResetReading()
            for feat in layer:
                geom = feat.GetGeometryRef()
                if geom is None or geom.IsEmpty():
                    continue
                centroid = geom.Centroid()
                text = feat.GetField(text_idx) if text_idx >= 0 else None
                if not text and code_idx >= 0:
                    text = feat.GetField(code_idx)
                by_crs.setdefault(crs, []).append({
                    "id": f"{layer.GetName()}:{feat.GetFID()}",
                    "centroid": (centroid.GetX(), centroid.GetY()),
                    "layer": layer.GetName(),
                    "text": text,
                })
    finally:
        ds = None
    return by_crs


def _run_gpkg(args):
    by_crs = _load_gpkg_features(args.gpkg)
    if not by_crs:
        print("No spatial features found.")
        return 1
    crs, features = max(by_crs.items(), key=lambda kv: len(kv[1]))
    skipped = {c: len(f) for c, f in by_crs.items() if c != crs}
    print(f"GeoPackage: {args.gpkg}")
    print(f"Detection set: {len(features)} features in majority CRS EPSG:{crs}"
          + (f" (skipped other-CRS features: {skipped})" if skipped else ""))

    exclusions = load_exclusions(args.exclusions)
    result = detect_legend_clusters(
        features, gap_min=args.gap_min, gap_k=args.gap_k,
        min_cluster_size=args.min_cluster_size,
        max_cluster_fraction=args.max_cluster_fraction,
        min_confidence=args.min_confidence, exclusions=exclusions)

    print(f"Body bbox: {result['body_bbox']}")
    print(f"Candidate clusters: {len(result['clusters'])}")
    for cluster in result["clusters"]:
        layers = {}
        for mid in cluster["member_ids"]:
            layers[str(mid).split(":")[0]] = layers.get(str(mid).split(":")[0], 0) + 1
        print(f"  {cluster['cluster_id']}: members={cluster['member_count']} "
              f"confidence={cluster['confidence']} axes={cluster['separation_axes']} "
              f"separation={cluster['separation_distance']} "
              f"confirmed={cluster['confirmed']}")
        print(f"    bbox={[round(v, 1) for v in cluster['bbox']]}")
        print(f"    per-layer={layers} anchor_hits={cluster['anchor_hits']}")
    return 0


# ── Synthetic self-test ───────────────────────────────────────────────────────

def _run_synthetic():
    failures = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    # Main body: 20x10 grid, 100 m pitch -> spans 1900 x 900 m. Internal
    # voids are exactly the 100 m pitch, below the split threshold
    # max(100, 0.15 * ~1520) = ~228 (robust span P10-P90 of X = 0.8*1900).
    body = [{"id": f"body-{i}", "centroid": (500000.0 + (i % 20) * 100.0,
                                             60000.0 + (i // 20) * 100.0)}
            for i in range(200)]
    # Legend cluster: 12 samples 3 km right of the body, with anchor texts.
    legend = [{"id": f"legend-{j}", "centroid": (504900.0 + (j % 2) * 40.0,
                                                 60300.0 + (j // 2) * 30.0)}
              for j in range(12)]
    legend[0]["text"] = "LEGENDA"
    legend[1]["text"] = "CABLE TYPE"

    result = detect_legend_clusters(body + legend)
    clusters = result["clusters"]
    check("exactly one candidate cluster detected", len(clusters) == 1,
          f"found {len(clusters)}")
    if clusters:
        cluster = clusters[0]
        check("cluster captures all 12 legend members",
              cluster["member_count"] == 12
              and all(str(mid).startswith("legend-") for mid in cluster["member_ids"]))
        check("anchor terms detected",
              set(cluster["anchor_hits"]) == {"LEGENDA", "CABLE TYPE"},
              str(cluster["anchor_hits"]))
        check("confidence includes separation + anchor weight",
              cluster["confidence"] > 0.9, f"confidence={cluster['confidence']}")
        check("separated along x", cluster["separation_axes"] == ["x"])
        check("unconfirmed on first run", cluster["confirmed"] is False)

        # Exclusion mechanism: confirmed bbox -> confirmed=True.
        exclusions = {"confirmed_clusters": [{"bbox": cluster["bbox"]}]}
        confirmed = detect_legend_clusters(body + legend, exclusions=exclusions)
        check("bbox exclusion confirms the cluster",
              confirmed["clusters"][0]["confirmed"] is True)
        by_ids = {"confirmed_clusters": [{"member_ids": cluster["member_ids"]}]}
        confirmed_ids = detect_legend_clusters(body + legend, exclusions=by_ids)
        check("member_ids exclusion confirms the cluster",
              confirmed_ids["clusters"][0]["confirmed"] is True)

    # Diagonal offset must be caught too (no rightmost assumption): shift
    # the legend below-left of the body.
    diag_legend = [{"id": f"dl-{j}",
                    "centroid": (497000.0 + (j % 2) * 40.0, 57000.0 + (j // 2) * 30.0)}
                   for j in range(6)]
    diag = detect_legend_clusters(body + diag_legend)
    check("diagonal (left/below) cluster detected without anchors",
          len(diag["clusters"]) == 1
          and diag["clusters"][0]["member_count"] == 6
          and set(diag["clusters"][0]["separation_axes"]) == {"x", "y"},
          str([(c["member_count"], c["separation_axes"]) for c in diag["clusters"]]))

    # Body-only input yields no candidates (no false positives on the grid).
    clean = detect_legend_clusters(body)
    check("no false positives on the uniform body", len(clean["clusters"]) == 0)

    # Tiny inputs are rejected by min_features.
    tiny = detect_legend_clusters(body[:5])
    check("below min_features returns empty", len(tiny["clusters"]) == 0)

    print(f"\nSynthetic self-test: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s))")
    return 0 if not failures else 1


def main():
    parser = argparse.ArgumentParser(
        description="Legend/non-subject cluster detector (component F)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--synthetic", action="store_true",
                       help="Run the synthetic self-test")
    group.add_argument("--gpkg", help="Run detection on a GeoPackage's feature layers")
    parser.add_argument("--gap-min", type=float, default=DEFAULT_GAP_MIN,
                        help=f"Absolute gap floor in CRS units (default {DEFAULT_GAP_MIN})")
    parser.add_argument("--gap-k", type=float, default=DEFAULT_GAP_K,
                        help=f"Relative gap floor as fraction of P10-P90 body span "
                             f"(default {DEFAULT_GAP_K})")
    parser.add_argument("--min-cluster-size", type=int, default=DEFAULT_MIN_CLUSTER_SIZE,
                        help=f"Minimum members per candidate (default {DEFAULT_MIN_CLUSTER_SIZE})")
    parser.add_argument("--max-cluster-fraction", type=float,
                        default=DEFAULT_MAX_CLUSTER_FRACTION,
                        help=f"Maximum candidate size as fraction of all features "
                             f"(default {DEFAULT_MAX_CLUSTER_FRACTION})")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE,
                        help=f"Report threshold (default {DEFAULT_MIN_CONFIDENCE})")
    parser.add_argument("--exclusions", default=None,
                        help=f"Exclusion config JSON (default {DEFAULT_EXCLUSIONS_PATH})")
    args = parser.parse_args()
    if args.synthetic:
        sys.exit(_run_synthetic())
    sys.exit(_run_gpkg(args))


if __name__ == "__main__":
    main()
