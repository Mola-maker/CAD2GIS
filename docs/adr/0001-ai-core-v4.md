# ADR 0001: AI-Core v4 CAD Understanding Architecture

- Status: Accepted
- Date: 2026-08-11
- Owners: CAD2GIS maintainers
- Scope: `src/cad2gis`, the agent plugin, review UI, and delivery contracts

## Context

The current repository contains three partially overlapping execution models:

1. the canonical deterministic adapter in `src/cad2gis/pipeline.py` and
   `src/cad2gis/cad2gis_v3/pipeline.py`;
2. source-specific APD classification helpers such as `src/cad2gis/apd_rules.py`;
3. operator and experiment scripts that have produced GeoPackages outside the
   canonical manifest and validation boundary.

This split explains the observed failure modes on unseen drawings:

- title blocks, legends, schedules, symbol samples, and plan content are mixed;
- anonymous or unfamiliar layers and blocks are either over-classified or
  reduced to noise;
- source text is associated by local proximity without sufficient leader,
  object-type, or topology evidence;
- internal stable IDs become visible labels when source labels are absent;
- drawing-specific rules work for one vendor family but do not transfer to a
  new drafting convention;
- a visually plausible map overlay can be produced by a non-canonical
  registration path without independent check-point accuracy.

Reader completeness, evidence conservation, and deterministic execution remain
valuable. The missing capability is a source-agnostic semantic control plane
that understands one drawing as a structured scene before compiling it into GIS
objects.

## Decision

CAD2GIS v4 uses an **AI semantic core with a deterministic numeric data plane**.

The AI core is authoritative for proposing scene roles, ontology mappings,
object-to-label relations, legend interpretations, and review priorities. It is
not authoritative for coordinates, curve vertices, lengths, transforms, GCP
values, or acceptance metrics.

The production path is:

```text
DWG readers
  -> lossless CAD Scene Graph
  -> model/layout/viewport/legend/title/schedule segmentation
  -> multimodal evidence tiles + structural neighbourhoods
  -> AI semantic interpretation and typed candidate ranking
  -> source-bound Semantic Plan
  -> deterministic geometry/topology/label compiler
  -> nominal CRS or reviewed GCP registration
  -> independent validators
  -> source + evidence + delivery GeoPackages, QML, manifest, run_status
```

The model can be the workflow and semantic core without becoming a geometry
generator. Every accepted semantic claim must reference immutable source entity
IDs and evidence regions. Every numeric result must be recomputed by a
registered deterministic implementation.

## Required contracts

### 1. Lossless CAD Scene Graph

The graph preserves, before classification:

- model and paper layouts, viewports, and layout transforms;
- block definitions, nested INSERT instances, attributes, dynamic-block facts,
  and XREF provenance;
- native curves, bulges, splines, dimensions, leaders, hatches, text, and style;
- WCS/OCS coordinates and exact source handles;
- structural edges such as `contains`, `instance_of`, `leader_targets`,
  `text_near`, `touches`, `crosses`, and `same_style_as`.

No scene role removes a source entity. Roles create reversible, auditable views.

### 2. Scene segmentation before semantic classification

The system must distinguish at least:

- `plan_content`;
- `legend_catalog`;
- `title_block`;
- `schedule_or_summary`;
- `overview_or_schematic`;
- `annotation_only`;
- `unknown_scene`.

CAD metadata and deterministic geometry generate candidates. A multimodal model
can rank candidates using rendered tiles and structural context. Fixed ratios
or text keywords alone cannot auto-exclude content. Low-confidence entities stay
in `unknown_scene` and are absent from the delivery GeoPackage.

The model never receives an ungrounded screenshot as its only evidence. Each
layout is rendered independently at overview and adaptive detail scales. Every
region carries a parallel entity-ID hit map and a structured context page with
exact text, block attributes, types, layers, styles, and Scene Graph node IDs.
Paper layouts and block definitions remain visible because they often contain
title, schedule, legend, and symbol-prototype evidence.

Scene-role output is a content-addressed `Scene Interpretation Plan` bound to
the source, CAD Scene Graph, and visual-manifest hashes. It can only rank
existing graph nodes into the registered scene-role vocabulary. Unassigned
nodes default to `unknown_scene`; no role assignment deletes source evidence.

