# CAD2GIS Accuracy Doctor Design

## Goal

Upgrade Cad2GIS from a one-shot deterministic CAD-to-GIS converter into a reproducible conversion
platform with an auditable correction loop. The LLM acts as a monitor and doctor: it diagnoses subtle
GIS errors and proposes structured fixes, while deterministic validators and human review decide what
can enter the authoritative output.

## Original Purpose Alignment

Cad2GIS should stay focused on the competition's strongest fit: sub-track 2, multi-source
heterogeneous engineering data fusion. The product promise is not "AI edits GIS freely." The promise
is:

- historical CAD drawings become reusable GIS assets;
- graphics, attributes, topology, provenance, and verification evidence are preserved;
- QGIS becomes the practical review and delivery surface;
- AI reduces expert workload by finding likely defects and drafting evidence-backed corrections;
- final GIS data remains deterministic, auditable, and defensible.

This means every upgrade should improve at least one of these outcomes:

- higher verified conversion accuracy;
- easier correction of subtle GIS errors;
- stronger traceability from GIS feature back to CAD evidence;
- better QGIS operator workflow;
- stronger validation materials for the competition submission.

## Requirements Readback

The XA-202610 brief prioritizes sub-track 2, multi-source heterogeneous engineering data fusion:

- Preserve historical CAD assets by converting graphics, attributes, and topology into GIS.
- Target CAD-to-GIS automatic conversion accuracy of at least 90%.
- Prefer QGIS/open-source GIS platform support.
- Provide runnable software, technical documentation, verification reports, raw/result data, and
  reproducible scoring evidence.
- Demonstrate real engineering value: reduced manual work, reusable assets, standardization, and
  credible delivery into design/construction/operations workflows.

The current system already meets the main conversion target on DS-04. The remaining opportunity is
to make subtle GIS corrections easier, safer, and more repeatable across future drawings.

## Design Principles

- The deterministic pipeline remains authoritative.
- LLM output never mutates GIS data directly.
- Every proposed correction must be represented as structured data.
- Every accepted correction must pass deterministic validation.
- Every correction must be traceable to source CAD handles and written to a ledger.
- Score changes must be reproducible before and after corrections.
- QGIS should be the primary review surface for humans.
- Competition evidence matters: every new capability should produce a report, ledger, screenshot, or
  metric that can be submitted or audited.

## Architecture

```text
CAD/DXF
  -> deterministic pipeline
  -> evidence package
  -> deterministic diagnostics
  -> LLM doctor proposals
  -> deterministic patch validation
  -> human or policy approval
  -> correction ledger
  -> patched FeatureCollection / GeoPackage
  -> verification and score delta
```

### Existing Core

The current modules remain the core:

- `src/cad2gis/pipeline.py`: canonical runner.
- `src/cad2gis/model.py`: `Feature`, `SourceRef`, `FeatureCollection`.
- `src/cad2gis/feature_context.py`: hit-vector extraction.
- `src/cad2gis/mapping/engine.py`: deterministic mapping.
- `src/cad2gis/refine.py`: topology refinement and label propagation.
- `src/cad2gis/verify/`: benchmark and per-feature verification.
- `src/cad2gis/warehouse/`: GeoPackage output.

### New Package: `cad2gis.diagnostics`

Responsible for finding suspicious features without changing them.

Issue types:

- `low_confidence_mapping`: mapped feature has weak or conflicting evidence.
- `unverified_duct`: duct is classified but lacks independent verification.
- `dangling_route`: cable/duct route endpoint is not connected.
- `possible_paving_leak`: comms feature has paving/surface-restoration evidence.
- `weak_gcp`: control point has high residual or unstable pairing.
- `schema_gap`: required attribute is missing or suspicious.
- `duplicate_or_fragment`: route fragment or duplicate geometry may be annotation noise.
- `cross_discipline_conflict`: feature conflicts with power/water/road/survey layers or labels.
- `delivery_gap`: converted GIS output lacks a field or metadata needed for downstream construction
  handoff.

