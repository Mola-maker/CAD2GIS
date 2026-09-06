# CAD2GIS onsite conversion upgrade: final code review

## Verdict

- `codeQualityStatus`: **CLEAR**
- `recommendation`: **APPROVE**
- `blockers`: **none**
- Review date: 2026-09-05
- Review scope: reader preservation and GEODATA authority, AI onboarding, annotation-family derivation, semantic field/geometry lineage, canonical publication, MCP/doctor identity, Web review wording, plugin contract, and the nine-drawing operational artifact audit.

The tracked deletion of `official/AGA-Al Baraka TR2.dwg` is excluded from this verdict. The user explicitly identified it as their own intended working-tree change.

All nine selected conversions have an immutable artifact audit with status `PASS`. Every conversion remains `CONDITIONAL`, because surveyed GCP checks were not supplied and the pipeline correctly refuses to claim independently verified absolute positional accuracy.

## Findings by severity

### CRITICAL

None.

### HIGH

None remain.

The review found and required fixes for the following HIGH issues before approval:

1. Bridged CABLE geometry was marked `DERIVED_ROUTE` while the canonical source-geometry validator accepted only `SOURCE_ROUTE`, causing real drawings 04, 05 and 06 to fail. `src/cad2gis/cad2gis_v3/curve_geometry.py:479`, `src/cad2gis/cad2gis_v3/semantics.py:2026` and `src/cad2gis/cad2gis_v3/pipeline.py:311` now constrain bridging to one endpoint of an open straight source route and replay the exact audited source/support/displacement evidence.
2. Canonical run directories were published from protected Windows temporary directories and were unreadable by the desktop user. `src/cad2gis/cad2gis_v3/artifact_io.py:14` resets inheritance before publication, and `src/cad2gis/cad2gis_v3/pipeline.py:244` does this before any existing destination is renamed. Failure preserves both staging and any prior published run.
3. Equal-width POLE-ID samples with different field structures were merged and then dropped during regex derivation, stopping drawings 03 and 09 at L2. `src/cad2gis/cad2gis_v3/family_validation.py:63` now groups by token width and token-kind signature; the unchanged L2 gate still requires each asset sample to match exactly one family.
4. The review launcher could accept a stale server on the same run path without identifying its runtime, exit successfully with fewer than nine selected drawings, and leave a socket-owning child behind when a Windows virtual-environment launcher was killed without its tree. `tools/start_onsite_reviews.ps1` now requires the exact unique set `drawing-01` through `drawing-09`, rejects occupied ports, and terminates only the process trees it started if launch fails.
5. A partition Web review inherited the parent run's 375-row delivery census even though the selected EMR GeoPackages contain 20 and 38 rows. `src/cad2gis/review_server.py:1248` now binds the selected region to the parent `delivery_partitions` record, verifies the partition manifest counts and delivery SHA-256, reports the partition census, and explicitly retains parent-run source and validation provenance.

### MEDIUM

None remain.

### LOW

None remain.

## Correctness review

