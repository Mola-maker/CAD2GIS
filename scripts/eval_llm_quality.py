#!/usr/bin/env python3
"""Evaluate v4-flash model quality on the two LLM integration points.

1. Source-layer extraction (AI onboarding): how well does the model classify
   observed layers/blocks into route_layers / block_families?
2. Noise removal (spatial supervisor): how well does the model classify
   detected spatial clusters into dispositions?

Ground truth is derived from observed DWG content + known project semantics.
Run with: DEEPSEEK_API_KEY set.
"""
import json, os, sys
from pathlib import Path

sys.path.insert(0, "src")

from cad2gis.cad2gis_v3.onboarding import (
    prepare_onboarding_bundle,
    request_onboarding_proposal,
)
from cad2gis.cad2gis_v3.ingest import extract_records
from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.plan_domain import build_plan_domain
from cad2gis.cad2gis_v3.legend_detector import filter_legend_entities
from cad2gis.cad2gis_v3.spatial_llm import classify_spatial_clusters
from cad2gis.cad2gis_v3.curation_providers import load_provider_config

PROJECTS = [
    {
        "name": "Hutabohu",
        "dir": "baselines/hutabohu",
        "dwg": "raw/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg",
        "ground_truth": {
            "route_layer_keywords": ["FO", "CORE", "CABLE"],
            "block_hints": {
                "FAT": "BOITE", "FDT": "SITE", "OLT": "SITE", "POLE": "PTECH",
            },
            "clusters": {
                "technical_diagram": {"layer_keywords": ["FDT STRUCTURE", "FDT-Info"]},
                "legend": {"layer_keywords": ["KETERANGAN", "LEGENDA"]},
            },
        },
    },
    {
        "name": "Lamteh MAIN",
        "dir": "baselines/lamteh_main",
        "dwg": "raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg",
        "ground_truth": {
            "route_layer_keywords": ["FO", "CORE", "CABLE"],
            "block_hints": {
                "FAT": "BOITE", "FDT": "SITE", "OLT": "SITE", "POLE": "PTECH",
            },
            "clusters": {
                "legend": {"layer_keywords": ["LEGEND", "KETERANGAN"]},
                "derived_noise": {"layer_keywords": ["Home Number"]},
            },
        },
    },
    {
        "name": "Lamteh SF",
        "dir": "baselines/lamteh_sf",
        "dwg": "raw/APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg",
        "ground_truth": {
            "route_layer_keywords": ["FO", "CORE", "CABLE"],
            "block_hints": {
                "FAT": "BOITE", "FDT": "SITE", "OLT": "SITE", "POLE": "PTECH",
            },
            "clusters": {
                "legend": {"layer_keywords": ["LEGEND", "KETERANGAN"]},
            },
        },
    },
    {
        "name": "Kletek",
        "dir": "baselines/kletek",
        "dwg": "raw/APD - KLETEK RW 05 SIDOARJO.dwg",
        "ground_truth": {
            "route_layer_keywords": ["FO", "CORE", "CABLE"],
            "block_hints": {
                "FAT": "BOITE", "FDT": "SITE", "OLT": "SITE", "POLE": "PTECH",
            },
            "clusters": {},
        },
    },
]


def eval_onboarding(cfg: dict) -> dict:
    """Score AI onboarding proposal against observed layer/block ground truth."""
    result: dict = {"score": 0.0, "checks": [], "proposal_keys": {}}

    # Prepare bundle from the project's existing inventory
    bundle = prepare_onboarding_bundle(cfg["dir"])
    layers = set(bundle.get("layers", {}))
    blocks = set(bundle.get("named_blocks", {}))

    # Call LLM for the onboarding proposal
    try:
        proposal, provenance = request_onboarding_proposal(cfg["dir"])
    except Exception as exc:
        result["error"] = str(exc)
        return result

    result["model"] = provenance.get("model", "?")
    gt = cfg["ground_truth"]
    checks = result["checks"]

    # 1. route_layers coverage — do observed FO/CORE layers appear?
    route_layers = set(proposal.get("route_layers", []))
    route_regex = proposal.get("positive_route_layer_regex", "")
    observed_route_candidates = [
        layer for layer in layers
        if any(kw.casefold() in layer.casefold() for kw in gt["route_layer_keywords"])
    ]
    covered = [
        layer for layer in observed_route_candidates
        if layer in route_layers or layer.casefold() in route_regex.casefold()
    ]
    if observed_route_candidates:
        route_score = len(covered) / len(observed_route_candidates)
    else:
        route_score = 0.0
    checks.append({
        "dimension": "route_layer_coverage",
        "score": round(route_score, 2),
        "observed": len(observed_route_candidates),
        "covered": len(covered),
        "missed": sorted(set(observed_route_candidates) - set(covered))[:5],
    })

    # 2. block_families — do block name hints map to the right feature class?
    block_families = proposal.get("block_families", {})
    hint_hits = 0
    hint_total = 0
    block_hits_detail = []
    for block_hint, expected_class in gt["block_hints"].items():
        hint_total += 1
        matched = any(
            block_hint.casefold() in name.casefold()
            for name in block_families.get(expected_class, [])
        )
        if matched:
            hint_hits += 1
        block_hits_detail.append({
            "hint": block_hint, "expected": expected_class, "hit": matched,
        })
    block_score = hint_hits / hint_total if hint_total else 0.0
    checks.append({
        "dimension": "block_family_hints",
        "score": round(block_score, 2),
        "detail": block_hits_detail,
    })

    # 3. annotation_families — regexes should match observed text samples
    annotation_families = proposal.get("annotation_families", [])
    text_samples = bundle.get("text_samples_by_layer", {})
    all_samples = [
        item.get("text", "")
        for samples in text_samples.values() if isinstance(samples, list)
        for item in samples if isinstance(item, dict)
    ]
    import re
    family_hits = 0
    family_total = 0
    for family in annotation_families:
        pattern = family.get("text_pattern", "")
        if not pattern:
            continue
        family_total += 1
        try:
            regex = re.compile(pattern)
            matched = any(regex.search(sample) for sample in all_samples if sample)
            if matched:
                family_hits += 1
        except re.error:
            pass
    ann_score = family_hits / family_total if family_total else 0.0
    checks.append({
        "dimension": "annotation_family_match",
        "score": round(ann_score, 2),
        "families": family_total,
        "matched": family_hits,
    })

    # Overall = mean of dimension scores
    dims = [c["score"] for c in checks if "score" in c]
    result["score"] = round(sum(dims) / len(dims), 3) if dims else 0.0
    result["proposal_keys"] = {
        "route_layers": len(route_layers),
        "block_families": {k: len(v) for k, v in block_families.items()},
        "annotation_families": len(annotation_families),
    }
    return result


