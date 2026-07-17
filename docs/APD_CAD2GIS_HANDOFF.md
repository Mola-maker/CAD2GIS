# APD CAD2GIS Handoff

## Purpose and latest scope

This is the implementation handoff for an accuracy-first CAD2GIS system for one
real drawing:

**E:\branch_CAD2GIS\CAD2GIS\official\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg**

The user has fixed these boundaries:

- This DWG is the only blueprint and the only APD feature truth.
- All available feature, symbol, label, cable, attribute, and topology evidence
  must be distilled from the DWG database.
- The existing conversion runs, but labels, symbols, cables, and semantic
  layers are missing or wrong.
- Highest conversion accuracy is primary; runtime and token cost are secondary.
- An OpenAI-compatible cloud API may assist semantic curation. No local LLM.
- The user will supply API key, base URL, and model.
- The user will verify the product personally.
- Product verification, gold-standard creation, independent accuracy
  certification, and formal release certification are out of scope.
- Engineering tests, lint, types, CLI smoke use, deterministic/offline
  conversion, malformed-input handling, and interrupted-write safety remain in
  scope.

This handoff does not claim implementation has started or passed.

## Immutable input

| Property | Value |
| --- | --- |
| DWG | E:\branch_CAD2GIS\CAD2GIS\official\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg |
| SHA-256 | 557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557 |
| Format | AC1032; file inspection reports AutoCAD 2018/2019/2020 |
| Primary extractor available | C:\Program Files\Autodesk\AutoCAD 2027\accoreconsole.exe |

Record the hash before every run. Open read-only or copy to an immutable run
directory and abort if the hash changes. AutoCAD currently creates
official\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwl and .dwl2. These
are user/runtime locks: never delete, edit, stage, rename, or treat them as
input.

## Evidence boundary

The DWG is authoritative for entity geometry and handles, block definitions and
instances, attributes, text, dimensions, layers, colors, linetypes, layouts,
legend/detail semantics, explicit cable routes, span measurements, and
source-supported relationships.

The unrelated AGA drawing, JAD_MARJANE_Reference.gpkg, old converted GPKG,
generic layer-name heuristics, papers, standards, and LLM output are not APD
truth. Papers guide architecture. The cloud model may rank existing candidates.
Neither may invent geometry, coordinates, CRS, topology, asset IDs, attributes,
or missing features.

## Current repository and output

Inspected state:

- repo: E:\branch_CAD2GIS\CAD2GIS
- HEAD: 324cb1214baa2919b158ab24be830c8521ed7a35
- subject: Indonesia-Hutabohu CABLE_ALL Topology Done
- no root pyproject.toml, installable package, or root tests;
- active logic is mainly in experiment\py_scripts and official\validation;
- historical d7f7350 has a fuller CAD2GIS package and tests, but only tested
  mechanics should be selectively ported.

Current artifact:

**experiment\output\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.gpkg**

| Layer | Count | Populated CODE |
| --- | ---: | ---: |
| BOITE | 2,626 | 0 |
| CABLE | 575 | 0 |
| INFRASTRUCTURE | 415 | 0 |
| PTECH | 0 | n/a |
| SITE | 0 | n/a |
| ZNRO | 0 | n/a |
| ZPM | 0 | n/a |
| IMB | 0 | n/a |

All eight tables report EPSG:4326 metadata, which does not prove the source CRS
or transformation. experiment\output\hutabohu_verification_report.json is stale
relative to the GPKG. Do not modify either existing file.

## Diagnosed failure seams

Line numbers are for HEAD 324cb121.

1. **Anonymous assets dropped.** official\validation\converter.py:674-681
   skips every INSERT whose name fails a generic telecom-name predicate. The
   212 plan symbols use anonymous names such as *U7, *U11, and *U13 variants.
   Recover by definition fingerprint, contents, attributes, transform, and
   legend/layout evidence, not English block name.
2. **Annotation bookkeeping broken.**
   official\validation\converter.py:532-560 initializes linked but never adds a
   successful index, so every annotation is returned as unlinked. Replace with
   typed, provenance-bearing label relations and explicit unresolved state.
3. **Linux-only ingestion.** official\validation\converter.py:60-72 hardcodes
   /usr/local/lib/libredwg.so; lines 573-589 inject a Linux Python path. The
   experiment converter duplicates this. Use AutoCAD 2027 Core Console as
   authoritative DB extractor and DXF/ezdxf as an independently reconciled
   geometry lane.
4. **False-positive fallbacks.**
   experiment\py_scripts\schema_config.py:1835-1866 marks FDT DWG and FAT DWG
   negative even though they provide symbol evidence; lines 1920-1923 map
   generic Line to CABLE and detail core layers to INFRASTRUCTURE; lines
   1924-1925 map sling wire to CABLE. Remove these from the new registry.
   Legend/detail graphics remain evidence but are not plan features.
