# CAD2GIS Baseline Reconciliation

## APD Closed Loop

The fast APD loop validates committed evidence without reopening the DWG:

- records: `baselines/apd_hutabohu/records/readcad_review_bundle.json`
- source profile: `experiment/config/apd_source_profile.json`
- delivery: `baselines/apd_hutabohu/delivery/apd_delivery.gpkg`

Run:

```powershell
python -m pytest tests/test_baseline_reconciliation.py -q
```

The contract is:

| Evidence | Expected |
| --- | ---: |
| bundle schema | `cad2gis.review_bundle.v2` |
| bundle objects | 9391 |
| BOITE | 43 |
| CABLE | 6 |
| PTECH | 167 |
| IMB | 682 |
| SITE | 2 |

The test also validates the records bundle against the source-bound APD
profile before materializing 9391 records.

## Drift Policy

Any change to the records bundle, source binding, baseline GeoPackage, expected
counts, curve/length contract, or implementation hash is baseline drift. It
requires a new reviewed run and evidence comparison; expected values must not
be edited merely to make a regression pass.

This baseline proves only repeatability for the bound APD snapshot. It does not
prove cross-CAD semantic accuracy or absolute ground accuracy.
