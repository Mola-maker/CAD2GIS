# SVG source coverage upgrade — 2026-09-11

The automatic inventory previously selected delivered source handles only when they resolved to `INSERT`. This omitted visible frames whose identity was a `LWPOLYLINE`, and left compound symbols whose semantic identity was a `POINT` dependent on a project-specific override. The upgraded workflow extracts native geometry and explicitly grouped source geometry without inferring a symbol from nearby objects.

## Implementation

- `symbol_workflow.py` accepts delivered `LWPOLYLINE`, `POLYLINE`, `LINE`, `CIRCLE`, `ARC`, `ELLIPSE`, and `SPLINE` identities as source geometry. An isolated `POINT` or text identity still has no inferred outline.
- For non-INSERT identities, a single explicit DXF `GROUP` can bind a combination of graphic primitives, INSERTs, and source text. The group must contain actual graphic geometry and at most 64 members. Multiple groups, unsupported members, or conflicting owners are unresolved. POINT glyphs are excluded because their display is controlled by the drawing header and is not evidence of a pole outline. Spatial proximity alone never supplies membership.
- `symbol_assets.py` validates both identity and rendering handles. A real renderer selection cannot conceal a missing identity, and a selection cannot combine identities/render entities from different layouts. Existing explicit curated mappings remain available and are distinguished from direct source geometry.
- Render context follows the actual source layout, layout redraw order is honored, and source BYLAYER/BYBLOCK rendering is retained through ezdxf. Fingerprint caching includes extrusion, Z scale and array geometry, preventing distinct MINSERT arrays from reusing a signature.
- Source identity facts, render handles, source layers/layouts, block dependency facts, skips and overlay provenance are retained. Curated FAT/FDT text is explicitly presentation artwork, not extracted source text. No candidate is marked as having independently verified source-instance fidelity.
- Empty or fully hidden selections receive an incomplete asset and explicit skip diagnostics, rather than erasing valid candidates in the same inventory. Incomplete assets cannot be exported or bound by the delivery workflow.
- Conflicting overrides for the same feature class and identity are rejected instead of silently picking one.

## Brainstorm and selected product upgrade

| Rank | Upgrade | User value | Scope and acceptance |
| --- | --- | --- | --- |
| 1 | Per-class source/render coverage and a localized review queue | Makes missing geometry and manually substituted presentation visible before delivery | Implemented here: JSON and HTML summaries plus `svg-review-queue.csv`, with source handle, layer, layout, reason, render handles, and original POINT coordinates when available |
| 2 | Reproducible coverage comparison between two runs | Shows exactly which source identities improved or regressed | Requires a source-hash and database-scope comparison contract; defer until the current coverage schema has real-corpus acceptance |
| 3 | Interactive group/shape association review editor | Lets reviewers resolve genuine associations without editing JSON manually | Requires UI, persisted approval and stale-source protection; beyond this SVG-only change |

The first option uses existing source evidence and can be delivered without inventing a new semantic authority. `coverage` in `correspondence.json` exposes totals, per-layer and per-database-scope counts for delivered instances, source presence, rendered candidates, explicit groups, curated alternate geometry, presentation overlays and unresolved instances. The grain is a delivered point per database scope and layer: a repeated point in a partition is counted again. Counts explicitly do not claim positional or semantic accuracy.

The CSV prioritizes unresolved instances, curated alternate geometry and curated text overlays. Its coordinates, when present, are original source `POINT` WCS coordinates, not GCP-adjusted GIS coordinates. Blank coordinates do not imply a zero location; source handle/layout remain the locator. Every successful asset remains a candidate requiring visual review even if it does not appear in this focused queue.

## Existing Tinggar boundary

`baselines/tinggar/config/svg_symbols.json` was read but not changed. Its FAT rules render the delivered frame itself; its FDT and pole rules explicitly name other rendering handles. The existing rule for `A5BF2` / `MR.TGGR04-2.P011` says the source lacks its drawn circle and borrows the 2.5-inch symbol from `A5B05`. This is a curated representation and must not be described as recovering an original P011 circle. The new coverage and CSV show alternate-handle mappings separately. The baseline geometry, GCP settings and original data remain unchanged.

## Verification

Runtime: Windows, Python 3.12.13, ezdxf 1.4.4, resvg-py 0.5.0. The existing `.venv-multipage-final` was missing the project's declared resvg dependency and had no pip module. The dependency was installed using `uv pip install --python E:/branch_CAD2GIS/.venv-multipage-final/Scripts/python.exe resvg-py==0.5.0` after the initial restricted-network attempt failed and network access was approved.

Command, with `PYTHONPATH=E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/src`:

