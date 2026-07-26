# CAD2GIS Robustness Validation

Validation date: 2026-07-26  
Repository: `E:\branch_CAD2GIS\CAD2GIS`  
External compatibility corpus: `E:\branch_CAD2GIS\APD_test`

## Conclusion

The local CLI/MCP workflow now completes a reviewed APD conversion and fully
reads the three external DWGs without silent row loss. The reader lifecycle,
Python/GIS environment, reproducible build, MCP import path, and block/layout
coordinate-domain gap found during the audit have been corrected.

This is an engineering robustness result, not a claim of universal semantic
or surveyed positional accuracy. `APD_test` is compatibility/stress evidence,
not training data or GIS truth. A new drawing still requires a source-bound
profile and mapping; absolute accuracy still requires authoritative GCPs and
independent check points.

## Result matrix

| Dimension | Result | Evidence |
| --- | --- | --- |
| Canonical automated suite | PASS | 196 passed, 3 skipped: 2 opt-in complex-DWG cases and 1 unavailable LibreDWG capability case |
| Source/profile/hash binding | PASS | wrong source, stale inventory and tampered artifacts fail closed |
| Reader capability isolation | PASS | explicit LibreDWG/AutoCAD capability and typed failure boundary |
| Small real DWG | PASS | KLETEK complete inventory, zero skipped rows |
| Complex real DWG lifecycle | PASS | SF 61,829 records; Lamteh 65,957 records; zero skipped rows |
| Completion/partial-output safety | PASS | versioned completion marker, exact row conservation and negative timeout/partial-output tests |
| Plan-domain coordinate separation | PASS | immutable raw inventory plus a lineage-bound Model/plan instance view |
| Nested INSERT transforms | PASS | block base, insertion, scale, rotation, normal and extrusion are required; nested composition tested |
| Anti-overfitting checks | PASS | reordered inventory, arbitrary block names, translations, rotations, scales and nested transforms |
| Lamteh plan-domain stress | PASS | SF: 213 roots/709 derived; main: 3,283 roots/3,090 derived; 0 issues |
| Curve/source-length conservation | PASS | source fingerprints, native lengths and 139 APD segment closures pass |
| Topology/source immutability | PASS | no synthetic route vertices; source component definitions agree |
| Nominal CRS policy | PASS | unknown/local CRS cannot be guessed |
| Absolute accuracy policy | PASS | no surveyed GCP means `CONDITIONAL` and `not independently verified` |
| APD end-to-end workflow | PASS | source/evidence/delivery GeoPackages, QML, evidence graph and manifest published |
| MCP control plane | PASS | installed plugin exposes 13 tools; all three external DWGs pass `inspect_source`; foreign project conversion fails closed |
| Wheel content isolation | PASS | no test corpus, experiment, OMO or historical verify payload |
| Wheel byte reproducibility | PASS | verified double build SHA-256 `e2072ca128b6f69e9a4aab888375d27db75cebd98c6768e4b6cd0bb66f01e391` |
| Pinned runtime readiness | PASS | Python 3.12.13; doctor reports `conversion_ready: true` |
| Runtime ABI consistency | PASS | `pip check` clean; cffi is the Conda `cp312-cp312-win_amd64` build |
| New plan-domain type gate | PASS | basedpyright: 0 errors |
| Full-repository type debt | WATCH | legacy broad basedpyright scan remains noisy and is not a release gate yet |
| Ruff/diff hygiene | PASS | changed production/test files pass Ruff and `git diff --check` |

## Reader lifecycle correction

Large drawings can finish the read-only export but leave AutoCAD Core Console
stuck after `QUIT`. Export completion and process shutdown are therefore
separate protocol events:

1. AutoLISP closes the TSV and writes
   `cad2gis-autocad-bulk-completion-v1<TAB><row_count>`.
2. Python watches the marker independently of process exit.
3. A valid marker starts a short shutdown grace period.
4. A still-running process is terminated without discarding the completed
   export.
5. Missing markers, timeouts, malformed rows, partial files and row-count
   mismatches remain fatal.

Observed full-reader results:

| Drawing | Returned records | Skipped | Shutdown |
| --- | ---: | ---: | --- |
| KLETEK RW 05 SIDOARJO | 1,520 including document metadata | 0 | normal |
| LAMTEH DAYAH ACEH - SF | 61,829 | 0 | forced after valid completion |
| LAMTEH DAYAH ACEH | 65,957 | 0 | forced after valid completion |

