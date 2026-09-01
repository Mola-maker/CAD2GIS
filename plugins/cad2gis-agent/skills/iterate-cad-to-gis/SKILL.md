---
name: iterate-cad-to-gis
description: Improve an unsatisfactory CAD2GIS conversion from user-provided visual and language evidence through bounded, source-bound candidate runs. Use when a user says the first conversion looks wrong, supplies screenshots or annotated regions, corrects semantic interpretation, or asks the plugin to retry and learn from review.
---

# Iterate CAD to GIS

Skill contract: `cad2gis.iterate_skill.v1`.

Use the CAD2GIS MCP iteration tools to adapt project interpretation and
configuration. Do not edit plugin code, source CAD geometry, or an existing run
as part of an iteration.

## Loop

1. Call `inspect_run`, then `start_feedback_iteration` on the unsatisfactory
   immutable run. Use a small finite budget; three iterations is the default.
2. Call `record_iteration_feedback`. Preserve the user's words as
   `observation` and convert their desired correction into a concrete
   `expected_outcome` without adding facts. Bind visual evidence either to a
   region returned by the run or to a user image path. Use Evidence Graph IDs
   only when they have already been observed.
3. Call `prepare_iteration_context`. Follow its category routes and inspect the
   smallest relevant evidence slice. Pixels and screenshots are secondary
   evidence; vector facts, source hashes, candidate IDs, and validation gates
   remain authoritative.
4. Make the smallest source-bound change through the existing constrained
   workflow: scene plan, semantic decision pack, registered network operation,
   reviewed mapping/style configuration, or independently validated GCP
   profile. Never invent coordinates, WKT, lengths, CRS, graph IDs, or source
   entities.
5. Run the canonical pipeline into a new run directory. Call
   `evaluate_iteration_candidate` with the feedback IDs addressed, a concise
   change summary, and the changed configuration/decision artifacts.
6. Present the new visual artifacts and deterministic comparison to the user.
   Call `decide_iteration_candidate` with `accept` only after explicit user
   confirmation and only if the candidate is eligible. Otherwise record
   `reject` or `revise`, add the new evidence, and repeat within the budget.
7. After acceptance, call `export_iteration_learning` into the project. Pass
   that registry to `prepare_ai_onboarding` on later retries for the same source.

## Boundaries

- Learning is source-bound, suggestions-only context. It is never silently
  applied to another DWG.
- A visually better candidate cannot override a regressed geometry, topology,
  length, coordinate, entity-conservation, or run-status gate.
- Candidate runs and accepted lessons are content-addressed and immutable.
- Stop when the budget is exhausted, evidence is insufficient, or a required
  change falls outside the registered operations. Report the precise blocker
  instead of widening model authority.
- The loop upgrades the project's understanding. Changes to plugin code or
  global policy require a separate reviewed development task.
