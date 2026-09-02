# CAD2GIS LLM Agent Architecture

## Authority model

The LLM is the workflow planner and semantic reasoner. Reader facts, CAD curve
parameters, geometry calculations, topology, lengths, coordinate operations,
and GCP residuals remain under deterministic implementations.

The architecture is split into two planes:

1. The control plane reads an Evidence Graph and emits a frozen Decision Pack.
2. The data plane simulates registered operations, runs independent validators,
   and applies only auto-accepted results through the canonical pipeline.

The same source, profiles, code fingerprint, runtime fingerprint, and Decision
Pack must be replayable without contacting a model provider.

## Implemented contracts

### Source-bound automatic onboarding

`cad2gis.ai_onboarding_bundle.v1` is the control-plane input for an unseen
drawing. It contains only the immutable source inventory, observed layer/block
identifiers, bounded text samples, deterministic role suggestions, and CRS/unit
candidates derived from exact DWG metadata. The model returns
`cad2gis.ai_onboarding_proposal.v1`; it may select identifiers but cannot author
coordinates, geometry, lengths, GCPs, expected counts, CRS identifiers, or
regular expressions.

The deterministic compiler validates source, inventory, and bundle hashes,
rejects invented identifiers and ambiguous INSERT-layer assignments, compiles
exact anchored mapping rules, then rereads the drawing. A semantic dry run
derives the source census and expected feature counts. Admission is
transactional: success gives both contracts the same `auto_accepted` provenance
record; any error restores the original draft files.

Candidate compilation keeps `$INSUNITS` (block insertion scaling) separate from
the projected CRS axis unit used by WCS coordinates. The model cannot choose a
numeric scale; the deterministic candidate records both metadata facts and the
derived source-to-axis factor.

Anonymous dynamic blocks can be classified through their observed INSERT layer
when the block name itself is not stable. Unmapped entities use an explicit
abstention policy and remain visible in evidence.

### Plan-domain instance view

`cad2gis-plan-domain-v1` runs before semantic reasoning. It preserves the raw
inventory and deterministically materializes drawing-space instances from
nested block definitions using complete INSERT transform facts. Derived
entities carry content-addressed root/definition/instance-path lineage.
Missing or inexact transforms fail closed; neither the LLM nor VLM can propose
coordinates for this stage.

### Evidence Graph

`cad2gis.evidence_graph.v1` binds every node and edge to one source SHA-256.
Nodes are content-addressed and contain immutable reader or pipeline facts.
Edges reference only existing nodes. The graph is emitted as
`reasoning/evidence_graph.json.gz` (transparent deterministic gzip) and recorded
in `run_manifest.json`. The adjacent SQLite evidence index remains the default
query surface, so agents do not need to inflate the full graph for paged reads.

### Visual evidence

`cad2gis.visual_evidence.v1` emits one overview and four overlapping detail
renders, plus entity-ID hit maps and RGB-to-node indexes. Only the resolved
plan-domain entities are rendered, so block-definition-local geometry, title
blocks and paper-space legends do not become semantic evidence. Visual regions
are content-addressed graph nodes and are explicitly secondary evidence;
pixels cannot supply coordinates or lengths.

### Repair operation

`cad2gis.repair_operation.v1` permits only registry entries. Operation
parameters are IDs of registered policies, candidates, or deterministic tools;
numeric measurements and arbitrary keys are rejected. Every operation records
model confidence, agreement count, and a rationale hash without treating the
rationale as evidence.

### Decision Pack

`cad2gis.decision_pack.v1` binds one source hash, one Evidence Graph hash, one
automatic-decision policy, provider request/response hashes, and a sorted set
of repair operations. The pack is frozen in the conversion snapshot and
verified again before publication.

### Independent validation

Each executable operation requires separate geometry, topology, and native
length reports. Georeference operations additionally require an independent
CRS report with check points and spatial coverage. Missing validators
quarantine an operation; a failed validator rejects it.

### Automatic decision

The automatic policy combines deterministic reports with task-specific
confidence and agreement thresholds. It produces only:

- `AUTO_ACCEPTED`: apply the deterministic simulation result;
- `QUARANTINED`: preserve evidence but apply nothing;
- `REJECTED`: validation or execution failed.

Manual approval is not required, but uncertainty cannot be promoted to
`VERIFIED`.

### Similarity-first residual calibration

New source profiles can use
`select_shape_preserving_model_with_independent_validation`, which evaluates
similarity before translation and permits affine only behind the existing
residual-structure and independent-holdout gates. Legacy reviewed profiles keep
the prior simplest-model policy. Control coordinates remain surveyed or
authoritative inputs; OSM and LLM output are not absolute control sources.

## Current execution coverage

The following operations have deterministic executors:

