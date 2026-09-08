# Code review fixes and Kletek validation — 2026-09-08

The fixes are integrated on remote `main` commit
`1c3d45f73764d896fb880d5f94fe03fed4d4279d`. The initial local audit used
`3d3549776a6aa275e4076f1f25d18b1d22cdb164`; all 12 intervening remote commits
were retained, including their source replay, semantic revision and canonical
registration preview implementations.

## Findings addressed

| Finding | Resulting behavior |
| --- | --- |
| B01: published runs could be replaced while aliases retained old verification | Conversion, source and semantic manifests prevent output-directory reuse; admission and publication both check. |
| B02: native CAD review reconstructed parent vertices for every segment and chordified curves | Feature-native points and canonical materialization identify each route segment; incomplete legacy curves fail explicitly. Straight endpoint bridges and non-cable lineage remain compatible. |
| B03: domain admission ignored reviewed coordinate scale | Domain checks operate on a derived view in CRS axis units; immutable source coordinates remain unchanged. |
| B04: indexed nearest matching missed a near-tie outside tolerance | The candidate radius includes the full 1 cm ambiguity neighbourhood. |
| B05: auxiliary spatial/delivery configuration was not snapshot-bound | Presence, absence and content of both configuration files are verified; conversion no longer rewrites its own spatial input. |
| B06: raw local WCS received a projected CRS label | Mixed-space, scaled or GEODATA inventories receive undefined source SRS. The upstream writer's `SRID=-1` support is retained. |
| B07: external deployment selection still imported bundled backend/reader modules | Parent package lookup and namespace reader paths select the actual deployment; incomplete or already-mixed deployments fail explicitly. |
| B08: moving a run invalidated otherwise intact evidence indexes | Canonical graph and index sidecars rebase inside the copied run, with full digest and source checks. |
| B09: index verification cache ignored manifest changes | The newer upstream manifest-digest resolver is retained, with additional MCP entry-point regression coverage. |
| B10: `#` in SQLite paths changed URI meaning | All index readers use escaped, read-only file URIs; tests also cover `%` and unwanted file creation. |
| B11: WebSocket reconnect displayed stale controls as synchronized | Handshake refreshes current controls; response ordering and synchronization status are guarded. |
| O01: exhaustive disjoint route-pair intersection checks | An STRtree filters segment candidates while preserving exact predicates and result order. |
| O02: full-file source hashing allocated another DWG-sized buffer | Snapshot hashes stream 1 MiB chunks while retaining the pre-publication recheck. |

Integration testing also found a Windows publication failure in the newer
style formatter: a SQLite connection context manager committed transactions
but did not close the database. Explicit closure releases its file handle.
Whole-directory publication now retries Windows access/sharing errors at most
six times, with at most 2.5 seconds of backoff per rename. Persistent errors
still raise and use the existing rollback path. Tests cover retained connection
references, a real Windows directory lock, persistent failure and existing data
preservation. The reviewed policy guard is active before ingest.

Independent core and interface reviews found no remaining blockers in their
bounded scopes after these corrections.

## Real drawing and configuration

- Drawing: `APD - KLETEK RW 05 SIDOARJO.dwg`, 308,753 bytes.
- Source SHA-256: `756f6e5bb4feb61fed2d86046478e0fb714434628bd0f17db4d78fef0b221016`.
- Reader: pinned LibreDWG 0.14 CLI with ezdxf; Python 3.12 on Windows.
- Source inventory: 1,538 entities; model census 527 entities, 53 inserts,
  zero dimensions.
- Nominal transformation: DWG GEODATA → EPSG:32749 → EPSG:3857.

The newer pipeline rejects the historical inventory binding. A separate
temporary copy of `baselines/kletek/config` was reconciled against the current
reader: every extracted entity retained its previous native coordinates and
text, the DWG SHA was identical, and the exact model census passed. Only the
inventory hash and review provenance were refreshed. Semantic rules, coordinate
contracts, thresholds and exact count gates were retained. The original
baseline files were not edited.

