---
name: convert-cad-to-gis
description: Inspect, convert, register, and review CAD/DWG projects with CAD2GIS MCP tools. Use for entity census, labels/styles, source-bound semantic mapping, geometry/topology/length validation, CRS/GCP registration, GeoPackage/QML delivery, or run-status diagnosis.
---

# Convert CAD to GIS

Use the CAD2GIS MCP tools as the orchestration surface. The Python package is
the only conversion implementation; never recreate geometry, topology, length,
or CRS algorithms inside the host agent or plugin.

## Required opening sequence

1. Call `get_capabilities` and report its configured filesystem roots.
2. Call `inspect_source` for a new DWG, or `inspect_run` for an existing run.
3. Keep source facts, semantic interpretation, geometry/topology/length checks,
   and coordinate accuracy as separate claims.
4. If a requested path is outside the configured roots, stop and give the exact
   `CAD2GIS_PROJECT_ROOTS` configuration needed. Do not broaden access silently.

## New drawing workflow

1. `inspect_source`
2. `bootstrap_project`
3. `prepare_ai_onboarding`
4. Select only observed layer/block/entity identifiers and supplied candidates.
5. `apply_ai_onboarding`
6. `validate_project`
7. `run_conversion`
8. `inspect_run`
9. `audit_run`

Provider-backed onboarding may use `auto_onboard_and_convert`, but its proposal
still passes deterministic compilation and admission gates.

## Evidence and repair workflow

1. Page evidence with `list_evidence_nodes`; read only needed nodes.
2. Use `list_visual_regions` and `resolve_visual_hit` as secondary evidence.
3. Read `list_registered_operations` before proposing a repair.
4. Select only returned endpoint/network candidate IDs.
5. Create and validate an ID-only decision pack.
6. Run `observe` before `assist` unless the user explicitly requested applying
   a registered repair.

Read [decision-contract.md](references/decision-contract.md) before creating or
diagnosing a decision pack.

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
- Review edits stay in the separate review workspace. A corrected GeoPackage is
  created only by running the returned conversion command into a new run.

## Interactive review

Call `prepare_review_workspace`, launch the returned command, and open its local
URL. Use the ToC to move through source, mapping, registration, validation, and
delivery evidence. The console command is copyable; executing it creates a new
immutable run rather than modifying the current delivery in place.

Before delivery, call `audit_run`. Require `audit_status=PASS`; report artifact
hash failures and layer-census mismatches separately. `source:not_replayable`
is a portability warning: archived GeoPackages remain reviewable, but the agent
must not claim it can create a calibrated rerun until the original DWG is
reattached and verified against the manifest SHA-256.
