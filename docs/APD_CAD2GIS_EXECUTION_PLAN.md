# APD CAD2GIS Execution Plan

## Outcome and scope

Implement an APD-specific deterministic CAD2GIS package in
E:\branch_CAD2GIS\CAD2GIS. It must distill the complete DWG database into an
auditable evidence GeoPackage, recover source-supported semantics and topology,
use an optional OpenAI-compatible cloud model only during curation, and emit an
atomic eight-layer delivery plus QGIS styles.

Read APD_CAD2GIS_HANDOFF.md first.

Included: immutable DWG ingestion, complete census/loss accounting,
anonymous/dynamic blocks, role segmentation, FAT/FDT/pole/homepass/cable/span/
sling/boundary evidence, provenance, reviewed registry, optional cloud curation,
offline conversion, eight official layers, QGIS sidecars, and normal engineering
tests/lint/types/smoke/failure paths.

Excluded: product verification/certification, gold data, independent reviewer
workflow, accuracy-percentage claims, product acceptance for the user, invented
business data, generic multi-DWG claims, web UI, local LLM, fine-tuning, and
modification of current output/report files. The user will verify the result.

## Required tree

~~~text
E:\branch_CAD2GIS\CAD2GIS
├── pyproject.toml
├── environment.yml
├── conda-lock.yml
├── src\cad2gis\
│   ├── __init__.py
│   ├── cli.py
│   ├── errors.py
│   ├── model.py
│   ├── config.py
│   ├── manifest.py
│   ├── ingest\{autocad.py,dxf.py,reconcile.py}
│   ├── evidence\{schema.py,geopackage.py,conservation.py}
│   ├── semantics\{roles.py,fingerprints.py,candidates.py,annotations.py,registry.py}
│   ├── cloud\{client.py,capabilities.py,schema.py,cache.py,redact.py}
│   ├── topology\{candidates.py,solve.py,graph.py}
│   ├── delivery\{contract.py,build.py,atomic.py,lineage.py}
│   └── qgis\{symbols.py,qml.py,project.py}
├── tools\autocad_extractor\
│   ├── Cad2Gis.AutoCAD.csproj
│   ├── Commands.cs
│   ├── CensusWriter.cs
│   ├── GeometryWriter.cs
│   └── Distill.scr.in
├── contracts\apd_delivery_v1\
│   ├── contract.json
│   ├── domains.json
│   ├── field_provenance.json
│   └── source_profile.example.json
├── qgis_plugin\cad2gis\{__init__.py,metadata.txt,plugin.py,dockwidget.py}
├── tests\{fixtures,unit,integration,e2e}
└── .omo\evidence\
~~~

Use Python 3.12, Pydantic v2, ezdxf, GDAL/OGR, Shapely 2, pyproj,
OR-Tools, OpenAI Python SDK, pytest, Ruff, and basedpyright. Put heavy GIS
dependencies in the conda environment. Expose:

~~~toml
[project.scripts]
cad2gis = "cad2gis.cli:main"
~~~

Keep modules focused and near/below 250 pure source lines where practical.

## Selective historical port

Read historical files with:

~~~text
git show d7f7350:CAD2GIS/plugincad2gis/<path>
~~~

Selective reuse:

| Historical source | New target | Constraint |
| --- | --- | --- |
| pyproject.toml and env files | root package/environment | Modernize/pin |
| model.py | model.py | Keep useful types, replace feature assumptions |
| ingest.py and parse.py | ingest modules | DXF mechanics only |
| evidence.py | evidence modules | Provenance ideas, new schema |
| mapping/engine.py | semantics modules | Rule mechanics, not mappings |
| network.py/topology.py | topology modules | Graph utilities after APD tests |
| warehouse files | evidence/delivery writers | Reuse only transactional patterns |
| QML/plugin shell | qgis modules/plugin | Replace styles; CLI remains sole converter |
| historical tests | new tests | Port only current-contract behavior |

Do not wholesale restore d7f7350. Do not port historical verification,
benchmarks, generic symbol YAML, old block-code mappings, or unrelated samples.

## Public interfaces and CLI