Old inventory digest:
`929f58d1b09da9f52831af617c1842114dc9207a6e33394e63412ef480dfce11`.
Current inventory digest:
`bf44ebd977cf650b5e9a9b475eb1481547b6dcaa651dffccdf0a9fe0cd15744a`.
This reconciliation authorizes regression execution, not surveyed acceptance.

The final conversion successfully published
`tmp/review-fix-20260908/kletek-verified/`:

| Layer | Features |
| --- | ---: |
| SITE | 1 |
| BOITE | 9 |
| PTECH | 33 |
| IMB | 167 |
| INFRASTRUCTURE | 9 |
| CABLE | 33 |
| ZPM | 9 |
| ZNRO | 2 |
| Total | 263 |

- `audit_run`: **PASS** for artifact hashes and layer census.
- Conversion snapshot verification: **PASS**.
- Source geometry, topology, measurements and segment length closure: **PASS**.
- All 33 native cable segments match the ledger materialization, including
  vertex counts; coordinate comparison tolerance is `1e-9` native units.
- All eight native and geographic layer views load, with matching counts.
  The older Kletek run's eight native views also remain usable.
- Delivery, source, evidence and evidence-index SQLite integrity: **ok**.
- Raw source layers: undefined Cartesian SRS (`srs_id=-1`), preserving local WCS.
- Exact comparison against the latest remote baseline's generated delivery:
  **equivalent=true**, no differing layers. The comparison includes complete
  feature schemas and rows, binary geometry, labels, lengths and lineage.

The remote baseline completed its delivery file but failed Windows directory
publication because of the open connection described above. That retained,
complete GeoPackage is the comparison input; the final fixed run successfully
publishes and passes the separate whole-run audit.

Implementation digest:
`e8612e18cd73b63cac21eeb7c699880f8bbd9fda8e12373402b713fadb1f422d`.
Conversion snapshot digest:
`3acabb3cb169b5f78d6523de099dbffda550a0c01278e2a6d380b77e5cbf684b`.

The run is **CONDITIONAL**. No surveyed independent GCP was supplied; numerical
closure, metadata-backed registration and equivalence do not establish
absolute positional accuracy. Kletek has no curved cable source segments, so
the curved native-view behavior is additionally exercised with actual GDAL
GeoPackage fixtures containing a bulge arc.

## Completed QGIS delivery

The initial validation stopped at the canonical conversion run and omitted the
QGIS delivery step. The completed deliverables now also include:

- `tmp/review-fix-20260908/kletek-qgis-delivery.zip`: the portable delivery
  directory with `delivery.qgz`, the unchanged GeoPackage, eight QML files,
  eight attribute tables, a delivery manifest, README and checksums.
- `tmp/review-fix-20260908/Kletek-RW05.qgz`: a **standalone project**, with the
  GeoPackage attached inside the QGZ and all eight layers, styles, labels and
  the initial map extent saved. Only this QGZ is needed to open the map.

The standalone QGZ is 147,841 bytes; SHA-256:
`6c19753333c404aff419241833514b28b168d1734038105eac63ddf6f2842bcc`.
Its attached GeoPackage retains the canonical delivery SHA-256:
`38adccb40cc7d70358fb5ecd93b58a2572d6400987875663c449de833705cb68`.
All 20 file entries in the portable directory's checksum manifest also match.

Packaging used the repository's existing `batch package` and `qgis_package`
entry points. A new, independent **QGIS 3.34.4 process on Ubuntu 24.04 WSL**
then verified the standalone file after the packaging process exited:

- Relocating the QGZ by itself loads all eight layers and all 263 features.
- Every data source resolves to an internal attachment; an intentionally
  invalid adjacent `delivery.gpkg` is ignored.