### 3. Drawing-local ontology induction

An unseen drawing is not forced into the APD vocabulary. The AI core proposes a
drawing-local ontology from observed blocks, layers, styles, labels, dimensions,
and legend samples. A domain adapter may later map those local classes to a
telecom, utilities, or customer schema.

Reusable vendor knowledge supplies candidates, never unconditional mappings.
Every reused mapping is revalidated against the current source graph.

### 4. Legend decoding

Legend regions are used to build a drawing-local symbol and line-style catalog.
Catalog samples are matched to plan instances by block identity, geometry
fingerprint, style, surrounding text, and spatial context. The samples
themselves never become delivery assets.

### 5. Public label contract

`display_label` is a customer-facing business value, not a technical identity.

- Stable handles, feature hashes, `NN-*` placeholders, and generated
  `<CLASS>-CAD-<HANDLE>` values are never public fallback labels.
- A public label must reference explicit CAD text, a block attribute, a
  dimension, a reviewed external record, or a deterministic format composed
  entirely from such fields.
- A source text is not accepted merely because it is nearest. The relation must
  consider leader connectivity, source/target class compatibility, layer and
  block context, orientation, containment, and ambiguity.
- When no defensible label exists, `display_label` is empty and the unresolved
  candidate remains in evidence.

### 6. Geometry, length, and topology authority

The model selects or ranks source-bound candidates only. Registered code:

- materializes native curves from reader facts;
- computes lengths from each exact curve and records measurement provenance;
- prevents one source measurement from being copied to unrelated fragments;
- builds a derived network without silently moving source vertices;
- validates source geometry, topology, and length independently.

### 7. Coordinate authority

Absolute location follows this precedence:

1. reviewed CAD CRS metadata with valid coordinate-domain evidence;
2. surveyed or authoritative controls;
3. reviewed GPS/GNSS observations embedded in or supplied with the drawing;
4. operator-selected GCPs in the review UI;
5. basemap/AI correspondence proposals, which remain non-authoritative until
   reviewed and independently checked.

Similarity is evaluated before translation or affine models. Rubber-sheet/TPS
transforms require a separate dense-control profile, spatial coverage tests,
and independent holdouts. Visual overlap with OSM is not an accuracy result.

### 8. Delivery isolation

Only the canonical pipeline may publish a production delivery. A valid delivery
contains a manifest binding:

- source, inventory, Scene Graph, Semantic Plan, code, and runtime hashes;
- the exact registered operation set;
- per-stage validation reports;
- explicit `VERIFIED`, `CONDITIONAL`, `UNSAFE`, or `FAILED` status.

Experiment scripts and manually cleaned GeoPackages are diagnostic artifacts,
not product deliveries.

## Performance model

The full drawing is parsed once and cached by source hash. AI work is
coarse-to-fine:

1. deterministic scene proposals and overview tiles;
2. model review only for ambiguous regions;
3. object-level requests only for unresolved candidate subgraphs;
4. replay from the frozen Semantic Plan without contacting a provider.

This avoids sending every entity to a model and keeps deterministic reruns fast.

## Acceptance evidence

Release claims require three separate corpora:

- reader compatibility corpus: can the source be inventoried without loss;
- semantic truth corpus: reviewed scene roles, object classes, legends, and
  label relations across vendors and drawing types;
- geospatial truth corpus: authoritative controls plus independent check points.

Minimum reported metrics are scene-role precision/recall, legend leakage,
object precision/recall, public-label exact match and association accuracy,
curve/length error, topology precision/recall, held-out positional RMSE,
abstention calibration, and runtime.

## Consequences

- Existing reader, evidence, curve, topology, warehouse, manifest, and review
  components remain reusable behind stricter contracts.
- APD mappings become a source adapter and regression fixture, not the universal
  ontology.
- Existing heuristic scene partitioning becomes candidate generation only.
- Provider calls are allowed in the semantic control plane, but a frozen
  Semantic Plan must make conversion replayable without a provider.
- Unknown content produces an honest incomplete run instead of a noisy
  GeoPackage that appears successful.
