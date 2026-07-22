# Implementation Plan: ACEH Conversion 6-Failure Remediation

**Status:** APPROVED (Ralplan consensus — Architect + Critic approved revision 2)
**Reviewers:** Architect (APPROVED_WITH_SUGGESTIONS r2), Critic (ACCEPT r2)
**Source Spec:** `.omc/specs/deep-dive-aceh-conversion-6-failures.md`
**Created:** 2026-07-22

## RALPLAN-DR Summary

### Principles (3-5)
1. **Config-first**: Label patterns, layer mapping, tolerances overrideable via JSON without touching converter core
2. **Minimal incision**: Line-level fixes for confirmed bugs only; new capability in dedicated modules
3. **CRS pre-condition**: Georeference must complete before any spatial QC is meaningful
4. **Separate projects**: aceh_main and aceh_sf are independent — no shared config, no merged GPKG
5. **Evidence-gated AC**: Every acceptance criterion includes a grep-able or table-queryable verification

### Decision Drivers (top 3)
1. **CRS blocks everything** — without correct geographic placement, label fixes and QC are unverifiable
2. **Label binding is 0%** — `_LABEL_FAMILY_COMPILED` stall + regex mismatch = 0 annotation assignments
3. **Noise ratio 44%** — Basic Map background + unconfirmed legend clusters inflate fc_misc

### Viable Options

**Option A: Sequential P0→P1→P2 (Recommended)**
- CRS georeference first → verify spatial correctness → label binding fixes → noise cleanup
- Pros: Each phase validates independently, rollback safe
- Cons: More conversion runs (3-4), total ~2 hours

**Option B: Batch all fixes at once**
- Apply all 7 changes in one pass, run single conversion
- Pros: Fastest (1 run)
- Cons: If CRS is wrong, all other fixes produce unverifiable results; hard to isolate which fix broke what

**Option C: CRS only, defer labels**
- Only georeference this round, label fixes next round
- Pros: Most focused, lowest risk
- Cons: User explicitly requested all 6 issues in this round → violates scope

**Invalidation rationale for rejected alternatives:**
- Option B rejected because CRS failures contaminate all spatial measurements (Hungarian assignment distances are in ground meters — wrong CRS = wrong distances = wrong label assignments)
- Option C rejected because user confirmed "all 6 issues" in Q3 interview

### Decision
Option A (sequential) is the recommended approach. Within each phase, parallelizable sub-steps are batched. This aligns with Principle 3 (CRS pre-condition) and Driver 1 (CRS blocks everything).

---

## Requirements Summary

Fix the ACEH conversion pipeline for two independent projects (aceh_main, aceh_sf) at Kelurahan Lamteh Dayah, Aceh Besar, Indonesia. The pipeline currently produces features at the Atlantic Ocean (CRS bug) with 100% synthetic CODEs (label binding bug) and 44% noise ratio (Basic Map + legend clusters unexcluded).

## Implementation Steps

### Phase 0: Code Bug Fixes (pre-requisite, 15 min)

**Step 0.1: Fix `_LABEL_FAMILY_COMPILED` stale cache** ⚠️ MUST RUN FIRST (before any GCP validation)
- File: `experiment/python/ftth_converter.py`
- At line ~2090, after `LABEL_FAMILIES = cfg['label_families']` in `_apply_project_config()`:
  ```python
  # Rebuild the compiled label-family cache so the newly-overridden
  # patterns take effect. The compilation is an inline comprehension at
  # module level (lines 273-280); reproduced here to avoid extracting a
  # phantom function that does not exist.
  _LABEL_FAMILY_COMPILED[:] = [
      {"family": f["family"], "target_fc": f["target_fc"],
       "regex": re.compile(f["pattern"]),
       "node_color_filter": f.get("node_color_filter"),
       "min_distance_m": f.get("min_distance_m"),
      }
      for f in LABEL_FAMILIES
  ]
  ```
- Verification: `grep -n "_LABEL_FAMILY_COMPILED\[:" ftth_converter.py` shows rebuild call after `LABEL_FAMILIES =` assignment
- **Tech debt note**: `_LABEL_FAMILY_COMPILED` is a module-level list (`[:]` slice mutation preserves the reference shared across `_match_label_family()` closures). The inline comprehension at lines 273-280 should be extracted to a proper function in a future refactor.

