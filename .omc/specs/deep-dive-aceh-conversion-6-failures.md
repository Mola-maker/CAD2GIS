# Deep Dive Spec: ACEH Conversion — 6-Failure Remediation

## Goal
Fix the ACEH conversion pipeline to produce spatially correct, correctly labeled FTTH features for two independent projects (aceh_main, aceh_sf) at the Kelurahan Lamteh Dayah, Aceh Besar, Indonesia site.

## Trace Findings

Two independent root causes were identified by 3 parallel trace lanes:

**A. CRS misidentification (Lane 2, HIGH confidence):** The ACEH DWG uses a local engineering grid (X≈1k-24k, Y≈-9.8k to -6.4k, span ~23km × 3.4km). aceh.json declared source_crs=EPSG:3857, causing identity pass-through that placed all features at the Web Mercator origin (0°N,0°E — Atlantic Ocean). The paper APD layout contains true geographic annotations (e.g., "5.468867°N, 95.361535°E" near FDT label "KLDYA.011") usable as ground control points.

**B. Label-binding cascade (Lane 1+3, HIGH confidence):** Two sequential bugs: (1) `_LABEL_FAMILY_COMPILED` at ftth_converter.py:273-280 is compiled from Hutabohu DMPH defaults at module load and never rebuilt after aceh.json KLDYA patterns replace `LABEL_FAMILIES` via `_apply_project_config`. (2) aceh.json label_families regexes don't match ACEH actual label text (FAT short codes "A01" vs configured "KLDYA.011.A01"; POLE EXT/HC variants; no IMB family). Combined effect: 0 annotation_assignment_candidates, 100% synthetic CODEs.

**C. Two-DWG structure (Lane 3, MEDIUM confidence):** ACEH main and SF DWGs are independent projects with different layer inventories, not a main+supplement pair.

## Constraints

1. **Converter architecture preserved:** Core module structure (ftth_converter → topology_repair → style_exporter) unchanged
2. **Bug fixes allowed:** Line-level fixes in existing converter code for confirmed bugs (stale cache, DWG_TYPE_* sync)
3. **New modules allowed:** New capability modules (e.g., georeference pre-processing) added to python/ package
4. **Config-first preference:** Label patterns, layer mapping, tolerances configurable via project JSON without code changes
5. **Two separate projects:** aceh_main and aceh_sf get independent configs and GPKGs

## Non-Goals

- CABLE topology iterative correction (deferred — "目前先不急")
- Hungarian assignment for PTECH labels in this round (depends on CABLE segment coloring completing first)
- Automated CRS detection from DWG metadata (DWGs lack embedded CRS info)
- FDT domain decoupling (no plan/topology layouts exist in ACEH DWGs)

## Acceptance Criteria

### P0 — CRS Georeference (blocks all spatial verification)

| ID | Criterion | Verification |
|----|-----------|-------------|
| CRS-1 | Known FDT control points (KLDYA.011, KLDYA.012) fall within Aceh region (5.4-5.5°N, 95.3-95.4°E) ±500m | QGIS with Tianditu basemap overlay |
| CRS-2 | All delivery FC features pass the geographic outlier check (no more GEOGRAPHIC_OUTLIER warnings) | qc_summary table: geographic_outliers=0 |
| CRS-3 | Georeference approach documented: either PROJ string or affine transform with GCP list | georeference section in project config |

### P1 — Label Binding (core quality)

| ID | Criterion | Verification |
|----|-----------|-------------|
| LBL-1 | `_LABEL_FAMILY_COMPILED` rebuilt after `_apply_project_config` label_families override | Code review: one-line fix confirmed |
| LBL-2 | annotation_assignment_candidates table has N>0 features (currently 0) | GPKG post-conversion check |
| LBL-3 | BOITE: ≥60% of FAT features have non-synthetic CODE (label_provenance="annotation-assigned") | field_provenance query |
| LBL-4 | IMB: ≥50% of 849 Home Number features have non-empty display_label showing the house number | IMB layer: display_label field non-empty count |
| LBL-5 | aceh_main.json label_families includes: fat (short codes), pole (with EXT/HC variants), fdt (full IDs), imb (simple integers) | Config file review |

### P2 — Feature Quality

| ID | Criterion | Verification |
|----|-----------|-------------|
| NOISE-1 | Basic Map layer LWPOLYLINE entities excluded from delivery FC layers (in fc_misc only) | drop_accounting: "Basic Map" not in mapped disposition |
| NOISE-2 | Legend cluster exclusion config written for aceh_main (3 unconfirmed clusters → user verified → confirmed) | legend_exclusions.json populated |
| BOITE-1 | BOITE false-positive count reduced: 119 → ≤80 for aceh_main (current FC_misc: FAT entities correctly classified) | GPKG feature count comparison |
| CABLE-1 | CABLE layer QML style includes segment-based coloring by TYPE_CABLE field | .qgz visual inspection |

## Assumptions Exposed

