# Final code and architecture review — 2026-09-06

Scope: the optional SVG work, portable QGIS delivery, batch/AI entry points,
repository cleanup, GitHub Pages and the Docker release pipeline. This review
does not upgrade the nine drawings from CONDITIONAL to engineering acceptance.

## Findings and corrections

| Finding | Correction | Verification |
| --- | --- | --- |
| SQLite context managers retained open files on Windows | Explicit connection closure in symbol extraction, correspondence and QGIS tools | Windows atomic-directory publication regression |
| Optional SVG failure could leave a partial asset library | Same-volume staging, source/database consistency checks, Windows ACL inheritance and publish after completion | Failed-render rollback and mismatched-source tests |
| SVG options were missing from MCP and batch font inputs | Shared `svg_mode`, bounded `svg_font_dirs`, early dependency/output checks; CLI/MCP return candidate report paths | MCP forwarding, input traversal and preflight tests |
| QGIS helpers existed only under repository tools | Implementations ship in the wheel; scripts remain compatibility entry points | Module import and real QGIS packaging/fresh-process verification |
| QGZ was visible before packaging checks completed | Build/check in a staging directory before publishing the single file | Actual QGIS attachment and relocation checks |
| SVG delivery retained obsolete checksum files | Regenerate the complete file seal after deriving styles and projects | Delivery builder preserves GeoPackage hashes; fresh-process verification remains required |
| Optional verification assertions could be disabled | Reject optimized Python for the assertion-based QGIS verification and SVG delivery gates | Explicit runtime guard |
| Docker only checked startup | Add DejaVu fonts and a synthetic native-DWG export, SQLite index/integrity and SVG/QML smoke step | WSL native run passed; image run is recorded in Docker Actions |
| Source checkout included DWGs and generated demonstrations | Move raw, historical scratch state, build outputs and derived delivery trees outside the source checkout | Reviewed deletions; external original drawings preserved |
| Pages contained stale product claims and demonstration ownership | New responsive architecture page; remove invented entity count and blanket VERIFIED label; fetch a hash-pinned authorized Release | Browser desktop/390px inspection, release manifest, publication gate |

Canonical conversion and optional SVG presentation have separate publication
boundaries. Predictable SVG failures are rejected before conversion. If a later
candidate-stage failure occurs, the valid canonical run is retained and its path
is returned in the error. A failed optional stage is not reported as successful.

The SQLite source store remains immutable reader evidence. The semantic/review
store owns revision-bound changes and audit records. GeoPackage owns generated
GIS delivery. The independent `symbols.sqlite3` is a derived asset library.
Redis is not required by the local conversion path and is not a substitute for
these durable stores; distributed caching/queues are deployment extensions.

## Evidence and release gates

- Initial full Windows regression after core corrections: **648 passed, 7 skipped**.
  Skips are explicit external-corpus/AutoCAD fixture gates, not successful checks.
- Additional entry-point and publication tests run after packaging changes.
- Linux WSL native synthetic DWG export, indexed SQLite, SVG/QML and unchanged
  source checks passed without AutoCAD. This is a runtime smoke, not acceptance
  of nine engineering drawings or a QGIS rendering test.
- Real QGIS 4.0.3 fresh-process tests cover internal attachments, an unrelated
  same-named sidecar, database byte hashes, default extent and actual raster output.
- The previously recorded nine-drawing SVG rerun and eleven QGIS project checks
  remain in the derived demonstration archive, alongside the historical results.
- Public Release SHA256 and size are pinned in [derived-release.json](derived-release.json).
  The user explicitly authorized this separate public Release destination.
- Final cross-platform CI, Pages and Docker results must be checked against the
  pushed commit in [GitHub Actions](https://github.com/Mola-maker/CAD2GIS/actions).
  A workflow definition alone is not a successful build.

The synthetic DXF→DWG round trip exposed LibreDWG 0.14 encoding a null handle that
ezdxf rejected. The smoke uses LibreDWG's own `dwgadd` to create a valid synthetic
source; it does not repair or ignore invalid reader entities. The fixture syntax
follows [LibreDWG's official example](https://github.com/LibreDWG/libredwg/blob/master/examples/dwgadd.example).
DWG writing is not the plugin's conversion output contract.

## Remaining acceptance boundaries

Complete legend correspondence is **not verified**. Missing SHX fonts, dynamic
block states and nested source handles remain explicit review dispositions;
unresolved objects retain their existing style. There is no automatic `reviewed`
mode that silently approves all candidates. Future acceptance must bind each
approved mapping to the source and library hashes and include a visual check.

Independent GCP accuracy is also unaccepted. Full precision coordinates and
source-vs-delivery consistency do not establish survey accuracy. Display values
use two decimals; CAD dimension labels have no suffix, original curve fallback
uses `[CAD curve]`. CSV is a presentation export, not a lossless editing interface.

The base Docker image includes conversion/MCP dependencies and open fonts, but
not QGIS. QGZ packaging/render verification needs a separately installed QGIS
Python environment. SSH or a controlled MCP connection can invoke the same CLI;
the bundled unauthenticated MCP service remains local-only.

Repository cleanup removes files from the current source tree, not Git history.
It does not rewrite historical commits or delete the user's external drawings.