~~~text
inventory(DwgArtifact) -> InventoryReport
normalize(DwgArtifact) -> NormalizedArtifact
parse(DwgCensus, NormalizedArtifact) -> EvidenceStore
mine(EvidenceStore, MiningPolicy, LlmClient | None) -> ReviewBundle
compile_registry(ReviewBundle, ReviewDecision) -> MappingRegistry
build_topology(EvidenceStore, MappingRegistry, TopologyPolicy) -> TopologyResult
convert(ConversionRequest) -> ConversionResult
write_styles(DeliveryArtifact, StyleCatalog) -> QgisArtifacts
~~~

Boundary models reject unknown fields unless versioned. Paths are resolved,
hashes validated, CRS explicit, and distinct IDs use distinct types. Each result
manifest records run/tool/source hashes, census/registry/contract hashes, CRS and
PROJ operation, counts, warnings/unresolved items, and output hashes.

~~~text
cad2gis inventory DWG --out inventory.json
cad2gis distill DWG --evidence apd_evidence.gpkg --work-dir RUN_DIR
cad2gis curate EVIDENCE --review-bundle review_bundle.json
cad2gis compile-registry REVIEWED_BUNDLE --out apd_registry.json
cad2gis convert EVIDENCE --registry apd_registry.json --contract CONTRACT \
  --source-crs EPSG:3857 --target-crs EPSG:4326 --out apd_delivery.gpkg
cad2gis styles EVIDENCE apd_delivery.gpkg --out-dir qgis
~~~

All commands support --help, --log-format text|json, and --run-manifest.
Failures exit non-zero and never print success. Success names paths and hashes.

## Phase 0: protect and baseline

- [ ] Read the handoff completely.
- [ ] Inspect git status and HEAD; preserve every existing/untracked file.
- [ ] Hash APD and require the immutable digest.
- [ ] Never remove AutoCAD .dwl/.dwl2 files.
- [ ] Use a new run directory, not official or experiment/output.
- [ ] Record current GPKG counts for comparison only.
- [ ] Store concise RED/GREEN engineering logs under .omo/evidence.

Suggested Git Bash commands:

~~~bash
git status --short --untracked-files=all
git rev-parse HEAD
sha256sum "official/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"
find docs official experiment/py_scripts official/validation -maxdepth 3 -type f -print
git ls-tree -r --name-only d7f7350 -- CAD2GIS/plugincad2gis/src/cad2gis CAD2GIS/plugincad2gis/tests
~~~

## Phase 1: package and harness

RED first: CLI help, bad path, hash mismatch, output collision, malformed
manifest, and no success banner after failure.

Then add package/environment skeleton, typed errors, canonical JSON/hash
utilities, structured logs with secret redaction, and only the needed historical
CLI mechanics.

~~~bash
python -m pytest tests/unit/test_cli.py tests/unit/test_manifest.py -q
ruff check src tests
ruff format --check src tests
basedpyright src
cad2gis --help
~~~

## Phase 2: authoritative AutoCAD census

RED first: model/paper split, anonymous INSERT attributes, nested instance paths
and transforms, dynamic effective name/properties, DIMENSION measurement/text,
XDATA/dictionaries/XRecords, missing xref/proxy dispositions, stable EntityKey.

Implement a .NET 8 AutoCAD 2027 command CAD2GISEXPORT that opens read-only and
enumerates database objects, symbol tables, named dictionaries, layouts, blocks,
references, attributes, fields, dimensions, xrefs, proxies, XDATA, extension
dictionaries, and persistent reactors. Write versioned UTF-8 NDJSON and a
manifest. Preserve handles, owners, paths, matrices, units, and typed
unsupported/error records. Never save the DWG.

Invocation pattern:

~~~powershell
& "C:\Program Files\Autodesk\AutoCAD 2027\accoreconsole.exe" /i "E:\branch_CAD2GIS\CAD2GIS\official\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" /s "E:\branch_CAD2GIS\CAD2GIS\tools\autocad_extractor\Distill.scr"
~~~

The generated SCR NETLOADs the assembly, calls CAD2GISEXPORT with explicit
output paths, and quits without save. Engineering regressions: preserve 222
INSERT and 170 DIMENSION records; accept anonymous names; no silent loss.

