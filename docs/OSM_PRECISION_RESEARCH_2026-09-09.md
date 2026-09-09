# OSM alignment, drafting, and coordinate precision

## Engineering conclusion

CAD2GIS should resolve coordinate identity and DWG registration before using OSM
to estimate residual placement. Source geometry, displayed annotations, projected
grid distances, ellipsoidal distances, and independent positional accuracy are
different quantities. A visually convincing road overlay cannot establish the
accuracy of an engineering drawing.

The investigation of the September 9 remote updates reproduced three numerical
problems: vertex-density bias in automatic alignment, projected distances reported
as ground residuals, and legacy-datum reverse operations that did not exactly
invert the delivered two-dimensional coordinates. The verification branch also
introduced two DIMENSION annotation fallback regressions. These have targeted
fixes and regression tests on `codex/osm-precision`.

This report compares selected primary sources covering desktop GIS, browser OSM
editors, data validation, conflation, and coordinate libraries. It is a practical
engineering survey, not an assertion that every platform or program was tested.
External documentation was consulted on September 9, 2026. Platform capabilities
below are documented capabilities; only the CAD2GIS checks described under
validation were executed locally.

## Incoming branches and evidence

| Remote update | Findings | Local disposition |
| --- | --- | --- |
| `main`, `dfbee3a` → `e820c4c` | Corrects Taipa, Tinggede, and Darat source metadata/bindings; includes frozen registrations for Taipa/Tinggede and lint fixes | Pulled by fast-forward into local `main` |
| `fix/real-dwg-verification-20260909`, tip `747d666` | Real-DWG verification report, visible CABLE label rules, combined review output, settings/database fixes; two DIMENSION fallback regressions | Fetched and pulled onto local `codex/osm-precision`, which starts at this tip |

The saved Taipa profile reports check RMSE 4.89 m and maximum 8.68 m. Tinggede
reports check RMSE 7.48 m and maximum 12.83 m. These are the historical recorded
metrics, calculated under the earlier projected-grid residual convention. The
saved profiles retain `relative_osm` authority and
`absolute_accuracy_verified: false`. They were not rewritten to claim better
accuracy. Newly computed refinements identify their ellipsoidal residual method
explicitly, while old records retain their original hash and interpretation.

The incoming verification report also records Lamteh and Tinggar reverse-operation
errors above the existing 1e-6 m internal consistency threshold. Those reported
errors are submillimetre computational discrepancies; they do not quantify the
much larger possible error in physical location. Some linked evidence in that
report refers to another machine's `/home/jlu/...` paths and is not a portable
artifact available from this checkout.

## The quantities that determine precision

| Quantity | Meaning | Suitable evidence |
| --- | --- | --- |
| Native CAD coordinate and length | The source drawing's geometric facts after its explicit unit contract | DWG extraction, source hashes, curve facts and source lengths |
| Source-to-map coordinate operation | Unit handling, GEODATA registration, datum/projection conversion, then any declared residual registration | Source-bound profile and recorded operations |
| Numerical round-trip error | Whether inversion recovers the original coordinates under the implemented mathematical model | Forward/inverse comparison with the same immutable source coordinates |
| Forward-engine crosscheck | Agreement of GDAL and pyproj results | Independently evaluated engine paths; this is not independent survey control |
| Training residual | Fit discrepancy at points used to solve a registration | Training controls and a stated distance method |
| Check residual | Discrepancy at held-out references | Check controls excluded from fitting |
| Absolute positional accuracy | Agreement with a sufficiently accurate independent reference in the required datum/epoch | Surveyed or otherwise authoritative controls and an explicit acceptance specification |

PROJ documents Web Mercator as a spherical calculation using the ellipsoid's
semi-major axis. Consequently its coordinate metres must not be assumed to be
ground-distance metres. The effect grows with latitude, and a cosine correction
is still an approximation to ellipsoidal distance. For manual point residuals,
CAD2GIS now transforms both positions to geographic coordinates and evaluates
the WGS84 inverse geodesic. [1][2]

PROJ's transformation documentation distinguishes projections from datum
transformations and describes operations assembled into pipelines. pyproj exposes
operation definitions, declared accuracy, and transformer controls. These
facilities support reproducibility and diagnosis; an operation's declared
accuracy does not verify a particular DWG's actual placement. [3][4]

## Platform and program comparison

