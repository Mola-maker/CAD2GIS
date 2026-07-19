#!/usr/bin/env python3
"""
convert_all.py — thin orchestrator chaining the 4-stage CAD2GIS pipeline.

Stages:
  1. ftth_converter  — DWG → GPKG (classification, annotation, writing)
  2. topology_repair  — CABLE chaining, endpoint snapping, FDT domain tagging
  3. style_exporter   — QML sidecar + layer_styles embedding + .qgz project
  4. (optional) evaluator — quality verification

Usage:
  python3 convert_all.py --config config/hutabohu.json \\
      --input "source/project.dwg" --output "output/project.gpkg" \\
      --source-crs EPSG:3857 --target-crs EPSG:3857

Stage-skip flags:
  --skip-extract   Skip ftth_converter (assumes GPKG already exists)
  --skip-topology  Skip topology_repair
  --skip-styles    Skip style_exporter
  --dwgread-cache  Path to dwgread JSON dump for ATTRIB extraction
"""

import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_stage(name, args_list):
    """Run a pipeline stage and exit on failure."""
    print(f"\n{'=' * 60}")
    print(f"Stage: {name}")
    print(f"Command: {' '.join(args_list)}")
    print(f"{'=' * 60}")
    result = subprocess.run(args_list, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"ERROR: {name} failed with exit code {result.returncode}",
              file=sys.stderr)
        sys.exit(result.returncode)
    print(f"{name}: OK")


def main():
    parser = argparse.ArgumentParser(
        description="CAD2GIS 4-stage pipeline orchestrator")
    parser.add_argument("--config", required=True,
                        help="Project JSON config (e.g. config/hutabohu.json)")
    parser.add_argument("--input", "-i", required=True,
                        help="Input DWG file")
    parser.add_argument("--output", "-o", required=True,
                        help="Output GeoPackage path (.gpkg)")
    parser.add_argument("--source-crs", default="EPSG:3857",
                        help="Source DWG CRS (default: EPSG:3857)")
    parser.add_argument("--target-crs", default="EPSG:3857",
                        help="Output CRS (default: EPSG:3857)")
    parser.add_argument("--dwgread-cache",
                        help="Path to dwgread JSON dump for ATTRIB extraction")
    parser.add_argument("--snap-tol", type=float, default=5.0,
                        help="Topology snap tolerance in metres")
    parser.add_argument("--isolation-threshold", type=float, default=30.0,
                        help="Topology isolation threshold in metres")
    parser.add_argument("--enable-gap-bridge", action="store_true",
                        help="Enable constrained gap bridging")
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip ftth_converter stage")
    parser.add_argument("--skip-topology", action="store_true",
                        help="Skip topology_repair stage")
    parser.add_argument("--skip-styles", action="store_true",
                        help="Skip style_exporter stage")
    args = parser.parse_args()

    # Ensure config path is absolute or relative to SCRIPT_DIR
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(SCRIPT_DIR, config_path)

    # Stage 1: ftth_converter
    if not args.skip_extract:
        stage1 = [
            sys.executable, os.path.join(SCRIPT_DIR, "ftth_converter.py"),
            "--input", args.input,
            "--config", config_path,
            "--output", args.output,
            "--source-crs", args.source_crs,
            "--target-crs", args.target_crs,
        ]
        if args.dwgread_cache:
            stage1.extend(["--dwgread-cache", args.dwgread_cache])
        _run_stage("ftth_converter", stage1)
    else:
        print("Skipping ftth_converter (--skip-extract)")

    # Stage 2: topology_repair
    if not args.skip_topology:
        stage2 = [
            sys.executable, os.path.join(SCRIPT_DIR, "topology_repair.py"),
            "--gpkg", args.output,
            "--snap-tol", str(args.snap_tol),
            "--isolation-threshold", str(args.isolation_threshold),
        ]
        if args.enable_gap_bridge:
            stage2.append("--enable-gap-bridge")
        _run_stage("topology_repair", stage2)
    else:
        print("Skipping topology_repair (--skip-topology)")

    # Stage 3: style_exporter
    if not args.skip_styles:
        stage3 = [
            sys.executable, os.path.join(SCRIPT_DIR, "style_exporter.py"),
            "--gpkg", args.output,
        ]
        _run_stage("style_exporter", stage3)
    else:
        print("Skipping style_exporter (--skip-styles)")

    print(f"\n{'=' * 60}")
    print(f"Pipeline complete: {args.output}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