## Phase 3: normalized DXF and reconciliation

RED first: census/DXF handle match, nested transforms, explicit mismatch
categories, and no silent loss.

- Normalize a read-only APD copy with AutoCAD Core Console.
- Parse with ezdxf.
- Reconcile by handle, type, owner, and normalized geometry.
- Classify MATCHED, TRANSFORMED, DXF_OMITTED, PROXY_ONLY, MISSING_XREF,
  UNSUPPORTED, or ERROR.
- AutoCAD census wins for database semantics.
- Do not depend on the external normalized DXF under E:\aaaCAD2GIS.

## Phase 4: evidence database and conservation

RED first: required tables, stable EntityKey, terminal dispositions, immutable
source geometry, APD 222/170 counts, rollback on malformed census, and interrupted
write preserving an old artifact.

Create the handoff evidence tables. Store native and normalized geometries,
source/derived/reviewed provenance, and a conservation ledger.

Atomic writer:

1. Refuse equal input/output paths.
2. Resolve all paths.
3. Create a unique sibling .<name>.<run-id>.tmp.gpkg.
4. Write in a transaction.
5. Close GDAL/SQLite handles.
6. Run SQLite integrity_check and required table/count checks.
7. Flush file and directory where supported.
8. Replace destination with os.replace.
9. On failure, delete only this run's temporary file.

## Phase 5: APD roles and semantic candidates

RED first: anonymous fingerprint matching, 10 legend versus 212 plan inserts, no
nested child duplicates, pole/FAT/FDT recovery, generic Line not cable,
detail-core not infrastructure, legend cable not route, unresolved text not an
asset.

Evidence precedence:

1. handle/space/layout/block-reference context;
2. layout/viewports and plan/detail components;
3. normalized block fingerprint and dynamic properties;
4. attributes/text;
5. layer/color/linetype;
6. proximity/containment;
7. cloud proposal;
8. explicit user review.

Fingerprint canonical child entity types, relative geometry, attribute tags,
layers, colors/linetypes, and dynamic properties. Never include insertion
coordinates.

Generate only supported candidates: PTECH from pole families, BOITE from FAT,
SITE from FDT, IMB from homepass/footprint evidence, CABLE from positive 24C/48C
routes/spans, INFRASTRUCTURE from reviewed plan-role sling wire, ZPM from
BOUNDARY CLUSTER only after user acceptance, and ZNRO only from explicit source.
Do not create features to make a layer non-empty.

## Phase 6: labels and attributes

RED first: one asset ID per asset, home containment, cable label to component,
dimension-versus-label separation, no reused one-to-one label, and tie abstention.

Initial candidate radii:

- exact endpoint tolerance: max(1e-6 drawing units, 10 times export precision);
- endpoint-to-node: 2 units;
- FAT/asset label: 15 units with family/component evidence;
- home: containment then at most 5 units;
- cable annotation: same component and at most 10 units.

Persist unresolved alternatives. Keep native dimension, displayed rounded,
design-summary declared, and geometry-computed lengths separate.

## Phase 7: cloud curation

RED first with a fake HTTP server: custom base URL/key, capability probe, strict
schema success, malformed JSON, unknown candidate ID, coordinate/geometry/CRS
injection, oversized response, retry policy, secret redaction, zero-call cache
hit, stale-cache rejection, and JSON-object-only proposal status.

Request carries schema version, source hash, task ID, existing candidate/evidence
IDs/facts, allowed classes, and select/abstain actions. Response contains only
same task ID, existing candidate/evidence IDs, select/abstain, allowed class,
bounded confidence, and short rationale. Use additionalProperties false.
Revalidate all IDs and prohibit coordinate/geometry/CRS/new-ID keys.

Cache key is SHA-256 over source hash, evidence digest, candidate IDs, prompt
digest, response-schema digest, model, non-secret base-URL profile, and optional
crop digest. A mismatch is stale.

Only cloud modules and curate import the OpenAI SDK. compile-registry accepts
explicit user-reviewed decisions and binds source/evidence/contract/decision
hashes. Add a network-deny test around convert.

## Phase 8: deterministic topology

