# CAD2GIS Architecture

The geometry-first scene partition, coordinate-domain admission gate,
paired-GCP registration, and two-pane review workspace are specified in
[REGISTRATION_AND_SCENE_ARCHITECTURE.md](REGISTRATION_AND_SCENE_ARCHITECTURE.md).

## Boundaries

`src/cad2gis/` is the only production implementation. The CLI, Python API,
MCP tools, and review server delegate to this package and do not fork
conversion logic.

The data plane is deterministic:

1. A selected DWG reader emits immutable CAD records.
2. A plan-domain stage separates drawing WCS from block-definition-local
   coordinates and materializes exact nested INSERT instances.
3. For a new source, AI onboarding selects only observed semantic identifiers
   and a deterministic CAD-metadata CRS/unit candidate. A deterministic dry
   run compiles source-bound profile/registry contracts and exact gates.
4. A source profile and mapping registry bind all rules to the source SHA-256.
5. Classification preserves unresolved and unsupported evidence.
6. Native curve facts, geometry, topology, and cable measurements are
   validated independently.
7. Nominal CRS transformation runs separately from optional GCP calibration.
8. Delivery and evidence GeoPackages, QML, manifests, and status are published
   atomically.

The model-assisted control plane can propose a hash-bound Decision Pack over
registered evidence IDs. It cannot author coordinates, geometry, lengths, CRS,
or GCPs. Only registered deterministic executors can materialize a validated
operation. See [LLM_AGENT_ARCHITECTURE.md](LLM_AGENT_ARCHITECTURE.md).

## Reader Contract

- `reader/libredwg.py`: cross-platform default when its runtime is available.
- `reader/autocad.py`: maintained Windows adapter selected with
  `CAD2GIS_READER_BACKEND=autocad`.
- `reader/contracts.py`: shared inventory and typed reader errors.
- `reader/records_adapter.py`: replay of a source-bound immutable record bundle.

A reader succeeds only when inventory is complete, zero rows were silently
skipped, every record is bound to the source hash, and required native facts
are retained. Capability absence and extraction failure are distinct typed
states.

The AutoCAD bulk protocol publishes a versioned completion marker only after
the export is flushed and closed. Marker row count, TSV row count and parsed
row count must agree exactly. Process shutdown is monitored separately so a
Core Console hang after a valid export cannot discard the inventory.

`$INSUNITS` is retained as block-insertion scale evidence, not automatically
treated as the WCS coordinate unit. When authoritative DWG `CGEOCS` metadata
identifies a projected CRS, its horizontal axis unit controls the WCS-to-metre
scale. The manifest records both facts and the resulting source-to-axis factor.

## Plan-Domain Contract

`cad2gis_v3/plan_domain.py` creates a derived semantic view without modifying
the reader inventory:

- Model drawing WCS is preferred; plan layouts are considered only when Model
  is absent.
- A role fallback is explicit in diagnostics and requires complete block
  expansion.
- Nested INSERTs use reader-supplied insertion point, block base, scale,
  rotation, normal and extrusion facts through the shared transform port.
- Every derived leaf has a content-addressed ID, root/definition/instance-path
  lineage and the exact affine matrix.
- Missing facts/definitions, cycles, oblique transforms and unsupported
  non-uniform curved transforms fail closed.

This stage is source agnostic: it contains no project filename, vendor layer,
block-name, coordinate or expected-count rule. Semantic mapping remains a
separate source-bound contract.

## Test Layers

All executable tests live under `tests/`:

- focused unit and stage-contract tests;
- canonical CLI/package tests;
- APD records and delivery baseline reconciliation;
- external real-DWG compatibility tests governed by
  `tests/data/apd_test_manifest.json`.

The external `APD_test` corpus is compatibility evidence, not domain or
absolute-accuracy truth. Full extraction of complex DWGs is an explicit
performance gate.

## Repository Layout

- `src/`: production package
- `tests/`: all automated tests
- `baselines/`: immutable regression evidence
- `experiment/`: APD source/config compatibility project pack only
- `docs/`: maintained architecture and operator documentation
- `plugins/`: MCP/plugin integration
- `env/`: pinned runtime
- `official/`: unreviewed real-input inventory

Historical OMC sessions, duplicate test assets, old verification trees, Python
bytecode copies, build caches, and generated runs do not belong in the source
tree.

## Accuracy Claims

The following must be reported separately:

- source-record fidelity;
- geometry/curve fidelity;
- topology and segment conservation;
- nominal CRS transformation;
- GCP residuals and independent check-point accuracy.

Without authoritative controls and independent checks, absolute accuracy is
`not_verified`, even if an OSM overlay looks plausible.