| Tool or family | Useful documented capability | Application to CAD2GIS | Limit for this task |
| --- | --- | --- | --- |
| QGIS editing tools | Layer-specific snapping, topological editing, vertex and advanced digitizing tools [5] | Inspect correspondence and edit a deliberate derivative with suitable snapping rules | A snapping tolerance is an editing choice, not a positional accuracy guarantee |
| QGIS Georeferencer | Control-point registration with several transformation models and residual reporting [6] | Compare residual patterns and choose a justified registration model | More flexible models can fit controls while deforming engineering geometry |
| JOSM Improve Way Accuracy | Efficient movement, addition, and deletion of way nodes [7] | Useful when the task is to improve an OSM way itself | Moving CAD cable vertices to OSM roads changes the engineering source |
| JOSM Validator | Invalid-data checks and customizable rules [8] | Inspire explicit issue categories and reviewable validation | A valid OSM topology can still be geographically displaced |
| JOSM Conflation plugin | Match features and transfer/merge data between reference and subject datasets [9] | Useful for reviewed correspondence candidates | Correspondence and geometric replacement need separate approval/evidence |
| iD | Browser editor with severity-based validation and structured tagging data [10][11] | Useful model for understandable warnings and maintained OSM presets | Tag validity does not establish CAD registration |
| Rapid | Browser editor integrating open geospatial data and assisted mapping with integrity checks [12] | Additional candidate/reference inspection where relevant coverage exists | Assisted suggestions remain reference candidates requiring review |
| Osmose | Analysis backend detecting OSM issues and comparing with external data [13] | Investigate whether apparent drafting issues originate in reference data | Its findings need interpretation for the actual feature and locality |
| Keep Right | OSM consistency/error detection [14] | Additional network/topology checks around a disputed match | No replacement for datum verification or surveyed controls |
| ArcGIS Pro Transform/rubbersheet | Displacement links, anchors, and local interpolation to align features [15] | A useful comparison for deliberate correction of a derived dataset | Alters spatial geometry; inappropriate as an implicit CAD preservation step |
| PROJ / pyproj / GDAL | Coordinate operations, inverse geodesics, operation inspection [1–4] | Core numerical foundation and diagnostics | Correct numerical conversion cannot repair incorrect source CRS evidence |
| Overpass / Nominatim | OSM feature queries and place search [16][17] | Obtain bounded, reproducible road snapshots or initial locality hints | Place centres are coarse; public-service availability and coverage vary |

The recommended working combination is CAD2GIS for source-bound conversion,
QGIS for detailed GIS inspection, and JOSM/iD plus OSM quality tools when the
reference map itself needs investigation. This is an engineering recommendation
based on the capabilities above, not a benchmark establishing one universal
winner. Installing another editor alone will not fix a wrong coordinate profile.

## Diagnosing drift before changing geometry

A nearly constant displacement over a compact drawing is compatible with an
incorrect origin or an omitted registration translation. Check DWG GEODATA
design/reference points first, then evaluate paired controls. A displacement that
changes direction across the drawing suggests rotation, a unit/scale error,
projection differences, or spatially varying reference error. A translation
cannot resolve every such pattern.

If the source is close to roads but consistently offset to one side, first
establish what each feature represents. A cable route, trench, roadside pole,
building outline, and road centreline are distinct physical objects. Matching
them as though they were the same line can create apparent improvement while
making the engineering result less faithful. OSM good-practice guidance favours
verifiable observations and cautions against modifying data solely to match a
particular renderer. [18]

Training points should cover the drawing rather than one corner. Independent
check points should cover the remaining area and remain excluded from fitting.
Two distinct training points support the current translation workflow, but do
not by themselves establish spatially representative accuracy. If an apparently
perfect fit has no independent controls, the accuracy evidence is incomplete.

For a drawing that contains separate layout regions, assess each region's
coordinate registration separately. Keep region identities in the saved matching
record and reproduce each refinement from original CAD coordinates. Applying a
correction from one local panel to another can create large, systematic drift.

## Implemented corrections

### Real Lamteh coordinate profiles

The two local Lamteh DWGs were read independently with the current LibreDWG
adapter, and each file's SHA-256 matched its existing project binding. Both
provide `Indonesian1974.UTM-46N` and a GEODATA registration to EPSG:23846. Their
checked-in profiles had incorrectly declared EPSG:3857. The source CRS and
registration are now copied from the observed DWG metadata; target EPSG:3857 and
the existing reviewed coordinate scale are retained. Full reader inventories
refresh both profile and mapping hashes without changing semantic feature gates.

| Real source | Model vertices evaluated | Previous reverse round-trip maximum | Corrected inverse maximum | Forward engine discrepancy |
| --- | ---: | ---: | ---: | ---: |
| Lamteh SF | 449 | 0.000028892214 m | 0.000000002837 m | 0 m |
| Lamteh main | 6,388 | 0.000028919486 m | 0.000000003492 m | 0 m |