Each issue includes:

- stable `issue_id`
- `source_handle` or feature index
- feature class and geometry summary
- evidence dictionary
- severity
- deterministic suggested patch types, if any

### New Package: `cad2gis.doctor`

Responsible for preparing evidence for LLM review and parsing LLM proposals.

The doctor has three roles:

- Monitor: summarize quality problems after conversion.
- Diagnose: explain likely causes using CAD/GIS evidence.
- Prescribe: emit structured correction proposals for deterministic validation.

Inputs:

- diagnostics issue bundle
- source provenance
- nearest text and block fingerprints
- topology neighborhood
- GCP residuals
- current score and per-feature verification status

Output:

Structured patch proposals only. The LLM response must validate against a JSON schema before any
downstream step sees it.

Example:

```json
{
  "patch_type": "reclassify_feature",
  "source_handle": "303D",
  "from_class": "__unmapped__",
  "to_class": "duct",
  "reason": "Reviewed duct schedule and 3孔PVC110 text evidence match this handle.",
  "confidence": 0.86,
  "required_checks": ["no_paving_veto", "near_comms_route", "schema_valid"]
}
```

### New Package: `cad2gis.corrections`

Responsible for patch schema, validation, application, and ledger writing.

Patch types:

- `reclassify_feature`
- `reject_feature`
- `set_attribute`
- `snap_route_endpoint`
- `split_route`
- `merge_routes`
- `apply_reviewed_label`
- `mark_gcp_outlier`
- `propose_block_code`
- `add_construction_attribute`
- `link_design_reference`

Validation rules:

- Patch must reference an existing source handle or stable feature id.
- Patch must be allowed for the feature geometry type.
- Patch must not violate paving veto or reviewed negative evidence.
- Patch must preserve `SourceRef`.
- Geometry edits must stay under configured tolerance unless explicitly reviewed.
- Patch must include evidence and reason.
- LLM-only confidence is never enough to apply a semantic correction automatically.

Ledger record:

```json
{
  "drawing_id": "DS-04",
  "patch_id": "patch-0001",
  "source": "llm_doctor",
  "status": "accepted",
  "patch_type": "apply_reviewed_label",
  "source_handle": "303D",
  "before": {"feature_class": "__unmapped__"},
  "after": {"feature_class": "duct"},
  "evidence": {"reviewed_label": "duct", "matched_text": "3孔PVC110"},
  "validation": {"passed": true, "checks": ["schema_valid", "no_paving_veto"]},
  "score_delta": {"per_feature_duct": 0.014}
}
```

### Configuration

Move subtle thresholds from hardcoded defaults into a versioned profile:

- `scope_threshold`
- `min_route_len`
- `snap_tol`
- `assoc_tol`
- GCP RMSE target
- class-specific geometry tolerances
- auto-apply policy

Default profile path:

- `config/cad2gis_accuracy_profile.yaml`

The pipeline should accept a profile object/path but keep current defaults when omitted.

## QGIS Review Workflow

Add an Accuracy Doctor panel to the QGIS plugin:

- issue table grouped by severity and type
- map highlight for selected feature
- evidence viewer: source handle, layer, block, nearest text, fingerprint, topology neighborhood
- proposed correction viewer
- accept/reject buttons
- ledger preview
- rerun validation and score delta
- export accepted corrections and validation evidence for competition reporting

The QGIS plugin should call the same correction engine as the CLI.

This is the most important user-facing upgrade. It turns QGIS from a passive output viewer into the
review console where a domain user can correct subtle conversion errors without editing code.

## Dual-Surface UI Design

The user experience has two coordinated surfaces:

- QGIS Accuracy Doctor dock: the authoritative spatial review and correction console.
- Web review dashboard: the QA, evidence, and competition-reporting surface for reviewers who do not
  need to edit geometry inside QGIS.

