# CAD2GIS v3 architecture

This directory contains the architecture-v3 backend and reviewed compatibility
pack for the APD Hutabohu drawing.  The public, canonical entrypoint is the
installable `cad2gis` package under `src/cad2gis`; `experiment/` is not a
second CLI.  The backend replaces the legacy geometry-repair workflow with a
deterministic evidence-first pipeline.

## Scope and source contract

- Authoritative input: `APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`
- Source SHA-256: `557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557`
- DWG-declared nominal source CRS: `EPSG:3857` (`WGS84.PseudoMercator` in the DWG)
- Drawing units: `INSUNITS=6` (metres)
- Delivery CRS: `EPSG:9481` (SRGI2013 / UTM zone 51N)
- Conversion is direct `EPSG:3857 -> EPSG:9481`; no intermediate WGS 84
  longitude/latitude geometry is created.

The source profile, reviewed semantic registry and ground-control contract are
versioned in `config/apd_source_profile.json`,
`config/apd_mapping_registry.json` and `config/apd_gcp_profile.json`.
Conversion stops if the source hash, entity census, route graph, span
partition, annotation assignment, CRS round-trip regression, or an enabled
ground-control gate does not match its reviewed profile.

## Public entrypoint and project-pack boundary

All users, automation and QGIS integrations enter through `cad2gis` or
`cad2gis.pipeline`.  `src/cad2gis` owns argument parsing, project discovery,
backend deployment checks, the GCP operator adapter and the read-only
verification matrix.  The architecture-v3 implementation remains deployable
as an importable `cad2gis_v3` backend, through `CAD2GIS_BACKEND_PATH`, or from
this editable checkout.  A wheel does not silently bundle or discover an
arbitrary `experiment/` tree; `cad2gis doctor --deep --strict` reports whether
the selected deployment is actually ready.

For a different CAD, the supported onboarding sequence is `cad2gis inspect`,
`cad2gis bootstrap`, human review, `cad2gis validate`, then
`cad2gis convert`.  Inspection records facts but does not infer GIS meaning,
units or CRS.  Bootstrap output is source-hash-bound and always `draft`; it
cannot convert until unit/CRS, reader coverage, curve, semantic, style,
topology, segment and unsupported policies have been reviewed with provenance.
Validation checks those bindings but never approves them.  The APD profiles in
this directory therefore remain a compatibility project pack for this one DWG
hash, not a template or evidence of cross-CAD generalisation.

Public conversion is fail-closed.  Malformed/unsupported reader facts,
unreviewed unit scaling or local registration, lost curve primitives, stale
source bindings, topology/length non-closure and semantic/style coverage outside
a reviewed allowlist prevent publication.  A policy-approved unsupported fact
remains structured evidence; an `abstain` remains an unresolved choice.  Neither
state may be silently dropped or promoted to a guessed feature.