- `src/cad2gis/reader/libredwg_cli.py:124` changes only strings containing lone surrogate code units, stores reversible `surrogatepass` evidence, and allocates escaped block names against downstream normalized key spaces. This prevents two different definitions from collapsing after `strip().upper()` or `casefold()`.
- `src/cad2gis/reader/autocad.py:3414` retains AutoCAD as the authority for entities, text and geometry while attaching only hash-bound GEODATA metadata from LibreDWG. Conflicting or multiple registrations fail closed; source SHA is checked around the companion read.
- `src/cad2gis/cad2gis_v3/onboarding.py:84` admits an unlisted projected CRS only from normalized `DWG_DIRECT:GEODATA` evidence. `src/cad2gis/cad2gis_v3/onboarding.py:477` reconstructs the observed profile from the immutable inventory and rejects editable-profile CRS, GEODATA or INSUNITS tampering.
- `src/cad2gis/cad2gis_v3/source_export.py:64` preserves reader diagnostics and protocol identity in the source snapshot rather than silently rebuilding a weaker inventory.
- `src/cad2gis/doctor.py:171` checks the entrypoint belonging to the selected interpreter and reports an unrelated PATH executable only as a warning.
- `src/cad2gis/cad2gis_v3/semantics.py:803` and `src/cad2gis/cad2gis_v3/pipeline.py:1107` account for the exact derived feature keys allowed to cross an annotation-layer boundary. There is no global same-layer bypass.
- Invalid source boundary repair is recorded as a lossy `DERIVED_BOUNDARY_REPAIR` with source validity, vertex/area changes and native Hausdorff displacement; the source entity remains unchanged and review is required.
- `src/cad2gis/cad2gis_v3/semantics.py:1499` marks a nearby integer DEVICE_NUMBER as `DWG_DERIVED`, retains the label entity key, value, distance and target frame, and creates a review-required diagnostic at `src/cad2gis/cad2gis_v3/semantics.py:2585`. Direct owned block attributes remain `DWG_DIRECT`.
- `src/cad2gis/cad2gis_v3/project_profile.py:697` admits only the existing built-in `EMR` semantic class. The recognizer remains the exact source label contract, while other unconfigured class names remain rejected.
- Web copy states that the CAD pane is a delivered-source backtrack rather than a full DWG renderer, and that the GCP similarity preview is not the translation-only export model. This prevents preview RMSE from being presented as final conversion accuracy.
- Partition review keeps the selected delivery artifact and its counts separate from parent-run source evidence. A mismatched region, path, hash, count or boolean count fails before the Web app is created; the review overlay does not mutate either manifest or the delivery.
- The rewritten conversion skill requires runtime identity and `debug_mcp`, rebuilds source evidence for every DWG, treats `CONDITIONAL` as unresolved rather than precise, requires source/delivery visual and field comparison, and forbids guessed GCP coordinates.

## Test and evidence review

The latest focused verification was rerun against checkout source with an explicit source path, avoiding false passes from an older installed package:

- `validation/onsite-corpus-20260905/final-review-device-number.xml`: 50 tests, 0 failures. Covers nearby-derived versus owned-attribute DEVICE_NUMBER provenance, source immutability, annotation gates and geometry lineage.
- `validation/onsite-corpus-20260905/final-review-emr-contract.xml`: 36 tests, 0 failures. Covers the exact EMR built-in admission plus AI onboarding and annotation-family derivation.
- `validation/onsite-corpus-20260905/final-code-review-tests.xml`: 112 tests, 0 failures. Covers the broader reader/onboarding/publication/semantics/doctor/plugin boundary set before the last two bounded fixes.
- `validation/onsite-corpus-20260905/publication-permissions-tests.xml`: 61 tests, 0 failures, including an actual Windows inherited-ACL check and preservation on publication failure.
- `validation/onsite-corpus-20260905/final-review-partition-web.xml`: 16 tests, 0 failures. Covers the reported 20-row partition census, parent source/evidence binding and fail-closed manifest/hash/count mismatches.
- `validation/onsite-corpus-20260905/web-review/partition-metadata-real-artifact-test.json`: both real Drawing 03 partitions return API totals equal to their GeoPackage layer totals (20 and 38), resolve the source, and expose the exact parent-manifest provenance under checkout code.
- `validation/onsite-corpus-20260905/web-review/partition-servers.json`: the final installed-Web runtime serves both real partition GeoPackages on ports 8790/8791; `/api/run` and `/api/layers` agree at 20/38 rows, the source resolves, and the exact parent manifest SHA/status/scope is exposed. Raw endpoint responses are retained beside this record.
- Ruff passed on every final changed production/test file reviewed. `git diff --check` passed; remaining output is limited to line-ending notices.

Superseded failure artifacts such as `final-boundary-tests.xml` and `bridge-integration-regression.xml` are retained as debugging history and are not cited as success evidence.

## Installed-runtime and operational evidence