Both surfaces must read the same diagnostics, proposal, correction-ledger, and verification formats.
QGIS owns accepted corrections in the first implementation. The web surface can display, filter,
compare, and export corrections, but should not apply authoritative GIS edits until explicit
multi-user write controls are designed.

### Shared UI Data Contract

Both surfaces use these artifacts:

- `build/diagnostics.json`: deterministic issues found after conversion.
- `build/doctor_proposals.json`: structured LLM doctor proposals that passed JSON schema parsing.
- `build/corrections/<drawing_id>.jsonl`: accepted, rejected, and pending correction ledger entries.
- `build/verification_after_corrections.json`: score and validation output after applying accepted
  patches.
- GeoPackage metadata tables, after the ledger schema stabilizes, for delivery-time auditability.

The UI must never depend on free-form LLM text for behavior. Every button, status, filter, and score
delta reads deterministic fields from these artifacts.

### QGIS Accuracy Doctor Dock

The QGIS dock should use a compact split-pane layout:

- Header toolbar: source drawing, conversion status, overall accuracy, per-feature correctness, and
  action buttons for `Diagnose`, `Doctor`, `Apply Accepted`, and `Verify`.
- Left issue pane: sortable/filterable issue table with severity, class, issue type, source handle,
  confidence, and status.
- Map canvas integration: selecting an issue highlights the converted GIS feature, source CAD
  provenance, nearby text labels, topology neighbors, and relevant control points.
- Right evidence inspector: CAD layer, block name, entity type, handle, nearest text candidates,
  ATTRIB values, block fingerprint, topology neighborhood, GCP residuals, and current validation
  checks.
- Doctor recommendation pane: proposed patch type, before/after values, reason, confidence,
  deterministic checks, and risk flags.
- Bottom correction ledger pane: pending, accepted, rejected, and failed patches with score deltas
  and export controls.

Default actions:

- `Accept`: validates the selected patch and marks it accepted if checks pass.
- `Reject`: records a rejected ledger entry with reason.
- `Needs Review`: keeps the issue visible without applying changes.
- `Zoom to Evidence`: centers the QGIS map on the feature and its evidence neighborhood.
- `Open CAD Evidence`: opens the source-handle evidence packet where available.

Semantic and geometry corrections are never auto-applied by default. Low-risk attribute enrichment
may support batch acceptance only after deterministic validation passes and the user explicitly
selects the batch.

### Web Review Dashboard

The web dashboard should extend the existing demo server into a review surface:

- Overview tab: conversion score, per-feature score, issue counts, and before/after deltas.
- Map tab: Leaflet layers with issue overlays, class filters, selected-feature provenance, and
  synchronized issue selection.
- Issues tab: searchable table for severity, class, issue type, source handle, proposal status, and
  validation status.
- Evidence tab: source CAD evidence packages, nearest text, block fingerprints, topology
  neighborhoods, GCP residuals, and snapshots needed for competition reporting.
- Corrections tab: correction ledger browser with accepted/rejected/pending filters and before/after
  score impact.
- Report tab: exportable evidence pack containing commands, scores, screenshots, ledgers,
  validation reports, and input/output artifact paths.

The web dashboard should initially be read-only for authoritative corrections. If review actions are
added later, they should produce draft decisions in a separate file rather than mutating the accepted
ledger directly. QGIS or the CLI can then promote reviewed drafts into the authoritative ledger after
validation.

### UI States

Both surfaces should handle the same states explicitly:

- no drawing loaded
- conversion running
- diagnostics running
- issues loaded
- doctor proposals loaded
- proposal schema invalid
- patch validation failed
- patch accepted
- patch rejected
- verification running
- score improved
- score regressed
- export completed

Failure states must preserve the original GeoPackage and existing ledger. A failed proposal or patch
should become a visible rejected or failed record, not a silent UI error.

### Visual Style

The product UI should be operational and dense:

