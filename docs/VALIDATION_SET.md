# Validation Set — Six Held-out APD Drawings

The six DWGs added under `raw/` are held-out validation inputs for the
CAD2GIS algorithm flow.  They are processed exactly like new projects:
`inspect` → source-bound `bootstrap` → DeepSeek `auto-convert --llm assist`
with a fresh project pack under `baselines/validation_*`.  No baseline rule,
count, threshold, label family, or GCP is reused.

## Filename → engineering-region inference

| Site | DWG | Inferred region | Network role |
|---|---|---|---|
| `validation_bulu_lor_semarang` | `APD - BULU LOR RW 05 SEMARANG - SF.dwg` | Bulu Lor, RW 05, Semarang, Central Java, Indonesia | subfeeder (SF) |
| `validation_darat_sekip_pontianak` | `APD - DARAT SEKIP RW 12 PONTIANAK - SF.dwg` | Darat Sekip, RW 12, Pontianak, West Kalimantan, Indonesia | subfeeder (SF) |
| `validation_manado_tomohon_uplink` | `APD - MANADO- UPLINK_FWA_OLT_TOMOHON_TO_EMR- 46478_FO_24C.dwg` | Manado–Tomohon uplink corridor, North Sulawesi, Indonesia | FWA/OLT→EMR uplink, 24-core FO |
| `validation_tinggede_view_palu` | `APD - PERUMAHAN TINGGEDE VIEW PALU.dwg` | Perumahan Tinggede View, Palu, Central Sulawesi, Indonesia | FTTH distribution plan |
| `validation_taipa_palu` | `APD - TAIPA RW 05 PALU.dwg` | Taipa, RW 05, Palu, Central Sulawesi, Indonesia | FTTH distribution plan |
| `validation_tinggar_serang` | `APD - TINGGAR RW 04 SERANG.dwg` | Tinggar, RW 04, Serang, Banten, Indonesia | FTTH distribution plan |

All six remain Indonesian FTTH As-Plan-Drawing projects; `SF` files are
subfeeder drawings.

## Conversion results

LLM assist conversions completed with the same canonical pipeline.  Every
run is `CONDITIONAL` by design: no surveyed GCP / independent check-point
evidence was supplied, so absolute ground accuracy is not claimed.

Two drawings carry real DWG geographic coordinates and are delivered without
an anchor.  The other four use a cached coarse OSM place-name anchor derived
from the classified asset network bbox (PTECH/BOITE/SITE/CABLE) and the
filename-region lookup; the anchor is an explicit per-project config and is
applied even when the declared-CRS heuristic passes.

| Site | run_status | PTECH | BOITE | SITE | IMB | CABLE | CABLE_SEGMENT | ZPM | DIM spans | placement |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| semarang_sf | CONDITIONAL | 19 | 0 | 1 | 0 | 1 | 18 | 0 | 18/18 | OSM coarse anchor |
| darat_sekip_sf | CONDITIONAL | 13 | 1 | 1 | 0 | 2 | 24 | 0 | 12/24 | DWG real coordinates |
| manado-tomohon_uplink | CONDITIONAL | 49 | 16 | 1 | 243 | 16 | 93 | 15 | 42/93 | DWG real coordinates |
| tinggede | CONDITIONAL | 51 | 22 | 1 | 337 | 16 | 65 | 22 | 35/65 | OSM coarse anchor |
| taipa | CONDITIONAL | 112 | 26 | 1 | 369 | 17 | 118 | 26 | 100/118 | OSM coarse anchor |
| tinggar | CONDITIONAL | 109 | 11 | 2 | 435 | 17 | 119 | 0 | 0/119* | OSM coarse anchor |

`*` Tinggar's source inventory contains zero DIMENSION entities; its
CABLE_SEGMENT labels therefore remain computed geometry lengths.  Every other
project now labels matched spans with the integer DWG DIMENSION value.

Artifacts per site:

- `baselines/<site>/config/` — source-bound profile / mapping registry
- `baselines/<site>/review/` — source inventory + AI onboarding result
- `baselines/<site>/run/` — source/evidence/delivery GeoPackages, QML,
  evidence graph, manifest

## Generalization fixes exercised by this set

The first conversion exposed real generalization gaps; they were fixed
without site-specific rules:

1. **Zero-length polyline vertices** — duplicate consecutive WCS vertices are
   now skipped in curve materialization, recorded in
   `skipped_zero_length_segments`, and dimension/span indices are aligned to
   the materialized segment list.
2. **Ambiguous span dimensions** — `unmeasured_ambiguous_dimensions` /
   `unmeasured_missing_dimension_value` are now valid closure statuses.
3. **`INSUNITS=0` (unitless)** — accepted only with an explicit reviewed
   coordinate scale from the projected CRS axis; a bare unitless identity is
   still rejected.
