#!/usr/bin/env python3
"""Per-project DWG-to-GIS orchestration with layout analysis."""
import argparse, json, os, subprocess, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

def _resolve_path(path):
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)

def stage_layout_analysis(project, dwg_paths):
    from layout_miner import mine_dwg
    report = {"project": project, "drawings": {}, "summary": {}}
    for dwg_path in dwg_paths:
        dwg_name = os.path.basename(dwg_path)
        result = mine_dwg(dwg_path)
        model = [l for l in result["layouts"] if l["role"] == "model"]
        report["drawings"][dwg_name] = {
            "file": dwg_name, "layouts": result["layouts"],
            "mined_counts": result["mined_counts"],
            "geometry_source": "MODEL" if model else "PAPER_SPACE",
        }
    all_model = all(d["geometry_source"] == "MODEL" for d in report["drawings"].values())
    report["summary"] = {"geometry_source": "MODEL" if all_model else "MIXED"}
    os.makedirs(os.path.join(OUTPUT_DIR, project), exist_ok=True)
    rp = os.path.join(OUTPUT_DIR, project, f"{project}_layout_analysis.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"Layout analysis: {rp}")
    return report

def stage_convert(project, dwg_paths, config_path, args):
    out_dir = os.path.join(OUTPUT_DIR, project)
    os.makedirs(out_dir, exist_ok=True)
    gpkg_path = os.path.join(out_dir, f"{project}.gpkg")
    r = subprocess.run([sys.executable, "-m", "python.ftth_converter",
        "--input"] + dwg_paths + ["--output", gpkg_path, "--config", config_path,
        "--source-crs", args.source_crs, "--target-crs", args.target_crs],
        cwd=PROJECT_ROOT)
    if r.returncode != 0: sys.exit(r.returncode)
    r = subprocess.run([sys.executable, "-m", "python.style_exporter",
        "--gpkg", gpkg_path], cwd=PROJECT_ROOT)
    if r.returncode != 0: sys.exit(r.returncode)
    return gpkg_path

def main():
    p = argparse.ArgumentParser(description="Per-project CAD2GIS pipeline")
    p.add_argument("--project", required=True)
    p.add_argument("--input", "-i", nargs="+", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--source-crs", default="EPSG:3857")
    p.add_argument("--target-crs", default="EPSG:3857")
    p.add_argument("--skip-convert", action="store_true")
    p.add_argument("--skip-analysis", action="store_true")
    args = p.parse_args()
    dwg_paths = [_resolve_path(p) for p in args.input]
    config_path = args.config or os.path.join(CONFIG_DIR, f"{args.project}.json")
    if not os.path.isabs(config_path): config_path = os.path.join(PROJECT_ROOT, config_path)
    for dp in dwg_paths:
        if not os.path.isfile(dp): sys.exit(f"ERROR: Input not found: {dp}")
    if not os.path.isfile(config_path): sys.exit(f"ERROR: Config not found: {config_path}")
    if not args.skip_analysis: stage_layout_analysis(args.project, dwg_paths)
    if not args.skip_convert: gpkg = stage_convert(args.project, dwg_paths, config_path, args); print(f"Done: {gpkg}")

if __name__ == "__main__": main()
