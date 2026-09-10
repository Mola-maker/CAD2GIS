# OSM matching accuracy fixes — 2026-09-10

This change continues the supplied CAD2GIS 0.6.0 source snapshot. That snapshot
already contains `main` (`e820c4c`), real-DWG verification (`747d666`) and OSM
precision (`e401383`) work. The six supplied ZIPs were extracted independently;
the separate plugin archive exactly matches the plugin in the source archive.
No earlier checkout, drawing, reviewed configuration or delivery was overwritten.

## Corrected failure cases

1. **A crossing could hide the correct road.** Geometric-nearest lookup selected
   a perpendicular road 1 m away and rejected the sample, even when a compatible
   road was only 5 m away. Fitting now selects the nearest direction-compatible
   segment within a bounded radius. Final scoring and sealed evidence both use
   the same 25 m ground corridor and direction threshold of 0.7. Refinement uses
   a 100 m ground search radius. Unusable correspondences retain finite diagnostic
   distances and remain unsupported. Compatible geometric-nearest results need
   no additional neighbourhood search.
2. **Short OSM ways could exclude every correct hypothesis.** Whole-way ranking
   rejects length ratios below 0.2 and only proposes six ways per source route.
   Additional geometric anchors now vote for translations from two independent
   directions. Coincident endpoints let fragmented and unsplit geometry propose
   the same placement. These anchors make no physical-connectivity assertion;
   shared OSM node IDs remain the only authority for the topology evidence.
3. **Search shortcuts could create false confidence.** Distinct translations
   within a 5 m voting bin are retained at 0.05 m numerical seed resolution.
   They are never averaged into one seed. Final candidate merging remains
   0.25 m. Exhausting the additional 256-seed budget forces abstention with
   `landmark_hypothesis_budget_exhausted`. Canonical axis ordering prevents
   near-parallel route permutations from changing landmark observability.
4. **The iteration cap could create numerical duplicate candidates.** Taipa's
   competing fits were only 0.385 m apart after 20 iterations; three additional
   iterations brought them to the same optimum. The cap is now 100 iterations,
   with the unchanged 0.01 m step tolerance. Acceptance requires convergence;
   exhausted fits report `translation_refinement_not_converged`. Each retained
   candidate records its iteration count and final step. Kletek's two leading
   fits were already converged, so extending the budget does not approve them.

The global placement remains a translation through the existing unit/CRS base
operation. Native CAD vertices, shape, lengths, OSM topology and GCP approvals
are not changed by these fixes. The existing length-weighted observability,
Huber refinement and ambiguity gates remain active.

## Reproducible acceptance cases

The authored fixtures in `tests/test_osm_accuracy.py` contain known synthetic
translations; they are independent of the archived delivery positions.

| Case | Supplied 0.6.0 | Updated matcher |
| --- | --- | --- |
| Correct network split into 10 m OSM ways, distant initial hint | No recovered translation | Recovers `(1000, 2000)`; measured recovery error 0 m |
| Two exact fragmented networks, one coinciding with initial hint | Accepts one without discovering the other | Abstains; both alternatives retained |
| Perpendicular road at 1 m, compatible road at 5 m | Selects perpendicular way 11 | Selects compatible way 10 |
| Single-corner exact alternatives 0.5–8 m apart | Not a baseline success requirement | Abstains for all five separations |
| More than 256 fine seeds hide a second exact solution | New search stress case | Explicit budget-exhaustion abstention |
| Near-parallel axis permutations | New search stress case | Identical geometric anchors and placement behaviour |

These results establish the exercised software behaviour, not surveyed accuracy.
The frozen before/after synthetic receipts record implementation hashes.

## Real-vector replay

`tools/benchmark_osm_matching.py` reads four supplied baseline CABLE GeoPackages
and their offline OSM road GeoPackages. It checks each geometry header and CRS,
projects both layers to EPSG:3857, records input/code hashes, and verifies that
neither input files nor in-memory vectors changed. Run separate processes for
the supplied and updated implementation:

