# Verification matrix

`cad2gis.verify` is the release gate for claims about source fidelity and
coordinate accuracy.  It is a read-only checker: it does not open AutoCAD, edit
a manifest, rewrite a GeoPackage, or infer a CRS from OpenStreetMap.  Production
conversion remains deterministic and the matrix never calls an LLM.

## Schema

The current input schema is `cad2gis-verification-matrix-v1`.  A matrix contains
one or more `samples`; a single `run_manifest.json` is also accepted as a
one-sample input during migration.

```json
{
  "schema_version": "cad2gis-verification-matrix-v1",
  "samples": [
    {
      "sample_id": "project-a",
      "evaluated": true,
      "input_verified": true,
      "source": {
        "sha256": "<64 lowercase hex characters>",
        "version": "2026.1",
        "vendor": "AutoCAD",
        "units": "m",
        "crs": "EPSG:3857"
      },
      "layouts": ["Model"],
      "blocks": {"count": 0, "reviewed": true},
      "curves": {"count": 0, "reviewed": true},
      "profile": {"id": "project-a-v1", "reviewed": true},
      "gold": {"path": "gold/project-a.json", "independent": true},
      "geometry": {"passed": true},
      "topology": {"passed": true},
      "semantics": {"passed": true},
      "style": {"passed": true},
      "length": {"passed": true, "closure_passed": true},
      "nominal_crs": {"passed": true},
      "gcp": {
        "surveyed": true,
        "training_control_count": 4,
        "check_control_count": 4,
        "check_status": "PASS",
        "reviewed": true
      }
    }
  ]
}
```

The source record should bind the exact drawing bytes, reviewed profile and
mapping version, CAD vendor/version, layouts, block/curve inventory, gold
reference (if one exists), and GCP train/check records.  A status string by
itself is not evidence; the evaluator requires a corresponding validation or
evidence object and downgrades a bare `PASS` to `WATCH`.

## Dimensions and fail-closed rules

Each evaluated sample receives `PASS`, `WATCH`, or `FAIL` for:

- `geometry` — source geometry/curve facts and immutable-source checks;
- `topology` — endpoint, route, crossing, and graph checks;
- `semantics` — reviewed layer/block mapping and unknown/unmatched coverage;
- `style` — line colour, linetype, point symbol, and label evidence;
- `length` — source segment/span measurement and delivery closure;
- `nominal_crs` — an explicitly declared source/target CRS operation; and
- `absolute_accuracy` — an independently reviewed surveyed GCP check.

Absolute accuracy is **always `FAIL` when surveyed GCP evidence is absent**.
At least three independent training controls and three independent check
controls, with a reviewed passing check result, are required for `PASS`.  A
nominal CRS transform is not a survey result.

Rows marked `inventory_only`, `inventory`, `unreviewed`, or `not_evaluated` are
retained for onboarding (including APD/AGA/demo inventories), but cannot support
a precision claim.  Missing source SHA-256 or an unverified source also blocks
the claim.

## Scope of claims

`strongest_allowed_claim(report)` derives scope from normalized sample evidence:

1. no evaluated sample: inventory only;
2. one verified input hash: single-input claim only;
3. two or more **distinct verified input SHA-256 values**: cross-CAD scope may
   be claimed, provided every required dimension passes;
4. absolute claims additionally require a passing absolute-accuracy dimension
   for every sample.

Copies of one drawing, repeated runs, or different output paths do not create a
cross-CAD sample.  The helper never trusts a caller-supplied summary count or
claim string.

## API and example

```python
from cad2gis.verify import evaluate_matrix, strongest_allowed_claim

report = evaluate_matrix("build/verification_matrix.json")
print(report["status"])
print(strongest_allowed_claim(report))
```

`evaluate_matrix` only reads the specified JSON file and returns a deterministic
report containing the matrix hash, normalized source metadata, per-sample
dimensions/reasons, aggregate dimension statuses, input-hash cardinality, and
the strongest allowed claim.  Invalid JSON or an unsupported matrix schema
returns a `FAIL` report with an actionable error; the input file is untouched.

For a release review, archive the matrix, the source SHA-256, the profile and
mapping registry hashes, the independent gold/check artifacts, and the returned
report together.  Do not describe a `WATCH`, `FAIL`, or inventory result as an
accuracy guarantee.