```powershell
E:/branch_CAD2GIS/.venv-multipage-final/Scripts/python.exe -m pytest tests/test_symbol_assets.py tests/test_symbol_workflow.py tests/test_svg_pipeline.py tests/test_delivery_svg_candidates.py tests/test_svg_entrypoints.py tests/test_svg_delivery.py --basetemp=E:/branch_CAD2GIS/validation/svg-upgrade-pytest-5 -q --tb=short --junitxml=E:/branch_CAD2GIS/validation/svg-upgrade-results.xml
```

The regression fixtures render real DXF data through ezdxf and resvg, not mocked SVG. Cases include source polyline frames; a POINT, far-offset block and text linked by an explicit group; unrelated geometry at the same coordinates; ambiguous groups; annotation-only groups; unsupported members; complete hidden selections; nested mirrored/nonuniform INSERTs with layer/block colors; explicit redraw order; distinct MINSERT arrays; original-source and delivery-database byte preservation; invalid identity/layout rejection; curated overlay provenance; and end-to-end candidate publication/binding compatibility. The existing pipeline fixtures simulate the conversion reader and do not establish real DWG conversion fidelity.

Result: **80 passed, 1 skipped in 15.01 seconds**. The one skip is Windows symlink creation unavailable (`test_delivery_svg_candidates.py:213`). The JUnit report is `E:/branch_CAD2GIS/validation/svg-upgrade-results.xml`.

A generated green grouped pole preview was visually inspected at `E:/branch_CAD2GIS/validation/svg-upgrade-pytest-4/test_unblocked_source_primitiv0/assets/symbols/instance-30.png`: the green circle and source NP7 glyph are visible; the unrelated red circle is absent. This is a synthetic regression fixture, not a Tinggar validation image.

## Remaining acceptance boundary

### Lamteh attached-attribute regression repaired

The real Lamteh SF corpus exposed a valid entity-owner relationship that the old dependency census did not handle. Attribute `139934` belongs to INSERT `139933`, rather than directly to a `BLOCK_RECORD`. ezdxf 1.4.4's generic `DXFGraphic.get_layout()` tries a block-record lookup after its layout lookup; passing the INSERT owner to that fallback raised `AttributeError: 'Insert' object has no attribute 'block_layout'`.

`symbol_assets._source_layout` now follows the actual owner chain until a `BLOCK_RECORD`, then resolves the drawing layout or block definition. Explicitly attached but unlinked children inherit the known parent layout. Missing owners and owner cycles raise precise errors instead of silently dropping attributes. Dependency traversal uses object identity for handle-less children so separate attributes are not collapsed into one empty-handle entry. Identity/render layout admission and direct rendering use the same resolver.

Real ezdxf fixtures now exercise paper-space INSERTs with ATTRIBs, nested blocks with inner and outer attributes, inherited layout for two unlinked attributes, missing owners and two kinds of owner cycles. Changing `FAT-16` to `FDT-144` changes both the serialized source-fact fingerprint and rendered SVG SHA-256. Attribute text and the original owner handle remain in the source-fact record. Result: **23 `test_symbol_assets.py` cases passed**, and Ruff passed. The accompanying workflow suite exposed an existing order-sensitive explicit-GROUP assertion once (`['37', '35']` versus `['35', '37']`); the isolated rerun passed. That unrelated test/file was not changed in this fix.

A direct single-symbol extraction was also run against the original `E:/source_dwg/APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg` through LibreDWG and ezdxf. INSERT `139933` and its `FAT=10` attribute `139934` produced one `CANDIDATES_EXTRACTED` library at `E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/lamteh-attrib-single/assets`; the receipt is beside that directory. The source SHA-256 remained `5b0872935a070cac9b3969811799d229bb48e534085254930263d526db9c5735`. This proves that the formerly failing ATTRIB ownership shape is accepted by the real DWG extraction path. It is a single-symbol regression check, not full Lamteh conversion or independent visual/geospatial acceptance.

This change does not prove that LibreDWG preserves every GROUP, dynamic block state, clipping boundary, font or layout detail in the real drawing corpus. Nested source paths that cannot be resolved as actual DXF entity handles remain unresolved. It does not perform survey validation or modify OSM matching, baseline coordinates, class assignments or network connectivity. Real Tinggar/other-corpus visual acceptance and relocated QGIS installation checks must be reported separately.

## Primary references

- [ezdxf DXF rendering rules](https://ezdxf.readthedocs.io/en/stable/dxfinternals/rendering_of_dxf_content.html): contextual colors, layer visibility, draw order and POINT header display behavior.
- [ezdxf INSERT reference](https://ezdxf.readthedocs.io/en/stable/blocks/insert.html): block base point, scale/reflection, MINSERT arrays and transformation limitations.
