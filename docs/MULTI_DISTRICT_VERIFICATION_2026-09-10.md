# Multi-district DWG conversion and real-environment verification

This change starts from main `32ad71ec1cb94838291a3f6706fdadb5586dcf27` and addresses district discovery, portable QGIS delivery, current native-reader profiles and large-file memory pressure. The earlier worktree is preserved. No raw source DWG is changed.

## What the three-area example establishes

The supplied Manado–Tomohon uplink drawing contains the MAIN route and the two named areas EMR28560 and EMR29619. Its existing reviewed configuration already declares those two geographic partitions. These are three GIS districts, not three independent print-layout coordinate systems. The current reader reports 2,849 Model records and 198 APD paper-layout records; the latter are predominantly design-summary and title-block content.

The application previously opened one run at a time without exposing its sibling districts. Packaging discovered children by a filesystem glob, so an absent declared child could disappear from the package. The application now offers a district selector with each district's own feature count and availability. It remains visible when the map workspace is expanded. Opening a district retains the ordinary path, artifact and session checks; a retired review session cannot mutate the newly selected run.

Packaging enumerates the parent's declarations, verifies child manifests and GeoPackage hashes, and refuses incomplete district sets. Multi-district packages include `all-districts.qgz`, with independent layer groups, unique layer IDs, relative database paths and a combined map extent. Original GeoPackage bytes are preserved.

Invalid partition JSON, duplicate or unsafe identifiers, malformed bounds and overlapping declared rectangles fail explicitly. Optional absent partition configuration still means a single district. This does not infer districts from every paper layout: title blocks, legends, duplicate viewports and non-geographic diagrams remain source evidence.

## Current reader profile migrations

Three saved profiles described an older layout census and no longer passed source binding under the current native reader. New source-bound onboarding revisions are stored alongside their agent review receipts in each baseline's `reviews/20260910-native-layouts` directory. Previous configurations remain in Git history. These receipts describe an agent's metadata and semantic review, not human survey approval.

| Drawing | Previous model entities / inserts | Current model entities / inserts | Registration evidence |
| --- | ---: | ---: | --- |
| Manado–Tomohon | 2,970 / 127 | 2,788 / 125 | Native GEODATA, EPSG:3857 |
| Tinggar | 2,845 / 156 | 2,342 / 108 | Native GEODATA, EPSG:21148 |
| Semarang subfeeder | 571 / 56 | 118 / 28 | Native GEODATA, EPSG:32749 |

Observed pole annotation families omitted by the first migration proposal were added from their exact source layer, class and deterministic text patterns. The existing semantic and length gates were retained. Expected counts come from the current source-bound onboarding compilation.

## Large-file behavior

Real Lamteh runs exposed aggregate graph hashing and JSON encoding that simultaneously allocated graph dictionaries, a full JSON string and UTF-8 bytes. CAD scene and evidence graph identities now hash one record at a time. Artifact output streams JSON while preserving the existing plain and deterministic gzip bytes. Serialization failures leave any previous artifact intact.

Source, evidence and warehouse GeoPackage compaction now writes a validated sibling database on the destination drive and replaces the staged database only after its connection closes. This avoids putting the full temporary VACUUM database on a nearly full Windows system drive. It does not alter a global SQLite temporary-directory setting. See SQLite's [temporary-file documentation](https://www.sqlite.org/tempfiles.html) and [VACUUM INTO contract](https://www.sqlite.org/lang_vacuum.html).

Memory pressure still depends on drawing complexity and available host resources. Run the large corpus serially. Resource regressions check peak allocations, unchanged canonical identities, database contents, deterministic metadata and preservation of the original on publication failure.

## OSM and accuracy boundaries

Each district retains its own matching decision. A matched MAIN district does not authorize applying its translation to an ambiguous inset. The independent audit now recognizes a source-bound, zero-shift nominal district inside an aggregate OSM result and still rejects mismatched source identity or a fabricated nonzero shift.

In Manado, MAIN accepts a small relative OSM translation; EMR28560 and EMR29619 retain nominal registration because their candidates are ambiguous. The OSM maps use the saved real OpenStreetMap snapshots so network changes cannot influence the comparison. Buildings and roads are reference layers. No independent surveyed control points were supplied, and every result remains **CONDITIONAL**. Source fidelity, explicit derived/relocated features, length evidence and absolute positional accuracy are separate claims.