RED first: exact endpoint, near endpoint pending review, crossing without node,
ring/self-loop, support not optical connection, multiple-optimum abstention,
timeout/FEASIBLE abstention, and immutable source geometry.

Relation kinds: connects, supported_by, contained_in, hosted_at.

No crossing-only connection, no automatic pole junction, no legend/plan or
cross-layout links, and at most one accepted node per endpoint. Derived vertices
retain source segment, operation, displacement, and decision lineage.

Use CP-SAT only for pre-generated candidates in a component no larger than 200
Boolean variables/50 objects. Integer distances, seed 0, one worker, 30 seconds.
Require OPTIMAL; exclude the result and re-solve. Another optimum, timeout,
FEASIBLE, or infeasible means abstain. Deterministic evidence outranks LLM score.

## Phase 9: offline eight-layer delivery

RED integration/E2E first: exactly eight layers, correct geometry families,
source-unsupported layers remain empty, field provenance, no audit tables,
zero network, stale/wrong registry failure, malformed CRS failure, interrupted
write preserves destination, complete lineage, and no generic Line/detail
false positives.

Field provenance:

~~~text
DWG_DIRECT
DWG_DERIVED:<rule-id>
USER_APPROVED:<decision-id>
UNAVAILABLE
~~~

Generated IDs are marked generated and never represented as source labels.
Constants for absent mandatory business fields require an explicit versioned
user decision.

convert loads/hashes evidence, registry, contract, and source profile; rejects
mismatches/incomplete review; installs a network-deny guard; writes all eight
tables to a temporary sibling; writes lineage outside the delivery layer set;
integrity-checks; atomically replaces; and prints path/hash/count/unresolved
summary. Do not run the existing evaluator as a product gate or invent data for
it.

## Phase 10: QGIS sidecars

RED first: every delivery layer has QML, SVG references resolve portably,
pole/FAT/FDT/home/cable classes resolve, source label fields are used, generated
IDs are not labels, and QGZ paths survive moving the output folder.

~~~text
<output>\qgis\
├── apd_delivery.qgz
├── styles\{BOITE,CABLE,PTECH,INFRASTRUCTURE,SITE,ZNRO,ZPM,IMB}.qml
└── svg\{pole_new_7_3,pole_new_7_2_5,pole_new_7_4,pole_existing,fat,fdt,homepass}.svg
~~~

Derive SVG geometry/colors from user-reviewed APD legend glyphs. Preserve
rotation; distinguish 24C/48C. Starting presentation assumptions: 4 mm point
markers, 0.6 mm cable line, 8 pt labels, 1 mm buffer, collision avoidance.

If retained, the QGIS plugin invokes the same CLI via QProcess, streams JSONL,
loads artifacts, and contains no second converter.

## Engineering finish gate

~~~bash
python -m pytest -q
ruff check src tests
ruff format --check src tests
basedpyright src
cad2gis --help
cad2gis inventory "official/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" --out "<run>/inventory.json"
cad2gis distill "official/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" --evidence "<run>/apd_evidence.gpkg" --work-dir "<run>/work"
cad2gis convert "<run>/apd_evidence.gpkg" --registry "<run>/apd_registry.json" --contract "contracts/apd_delivery_v1/contract.json" --source-crs "EPSG:3857" --target-crs "EPSG:4326" --out "<run>/apd_delivery.gpkg"
cad2gis styles "<run>/apd_evidence.gpkg" "<run>/apd_delivery.gpkg" --out-dir "<run>/qgis"
~~~

Also exercise missing DWG, wrong hash, malformed census, stale registry, existing
destination, simulated interruption, convert with network calls forced to fail,
curate without credentials, and curate against a fake compatible server. These
are engineering checks, not semantic/product certification.

## Implementation handoff definition

- package/CLI exist;
- distill creates evidence from immutable APD;
- 222 INSERT/170 DIMENSION are retained;
- anonymous symbols and positive cables are recoverable;
- generic Line/detail primitives are not emitted;
- reviewed registry is content-addressed and stale state fails closed;
- convert is deterministic/network-disabled and atomic;
- eight-layer delivery and QGIS sidecars are produced;
- engineering tests/lint/types/help/happy/bad paths pass;
- no protected file changed.

Semantic acceptance remains with the user.

