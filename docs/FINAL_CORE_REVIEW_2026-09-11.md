# Final core review — 2026-09-11

Result: **no remaining actionable P1/P2 defect found in the reviewed core changes**. This is a scoped source and evidence review, not approval of every real drawing, platform installer, or geospatial accuracy claim.

Reviewer: independent `upgrade_review` agent. Source was read-only during this final pass; this report is the only file written. No platform build or full test-suite rerun was started.

## Reviewed input

- Worktree: `E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade`.
- Base revision: `1d4086213f8bb882d8ab6cddd64cb1cdf434a6e9`; the release input explicitly records a dirty source tree. The base revision alone does not identify the delivered changes.
- Freeze receipt: `tmp/RELEASE_INPUT_READY.json`.
- Wheel: `dist/semantic-upgrade-final/cad2gis-0.7.0-py3-none-any.whl`.
- Independently recomputed wheel SHA-256: `bbdbfe88b5b0266c2e55ea1c2133e176408da0e697158d3bbf3a0952c5f5257d`, matching the freeze receipt.
- Independently compared the wheel's bytes with current source for all eight reviewed files: `symbol_assets.py`, `symbol_workflow.py`, `application.py`, `redis_runtime.py`, `cad2gis_v3/source_query.py`, `desktop/app.js`, `cad2gis_v3/osm_matching.py`, and `cad2gis_v3/osm_evidence.py`. All eight matched. This verifies inclusion of the reviewed changes; it is not a complete inventory of every installer or plugin.

## Findings and checked boundaries

### Lamteh owner/layout resolution

No P1/P2 found in `src/cad2gis/symbol_assets.py:94`, `:213`, and `:258`.

`_source_layout` follows the actual entity owner chain to a `BLOCK_RECORD`. Attached ATTRIB entities therefore resolve through their INSERT, rather than passing that INSERT to a block-record lookup. Missing owners and cycles produce explicit errors. Only unlinked children inherit the known parent layout. `_closure` distinguishes separate handle-less objects, retains attribute text/owner/layout facts, and handles both block-definition and attached attributes. Identity admission, render admission, and direct rendering use the same resolver.

The new tests at `tests/test_symbol_assets.py:227`, `:285`, and `:306` cover paper-space and nested attributes, source-fingerprint/render changes after text changes, separate unlinked attributes, missing owners, and cycles. Their outcomes are included in the existing passing final suite described below.

The real single-symbol evidence was independently read from:

`E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/lamteh-attrib-single/assets`

Its read-only SQLite record retains INSERT `139933` and ATTRIB `139934`, with `owner=139933`, `tag=FAT`, `text=10`, and `source_layout=Model`. The manifest identifies the LibreDWG → DXF → ezdxf path. Independently recomputed hashes agree with the recorded evidence:

| Evidence | SHA-256 |
| --- | --- |
| Original Lamteh SF DWG | `5b0872935a070cac9b3969811799d229bb48e534085254930263d526db9c5735` |
| Extracted single-symbol SVG | `cba6c54d7b0618f76564db8d29eb16ee96a20f592a4bf27da7abafe005135a8c` |

This confirms acceptance of the formerly failing owner relationship and retention of its attribute facts. It does not establish full Lamteh extraction completeness. The asset remains a candidate, records `source_instance_fidelity_verified=false`, and reports unavailable `simplex.shx` with font-substitution review required. The absence of a crash does not prove matching glyph appearance.

### SVG evidence, unresolved candidates, and baseline

No P1/P2 found in the reviewed extraction/coverage path. Source/DXF/selection fingerprints and identity/render handles are retained. Explicit alternate render geometry and overlay text are marked as such. Ambiguous GROUP membership or unsupported source shapes remain unresolved; the new path does not infer group membership from proximity. Empty/non-vector or incomplete render results do not silently become accepted symbol bindings. Coverage counts describe delivered feature scopes and candidate availability, not survey accuracy or exact visual fidelity.

The inspected changes preserve candidate/review status and source-bound publication checks. No tracked change under `baseline/` was present in the final diff check. This check does not independently re-survey baseline coordinates or certify every external corpus file. OSM evidence remains relative evidence, with `authority=relative_osm` and `absolute_accuracy_verified=false`; inspected source/snapshot/topology checks do not convert OSM fit into a GCP accuracy claim.

### Redis source binding

No P1/P2 found in `src/cad2gis/redis_runtime.py:39`, `:125`, `:164`, and `src/cad2gis/cad2gis_v3/source_query.py:171`, `:402`, `:452`.

Cache identity binds source, snapshot, index, schema, policy, revision, implementation, and query filters. Source/index/cursor validation precedes cache use. Cached envelopes have bounded size, binding/expiry/digest checks, and query-result shape checks; optional connection, timeout, or decoding failures fall back to the authoritative query. File hashing uses the existing file-identity memoization rather than rehashing unchanged files on every cache lookup. Redis is an optional query-summary cache and does not commit semantic state or replace the source/SQLite authority.

Real Redis networking was not exercised by the final suite because `CAD2GIS_TEST_REDIS_URL` was absent. Unit and integration fixtures are evidence for the code paths, not proof of a deployed Redis instance or database performance improvement under production load.

### Semantic jobs, cancellation, and downloads

No remaining P1/P2 found in `src/cad2gis/application.py:237`, `:319`, `:555`, `src/cad2gis/cad2gis_v3/semantic_stage.py:275`, and `src/cad2gis/desktop/app.js:63`.

The semantic action reserves a fresh run, exports source, and prepares candidates. It does not report semantic commit. Subprocess failure, cancellation, and final artifact-validation failure prevent success status. Download admission is limited to the named artifacts inside allowed roots and revalidates source/candidate binding. Inspection did not find a new path that downloads arbitrary files or promotes a partially prepared run to success.

The earlier review's reproducible duplicate-submit defect is fixed: submit events now call `preventDefault()` before the pending guard returns. A focused execution of the actual `on`/`guarded` functions confirmed that both concurrent submit events are prevented. The earlier focused Python run passed 37 tests with one real-Redis skip. These are prior checks in this review task; they were not rerun during the final freeze pass.

## Existing final test evidence

The reviewer independently parsed, but did not rerun:

`E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/full-pytest-final.xml`

The JUnit suite records **1,312 tests: 1,300 passed, 12 skipped, zero failures, zero errors**, with elapsed time 306.237 seconds. The freeze receipt records three warnings.

The twelve skips comprise:

- Two opt-in complex Lamteh DWG reader tests requiring `CAD2GIS_FULL_DWG_TESTS=1`.
- Two Windows symlink tests where symlink creation was unavailable.
- One optional Semarang attribute-regression DWG fixture requiring `CAD2GIS_ATTRIBUTE_REGRESSION_DWG`.
- Four Hutabohu baseline tests whose optional baseline fixture was absent.
- One real Redis integration test requiring `CAD2GIS_TEST_REDIS_URL`.
- Two POSIX executable-mode checks on Windows.

These skips remain verification boundaries. The single real Lamteh symbol receipt does not replace either full complex-DWG test, the missing baseline corpus, source-font visual acceptance, or GCP comparison. Actual Windows/Linux/Android installation and mobile network/user-flow verification belong to the separate platform handoff and are outside this review verdict.
