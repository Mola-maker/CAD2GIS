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

| Site | run_status | PTECH | BOITE | SITE | CABLE | CABLE_SEGMENT | ZPM |
|---|---:|---:|---:|---:|---:|---:|---:|
| bulu_lor_semarang | CONDITIONAL | 23 | 0 | 1 | 6 | 27 | 0 |
| darat_sekip_pontianak | CONDITIONAL | 13 | 1 | 1 | 114 | 144 | 0 |
| manado_tomohon_uplink | CONDITIONAL | 49 | 15 | 1 | 20 | 105 | 15 |
| tinggede_view_palu | CONDITIONAL | 51 | 22 | 1 | 16 | 65 | 22 |
| taipa_palu | CONDITIONAL | 112 | 26 | 1 | 17 | 118 | 26 |
| tinggar_serang | CONDITIONAL | 106 | 0 | 0 | 17 | 119 | 0 |

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
