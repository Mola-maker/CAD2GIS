# CAD2GIS v3 architecture

This directory contains the canonical direct-DWG conversion path for the APD
Hutabohu drawing.  It replaces the legacy geometry-repair workflow with a
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

## Pipeline boundaries

```text
direct DWG readCAD
  -> immutable, loss-aware all-object inventory
  -> reviewed semantic classification
  -> independent route/support/optical evidence graphs
  -> nominal direct CRS transformation
  -> optional deterministic GCP residual calibration
  -> eight-layer delivery warehouse
  + separate evidence warehouse and QGIS styles

optional operator-only review lane (never imported by conversion)
  readCAD + semantic/span evidence
  -> content-addressed review bundle
  -> provider-neutral review application port
     -> DeepSeek direct adapter (first verification)
     -> New API OpenAI-compatible gateway adapter (demo/runtime)
  -> human/LLM select, rank, or abstain proposal
  -> local strict domain validation
  -> validated audit artifact
```

The implementation is split by responsibility:

- `autocad_reader.py`: direct AutoCAD extraction only; it does not assign GIS
  meaning.  The versioned 30-column primary protocol remains compatible with
  17/21/24/29/30-column records.  It retains owner handles, block attributes,
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
  training from independent checks, rejects non-finite, degenerate or
  reflected solutions, and returns an immutable calibration result.
- `cad2gis_v3/warehouse.py`: writes exactly the eight contractual delivery
  layers.
- `cad2gis_v3/evidence.py`: writes audit, provenance, lineage, topology and
  unresolved evidence to a different GeoPackage.  Its all-object tables include
  `cad_entities` (9,391), `block_instances` (362),
  `annotation_carriers` (2,292) and `cable_span_metrics` (139).  The
  `cable_span_segments` spatial evidence layer exposes those 139 ordered spans
  directly in EPSG:9481 for QGIS selection and labelling.
- `cad2gis_v3/styles.py`: writes portable QGIS QML files from effective CAD
  colour/style evidence and registers each QML as the default style inside the
  delivery GeoPackage.
- `cad2gis_v3/pipeline.py`: enforces conservation and regression gates and
  writes an auditable manifest.  Reproducibility means stable semantic results
  for identical reviewed inputs; it does not claim byte-identical GeoPackage
  files across GDAL, SQLite, Python, or operating-system versions.
- `cad2gis_v3/curation.py`: owns only the content-addressed review domain,
  proposal schema, forbidden-fact policy and local validation.  It contains no
  provider configuration and performs no network access.
- `cad2gis_v3/curation_service.py`: is the offline application layer.  It
  exposes one sanitised task through a provider-neutral port, then sends the
  returned JSON through the same local proposal validator used for a manual
  review.
- `cad2gis_v3/curation_providers/`: contains the provider port, environment
  profiles and the single OpenAI-compatible HTTP transport.  DeepSeek and New
  API are profiles/capabilities of this boundary, not dependencies of readCAD,
  topology, CRS, length or publication code.

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

`gcp_capture.gpkg`, GCP diagnostic JSON, `readcad_review_bundle.json` and any
proposal/audit JSON are operator sidecars.  They are intentionally created
after publication and do not rewrite the already-hashed run manifest.

## Optional readCAD curation lane

LLM review starts only after AutoCAD has deterministically produced immutable
facts; the model never parses binary DWG and is never imported by
`convert_v3.py`.  Review bundle schema `cad2gis.review_bundle.v2` represents all
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
  --evidence '..\runs\apd_architecture_v3_complete\apd_evidence.gpkg' `
  --dwg '..\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg' `
  --out '..\runs\apd_architecture_v3_complete\readcad_review_bundle.json'
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
organising complete readCAD evidence and proposing exception decisions, not
for manufacturing missing CAD facts.