- Use QGIS-native controls in the plugin and compact tables/split panes in the web dashboard.
- Use icon buttons for common actions, with tooltips for less obvious actions.
- Use tabs for major views and segmented controls for issue status filters.
- Use severity colors sparingly: red for blocking errors, amber for warnings, green for passed
  validation, gray for neutral or pending states.
- Avoid marketing-style hero sections, decorative backgrounds, nested cards, and oversized type in
  the actual review surfaces.
- Keep dimensions stable for tables, issue rows, map panels, and action bars so filtering or status
  changes do not shift the layout.

This keeps the UI aligned with the original purpose: accurate engineering conversion, practical QGIS
review, auditable corrections, and reproducible competition evidence.

## CLI Workflow

Add commands:

```bash
cad2gis diagnose input.dxf --report build/diagnostics.json
cad2gis doctor build/diagnostics.json --out build/doctor_proposals.json
cad2gis apply-corrections input.dxf build/doctor_proposals.json --ledger build/corrections/DS04.jsonl
cad2gis verify --report build/run_report.json --benchmark src/cad2gis/verify/benchmark/ds04_surveyed.json
```

`cad2gis doctor` may initially support `--offline-template` to generate a prompt package without
calling an external LLM. A later integration can call a model when credentials are configured.

CLI parity is required because the competition evidence must be reproducible without clicking
through the QGIS UI.

## Highest-Value Accuracy Levers

Prioritize these before lower-value polish:

1. Duct truth closure: reviewed duct subset and node-coordinate/detail-sheet matching for `gc013*`
   and route ducts.
2. Route topology correction: diagnose and propose safe endpoint snaps, route splits, and fragment
   demotions with score deltas.
3. Cross-discipline filtering: detect paving/tree/road/water/power symbols that leak into comms GIS.
4. GCP doctor: explain high-residual labels and propose outlier handling without overclaiming CRS.
5. Attribute and handoff enrichment: preserve construction-useful fields such as duct holes,
   material, diameter, node id, cable length, source sheet, and review status.
6. QGIS correction UX: make every issue visible, reviewable, and exportable.

## Error Handling

- Diagnostics failure should not block conversion; it should produce an error section in the report.
- Doctor JSON schema failure rejects the proposal bundle.
- Patch validation failure writes a rejected ledger entry and leaves GIS output unchanged.
- Applying corrections should be transactional: if a patch batch fails unexpectedly, preserve the
  original GeoPackage and write a failure report.

## Testing Strategy

Unit tests:

- diagnostics issue detection for each issue type
- patch schema validation
- patch application preserving provenance
- rejection of circular or unsafe LLM proposals
- correction ledger serialization

Integration tests:

- run synthetic conversion
- detect a known subtle error
- apply a reviewed-label patch
- verify score/per-feature uplift
- confirm QGIS/CLI surfaces read the same ledger format

Regression tests:

- topology-propagated ducts do not self-verify
- paving veto still blocks non-comms symbols
- geometry edits cannot exceed tolerance without review
- score cannot improve from rejected proposals

## Rollout

Phase 1: Deterministic diagnostics and correction schema.

Phase 2: Reviewed-label and duct-verification correction workflow.

Phase 3: CLI commands for diagnose/apply/verify.

Phase 4: QGIS Accuracy Doctor panel.

Phase 5: LLM doctor prompt package and JSON response parser.

Phase 6: Optional configured LLM call with strict schema validation and no direct mutation.

Phase 7: Competition evidence pack generation: before/after scores, accepted correction ledger,
screenshots, and reproducibility commands.

## Open Decisions

- Whether any patch type may auto-apply by default. Recommended default: no semantic or geometry
  patch auto-applies; only low-risk attribute enrichment may auto-apply.
- Which LLM provider to use first. Recommended default: produce prompt packages first and keep the
  provider adapter behind a small interface.
- Whether correction ledgers should be stored beside build artifacts or embedded into GeoPackage
  metadata tables. Recommended: do both after the ledger schema stabilizes.
