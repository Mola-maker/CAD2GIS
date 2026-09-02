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

Instrumented boundaries emit `cad2gis.stage_contract.v2` receipts. The full
output-state fingerprint is separate from the counts/summary fingerprint;
topology includes its in-place feature changes. Context binds the conversion
snapshot (implementation, runtime, source, profiles, GCP and decision pack),
reader selection and modes. Delivery fingerprints are taken after embedded
QGIS styles, and include external style-file hashes. Hashing time is recorded
separately from operation time; these sums are not total conversion wall time.

There is **no result-cache reuse**: every receipt declares `cacheable: false`
and `cache_status: disabled_receipt_only`. The reserved `cache_key` alone must
never authorize reuse. A future cache still needs complete external-input and
side-effect contracts, replay validation and deterministic-output tests.
`deterministic: false` means no determinism guarantee has been asserted for
that receipt; it does not change the conversion algorithm.

Source admission, reviewed spatial filtering, styled delivery, layer schema
creation and segment/asset row writing have explicit helper boundaries.
Classification and topology still need further decomposition; this work does
not change their matching or denoising policy. Before a refactor is accepted,
`tools/verify_delivery_equivalence.py BASELINE.gpkg CANDIDATE.gpkg` compares
feature schemas and exact rows (including binary geometries, labels, lengths
and lineage). Equivalence is a regression check, not surveyed accuracy proof.

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

- Model drawing WCS is preferred. Named paper-space layouts remain evidence
  unless a reviewed, source-bound profile lists them in `plan_layouts`; an
  explicit layout may be admitted alongside Model without rewriting every
  paper tab globally.
- Reader scene heuristics retain their original CAD role. A reviewed route
  layer may restore that source role without admitting nearby legend or title
  geometry.
- Unreachable block-definition trees are inventoried as orphan evidence.
  Recovery requires an explicit `plan_domain.include_orphan_blocks` name,
  reviewed-profile authority, a finite block base point, and a complete nested
  transform chain; wildcard or partial recovery is rejected.
- A role fallback is explicit in diagnostics and requires complete block
  expansion.
- Nested INSERTs use reader-supplied insertion point, block base, scale,
  rotation, normal and extrusion facts through the shared transform port.
- Every derived leaf has a content-addressed ID, root/definition/instance-path
  lineage and the exact affine matrix.
- Missing facts/definitions, cycles, oblique transforms and unsupported
  non-uniform curved transforms fail closed.

This stage contains no project filename, vendor layer, block-name, coordinate
or expected-count rule. It accepts only compiled declarations and route-layer
patterns from the reviewed source-bound contract; semantic mapping remains a
separate stage.

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

## Performance and Evidence Storage

Topology nearest-neighbour hotspots use Shapely 2 `STRtree` with bounded
queries. Final distances, the 1 cm ambiguity rule and abstention decisions are
still evaluated by the legacy scalar contract; randomized equivalence tests
compare both implementations.

The canonical evidence graph is stored as deterministic
`reasoning/evidence_graph.json.gz`; the content-addressed SQLite index remains
the normal paged query interface. Repeated `raw_properties` in auxiliary
block/annotation tables are SHA-256 references to the canonical `cad_entities`
fact, while the authoritative fact remains queryable for curation and audit.

## OSM Review Boundary

OSM is an optional review aid, never coordinate authority. Place lookup may
produce a coarse translation candidate. When road data is available, the
review pack crops implausible lengths and ranks Top-K roads using direction,
length, normalized shape, endpoint topology and coverage. Low score or a
small best/second-best gap produces `abstained`. Even a high-scoring result is
`relative_only`, `applicable_for_delivery=false`, and requires surveyed GCP or
explicit human review. DWG GEODATA always takes precedence.

**Terminology**: `APD` means **As Plan Drawing** (as-planned construction
design, not as-built), and the filename suffix `SF` means **Subfeeder**.
The six DWGs added under `raw/` after the four development baselines are a
held-out validation set and must receive fresh source-bound profiles.  See
[GLOSSARY.md](GLOSSARY.md).

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
