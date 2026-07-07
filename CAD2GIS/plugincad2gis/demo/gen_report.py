"""Generate the web-demo data file (demo/data/report.js) from a pipeline run + evidence package.

The demo is a standalone static page (opens via file://), so we bake the report into a JS global
rather than fetch JSON (which file:// blocks). Run after a pipeline run to refresh the demo.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cad2gis.pipeline import run  # noqa: E402
from cad2gis.feature_context import extract_insert_contexts  # noqa: E402
from cad2gis.mapping import MappingEngine  # noqa: E402
from cad2gis.evidence import collect_unmapped_evidence  # noqa: E402

DXF = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "build", "normalized", "DS-04_comms.dxf"))
BENCH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "cad2gis", "verify", "benchmark", "ds04_surveyed.json"))
OUT = os.path.join(os.path.dirname(__file__), "data", "report.js")


def main():
    coll, rep = run(DXF, benchmark=BENCH, georeference=True)

    # evidence package (top unmapped groups) for the "honest remainder" section
    eng = MappingEngine.from_yaml()
    ctxs = extract_insert_contexts(DXF)
    evrows = collect_unmapped_evidence(ctxs, eng)

    raw_by_type = rep.parse.get("by_type", {})
    raw_breakdown = sorted(raw_by_type.items(), key=lambda kv: -kv[1])[:6]

    data = {
        "source": os.path.basename(DXF),
        "track": "Sub-track 2 · 多源异构工程数据融合",
        "source_entities": rep.parse.get("entities_seen"),
        "total_features": sum(v for k, v in rep.counts_final.items() if k != "__unmapped__"),
        "counts": {k: v for k, v in rep.counts_final.items() if k != "__unmapped__"},
        "raw_breakdown": raw_breakdown,
        "accuracy": rep.accuracy,
        "network": rep.network,
        "georef": rep.georef,
        "refine": rep.refine,
        "attributes_added": rep.attributes_added,
        "benchmark_note": "Ground truth: manhole count anchored to 259 surveyed X=/Y= node labels "
                          "(an independent entity type). Per-feature correctness measured via cross-source "
                          "signals INDEPENDENT of the classifier's rule path (manhole↔surveyed-label match, "
                          "cable↔topological anchoring, duct↔geometry fingerprint, annotation↔text). "
                          "Positional from GCP RMSE. Dimensions without an independent source are shown "
                          "but marked not-scored — never faked.",
        "per_feature": rep.per_feature,
        "evidence": [
            {"block": e["block"], "count": e["count"], "reason": e["reason"],
             "nearest_text_top": e.get("nearest_text_top", [])}
            for e in evrows[:8]
        ],
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.REPORT = ")
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write(";\n")
    print("wrote", OUT)
    print("overall accuracy:", rep.accuracy["overall"] if rep.accuracy else None)


if __name__ == "__main__":
    main()