The checked-in `config/llm_provider.env.example` contains only variable names
and safe defaults; secrets stay in the runtime environment.  The profiles are
based on the official [DeepSeek API quick start](https://api-docs.deepseek.com/),
[DeepSeek Chat Completions contract](https://api-docs.deepseek.com/api/create-chat-completion/),
and [New API Chat Completions contract](https://docs.newapi.pro/en/docs/api/ai-model/chat/openai/createchatcompletion).

## Deterministic GCP residual calibration

`config/apd_gcp_profile.json` is disabled and has an empty `controls` array by
default.  In that state the pipeline performs only the nominal
`EPSG:3857 -> EPSG:9481` operation, makes no coordinate adjustment, and must
record that absolute positioning is not independently verified.  Round-trip
error, agreement between projection engines, and the PROJ operation's declared
accuracy do not measure agreement with features on the ground.

The operator workflow is deliberately separate from publication:

```powershell
python gcp_tool.py prepare `
  --delivery '..\runs\apd_architecture_v3_complete\apd_delivery.gpkg' `
  --evidence '..\runs\apd_architecture_v3_complete\apd_evidence.gpkg' `
  --manifest '..\runs\apd_architecture_v3_complete\run_manifest.json' `
  --out '..\runs\apd_architecture_v3_complete\gcp_capture.gpkg'
python gcp_tool.py diagnose --capture <edited-capture.gpkg> --report <diagnostic.json>
python gcp_tool.py export `
  --capture <edited-capture.gpkg> `
  --template-profile '..\config\apd_gcp_profile.json' `
  --diagnostic-report <diagnostic.json> --out <reviewed-profile.json> --enable
```

`prepare` verifies delivery/evidence/manifest hashes and exposes 212 immutable
PTECH/BOITE/SITE candidates for QGIS editing.  `diagnose` compares translation,
similarity and affine parameters, residuals and spatial coverage without moving
published geometry.  `export` freezes train/check roles by control-set hash and
can enable a profile only after the diagnostic and reviewed numeric gates pass.
Editing the capture alone never changes delivery; a reviewed profile must be
exported and conversion rerun.

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
Reserve 20--30 percent, with at least two points, as
independent `check` observations that never enter fitting.  A common model is
applied to all coherent drawing layers so point/line relationships and
topology are preserved.  Separate coordinate regimes require residual
evidence and a separately reviewed profile; per-layer visual nudges are not
permitted.

The tool will not accept fewer than three training and two independent check
points for translation.  That is only a mechanical minimum; use at least five
training plus two check observations for useful redundancy, and retain the
8--15 well-distributed target when control availability permits.

Model selection is complexity-gated:

1. Evaluate translation and four-parameter similarity models first.
2. Promote to a six-parameter affine model only when an authorised reviewer
   confirms spatial structure in the similarity residuals and the affine model
   materially improves both fitting and independent-check results.  Reflected
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
training-extent coverage ratios, and the count of drawing vertices outside the
training bounding box are retained with the human spatial-review source; these
diagnostics make extrapolation visible but do not replace survey review.
The manifest and evidence
warehouse must retain the profile hash, control roles and provenance, selected
model and parameters, inlier decisions, per-control residual vectors, and
independent-check RMSE, P95 and maximum error.  When calibration is enabled,
missing controls, missing check points, null acceptance thresholds, a CRS
mismatch, an unreviewed spatial distribution, or a failed validation gate
stops the run.  The configuration does
not invent a business accuracy tolerance; an authorised reviewer must supply
the numeric thresholds before enabling it.

LLMs are prohibited from the production coordinate chain.  They may assist
offline with locating candidate landmarks, transcribing a survey sheet for
human review, or explaining residual diagnostics, but they may not create
control coordinates, weights, model parameters, inlier decisions, or move
features.  Every production coordinate must result from the versioned numeric
inputs and deterministic algorithms above.

Calibration audit data never enters the eight delivery layers.  The evidence
GeoPackage stores `georef_models`, `gcp_observations`, target-CRS
`gcp_residual_vectors`, and `georef_feature_lineage`; native route/span
evidence remains EPSG:3857 and unchanged.  The feature georeference lineage
stores full native, nominal, and adjusted vertex arrays plus their fingerprints
so every delivered point, line vertex, and polygon vertex can be replayed and
compared.  Controls marked `enabled=false` remain in `gcp_observations` with an
`excluded_by_review` decision, but never participate in fitting.

## Geometry and topology invariants

1. The six source CABLE polylines are immutable.  No vertex may be appended,
   snapped, bridged, or replaced.
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
has its own SHA-256 in the style manifest.

The run manifest records the selected PROJ operation, library versions and its
declared 1.2 m accuracy.  Round-trip and OSR/PROJ agreement are numerical
regression checks only.  With the default disabled, empty GCP profile,
absolute positioning remains unverified because no surveyed ground-control
observation or independent check point has been supplied.

## Outputs

`runs/apd_architecture_v3_complete/apd_delivery.gpkg` contains exactly eight
business feature layers:

- `BOITE`
- `CABLE`
- `PTECH`
- `INFRASTRUCTURE`
- `SITE`
- `ZNRO`
- `ZPM`
- `IMB`

Unsupported contractual layers are present and empty.  Audit and topology
tables are intentionally absent from the delivery file and are stored in
`runs/apd_architecture_v3_complete/apd_evidence.gpkg`.  Current counts are
BOITE 43, CABLE 6, PTECH 167, SITE 2 and IMB 682; INFRASTRUCTURE, ZNRO and ZPM
are present and empty.  Load evidence layer `cable_span_segments` when QGIS
needs to select or label each of the 139 spans individually.

The delivery contains two source optical components, one per FDT domain.  The
40 DIMENSION-backed SLING support spans remain evidence and are not promoted to
CABLE.  Device-to-route connections remain candidates until block ports are
reviewed, so the six logical cable sections deliberately remain abstained and
`ORIGINE`/`EXTREMITE` are not fabricated.  Point symbols use portable
family-specific primitives; exact CAD block artwork is not claimed.

## Canonical command

Run from `experiment/py_scripts` in the `cad2gis` Conda environment:

```powershell
python convert_v3.py `
  --input '..\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg' `
  --run-dir '..\runs\apd_architecture_v3_complete' `
  --source-profile '..\config\apd_source_profile.json' `
  --mapping-registry '..\config\apd_mapping_registry.json' `
  --gcp-profile '..\config\apd_gcp_profile.json'
```

This command is backward-equivalent to nominal reprojection while the GCP
profile remains disabled.  Do not set `enabled=true` until reviewed controls,
robust-fit thresholds and independent validation thresholds have been added.

The old `converter.py` entry point is disabled unless explicitly opted into via
`CAD2GIS_ENABLE_LEGACY=1`.  It must not be used to produce the v3 APD delivery.
