# Cad2GIS Architecture

This document is the operator and extension handoff for the deterministic Cad2GIS system.

## Invariants

- `pipeline.run()` is the canonical conversion path. CLI, QGIS, and demo surfaces should call it rather than duplicate conversion logic.
- Runtime conversion is deterministic. AI may propose mappings or review evidence offline, but no LLM call belongs in the conversion route.
- Every emitted feature must preserve `SourceRef` provenance: source file, layer, block, handle, and entity type when available.
- Semantic coverage and per-feature correctness are separate. A classifier output is never allowed to verify itself.
- Optional stages may fail softly and report errors in `RunReport`; they must not corrupt the converted feature collection.

## Data Flow

```text
DWG/DXF
  -> ingest/profile
  -> parse FeatureCollection
  -> scope + classify with FeatureContext hit vectors
  -> topology clean + refinement + graph label propagation
  -> attribute enrichment
  -> network QC
  -> GCP georeference
  -> per-feature verification
  -> optional benchmark scoring
  -> optional GeoPackage warehouse
```

## Stable Contracts

- `src/cad2gis/model.py`
  - `SourceRef` is the provenance contract.
  - `Feature` is the per-object contract used by all stages.
  - `FeatureCollection` is the stage-to-stage container.
- `src/cad2gis/feature_context.py`
  - `FeatureContext` is the hit-vector contract: layer, block, entity type, nearest text, ATTRIB text, fingerprint, spatial context.
- `src/cad2gis/mapping/engine.py`
  - `classify_context()` is authoritative for opaque INSERT codes when context exists.
- `src/cad2gis/mapping/block_codes.yaml`
  - Reviewed block-code evidence is frozen runtime input. Edit only through offline review.
- `src/cad2gis/pipeline.py`
  - `RunReport` is the report contract.
  - `on_stage(name, detail)` is the progress callback contract.
- `src/cad2gis/warehouse/schema.py`
  - Published GeoPackage schema and required-field rules.
- `src/cad2gis/verify/per_feature.py`
  - Independent per-feature verification contract.

## Failure Semantics

- Parse/classify/topology failures are core failures and should surface to the caller.
- Georeference, per-feature verification, and warehousing are best-effort in `pipeline.run()` today; failures are captured in the report.
- A soft failure must never inflate scores. Missing independent evidence means unverified, not verified.

## Surface Parity Targets

The CLI currently exposes less than `pipeline.run()`. The target parity matrix is:

- `cad2gis convert`: `--benchmark`, `--warehouse`, `--no-georef`, `--no-refine`, repeated `--scope-layer`, `--report`.
- `cad2gis verify`: read a conversion report/GeoPackage plus benchmark and emit the six-dimension score.
- QGIS plugin: call the same conversion path and expose report, warehouse path, benchmark path, and debug artifact directory.
- Demo server: call the same conversion path and stream `on_stage` events only.

## Debug Runbooks

Common investigations should start from source provenance:

- Classification: inspect `SourceRef`, `_map_evidence`, nearest text, ATTRIB text, and block fingerprint.
- Gated/rejected blocks: compare the feature's `_map_evidence.decision` with `block_codes.yaml`.
- Propagated ducts: inspect `resolved_by=topology_propagation`, nearby anchor geometry, and the original gate failure.
- GCP issues: compare raw label GCPs, refined node-paired GCPs, RMSE, and outliers.
- Surface drift: compare CLI/QGIS/demo outputs through `RunReport.counts_final`, `network`, `georef`, `accuracy`, and `per_feature`.

The remaining duct-verification workflow is documented in `docs/duct_verification_runbook.md`.

