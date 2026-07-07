# CAD2GIS 技术方案 / Technical Plan

**Competition XA-202610 (烽火通信 / FiberHome)** — 子赛题2 / Sub-track 2: 多源异构工程数据融合
(Multi-source heterogeneous engineering-data fusion)

**Lossless parsing + semantic conversion of historical CAD (DWG/DXF) communications-infrastructure
drawings into a QGIS-based GIS system with standardized GeoPackage warehousing.**

---

## 1. 问题与目标 / Problem & Target

Historical comms-engineering drawings are almost always **DWG** (Autodesk binary, closed format),
mixing many disciplines (comms/power/water/survey) in one file with no georeference and opaque
symbol blocks. The competition requires **≥ 90 % auto-conversion accuracy**, measured
multi-dimensionally (geometric / semantic / positional-CRS / network / attribute) against labeled
ground truth — a single blended number is not defensible.

**Target metric:** ≥ 90 % overall conversion accuracy on the real DS-04 organizer drawing,
measured across all five dimensions with an independently-anchored ground truth.

## 2. "The right form of CAD" — discovery conclusion

- Historical comms drawings are **DWG** (binary). The reliable, programmable form is **DXF**
  (documented interchange).
- **Normalization:** DWG → DXF via **LibreDWG `dwg2dxf`** (the real organizer files are AC1021 /
  AutoCAD 2007, which the GDAL OGR CAD driver cannot read — empirically confirmed).
- **Canonical internal form:** normalized DXF → in-memory typed feature model (Shapely geometry +
  full source provenance).
- **CAD structures handled:** layers; blocks/INSERT with ATTRIB; LINE/LWPOLYLINE/POLYLINE/ARC/
  CIRCLE/ELLIPSE/SPLINE/POINT/TEXT/MTEXT/HATCH; units (INSUNITS); annotation kept separate.

## 3. Architecture — deterministic core + offline assist, surfaced via QGIS

**Principle (guardrail):** a deterministic, provenance-preserving core pipeline is the
*authoritative* transformation. Any "intelligent" assist is **rules-first, review-only, never
silently applied** — intelligence must never corrupt authoritative geometry. This is both correct
engineering and the innovation story for judges.

### Pipeline (8 phases, each with a validation gate — designed with Codex)

```
Ingest → Profile → Parse → Classify → Topology/Refine → Network → Georeference → Warehouse → Accuracy
  gate     gate    gate     gate        gate             gate      gate          gate        gate
```

| Phase | Module | What it does | Gate (pass criteria) |
|---|---|---|---|
| 0. Verify | `verify/protocol.py` + `per_feature.py` | 5-dim accuracy protocol + per-feature cross-source verification | scoring reproducible; not-scored dims marked |
| 1. Ingest | `ingest.py` | DWG→DXF (LibreDWG), encoding-safe, xref detect | DXF loads in ezdxf; manifest emitted |
| 2. Profile | `profile.py` | inventory layers/blocks/entities/extent; CRS-classify | profile.json written (UTF-8) |
| 3. Parse | `parse.py` | ezdxf→typed features; node/symbol blocks as Points; provenance | entity counts reconcile |
| 4. Classify | `mapping/engine.py` + `block_codes.yaml` + `feature_context.py` | rules + reviewed opaque-code table, evidence-gated (hit vector) | coverage; no paving leak |
| 5. Topology/Refine | `topology.py` + `refine.py` | STRtree clean; noise-fragment demotion; route snapping; **label propagation** | no over-aggressive repairs |
| 6. Network | `network.py` | node-edge graph + **junction synthesis** at route splices | connectivity_ratio |
| 7. Georeference | `gcp.py` | GCP from in-drawing X=/Y= labels; **consensus re-pair**; similarity fit | RMSE < 3 m; outliers logged |
| 8. Warehouse | `warehouse/` | GeoPackage + published schema + provenance + metadata + QML | QGIS opens; schema validates |
| 9. Accuracy | `verify/` | 6-dim score + per-feature cross-source verification | ≥ 0.90; honest methodology |

### The hit vector (per entity) — multi-signal classification

`layer · block-code · entity-type · nearest-TEXT-label (ranked kNN, ATTRIB-first) · block
geometry-fingerprint · spatial-context · coordinate-table cross-reference`. Carried by
`FeatureContext`; consumed by `engine.classify_context()`.

**Why:** on the real DS-04 drawing, layer-name alone left 807 opaque INSERT blocks unmapped. The
`gc*` codes are not self-describing — `gc170` is a duct cross-section, `gc043` is a paving symbol,
and only the **nearest-text label** separates them (`3孔PVC110` → duct; `地砖` → paving).

### Where AI belongs — OFFLINE ONLY (designed with Codex)

An LLM reads the evidence package (fingerprint + nearest-text per opaque block) and *proposes*
block-code→facility mappings; a human reviews; the result is **frozen into `block_codes.yaml`**.
**No runtime LLM in the conversion route** — runtime stays 100 % deterministic and reproducible.
Confidence is **audit metadata only — it never inflates the accuracy score**.

## 4. Platform surfaces

- **CLI** (`cad2gis convert …`) — canonical, reproducible headless runner.
- **QGIS Plugin (PyQGIS)** — primary UX: dockwidget, load GeoPackage layers, view QC report.
- **Web demo** (`demo/`) — Claude-style static page: 6-dim accuracy dashboard, before/after,
  per-feature verification, evidence package, pipeline gates.

## 5. Tech stack

Python 3.12 · `ezdxf` (authoritative parse) · GDAL/OGR · Shapely/GEOS (STRtree) · `pyproj`/PROJ ·
GeoPandas · QGIS PyQGIS · LibreDWG (`dwg2dxf`) · GeoPackage store · numpy (least-squares georef).

## 6. Key risks resolved

- **DWG→DXF fidelity (AC1021):** GDAL OGR can't read it → LibreDWG `dwg2dxf` (isolated env).
- **O(n²) topology on 83k entities:** layer-scoping + STRtree.
- **Node identity lost (manholes 0→258):** parser emits well-block INSERTs as Points at insertion.
- **Opaque gc* codes (807 unmapped):** hit-vector + reviewed `block_codes.yaml` + evidence gate.
- **Network connectivity 0.115 → 0.85:** noise-fragment filter + junction-node synthesis.
- **CRS ambiguity (local-engineering):** GCP consensus re-pair from in-drawing surveyed labels;
  honest declaration (local grid, no overclaimed EPSG).
- **Tautological correctness (audit-caught):** semantic now reported as coverage; per-feature
  cross-source verification added.
