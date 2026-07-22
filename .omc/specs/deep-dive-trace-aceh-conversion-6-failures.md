# Deep Dive Trace: aceh-conversion-6-failures

## Observed Result
ACEH conversion produced 6 failure modes: (1) excessive noise/legacy features in output, (2) all features at Atlantic Ocean (0°N,0°E) instead of Aceh Indonesia, (3) CABLE layer without segment-based coloring, (4) BOITE wrong point positions + mismatched labels, (5) IMB display labels empty, (6) CABLE topology errors near dense PTECH clusters. Two ACEH DWGs were processed as main+supplement instead of independent projects.

## Ranked Hypotheses (Cross-Lane Synthesis)

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads | Source Lane |
|------|------------|------------|-------------------|--------------|-------------|
| 1 | CRS pipeline: ACEH local engineering grid treated as EPSG:3857 → identity pass-through → Atlantic Ocean | **High** | **Strong** — gpkg coords identical to raw DWG values, paper layout has true geo coords | Directly explains the single most visible failure; unblocks all spatial QC | Lane 2 |
| 2 | `_LABEL_FAMILY_COMPILED` stale cache: compiled at module load from DMPH defaults, never rebuilt after aceh.json KLDYA override → 0 annotation assignments | **High** | **Strong** — code line ordering unambiguous, output shows 0 candidates | Explains 100% synthetic CODEs, IMB empty labels, BOITE wrong labels | Lane 1 |
| 3 | Config pattern mismatch: aceh.json regexes (KLDYA.011.A01) don't match actual label text (A01, MR.XXX.P001.HC) | **High** | **Strong** — 0 of 98 FAT texts match, POLE variants fail, no IMB family exists | After fixing Lane 1's stall, labels still won't bind without correct patterns | Lane 3 |
| 4 | Two DWGs are independent projects, processed together causing feature overlap | **Medium** | **Moderate** — coordinate ranges identical, SF has unique layers | Explains noise; POLE/FDT/FAT layers in both DWGs risk duplication | Lane 3 |
| 5 | FAT two-level labeling (cluster ID on FDT, sub-label on FAT) exceeds converter's flat label model | **Medium** | **Weak** — structural observation from text analysis | Secondary cause for BOITE label mismatch beyond simple regex fix | Lane 3 |

## Evidence Summary by Hypothesis

### Hypothesis 1 — CRS local grid misidentification
- ACEH model coords: X≈1060-24322, Y≈-9832 to -6451 (span ~23km × 3.4km)
- aceh.json declares source_crs=EPSG:3857, target_crs=EPSG:3857 → identity transform
- Paper APD layout contains true geographic coords: "5.468867°, 95.361535°" (FDT-011/KLDYA.011)
- In EPSG:3857, Aceh should be at X≈10,618,660 Y≈609,260 — the local coords (X≈2000) map to Gulf of Guinea
- Hutabohu worked because its DWG was authored in EPSG:3857 (X≈13,680,000 range)
- Fix path: georeference affine transform from GCPs (paper layout annotations → model space features)

### Hypothesis 2 — _LABEL_FAMILY_COMPILED stall
- ftth_converter.py:273-280 builds `_LABEL_FAMILY_COMPILED` at module load from `LABEL_FAMILIES`
- Module-load `LABEL_FAMILIES` = schema_config DMPH defaults (Hutabohu)
- _apply_project_config at line 2089 correctly replaces `LABEL_FAMILIES` with aceh.json KLDYA patterns
- But `_LABEL_FAMILY_COMPILED` is never rebuilt — DMPH regexes used forever
- _match_label_family at line 292 iterates stale DMPH cache → no ACEH text ever fullmatches
- Output: annotation_assignment_candidates = 0 features

### Hypothesis 3 — Config regex mismatch
- FAT pattern `^KLDYA\.\d{3}\.[A-Z]\d{2}$` matches 0 of 98 FAT layer texts (all are "A01" style)
- POLE pattern `^MR\.KLDYA\.P\d{3}$` fails for EXT.MR.IJY.KLDYA.P002, MR.XXX.P001.HC variants
- FDT pattern `^KLDYA\.\d{3}$` DOES match — the only working family
- No IMB label_family exists at all → 849 IMB features get synthetic-only codes
- 2,283 unique Home Number texts ("1", "44", "002") never reach Hungarian assignment

### Hypothesis 4 — Independent projects
- ACEH main: 3,283 Model entities, layers include FAT, FDT, POLE ID, NEW POLE, SLING WIRE
- ACEH SF: 213 Model entities, unique layers: CLOSURE, DROP DUCT, FIBER SPARE COIL
- Paper layouts differ: main has APD+SALES sheets, SF has APD-SF only
- Same coordinate extent but different project scope → should be separate GPKGs

### Hypothesis 5 — Two-level FAT labeling
- FAT layer has short codes "A01" (sub-position within FAT cluster)
- FAT CODE layer has compound codes "FAT.A01" (layer-qualified)
- FDT INSERT attributes carry cluster capacity (FAT=16)
- True FAT identity requires composing: FDT cluster ID + FAT sub-label
- Converter's flat label→feature model: one annotation → one feature CODE

