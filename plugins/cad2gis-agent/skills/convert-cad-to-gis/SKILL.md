---
name: convert-cad-to-gis
description: Inspect, convert, register, and review CAD/DWG projects with CAD2GIS MCP tools. Use for entity census, labels/styles, source-bound semantic mapping, geometry/topology/length validation, CRS/GCP registration, GeoPackage/QML delivery, or run-status diagnosis.
---

# Convert CAD to GIS

For optional DXF queries, drawing checks or copy edits before conversion, read
[the CAD sidecar reference](references/cad-bundles.md). Its tools preserve the
source and return a revised source for the existing validation workflow.

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

First check whether the supplied DWG SHA-256 already identifies a reviewed
project or a user-selected baseline. If it does, use the existing-project
workflow below instead of generating a replacement mapping from layer guesses.

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

## Reviewed baseline replay and delivery

1. Bind the DWG bytes and complete reader inventory to the selected baseline.
   Reconstruct an inventory under the documented historical basename when
   needed; accept it only if its computed digest matches. A validated complete
   native snapshot may be replayed. Changed reader facts require a separate
   source-bound review, never an edited digest or a mixture of snapshot records.
2. Copy reviewed configuration into a new project with the original project
   directory name. Preserve mapping, CRS, spatial partitions, pinned matching
   profiles, frozen OSM evidence and geometry repair mode. SF policy can depend
   on that directory name as well as project_id. Use the same explicit
   geometry_repairs value when compiling new onboarding and converting it.
3. Run canonical conversion into a new directory. For OSM-reviewed baselines,
   use matching="osm" so pinned project corrections are selected. Never replace
   a reviewed correction with a nominal run just to make conversion finish.
4. For the standard baseline six-file delivery, call `publish_matched_delivery`
   with a new delivery_root and source-bound frozen osm_snapshots for every
   populated scope. QGIS uses CAD2GIS_QGIS_PYTHON or canonical discovery.
   Preserve its matching gates and partition directories. `package_qgis_delivery`
   creates an ordinary portable QGZ/ZIP, without automatically applying SVGs;
   it does not produce this standard SVG/OSM delivery contract.
   For an unmatched scope, use `package_qgis_delivery`, then
   `build_source_svg_delivery` with source-bound SVG assets, then
   `build_qgis_review` as an explicitly uncorrected reference view.
   Use a new output directory at every stage. Bind its actual matching record to
   the exact displayed database; retain abstained/unavailable titles and never
   present such a scope as an accepted matching revision.
5. Call `verify_qgis_review` for both QGZ files with new image paths. It reopens
   and renders each project in a separate QGIS process. Verify embedded datasets,
   source SVG bindings, layer/partition identities, feature counts and actual
   rendering. Compare logical source entities separately from cable segments;
   transform both datasets into the same CRS before evaluating offsets.
   Use `audit_visual_conversion` for independent source/geometry/field overlays;
   preserve its separate source snapshot and requested partition.
6. Call `start_review_server` for the run or selected partition and open the URL
   only after status=ready. `get_review_server_status` rechecks run/source/manifest
   identity and live GIS layers. The tool does not open a browser itself.
   Verify a live run-bound page and its SVG resources; a static batch index is
   not the conversion review console. Keep unresolved SVG points and all
   CONDITIONAL statuses visible in the delivery report.

## Source database and semantic revision workflow (package 0.4+)

Use `debug_mcp` to compare the running code and complete tool schema digests
before relying on this workflow. Older installed plugins may expose a different
implementation even when tool names look similar.

For extraction-only requests, require the running `get_capabilities` response
to advertise `semantic_database.workflow` including `export_source`,
`query_source_entities`, and `prepare_semantic_batches`, and confirm those
names in the actual MCP tool list. A plugin manifest version alone is not a
runtime capability check. If missing, report the installed-runtime mismatch
and reinstall the canonical runtime; do not imitate extraction in the client.

The Windows/Linux workspace and Android's connected server expose a
"语义提取" button. Its `semantic-extract` action runs `export-source` then
`semantic prepare` into fresh `source/` and `candidates/` directories. It
validates the output bindings but does not initialize a semantic store, commit
classification, compile a revision, or change baseline/GCP. When given these
artifacts, resume at step 2 below rather than exporting the drawing again.
The UI's "交给 AI 审阅" action prepares a draft only; respect its review-only
scope when the user sends it. A successful extraction is not accepted GIS
delivery or proof of complete CAD semantics.

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
   For an extraction/review-only request, report the source and candidate
   manifest paths, counts, unresolved/unsupported boundaries, and stop here.
   Do not infer permission for steps 4–6 from an extraction request.
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

Call `start_review_server` and open the verified local URL. The process belongs
to the current MCP connection; use `stop_review_server` for graceful shutdown.
Do not reuse another service occupying the port. `prepare_review_workspace`
only creates the edit store and a launch command; it does not start a server.
Use the ToC to move through source, mapping, registration, validation, and
delivery evidence. The console command is copyable; executing it creates a new
immutable run rather than modifying the current delivery in place.
An exported reviewed GCP profile can be supplied explicitly through
`run_conversion`'s gcp_profile argument; existing registration conflict and
source-binding gates still apply.

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