### Phase 1: CRS Georeference (P0, 60 min)

**Step 1.1: Extract GCPs from paper layout annotations**
- File: `experiment/python/layout_miner.py`
- Add function `extract_geo_annotations(mined, layout_name, proximity_threshold=100.0)`:
  - Search TEXT/MTEXT entities in the given paper layout for coordinate patterns: `(\d+\.\d+)°.*?(\d+\.\d+)°`
  - Extract the nearest FDT label text (e.g., "KLDYA.011") within `proximity_threshold` paper-space units of the geo annotation's insertion point (APD layout scale: ~1 unit ≈ 1 DWG unit)
  - Return `[(fdt_label, lat, lon), ...]`
- **Rationale for 100-unit threshold**: APD layout annotation texts sit near FDT labels at ~10-50 units distance; 100 units provides 2× margin without capturing unrelated text
- **Tech debt note**: DD.DDDDDD° format hardcoded — DMS variants ("5°28'07.92\"N") would silently fail. Add DMS parsing before web deployment.

**Step 1.2: Try standard CRS identification (Approach A)**
- File: `experiment/python/georeference.py` (NEW)
- Function `try_identify_crs(gcp_pairs)`:
  - For each Indonesian CRS candidate (EPSG:32746, EPSG:32747, EPSG:23830-23849 DGN95 zones):
    - Project GCP geographic coords (EPSG:4326) to candidate CRS
    - Compare with model-space GCP coords
    - If scale factor ≈ 1.0 and residual < 10m across GCPs → CRS found
  - Return `(epsg_code, confidence)` or `(None, 0)` if no match
- **Tech debt note**: Indonesian CRS list hardcoded — should be configurable per-project for non-Indonesia sites

**Step 1.3: Affine georeference fallback (Approach B)**
- File: `experiment/python/georeference.py` (same module)
- Function `compute_helmert(model_pts, geo_pts)`:
  - Input: list of (mx, my) model coords + corresponding (lon, lat) geo coords
  - Compute 2D Helmert similarity (scale, rotation, translation) from 2+ GCPs
  - Return `(transform_matrix, residuals)`
- Function `apply_georeference(features, gcp_pairs, source_crs, target_crs)`:
  - Compute Helmert from GCPs
  - Apply affine transform to all model-space coordinates
  - Pass through existing CRS pipeline (cad_common._CRS_TRANSFORM)
- **Tech debt note**: Helmert only (scale+rotation+translation) — no shear correction. For DWGs with anisotropic distortion, a full affine (6-param) may be needed. Current GCP count (2) insufficient for full affine validation.