- `validation/onsite-corpus-20260905/final-runtime/web-binding.json`: `PASS`; final wheel SHA-256 `0752c56d8d9a4ffc7b4a8c8469787c0abafc5f98aa1aca32ecca6341ccf84ee3`; installed files match both wheel and checkout. This supersedes the pre-Web EMR wheel while retaining its conversion fixes.
- `validation/onsite-corpus-20260905/final-runtime/mcp-web/report.json`: `PASS` through the actual isolated stdio MCP transport under `.venv-onsite-web`.
- `validation/onsite-corpus-20260905/final-runtime/doctor-web.json`: conversion profile ready; the current interpreter entrypoint is valid and the unrelated PATH entrypoint is only a warning.
- `validation/onsite-corpus-20260905/plugin-install/installed-web-bundle-proof.json`: final host plugin version `0.4.0+codex.20260905233123` matches all 18 staged source files.
- `validation/onsite-corpus-20260905/final-artifact-audit.json`: 9/9 selected runs `PASS` using isolated installed Python and public `cad2gis.agent_mcp.audit_run`. Its exact-ID/path/hash/count comparison with `delivery-selection.json` also reports `selection_alignment: PASS`. Every manifest artifact hash, main delivery layer census, exact source SHA replay and read-only SQLite GeoPackage integrity check passed.
- `validation/onsite-corpus-20260905/final-artifact-audit/details/`: one complete audit record per drawing.
- Drawing 03 has two additional partition deliveries. Their manifest SHA, SQLite integrity and exact layer counts also pass; each partition contains its expected EMR feature. Across the corpus, 11 GeoPackages contain 3,665 main-delivery rows plus 58 partition rows, 3,723 published layer rows in total. This is a row count, not a claim about unique CAD assets.
- `validation/onsite-corpus-20260905/source-artifact-user-access.json`: the desktop user can read the five published source artifacts for all nine drawings. `validation/onsite-corpus-20260905/native-delivery-user-access.json`: for native-reader cases 03, 07 and 09, the desktop user can read their three main deliveries, three source GeoPackages, three evidence GeoPackages and Drawing 03's two partition deliveries (11 GeoPackages in that narrower access check).
- `validation/onsite-corpus-20260905/geometry-lineage-lamteh-replay.json`: real invalid-boundary replay records the exact lossy repair and leaves source geometry unchanged.
- `validation/onsite-corpus-20260905/autocad-geodata-real-source-check.json`: actual drawings 03, 07 and 09 retain AutoCAD geometry/text while the GEODATA supplement matches source-bound metadata.
- `validation/onsite-corpus-20260905/visual-qa/FINAL_QA.md` and its per-case JSON/CSV/PNG evidence cover all nine main deliveries and both partitions. They found no unexplained above-threshold 2D geometry changes and no source-ledger field-value mismatch. The 31 nearby integer relationships retain their source key, text and distance, but their engineering meaning still requires human review.

The visual audit also identifies material, explained derived changes that must remain visible to an engineering reviewer: Drawing 03 partition EMR28560 source handle `75E1` has an audited endpoint bridge with 7.90816566 m Hausdorff displacement and +7.36382426 m route length; Lamteh includes a 12.29758460 m support-point relocation and a 97.70506889 m invalid-boundary spike repair. The source geometry and operation receipts are retained. An explained operation is not an acceptance of that change or a lossless-conversion claim.

Operational `PASS` means artifact integrity, source identity and layer census are verified. It does not convert the `CONDITIONAL` runs into an absolute-accuracy pass. Coordinate validation remains `not_independently_verified` until surveyed GCP controls and independent check points are supplied.

## Skill-perspective check

The `remove-ai-slops` and `programming` skills were not present in the available-skill catalog, and a filesystem search under the configured skill root found no matching `SKILL.md`; they could not be loaded. I applied the criteria supplied in the reviewer instructions directly.

The final diff does not violate either perspective. The added tests exercise real corpus failures and fail-closed boundaries; they are not deletion-only, tautological, implementation-constant mirrors or brittle prompt tests. The production validation is located at required reader, source-authority, semantic-lineage, publication and installed-runtime boundaries. No untyped escape hatch or goal-unrelated parser/normalizer was added.