The [source-bound audit](OSM_PRECISION_LAMTEH_AUDIT_2026-09-09.json) records exact
source and inventory hashes, census counts, registration values, and numerical
results. Applying the correct nominal source registration prevents the wrong
CRS interpretation. It still does not establish that a design drawing coincides
with today's ground features or with every OSM road.

### Arclength sampling

Previously, every CAD segment received at least two samples. Adding 99 collinear
vertices to the first leg of a 200 m L-shaped route changed its total samples
from 40 to 220. That made tessellated geometry vote more strongly even though its
shape and total length were unchanged.

Sampling now advances at evenly distributed distances along the whole route.
Each sample retains the direction of its containing segment. Zero-length edges
are ignored. Redundant vertices therefore do not change the positions or count
of the route's samples. The automatic acceptance thresholds and translation-only
model remain in place. Route segmentation into separately classified features
and semantic correspondence remain separate issues; this change does not claim
invariance under every possible restructuring of a drawing.

### Ground residuals

An explicit 30 m eastward ground offset at latitude 60 degrees previously appeared
as approximately 59.85 m in manual-control diagnostics. That could incorrectly
trigger the 40 m review threshold. New `error_m`, RMSE, maximum residual, and the
threshold comparison use WGS84 ellipsoidal geodesic distance. The former grid
quantity remains available as `grid_error_m` on each residual.

The quality record includes
`residual_distance_method: WGS84_ellipsoidal_geodesic`. The fit is still a weighted
translation in the existing fixed map operation. Changing the residual
measurement does not silently change the fitting model, apply rotation, or
certify the controls as survey observations. The automatic matcher's
latitude-scaled corridor score remains a relative matching heuristic rather than
an exact ellipsoidal accuracy measurement.

### Two-dimensional inverse consistency

Some datum transformations involve three-dimensional steps followed by a
two-dimensional output. Starting a separately evaluated reverse operation at
zero height need not recover the original XY exactly after transformed height
has been discarded. This was reproduced for EPSG:23846 and EPSG:21148.

The corrected inverse starts from the CRS reverse result and uses a bounded
local derivative iteration to invert the unchanged forward XY mapping. Both
scalar and batch paths, including GEODATA registration, share the correction.
Nonfinite, singular, and nonconvergent cases fail explicitly. Forward delivery
coordinates and the independent forward-engine comparison remain unchanged.
Metadata identifies the inverse method. The existing 1e-6 m source round-trip
gate was not relaxed.

This improves numerical reversibility and operator coordinate capture. It does
not improve the physical accuracy of the underlying datum transformation or
compensate for missing survey information.

### Original DIMENSION annotations

The incoming reader attempted to obtain visible dimension text from an anonymous
block. If no block text existed, it discarded an explicit entity annotation such
as `42m` and could substitute the raw measurement `42.4`. If a named block was
missing, ezdxf returned `None`, which the reader attempted to iterate, losing the
measurement through its error path.

The reader now checks for a missing block and uses the explicit entity text when
rendered block text is unavailable. Rendered annotation text retains priority;
the raw measurement remains the final fallback. Tests cover each case. This
preserves the original displayed length while keeping calculated geometric
distances separate.

## Reproducibility and operating conventions

Use exact source hashes and the observed DWG metadata to bind each project. A
profile copied from another drawing is insufficient even if its feature counts
look plausible. Record native units explicitly: `$INSUNITS` alone can conflict
with the drawing's effective coordinate scale and must be reconciled with source
evidence and the reviewed unit contract.

Keep road snapshots with their original identities, content hashes, and
attribution. Overpass supports returning way geometry and node references; an
offline snapshot makes the matching input repeatable even as OSM changes. A
snapshot's hash establishes identity, not completeness or accuracy. [16]

Nominatim should provide initial locality, not a solved drawing registration.
Its public policy requires identifiable requests, caching, and an overall
maximum request rate of one per second. Multi-process deployments need an
application-wide limit rather than relying on one worker's timer. OSM-derived
reference data should retain attribution and the applicable licence information.
[17][19]

Preserve original DWGs and published runs. Write new output for each refinement
and record the parent matching/run identities. A QGIS display correction is
different from corrected coordinates in a GeoPackage: reopening or exporting
outside the saved project transform context must not be described as equivalent
without verification. Precision checks must also inspect layer validity, source
geometry/length preservation, labels, and checkpoint residuals.

## Validation and remaining limits

The final real-DWG delivery conversions were stopped on request before completion.
The first Lamteh SF attempt had correctly rejected publication because production
code changed during its run; the restarted SF conversion and the main-drawing
conversion were then interrupted. No completed real-DWG end-to-end delivery is
claimed for this change. The completed real-source coordinate audit below and
the successful generated-DWG conversion remain valid, distinct evidence.

