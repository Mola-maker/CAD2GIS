# QGIS / OpenStreetMap coordinate review

This optional delivery step adds portable OSM reference data to an existing QGZ,
retains its CAD GeoPackage bytes and embedded SVG bindings, and optionally applies
a source-bound coordinate-label translation **inside the review project only**.
It does not change the canonical conversion, lengths, or acceptance status.

`publish-match` also supplies the canonical conversion's sealed matching record,
bound to the source drawing, run manifest and exact CAD database hashes. This
record is retained in the QGZ and review receipt and checked by the independent
verifier. The title reports the actual OSM status and revision; a project without
this provenance says `CAD registration not supplied`, rather than assuming a
nominal conversion. Publication rejects populated scopes whose match was not
accepted. The `--matching-context` option carries this provenance only; it does
not apply another coordinate correction.

The nine-drawing review is available from the
[2026-09-08 GitHub Release](https://github.com/Mola-maker/CAD2GIS/releases/tag/osm-review-20260908).
Download the project archive for all 22 QGZ files, or the smaller snapshot archive
for 22 rendered views and nine before-images. Both contain an offline HTML index.
Exact archive sizes and SHA256 values are recorded in [osm-review-release.json](osm-review-release.json).

## Build and verify

Run with a Python runtime containing QGIS and the installed `cad2gis` package.
The CAD conversion itself does not import QGIS or require this optional step.

```sh
python -m cad2gis.qgis_review --project delivery.qgz \
  --osm-data area-overpass.json --output review/delivery.qgz

python -m cad2gis.qgis_review --project delivery.qgz \
  --osm-data area-overpass.json --output standalone/view.qgz --standalone

python -m cad2gis.qgis_review_verify --project standalone/view.qgz \
  --image snapshots/view.png --font /path/to/Arial.ttf
```

Equivalent installed commands: `cad2gis-qgis-review` and
`cad2gis-qgis-review-verify`. On Windows, use the QGIS-supplied Python launcher;
an unrelated virtual environment cannot load the QGIS native libraries.

`--osm-data` is an already fetched Overpass JSON response containing ways with
geometry. This command makes no Overpass request. Obtain authorization before
sending confidential project bounds to a public service. Incomplete/error
responses and empty maps are rejected. Roads, buildings, land use and water
polygons are persisted to `osm-basemap.gpkg`. This is a simplified OSM vector
reference map, not the standard OSM raster cartography.

Editable output requires keeping its QGZ, CAD GPKG and OSM GPKG together.
Standalone output embeds both databases and can be moved alone. SVGs remain
base64-embedded. An unchecked optional online OSM XYZ layer is also included;
verification renders the offline vectors without fetching public tiles.

## Source-bound display registration

Add `--registration review.json --source-evidence source.gpkg` only after
reviewing the exact source-coordinate/device associations. The JSON contract is
`cad2gis.qgis-coordinate-review.v1`, with `model: translation` and
`scope: project_display_only`. It binds:

- `delivery_sha256`: exact target CAD database;
- `source_evidence_sha256` and `source_sha256`: source extraction and DWG identity;
- at least two unique controls: `layer`, `source_handle`,
  `expected_display_label`, `expected_native_point`, `source_label`,
  `coordinate_text_handle`, `identity_text_handle`.

Each coordinate and device identity must come from the same Model-space CAD
table. The actual target feature must match the reviewed native point and label.
The only allowed label normalization is removing a leading `EXT.`. Targets are
parsed from explicit latitude/longitude degree text, not supplied as arbitrary
replacement coordinates. Duplicate, ambiguous, nonfinite or mismatched evidence
is rejected. Evidence reuse between related drawings requires independently
reviewed identical native positions and identities and a separate target hash.

The translation is fitted after an explicit normalized PROJ operation and appended
to that **same** operation. Mixing a QGZ's historical datum transform for fitting
with a different default transform for execution previously introduced several
metres of inconsistency. Fallback is disabled for the review operation and its
effect is checked immediately and again after reopening the saved project.

The correction applies to the project's original-layer CRS → EPSG:3857 display
operation. **Opening the GPKG alone, changing the project CRS, or exporting through
a different transform context does not constitute a corrected canonical export.**
Do not replace authoritative source geometry with the review display operation.
Do not use OSM road centre lines as automatic cable snapping targets. A small fit
residual is not independent accuracy evidence.

## Verification and limits

The verifier checks the QGZ hash, all layer validity and counts, exact CAD and OSM
database hashes, per-feature SVG hashes, saved correction provenance and control
coordinates, then renders with QGIS and checks for render errors/blank output.
Move the editable folder and isolate the standalone QGZ before invoking it to
test portability. Review the resulting PNGs visually; a nonblank map alone is
not semantic or geographic acceptance.

2026-09-08 corpus evidence: QGIS 4.0.3 / Qt6 on Windows, nine source drawings,
11 project scopes, 22 relocated/isolated QGZ opens and renders passed. All 11
editable/standalone map-image pairs were pixel-identical (excluding captions).
751 SVG feature bindings and all CAD database bytes were preserved. Lamteh's
two source-label controls improved from 58.36 / 62.50 to 4.86 / 4.86 projected
metres using a translation. The other seven drawings retained nominal
registration; sparse OSM coverage and local discrepancies remain review issues.
This run does not establish survey acceptance, complete legend semantics,
cross-version compatibility, or Linux execution of this new optional command.

References: [QGIS coordinate transform context API](https://api.qgis.org/api/classQgsCoordinateTransformContext.html),
[OpenStreetMap attribution](https://www.openstreetmap.org/copyright),
[public tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