- Attachment hashes, numeric display precision, cable label expressions and
  the saved initial extent pass validation.
- An actual first-open render completes without renderer errors and has
  6,141 nonwhite samples. The image was inspected; labels are readable.
- Reading and saving again with QGIS preserves attachment references.

The verifier exposed a Qt compatibility bug: `QFontDatabase.families()` is an
instance method in Qt 5 and a static method in Qt 6. The verifier now falls back
to an instance for Qt 5, retaining the original Qt 6 call and the empty-font
failure check. The corrected independent QGIS verification passes, and the
related delivery, equivalence and symbol regression suite passes **44 tests**.
The real render loaded the existing Windows Arial font for that process only;
fonts are not redistributed in the QGZ. Source-specific SVG remains optional.

Evidence is in `Kletek-RW05.verification.json` (packaging receipt) and
`kletek-qgis-check/verification.json` plus `first-open.png` (independent check).
The run's **CONDITIONAL** positional-accuracy status remains unchanged.

The follow-up also found the original run's reasoning directory nested under
`qgis/reasoning`, outside the paths recorded in its manifest. After confirming
the original artifact hashes, copies were restored to `run/reasoning`; the
existing nested directory was retained. The original run audit, frozen snapshot
and 33-segment native geometry verification pass again.

## Automated checks and performance

The final complete installed-runtime regression suite passed **695 tests**, with
**11 explicit skips** for missing external DWG/AutoCAD/Hutabohu fixtures and
**4 existing dependency deprecation warnings**. This includes the bounded
publication retry and snapshot publication checks. The aggregate result is
recorded in the local `release-tests.xml` evidence.

Ruff, Python compilation, JavaScript syntax checks and
`cad2gis doctor --deep --strict --profile full` pass. The doctor reports all
conversion, review and agent profiles ready. A current 0.4.0 wheel builds;
SHA-256: `08996af48140c54a61c11f24669fde0845d3b11084db44c60c3ffc8bdb1dfff8`.
This is a successful build, not a claim that two independent builds were
compared for reproducibility.

A three-repeat median microbenchmark on disjoint one-segment routes compared
the original function at `3d35497` with the indexed implementation:

| Routes | Before | After |
| ---: | ---: | ---: |
| 250 | 0.0815 s | 0.00848 s |
| 500 | 0.3611 s | 0.01692 s |
| 1,000 | 1.6625 s | 0.05443 s |

For a 32 MiB file, peak Python allocation during source hashing fell from
33,563,565 to 2,106,961 bytes, with identical SHA-256 and byte counts. These are
local function microbenchmarks, not whole-drawing speed claims or process RSS.

## Local reproduction evidence

Generated drawings, databases, runtime dependencies and test output remain in
the ignored `tmp/review-fix-20260908/` directory. Relevant files are:

- `kletek-verified/run_manifest.json` and `delivery.gpkg`;
- `kletek-validation.json`, `final-delivery-equivalence.json`;
- `inventory-binding-review.json`, `latest-source-inventory.json`;
- `doctor-final.json`, `release-tests.xml`, `performance.json`;
- `verify_kletek.py`, `migrate_validation_profile.py`, `measure_performance.py`.

With the project's native runtime installed, the conversion and comparison are:

```powershell
python -m cad2gis convert "raw/APD - KLETEK RW 05 SIDOARJO.dwg" --project tmp/review-fix-20260908/kletek-latest-project --run-dir tmp/review-fix-20260908/a-new-run --json
python tools/verify_delivery_equivalence.py tmp/review-fix-20260908/kletek-latest-before-delivery.gpkg tmp/review-fix-20260908/a-new-run/delivery.gpkg
python -m pytest -q
```

Each conversion must use a fresh output directory. Full Windows execution was
used for the real conversion because the restricted process could reject the
publication rename. The PostGIS first-insert concurrency candidate from the
initial audit remains outside this validated scope; no live PostGIS database
was available in this run.
