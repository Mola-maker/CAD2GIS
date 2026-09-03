# Memory optimization validation matrix (lamteh_main)

Date: 2026-09-03
Environment: robustness workspace `.venv` (Python 3.12.3, GDAL 3.8.4 local);
container experiment: `cad2gis-ftth:0.1.0` rebuilt from current `src/` (GDAL 3.10.3).

## Peak RSS progression (`/usr/bin/time -v`, same machine, same lamteh_main DWG)

| Step | Commit | Peak RSS (KB) | Peak RSS (GiB) | vs baseline | evidence_graph.json sha256 == baseline | delivery.gpkg sha256 == baseline |
|---|---|---|---|---|---|---|
| baseline (pre-S1) | `c3a4e5a` + EMR fix | 3,765,732 | 3.59 | — | ✅ `239101f6…` | ✅ `77f87865…` |
| S1 single serialization cache | `8700c05` | 3,552,132 | 3.39 | -5.7% | ✅ | ✅ |
| S2 build-index release | `2e3768d` | 3,552,612 | 3.39 | -5.7% | ✅ | ✅ |
| S3 streaming graph write | `cb73aa9` | 1,592,616 | 1.52 | **-57.7%** | ✅ | ✅ |
| S4 records lifecycle trim | `5813151` | 1,592,632 | 1.52 | **-57.7%** | ✅ | ✅ |

Acceptance target: lamteh_main peak RSS ≤ 2.5 GiB (2,621,440 KB) — **PASS at S3 and S4**.

S3 switch A/B check on the final S4 tree:
`CAD2GIS_GRAPH_STREAMING=0` → 3,553,832 KB (3.39 GiB), artifacts still
byte-identical to baseline; `=1` (default) → 1,592,632 KB (1.52 GiB).

Baseline sha256 (from `baselines/lamteh_main/run/`):

```text
239101f64228f2f3011c340d847fcb358db3ce85726055a56dbed9485323a48f  reasoning/evidence_graph.json
77f878654b1c5c0e17e74c18a6753bc28de045107e2fd2d9fbe5eb0cd5dbccaf  delivery.gpkg
```

## Small-image non-regression (kletek)

| Run | Peak RSS (KB) | vs |
|---|---|---|
| baseline code (`c3a4e5a`, PYTHONPATH worktree) | 318,432 | — |
| S4 code | 206,144 | **-35.3% (no regression)** |

kletek `delivery.gpkg` sha256 matches `baselines/kletek/run/delivery.gpkg` in both runs:
`ca8fecf4ccc8fa2c393108b784584a12092a0625c4e646a0f711e49d1a29147d`.

## Regression

- Full workspace suite (after installing optional `mcp` dependency):
  **263 passed, 6 skipped** in 5.12s.
- Contract-focused suites (`test_crosscad_contracts`, `test_verification_matrix`,
  `test_baseline_reconciliation`, `test_compare_runs`): **41 passed**.
- Skipped: `tests/test_apd_test_compatibility.py` external APD_test corpus is not
  installed at the default root. When supplied via a symlinked corpus root,
  `test_apd_test_reader_contract` fails with `KeyError: 'parsed_rows'` **both on
  the pre-S1 baseline commit and on S4** — a pre-existing reader-diagnostics
  test/code mismatch, unrelated to this memory work.

## Container experiment (optional acceptance)

- Image rebuilt from current `src/` (libredwg compile layer cache hit).
- `docker run --rm --memory 4g --memory-swap 4g -v /tmp/cad2gis_out_4g:/out cad2gis-ftth:0.1.0`
  completed all 10 sites: `ALL 10 SITES CONVERTED -> /out` — **PASS**.
- Container lamteh_main `reasoning/evidence_graph.json` sha256 == baseline ✅.
- Container lamteh_main `delivery.gpkg` content: all 17 logical tables
  (`BOITE/CABLE/IMB/INFRASTRUCTURE/PTECH/SITE/ZNRO/ZPM`, gpkg metadata,
  `layer_styles`) are row-for-row identical to the local baseline; the file
  sha256 differs only in SQLite RTree internal byte layout because the image
  uses GDAL 3.10.3/SQLite while the local baseline was produced by GDAL 3.8.4.
  This container-vs-local sha difference also existed in the pre-optimisation
  image build (same `a161e960…`), so it is environmental and not caused by
  S1-S4.

## Assets

- `baseline_peak_time.log`, `s1_peak_time.log`, `s2_peak_time.log`,
  `s3_peak_time.log`, `s4_peak_time.log` — `/usr/bin/time -v` peak logs
- `baseline_sha256.txt`, `baseline_run_sha256.txt`, `s*_sha256.txt` — artifact digests
- `baseline_probe_reader.log` — reader-stage RSS probe (§6.1B)
- `kletek_baseline_peak_time.log`, `kletek_s4_peak_time.log` — small-image gates
- `docker_4g_run.log`, `docker_4g_lamteh_sha256.txt` — 4g container experiment