def eval_spatial(cfg: dict) -> dict:
    """Score spatial supervisor disposition decisions."""
    result: dict = {"score": 0.0, "decisions": [], "checks": []}

    # Re-run the pipeline to get clusters
    records = extract_records(Path(cfg["dwg"]))
    entities = [SourceEntity.from_record(r) for r in records]
    plan = build_plan_domain(entities)
    semantic = list(plan.entities)

    flag_map: dict[str, str] = {}
    for key in plan.catalog_roots:
        flag_map[key] = "scene_partition"
    legend_result = filter_legend_entities(semantic)
    for key in legend_result["legend_flagged_keys"]:
        flag_map[key] = (
            "legend_detector"
            if flag_map.get(key) != "scene_partition"
            else "scene_partition+legend_detector"
        )

    clusters = legend_result["diagnostics"].get("clusters", [])
    if not clusters:
        result["note"] = "no clusters detected (Kletek expected)"
        result["score"] = 1.0 if not cfg["ground_truth"]["clusters"] else 0.0
        return result

    llm_result = classify_spatial_clusters(
        clusters=clusters,
        entities=semantic,
        body_bbox=legend_result["diagnostics"].get("body_bbox"),
        total_entities=len(semantic),
        flag_map=flag_map,
        legend_flagged_keys=legend_result["legend_flagged_keys"],
        project_config_dir=Path(cfg["dir"]) / "config",
        llm_mode="observe",
    )

    decisions = llm_result["decisions"]
    result["decisions"] = [
        {"cluster_id": d["cluster_id"], "disposition": d["disposition"],
         "confidence": d["confidence"]} for d in decisions
    ]

    # Score: expected dispositions by layer keyword in cluster
    gt_clusters = cfg["ground_truth"]["clusters"]
    if not gt_clusters:
        result["score"] = 1.0  # nothing expected → any decision is noise-free
        result["checks"] = [{"dimension": "no_expected_clusters", "score": 1.0}]
        return result

    # For each LLM decision, check if its disposition appears in expected set
    hits = 0
    total = len(decisions)
    detail = []
    for dec in decisions:
        disposition = dec["disposition"]
        expected_match = disposition in gt_clusters
        if expected_match:
            hits += 1
        detail.append({
            "cluster_id": dec["cluster_id"],
            "disposition": disposition,
            "confidence": dec["confidence"],
            "expected": bool(expected_match),
        })
    result["checks"] = [{
        "dimension": "disposition_correctness",
        "score": round(hits / total, 2) if total else 0.0,
        "hits": hits, "total": total,
    }]
    result["score"] = result["checks"][0]["score"]
    return result


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key.strip():
        print("ERROR: export DEEPSEEK_API_KEY")
        sys.exit(1)

    config = load_provider_config()
    print(f"Model: {config.model}")
    print()

    print("=" * 70)
    print("PART 1: SOURCE-LAYER EXTRACTION (AI onboarding)")
    print("=" * 70)
    for cfg in PROJECTS:
        print(f"\n--- {cfg['name']} ---")
        r = eval_onboarding(cfg)
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  Model: {r['model']} | Overall score: {r['score']:.2f}")
        for check in r["checks"]:
            print(f"  [{check['dimension']}] {check.get('score', '?'):.2f} | {json.dumps({k:v for k,v in check.items() if k not in ('dimension','score')}, ensure_ascii=False)[:180]}")
        print(f"  Proposal: {json.dumps(r['proposal_keys'], ensure_ascii=False)}")

    print()
    print("=" * 70)
    print("PART 2: NOISE REMOVAL (spatial supervisor)")
    print("=" * 70)
    for cfg in PROJECTS:
        print(f"\n--- {cfg['name']} ---")
        r = eval_spatial(cfg)
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  Score: {r['score']:.2f}")
        if "note" in r:
            print(f"  Note: {r['note']}")
        for d in r["decisions"]:
            print(f"  {d['cluster_id']}: {d['disposition']} (conf={d['confidence']:.2f})")
        for check in r.get("checks", []):
            print(f"  [{check['dimension']}] {check.get('score','?'):.2f} | {json.dumps({k:v for k,v in check.items() if k not in ('dimension','score')}, ensure_ascii=False)}")

    print()
    print("DONE")


if __name__ == "__main__":
    main()
