# CAD2GIS software and real-use verification — 2026-09-09

**Software regression checks passed; the drawing outputs are not all accepted.** All nine supplied DWGs were read and converted, producing 3,664 main-delivery features. Artifact audits pass for all nine. Six runs are CONDITIONAL and three remain UNSAFE under the existing coordinate round-trip gate. No result is certified for surveyed positional accuracy.

The source is commit `e820c4c` plus the local fixes below. The nine original DWGs in `/home/jlu/下载/datadwg` were read without modification; all nine SHA-256 values were rechecked after verification. Their contents exactly match the corresponding repository baseline source bindings.

Local software fixes:

- AI settings now save accepted provider aliases under the canonical provider key, so saving `new_api` remains accessible as `new-api` after restart.
- The read-only SQLite browser displays positive/negative infinity as text instead of failing JSON serialization. Stored values remain unchanged.
- Packaged README files now display the manifest’s actual run status. An UNSAFE run is no longer mislabeled CONDITIONAL. Regression coverage includes CONDITIONAL, UNSAFE, FAILED, and VERIFIED.

Changes: [patch](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/changes.patch). The repository baseline configurations were not overwritten.

The verification environment was Linux x86-64, Python 3.12.14, LibreDWG 0.14, GDAL 3.13.1, PROJ 9.8.1, and Node.js 22.14.0. The machine initially had unsupported Python 3.14 and Node.js 10; supported runtimes and dependencies were provisioned in isolated directories. The full runtime doctor passed after installing the pinned LibreDWG reader.

Final automated results:

- **872 passed, 14 skipped** in the full CI-style editable suite.
- **71 passed** in the separately installed wheel’s desktop, HTTP, WebSocket, MCP, and packaging checks.
- Ruff, Python compilation, JavaScript syntax checks, and `git diff --check` passed. The WebDemo build succeeded.
- The built wheel was installed separately. A real Kletek DWG conversion using that installation passed its artifact audit and exactly matched the editable conversion’s feature schemas, geometry BLOBs, labels, lengths, and lineage.

Explicit skips cover unavailable external APD_test/Hutabohu evidence and Windows-only AutoCAD/DACL/sharing-lock checks. Windows/macOS installation and Android device execution were not performed on this Linux machine. A broad installed-wheel experiment initially produced 34 failures because checkout-forcing test fixtures replaced modules already imported from the installed package; its log is retained. The clean installed smoke suite and independent installed conversion passed. This experiment is not counted as a passing full-wheel suite.

The real workflow exercised source export, draft bootstrap, evidence inventory, source-specific configuration validation, conversion, GIS/SVG publication, artifact hashing, SQLite integrity, and packaging. Historical configuration failures are retained. Separate `current-reader-project` copies bind the fresh inventories and current model-role counts to each identical DWG, and use the DWG’s embedded GEODATA/CRS where older profiles disagreed. Semantic mappings, feature-count gates, and numeric tolerances were preserved. These are documented regression configuration migrations, not a new business-label or ground-control acceptance.

| Drawing | Features | OSM result | Run status | GIS package |
|---|---:|---|---|---|
| lamteh_sf | 56 | abstained | **UNSAFE** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/lamteh_sf-UNSAFE.zip) |
| lamteh_main | 1,000 | abstained | **UNSAFE** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/lamteh_main-UNSAFE.zip) |
| manado-tomohon_uplink | 375 | matched | **CONDITIONAL** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/manado-tomohon_uplink-CONDITIONAL.zip) |
| tinggar | 715 | abstained | **UNSAFE** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/tinggar-UNSAFE.zip) |
| kletek | 263 | unavailable | **CONDITIONAL** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/kletek-CONDITIONAL.zip) |
| semarang_sf | 39 | matched | **CONDITIONAL** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/semarang_sf-CONDITIONAL.zip) |
| taipa | 673 | matched (frozen replay) | **CONDITIONAL** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/taipa-CONDITIONAL.zip) |
| tinggede | 515 | matched (frozen replay) | **CONDITIONAL** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/tinggede-CONDITIONAL.zip) |
| darat_sekip_sf | 28 | matched | **CONDITIONAL** | [ZIP](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/deliveries/darat_sekip_sf-CONDITIONAL.zip) |

