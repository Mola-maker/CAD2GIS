# Session Summary 2026-07-22

## Overview

Full-stack session: ACEH project conversion → deep-dive investigation → ralplan consensus → autopilot execution → CAD2GIS_Linux branch migration.

Source repo: `/home/cat/projects/CAD2GIS` (main branch)
Output repo: `/home/cat/projects/CAD2GIS_Linux` (CAD2GIS_Linux branch, orphan)

## Phase 1: ACEH Conversion Initial Attempt

**Input DWGs:**
- `APD - KELURAHAN LAMTEH DAYAH ACEH.dwg` (5.1M, 3283 Model entities)
- `APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg` (4.7M, 213 Model entities)

**User feedback (6 issues):**
1. 噪声多 — Basic Map background (1178 entities) + unconfirmed legend clusters
2. 地理坐标在大西洋 — local engineering grid treated as EPSG:3857 → Atlantic Ocean
3. CABLE 无分段设色 — QML coloring not applied
4. BOITE 错误点位 + 标签不匹配 — 100% synthetic CODEs
5. IMB 标签为空 — 849 Home Number features with empty display_label
6. CABLE 拓扑在 PTECH 密集处错误 — 25 FLOATING_CABLE, 34 ISOLATED_NODE

## Phase 2: Deep-Dive Investigation

**Trace (3 parallel lanes):**

| Lane | Hypothesis | Confidence | Key Finding |
|------|-----------|------------|-------------|
| 1 | Code-path: _LABEL_FAMILY_COMPILED stale cache | HIGH | Compiled at module load from DMPH defaults, never rebuilt after aceh.json KLDYA override → 0 annotation assignments |
| 2 | CRS: local grid treated as EPSG:3857 | HIGH | ACEH coords X≈1k-24k, Y≈-9.8k to -6.4k; paper APD has true geo coords (5.468°N, 95.368°E); identity pass-through → Atlantic Ocean |
| 3 | Assumption mismatch: Hutabohu model doesn't generalize | HIGH | aceh.json regexes don't match ACEH text (FAT short codes "A01" vs "KLDYA.011.A01"); no IMB label family; two DWGs are independent projects |

**Interview (6 rounds):**
- Q1: CRS approach → Both (try PROJ first, affine georeference fallback)
- Q2: Code modification boundary → Fix bugs + add new modules
- Q3: Priority → CRS #1, then all 6 issues
- Q4: Acceptance criteria → Adjusted (BOITE noise reduction, IMB labels must be visible, PTECH labels after CABLE coloring)
- Q5: Threshold tuning → De-prioritized CABLE topology, emphasized BOITE/IMB labels
- Q6: Scope confirmed

**Spec:** `.omc/specs/deep-dive-aceh-conversion-6-failures.md`

## Phase 3: Ralplan Consensus

**Plan:** `.omc/plans/ralplan-aceh-6fixes.md`

**Reviewers:**
- Architect r1: APPROVED_WITH_SUGGESTIONS (R1: non-existent function, R2: dependency reorder, R3: georeference boundary, R4: shared config)
- Critic r1: REJECT (C1: broken code block, C2: wrong GPKG schema in verification, C3-C5: missing recovery/GCP threshold/AC scope)
- Plan revised r2: All CRITICAL + MAJOR fixes applied, ADR + Design Constraints + Recovery added
- Architect r2: APPROVED_WITH_SUGGESTIONS (minor Step 1.4 consistency fix)
- Critic r2: ACCEPT

## Phase 4: Autopilot Execution

**Phase 0 — Code fix (completed):**
- `ftth_converter.py:2094`: Added `_LABEL_FAMILY_COMPILED[:] = [...]` rebuild after config override
- Verified: 5 ACEH patterns compile and match correctly

**Phase 1 — Georeference (completed):**
- New `georeference.py`: Helmert similarity transform + Indonesian CRS auto-detection
- `layout_miner.py`: Added `extract_geo_annotations()` for paper-layout GCP extraction
- `cad_common.py`: Added `_GEOREF_HELMERT` + `set_georeference()` hook in `_reproject_point`

**Phase 2 — Configs (completed):**
- `aceh_main.json`: 5-family label patterns (fat_short, fat_code, pole, fdt, imb)
- `aceh_sf.json`: SF-specific config
- Regressed feature counts tracked to layer_pattern_map → fixed by using old working patterns

**Phase 4 — Verification (blocked):**
- Label binding still 0 candidates despite correct `_LABEL_FAMILY_COMPILED` rebuild
- **Root cause discovered:** LibreDWG TEXT entity text extraction returns empty strings
  - `_entity_utf8_text` dynapi call works for ATTRIB entities but fails for TEXT entities
  - `TEXT_r11` type (49) not in `text_types` set — 83 entities lost
  - This is a pre-existing bug, not introduced by our changes

**Known unresolved:**
- TEXT entity text extraction (LibreDWG dynapi bug)
- CRS georeference not active (no GCP extraction run)
- CABLE topology correction deferred (user: "目前先不急")

## Phase 5: CAD2GIS_Linux Branch Migration

**Portable changes (3 files, zero WSL2 hardcoded paths):**

| File | Change |
|------|--------|
| `cad_common.py` | `_find_libredwg()` multi-path search: `$LIBREDWG_SO` → `/usr/local/lib/` → `/usr/lib/` → `./`; `ctypes.util.find_library("c")` for libc |
| `ftth_converter.py` | SWIG search: `$LIBREDWG_PYTHON` → `site.getusersitepackages()` → `sysconfig.get_path()` |
| `domain_vocab.py` | Removed `official/Shape/` CSV dependency; uses hardcoded fallback only |

**evaluation_standards reorganization:**
```
evaluation_standards/APD/  ← As Plan Drawing type (Indonesian FTTH, currently Moroccan standards)
```

**Branch structure:**
```
CAD2GIS_Linux/
├── python/              16 modules (portable)
├── config/               5 JSONs
├── evaluation_standards/APD/  8 CSVs
├── archives/             consolidation report
├── .omc/                 specs(25) + plans(5) + wiki(38)
├── .gitignore
└── README.md
```

**Commit:** `b519fb3` — orphan root commit, 102 files, 22,022 insertions

**Bad commit `62155e9` force-pushed over** — contained `.omc/sessions/` runtime artifacts

## Key Technical Debt Recorded

1. `ftth_converter.py:273-280`: `_LABEL_FAMILY_COMPILED` inline comprehension should be extracted to function
2. `cad_common.py:233`: georeference hook in `_reproject_point` — should be in `init_crs()` (Design Constraint #3)
3. `georeference.py`: Helmert only (no affine); DMS coordinate format not supported
4. `layout_miner.py`: DD.DDDDDD° regex hardcoded for geo annotation extraction
5. `domain_vocab.py`: `SHAPE_DIR` removed but `import os` still present (CLI uses it)
6. TEXT entity text extraction via LibreDWG dynapi — blocks all annotation-based label binding

## Ontology

| Entity | Definition |
|--------|-----------|
| BOITE | FTTH box/splitter/closure (FAT, CLOSURE layers) |
| CABLE | Fiber optic cable (SLING WIRE, FO * CORE layers) |
| PTECH | Pole/support structure (NEW POLE *, EXT POLE, POLE ID) |
| IMB | Building/home pass (Home Number layer) |
| SITE | FDT cabinet (FDT layer) |
| ZPM | Distribution zone polygon (FAT AREA layer) |
| INFRASTRUCTURE | Road/duct (Garis Jalan, JALAN) |
| APD | As Plan Drawing — Indonesian FTTH engineering drawing type |
