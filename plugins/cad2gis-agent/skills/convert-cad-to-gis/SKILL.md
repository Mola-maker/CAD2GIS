---
name: convert-cad-to-gis
description: Inspect, convert, and review CAD/DWG projects with the CAD2GIS evidence graph and MCP tools. Use for DWG/CAD entity census, labels and styles, typed LLM decision packs, geometry/topology/length checks, CRS/GCP review, GeoPackage delivery, live QGIS desktop-session control, QGIS artifacts, or explaining run_status and manifest evidence.
---

# Convert CAD to GIS

Use the `cad2gis` MCP tools as the orchestration surface. Keep the canonical
Python pipeline as the only conversion implementation.

Use LibreDWG as the primary DWG reader. Select the AutoCAD reader only after a
classified LibreDWG failure or when the user explicitly requests a parallel
verification. Never persist `CAD2GIS_READER_BACKEND=autocad` as the plugin
default and never switch readers silently; keep each reader attempt and its
inventory provenance observable.

When the requested boundary is source facts only, call `export_source`. It
creates `source.gpkg`, the authoritative inventory, CAD Scene Graph, visual
evidence, and `source_manifest.json`, then stops before semantic mapping,
topology repair, length inference, CRS/GCP registration, and delivery output.
Omit `source_crs` unless authoritative evidence supplies it; omission is
recorded as `native_cad_unregistered`, never guessed.

For the next source-bound stage, call `prepare_semantic_batches` and page
`list_semantic_candidates`. Submit only observed assembly/entity/class/label/
evidence IDs through a `cad2gis.semantic_decisions.v1` pack, then call
`compile_semantic_layers` and `inspect_semantic_coverage`. The compiler copies
geometry, coordinates, native lengths, styles, and label text from
`source.gpkg`; the model cannot write those values. Every source entity ends
in exactly one of `CONSUMED_BY_FEATURE`, `RETAINED_AS_REFERENCE`,
`EXCLUDED_AS_DOCUMENTATION`, or `UNRESOLVED`. Unmentioned entities remain
`UNRESOLVED`; never force classification to improve a conversion-rate metric.
For network lines, inspect `relationship_evidence`: keep
`EXACT_SOURCE_ENDPOINT` distinct from `PROXIMATE_ONLY`, cite the candidate
relationship evidence ID and any matching CAD legend evidence ID, and never
infer or rewrite `native_length`.

## Workflow

If AutoCAD Core Console reports that it cannot set up the current profile and
the MCP process runs as a different Windows user from the interactive desktop,
do not retry with guessed registry values or claim that COM can attach across
the user boundary. Ask the user to load
[`scripts/export-autocad-profile.lsp`](scripts/export-autocad-profile.lsp) with
`APPLOAD` in their already initialized AutoCAD session, run
`CAD2GIS_EXPORT_PROFILE`, and choose a new `.arg` path under an allowed project
root. Then set `CAD2GIS_AUTOCAD_PROFILE` to that file, rerun
`cad2gis doctor --deep --strict --json`, and retry `inspect_source`. The helper
exports the active profile only; it does not switch or import profiles, touch
the registry directly, or modify the drawing.

1. For a new DWG, call `inspect_source`, then `bootstrap_project`. Inspection
   establishes reader and plan-domain completeness without borrowing another
   drawing's rules.
2. Read `review/cad_scene_graph.json` through `list_cad_scene_nodes` and
   `get_cad_scene_node` when structural context is needed. Treat scene-role
   detections as candidates only; never infer deletion from a candidate.
3. Use `list_scene_visual_regions` to inspect every layout, then page exact
   entity/text/block context with `get_scene_visual_region_context`. Ground
   visual claims in both a region ID and an existing Scene Graph node ID. Build
   the typed plan with `create_scene_interpretation_plan`; do not hand-edit its
   hashes or add coordinates.
4. Call `prepare_ai_onboarding`. Use its task-bound JSON schema and select only
   observed graph/layer/block identifiers and the supplied deterministic CRS
   candidate. Prefer empty arrays over weak mappings. Never propose
   coordinates, lengths, CRS identifiers, GCPs, expected counts, or arbitrary
   regular expressions.