## Reproduce the supplied corpus

Install the branch in an isolated Python 3.12 environment with the full conversion dependencies. Configure the actual LibreDWG executable, then run `cad2gis doctor --deep --strict --profile full --json`. Use the original nine DWGs and the checked-in source-name map; matching is selected by source SHA-256, never by filename alone.

From the repository root:

```powershell
python tools/verify_upgrade_corpus.py `
  --input E:\source_dwg `
  --baselines baselines `
  --source-names docs/NINE_DWG_SOURCE_NAMES.json `
  --source-snapshots E:\branch_CAD2GIS\validation\multipage-20260910\source-snapshots.json `
  --output E:\cad2gis-results\nine-dwg `
  --matching osm --jobs 1
```

For a repeatable offline OSM comparison, add `--osm-data-dir <snapshot-directory>`. That directory must contain `<baseline-id>.json` for every requested drawing. Missing snapshots fail explicitly. `--source-snapshots` maps baseline IDs to the existing native snapshot directories; it does not rewrite source manifests or relax path/hash validation. Alternatively, `--reuse-exports` accepts a tree already arranged as `<baseline-id>/source`. Filename-bound inventories are rebuilt from verified native records.

The source-snapshot map above belongs to this local verification workspace. On another machine, extract and onboard each source against its own current native inventory, or use that machine's already validated native snapshots. Do not simply relocate path-bound source snapshots or overwrite inventory hashes to satisfy a saved baseline.

A fresh Semarang extraction exposed a reader-text difference: Model-space MTEXT handle `21D6C` had a trailing `C` in the earlier snapshot, while the fresh extraction and three additional native reads ended at `}`. The rest of the compared annotation census was unchanged. Source binding correctly blocked using the older profile with the new inventory. This is a remaining native-reader text-repeatability limitation, not permission to strip text or approve a new inventory automatically. The documented snapshot replay was exercised through the generic corpus command and passed. `evidence/semarang-reader-repeatability-20260910.json` retains the observations; fresh-reader determinism is not claimed for this file.

Audit every declared district with `tools/verify_multidistrict_delivery.py`. It accepts `--runs`, `--sources`, `--output` and repeated `--drawing <id>` arguments. Runs are `<runs>/<id>/run`; snapshots are `<sources>/<id>/source`. Missing manifests, missing districts, database count disagreements, unexplained geometry changes, unexpected field mismatches, unsupported label provenance or mutated immutable artifacts are failures. An audit passing these checks does not turn CONDITIONAL coordinate accuracy into surveyed acceptance.

Open a completed run in the application's map workspace and use **图页 / GIS 分区** to switch districts. Use `cad2gis batch package --help` for portable packaging. In multi-district packages, open `all-districts.qgz` to see every district together or open each district's `delivery.qgz` for its own extent. Keep adjacent GeoPackages and styles together.

The verification delivery also contains standalone `OSM-review.qgz` projects with embedded CAD and cached OSM data, fresh-process QGIS PNG renders, source-audit summaries and checksums. `index.html` links every project and image; `nine-dwg-verification.png` is the contact sheet, and `manado-three-districts.png` shows the three Manado districts separately.

## Verification evidence

The final machine-readable receipt is `evidence/multidistrict-20260910.json`. It records original source hashes, per-district feature counts, audit outcomes, installed runtime checks and QGIS reopening/render verification. Full local source/evidence databases and intermediate failure traces are retained outside Git under `E:\branch_CAD2GIS\validation\multipage-20260910`.

Verification uses native Windows Python 3.12.13, the installed CAD2GIS package, LibreDWG 0.14, bundled GDAL and QGIS 4.0.3. The headless QGIS harness explicitly loads the installed Arial fonts. QGIS builds and verifies each project in separate processes; the browser exercises the actual local application and district selector. The cached host plugin is a separate older installation and is not claimed to have been upgraded by the installed-server probe.

The repository suite runs against the checkout's source package. An exploratory attempt to run that suite against the installed wheel mixed installed classes with tests that intentionally reload checkout modules and failed module-identity checks; that mode is not used as the repository-suite result. Installed behavior is verified separately by the real nine-DWG pipeline, doctor, stdio MCP probe and local application.