5. **Non-atomic output.** official\validation\converter.py:924-925 and the
   experiment equivalent at 996-997 delete the destination first. Write a
   sibling temporary GPKG, close/check it, and atomically replace only on
   success.
6. **Contract versus source conflict.** official\validation\evaluator.py:255-264
   requires every official layer non-empty and later rules demand business
   fields/relationships. Do not invent evidence to satisfy it. Emit eight
   tables, explicit unavailable provenance, and let the user decide on external
   enrichment.

## APD inventory already observed

These measurements came from a read-only normalized-DXF census and direct
review. They are implementation regression fixtures, not an independent
accuracy certificate.

### Census

- 6,940 modelspace entities; 112 layers; 236 block definitions.
- Definitions: 1 model, 7 paper, 170 dimension, 20 anonymous dynamic, 38 named.
- 4,265 LWPOLYLINE, 1,325 TEXT, 222 INSERT, 170 DIMENSION.
- 222 INSERTs split into 10 legend exemplars and 212 plan symbols.
- 167 poles: 71 NEW POLE 7-3, 46 NEW POLE 7-2.5, 1 NEW POLE 7-4,
  49 EXISTING POLE.
- 43 FAT, 2 FDT, and 682 homepass labels.
- *U13 through *U17 are pole candidates; *U11 is the FAT family with
  FAT_ID/FAT attribute definitions; *U7 is the FDT family.
- FDT-01 is near native (13681914.403, 69386.445) with
  FDT_ID DMPH-1.010.
- FDT-02 is near native (13683236.666, 68765.958) with
  FDT_ID DMPH-2.011.
- ETIKET EMR-NEW 2026 is used in seven paper layouts and labels symbol
  classes. Unreferenced EMR - FH FRAME also contains semantic glyph layers.
  Both are dictionaries/evidence, never plan features.

A normalized artifact was observed at
E:\aaaCAD2GIS\CAD2GIS\plugincad2gis\build\apd_normalized.dxf. Regenerate from
the immutable official DWG; do not depend on that external file.

### Cable/support evidence

- 170 SPAN CABLE dimensions.
- Native measurement sum about 7,005.8806339 drawing units.
- All displayed span labels parsed; displayed rounding difference no more than
  about 0.493136 m in the prior census.
- Of 340 endpoints: 316 exactly matched inserts, 318 were within 1 unit, 338
  within 2, and all within about 7.876.
- Six positive plan cable polylines: five 24C and one 48C.
- Three other explicit cable polylines are legend prototypes.
- Six plan routes have 145 vertices; 129 vertices coincide with assets.
- 25 sling-wire polylines.
- Design summary separately declares 24C = 4,338 m and 48C = 2,438 m.
- Generic Line contains 411 line features and no endpoint within 10 drawing
  units of network assets: strong evidence against Line-to-CABLE.

Native dimension, displayed rounded, declared-summary, and GIS-computed lengths
are different facts and need separate provenance.

### Home/boundary evidence

- Base Map has 1,255 open LWPOLYLINE entities.
- Prior polygonization produced 398 candidates; 379/682 home texts were inside
  and 16 more were within 5 units. These remain candidates.
- One valid closed BOUNDARY CLUSTER polygon exists.
- A two-point BOUNDARY FAT line is a legend prototype.

### CRS

EPSG:3857 inversions are geographically plausible for Gorontalo but plausibility
is not proof. Preserve native coordinates, require --source-crs or an approved
profile, record the exact PROJ operation, and never silently label native
coordinates EPSG:4326.

## Architecture decisions

### Artifacts and conservation

Produce:

1. **apd_evidence.gpkg**: complete source evidence, candidates, decisions,
   topology, style catalog, lineage, and run manifest.
2. **apd_delivery.gpkg**: exactly BOITE, CABLE, PTECH, INFRASTRUCTURE, SITE,
   ZNRO, ZPM, and IMB.
3. QML/SVG/QGZ sidecars.

Stable identity:

~~~text
EntityKey = SHA256(DWG) + handle + owner path + instance path
~~~

Each object has one terminal disposition:

~~~text
mapped | annotation | graphic_only | legend | out_of_scope |
unsupported | missing_xref | error
~~~

Evidence tables should include cad_entities, cad_layers, cad_object_tables,
block_definitions, block_instances, annotations, dimensions, xrefs,
proxy_objects, legend_entries, feature_candidates, mapping_decisions,
topology_candidates, topology_relations, feature_lineage, field_provenance,
style_catalog, review_decisions, llm_audit, run_manifest, and
conservation_ledger.

### Runtime boundary

- inventory/distill create evidence from the DWG.
- curate is the only network/cloud command.
- compile-registry converts explicit user-reviewed decisions into an immutable,
  content-addressed registry.