Feature totals count the nine main deliveries; Manado partition packages may repeat assets already present in the main result. Three matches were obtained in the online workflow, while Taipa and Tinggede replayed the repository’s frozen source-bound OSM profiles. Both Lamteh drawings and Tinggar abstained from automatic alignment because it was weak or ambiguous. Kletek’s online request did not obtain road data. The software retained these statuses instead of claiming a successful match.

The three UNSAFE results exceed the unchanged `1e-6 m` internal round-trip threshold:

| Drawing | Maximum round-trip error | Internal threshold |
|---|---:|---:|
| lamteh_sf | 2.8831592016e-05 m | 0.000001 m |
| lamteh_main | 2.88425189891e-05 m | 0.000001 m |
| tinggar | 0.000452014109997 m | 0.000001 m |

These are internal coordinate transformation consistency errors, not measurements of field-position error. Lamteh uses EPSG:23846 with an ID74→WGS84 operation; Tinggar uses EPSG:21148 with a Batavia→WGS84 operation. The recorded forward-engine crosschecks are zero. These datum-transform round trips need investigation before those outputs are accepted; no threshold was relaxed to make them pass. All nine retain unverified absolute positional accuracy.

The browser simulation used the actual local desktop application: selected Kletek from the supplied folder, completed inspection, observed rejection of an unreviewed draft, completed the configured conversion, loaded eight layers and 263 features in the OSM review pane, opened the SVG correspondence report, and paged through GeoPackage tables. The cable query returned 33 segments and 1,078.40 m of source CAD length.

A separate local review workspace exercised control-point editing, preview, stale-edit rejection, rematching, and restart recovery against the real Kletek geometry. Its two training points and one check point were deliberately shifted +2 m east / −3 m north in EPSG:3857 and explicitly marked as simulation-only. It produced a new audited run, retained the old GeoPackage bytes, rejected stale requests, and recovered the selected result and all controls after restart. These artificial points are not surveyed controls and are not supplied as an accepted drawing correction.

Control-workflow evidence: [report](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/real-review-check.json), [rematch audit](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/simulation-rematch-audit.json). Installed conversion evidence: [audit](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/installed-run-audit.json), [exact equivalence](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/installed-equivalence.json), [installed identity](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/installed-identity.json).

All nine GIS ZIPs preserve the generated GeoPackage bytes and contain QML, QGZ, attribute tables, SVG candidates, a status-bearing README, and file checksums. Every ZIP passed CRC checks and every recorded member checksum was verified. Extract the full ZIP and keep `delivery.gpkg` and styles beside `delivery.qgz`; the QGZ alone is not self-contained. QGIS itself is unavailable on this machine, so actual QGIS opening/rendering is not claimed. SVG extraction completion is not complete legend/font/dynamic-state acceptance.

The corrected software is [cad2gis-0.5.0 wheel](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/dist-final/cad2gis-0.5.0-py3-none-any.whl); SHA-256 `593bf75b715b867fa586ab0312acf8e20bd9134b9eb4d2f4bb2435360c10c1c2`. It requires Python 3.11–3.12 and the declared dependencies; it is not a standalone OS installer. The isolated live test environment references runtimes in `/tmp`, so it is not a permanent installation. Use the wheel with the project’s documented install/runtime procedure for a durable installation.

Machine-readable results: [summary.json](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/summary.json). Full evidence and configuration copies: [verification directory](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909). Final test logs and the failed mixed-import experiment are in [logs](/home/jlu/桌面/claude-project/CAD2GIS/tmp/verification-20260909/logs).

No real external AI account was configured. AI integration was exercised using local protocol fixtures with the actual SDK/MCP tool chain. Online OSM location queries were performed only after explicit user approval; raw DWG files were not uploaded. No source drawing or existing published run was overwritten.