The CRS and registration policy follows the published definitions rather than
map appearance: [EPSG:9481](https://epsg.io/9481) is SRGI2013 / UTM zone 51N
for Indonesia north of the equator between 120E and 126E; the
[QGIS Georeferencer documentation](https://docs.qgis.org/3.44/en/docs/user_manual/managing_data_source/georeferencer.html)
distinguishes Helmert/similarity-style rotation and uniform scale from a more
general affine transform; and Li and Briggs' [topological point-pattern
matching paper](https://www.cartogis.org/docs/proceedings/2006/li_briggs.pdf)
supports using intersection topology, scaled distances and angles to propose
control pairs under a similarity transform.  Those references justify the
candidate and model order; only reviewed project controls can establish this
drawing's absolute position.

## Pipeline boundaries

```text
direct AutoCAD DWG extraction
  -> immutable, loss-aware all-object inventory
  -> versioned native curve facts and fingerprints
  -> reviewed semantic classification
  -> independent route/support/optical evidence graphs
  -> nominal direct CRS transformation
  -> optional deterministic GCP residual calibration
  -> nine-layer delivery warehouse (eight base contractual layers plus
     normalized CABLE_SEGMENT business detail)
  + separate evidence warehouse and QGIS styles

optional operator-only review lane (never imported by conversion)
  deterministic DWG IR + semantic/span/topology evidence
  -> model-safe semantic-anchor context and external target candidates
  -> content-addressed review bundle
  -> provider-neutral review application port
     -> DeepSeek direct adapter (first verification)
     -> New API OpenAI-compatible gateway adapter (demo/runtime)
  -> human/LLM select, rank, or abstain proposal
  -> local strict domain validation
  -> validated audit artifact
```

The conversion reports three validation domains separately, although some
implementation gates share the same pipeline orchestration.  **Source geometry**
validation covers the immutable DWG inventory, native curve facts and
fingerprints, entity census, and native-length conservation.  **Topology**
validation covers route/support/optical graphs, span partition and membership,
and reviewed attachment/port relations; geometry proximity alone does not pass
this domain.  **Coordinate and absolute-accuracy** validation covers the
nominal CRS operation and numerical round-trip regressions, while absolute
ground accuracy requires a separately enabled, reviewed GCP residual model with
independent check points.  A pass in one domain is not evidence that either of
the other two domains passed, and the current documentation does not claim
three physically independent validator executables.

The implementation is split by responsibility:

- `autocad_reader.py`: direct AutoCAD extraction only; it does not assign GIS
  meaning.  The primary bulk protocol remains backward-compatible with
  17/21/24/29/30-column records and has a versioned curve-facts extension.  It
  retains ordered WCS vertices, per-vertex bulges, elevation,
  normal/extrusion, closed state, primitive parameters, owner handles, block attributes,
  insert scale, DIMENSION measurement and text override, AutoCAD native curve
  length, effective layer style, block-definition children, raw properties and
  explicit unsupported-capability states.  CoreConsole failure stops by
  default; the semantically different COM fallback is available only through
  the explicit `CAD2GIS_ALLOW_COM_FALLBACK=1` opt-in and records that status.
- `cad2gis_v3/ingest.py`: validates the immutable DWG source contract and
  produces the loss-aware reader inventory.  The authoritative APD read
  contains 9,391 records, all from
  `autocad_core_console_bulk / authoritative`.
- `cad2gis_v3/semantics.py`: executes versioned field rules and assigns
  same-family CAD annotations with a deterministic maximum-cardinality,
  minimum-total-distance one-to-one solver.  Unsupported meaning is left
  unavailable.
- `cad2gis_v3/topology.py`: builds source route walks, segment occurrences,
  physical span evidence and attachment candidates without changing source
  route coordinates.
- `cad2gis_v3/ports.py`: transforms block-definition geometry into drawing
  space and records candidate connection ports.  It never inserts a point into
  a cable route.
- `cad2gis_v3/georef.py`: performs the nominal direct CRS operation.  This is
  a reproducible coordinate operation, not evidence of absolute ground
  accuracy.  Projected coordinates and length metrics are enriched once so
  evidence and delivery consume the same values and provenance.
- Ground-control calibration is a separate residual stage after the nominal
  CRS operation.  It reads only the versioned GCP profile, fits an allowed
  deterministic model, applies the accepted model to derived delivery
  geometry, and retains native and nominal coordinates as immutable evidence.
- `cad2gis_v3/calibration.py`: strictly validates the DWG-bound GCP profile,
  fits translation/similarity/affine target-space residual models, separates
  training from independent checks, uses centred and scale-normalised design
  geometry gates, rejects non-finite, near-coincident, ill-conditioned,
  degenerate or reflected solutions, and returns an immutable calibration result.
- `cad2gis_v3/spatial_coverage.py`: derives the v4 drawing extent from all
  valid geospatial plan-domain model-space `SourceEntity` vertices, including
  classified and unclassified entities, then evaluates drawing-relative
  numeric coverage gates from the source profile.  Paper/block-definition,
  legend/title and non-materialized HATCH geometry are excluded.  A human
  "reviewed" flag cannot override a clustered training set or independent
  checks that cover too little of the contractual feature footprint; check
  controls must also span the configured two-dimensional baseline and hull.
- `cad2gis_v3/warehouse.py`: writes the eight base contractual delivery layers
  plus the normalized `CABLE_SEGMENT` business-detail layer (nine physical
  delivery layers).  `CABLE_SEGMENT` is built directly from immutable parent
  CABLE vertex chains and enriched span metrics; it is not read back from the
  evidence GeoPackage.
- `cad2gis_v3/evidence.py`: writes audit, provenance, lineage, topology and
  unresolved evidence to a different GeoPackage.  Its all-object tables include
  `cad_entities` (9,391), `block_instances` (362),
  `annotation_carriers` (2,292) and `cable_span_metrics` (139).  The
  `cable_span_segments` spatial evidence layer remains the audit counterpart
  to delivery `CABLE_SEGMENT`; it exposes the same 139 ordered spans directly
  in EPSG:9481 for provenance inspection.
- `cad2gis_v3/styles.py`: writes portable QGIS QML files from effective CAD
  colour/style evidence and registers each QML as the default style inside the
  delivery GeoPackage.  QGIS geometry simplification is disabled in every
  generated style so rendering cannot visually remove short source segments.
- `cad2gis_v3/pipeline.py`: enforces conservation and regression gates and
  writes an auditable manifest.  The manifest exposes a bounded validation
  summary for CABLE curve-fact coverage and fingerprint-set hash, source graph,
  native/span length closure, calibration status and always-on lineage; the
  content-hashed evidence GeoPackage remains authoritative.  Reproducibility
  means stable semantic results for identical reviewed inputs.  Generated
  GeoPackages canonicalize writer-clock metadata to the fixed
  `1970-01-01T00:00:00.000Z` timestamp in deterministic primary-key order and
  run `VACUUM`; this supports byte-level reproducibility without changing
  feature, audit, extent, CRS or style semantics.  Byte identity remains
  contingent on the declared GDAL, SQLite, Python and operating-system
  toolchain.
- `cad2gis_v3/gpkg_metadata.py`: performs that metadata normalization for
  delivery, evidence and embedded-style GeoPackages; it touches generated
  timestamps only, then rebuilds the SQLite file so superseded clock values do
  not remain in unused cells.
- `cad2gis_v3/curation.py`: owns only the content-addressed review domain,
  proposal schema, forbidden-fact policy and local validation.  It contains no
  provider configuration and performs no network access.
- `cad2gis_v3/curation_service.py`: is the offline application layer.  It
  exposes one sanitised task through a provider-neutral port, then sends the
  returned JSON through the same local proposal validator used for a manual
  review.
- `cad2gis_v3/curation_providers/`: contains the provider port, environment
  profiles and the single OpenAI-compatible HTTP transport.  DeepSeek and New
  API are profiles/capabilities of this boundary, not dependencies of DWG extraction,
  topology, CRS, length or publication code.
- `cad2gis_v3/semantic_anchor.py`: builds a coordinate-free model context and
  ID-only export boundary for optional landmark assistance.  Stable anchors
  bind the DWG hash/entity/handle; separate facts hashes cover labels and
  topology.  Operator-side candidate objects may retain additional facts, so
  they still pass the local binding validator before model context or export.
  A model may only select/rank/abstain over pre-existing candidate IDs, and a
  human approval is required before an ID-only GCP binding can be exported.

The delivery GeoPackage, evidence GeoPackage, QML sidecars, style manifest and
run manifest form one publication bundle.  They are first written, closed,
validated and hashed in a same-volume temporary run directory.  The completed
directory is then published by directory rename; replacement failure restores
the previous directory.  On Windows an output held open by QGIS therefore
fails before any current artifact is replaced, instead of leaving mixed run
versions.  The run manifest is present only in a completed staged bundle and
records `publication.status=complete`.

The run manifest implementation fingerprint has the explicit scope
`production-conversion`.  Its versioned file allow-list excludes all optional
curation and provider modules.  Changing a prompt, DeepSeek profile or New API
adapter therefore cannot masquerade as a production conversion change;
changing any declared conversion module must change the production fingerprint.

`gcp_capture.gpkg`, GCP diagnostic JSON, optional review bundles and any
proposal/audit JSON are operator sidecars.  They are intentionally created
after publication and do not rewrite the already-hashed run manifest.

## Optional offline curation lane

There is no ReadCAD product dependency.  LLM review starts only after AutoCAD
has deterministically produced immutable facts; the model never parses binary
DWG and is never imported by the canonical production conversion path.
Review bundle schema `cad2gis.review_bundle.v2` represents all
9,391 objects.  Every object belongs to exactly one of 514 coherent inventory
review batches (`untasked_objects=0`, `multiply_tasked_objects=0`).  The final
APD bundle contains 1,635 existing candidates and 1,624 tasks, including the
inventory, feature and annotation-assignment review domains.

The model-visible typed facts include text, attributes, layer/type/block/style,
owner/backend state, AutoCAD native length, DIMENSION measurement, candidate
distance, CABLE totals and ordered SPAN metrics.  Coordinate arrays, geometry
payloads, transforms and CRS payloads remain hidden behind full-fact and
geometry hashes.  Numeric measurements are read-only evidence: a proposal can
only select/rank existing candidate and evidence IDs, or abstain.  It cannot
create or rewrite an ID, label, attribute, layer, coordinate, geometry, CRS,
length, SPAN value or topology relation.  A proposal never feeds conversion
directly; any useful mapping proposal must be human-reviewed and promoted into
a new versioned registry before a fresh deterministic run.

Create and validate the sidecar from `experiment/py_scripts`:

```powershell
python curate_v3.py bundle `
  --evidence '<RUN_DIR>\apd_evidence.gpkg' `
  --dwg '..\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg' `
  --out '<RUN_DIR>\cad_review_bundle.json'
python curate_v3.py schema --bundle <bundle.json> --task-id <existing-task-id> --out <schema.json>
python curate_v3.py validate --bundle <bundle.json> --proposal <proposal.json> --out-audit <audit.json>
```

The review application follows a hexagonal boundary:

```text
ReviewBundle + task-bound Proposal JSON Schema
                    |
                    v
             ReviewProvider port
                    |
          OpenAI-compatible transport
             /                 \
DeepSeek direct profile      New API gateway profile
 json_object + local gate    explicit capability + local gate
```

The first verification profile is DeepSeek direct.  It defaults to the current
official `https://api.deepseek.com` endpoint, model `deepseek-v4-flash`, and
JSON Object output.  The API key is required at runtime and is never written to
a profile, request audit or exception.  Run one content-bound task with:

```powershell
$env:CAD2GIS_LLM_PROVIDER = 'deepseek'
$env:DEEPSEEK_API_KEY = '<runtime-secret>'
python curate_v3.py cloud --provider deepseek `
  --bundle <bundle.json> --task-id <existing-task-id> `
  --out-proposal <proposal.json> --out-audit <audit.json>
```

For the demo, switch only the provider profile.  New API has no universal
host, model, or structured-output guarantee because those depend on the
deployed gateway channel.  Its base URL and model are therefore mandatory and
its capability must be declared as `json_schema` or `json_object` after a
channel-level probe:

```powershell
$env:CAD2GIS_LLM_PROVIDER = 'new_api'
$env:NEW_API_BASE_URL = 'https://your-new-api.example/v1'
$env:NEW_API_API_KEY = '<runtime-secret>'
$env:NEW_API_MODEL = '<gateway-model-id>'
$env:NEW_API_CAPABILITY = 'json_schema'
python curate_v3.py cloud --provider new_api `
  --bundle <bundle.json> --task-id <existing-task-id> `
  --out-proposal <proposal.json> --out-audit <audit.json>
```

Provider switching changes no domain or conversion code.  Unsupported
structured output fails closed; it never falls back to free text.  Even when a
gateway claims strict JSON Schema support, the returned object is treated as
untrusted and must pass the local task-bound validator.  This lane is for
organising complete CAD evidence and proposing exception decisions, not
for manufacturing missing CAD facts.

The checked-in `config/llm_provider.env.example` contains only variable names
and safe defaults; secrets stay in the runtime environment.  The profiles are
based on the official [DeepSeek API quick start](https://api-docs.deepseek.com/),
[DeepSeek Chat Completions contract](https://api-docs.deepseek.com/api/create-chat-completion/),
and [New API Chat Completions contract](https://docs.newapi.pro/en/docs/api/ai-model/chat/openai/createchatcompletion).

## Source-profile v4 spatial coverage

The source-profile v4 coverage extent is the complete geospatial plan domain,
not the classified feature subset.  It is built from every valid finite vertex
of every model-space `SourceEntity` whose CAD role is `model` or `plan`, whether
that entity is classified or unresolved.  Paper-space/layout coordinates and
block-definition coordinates are excluded.  Model-space `style_legend` and
`title_block` roles are excluded, as are `HATCH` records whose boundary
geometry is not materialized by the reader.  Contradictory layout metadata or
invalid plan coordinates fail closed instead of silently shrinking the extent.
For APD this canonical extent contains 27,041 vertices and is approximately
`5513.460 m x 2830.612 m`.

Active training and independent check controls must both lie inside this
drawing bounding box.  The v4 policy requires training to cover at least 60%
of each drawing axis and 20% of its bounding-box area, permits at most 5% of
drawing vertices outside the training bounding box, and at most 5% outside the
training convex hull.  Independent checks must span at least 25% of the
drawing diagonal and at least 5% of its bounding-box area; fewer than three
active training or three independent checks also fails.  These are source-bound
coverage gates (`min_check_baseline_to_drawing_diagonal_ratio=0.25` and
`min_check_hull_area_ratio=0.05` for APD), not a human-review flag or a
substitute for survey accuracy.

Coverage is evaluated before fitting and again for each translation ->
similarity -> affine candidate using only retained robust training inliers;
candidate post-inlier coverage therefore participates in model selection.  The
accepted candidate is rechecked once more before delivery.  Source-profile v2
and v3 remain read-only compatibility inputs for nominal/disabled runs only;
they cannot enable calibration unless the v4 two-dimensional training-hull
gate (`max_drawing_vertices_outside_training_hull_ratio`, reviewed as `0.05`
for APD) is present, so a missing v4 hull policy fails closed.

## Deterministic GCP residual calibration

`config/apd_gcp_profile.json` is disabled and has an empty `controls` array by
default.  In that state the pipeline performs only the nominal
`EPSG:3857 -> EPSG:9481` operation, makes no coordinate adjustment, and must
record that absolute positioning is not independently verified.  Round-trip
error, agreement between projection engines, and the PROJ operation's declared
accuracy do not measure agreement with features on the ground.

The operator workflow is deliberately separate from publication:

```powershell
cad2gis gcp status --project "<RUN_DIR>" --json
cad2gis gcp prepare --project "<RUN_DIR>" --json
cad2gis gcp diagnose --project "<RUN_DIR>" --json
cad2gis gcp export --project "<RUN_DIR>" --json
```

Here `<RUN_DIR>` is the working directory containing the published manifest,
delivery/evidence GeoPackages and operator sidecars.  If those artifacts are
not colocated, `prepare`, `diagnose` and `export` also accept the explicit paths
shown by their respective `--help`.  The legacy `gcp_tool.py` remains an
implementation detail behind the public adapter, not the documented entrypoint.

`prepare` verifies delivery/evidence/manifest hashes and exposes 212 immutable
PTECH/BOITE/SITE candidates for QGIS editing.  `diagnose` compares translation,
similarity and affine parameters, residuals and spatial coverage without moving
published geometry.  `export` freezes train/check roles by control-set hash and
can enable a profile only after the diagnostic and reviewed numeric gates pass.
The documentation intentionally does not provide a copy-paste `--enable`
example because controls, limits and provenance are project decisions.  Editing
the capture or exporting a profile alone never changes delivery or verifies
accuracy; a reviewed profile must be exported, conversion rerun into a new
directory, and the accepted manifest checked again with `gcp status`.

Calibration may be enabled only with reviewed ground-control observations.
Each observation carries a stable `point_id`, source `cad_x`/`cad_y`, surveyed
`target_easting`/`target_northing`, `target_crs`, `role`, provenance `source`,
`accuracy_m`, `weight`, and an explicit `enabled` review flag.  Target
coordinates must be authoritative `EPSG:9481` metres.  The profile is bound to
the unique DWG SHA-256, and both CRSs must be projected with metre axes.  GNSS, total-station, or
an approved authoritative control network is suitable evidence; a visual
match to OSM or imagery is at most a coarse candidate and cannot establish
survey-grade accuracy.

An OSM-derived observation must use `reference_kind=relative_osm_reference`.
Enabled export additionally requires `--allow-relative-osm`, which acknowledges
visual alignment only and is not an absolute ground-accuracy claim.  LLMs do
not solve the observed common offset; the current symptom should test the
simplest translation model first using real controls.

Use 8--15 well-distributed observations covering both route ends, the network
perimeter, and branches.  Duplicate CAD or target coordinates are rejected.
Reserve 20--30 percent, with at least three non-collinear points, as
independent `check` observations that never enter fitting.  A common model is
applied to all coherent drawing layers so point/line relationships and
topology are preserved.  Separate coordinate regimes require residual
evidence and a separately reviewed profile; per-layer visual nudges are not
permitted.

The production coverage gate will not accept fewer than three training and
three independent, two-dimensionally distributed check points.  That is only a
mechanical minimum; use at least five training plus three check observations
for useful redundancy, and retain the
8--15 well-distributed target when control availability permits.

Model selection is complexity-gated:

1. Evaluate translation and four-parameter similarity models first.
2. Promote to a six-parameter affine model only when an authorised reviewer
   confirms spatial structure in the similarity residuals and the affine model
   meets the configured improvement ratio for independent-check RMSE, P95 and
   maximum error.  Similarity and affine must use exactly the same check point
   IDs.  Both safeguards are mandatory configuration invariants and the
   improvement ratio must be strictly positive.  Reflected
   affine transforms are always rejected.
3. Select the simplest model that passes all reviewed validation thresholds.
   TPS, rubber-sheet and other nonlinear models are outside this APD production
   contract.

Robust estimation must be deterministic: iteratively reweighted fitting uses a
reviewed numeric residual threshold, then hard inlier classification and
weighted-least-squares refitting repeat until the inlier set is stable.  A
non-convergent fit or an inlier count below the reviewed model minimum fails.
Every enabled profile must also define reviewed upper bounds for pivot shift,
absolute rotation, scale deviation, and affine condition number.  A model that
fits its controls but exceeds one of those physical-plausibility limits is not
eligible for delivery.  Deterministic drawing/train/check bounding boxes,
training convex-hull area, axis coverage, the drawing-vertex extrapolation
ratio and the independent-check baseline are retained with the human
spatial-review source.  The source profile defines numeric minimum coverage
values; an enabled calibration that misses any one of them fails before
fitting.  After robust outlier rejection the same coverage gates are recomputed
from retained training inliers; losing the reviewed footprint stops delivery.
Legacy source-profile v2/v3 inputs are read-only compatibility profiles: they
remain parseable for nominal/disabled runs and are never upgraded in place.  If
calibration is requested without the v4 two-dimensional training-hull policy,
the missing check-hull gate is a fail-closed configuration error before fitting;
no calibrated output may be published.
These gates expose and limit extrapolation but do not replace survey review.
The manifest and evidence
warehouse must retain the profile hash, control roles and provenance, selected
model and parameters, inlier decisions, per-control residual vectors, and
independent-check RMSE, P95 and maximum error.  When calibration is enabled,
missing controls, missing check points, null acceptance thresholds, a CRS
mismatch, an unreviewed spatial distribution, or a failed validation gate
stops the run.  The configuration does
not invent a business accuracy tolerance; an authorised reviewer must supply
the numeric thresholds before enabling it.

LLMs are prohibited from the production coordinate chain.  This repository does
not implement landmark discovery from imagery, survey-sheet OCR/transcription,
or automated residual-diagnostic interpretation.  An operator may use an
external LLM or a manual tool for one of those drafting tasks, but that is an
external, optional workflow: its output is untrusted and must be independently
checked and entered by a human into the versioned numeric inputs.  No such tool
may create control coordinates, weights, model parameters, inlier decisions, or
move features.  Every production coordinate must result from the versioned
numeric inputs and deterministic algorithms above.

Calibration audit data never enters the nine delivery layers.  The evidence
GeoPackage stores `georef_models`, `gcp_observations`, target-CRS
`gcp_residual_vectors`, and `georef_feature_lineage`; native route/span
evidence remains EPSG:3857 and unchanged.  The feature georeference lineage
stores full native, nominal, and adjusted vertex arrays plus their fingerprints
so every delivered point, line vertex, and polygon vertex can be replayed and
compared.  The table is populated for every geometric feature even with no GCP
profile (`nominal_direct`) or a disabled profile (`identity_residual`), where
delivery equals nominal and displacement is exactly zero.  Controls marked
`enabled=false` remain in `gcp_observations` with an
`excluded_by_review` decision, but never participate in fitting.

## Geometry and topology invariants

1. The six source CABLE polylines are immutable.  No vertex may be appended,
   snapped, bridged, or replaced.
   Their reviewed curve facts are open and contain zero bulges.  The current
   LineString delivery contract rejects any nonzero CABLE bulge or closed route
   rather than silently replacing an AutoCAD arc/closing segment with a chord.
   It also rejects 3D polylines and fitted/spline/mesh/polyface legacy
   polylines: a two-dimensional straight-segment delivery must never flatten
   or chordize those primitives implicitly.
2. The two source optical components represent two FDT service domains.  They
   are not an error and must not be forced into one component.
3. `SPAN CABLE` dimensions are measurements.  Their native segment signatures
   partition into 130 cable-route spans and 40 sling-support spans.  They are
   not route geometry and are never replaced by a default value.
4. `SLING WIRE` is support infrastructure, never optical cable geometry.
5. A support pole is not automatically an optical node.  Route/support
   proximity and device/route proximity are separate evidence relations.
6. Crossings are not connections unless reviewed evidence identifies a port.
7. Device centre offsets do not justify route edits.  Ambiguous or distant
   attachments remain candidates or unresolved.
8. Source CAD length, dimension length, target grid length and geodesic length
   remain separate fields; `LONGUEUR` is the target `EPSG:9481` geometry
   length.
9. All 139 optical source-segment occurrences have an explicit route/span
   membership row: 130 have status `measured` and 9 have the exact status
   `unmeasured_no_dimension`.  Those nine retain source, grid and geodesic
   lengths; no DIMENSION value is fabricated.
10. AutoCAD native curve length is conserved for all six source routes and is
    independently compared with the ordered source-segment sum.  The APD
    maximum difference is approximately `1.24e-8 m`; a difference above
    `1e-6 m` fails so a future bulged/curved cable cannot be silently reduced to
    straight chords.
11. Each curve entity carries canonical `cad2gis-curve-facts-v1` evidence and
    a SHA-256 fingerprint over ordered WCS vertices, bulges, elevation,
    normal/extrusion, closed state, primitive parameters and native length.
    The source-geometry gate recomputes that fingerprint immediately before
    publication so nested fact mutation cannot leave a stale hash in evidence.
    Delivery remains two-dimensional, but loss of a source curve primitive is
    detectable before topology or CRS processing.
12. The crossing inventory keeps `proper_interior_crossing`,
    `shared_source_segment_endpoint`, `source_endpoint_on_segment`,
    `collinear_overlap`, and `collinear_endpoint_on_segment` as separate
    evidence types.  None is promoted to a connection by geometry alone.  The
    route-group and source-segment graph component definitions must also agree;
    a mismatch is unresolved evidence and fails closed before publication.

The 130 measured values range from approximately `1.001178 m` to
`89.136056 m`; 128 distinct values remain after rounding to six decimals and
none equals 15.  The registry values 15 m (`fat`, `pole_new`) and 23 m
(`pole_existing`) are family-local annotation search radii, not SPAN or cable
lengths.

CABLE exposes `source_cad_length_m`, `source_segment_sum_m`,
`source_native_length_delta_m`, `LONGUEUR`, `delivery_grid_length_m`,
`geodesic_length_m`, `span_count`, `measured_span_count`,
`unmeasured_span_count`, `dimension_measured_sum_m`,
`dimension_measurement_status`, `dimension_coverage_ratio`, `span_unit`,
`span_schema_version` and `span_metrics_json`.  `dimension_length_m` is kept for
compatibility and is the sum of available measured spans, not an invented total;
use the explicit status and coverage fields to distinguish complete from partial
measurement coverage.

## Labels, styles and provenance

Display labels come only from direct DWG text/attributes or a deterministic,
traceable reviewed rule.  Internal generated IDs are not used as visible CAD
labels.  Each populated delivery field has a provenance state such as
`DWG_DIRECT`, `DWG_DERIVED:<rule>`, `USER_APPROVED:<decision>`, or
`UNAVAILABLE` in the evidence warehouse.

Annotation ownership is not assigned greedily or across unrelated families.
Three isolated, globally one-to-one assignment domains are conserved:

- `fat`: 43/43, source layer `FAT` to target layer `FAT DWG`; this reviewed
  cross-layer relation is intentional.
- `pole_new`: 118/118, same-layer assignment.
- `pole_existing`: 49/49, same-layer assignment.

PTECH therefore has 167/167 direct CAD labels and zero missing labels.  The
candidate table also retains unselected alternatives (`pole_new` 123 candidates
for 118 selections and `pole_existing` 55 for 49); those rows are evidence, not
missing or duplicated final labels.  Candidate edges store family, source and
target layer, distance, selected status, rule ID and provenance in
`annotation_assignment_candidates`.

Constant, layer, block-attribute and display-label semantics are declared in
the mapping registry with reviewed rule IDs.  Annotation and layout-topology
decisions carry registered decision-rule IDs.  A populated field without
explicit provenance stops the run.  `display_label` is itself a contractual
audited field: a non-empty label with empty or `UNAVAILABLE` label provenance
stops evidence publication, and every feature receives a `display_label` row
in `field_provenance`.

Effective CAD styling resolves entity style against its layer style before QML
generation.  This preserves the reviewed 24C/48C cable colour distinction and
asset-family colours without embedding a machine-specific QGIS project path.
The same QML is stored in `layer_styles` with `useAsDefault=1`, so loading a
delivery layer directly from the GeoPackage enables its labels and colours;
the sidecars remain available for explicit style re-application.
`layer_styles` is registered as an attributes table in both `gpkg_contents`
and `gpkg_ogr_contents`, making it discoverable through the QGIS OGR provider.
Raw CAD radians, CAD counter-clockwise degrees and QGIS clockwise degrees are
stored separately; labels use the QGIS render-angle field.  Every QML sidecar
has its own SHA-256 in the style manifest, and the manifest records
`geometry_simplification=disabled_for_source_fidelity`.

The run manifest records the selected PROJ operation, library versions and its
declared 1.2 m accuracy.  Round-trip and OSR/PROJ agreement are numerical
regression checks only.  With the default disabled, empty GCP profile,
absolute positioning remains unverified because no surveyed ground-control
observation or independent check point has been supplied.

## Outputs

`<RUN_DIR>/apd_delivery.gpkg` contains nine physical delivery layers for the
APD contract: eight base contractual layers plus the normalized
`CABLE_SEGMENT` business-detail layer.  Directories already present under
`runs/` are regression snapshots; a snapshot is not a current release merely
because its name contains `validation`.  Its source/config/implementation
fingerprints and artifact hashes must match the code and inputs under review.

- `BOITE`
- `CABLE`
- `PTECH`
- `INFRASTRUCTURE`
- `SITE`
- `ZNRO`
- `ZPM`
- `IMB`
- `CABLE_SEGMENT`

Unsupported base contractual layers are present and empty.  Current counts are
BOITE 43, CABLE 6, PTECH 167, SITE 2 and IMB 682; INFRASTRUCTURE, ZNRO and ZPM
are present and empty.  The direct `CABLE_SEGMENT` delivery layer has 139
rows: 130 `measured` and 9 `unmeasured_no_dimension`.  QGIS can load that
business layer directly for segment selection and labels;
`cable_span_segments` remains its audit/provenance counterpart in
`<RUN_DIR>/apd_evidence.gpkg`.

`CABLE_SEGMENT` is normalized only from each immutable parent `CABLE` feature's
ordered native vertex chain and its already-enriched `span_metrics`; it never
reads the evidence layer and never includes `SLING WIRE`/support spans.  One
row is emitted for each consecutive native vertex pair, with zero-based
`segment_index` order and a stable `source_segment_key`.  The business fields
are `route_key`, `source_entity_key`, `source_handle`, `source_layer`,
`segment_index`, `source_segment_key`, `source_native_length_m`,
`dimension_entity_key`, `measurement_native_m`, `measurement_delta_m`,
`delivery_grid_length_m`, `geodesic_length_m`, `length_value_m`, `status`,
`length_label`, `length_source`, `unit`, `schema_version`,
`parent_cable_code`, `parent_display_label`, and
`parent_label_provenance`; standard display/provenance, lineage, style and
`LONGUEUR` fields are also retained.  `unit=m` and
`schema_version=cad2gis.cable_segment.v1` are explicit.

For `status=measured`, `dimension_entity_key` and `measurement_native_m` come
from the matching DWG `DIMENSION`, `measurement_delta_m` is its difference
from `source_native_length_m`, `length_value_m` selects the measurement, and
`length_source=dwg_dimension`.  For
`status=unmeasured_no_dimension`, both measurement fields (including
`measurement_delta_m` and `dimension_entity_key`) are NULL,
`length_value_m` selects `delivery_grid_length_m`, and
`length_source=delivery_grid_fallback_unmeasured` is explicit.  No 15 m/23 m
family search radius, default, fabricated, or LLM-derived length is accepted.
The `LONGUEUR` field and segment geometry use the projected
`delivery_grid_length_m`; `length_label` is the QGIS label field and renders
measured values as `<value> m` or unmeasured values as
`<value> m [grid; unmeasured]`.  The generated `CABLE_SEGMENT.qml` uses
`length_label` and inherits the effective parent CABLE style.

Publication fails closed unless each segment's source length matches its native
endpoints, its transformed LineString matches `delivery_grid_length_m`,
indices are contiguous and metrics align with vertex order, and each parent
CABLE's segment count, measured/unmeasured partition, projected-grid total,
geodesic total, and measured DIMENSION total (when present) close to the
corresponding parent fields within their reviewed numeric tolerances.

The delivery contains two source optical components, one per FDT domain.  The
40 DIMENSION-backed SLING support spans remain evidence and are not promoted to
CABLE.  Device-to-route connections remain candidates until block ports are
reviewed, so the six logical cable sections deliberately remain abstained and
`ORIGINE`/`EXTREMITE` are not fabricated.  Point symbols use portable
family-specific primitives; exact CAD block artwork is not claimed.

## Canonical command

Run from the repository root in the Python 3.12 `cad2gis` Conda environment,
and choose a new output directory:

```powershell
pip install -e .
cad2gis doctor
cad2gis convert `
  'experiment\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg' `
  --run-dir '<NEW_APD_RUN_DIR>' `
  --project 'experiment' `
  --json
```

While `config/apd_gcp_profile.json` remains disabled and empty, this run applies
only the nominal direct CRS operation.  It must report absolute accuracy as
`not_verified`; do not set `enabled=true` until real reviewed controls, robust
fit limits, spatial coverage and independent validation thresholds have been
supplied and passed.

For another source, use `cad2gis inspect SOURCE`,
`cad2gis bootstrap SOURCE --project DIR`, human review,
`cad2gis validate --project DIR`, and only then
`cad2gis convert SOURCE --run-dir NEW_DIR --project DIR`.  Cross-CAD claims are
evaluated read-only with `cad2gis verify MATRIX.json`; APD is currently the only
real-DWG regression row and therefore cannot establish cross-CAD success.

`experiment/py_scripts/convert_v3.py` is a compatibility wrapper that delegates
to `cad2gis.cli`.  The old `converter.py` implementations under `experiment/`,
`demo/` and `official/validation/` are disabled by default.  The exact opt-in
`CAD2GIS_ENABLE_LEGACY=1` emits a deprecation warning and is only for reproducing
legacy behaviour; it must not produce a v3 delivery or accuracy claim.