**Step 1.4: Wire georeference into CRS pipeline**
- File: `experiment/python/cad_common.py`
- In `init_crs()`: after source/target SRS setup, if project config has `georeference.gcp_pairs`, compute Helmert from GCPs and apply to the coordinate transform queue. This keeps georeference as a CRS-pipeline concern, not converter orchestration (see Design Constraint #3).
- File: `experiment/config/aceh_main.json` — add `georeference` section with `auto_extract: true` and `gcp_pairs` (or leave empty for auto-extraction from paper layout)
- File: `experiment/config/aceh_sf.json` — add georeference section
- **Tech debt note**: The `init_crs()` function signature needs an additional optional parameter for the project config dict, or georeference parameters must be passed via a module-level global set before `init_crs()` is called. Currently `init_crs(source, target)` only takes two strings — adding a `georef_config=None` keyword argument preserves backward compatibility.

**Step 1.5: GCP validation**
- Extract GCPs from SF DWG paper layout (has clearest geo annotations near FDT labels)
- Apply Helmert, verify FDT-011 and FDT-012 land at (5.468867°N, 95.361535°E) within ±500m

### Phase 2: Label Binding + Config (P1, 45 min)

**Step 2.1: Rewrite aceh_main.json label_families**
- File: `experiment/config/aceh_main.json` (rename from aceh.json)
- Replace existing 3-family KLDYA patterns with 5-family ACEH patterns:
  ```json
  "label_families": [
    {"family": "fat_short", "pattern": "^[A-Z]\\d{2}$", "target_fc": "BOITE"},
    {"family": "fat_code",  "pattern": "^FAT\\.[A-Z]\\d{2}$", "target_fc": "BOITE"},
    {"family": "pole",      "pattern": "^(EXT\\.)?MR\\.(KLDYA|XXX|IJY\\.KLDYA|MYD\\.S\\d+)\\.P\\d{3}(\\.HC)?$", "target_fc": "PTECH"},
    {"family": "fdt",       "pattern": "^KLDYA\\.\\d{3}$", "target_fc": "SITE"},
    {"family": "imb",       "pattern": "^\\d{1,4}$", "target_fc": "IMB"}
  ]
  ```
- Update `code_prefix` to include IMB: `"IMB": "IMB"`
- Keep existing `layer_pattern_map`, `negative_evidence_layers`, `tolerances`

**Step 2.2: Create aceh_sf.json**
- File: `experiment/config/aceh_sf.json` (NEW)
- Copy aceh_main.json as base, adjust:
  - Add SF-unique layers to layer_pattern_map: CLOSURE→BOITE, DROP DUCT→INFRASTRUCTURE, FIBER SPARE COIL→annotation
  - Add SF-unique negative_evidence: "TEXT INFO", "a"
  - Same label_families (SF uses same KLDYA patterns)
  - SF-specific georeference GCPs (may differ from main)

**Step 2.3: Update project_pipeline.py for separate projects**
- File: `experiment/python/project_pipeline.py`
- Support `--project aceh_main` and `--project aceh_sf` as separate invocations
- Each produces independent `output/<project>/<project>.gpkg`

### Phase 3: Noise Reduction + Styling (P2, 30 min)

**Step 3.1: Legend cluster exclusion**
- File: `experiment/config/legend_exclusions_aceh_main.json` (NEW)
- Review 3 detected clusters (LC-001: 1033 members, LC-002: 56, LC-003: 6)
- If user confirms via QGIS visual check → add to confirmed_clusters
- **Dependency**: Requires CRS fix first (geo coordinates must be correct for QGIS overlay verification)

**Step 3.2: CABLE segment coloring QML**
- File: `experiment/output/aceh_main/qgis/styles/CABLE.qml` (generated by style_exporter)
- After conversion with correct labels, verify QML uses TYPE_CABLE for renderer categories
- If TYPE_CABLE field is empty (all synthetic), configure fallback coloring by segment length or degree

### Phase 4: Verification (30 min)

**Step 4.1: CRS verification**
```bash
# Check FDT control points in output GPKG
python3 -c "
from osgeo import ogr
gpkg = ogr.Open('output/aceh_main/aceh_main.gpkg')
site = gpkg.GetLayerByName('SITE')
site.ResetReading()
for feat in site:
    geom = feat.GetGeometryRef()
    print(f'{feat.GetField(\"CODE\")}: ({geom.GetX():.6f}, {geom.GetY():.6f})')
"
# Expected: KLDYA.011 ~ (95.361, 5.468), KLDYA.012 ~ (95.36x, 5.46x)
```

**Step 4.2: Label binding verification**
```bash
# Check annotation_assignment_candidates count (must be > 0)
python3 -c "
from osgeo import ogr
gpkg = ogr.Open('output/aceh_main/aceh_main.gpkg')
lyr = gpkg.GetLayerByName('annotation_assignment_candidates')
print(f'Candidates: {lyr.GetFeatureCount()}')
# Count BOITE features with non-synthetic CODEs
boite = gpkg.GetLayerByName('BOITE')
total = annotated = 0
boite.ResetReading()
for feat in boite:
    total += 1
    if feat.GetField('label_provenance') == 'annotation-assigned':
        annotated += 1
print(f'BOITE annotated: {annotated}/{total} ({100*annotated/total:.1f}%)')
"
# Expected: annotation_assignment_candidates > 0, BOITE ≥60% annotation-assigned
```

**Step 4.3: IMB label verification**
```bash
# Check IMB display_label non-empty rate
python3 -c "
from osgeo import ogr
gpkg = ogr.Open('output/aceh_main/aceh_main.gpkg')
imb = gpkg.GetLayerByName('IMB')
total = labeled = 0
imb.ResetReading()
for feat in imb:
    total += 1
    if feat.GetField('display_label'):
        labeled += 1
print(f'IMB labeled: {labeled}/{total} ({100*labeled/total:.1f}%)')
"
# Expected: ≥50% non-empty
```

**Step 4.4: Noise verification**
```bash
# Check Basic Map excluded from delivery FC layers
python3 -c "
from osgeo import ogr
gpkg = ogr.Open('output/aceh_main/aceh_main.gpkg')
da = gpkg.GetLayerByName('drop_accounting')
# drop_accounting schema: port, dwg_layer, count (verified on actual GPKG)
da.ResetReading()
basic_map_in_delivery = False
for feat in da:
    port = feat.GetField('port') or ''
    layer = feat.GetField('dwg_layer') or ''
    if layer == 'Basic Map' and port not in ('fc_misc', 'block_definition', 'paper_space'):
        basic_map_in_delivery = True
        print(f'FAIL: Basic Map in port={port} count={feat.GetField(\"count\")}')
        break
if not basic_map_in_delivery:
    print('OK: Basic Map not in any delivery FC port')
"
```

## Acceptance Criteria Summary

| Phase | ID | Criterion | Target |
|-------|----|-----------|--------|
| P0 | CRS-1 | FDT GCPs in Aceh region | 5.4-5.5°N, 95.3-95.4°E ±500m |
| P0 | CRS-2 | sum(geographic_outliers) across 8 delivery FC layers = 0 | qc_summary table: sum "geographic_outliers" rows for FC-named layers |
| P0 | CRS-3 | Georeference documented | georeference section in config |
| P1 | LBL-1 | `_LABEL_FAMILY_COMPILED` rebuilt after config override | `grep -n "_LABEL_FAMILY_COMPILED\[:" ftth_converter.py` shows rebuild call after `LABEL_FAMILIES =` in `_apply_project_config` |
| P1 | LBL-2 | annotation_assignment_candidates > 0 | GPKG layer count |
| P1 | LBL-3 | BOITE ≥60% annotation-assigned | field_provenance query |
| P1 | LBL-4 | IMB ≥50% non-empty display_label | IMB display_label count |
| P1 | LBL-5 | 5-family label config in aceh_main.json | Config file review |
| P2 | NOISE-1 | Basic Map excluded from delivery FC | drop_accounting check |
| P2 | NOISE-2 | Legend exclusions config written | legend_exclusions json populated |
| P2 | BOITE-1 | BOITE count ≤80 for aceh_main | GPKG feature count |
| P2 | CABLE-1 | CABLE QML segment coloring | .qgz visual check |

## Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| GCP extraction fails (no geo text in paper layout) | Medium | High — blocks CRS fix | Fallback: manually specify GCPs in config JSON |
| Helmert residual >500m (local grid has non-uniform scale) | Low | High — spatial placement wrong | Switch to full affine with 3+ GCPs or use manual georeferencing in QGIS |
| IMB labels fail to bind (spatial distance between Home Number text and IMB point > tolerance) | Medium | Medium — IMB AC fails | Increase annotation_link_tol from 15m to 50m for IMB family |
| FAT short codes ("A01") collide across FDT clusters | Low | Medium — duplicate BOITE labels | Accept duplicates; synthetic fallback for collisions per existing dedup logic |
| aceh_sf has too few features (213) for meaningful topology | High | Low — SF project conversion is sparse | Accept low feature counts; document as expected |

## Verification Steps

1. `git diff --stat` shows only targeted files changed
2. All verification commands in Phase 4 pass
3. QGIS visual: GPKG overlaid on Tianditu shows features in Aceh, Indonesia (~15 min)
4. QGIS visual: BOITE layer shows correct labels (e.g., "A01" not "PBO0001")
5. QGIS visual: IMB layer shows house numbers (e.g., "44", "1") as labels
6. QGIS visual: CABLE layer colored by TYPE_CABLE segments
7. Total QGIS manual verification: budget 30 min

## Rollback Recovery (per phase)

| Phase | Rollback | Rerun command |
|-------|----------|---------------|
| 0 | `git checkout -- experiment/python/ftth_converter.py` | `python3 -m python.ftth_converter ...` |
| 1 | Remove `georeference` section from config + delete `georeference.py` | Re-run from Phase 1.1 |
| 2 | `git checkout -- experiment/config/aceh_main.json` | Re-run from Step 2.1 |
| 3 | Delete `legend_exclusions_aceh_main.json` | Re-run from Step 3.1 |
| Full | `git checkout HEAD -- experiment/` + delete `output/aceh_main/`, `output/aceh_sf/` | Fresh `project_pipeline.py --project aceh_main` |

If Phase 1 Helmert produces coordinates >500m off: adjust GCP pairs in config, re-run conversion (skip Phase 0+2, which are git-committed config changes).

---

## Design Constraints (from review feedback)

1. **Naming constraint**: `project_pipeline.py --project aceh_main` looks up `config/aceh_main.json` (line 254: `config/<project>.json`). The config filename MUST match the `--project` argument. Both `aceh_main.json` and `aceh_sf.json` follow this convention.
2. **source_crs after georeference**: After Helmert transform is applied to model-space coordinates, the resulting coordinates are in EPSG:4326 (WGS84 geographic). The config should set `source_crs: "EPSG:4326"` and `target_crs: "EPSG:3857"` — the CRS pipeline handles the 4326→3857 reprojection. The georeference module converts local grid → WGS84; CRS pipeline converts WGS84 → Web Mercator.
3. **Georeference hook location**: The `apply_georeference()` call is placed in `cad_common.py:init_crs()` as a conditional hook (not in `ftth_converter.py:main()`). If the project config has `georeference.gcp_pairs`, `init_crs()` applies the Helmert transform to `_COORD_TRANSFORM_QUEUE` before setting up the CRS transformation. This keeps separation of concerns: georeference = CRS module responsibility, not converter orchestration.
4. **SF layer pattern specification** (Step 2.2): `(?i).*closure.*` for BOITE, `(?i).*drop\\s*duct.*` for INFRASTRUCTURE, `(?i).*fiber\\s*spare.*` as annotation — regex syntax, not bare strings.
5. **IMB false-binding fallback**: If `^\d{1,4}$` integer regex produces false matches (e.g., non-IMB TEXT entities also match), add `min_distance_m: 10` to the IMB label family to restrict the Hungarian gate's spatial tolerance. The Home Number TEXT layer is already constrained by `layer_pattern_map`.
6. **Internal project field**: aceh_main.json must have `"project": "aceh_main"` (not "aceh"). Same for aceh_sf.json.

---

## ADR (Architecture Decision Record)

### Decision
Use sequential phased implementation (Option A) with Phase 0 (code bug fix) executed first, georeference applied as a cad_common.py CRS-pipeline hook, and label binding fixes tested with automated ogr verification scripts keyed to the actual GPKG schema.

### Drivers
- CRS misplacement blocks ALL spatial verification (Principle 3)
- Label binding requires functional `_LABEL_FAMILY_COMPILED` before GCP extraction can associate FDT labels with model-space features (Architect R2)
- User confirmed all 6 issues in this round (Q3)

### Alternatives considered
- **Option B (batch)**: Rejected — CRS errors contaminate Hungarian distances (in ground meters), making label assignments unverifiable
- **Option C (CRS only)**: Rejected — violates user-confirmed scope (all 6 issues)

### Why chosen
Sequential with Phase 0 first resolves the chicken-and-egg dependency: the compiled label list must be fixed BEFORE GCPs can be extracted and validated (FDT labels need functional `_match_label_family`). Georeference then unblocks spatial QC, enabling label binding verification in correct geographic context.

### Consequences
- 3-4 conversion runs required (vs 1 for batch)
- Each phase independently rollback-safe via git checkout
- Georeference hook in cad_common.py keeps converter core untouched (Principle 1)
- Config drift risk between aceh_main.json and aceh_sf.json (mitigated by shared label_families review)

### Follow-ups
- Extract `_LABEL_FAMILY_COMPILED` compilation to a proper function (Tech debt: inline comprehension at ftth_converter.py:273-280)
- DMS coordinate format support in layout_miner.py geo annotation extraction
- Shared aceh_common.json base config with `dict | other_dict` merge in `_apply_project_config` (deferred — label regexes for 2 projects are manageable manually)
- CABLE topology iterative correction when user priorities shift

---

## Changelog

- 2026-07-22 r2: Applied Architect (R1-R4) + Critic (C1-C8) feedback. CRITICAL fixes: Step 0.1 code block, Step 4.4 verification script. MAJOR: recovery section, GCP proximity threshold, CRS-2 scope. Added ADR + Design Constraints.
- 2026-07-22 r1: Initial plan created from deep-dive spec. Status: pending Architect + Critic review.
