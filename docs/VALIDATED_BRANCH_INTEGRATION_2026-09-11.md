# Validated branch integration, 2026-09-11

Integration starts at main `6251449`, merging Linux/Claude verification `76e5fed` through `3fc339a`. Old branch tips remain available for historical reproducibility; equivalent squashed commits are not replayed over newer contracts.

| Branch / source | Resolution and implementation |
| --- | --- |
| `codex/multi-page-gis-20260910` (`065c159`) | Already main: explicit district scopes, combined QGIS project, district selector, target-drive GeoPackage compaction, graph hash streaming. Retained alongside canonical GDAL timestamps. Fixed invalid JSON and missing target CRS in Tinggar/Semarang baseline profiles; restored the previously declared EPSG:3857 delivery CRS. |
| `fix/linux-claude-verification-20260911` | Merged: SQLite connection closure and session event index, safe precommit annotation diagnostics, canonical GDAL clock, narrow-screen focus control. |
| `robustness` (`8700c05`, `2e3768d`, `cb73aa9`, `5813151`) | Adapted useful memory changes: early reader inventory/index release; serialize each immutable graph node/edge without constructing a second complete fact tree. Preserve canonical plain/gzip bytes and graph identity. Main already streams hash calculation. Historical Docker logs, duplicate packaging and unconditional generated-EMR exemptions are not production changes to import. |
| `feature/land-robustness` (`afb21ec`) | Restored missing `scene_visual.py` and `scene_interpretation.py`, source-export visual bundle, four MCP scene tools and tool contract. Hash-bound role proposals never approve source exclusion. Existing main iteration, recovery, semantic and CAD graph implementations retained. Legacy Windows-only QGIS process launcher and AutoCAD scripts do not replace the portable desktop/native reader. |
| `feature/road-match-locator` (`ff8bafb`) | Experimental P1/P2 FFT and topology-seed benchmarks retained on their branch. Its own report marks three of four baselines INCONCLUSIVE and records a 6,391m inconsistent village match. Production keeps the newer OSM precision/ambiguity gates from merged PR7; do not promote experimental transforms into authority. |
| `CAD2GIS_Linux` (`7a5728c`) | Separate July history, no common ancestor. Modern portable runtime and Linux verification supersede its migration snapshot; no unrelated-history tree replacement. |
| `feature/hero-motion`, `feature/terminal-replay`, `feature/more-real-demos`, `fix/plugin-installation-docs` | Already squashed via PR1/2, PR3, PR6 and equivalent installation-doc commit. Preserve main's later fixes. |
| `codex/ai-database-architecture-upgrade`, `codex/osm-precision`, `codex/osm-matching-accuracy-20260910`, `newmodel`, `plugin-data`, `fix/real-dwg-verification-20260909` | Ancestors of main; preserve current implementations and contracts. |

## Output and review

- Normal `cad2gis batch run` produces `index.html` and `batch-report.json` automatically and selects the focus comparison PNG before the full-source overview.
- `cad2gis batch report --run-dir /path/to/run --run-dir /path/to/another/run --output /new/review` assembles existing conversions, copies visual evidence and creates derived PNG previews without modifying sealed runs.
- `tools/verify_upgrade_corpus.py` writes `review/index.html` automatically, including blocked/failed drawings. A report is a review index, not engineering approval.
- SVG candidates now contain both vector `.svg` and centered 512×512 `.png`, PNG hashes in the manifest and PNG previews linked to SVG originals. Rasterization uses pinned `resvg-py==0.5.0` ([upstream API](https://resvg-py.readthedocs.io/en/latest/api.html)); no font/network lookup during rasterization.
- Visual audit uses available Noto CJK, Windows or macOS fonts before a portable fallback; source/delivery panes retain the same CAD frame, center and scale.

## SQLite, Redis and agents

SQLite remains durable authority. Review connections close explicitly, event pagination uses a session index, and GeoPackage compaction stages on the target volume. Android retains its existing offline SQLite/client protocol. Per user instruction, Redis remains the existing design in `AI_DATABASE_ARCHITECTURE_PLAN_2026-09-05.md`; no Redis implementation or service is claimed.

The existing Claude configuration remains available. This integration does not call the paid model after its earlier balance exhaustion. Local MCP protocol, scene evidence hash binding and plugin tool-contract tests verify the software path independently.

## CI repair

Run `34574052332` showed successful Linux/macOS tests but artifact quota failures, plus two Windows timestamp-test failures caused by unclosed test SQLite connections. The fixture now closes its connection. JUnit summaries are persisted to the Actions job summary; artifact upload alone is best-effort with three-day retention. Test, runtime, lint, packaging and WebDemo build failures remain fatal. No existing remote artifacts are deleted and no branch protection is bypassed.

Local evidence and the latest CI links are recorded in the final verification receipt after the integrated run completes. Survey-grade absolute registration and Android device execution remain separate from local regression and rendering checks.