```sh
python tools/benchmark_osm_matching.py \
  --implementation-root /path/to/supplied-0.6.0 \
  --baseline-root /path/to/supplied-0.6.0/baselines \
  --report /path/to/results/before.json
python tools/benchmark_osm_matching.py \
  --implementation-root . \
  --baseline-root /path/to/supplied-0.6.0/baselines \
  --report /path/to/results/after.json
```

The CABLE layers are delivered specimens, **not independent geolocation ground
truth**. A lower OSM corridor residual does not certify a correct correspondence.
The optional perturbation mode measures translation equivariance relative to an
unperturbed fit; it cannot establish the original delivery's position accuracy.
Geometry-only reference layers remain identified as such.

The final replay of the reviewed implementation used byte-identical
input GeoPackages and identical projected vectors in both runs:

| Drawing | Baseline → updated support coverage | Baseline → updated acceptance |
| --- | --- | --- |
| Kletek | 90.12% → 94.47% | Accepted → abstained |
| Taipa | 92.72% → 99.99% | Accepted → accepted |
| Tinggede | 89.94% → 96.78% | Accepted → abstained |
| Darat Sekip SF | 100% → 100% | Abstained → abstained |

All updated rejections came from ambiguity, not the fine-seed cap. All 20
retained candidates converged. Kletek's best two translations are separated by
approximately 2.28 m, Tinggede's by 33.60 m and Darat's by 139.08 m. Taipa's
initial 0.385 m numerical duplicate was resolved by the convergence fix; its
final score gap is 0.382538. Distinct local optima do not by themselves prove
different road identities. The final replay took **170.2 s versus 53.2 s** for
the supplied algorithm on this machine: the broader search is more expensive.
Direction-compatible p95 corridor residuals did not uniformly improve and are
not directly equivalent to the old geometric-nearest residuals. No geographic
accuracy improvement is claimed for these four specimens.
Source hashes, implementation hashes and full per-candidate diagnostics are in
[OSM_ACCURACY_BENCHMARK_2026-09-10.json](OSM_ACCURACY_BENCHMARK_2026-09-10.json).

## Validation

- Complete imported-software regression: **1,145 passed, 11 skipped**. Skips
  cover opt-in complex DWGs, absent external evidence and platform capabilities.
- Final matching, benchmark, GCP, GEODATA and stage regressions after the final
  budget, convergence and correspondence fixes: **209 passed**, including the
  archived Taipa numerical-convergence regression.
- A generated DWG passed real LibreDWG reading, conversion, OSM matching,
  GeoPackage writing and SVG extraction. Its authored point `(12600100, -799800)`
  was recovered within `1.2e-10` projected units. Reader warnings about the
  generated DWG's duplicate handle/ENDBLK were retained in the execution log.
- Ruff, Python compilation, dependency compatibility, wheel and source-package
  builds passed. Independent exhaustive comparison covered 2,880 randomized
  correspondence queries; reviewed ambiguity/order/budget defects were fixed.

The full regression run preceded the final narrow budget/fast-path/convergence
fixes; the 209-test run and generated-DWG smoke exercised their final implementation.
These checks do not constitute a fresh nine-DWG conversion, UI/browser acceptance,
Linux installation, or independent surveyed-checkpoint validation.

## Search and accuracy boundaries

Landmark search uses at most 32 source anchors and 64 ranked coarse translation
bins; fine seeds are capped at 256. Other whole-way proposals remain bounded.
`search_complete=false` and the reported score gap refer to examined hypotheses,
not to every possible placement in the world. Repeated grids, insufficient OSM
coverage, unknown rotation/scale and cable/road offsets can still require manual
controls. No acceptance threshold was relaxed to make a drawing pass.

All OSM results retain `authority=relative_osm` and
`absolute_accuracy_verified=false`. Survey acceptance still requires separately
reviewed control points and independent checkpoints. Historical nine-DWG limits
remain documented in [UPGRADE_ACCEPTANCE_AND_NEXT.md](UPGRADE_ACCEPTANCE_AND_NEXT.md).

The correspondence query follows the [Shapely STRtree documentation](https://shapely.readthedocs.io/en/stable/strtree.html):
nearest ties are unordered, and bounding-box queries need exact distance
filtering. The implementation performs that filtering without introducing an
additional GEOS `dwithin` version requirement.
