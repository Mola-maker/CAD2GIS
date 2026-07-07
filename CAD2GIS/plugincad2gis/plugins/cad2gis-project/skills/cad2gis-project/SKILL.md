---
name: cad2gis-project
description: Use when working in the Cad2GIS repo, including CAD/DWG/DXF parsing, GIS/QGIS/GeoPackage output, accuracy verification, tests, project docs, or the demo server.
---

# Cad2GIS Project

Use this skill for work inside the Cad2GIS local project.

## Project Rules

- Runtime conversion must stay deterministic and reproducible.
- LLMs may assist offline with review, mapping proposals, docs, and analysis, but must not be introduced into the production conversion path.
- The canonical Python package is under `src/cad2gis`.
- The canonical CLI entrypoint is `cad2gis` from `src/cad2gis/cli.py`.
- The QGIS plugin under `qgis_plugin/` should call the same pipeline code paths, not fork conversion logic.
- Heavy GIS dependencies belong in the pinned conda environment at `env/environment.yml`.
- System Python 3.14 is not the target runtime for the GDAL/QGIS stack.

## Architecture Map

Pipeline stages:

```text
Ingest -> Profile -> Parse -> Classify -> Topology/Refine -> Network -> Georeference -> Warehouse -> Accuracy
```

Key files:

- `src/cad2gis/ingest.py`: DWG to DXF normalization and input handling.
- `src/cad2gis/profile.py`: DXF profiling and coordinate-range classification.
- `src/cad2gis/parse.py`: ezdxf entities to typed feature candidates.
- `src/cad2gis/feature_context.py`: hit-vector extraction with nearest text and geometry fingerprints.
- `src/cad2gis/mapping/engine.py`: deterministic rule classification.
- `src/cad2gis/refine.py`: route snapping, noise handling, label propagation.
- `src/cad2gis/topology.py`: geometry cleanup.
- `src/cad2gis/network.py`: graph and junction synthesis.
- `src/cad2gis/gcp.py`: GCP extraction and transform fitting.
- `src/cad2gis/warehouse/geopackage.py`: GeoPackage writer.
- `src/cad2gis/verify/protocol.py`: six-dimension accuracy scoring.
- `src/cad2gis/verify/per_feature.py`: independent per-feature verification.
- `src/cad2gis/pipeline.py`: canonical orchestrator.

## Standard Commands

Use the conda environment when available:

```bash
conda activate cad2gis
pip install -e .
cad2gis doctor
python -m pytest tests/ -q
```

Run the documented DS-04 pipeline:

```bash
python -c "from cad2gis.pipeline import run; c,r=run('build/normalized/DS-04_comms.dxf', benchmark='src/cad2gis/verify/benchmark/ds04_surveyed.json', warehouse='build/DS04_comms_full.gpkg'); print(r.accuracy['overall'])"
```

Run the interactive demo server:

```bash
python -m uvicorn demo.server.app:app --port 8000
```

Regenerate the static demo:

```bash
python demo/gen_report.py
```

## Testing Guidance

- For parser or classifier changes, add focused tests under `tests/` and run the matching file first.
- For topology, network, GCP, warehouse, or scoring changes, run the targeted test file and then `python -m pytest tests/ -q`.
- For mapping changes, preserve abstention/evidence behavior and avoid accuracy inflation from circular signals.
- Before claiming completion, report the exact verification command and observed result.

## Documentation Anchors

- `README.md`: project overview, accuracy summary, commands, deliverables.
- `docs/technical_plan.md`: full design.
- `docs/verification_report.md`: audited methodology and results.
- `src/cad2gis/mapping/block_codes.yaml`: frozen reviewed opaque block mappings.
- `src/cad2gis/mapping/comms_symbols.yaml`: deterministic comms symbol rules.