1. **GCP extraction from paper layout:** We assume at least 2 FDT geo-annotations can be associated with model-space features. The APD paper layout has geographic coordinates as text near FDT labels — this linkage is probabilistic (text proximity), not programmatically guaranteed.
2. **FAT label structure:** We assume the FAT CODE layer ("FAT.A01" format) or FAT layer short codes ("A01") can substitute for full KLDYA-prefixed FAT IDs for BOITE labeling. If the full cluster ID (KLDYA.011) + sub-label (A01) composition is required, a code-level label-composition mechanism would be needed — this is deferred.
3. **IMB labels as integers:** Home Number texts are simple integers ("1", "44", "002"). With a regex `^\d{1,4}$` label family, they should bind via Hungarian assignment. If there are no spatial correspondences between Home Number TEXT entities and IMB point features, binding will fail even with correct regex.
4. **CRS approach A (PROJ string) may fail:** The ACEH local grid may not be definable via any standard PROJ parameters. The fallback (approach B: affine georeference) requires extracting GCPs and implementing a coordinate transform pre-processing step.

## Technical Context

### Files to modify

| File | Change | Why |
|------|--------|-----|
| `experiment/python/ftth_converter.py:2090` | Add `_LABEL_FAMILY_COMPILED = _compile_label_families(LABEL_FAMILIES)` after `LABEL_FAMILIES = cfg['label_families']` | Fix stale cache bug |
| `experiment/config/aceh_main.json` | Rewrite label_families with correct regexes + add IMB family | Pattern mismatch fix |
| `experiment/config/aceh_sf.json` | New config for SF project | Separate project |
| `experiment/python/georeference.py` (NEW) | Affine transform module: extract GCPs from paper layout, compute Helmert, apply to model-space coords | CRS fix (approach B) |
| `experiment/python/layout_miner.py` | Add geo-coordinate text mining from "other"-role paper layouts | Feed GCP extraction |
| `experiment/config/legend_exclusions_aceh_main.json` (NEW) | Confirmed legend clusters for aceh_main | Noise reduction |
| `experiment/output/aceh_main/` + `experiment/output/aceh_sf/` | Separate output directories | Two independent projects |

### Architecture: georeference pre-processing flow

```
DWG → [NEW: extract GCPs from paper layout] → [NEW: compute affine transform]
    → [apply transform to all model-space coords] → [existing CRS pipeline] → GPKG
```

The georeference module reads paper-layout TEXT entities classified as "other", regex-matches geographic coordinate patterns (DD.DDDDDD°), extracts the nearest FDT label, associates with model-space FDT features, computes Helmert similarity transform from 2+ GCP pairs, and applies it to all extracted coordinates before they enter the CRS pipeline.

### Label family patterns (aceh_main.json)

```json
"label_families": [
  {"family": "fat_short", "pattern": "^[A-Z]\\d{2}$", "target_fc": "BOITE"},
  {"family": "fat_code",  "pattern": "^FAT\\.[A-Z]\\d{2}$", "target_fc": "BOITE"},
  {"family": "pole",      "pattern": "^(EXT\\.)?MR\\.(KLDYA|XXX|IJY\\.KLDYA|MYD\\.S\\d+)\\.P\\d{3}(\\.HC)?$", "target_fc": "PTECH"},
  {"family": "fdt",       "pattern": "^KLDYA\\.\\d{3}$", "target_fc": "SITE"},
  {"family": "imb",       "pattern": "^\\d{1,4}$", "target_fc": "IMB"}
]
```

## Ontology

| Entity | Definition | Label Source | ACEH Pattern |
|--------|-----------|-------------|--------------|
| BOITE | FTTH box/splitter/closures | FAT layer short codes or FAT CODE layer | "A01", "FAT.A01" |
| CABLE | Fiber optic cable segment | Synthetic only (no cable labels in DWG) | N/A |
| PTECH | Pole/chamber/support structure | POLE ID layer or EXT POLE texts | "MR.KLDYA.P001", "EXT.MR.IJY.KLDYA.P002" |
| SITE | FDT cabinet equipment | FDT layer texts | "KLDYA.011", "KLDYA.012" |
| IMB | Building/home pass | Home Number layer | "1", "44", "002" |
| ZPM | Distribution zone polygon | FAT AREA layer geometry | N/A |
| INFRASTRUCTURE | Road/duct linear infrastructure | JALAN layer | N/A |

## Ontology Convergence

- FAT labeling: 2 candidate sources (FAT layer short codes vs FAT CODE layer compound labels). Both will be tried; the first producing ≥60% binding wins.
- POLE labeling: The broad regex captures all observed variants. Risk: false matches. Mitigation: Hungarian nearest-neighbor spatial gate.
- IMB labeling: Simple integers risk false-binding to non-IMB text entities. Mitigation: constrain to Home Number layer only via layer_pattern_map.
- Two-level FAT (cluster + sub-label) is deferred — not addressed in this round.

## Interview Transcript

| Round | Question | Answer | Resolution |
|-------|----------|--------|------------|
| Q1 | CRS: try PROJ (config-only) or affine georeference (new code)? | Both — try A first, B as fallback | CRS approach defined |
| Q2 | Code modification boundary? | Fix bugs + add new modules, preserve core architecture | Scope boundary set |
| Q3 | Priority stack? | CRS #1, then all 6 issues | Priority defined |
| Q4 | Acceptance criteria with specific thresholds? | BOITE: reduce false positives + labels; IMB: labels must be visible; PTECH labels after CABLE coloring; topology deferred | AC refined |
| Q5 | Which thresholds to adjust? | User provided specific adjustments (BOITE noise, IMB empty, PTECH labels, topology de-prioritized) | AC adjusted |
| Q6 | Final scope confirmation? | Confirmed: P0 CRS, P1 label binding, P2 noise + CABLE coloring, P3 topology deferred | Scope frozen |
