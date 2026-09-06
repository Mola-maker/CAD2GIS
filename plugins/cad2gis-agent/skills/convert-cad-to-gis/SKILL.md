---
name: convert-cad-to-gis
description: Inspect, convert, register, and review CAD/DWG projects with CAD2GIS MCP tools. Use for entity census, labels/styles, source-bound semantic mapping, geometry/topology/length validation, CRS/GCP registration, GeoPackage/QML delivery, or run-status diagnosis.
---

# Convert CAD to GIS

Skill contract: `cad2gis.convert_skill.v3`.

Use the CAD2GIS MCP tools as the orchestration surface. The Python package is
the only conversion implementation; never recreate geometry, topology, length,
or CRS algorithms inside the host agent or plugin.

## Required opening sequence

1. Call `get_capabilities` and `debug_mcp`; record the interpreter, imported
   package path, package version, protocol, tool-contract digest and filesystem
   roots. Require `prompt_contract.version` to be `cad2gis.agent_prompt.v3`
   before constructing proposals. Check the installed client plugin separately:
   a wheel server cannot prove which skill the host loaded. If the injected
   plugin is stale, use the verified current installation through its public
   CLI/API and actual stdio probe while completing an authorized plugin update.
   Do not dispatch current proposals through incompatible cached tools.
2. Read the returned `runtime`. If the selected LibreDWG reader is unavailable,
   call `install_runtime`, then call `get_runtime_status` again. Do not require
   AutoCAD or Conda and do not silently select the AutoCAD fallback.
3. Call `inspect_source` for a new DWG, or `inspect_run` for an existing run.
4. Keep source facts, semantic interpretation, geometry/topology/length checks,
   and coordinate accuracy as separate claims.
5. If a requested path is outside the configured roots, configure only the
   task-authorized source and output directories through `CAD2GIS_PROJECT_ROOTS`
   when installation/configuration is already authorized. Otherwise explain
   the missing access and request that exact directory, not an entire drive.

## New drawing workflow

1. `inspect_source`
2. `bootstrap_project`
3. `prepare_ai_onboarding`
4. Select only observed layer/block/entity identifiers and supplied candidates.
5. Submit typed JSON tool arguments that cite those exact identifiers; do not
   return prose in place of a proposal object.
6. `apply_ai_onboarding`
7. `validate_project`
8. `run_conversion`
9. `inspect_run`
10. `audit_run`

Provider-backed onboarding may use `auto_onboard_and_convert`, but its proposal
still passes deterministic compilation and admission gates.

## Source database and semantic revision workflow (package 0.4+)

Use `debug_mcp` to compare the running code and complete tool schema digests
before relying on this workflow. Older installed plugins may expose a different
implementation even when tool names look similar.

1. `export_source` publishes a new immutable source snapshot. Omit CRS if it is
   not established; native coordinates and unknown units stay explicit.
2. Use `query_source_entities` with filters and keyset cursors, then
   `get_entity_context_batch` for observed IDs. Use `view=plan` to inspect
   expanded instances and their lineage. This revision's semantic patch service
   accepts source entity keys; instance-level semantic writes need the canonical
   plan-domain adapter. Do not infer curve intersections from
   conservative bbox candidates alone. Context chunks must be reassembled before
   interpreting a long text or geometry field. MCP byte budgets include the
   protocol envelope (8–64 KiB); never request arbitrary SQL.
   A cold index build can exceed a normal query RPC timeout. Record build time
   separately from warm lookup time and explicitly budget the first probe.
3. `prepare_semantic_batches` builds a separate immutable candidate index.
   `query_relationship_candidates` returns registered class, label or DIMENSION
   choices; a nearby label is advisory until explicitly selected.
4. `initialize_semantic_store`, then construct a patch using the returned binding
   hashes, `base_revision`, and only observed entity/candidate/policy/target IDs.
   Onboarding v2 similarly selects `annotation_family_selections` candidate and
   policy IDs; the model cannot supply regexes or numeric matching thresholds.