The probe-only entity counts are one lower because they exclude the document
metadata row.

## Plan-domain architecture

The previous semantic boundary hard-coded `cad_role == "model"`. That is
insufficient for drawings whose Model space contains top-level INSERTs and
whose geometry lives in nested block definitions.

The new `cad2gis-plan-domain-v1` stage:

- preserves all reader records as the source-of-truth inventory;
- prioritizes reviewed Model WCS, then plan layouts only when Model is absent;
- uses an explicit layout-role fallback when role partitioning leaves no Model
  entity;
- recursively expands block-definition leaves with reader-authoritative affine
  transforms;
- assigns content-addressed derived IDs and stores root, definition, instance
  path and affine matrix lineage;
- preserves/refingerprints curve facts under supported similarity transforms;
- rejects cycles, missing definitions/facts, oblique plans and non-uniform
  curved transforms that cannot be represented exactly.

No source filename, APD/Lamteh block name, vendor layer, coordinate, feature
count or expected output count participates in this logic.

Lamteh SF real-data probe:

```json
{
  "raw_entity_count": 61829,
  "selection_mode": "layout-role-fallback",
  "selected_root_count": 213,
  "definition_count": 1675,
  "expanded_insert_count": 54,
  "derived_entity_count": 709,
  "semantic_entity_count": 922,
  "issue_count": 0,
  "status": "PASS"
}
```

Lamteh main drawing independently produced 65,957 complete records, 3,283
Model roots, 1,858 block definitions and 3,090 derived entities with zero
plan-domain issues.

## Reviewed APD end-to-end evidence

The canonical CLI completed the existing reviewed project and the MCP service
successfully inspected its immutable manifest:

- delivery counts: BOITE 43, CABLE 6, PTECH 167, SITE 2, IMB 682,
  CABLE_SEGMENT 139;
- plan domain: 9,619 raw records conserved, 2,276 block-definition entities
  materialized into 9,143 semantic entities, zero plan-domain issues;
- source geometry: 6/6 cable sources and curve fingerprints checked;
- topology: 139 source edges, zero synthetic route vertices, component
  definitions consistent;
- measurements: 130 measured and 9 explicitly unmeasured segments;
- maximum source-route length closure delta:
  `1.2422788131516427e-08 m`;
- maximum span measurement delta:
  `1.2243219771335134e-08 m`;
- run status: `CONDITIONAL`, because surveyed absolute accuracy was not
  independently verified.

The status is intentional. Passing source geometry/topology/length gates does
not authorize an absolute map-accuracy claim.

## Commands

```powershell
conda env update -n cad2gis -f env/environment.yml --prune
conda activate cad2gis

cad2gis doctor --deep --strict --json
# status=ready, conversion_ready=true, Python 3.12.13

$env:CAD2GIS_READER_BACKEND = "autocad"
python -m pytest -q
# 196 passed, 3 skipped (2 opt-in complex DWGs; 1 unavailable LibreDWG capability)

python -m pytest tests/test_mcp_stdio.py -q
# 1 passed

$env:CAD2GIS_FULL_DWG_TESTS = "1"
python -m pytest tests/test_apd_test_compatibility.py -q
# both complex Lamteh cases pass when explicitly enabled

python tools/diagnostics/plan_domain_probe.py `
  "E:\branch_CAD2GIS\APD_test\APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg"
# status=PASS, issue_count=0

python tools/build_reproducible_wheel.py --output-dir dist --verify
# reproducible=true, identical SHA-256

basedpyright --level error src/cad2gis/cad2gis_v3/plan_domain.py
# 0 errors
```

## Remaining evidence boundaries

- No reviewed semantic mapping or GIS truth is supplied for Lamteh/KLETEK, so
  their business classification accuracy is not claimed.
- No surveyed GCP/check set is supplied for the APD run, so absolute position
  remains unverified.
- A working LibreDWG production runtime is not present in this Windows
  validation environment.
- Full-repository strict typing has historical debt; the new architecture
  boundary is clean, but legacy modules require a separate type-hardening
  project.
- Cross-vendor acceptance requires additional independently reviewed project
  packs, not reuse of APD mappings.