- convert consumes evidence, registry, contract, and explicit CRS settings. It
  runs with network disabled and never imports or calls the LLM client.
- styles builds APD-derived QGIS sidecars.

The model may select existing candidate IDs with a bounded rationale. It may not
create coordinates, geometry, CRS, IDs, layers, attributes, or topology. Strict
JSON Schema/tool output may enter review; free text or JSON-object-only output
stays proposal-only.

Topology separates connects, supported_by, contained_in, and hosted_at. A pole
is support, not automatically an optical node. A crossing is not a connection.
Source geometry is immutable; derived snapping/splitting retains displacement
lineage. Small ambiguous components may use deterministic bounded CP-SAT over
pre-generated relations only, with abstention on timeout or multiple optima.

## OpenAI-compatible boundary

~~~text
CAD2GIS_OPENAI_BASE_URL=https://provider.example/v1
CAD2GIS_OPENAI_API_KEY=<user secret>
CAD2GIS_OPENAI_MODEL=<user model>
CAD2GIS_OPENAI_CAPABILITY=json_schema|tool_call|json_object
CAD2GIS_OPENAI_VISION=0|1
CAD2GIS_OPENAI_TIMEOUT_S=60
CAD2GIS_OPENAI_MAX_CONCURRENCY=4
CAD2GIS_OPENAI_MAX_RETRIES=3
CAD2GIS_OPENAI_MAX_INPUT_BYTES=262144
CAD2GIS_OPENAI_MAX_COMPLETION_TOKENS=4096
~~~

Use the OpenAI Python SDK with base_url and Chat Completions as the compatibility
baseline. Probe structured-output/tool/vision support in curate. Cache keys
include source hash, evidence digest, candidate IDs, prompt/schema/model/base
URL profile, and crop hash. Any mismatch makes cache/registry stale and fails
closed. Never log keys or Authorization headers.

## QGIS expectations

Emit reviewed APD-derived SVG markers for new/existing pole variants, FAT, FDT,
and homepass; QML for all delivery layers; distinct 24C/48C cable styles; source
labels for FAT/FDT/homepass/cable capacity; and a portable QGZ project. Preserve
source rotation and use collision-aware label placement. Never display generated
internal IDs as source labels.

## Inputs still unavailable

Do not fabricate:

- cloud API key, base URL, model, or provider capabilities;
- authoritative source CRS;
- human domain acceptance of semantic candidates;
- official business attributes absent from the DWG;
- whether BOUNDARY CLUSTER represents ZPM;
- ZNRO evidence if exhaustive extraction finds none.

Distillation must work without cloud credentials. Curate should fail clearly
when credentials are absent.

## Protected files and state

Never edit/delete/stage the official DWG, .dwl/.dwl2 files, ErrorReports,
existing experiment output GPKG/report, JAD data/report, unrelated user changes,
or anything under E:\aaaCAD2GIS without explicit request. Write new artifacts
under a separate run/output path. Never overwrite until atomic replacement
succeeds.

## Risks

| Risk | Fail-safe behavior |
| --- | --- |
| Open/locked DWG | Read-only copy, preserve locks, hash check |
| Dynamic anonymous blocks | Preserve effective name, definition fingerprint, properties, transform, path |
| Missing xref/proxy support | Typed missing/unsupported record, no silent drop |
| Required but absent business data | UNAVAILABLE provenance, no invention |
| Plausible but unproved CRS | Explicit user source profile |
| Partial OpenAI compatibility | Capability probe and strict schema |
| Malformed/hallucinated cloud output | Reject before cache/registry |
| Stale cache/registry | Digest mismatch fails closed |
| Legend resembles plan | Role segmentation before feature emission |
| Interrupted write | Temporary sibling plus atomic replace |
| APD overfitting | State one-DWG scope; no generalization claim |

## Sources

Local: docs\Literature_review_lite.md, the XA-202610 competition PDF, and all
papers under paper. External design references:

- C2G: https://www.mdpi.com/2227-9709/9/2/42
- OGC GeoPackage 1.4: https://docs.ogc.org/is/12-128r19/12-128r19.html
- ISO 19107: https://www.iso.org/standard/66175.html
- ISO 19157-1: https://www.iso.org/standard/78900.html
- OGC MUDDI: https://docs.ogc.org/is/23-024/23-024.html
- ITU-T L.250: https://www.itu.int/epublications/publication/itu-t-l-250-2024-01-topologies-for-optical-access-network
- W3C PROV-O: https://www.w3.org/TR/prov-o/
- OpenAI Python SDK: https://github.com/openai/openai-python
- OpenAI Chat API: https://platform.openai.com/docs/api-reference/chat/create
- JSONSchemaBench: https://arxiv.org/abs/2501.10868
- Selective classification: https://jmlr.org/papers/v11/el-yaniv10a.html
- OWASP prompt injection: https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html