5. `preview_semantic_patch` validates without writing. Commit its exact
   `preview_hash` with `commit_semantic_patch` and a stable idempotency key.
   Revision conflicts require a fresh read and preview; never overwrite source
   coordinates, native lengths, curve parameters, original text, CRS or GCP.
6. `compile_semantic_revision` produces a new source-coordinate semantic
   candidate. It is not an accepted canonical GIS delivery; use the established
   conversion and registration gates for final delivery. Unsupported and
   unclassified entities remain explicitly unresolved.
7. After a timeout or cancelled RPC, use `inspect_semantic_store` with the same
   idempotency key before retrying. `cancel_compile_job` fences publication.
   `reconcile_compile_jobs` is for recovery after the worker is known stopped.

SQLite revisions, jobs and outbox are authoritative. Redis is optional and is
not required for this local workflow.

## Existing delivery evidence and repair workflow

1. Page evidence with `list_evidence_nodes`; read only needed nodes. Prefer a
   run that reports `query_backend=sqlite-index` for large drawings.
2. Read `list_cad_scene_nodes` for pre-semantic structure. Use
   `list_label_candidates` and `list_legend_catalog_candidates` only as
   advisory choices; they never authorize exclusion or label attachment.
3. Use `list_visual_regions` and `resolve_visual_hit` as secondary evidence.
4. Read `list_registered_operations` before proposing a repair.
5. Select only returned endpoint/network candidate IDs.
6. Create and validate an ID-only decision pack.
7. Run `observe` before `assist` unless the user explicitly requested applying
   a registered repair.

For a user-reported bad result, use the separate `iterate-cad-to-gis` skill.
Its bounded state machine must reject any candidate that regresses geometry,
topology, length, coordinate, conservation, or run-status gates.

Read [decision-contract.md](references/decision-contract.md) before creating or
diagnosing a decision pack.
Read [agent-prompt-contract.md](references/agent-prompt-contract.md) when a host
agent needs to construct onboarding or repair arguments directly.

## Accuracy and safety rules

- Never invent coordinates, WKT, lengths, CRS identifiers, GCPs, layers, or
  expected entity counts.
- Vector reader facts are primary; VLM/render evidence is secondary.
- Preserve source geometry and native length. Repairs create derived network
  relations and must pass independent validators.
- OSM/visual controls are relative references, not proof of absolute accuracy.
- Require distributed training points and independent check points for GCP
  registration. Prefer a shape-preserving similarity transform.
- Keep uncertain entities unresolved and preserve `CONDITIONAL`/`UNSAFE`.
- Do not reuse another DWG's source-bound registry, decision pack, or counts.
- Even identical DWG bytes do not prove a copied inventory is current: basename,
  reader metadata and annotation extraction can change its binding. Rebuild and
  review the current proposal; never overwrite hashes to force admission.
- Review edits stay in the separate review workspace. A corrected GeoPackage is
  created only by running the returned conversion command into a new run.

## Interactive review

Call `prepare_review_workspace`, launch the returned command, and open its local
URL. Use the ToC to move through source, mapping, registration, validation, and
delivery evidence. The console command is copyable; executing it creates a new
immutable run rather than modifying the current delivery in place.

For real-drawing simulation, reported offsets/missing objects, field review, or
GCP work, read [onsite-verification.md](references/onsite-verification.md).
Include full-source versus delivery overlays, close-ups of differences, source
dispositions, field provenance and explicit derived-geometry movements. The
web CAD pane traces delivered objects; it is not a complete DWG renderer.

Before delivery, call `audit_run`. Require `audit_status=PASS` for artifact
integrity, and report the independent run status, visual/geometry/field review
and coordinate accuracy separately. An intact `CONDITIONAL` run is a review
candidate, not an accepted precision result. Report artifact hash failures and
layer-census mismatches separately. `source:not_replayable`
is a portability warning: archived GeoPackages remain reviewable, but the agent
must not claim it can create a calibrated rerun until the original DWG is
reattached and verified against the manifest SHA-256.
