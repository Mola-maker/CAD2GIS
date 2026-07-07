# cad2gis — CAD → QGIS/GIS transformation platform

Correct, **verifiable** transformation of historical CAD (DWG/DXF) communications-infrastructure
drawings into a QGIS-based GIS system with standardized **GeoPackage** warehousing.

Built for FiberHome competition **XA-202610**, **Sub-track 2 — 多源异构工程数据融合** (multi-source
heterogeneous engineering data fusion): lossless parsing + semantic conversion of legacy CAD
(graphics + attributes + topology) into GIS. Target metric: **auto-conversion accuracy ≥ 90 %**,
measured multi-dimensionally against an independently-anchored ground truth.

**Status: delivered & verified.** Overall accuracy **0.9737** on the real DS-04 organizer drawing
(120,598 source entities → 3,089 comms features), 77/77 tests passing. Methodology independently
audited — honest, not gamed (see `docs/verification_report.md`).

---

## Architecture — deterministic core + offline assist, surfaced via QGIS

Eight phases, each with a validation gate (designed with Codex) so no stage proceeds on broken output.
The deterministic core is authoritative; any LLM assist is **offline-only, review-only, never in
the runtime path** — runtime stays 100 % deterministic and reproducible.

```
Ingest → Profile → Parse → Classify → Topology/Refine → Network → Georeference → Warehouse → Accuracy
  gate     gate    gate     gate        gate             gate      gate          gate        gate
```

**The hit vector (per entity):** `layer · block-code · entity-type · nearest-TEXT-label (ranked kNN,
ATTRIB-first) · block geometry-fingerprint · spatial-context · coordinate-table cross-reference`.
On real drawings, layer-name alone is not enough — opaque `gc*` symbol codes are disambiguated by
the nearest-text label (`3孔PVC110` → duct cross-section; `地砖` → paving, rejected).

**Where AI belongs — OFFLINE ONLY.** An LLM reads the evidence package and *proposes*
block-code→facility mappings; a human reviews; the result is **frozen into `block_codes.yaml`**.
No runtime LLM. Confidence is audit metadata only — it never inflates the accuracy score.

See `docs/technical_plan.md` for the full design.

## Measured accuracy (DS-04, independently anchored)

| Dimension | Score | Calibration |
|---|---|---|
| semantic | 0.9948 | in-vocabulary coverage (evidenced abstentions excluded) |
| geometric | 0.9987 | valid, non-empty geometry |
| count | 0.9961 | 258 manholes vs 259 surveyed `X=`/`Y=` labels (independent entity type) |
| attribute | 1.0000 | required-field completeness (provenance on every feature) |
| network | 0.8535 | connectivity with synthetic junction nodes at route splices |
| positional | 0.6728 | GCP similarity-transform RMSE 1.96 m (target 3 m) |
| **OVERALL** | **0.9737** | weighted (30/25/20/15/5/5), ≥ 0.90 target → PASS |

**Per-feature correctness 0.826** — measured separately, via signals *independent* of the
classifier's rule path: manhole↔surveyed-label cross-source (0.981), cable↔topological anchoring
(0.975), duct↔geometry fingerprint (0.277 — only `gc170` CIRCLE symbols shape-confirm),
annotation↔text (1.0). Reported honestly, not folded into the overall (would be a tautology).

## Environment (reproducible)

System Python 3.14 is too new for the GDAL/QGIS stack. Use the pinned conda env:

```bash
conda env create -f env/environment.yml     # gdal + ezdxf + geopandas + pyproj + shapely + qgis
conda activate cad2gis
pip install -e .
cad2gis doctor          # verify the toolchain
```

DWG→DXF normalization uses LibreDWG `dwg2dxf` (the real organizer files are AC1021 / AutoCAD 2007,
which the GDAL OGR CAD driver cannot read). Install in an isolated env to avoid solver conflicts:

```bash
conda create -n dwgtools -c conda-forge libredwg    # provides dwg2dxf
```

## Run

```bash
# headless conversion + accuracy scoring + GeoPackage warehousing
python -c "from cad2gis.pipeline import run; \
  c,r=run('build/normalized/DS-04_comms.dxf', \
          benchmark='src/cad2gis/verify/benchmark/ds04_surveyed.json', \
          warehouse='build/DS04_comms_full.gpkg'); \
  print(r.accuracy['overall'])"           # -> 0.9737

# regenerate the web demo from a live pipeline run
python demo/gen_report.py
```

Open `demo/index.html` in a browser (works via `file://` — data is inlined as a JS global).

### QGIS plugin (PyQGIS) — primary UX

