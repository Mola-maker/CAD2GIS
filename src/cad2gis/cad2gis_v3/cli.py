"""Command line entrypoint for the architecture-v3 experiment pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import ConversionRequest, convert


def main(argv=None):
    parser = argparse.ArgumentParser(description="APD direct-DWG evidence-first CAD2GIS v3")
    parser.add_argument("--input", required=True, type=Path, help="Immutable APD DWG")
    parser.add_argument("--run-dir", required=True, type=Path, help="New run directory")
    parser.add_argument("--source-profile", required=True, type=Path)
    parser.add_argument("--mapping-registry", required=True, type=Path)
    parser.add_argument("--gcp-profile", type=Path)
    args = parser.parse_args(argv)
    result = convert(ConversionRequest(
        source=args.input,
        run_dir=args.run_dir,
        source_profile=args.source_profile,
        mapping_registry=args.mapping_registry,
        gcp_profile=args.gcp_profile,
    ))
    topology_summary = {
        key: value
        for key, value in result.diagnostics["topology"].items()
        if key != "connection_port_candidates"
    }
    print(json.dumps({
        "status": "success",
        "evidence": str(result.evidence_path),
        "delivery": str(result.delivery_path),
        "styles": str(result.style_manifest_path),
        "manifest": str(result.run_manifest_path),
        "counts": result.counts,
        "topology": topology_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
