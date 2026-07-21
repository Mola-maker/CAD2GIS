# CAD2GIS Reconciliation

## A-Plan Closed Loop

The A-plan closed loop verifies pipeline behaviour without requiring the
original DWG.  It uses the pre-extracted canonical records bundle as input.

### Inputs

- **Records bundle**: `baselines/apd_hutabohu/records/readcad_review_bundle.json`
  - Schema: `cad2gis.review_bundle.v2`
  - Objects: 9391 canonical records
- **Source profile**: `baselines/apd_hutabohu/config/source_profile.json`
- **Mapping registry**: `baselines/apd_hutabohu/config/mapping_registry.json`

### Outputs

- `baselines/apd_hutabohu/output/delivery.gpkg`
- `baselines/apd_hutabohu/output/evidence.gpkg`

### Reconciliation Criteria

SQL count comparison between replay output and baseline:

| Layer | Expected Count |
|-------|---------------|
| BOITE | 43 |
| CABLE | 6 |
| PTECH | 167 |
| IMB | 682 |
| SITE | 2 |

### Running Reconciliation

```bash
# Run the replay driver
PYTHONPATH=src python verify/replay.py

# Run reconciliation tests
PYTHONPATH=src pytest verify/reconciliation/ -q
```

### Bundle Drift

The records bundle is the canonical-evidence baseline.  Any change to
`readcad_review_bundle.json` content is considered baseline drift and
requires re-validation of the closed loop.