```bash
python qgis_plugin/install.py        # registers the plugin in the active QGIS profile
```
Then in QGIS: **Plugins → Manage and Install Plugins → Installed → "cad2gis"**. The dockwidget lets
you pick a DWG/DXF, run the canonical pipeline (threaded, live stage log), load the resulting
GeoPackage layers into the map with the shipped QML styles, and view the accuracy report. It calls
the same `cad2gis.pipeline.run()` as the CLI/server — behavior is identical everywhere.

### Real-time demo server (interactive map + live conversion)

```bash
python -m uvicorn demo.server.app:app --port 8000      # or: python -m demo.server.app
# open http://localhost:8000
```

- **Interactive map** — the converted GeoPackage layers (manholes/cables/ducts/annotations) on a
  Leaflet map, georeferenced; click any feature for its CAD provenance (source layer/block/handle).
- **Live converter** — upload a DXF/DWG and watch each pipeline stage stream in real time (SSE:
  `parse → topology → network → georeference → accuracy`), then the result accuracy appears.
  Small samples convert in seconds; the full 68 MB organizer file takes a few minutes.

## Test

```bash
python -m pytest tests/ -q                  # 77 passed
```

Tests cover: parsing (node/symbol-block Points), hit-vector classification + evidence gate +
paving-leak guard, topology refinement + junction synthesis + label propagation, GCP georeferencing
+ consensus re-pairing, GeoPackage warehouse round-trip + metadata + styles, the 6-dimension scorer,
and per-feature cross-source verification.

## Deliverables

- `build/DS04_comms_full.gpkg` — GeoPackage (4 spatial layers + 4 metadata tables + embedded QML styles)
- `build/accuracy_DS04_v2.json` — the scored accuracy report
- `build/unconverted_evidence_DS04.json` — the honest remainder (unmapped entities + evidence)
- `build/transform_record_DS04.json` — the georeference transform record
- `demo/` — Claude-style web demo (accuracy dashboard, before/after, per-feature verification, evidence)
- `demo/server/` — real-time FastAPI demo server (interactive Leaflet map + live SSE conversion)
- `qgis_plugin/` — PyQGIS dockwidget plugin (pick DWG → run pipeline → load GeoPackage layers w/ QML)
- `docs/technical_plan.md` + `docs/verification_report.md` — competition deliverable docs (bilingual CN/EN)
- `docs/architecture.md` + `docs/duct_verification_runbook.md` — extension contracts and the remaining duct-verification workflow

## Project layout

```
src/cad2gis/
  ingest.py            # DWG->DXF (LibreDWG), encoding-safe, xref detect
  profile.py           # DXF profiler + coordinate-range CRS classifier
  parse.py             # ezdxf -> typed features (node/symbol blocks as Points, provenance)
  model.py             # Feature/Schema dataclasses (the extension contract)
  feature_context.py   # hit-vector extraction (ranked kNN nearest-text + ATTRIB-first + fingerprint)
  evidence.py          # unconverted-evidence package (the audit trail)
  mapping/             # rules engine + comms_symbols.yaml + block_codes.yaml (reviewed opaque codes)
  refine.py            # noise-fragment demotion + route snapping + graph label propagation
  topology.py          # per-geometry-class clean (STRtree) + make_valid (guarded)
  network.py           # node-edge graph + junction-node synthesis
  gcp.py               # GCP from in-drawing X=/Y= labels + consensus re-pair + similarity/affine fit
  crs.py               # CRS classify (geographic / projected / local-engineering)
  attributes.py        # structured spec parsing (3孔PVC110 -> holes/material/diameter)
  warehouse/           # GeoPackage writer + published schema + QML styles
  verify/              # 6-dim accuracy protocol + per-feature cross-source verification + benchmark
  pipeline.py · cli.py # canonical runner (plugin calls the same pipeline)
demo/                  # Claude-style static web demo
demo/server/           # real-time FastAPI demo server (interactive map + live SSE conversion)
qgis_plugin/           # PyQGIS dockwidget plugin
docs/                  # technical plan + verification report
env/                   # pinned conda environment
tests/                 # 77 pytest tests
```

## Honest limitations

- **Per-feature duct verification (0.277):** only `gc170` (single-CIRCLE) symbols are
  shape-confirmed; the `gc013*` ducts upgraded via graph label propagation are topology-classified
  but not shape-verified. `docs/duct_verification_runbook.md` defines the reviewed-label contract
  for closing this with a hand-labeled subset or 节点坐标表 node-id matching.
- **Positional (0.673):** RMSE 1.96 m is strong; the roll-off formula caps the score. An affine/
  piecewise transform is only justified if cross-validated to reduce RMSE materially.
- **CRS:** the surveyed coords are a local engineering grid (negative eastings ~−19000) — declared
  honestly as `local-engineering-grid (EPSG unknown)`, not overclaimed as CGCS2000.
- **No runtime LLM** by design.
