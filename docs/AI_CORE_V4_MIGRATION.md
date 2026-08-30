# CAD2GIS AI-Core v4 Migration Matrix

This document turns [ADR 0001](adr/0001-ai-core-v4.md) into executable slices.
It deliberately separates architecture replacement from drawing-specific fixes.

## Component disposition

| Current component | Decision | v4 responsibility |
| --- | --- | --- |
| `reader/autocad.py`, `reader/libredwg.py`, record bundles | Keep and harden | Lossless source facts and capability-typed failures |
| `cad2gis_v3/plan_domain.py` | Evolve | Instance materialization feeding the CAD Scene Graph |
| `cad2gis_v3/scene_partition.py` | Demote | Candidate generation only; never unilateral exclusion |
| `cad2gis_v3/evidence_graph.py`, visual evidence | Evolve | Unified structural and multimodal reasoning context |
| `cad2gis_v3/onboarding.py` | Replace control contract | Multimodal scene/ontology proposal over graph IDs |
| `cad2gis_v3/semantics.py` | Split | Semantic Plan compiler plus label resolver |
| `cad2gis_v3/topology.py`, curve materialization | Keep | Deterministic numeric authority |
| `cad2gis_v3/calibration.py`, `georef.py` | Keep behind stronger gates | Reviewed registration and held-out validation |
| `cad2gis_v3/warehouse.py`, styles, manifest | Keep and gate | Canonical, atomic delivery only |
| `apd_rules.py` | Quarantine | APD source adapter and regression evidence only |
| scripts and hand-built GeoPackages under `output/` | Exclude | Diagnostics; never production entrypoints |
| MCP/plugin | Evolve | Orchestrate the canonical semantic and data planes |

## Delivery sequence

### Slice 0: freeze and establish invariants

- Preserve the current dirty worktree and do not fold experiment output into
  production modules.
- Record the canonical entrypoint and delivery manifest marker.
- Enforce the public-label contract: generated stable IDs cannot become
  `display_label`.
- Add regression tests for the screenshots' failure classes.

Exit gate: canonical tests pass and a feature without source-backed label
evidence is delivered with an empty public label.

### Slice 1: CAD Scene Graph v1

Add a source-bound graph schema for layouts, viewports, definitions, instances,
entities, native text/leader relations, and deterministic spatial relations.
Adapt the current plan-domain output without changing source geometry.

Exit gate: every reader entity is represented or explicitly accounted for; no
layout or nested block silently disappears.

### Slice 2: scene understanding

Convert current legend/title/schedule heuristics into evidence-producing
candidates. Add multi-scale renders and hit maps for candidate regions. Define
typed model output that can rank roles but cannot delete source nodes.

Exit gate: reviewed multi-vendor fixtures measure scene-role precision/recall
and legend leakage independently.

Implementation status (2026-08-11): structural contract complete. The
canonical bootstrap and conversion run now publish per-layout adaptive renders,
hit maps, exact structured region context, and a typed hash-bound Scene
Interpretation Plan protocol. MCP exposes paged region/context reads and plan
creation/validation. Cross-vendor truth metrics remain pending reviewed truth
labels and therefore are not claimed complete. The MCP host path can inspect
the generated PNGs directly. The one-shot DeepSeek text-provider path receives
the complete layout summary and a bounded region index, but is not labelled as
visual reasoning; provider-native image attachment remains a later adapter
capability rather than being silently assumed.

### Slice 3: ontology and legend induction

Build a drawing-local ontology and symbol catalog. Use legend samples as
prototypes while excluding their instances from delivery. Compile accepted
claims into a hash-bound Semantic Plan.

Exit gate: an unseen vendor drawing can abstain or build a local schema without
using APD block names or label regular expressions.

### Slice 4: label relation resolver

Generate text-to-object candidates from explicit leaders, attributes,
containment, class compatibility, orientation, topology, and distance. Let the
AI rank only existing relations. Keep ambiguity unresolved.

Exit gate: technical-ID leakage is zero; exact-match and association accuracy
are reported on reviewed truth.

### Slice 5: deterministic compiler and validators

Compile the Semantic Plan to source-preserving features. Calculate curve,
length, topology, and style fidelity as independent reports. Prevent duplicated
source measurements across unrelated delivery objects.

Exit gate: source-feature conservation, native curve error, length error, and
topology precision/recall meet explicit thresholds.

### Slice 6: coordinate registration and review UI

Expose candidate controls and source anchors in the Web UI. A selected map point
must write a review record and trigger a new immutable registered run, never
mutate the old GeoPackage. Evaluate similarity first and require spatially
distributed held-out checks.

Exit gate: held-out RMSE and control coverage are reported; lack of authority
cannot produce `VERIFIED` absolute accuracy.

### Slice 7: customer workflow and compatibility

Present preflight, progress, unresolved items, confidence layers, registration,
and export as one workflow. Cache by source and plan hashes. Publish client
templates for mainstream MCP-capable agents while keeping one canonical API.

Exit gate: cold and cached runtime, failure recovery, and cross-vendor success
rates are measured on sources outside the development fixtures.

## Immediate code gates

The first implementation batch must add these non-negotiable checks:

1. `display_label` cannot equal a generated stable-handle code unless the same
   value is also present as explicit source text or a reviewed external value.
2. scene-role candidates cannot remove route or asset entities without a typed
   accepted Semantic Plan operation.
3. every delivery manifest identifies the canonical pipeline and Semantic Plan
   hash; a missing marker makes the artifact diagnostic only.
4. semantic, geometry, topology, length, and absolute-position status are
   separate fields and cannot upgrade one another.

## Repository hygiene boundary

Do not delete the current `output/` and `input_drawings/` material until its
source ownership and diagnostic value are reviewed. After evidence extraction,
move reproducible fixtures to governed test-data locations and remove transient
scripts, backups, caches, and hand-built packages from the product tree.
