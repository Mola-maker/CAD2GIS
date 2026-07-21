# CAD2GIS Architecture

## Workspace Layout

The robustness workspace is an independent workspace decoupled from `newmodel`.
It retains only three categories of content:

- **Core algorithm**: `src/cad2gis/` canonical package (pipeline, reader, semantic, topology, calibration, verification, CLI)
- **Architecture knowledge**: `docs/` design decisions, portability guides, reconciliation specs
- **Closed-loop verification**: `verify/` contract tests, portability tests, reconciliation tests, and replay driver

Top-level directories are strictly limited to `src/`, `verify/`, `docs/`, `baselines/`, `tests/`.

## Reader Elevation

The reader role has been redesigned from a Windows-only canonical path to a
cross-platform primary path:

- **LibreDWG** (`src/cad2gis/reader/libredwg.py`) is the primary cross-platform reader.
- **AutoCAD** (`src/cad2gis/reader/autocad.py`) is retained as an opt-in
  Windows-only fallback via `CAD2GIS_READER_BACKEND=autocad`.
- **Contracts** (`src/cad2gis/reader/contracts.py`) define the shared reader protocol.

The `CAD2GIS_READER_BACKEND` environment variable selects the backend
(default: `libredwg`).

## Canonical Boundary

The canonical ingestion boundary is `src/cad2gis/ingest.py`.  It integrates
reader switching and delegates to `src/cad2gis/cad2gis_v3/ingest.py` for the
core census validation.  The legacy `ingest_dev.py` wrapper has been removed;
reader selection is now handled by the canonical `ingest.py` via the
`CAD2GIS_READER_BACKEND` environment variable.

## A-Plan Closed Loop

Closed-loop verification does not require the original DWG:

1. Input: `baselines/apd_hutabohu/records/readcad_review_bundle.json`
2. Adapter: `src/cad2gis/reader/records_adapter.py` materialises bundle facts
   into `SourceEntity` objects
3. Pipeline: `semantic → topology → calibration → output`
4. Reconciliation: SQL count comparison against `baselines/apd_hutabohu/delivery/`

## Baselines

- `baselines/apd_hutabohu/delivery/apd_delivery.gpkg` — delivery baseline
- `baselines/apd_hutabohu/evidence/apd_evidence.gpkg` — evidence baseline
- `baselines/apd_hutabohu/records/readcad_review_bundle.json` — canonical records bundle
- `baselines/apd_hutabohu/config/` — source profile, mapping registry, GCP profile

## Testing Layers

- **Contract layer** (`verify/contract/`): reader behavior, records integrity, env switching
- **Portability layer** (`verify/portability/`): OS detection, ctypes cross-platform loading
- **Reconciliation layer** (`verify/reconciliation/`): records bundle → pipeline → GPKG count
- **Regression layer** (`tests/`): canonical contract tests
