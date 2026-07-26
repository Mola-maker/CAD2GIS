# CAD scene, registration, and interactive review architecture

## Failure model

The previous deliveries combined independent failures:

1. DWG model entities, paper layouts, block definitions, title blocks, and
   legend samples were treated as one semantic scene.
2. `CGEOCS` was treated as proof that entity WCS coordinates already occupied
   that CRS. It is only a declaration.
3. Empty target layers were presented without distinguishing
   `PRESENT`, `UNMAPPED`, and `ABSENT_IN_SOURCE`.
4. External raster, underlay, and DWG references were not represented as a
   typed source dependency graph.

Lamteh SF gives concrete evidence: eight cable style samples have the same
142.10 drawing length and aligned centroids, while the real cable is about
1290.53. Seven falsely mapped PTECH samples form an exact catalog column.
Coordinates around `(2300..10500, -9600..-8800)` cannot plausibly occupy
EPSG:23846.

## Canonical pipeline

```text
source bundle
  -> dependency inventory
  -> immutable entity/block/layout inventory
  -> geometry-first scene partition
       plan roots / style catalog / sheet evidence / unresolved dependencies
  -> semantic mapping
  -> independent curve-length-topology validation
  -> declared-CRS coordinate-domain gate
  -> optional paired-GCP registration
       translation -> similarity -> gated affine
       independent check points + spatial coverage
  -> source.gpkg + evidence.gpkg
  -> delivery.gpkg + QML + manifest + run_status
  -> separate revisioned review workspace
```

## Source and scene admission

| State | Meaning | Publication |
|---|---|---|
| `SOURCE_COMPLETE` | Conversion-relevant dependencies resolved | Continue |
| `SOURCE_VISUAL_DEPENDENCY_MISSING` | Raster/underlay missing | Continue with warning |
| `SOURCE_GEOMETRY_DEPENDENCY_MISSING` | DWG/model dependency missing | Stop |
| `SCENE_CATALOG_EXCLUDED` | High-confidence catalog separated | Continue with entity evidence |
| `SCENE_AMBIGUOUS` | Plan/catalog separation is unsafe | Stop semantic publication |

Autodesk reports unresolved references on drawing open and documents that
eTransmit packages dependent files. Production ingestion should therefore
accept a source bundle/eTransmit package rather than silently assuming one
host DWG is complete.

The scene partition uses relative geometry and layout regularity, not vendor
names or fixed coordinates. High-confidence patterns include translated
copies of the same open geometry across diverse styles, and precisely aligned
INSERT catalogs with diverse block/layer pairs.

## Coordinate authority

Three questions are recorded independently:

1. **Declared CRS:** which CRS metadata exists?
2. **Coordinate-domain plausibility:** do WCS coordinates occupy the declared
   CRS area of use?
3. **Ground registration:** do independent controls prove real-world location?

Failure of question 2 changes the source mode to local engineering
coordinates. Relabeling the layer is forbidden; paired CAD/map GCPs are
required.

Model selection is shape-preserving by default:

1. translation when scale and bearing are authoritative;
2. similarity/Helmert for translation, uniform scale, and rotation;
3. affine only when independent check-point improvement and residual structure
   justify shear/non-uniform scale;
4. projective/TPS only under a separate distorted-source policy.

QGIS documents the same distinctions between Helmert, affine Polynomial 1,
and locally deforming TPS. PROJ/GDAL datum accuracy is a separate downstream
gate: missing grids and ballpark fallbacks must be reported and may fail
closed.

## Interactive review workspace

The WebUI has two coordinate panes:

- **CAD pane:** immutable native coordinates, with no invented CRS.
- **Map pane:** OSM/reference basemap plus derived registration preview.

The operator clicks the same location in both panes. Control pairs are stored
as revisioned review features with explicit `train` or `check` roles. The
browser provides an immediate similarity preview; canonical publication must
rerun the server-side deterministic fitter and all gates.

Every published profile binds source/inventory hashes, control coordinates,
target CRS, roles and accuracies, selected model, coefficients, train/check
residuals, spatial coverage, outliers, and review lineage.

## AI boundary

LLM/VLM may summarize inventories and visual regions, propose semantic
mappings tied to entity IDs, suggest candidate correspondences, explain
residual patterns, and create typed decision packs.

It may not invent coordinates, lengths, zone polygons, or entities; rewrite
immutable source geometry; introduce an unregistered repair; approve its own
transform; or bypass independent check-point gates.

This keeps AI useful on unfamiliar drawings without hardcoding the three
compatibility files as training truth.

## Primary references

- [Autodesk External References palette](https://help.autodesk.com/view/ACD/2026/ENU?caas=caas%2Fdocumentation%2FACDLT%2F2014%2FENU%2Ffiles%2FGUID-2580E8B5-2175-49E9-8EA2-02371C61A1B7-htm.html)
- [Autodesk unresolved references](https://help.autodesk.com/view/ACD/2026/ENU?caas=caas%2Fdocumentation%2FACDLT%2F2014%2FENU%2Ffiles%2FGUID-99DFC05F-FE8E-480D-9AD6-BCA127EA2FA3-htm.html)
- [Autodesk eTransmit dependent files](https://help.autodesk.com/view/ACDLT/2026/ENU?caas=caas%2Fdocumentation%2FACD%2F2014%2FENU%2Ffiles%2FGUID-C32FA153-88D6-41D9-B868-0DFF59509CD2-htm.html)
- [Autodesk layout viewports](https://help.autodesk.com/cloudhelp/2022/ENU/AutoCAD-Core/files/GUID-2B5D404A-DCAB-4AF6-A5C1-51593B38F519.htm)
- [QGIS Georeferencer algorithms](https://docs.qgis.org/latest/en/docs/user_manual/managing_data_source/georeferencer.html)
- [QGIS datum transformations](https://docs.qgis.org/latest/en/docs/user_manual/working_with_projections/working_with_projections.html)
- [GDAL operation controls](https://gdal.org/en/stable/programs/ogr2ogr.html)
- [PROJ operation selection](https://proj.org/en/stable/apps/projinfo.html)
- [OpenLayers scale, rotate, and translate example](https://openlayers.org/en/latest/examples/modify-scale-and-rotate.html)
- [OpenLayers API](https://openlayers.org/en/latest/apidoc/)