- `attach_existing_label`: copy explicit text from an existing source entity;
- `register_style`: copy an existing source CAD style to an existing feature;
- `materialize_native_curve`: recompute delivery curve geometry from frozen
  reader facts with the registered materializer and policy.
- `join_observed_endpoints`: add a derived endpoint-to-endpoint network
  relation selected from source-observed candidates. It never moves, extends,
  or revertices the source cable.
- `split_at_observed_intersection`: connect two observed source segments to a
  derived intersection node without inserting a source CAD vertex.
- `merge_collinear_fragments`: group observed collinear source segments in the
  derived network without dissolving their source features or lengths.

Direction and CRS operations are registered in the schema but remain
quarantined until their deterministic simulators are implemented. Schema
registration is not execution authorization.

## CLI

Run provider-backed onboarding and conversion in one command:

```powershell
cad2gis auto-convert SOURCE.dwg --project PROJECT --run-dir RUN `
  --provider deepseek --force-bootstrap --json
```

The command fails closed when the provider is unavailable, CAD metadata cannot
produce a unique projected CRS/unit candidate, the proposal is weak or
source-inconsistent, or the dry run produces no semantic features.

Observe a pack without applying it:

```powershell
cad2gis convert SOURCE.dwg --project PROJECT --run-dir RUN `
  --llm observe --decision-pack decision-pack.json --json
```

Execute only registered auto-accepted operations:

```powershell
cad2gis convert SOURCE.dwg --project PROJECT --run-dir RUN `
  --llm assist --decision-pack decision-pack.json --json
```

## Plugin and MCP

`plugins/cad2gis-agent` is a dual Codex/Claude Code plugin. The stdio MCP server
delegates to `src/cad2gis/agent_mcp.py` and exposes:

- `inspect_run`
- `inspect_source`
- `bootstrap_project`
- `validate_project`
- `prepare_ai_onboarding`
- `apply_ai_onboarding`
- `auto_onboard_and_convert`
- `list_evidence_nodes`
- `get_evidence_node`
- `list_registered_operations`
- `list_endpoint_join_candidates`
- `list_network_repair_candidates`
- `list_visual_regions`
- `resolve_visual_hit`
- `prepare_review_workspace`
- `create_decision_pack`
- `validate_decision_pack`
- `run_conversion`

All file paths must remain below configured CAD2GIS project roots. The MCP
adapter contains no conversion implementation.

### Provider profiles

The host agent (Codex or Claude Code) is the primary planner. For standalone
provider experiments, the existing OpenAI-compatible review port supports:

- `deepseek`: `https://api.deepseek.com/chat/completions`,
  `deepseek-v4-flash`, JSON-object output, `max_tokens`, and explicitly
  disabled thinking for reproducible proposal generation;
- `new_api`: an operator-supplied HTTPS base URL, model and
  `json_object`/`json_schema` capability for the later aggregation-gateway
  demo.

Keys are runtime-only environment values and are excluded from configuration
representations, artifacts and sanitized transport errors. Provider output is
compiled into source-bound configuration or a source/evidence-bound Decision
Pack before execution; the provider never writes a GeoPackage or supplies GCP
coordinates.

## Real-time review workspace

Start the bundled OpenLayers review UI with:

```powershell
cad2gis review RUN_DIR --workspace REVIEW_DIR --port 8765
```

The UI overlays read-only delivery layers on OSM, supports point/line/polygon
review annotations, optimistic revisions, WebSocket synchronization, and the
multi-scale CAD visual evidence. Edits are stored outside the run and never
rewrite GeoPackages.

SQLite is the local demo store. Set `CAD2GIS_REVIEW_POSTGIS_DSN` in a deployment
with the `review-postgis` extra to use the same revision/event contract in
PostGIS. A QGIS Server WMS can be added with `--qgis-server-url`,
`--qgis-project`, and `--qgis-layers`.

## Architecture verification dimensions

`cad2gis verify` keeps conversion-fidelity dimensions separate from these
architecture dimensions:

- `evidence_provenance`: a content-verified evidence graph artifact;
- `derived_network`: complete decision artifacts, bound hashes, and explicit
  proof that source geometry and native lengths were not mutated;
- `visual_evidence`: model-space-only, paper-space-excluding render/hit-map
  inventory bound to the same evidence graph;
- `review_isolation`: independent evidence for a separate, revisioned review
  workspace.

These checks strengthen replayability but do not manufacture a second real CAD
truth set or upgrade nominal/OSM alignment to surveyed absolute accuracy.

## Next implementation stages

1. Package the web workspace as an MCP App resource in addition to its current
   plugin tool and local URL.
2. Evaluate on independently reviewed multi-CAD truth sets before making
   cross-CAD accuracy claims.
