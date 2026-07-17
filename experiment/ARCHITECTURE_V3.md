# CAD2GIS v3 architecture

This directory contains the canonical direct-DWG conversion path for the APD
Hutabohu drawing.  It replaces the legacy geometry-repair workflow with a
deterministic evidence-first pipeline.

## Scope and source contract

- Authoritative input: `APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`
- Source SHA-256: `557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557`
- Drawing CRS: `EPSG:3857` (`WGS84.PseudoMercator` in the DWG)
- Drawing units: `INSUNITS=6` (metres)
- Delivery CRS: `EPSG:9481` (SRGI2013 / UTM zone 51N)
- Conversion is direct `EPSG:3857 -> EPSG:9481`; no intermediate WGS 84
  longitude/latitude geometry is created.

The source profile and reviewed semantic registry are versioned in
`config/apd_source_profile.json` and `config/apd_mapping_registry.json`.
Conversion stops if the source hash, entity census, route graph, span
partition, annotation assignment, or CRS round-trip regression does not match
the profile.

## Pipeline boundaries

```text
DWG ingest
  -> reviewed semantic classification
  -> independent route/support/optical evidence graphs
  -> direct CRS transformation
  -> eight-layer delivery warehouse
  + separate evidence warehouse and QGIS styles
```

The implementation is split by responsibility:

- `autocad_reader.py`: direct AutoCAD extraction only; it does not assign GIS
  meaning.  Model entities, dimensions, insert transforms, effective layer
  style and block-definition children are retained as evidence.
- `cad2gis_v3/ingest.py`: validates the immutable DWG source contract.
- `cad2gis_v3/semantics.py`: executes versioned field rules and assigns
  same-family CAD annotations with a deterministic maximum-cardinality,
  minimum-total-distance one-to-one solver.  Unsupported meaning is left
  unavailable.
- `cad2gis_v3/topology.py`: builds source route walks, segment occurrences,
  physical span evidence and attachment candidates without changing source
  route coordinates.
- `cad2gis_v3/ports.py`: transforms block-definition geometry into drawing
  space and records candidate connection ports.  It never inserts a point into
  a cable route.
- `cad2gis_v3/georef.py`: performs the single direct CRS operation.  Projected
  coordinates and length metrics are enriched here once so evidence and
  delivery consume the same values and provenance.
- `cad2gis_v3/warehouse.py`: writes exactly the eight contractual delivery
  layers.
- `cad2gis_v3/evidence.py`: writes audit, provenance, lineage, topology and
  unresolved evidence to a different GeoPackage.
- `cad2gis_v3/styles.py`: writes portable QGIS QML files from effective CAD
  colour/style evidence and registers each QML as the default style inside the
  delivery GeoPackage.
- `cad2gis_v3/pipeline.py`: enforces conservation and regression gates and
  writes a reproducible manifest.

## Geometry and topology invariants

1. The six source CABLE polylines are immutable.  No vertex may be appended,
   snapped, bridged, or replaced.
2. The two source optical components represent two FDT service domains.  They
   are not an error and must not be forced into one component.
3. `SPAN CABLE` dimensions are measurements.  Their native segment signatures
   partition into 130 cable-route spans and 40 sling-support spans.
4. `SLING WIRE` is support infrastructure, never optical cable geometry.
5. A support pole is not automatically an optical node.  Route/support
   proximity and device/route proximity are separate evidence relations.
6. Crossings are not connections unless reviewed evidence identifies a port.
7. Device centre offsets do not justify route edits.  Ambiguous or distant
   attachments remain candidates or unresolved.
8. Source CAD length, dimension length, target grid length and geodesic length
   remain separate fields; `LONGUEUR` is the target `EPSG:9481` geometry
   length.
9. All 139 optical source-segment occurrences have an explicit route/span
   membership row: 130 reference exact dimension evidence and 9 remain
   `unresolved/no-exact-span-dimension`.

## Labels, styles and provenance

Display labels come only from direct DWG text/attributes or a deterministic,
traceable reviewed rule.  Internal generated IDs are not used as visible CAD
labels.  Each populated delivery field has a provenance state such as
`DWG_DIRECT`, `DWG_DERIVED:<rule>`, `USER_APPROVED:<decision>`, or
`UNAVAILABLE` in the evidence warehouse.

Annotation ownership is not assigned greedily.  The source conserves all 43
BOITE annotations and all 118 PTECH annotations through global one-to-one
assignment.  The remaining 49 PTECH objects have no direct CAD label and stay
`UNAVAILABLE`.  Candidate edges, distances, selected edges and rule IDs are
stored in `annotation_assignment_candidates`.

Constant, layer, block-attribute and display-label semantics are declared in
the mapping registry with reviewed rule IDs.  Annotation and layout-topology
decisions carry registered decision-rule IDs.  A populated field without
explicit provenance stops the run.

Effective CAD styling resolves entity style against its layer style before QML
generation.  This preserves the reviewed 24C/48C cable colour distinction and
asset-family colours without embedding a machine-specific QGIS project path.
The same QML is stored in `layer_styles` with `useAsDefault=1`, so loading a
delivery layer directly from the GeoPackage enables its labels and colours;
the sidecars remain available for explicit style re-application.
`layer_styles` is registered as an attributes table in both `gpkg_contents`
and `gpkg_ogr_contents`, making it discoverable through the QGIS OGR provider.
Raw CAD radians, CAD counter-clockwise degrees and QGIS clockwise degrees are
stored separately; labels use the QGIS render-angle field.  Every QML sidecar
has its own SHA-256 in the style manifest.

The run manifest records the selected PROJ operation, library versions and its
declared 1.2 m accuracy.  Round-trip and OSR/PROJ agreement are numerical
regression checks only.  Absolute positioning has not been independently
validated because no surveyed ground-control point was supplied.

## Outputs

`runs/apd_architecture_v3/apd_delivery.gpkg` contains exactly:

- `BOITE`
- `CABLE`
- `PTECH`
- `INFRASTRUCTURE`
- `SITE`
- `ZNRO`
- `ZPM`
- `IMB`

Unsupported contractual layers are present and empty.  Audit and topology
tables are intentionally absent from the delivery file and are stored in
`runs/apd_architecture_v3/apd_evidence.gpkg`.

The delivery contains two source optical components, one per FDT domain.  The
three intervening SLING spans remain support evidence and are not promoted to
CABLE.  Device-to-route connections remain candidates until block ports are
reviewed, so the six logical cable sections deliberately remain abstained and
`ORIGINE`/`EXTREMITE` are not fabricated.  Point symbols use portable
family-specific primitives; exact CAD block artwork is not claimed.

## Canonical command

Run from `experiment/py_scripts` in the `cad2gis` Conda environment:

```powershell
python convert_v3.py `
  --input '..\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg' `
  --run-dir '..\runs\apd_architecture_v3' `
  --source-profile '..\config\apd_source_profile.json' `
  --mapping-registry '..\config\apd_mapping_registry.json'
```

The old `converter.py` entry point is disabled unless explicitly opted into via
`CAD2GIS_ENABLE_LEGACY=1`.  It must not be used to produce the v3 APD delivery.
