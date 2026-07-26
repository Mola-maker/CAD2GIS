---
name: convert-cad-to-gis
description: Inspect, convert, and review CAD/DWG projects with the CAD2GIS evidence graph and MCP tools. Use for DWG/CAD entity census, labels and styles, typed LLM decision packs, geometry/topology/length checks, CRS/GCP review, GeoPackage delivery, QGIS artifacts, or explaining run_status and manifest evidence.
---

# Convert CAD to GIS

Use the `cad2gis` MCP tools as the orchestration surface. Keep the canonical
Python pipeline as the only conversion implementation.

## Workflow

1. For a new DWG, call `inspect_source`, then `bootstrap_project`. Inspection
   establishes reader and plan-domain completeness without borrowing another
   drawing's rules.
2. Call `prepare_ai_onboarding`. Use its task-bound JSON schema and select only
   observed layer/block identifiers and the supplied deterministic CRS
   candidate. Prefer empty arrays over weak mappings. Never propose
   coordinates, lengths, CRS identifiers, GCPs, expected counts, or arbitrary
   regular expressions.
3. Call `apply_ai_onboarding` with that exact proposal and host/model
   provenance. The deterministic compiler revalidates all identifiers, runs a
   fresh source census and semantic dry run, derives exact expectations, and
   either atomically admits the project as `auto_accepted` or restores draft
   state. Then call `run_conversion`.
4. When a configured DeepSeek or New API provider should perform the proposal
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