5. Call `apply_ai_onboarding` with that exact proposal and host/model
   provenance. The deterministic compiler revalidates all identifiers, runs a
   fresh source census and semantic dry run, derives exact expectations, and
   either atomically admits the project as `auto_accepted` or restores draft
   state. Then call `run_conversion`.
6. When a configured DeepSeek or New API provider should perform the proposal
   step, call `auto_onboard_and_convert`. This is the single-call path:
   bootstrap -> model proposal -> deterministic compile/validate -> canonical
   conversion. A provider failure, missing CRS evidence, weak confidence, or
   empty semantic result must fail closed.
5. For an existing reviewed/auto-accepted project, call `validate_project`
   before `run_conversion`. For an existing run, use `inspect_run`.
6. Page through `list_evidence_nodes`; call `get_evidence_node` only for the
   entities needed for the current decision.
7. Use `list_visual_regions` for multi-scale visual context and
   `resolve_visual_hit` to map a hit-map RGB value back to an entity node.
   Treat pixels as secondary evidence only.
8. Read `list_registered_operations` before proposing a repair.
9. For endpoint repair, call `list_endpoint_join_candidates` and select only a
   returned candidate ID and registered policy ID.
10. For crossing or collinear repair, call
   `list_network_repair_candidates`; treat the result as a derived network
   decision, not a source-geometry rewrite.
11. Build a decision pack with graph node IDs only. Never supply coordinates,
   geometry, WKT, lengths, GCP values, or arbitrary fields.
12. Validate the pack. Use `observe` to audit it and `assist` only when applying
   registered operations is intended.
13. Run the canonical conversion and report `run_status`, unresolved decisions,
   manifest hashes, and artifact paths separately.
14. When interactive review is requested, call `prepare_review_workspace` and
    launch the returned local command. Keep all edits in that separate
    workspace; the run artifacts remain immutable.
15. Use QGIS only after the canonical pipeline has produced a candidate or
    delivery artifact. QGIS is for late-stage visual review, styling, layer
    visibility, and fine-tuning; it is not a DWG reader, semantic authority,
    topology repair engine, or CRS authority. When the user asks to inspect the
    result in QGIS, call
    `start_qgis_desktop_session` with a session directory under an allowed
    project root, then `load_qgis_conversion_run`. Verify the returned QGIS
    version, layer validity, visibility, and canvas extent with
    `inspect_qgis_desktop_session`. Use only the typed visibility, zoom, and
    export tools; the bridge intentionally has no arbitrary Python tool.
16. Treat `export_qgis_desktop_view` as the GUI round-trip artifact, not as
    proof that the CAD conversion is semantically or spatially correct. Check its
    byte count and PNG signature, and show the image to the user when visual
    review is part of the task. Stop only the dedicated managed session, never
    an unrelated QGIS process.

## Accuracy rules

- Treat vector reader facts as primary and rendered/VLM evidence as secondary.
- Use the model for semantic selection and workflow planning, not numerical
  invention. Automatic does not mean unbounded authority.
- Unknown identifiers and uncertain mappings must remain explicit abstentions;
  never reuse a source-bound registry on a different DWG.
- Never infer a CRS or control point from visual resemblance alone.
- Require geometry, topology, and length validators for every operation.
- Treat endpoint joins as derived network relations; never move source cable
  vertices to close a visual gap.
- Require independent check points and spatial coverage for CRS/GCP decisions.
- Preserve `CONDITIONAL` or `UNSAFE`; never relabel an incomplete run as verified.
- For a new drawing, use its own source profile and mapping registry. Do not
  reuse source-bound APD rules or counts.

Read [decision-contract.md](references/decision-contract.md) when creating or
diagnosing a decision pack.

When the user reports that a completed conversion is visually or semantically
unsatisfactory, switch to the `iterate-cad-to-gis` workflow. Do not patch the
accepted run in place or turn one correction into a global rule.