## Evidence Against / Missing Evidence

### Against Hypothesis 1
- Two GCPs may be insufficient to determine if local grid is a standard CRS with custom false easting/northing vs. arbitrary engineering grid
- Could be a known Indonesian local CRS definable via PROJ string

### Against Hypothesis 2
- Even if fixed, Lane 3's regex mismatch means labels still won't bind — the stall fix alone is necessary but insufficient

### Against Hypothesis 3
- FDT pattern already works — proves the mechanism CAN work with correct regexes
- `_generic` fallback path provides partial nearest-neighbor linking (but no CODE assignment)

### Against Hypothesis 4
- If converter processes DWGs independently per-invocation, no overlap — only a deployment concern

### Against Hypothesis 5
- May not be needed if FAT CODE layer labels are used directly with correct regex

## Per-Lane Critical Unknowns

- **Lane 1 (Code stall)**: After fixing the `_LABEL_FAMILY_COMPILED` rebuild, does the Hungarian family gate actually engage for ACEH? (i.e., does the gate mechanism work correctly once the cache is fixed?)
- **Lane 2 (CRS)**: Can the ACEH local grid be defined as a standard PROJ/WKT string, or does it require an explicit georeference affine transform from GCPs?
- **Lane 3 (Assumption mismatch)**: What is the correct label composition strategy for FAT features — use FAT CODE layer compound labels directly, or compose FDT cluster ID + FAT sub-label?

## Lane 3 Misplacement / SoT Ownership Scope

N/A — No MOVE/SoT candidates identified. This is a configuration and georeference problem, not a system-of-truth placement problem.

## Rebuttal Round

**Best rebuttal to Lane 1 (stale cache as leader):** Fixing the stall won't help if Lane 3's regex patterns are also wrong. The stall fix is necessary but not sufficient — both must be fixed together. The CRS (Lane 2) is independently blocking because wrong coordinates prevent any spatial verification of the other fixes.

**Best rebuttal to Lane 2 (CRS as leader):** CRS is a global offset — it doesn't explain per-FC quality issues (BOITE wrong labels, IMB empty, CABLE topology). Those are Lane 1+3 issues. CRS first, then label fixes.

**Why Lane 2 leads:** Geographic placement is the single most visible failure AND blocks spatial quality verification. All other fixes are unverifiable until features land in the correct location. CRS is a pre-condition, not one of several equivalent issues.

## Convergence / Separation Notes

- **Lanes 1+3 converge**: Both point to label binding getting zero results. Lane 1 is the mechanism bug (stale cache); Lane 3 is the data mismatch (wrong patterns). Fixing both together restores annotation assignment.
- **Lane 2 is independent**: CRS misplacement is a separate pipeline stage. Fixing it doesn't affect labels, and fixing labels doesn't affect CRS.
- **Lane 4 (two projects) is a deployment concern**: Requires separate pipeline invocations, not code changes.
- **Lane 5 (two-level FAT) is a secondary concern**: May be resolved by using FAT CODE layer instead of FAT layer for labels.

## Most Likely Explanation

The ACEH conversion failures have TWO independent root causes, both requiring fixes:

**A. CRS misidentification (Lane 2):** The ACEH DWG uses a local engineering coordinate system with arbitrary origin (~23km × 3.4km extent). The aceh.json config declares source_crs=EPSG:3857, causing an identity transform that places all features at the Web Mercator origin (Atlantic Ocean). True geographic coordinates exist as text annotations in paper-space layouts but are not mined by the converter. Fix: georeference pre-processing using GCPs extracted from paper layout annotations.

**B. Label binding cascade (Lanes 1+3):** Two sequential bugs prevent annotation-to-feature label assignment: (1) `_LABEL_FAMILY_COMPILED` is stale — compiled from Hutabohu DMPH defaults at module load, never rebuilt after aceh.json KLDYA patterns replace `LABEL_FAMILIES` at runtime. (2) Even after fixing the stall, aceh.json regexes don't match ACEH's actual label text (short form "A01" for FAT, EXT/HC-suffixed variants for POLE, no IMB family at all). Both must be fixed for any label binding to work.

## Critical Unknown

**Whether the ACEH local engineering grid is definable via a standard PROJ string** (allowing config-only CRS fix) or **requires explicit georeference affine transformation** from control points (requiring a new georeference pre-processing step in the pipeline).

## Recommended Discriminating Probe

1. **Immediate (30 min):** Extract the two FDT ground control points from SF DWG paper layout annotations (5.468081°N,95.368299°E → model-space FDT feature) and attempt to fit to common Indonesian CRS codes (UTM 46N/47N, DGN95 / SRGI2013 zones). If no fit, compute Helmert similarity transform from 2 GCPs and verify on known features. This discriminates "standard CRS exists" from "local arbitrary grid."
2. **Follow-up (30 min):** Fix `_LABEL_FAMILY_COMPILED` rebuild + correct aceh.json regexes + add IMB label family. Run conversion. Check annotation_assignment_candidates count.