The initial precision regressions were observed before the implementation fix.
The relevant conversion, OSM, coordinate-domain, datum, reader, and delivery
suites subsequently passed 140 tests. Review/GCP/geometry integration tests
passed 100 tests, and all 8 desktop-agent tests passed outside the restricted
Windows context after fixture credential encryption was blocked by DPAPI in the
sandbox. A final 66-test regression run also passed after the real profile
corrections and three additional nonfinite-inverse test cases. These counts
describe overlapping runs and should not be added as distinct test cases.

A generated DWG was converted through the real LibreDWG reader, automatic OSM
matching against its explicit offline fixture, GeoPackage writing, and SVG
candidate extraction. The known output point was
`(12600100.0, -799799.9999999999)`, compared with the authored expected point
`(12600100.0, -799800.0)`. This demonstrates the exercised software path, not a
survey result for a real drawing. Evidence is in
`tmp/osm-precision-synthetic/conversion-smoke.json`.

Independent review tested 2,500 valid coordinate samples across the relevant
Indonesian CRS paths without inverse convergence failures, with maximum source
round-trip errors of roughly 2.3–4.4 nanometres. A reviewed feet-unit case also
passed. This is additional numerical coverage, not a replacement for regression
tests or real-drawing verification.

No external map edits were uploaded. Real-drawing absolute acceptance remains
dependent on appropriate independent controls and an agreed positional
tolerance. Sparse or ambiguous OSM geometry, historical imagery offsets, unknown
local rotations, and CAD design-versus-as-built differences cannot be removed by
rounding coordinates or forcing a high matching score. The software should
retain abstention and review states when the evidence is insufficient.

## Sources

1. PROJ. [Web Mercator / Pseudo Mercator](https://proj.org/en/stable/operations/projections/webmerc.html). Stable documentation, accessed 2026-09-09.
2. pyproj. [Geod](https://pyproj4.github.io/pyproj/stable/api/geod.html). Version 3.7.2 documentation, accessed 2026-09-09.
3. PROJ. [Geodetic transformation](https://proj.org/en/stable/usage/transformation.html). Stable documentation, accessed 2026-09-09.
4. pyproj. [Transformer](https://pyproj4.github.io/pyproj/stable/api/transformer.html). Version 3.7.2 documentation, accessed 2026-09-09.
5. QGIS. [Editing](https://docs.qgis.org/3.44/en/docs/user_manual/working_with_vector/editing_geometry_attributes.html). Version 3.44 documentation, accessed 2026-09-09.
6. QGIS. [Georeferencer](https://docs.qgis.org/3.44/en/docs/user_manual/managing_data_source/georeferencer.html). Version 3.44 documentation, accessed 2026-09-09.
7. JOSM. [Improve Way Accuracy](https://josm.openstreetmap.de/wiki/Help/Action/ImproveWayAccuracy). Project documentation, accessed 2026-09-09.
8. JOSM. [Validator](https://josm.openstreetmap.de/wiki/Help/Validator). Project documentation, accessed 2026-09-09.
9. OpenStreetMap Wiki. [JOSM Conflation plugin](https://wiki.openstreetmap.org/wiki/JOSM/Plugins/Conflation). Project documentation, accessed 2026-09-09.
10. OpenStreetMap iD. [Architecture and validation](https://github.com/openstreetmap/iD/blob/develop/ARCHITECTURE.md). Project repository, accessed 2026-09-09.
11. OpenStreetMap. [iD tagging schema](https://github.com/openstreetmap/id-tagging-schema). Project repository, accessed 2026-09-09.
12. Rapid. [Project README](https://github.com/facebook/Rapid). Project repository, accessed 2026-09-09.
13. Osmose QA. [Analysis backend](https://github.com/osmose-qa/osmose-backend). Project repository, accessed 2026-09-09.
14. OpenStreetMap Wiki. [Keep Right](https://wiki.openstreetmap.org/wiki/Keep_Right). Project documentation, accessed 2026-09-09.
15. Esri. [Rubbersheet features](https://doc.esri.com/en/arcgis-pro/latest/help/editing/rubbersheet-features.html). ArcGIS Pro documentation, accessed 2026-09-09.
16. OpenStreetMap Wiki. [Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL). Project documentation, accessed 2026-09-09.
17. OpenStreetMap Foundation. [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/). Current policy, accessed 2026-09-09.
18. OpenStreetMap Wiki. [Good practice](https://wiki.openstreetmap.org/wiki/Good_practice). Community guidance, accessed 2026-09-09.
19. OpenStreetMap Foundation. [Copyright and License](https://www.openstreetmap.org/copyright). Current attribution/licence information, accessed 2026-09-09.
