# OSM route-source separation and residual review — 2026-09-11

## Reproduced defect

The matcher previously combined features explicitly classified `ROAD` with features classified `CABLE` in the same translation objective. A cable can have a real physical offset from the road; treating that offset as registration error can move correctly placed road geometry. Existing high coverage and confidence-gap checks do not detect this source-domain mix.

A regression was added and run before implementation. Two nonparallel ROAD routes use a known translation `(1000, 2000)`; two corresponding CABLE routes have a source offset of `(8, 6)` metres. The old implementation accepts `(995.9998925, 1997.0258379)`, with **100% corridor coverage** and **0.58108 confidence gap**, but its known-translation error is **4.98463 m**. The red result is recorded at `E:/branch_CAD2GIS/validation/osm-upgrade-red.xml`.

The corrected route selection fits only the explicitly classified ROAD geometry when valid ROAD routes exist. The same fixture recovers `(1000, 2000)` to numerical precision (`1.14e-13 m` in the recorded run). This is a synthetic known-translation regression; these numbers are not field or survey accuracy.

Numerical reproduction and selected/excluded source evidence: `E:/branch_CAD2GIS/validation/osm-road-priority-reproduction.json`. On that small fixture the mixed objective took 0.288 s and the road-priority objective took 0.067 s. These are single-run observations, not a general performance benchmark.

## Changes

- `_native_routes` selects valid, explicitly classified ROAD geometry before CABLE geometry. Drawings with CABLE only retain their previous proxy fit. Degenerate ROAD features do not suppress valid CABLE routes. If selected ROAD geometry is insufficient or unobservable, the matcher abstains; it does not quietly reintroduce offset cables.
- The sealed `route_selection` record retains selected class, valid feature counts, source handles, source entity keys when present, native geometry hashes, deduplicated route identities, and excluded CABLE references with the reason. Reversed duplicates retain their individual source references without gaining extra weight.
- `road_centerline_semantics_verified=false` records the limit: an upstream ROAD classification does not itself establish that a line is a surveyed road centerline. Explicit road classification is a better source-domain contract than mixing it with utility lines, but it still needs project review.
- `osm_evidence` binds the route-selection hash to the revision. Rehashing the outer profile after changing route selection without updating its evidence is rejected. Old profiles without the new evidence remain readable.
- Each selected route receives sample count, corridor coverage, median/p95 relative distance, outside-corridor count, incompatible-direction count, and nearest OSM way IDs. These diagnostics are available on the WGS84 CAD-route GeoJSON properties and are rebuilt during manual revisions. Routes without inspectable residuals have null coverage/distances, not fabricated zero error.
- During numerical reproduction, integer-coordinate hypotheses exposed a NumPy in-place floating correction cast error. Hypotheses now start with floating-point storage; a regression covers this path.

The existing shape-preserving translation model, Huber point-to-line fit, corridor and confidence thresholds, observation-rank checks, bounded search, repeated-network abstention, OSM topology rules and source geometry immutability remain in place. No threshold was relaxed. `authority=relative_osm` and `absolute_accuracy_verified=false` remain mandatory.

## Verification

Runtime: Windows, Python 3.12.13, current worktree `src` via `PYTHONPATH`.

```powershell
E:/branch_CAD2GIS/.venv-multipage-final/Scripts/python.exe -m pytest tests/test_osm_accuracy.py tests/test_osm_matching.py tests/test_osm_benchmark.py tests/test_osm_evidence.py tests/test_osm_pipeline.py tests/test_osm_topology_ingest.py tests/test_osm_anchor.py --basetemp=E:/branch_CAD2GIS/validation/osm-upgrade-final -q --tb=short --junitxml=E:/branch_CAD2GIS/validation/osm-upgrade-final.xml
```

Coverage includes the demonstrated ROAD/CABLE bias, explicit source selection and exclusions, deduplication lineage, insufficient ROAD structure, unchanged CABLE-only behavior, manual revision/replay, evidence tampering, integer-coordinate hypotheses, split OSM ways, repeated nearby networks, parallel and near-parallel structures, direction-compatible segment search, explicit OSM node topology, no-survey-authority constraints, and the frozen Taipa numerical convergence benchmark. The Taipa snapshot remains under `tests/data/taipa-osm-matching`; it was not rebaselined from a newly generated package.

Final result: **119 passed in 53.95 seconds**, no skips. JUnit: `E:/branch_CAD2GIS/validation/osm-upgrade-final.xml`. Scoped `git diff --check` passed (Git emitted only its existing Windows line-ending warnings).

## Delivery boundary

This change does not establish improved absolute placement on Tinggar, EMR29619 or every affected real drawing. It does not manufacture CAD roads from CABLE labels, adjust baseline/GCP data, infer topology at geometric crossings, change pipeline GCP priority, or claim the physical cable offset should be zero. Drawings that expose only CABLE routes still require independent source/GCP evidence to distinguish real cable-to-road offsets from registration errors. The new route diagnostics make sparse references and unsupported branches inspectable; they do not convert them into accepted control points.
