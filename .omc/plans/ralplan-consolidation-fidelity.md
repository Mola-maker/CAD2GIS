# RALPLAN-DR: converter.py Consolidation with Fidelity Guarantee

**Status:** `pending approval` (consensus reached — Architect APPROVED, all 12 Critic issues resolved in revision 2)
**Plan saved to:** `/home/cat/projects/CAD2GIS/.omc/plans/ralplan-consolidation-fidelity.md`
**Date:** 2026-07-19 (revised 2026-07-19 per Architect/Critic feedback, iteration 2)
**Mode:** DELIBERATE (high-risk: baseline regression must be prevented)
**Source spec:** `/home/cat/projects/CAD2GIS/.omc/specs/deep-interview-consolidation-fidelity.md`

---

## 1. RALPLAN-DR Summary

### Principles (3-5)

1. **Fidelity-First** — Every refactoring step preserves pixel-identical output. The Hutabohu baseline (20/20 layers, CONV-SUM=6942) is the immovable constraint. No new features, no algorithm changes, no optimization.

2. **Domain Separation** — `cad_common.py` contains ZERO references to FTTH vocabulary (BOITE, CABLE, PTECH, FAT, FDT, DMPH, NRO, PM, ZNRO, IMB, SITE_TYPE, TYPE_BOX, etc.). Conversely, `ftth_converter.py` contains ZERO DWG type-code literal definitions (everything through `cad_common.DWG_TYPE_LINE` etc.).

3. **Preserve Import Topology** — The dependency graph must remain acyclic: `cad_common` has no internal dependencies on FTTH modules; `ftth_converter` depends on `cad_common` + `schema_config` + `domain_vocab`; `topology_repair` and `style_exporter` remain independent; `convert_all` orchestrates without creating cycles.

4. **Dual-Mode Execution** — Each core script supports both `python3 script.py --arg` (standalone) and `from python.script import *` (library) via try/except ImportError on relative imports.

### Decision Drivers (top 3)

| Rank | Driver | Why It Matters |
|------|--------|---------------|
| 1 | Baseline regression risk | A single missing entity in Hutabohu output (CONV-SUM != 6942) invalidates the entire refactoring |
| 2 | Domain code isolation | The spec's "ZERO FTTH symbols in cad_common" constraint determines every line-assignment decision |
| 3 | Standalone CLI usability | Batch users need `python3 ftth_converter.py`; library users need `import python.ftth_converter` |

### Viable Options

#### Option A: Shared Base Library + Independent Entry Scripts + Thin Orchestrator (SELECTED)

- `cad_common.py` (~650 lines) extracts all L1-L2 functions as pure library (no CLI)
- `ftth_converter.py` (~2,850 lines) imports cad_common, contains the converter CLI
- Original `converter.py` becomes a backward-compat re-export shim
- `topology_repair.py` = renamed `topology_builder.py`
- `style_exporter.py` = renamed `style_builder.py`
- `convert_all.py` = new thin orchestrator

**Pros:**
- Clean domain separation enforced by module boundary
- Standalone `python3 ftth_converter.py` works with try/except import
- Original `converter.py` kept as shim for scripts that import from it
- Each component independently testable and runnable

**Cons:**
- Refactoring risk: moving 650+ lines between files can introduce subtle bugs
- Two copies of small utility functions briefly exist during transition
- `read_dwg()` (~505 lines) stays in ftth_converter but must use `cad_common.` prefixed calls for all moved functions
- Cross-module mutation of globals (DWG_TYPE_*, DIMENSION_TYPE_UNION, CONTROL_TYPES) requires explicit patching specification

#### Option B: Minimal Rename-Only (REJECTED)

Only rename `topology_builder.py` -> `topology_repair.py` and `style_builder.py` -> `style_exporter.py`. Leave converter.py intact.

**Why rejected:** Fails the spec's core requirement (splitting converter.py into cad_common + ftth_converter). Converter.py remains 3,510-line monolith. Domain isolation never achieved.

#### Option C: Full Rewrite as Separate Modules with `dwglib/` Subpackage (REJECTED)

Extract L1-L2 functions into a `dwglib/` subpackage with 3-4 files.

**Why rejected:** Over-engineered for ~650 lines of shared code. Spec explicitly requests `cad_common.py` (single file), not a subpackage. Higher refactoring risk from more file boundaries.

**Verdict:** Option A selected. Options B and C are explicitly invalidated.

---

## 2. ADR (Architecture Decision Record)

**Decision:** Split converter.py into `cad_common.py` (L1-L2, no CLI, zero FTTH vocabulary) + `ftth_converter.py` (L3-L5, with CLI, imports cad_common), keeping `converter.py` as a transitional re-export shim. Rename topology_builder -> topology_repair and style_builder -> style_exporter. Add `convert_all.py` thin orchestrator. Introduce `cad_common.init_crs()` as the explicit CRS initialization entry point to replace fragile global-attribute mutation.

**Drivers:** Fidelity-first constraint (CONV-SUM=6942 must be preserved), domain symbol isolation (cad_common has ZERO FTTH vocabulary), dual-mode execution (standalone + package import).

**Alternatives considered:** Option B (rename-only, rejected: fails spec). Option C (dwglib/ subpackage, rejected: over-engineered for ~650 lines).

**Why chosen:** Option A achieves all spec goals with the least structural change. The key risk (moved code breaking the baseline) is mitigated by the verification steps below, the function dependency ordering table, and the explicit cross-module mutation contract.

**Consequences:**
- cad_common.py becomes a project-agnostic DWG parsing library reusable for non-FTTH CAD work
- ftth_converter.py is still ~2,850 lines (the `read_dwg()` orchestrator is inherently complex)
- converter.py shim creates a transient period where both old and new imports work
- All existing import paths continue to work during transition
- Cross-module global mutation is explicit and documented (DWG_TYPE_*, DIMENSION_TYPE_UNION, CONTROL_TYPES patched by read_dwg)

**Follow-ups:**
1. After verification, remove converter.py shim and update all remaining imports to use ftth_converter/cad_common directly
2. Consider extracting the `read_dwg()` orchestrator function into smaller stage functions if future work demands it
3. Add unit tests for cad_common functions (currently untested in isolation)
4. Once cad_common stabilizes, consider publishing it as a standalone `dwg-common` package

---

## 3. Pre-Mortem (3 Failure Scenarios)

### Scenario 1: TELECOM_ANNOTATION_KEYWORDS Leaks FTTH Terms into cad_common

**How it happens:** TELECOM_ANNOTATION_KEYWORDS (converter.py:240-247) contains `"nro"` and `"pm"` — domain terms explicitly forbidden by Principle #2. If accidentally copied to cad_common, grep verification will catch it, but only if run.

**Detection:** `grep -i "nro\|pm\|boite\|cable\|ptech\|fat\|fdt\|dmph\|znro\|zpm\|imb\|site_type\|type_box" cad_common.py` returns hits on "nro" and "pm" inside TELECOM_ANNOTATION_KEYWORDS.

**Mitigation:** TELECOM_ANNOTATION_KEYWORDS stays in ftth_converter (Step 3). It is the L3 function `_classify_entity_tier2()` that consumes it, and that function stays in ftth_converter. The plan explicitly excludes TELECOM_ANNOTATION_KEYWORDS from cad_common.

### Scenario 2: Moved Code Missing Ctypes/LibreDWG State

**How it happens:** `_init_libredwg()` sets global `_libdwg` and `_libc` ctypes handles. `_lwpoline_points()` and `_entity_utf8_text()` depend on these globals. When these functions are moved to cad_common.py, the globals must be in cad_common's module namespace, not converter's.

**Detection:** `_lwpoline_points()` raises AttributeError on `_libdwg.dwg_ent_lwpline_get_numpoints` because `_libdwg` is None (never initialized).

**Mitigation:** Move the global `_libdwg` / `_libc` declarations AND `_init_libredwg()` together into cad_common. All functions that use them must also be in cad_common. The dependency ordering table (Section 5) enforces this: `_init_libredwg` is a leaf extracted first.

### Scenario 3: CRS Functions Called Before init_crs()

**How it happens:** ftth_converter's `main()` imports cad_common but some code path or constructor calls `_reproject_point()` or `_valid_coord()` before `cad_common.init_crs()` is called. The CRS transform globals are at their default (identity/EPSG:3857), producing silently wrong coordinates.

**Detection:** Coordinates come out in wrong CRS (e.g., web-mercator meters interpreted as degrees). Subtle and data-dependent — may not crash but corrupts output.

**Mitigation:** `cad_common.init_crs()` sets a `_CRS_INITIALIZED = True` flag. Every CRS-dependent function checks this flag at entry and raises `RuntimeError("CRS not initialized. Call cad_common.init_crs(source, target) before using geometry functions.")` if False. This turns a silent data corruption into a loud, immediate failure.

---

## 4. Expanded Test Plan

### Unit Tests
- `cad_common.py`: Verify all functions import successfully with zero FTTH symbols
  - `python3 -c "import cad_common; print(dir(cad_common))"` -- confirm all expected symbols present
  - `grep -i "boite\|cable\|ptech\|fat\|fdt\|dmph\|znro\|zpm\|nro\|pm\|imb\|site_type\|type_box" cad_common.py` -- must return ZERO results (NOTE: word-boundary grep -- `\bnro\b` and `\bpm\b` would be more precise, but the full regex covers substring hits which are conservative and safer)
  - `python3 -c "import cad_common; assert 'BOITE' not in dir(cad_common); assert 'CABLE' not in dir(cad_common)"`
- `cad_common.py`: Verify init_crs guard works
  - `python3 -c "from cad_common import _reproject_point; _reproject_point(0, 0)"` -- must raise RuntimeError about CRS not initialized
  - `python3 -c "from cad_common import init_crs; init_crs('EPSG:3857', 'EPSG:4326'); from cad_common import _reproject_point; print(_reproject_point(0, 0))"` -- must succeed
- `cad_common.py`: Verify aci_to_rgb returns correct values
  - `python3 -c "from cad_common import aci_to_rgb; assert aci_to_rgb(1) == '#FF0000'; assert aci_to_rgb(7) == '#000000'; assert aci_to_rgb(999) == '#404040'"`
- `cad_common.py`: Verify `_lwpoline_points()`, `_extract_wkt()`, `_parse_dwg_color()` work on sample DWG data
- `convert_all.py`: Verify `python3 convert_all.py --help` parses correctly, all sub-stage --skip flags work

### Integration Tests
- `ftth_converter.py` standalone: `python3 ftth_converter.py --config /home/cat/projects/CAD2GIS/experiment/config/hutabohu.json --input <dwg> --output /tmp/test_standalone.gpkg`
- `python3 topology_repair.py --gpkg /tmp/test_standalone.gpkg --snap-tol 5.0`
- `python3 style_exporter.py --gpkg /tmp/test_standalone.gpkg`
- `python3 convert_all.py --config /home/cat/projects/CAD2GIS/experiment/config/hutabohu.json --input <dwg> --output /tmp/test_orch.gpkg`
- Dual-mode import test for each new module:
  ```bash
  # Standalone
  cd /home/cat/projects/CAD2GIS/experiment/python
  python3 -c "import cad_common; print('standalone OK')"
  # Package
  cd /home/cat/projects/CAD2GIS/experiment
  python3 -c "from python import cad_common; print('package OK')"
  ```

### End-to-End Regression (THE CRITICAL TEST)
- Run full pipeline via `convert_all.py` on the Hutabohu DWG
- Compare output GPKG against the existing baseline GPKG
- Verify ALL of:
  - 20/20 layers match (layer list identical)
  - Per-layer feature counts match baseline exactly:
    - CABLE=166, PTECH=167, BOITE=45, INFRASTRUCTURE=0, SITE=count, ZNRO=0, ZPM=count, IMB=count
  - CONV-SUM = 6942 (conservation ledger sum)
  - `fc_misc` count matches baseline
  - Per-layer discard accounting matches baseline
  - SHA256 of output GPKG may differ (metadata timestamps) but layer geometries must be identical

### Observability
- Each stage must log start/end timestamps and feature counts to stdout
- `convert_all.py` must propagate exit codes: any stage failure -> non-zero exit
- Conservation ledger table (`_conservation`) must be written to output GPKG
- All warnings must go to stderr; normal output to stdout

---

## 5. Implementation Steps

### Step 0: Complete Function-to-Module Assignment Table and Dependency Order

Before any code movement, the executor must internalize this table. It is the single source of truth for every symbol's destination module.

#### 5.0.1 Complete Symbol Assignment Table

| Symbol / Function | converter.py lines | Module | Notes |
|---|---|---|---|
| Module docstring + standard imports | 1-32 (adapted) | cad_common | ctypes, hashlib, json, math, os, re, sys, tempfile, time, collections, pathlib |
| OGR/GDAL imports | 120-128 | cad_common | `from osgeo import ogr, osr` |
| `_libdwg`, `_libc` globals | 62-63 | cad_common | Must precede `_init_libredwg` |
| `_init_libredwg()` | 66-98 | cad_common | Leaf: depends only on ctypes |
| `_entity_utf8_text()` | 101-117 | cad_common | Depends on `_libdwg`, `_libc`, `_init_libredwg` |
| `REGION_BOUNDS_WGS84` | 135 | cad_common | Constant tuple |
| `WEBMERC_MAX_X`, `WEBMERC_MAX_Y` | 138-139 | cad_common | Constants |
| `_CRS_TRANSFORM`, `_TO_WGS84` | 142-143 | cad_common | Mutable globals, set by `init_crs()` |
| `_SOURCE_IS_GEOGRAPHIC` | 144 | cad_common | Set by `init_crs()` |
| `_TARGET_IS_GEOGRAPHIC` | 145 | cad_common | Set by `init_crs()` |
| `_TARGET_IS_WEBMERC` | 146 | cad_common | Set by `init_crs()` |
| `_SOURCE_CRS_LABEL` | 147 | cad_common | Set by `init_crs()` |
| `_TARGET_CRS_LABEL` | 148 | cad_common | Set by `init_crs()` |
| `_CRS_INITIALIZED` | -- | cad_common | **NEW**: CRS init guard flag (default False) |
| `init_crs(src, tgt)` | -- | cad_common | **NEW**: explicit CRS init function (see 5.1.1) |
| `_reproject_point()` | 151-160 | cad_common | Guard: checks `_CRS_INITIALIZED` |
| `_to_wgs84()` | 163-171 | cad_common | Guard: checks `_CRS_INITIALIZED` |
| `_valid_coord()` | 174-180 | cad_common | Guard: checks `_CRS_INITIALIZED` |
| `_in_region_bounds()` | 183-187 | cad_common | Guard: checks `_CRS_INITIALIZED` |
| `_meters_to_units()` | 190-194 | cad_common | Guard: checks `_CRS_INITIALIZED` |
| `_line_length_m()` | 197-207 | cad_common | Guard: checks `_CRS_INITIALIZED` |
| `DWG_TYPE_LINE` through `DWG_TYPE_ATTRIB` | 209-218 | cad_common | **Mutable globals**: patched by read_dwg() in ftth_converter |
| `DWG_SUPERTYPE_ENTITY` | 219 | cad_common | **Mutable global**: patched by read_dwg() in ftth_converter |
| `DIMENSION_TYPE_UNION` | 223 | cad_common | **Mutable global dict**: patched by read_dwg() in ftth_converter |
| `CONTROL_TYPES` | 227 | cad_common | **Mutable global set**: patched by read_dwg() in ftth_converter |
| `DEFAULT_FRAGMENT_CLUSTER_TOL_M` | 230 | cad_common | Constant |
| `DEFAULT_ANNOTATION_LINK_TOL_M` | 232 | cad_common | Constant |
| `DEFAULT_BOITE_FUSION_TOL_M` | 234 | cad_common | Constant |
| `METERS_PER_DEGREE` | 237 | cad_common | Constant |
| `_cstr()` | 271-276 | cad_common | Leaf: depends on builtins only |
| `_layer_name()` | 284-288 | cad_common | Depends on `_init_libredwg`, `_libdwg`, `_cstr` |
| `_parse_dwg_color()` | 291-311 | cad_common | Leaf: pure numeric logic |
| `_resolve_effective_color()` | 314-333 | cad_common | Depends on `aci_to_rgb` |
| `_lwpoline_points()` | 336-357 | cad_common | Depends on `_libdwg`, `_libc`, `_init_libredwg` |
| `_adaptive_chord_tolerance()` | 360-366 | cad_common | Depends on `_SOURCE_IS_GEOGRAPHIC` (CRS guard) |
| `_haversine()` | 369-377 | cad_common | Leaf: pure math |
| `_haversine_length()` | 380-388 | cad_common | Depends on `_haversine` |
| `_centroid()` | 391-397 | cad_common | Leaf: pure math |
| `_sha256_file()` | 400-406 | cad_common | Leaf: hashlib only |
| `_safe_layer_name()` | 409-415 | cad_common | Leaf: builtins only |
| `_wkt_point()` | 420-421 | cad_common | Leaf |
| `_wkt_linestring()` | 424-426 | cad_common | Leaf |
| `_wkt_polygon_exterior()` | 429-431 | cad_common | Leaf |
| `_circle_points()` | 434-442 | cad_common | Leaf: pure math |
| `_arc_points()` | 445-457 | cad_common | Leaf: pure math |
| `_extract_wkt()` | 460-524 | cad_common | Depends on DWG_TYPE_* constants, `_adaptive_chord_tolerance`, `_lwpoline_points`, all WKT/geometry helpers |
| `_extract_dimension()` | 529-547 | cad_common | Leaf: pure ctypes access (union_name passed as param) |
| `_cluster_points()` | 549-597 | cad_common | Leaf: pure algorithm (grid + union-find); line 549 is the section comment, def starts at 552 |
| `_hsv_bytes()` | schema_config:2638-2646 | cad_common | Leaf: pure color math. Copied from schema_config. |
| `_generate_aci_table()` | schema_config:2649-2669 | cad_common | Depends on `_hsv_bytes`. Copied from schema_config. |
| `ACI_TO_RGB` dict | schema_config:2672 | cad_common | Generated by `_generate_aci_table()`. Copied from schema_config. |
| `DEFAULT_COLOR_RGB` | schema_config:2675 | cad_common | Constant `"#404040"`. Copied from schema_config. |
| `aci_to_rgb()` | schema_config:2678-2680 | cad_common | Depends on `ACI_TO_RGB`. Copied from schema_config. |
| | | | |
| `_PROJECT_CONFIG` | 59 | ftth_converter | Project config override global |
| Schema imports | 33-51 (adapted) | ftth_converter | From schema_config + domain_vocab |
| Evidence/legend imports | 55-56 | ftth_converter | evidence_ledger, legend_detector |
| `TELECOM_ANNOTATION_KEYWORDS` | 240-247 | ftth_converter | **CRITICAL**: Contains FTTH terms `"nro"`, `"pm"`. NOT in cad_common. |
| `ATTR_PATTERNS` | 249-266 | ftth_converter | Contains FTTH terms (`FDT`, `FAT`, `NRO`, `PM`, etc.) |
| `_fragment_aggregation_target()` | 600-606 | ftth_converter | Depends on `FRAGMENT_AGGREGATION_LAYERS` from schema_config |
| `_classify_entity_tier1()` | 611-629 | ftth_converter | L3: uses LAYER_PATTERN_MAP, NEGATIVE_EVIDENCE_LAYERS |
| `_classify_entity_tier2()` | 632-655 | ftth_converter | L3: uses TELECOM_ANNOTATION_KEYWORDS |
| `_assign_fc()` | 658-673 | ftth_converter | L3: two-tier classification |
| `_extract_attributes()` | 678-740 | ftth_converter | L3: uses ATTR_PATTERNS |
| `_LABEL_FAMILY_COMPILED` | 747-754 | ftth_converter | Compiled from LABEL_FAMILIES (schema_config) |
| `ANNOTATION_LEDGER` | 758-763 | ftth_converter | Global annotation evidence ledger |
| `_match_label_family()` | 766-774 | ftth_converter | Uses `_LABEL_FAMILY_COMPILED` |
| `_minimum_cost_assignment()` | 777-831 | ftth_converter | Pure Hungarian algorithm, but only consumed by `_assign_family_annotations` |
| `_assign_family_annotations()` | 833-917 | ftth_converter | Uses `_meters_to_units` (cad_common), `_minimum_cost_assignment` |
| `_link_annotations_generic()` | 920-943 | ftth_converter | L3 |
| `_link_annotations_to_geometries()` | 945-1050 | ftth_converter | L3 |
| `BOITE_FUSION_LEDGER` | 1059-1064 | ftth_converter | |
| `_REPRESENTATION_KINDS` | 1066-1071 | ftth_converter | |
| `_representation_kind()` | 1074-1075 | ftth_converter | |
| `_fuse_boite_representations()` | 1078-1180 | ftth_converter | Depends on `_cluster_points` (cad_common) |
| `read_dwg()` | 1185-1690 | ftth_converter | **L3-L5 orchestrator**: references cad_common. prefixed functions. Patches `cad_common.DWG_TYPE_*`, `cad_common.DIMENSION_TYPE_UNION`, `cad_common.CONTROL_TYPES`. Contains `sys.path.insert` (line 1200). |
| `_aggregate_fragments()` | 1693-1770 | ftth_converter | L3 |
| `FC_GEOM_RESOLVE` | 1774-1782 | ftth_converter | |
| `_resolve_fc_geometry()` | 1787-1822 | ftth_converter | L3 |
| `_ogr_field_type()` | 1827-1834 | ftth_converter | L3 |
| `_compute_layer_length()` | 1837-1841 | ftth_converter | L3 |
| `write_geopackage()` | 1844-2201 | ftth_converter | L4: uses schema_config layer configs |
| `_BOITE_STYLE_PALETTE` | 2206-2214 | ftth_converter | Module-level constant; QGIS style palette for BOITE layers |
| `_write_boite_styles()` | 2217-2272 | ftth_converter | L4 |
| `__main__` comment anchor | 2274 | ftth_converter | Section comment `# ── Main CLI ──`; not extractable code |
| `_LEDGER_EXCLUDED_PORTS` | 2282-2285 | ftth_converter | Module-level constant; ports excluded from conservation ledger |
| `_FIELD_PROVENANCE_RULES` | 2290-2297 | ftth_converter | Module-level constant; post-hoc provenance classification |
| `_PROVENANCE_METADATA_FIELDS` | 2298-2302 | ftth_converter | Module-level constant; metadata field names |
| `_entity_weight()` | 2305-2311 | ftth_converter | L4 |
| `_run_legend_detection()` | 2316-2397 | ftth_converter | L4; return at 2397, blank 2398, _build_conservation_entries starts 2400 |
| `_build_conservation_entries()` | 2400-2434 | ftth_converter | L4 |
| `_build_field_provenance()` | 2436-2470 | ftth_converter | L4 |
| `_write_evidence_stage()` | 2472-2512 | ftth_converter | L4 |
| `_load_project_config()` | 2514-2521 | ftth_converter | L5 |
| `_apply_project_config()` | 2524-2588 | ftth_converter | L5 |
| `main()` | 2590-3000 | ftth_converter | L5 CLI entry; calls `cad_common.init_crs()` instead of direct global mutation |
| `_append_topology_qc()` | 3003-3031 | ftth_converter | L5 |
| `_run_fdt_tagging()` | 3034-3100 | ftth_converter | L5 |
| `_assign_codes()` | 3125-3180 | ftth_converter | L4 |
| `_write_span_annotations()` | 3183-3303 | ftth_converter | L4 |
| `_snap_site_to_nearest_ptech()` | 3305-3346 | ftth_converter | L5 |
| `_write_boite_attrib_values()` | 3348-3397 | ftth_converter | L5 |
| `_create_fdt_boite_nodes()` | 3399-3507 | ftth_converter | L5 |
| `if __name__ == "__main__"` | 3509-3510 | ftth_converter | Entry point |

#### 5.0.2 Extraction Dependency Order (Leaf Functions First)

This is the order in which symbols must be added to `cad_common.py` to avoid forward-reference errors. Execute these micro-steps sequentially within Step 1:

```
Layer 0 (no cad_common internal deps):
  imports (ctypes, hashlib, json, math, os, re, sys, tempfile, time, collections, pathlib)
  from osgeo import ogr, osr
  constants: REGION_BOUNDS_WGS84, WEBMERC_MAX_X, WEBMERC_MAX_Y
  constants: METERS_PER_DEGREE, DEFAULT_*_TOL_M
  DWG_TYPE_* = 0, 19, 8, ... (mutable globals)
  DIMENSION_TYPE_UNION = {}
  CONTROL_TYPES = set()
  _CRS_TRANSFORM = None, _TO_WGS84 = None
  _SOURCE_IS_GEOGRAPHIC = False, _TARGET_IS_GEOGRAPHIC = False, _TARGET_IS_WEBMERC = True
  _SOURCE_CRS_LABEL = "EPSG:3857", _TARGET_CRS_LABEL = "EPSG:3857"
  _CRS_INITIALIZED = False   ← NEW

Layer 1 (depend on Layer 0 globals only):
  _libdwg = None, _libc = None
  _init_libredwg()
  _entity_utf8_text()

Layer 2 (depend on Layer 1 or are pure math):
  _cstr()
  _layer_name()                    ← depends on _init_libredwg, _libdwg, _cstr
  _parse_dwg_color()               ← pure
  _lwpoline_points()               ← depends on _init_libredwg, _libdwg, _libc
  _hsv_bytes()                     ← pure (copied from schema_config)
  _generate_aci_table()            ← depends on _hsv_bytes (copied from schema_config)
  ACI_TO_RGB = _generate_aci_table()  ← generated dict
  DEFAULT_COLOR_RGB = "#404040"
  aci_to_rgb()                     ← depends on ACI_TO_RGB
  init_crs()                       ← sets all CRS state globals
  _haversine()                     ← pure
  _haversine_length()              ← depends on _haversine
  _centroid()                      ← pure
  _sha256_file()                   ← pure
  _safe_layer_name()               ← pure
  _wkt_point()                     ← pure
  _wkt_linestring()                ← pure
  _wkt_polygon_exterior()          ← pure
  _circle_points()                 ← pure
  _arc_points()                    ← pure
  _adaptive_chord_tolerance()      ← depends on _SOURCE_IS_GEOGRAPHIC (CRS guard)
  _cluster_points()                ← pure

Layer 3 (depend on Layer 2 + CRS state):
  _reproject_point()               ← CRS guard
  _to_wgs84()                      ← CRS guard
  _valid_coord()                   ← CRS guard
  _in_region_bounds()              ← CRS guard
  _meters_to_units()               ← CRS guard
  _line_length_m()                 ← CRS guard
  _resolve_effective_color()       ← depends on aci_to_rgb

Layer 4 (aggregate: depend on multiple Layer 2/3 functions):
  _extract_dimension()             ← depends on ctypes, takes union_name as param
  _extract_wkt()                   ← depends on DWG_TYPE_*, _adaptive_chord_tolerance,
                                      _lwpoline_points, all WKT/geometry helpers
```

---

### Step 1: Create `cad_common.py` -- Extract L1-L2 Shared Library

**Goal:** Create `/home/cat/projects/CAD2GIS/experiment/python/cad_common.py` containing all L1-L2 functions from converter.py (DWG parsing, geometry, color, CRS) with zero FTTH domain symbols.

**Source files:**
- `/home/cat/projects/CAD2GIS/experiment/python/converter.py` (primary)
- `/home/cat/projects/CAD2GIS/experiment/python/schema_config.py` (aci_to_rgb chain only)

**Method:** Copy symbols from converter.py in the exact dependency order specified in Section 5.0.2. Do NOT copy the entire file -- extract symbol by symbol. Each symbol's line range is given in Section 5.0.1.

**Explicit EXCLUSIONS from cad_common (CRITICAL -- these go to ftth_converter, Step 3):**
- `TELECOM_ANNOTATION_KEYWORDS` (line 240-247) -- contains `"nro"`, `"pm"` (FTTH terms)
- `ATTR_PATTERNS` (line 249-266) -- contains `FDT`, `FAT`, `NRO`, `PM`, etc.
- `_fragment_aggregation_target()` (line 600-606) -- depends on FRAGMENT_AGGREGATION_LAYERS from schema_config
- `_minimum_cost_assignment()` (line 777-831) -- pure algorithm but only consumed by FTTH annotation logic; kept in ftth_converter to minimize cad_common surface

**aci_to_rgb chain:** Copy the FULL chain from schema_config.py into cad_common (Option A from reviewer feedback):
1. `_hsv_bytes()` (schema_config:2638-2646)
2. `_generate_aci_table()` (schema_config:2649-2669)
3. `ACI_TO_RGB = _generate_aci_table()` (schema_config:2672)
4. `DEFAULT_COLOR_RGB = "#404040"` (schema_config:2675)
5. `aci_to_rgb()` (schema_config:2678-2680)

This avoids a hardcoded 255-entry dict literal and keeps the color logic self-consistent. None of these functions reference FTTH vocabulary.

**cad_common.py characteristics:**
- No `if __name__ == "__main__"` block (library only, no CLI)
- No imports from schema_config, domain_vocab, evidence_ledger, legend_detector
- Docstring: "CAD Common Library -- project-agnostic DWG parsing, geometry, color, and CRS utilities"
- Contains `init_crs()` as the sole CRS initialization entry point

#### 5.1.1 `init_crs()` Specification

```python
# Module-level guard flag
_CRS_INITIALIZED = False

def init_crs(source_crs_label, target_crs_label):
    """
    Initialise the CRS pipeline. Must be called once before any CRS-dependent
    geometry function (_reproject_point, _to_wgs84, _valid_coord,
    _in_region_bounds, _meters_to_units, _line_length_m,
    _adaptive_chord_tolerance).

    Args:
        source_crs_label: Source DWG CRS (e.g. "EPSG:3857")
        target_crs_label: Output GeoPackage CRS (e.g. "EPSG:3857")

    Raises:
        RuntimeError: If source or target CRS string is invalid / unrecognised
                      by GDAL osr.SpatialReference.SetFromUserInput().
    """
    global _CRS_TRANSFORM, _TO_WGS84, _SOURCE_IS_GEOGRAPHIC, _TARGET_IS_GEOGRAPHIC
    global _TARGET_IS_WEBMERC, _SOURCE_CRS_LABEL, _TARGET_CRS_LABEL, _CRS_INITIALIZED

    _SOURCE_CRS_LABEL = source_crs_label
    _TARGET_CRS_LABEL = target_crs_label

    src = osr.SpatialReference()
    if src.SetFromUserInput(source_crs_label) != 0:
        raise RuntimeError(f"Invalid source CRS: {source_crs_label}")
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    dst = osr.SpatialReference()
    if dst.SetFromUserInput(target_crs_label) != 0:
        raise RuntimeError(f"Invalid target CRS: {target_crs_label}")
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    _SOURCE_IS_GEOGRAPHIC = bool(src.IsGeographic())
    _TARGET_IS_GEOGRAPHIC = bool(dst.IsGeographic())
    _TARGET_IS_WEBMERC = dst.GetAuthorityCode(None) == "3857"

    if src.IsSame(dst):
        _CRS_TRANSFORM = None
    else:
        _CRS_TRANSFORM = osr.CoordinateTransformation(src, dst)

    _TO_WGS84 = None if dst.IsSame(wgs84) else osr.CoordinateTransformation(dst, wgs84)

    _CRS_INITIALIZED = True
```

Every CRS-dependent function in cad_common must include a guard at entry:
```python
if not _CRS_INITIALIZED:
    raise RuntimeError(
        "CRS not initialized. Call cad_common.init_crs(source, target) "
        "before using geometry functions."
    )
```

Functions requiring this guard: `_reproject_point`, `_to_wgs84`, `_valid_coord`, `_in_region_bounds`, `_meters_to_units`, `_line_length_m`, `_adaptive_chord_tolerance`.

#### 5.1.2 Cross-Module Mutation Contract

Three globals in cad_common are **mutable and must be patched** by `read_dwg()` in ftth_converter:

| Global | Declared in | Patched by | How |
|--------|------------|------------|-----|
| `DWG_TYPE_LINE` through `DWG_TYPE_ATTRIB`, `DWG_SUPERTYPE_ENTITY` | cad_common (line ~209-219) | ftth_converter read_dwg() (line ~1220-1232) | Direct assignment: `cad_common.DWG_TYPE_LINE = L_LINE` |
| `DIMENSION_TYPE_UNION` | cad_common (empty dict `{}`) | ftth_converter read_dwg() (line ~1237-1243) | `.clear()` then `.update()` or direct key assignment |
| `CONTROL_TYPES` | cad_common (empty set `set()`) | ftth_converter read_dwg() (line ~1246-1250) | `.clear()` then `.add()` |

The patching code in `read_dwg()` must use `cad_common.` prefix:
```python
# In ftth_converter's read_dwg():
cad_common.DWG_TYPE_LINE = L_LINE
cad_common.DWG_TYPE_LWPOLYLINE = L_LWPOLYLINE
# ... all DWG_TYPE_* constants ...

cad_common.DIMENSION_TYPE_UNION.clear()
for nm in ("DIMENSION_ORDINATE", "DIMENSION_LINEAR", ...):
    val = getattr(LibreDWG, "DWG_TYPE_" + nm, None)
    if val is not None:
        cad_common.DIMENSION_TYPE_UNION[val] = nm

cad_common.CONTROL_TYPES.clear()
for nm in ("BLOCK", "ENDBLK", "SEQEND", "ATTDEF"):
    val = getattr(LibreDWG, "DWG_TYPE_" + nm, None)
    if val is not None:
        cad_common.CONTROL_TYPES.add(val)
```

**Critical constraint verification checklist:**
- [ ] `grep -i "boite\|cable\|ptech\|fat\|fdt\|dmph\|znro\|zpm\|nro\|pm\|imb\|site_type\|type_box" cad_common.py` returns ZERO results
- [ ] `python3 -c "import cad_common"` succeeds
- [ ] `cad_common.py` has no CLI (`if __name__ == "__main__"`)
- [ ] All docstrings adapted to remove "GeoFormer" references (use "CAD Common Library")
- [ ] `aci_to_rgb`, `_hsv_bytes`, `_generate_aci_table` are self-contained (no import from schema_config)

**Acceptance criteria:**
1. `cad_common.py` imports successfully as standalone: `python3 -c "import cad_common; print('OK')"`
2. Zero FTTH domain vocabulary in the file (verified by grep with the exact keyword list above)
3. All functions listed in Section 5.0.1 as "cad_common" are present and callable
4. `aci_to_rgb` lookup table is self-contained (no import from schema_config needed)
5. `_parse_dwg_color()` and `_resolve_effective_color()` work correctly with `aci_to_rgb`
6. Calling `_reproject_point(0,0)` before `init_crs()` raises RuntimeError
7. Calling `_reproject_point(0,0)` after `init_crs("EPSG:3857", "EPSG:3857")` returns `(0, 0)` unchanged

---

### Step 2: Rename topology_builder -> topology_repair and style_builder -> style_exporter

**Goal:** Rename two files with clean git history. Update all import references.

**Files to rename:**
| From | To |
|------|----|
| `/home/cat/projects/CAD2GIS/experiment/python/topology_builder.py` | `topology_repair.py` |
| `/home/cat/projects/CAD2GIS/experiment/python/style_builder.py` | `style_exporter.py` |

**Process:**
1. `cp topology_builder.py topology_repair.py`
2. `cp style_builder.py style_exporter.py`
3. Update module docstrings in new files (name reference)
4. Update `converter.py` lines that reference `topology_builder`: `from . import topology_builder` -> `from . import topology_repair as topology_builder` (alias approach for safety during transition)
5. Update `__init__.py` docstring to reference new names
6. Verify old files still exist (for backward compat during transition)
7. After full verification, `git rm` old files

**Acceptance criteria:**
1. `python3 topology_repair.py --help` displays correct program name
2. `python3 style_exporter.py --help` displays correct program name
3. `converter.py` imports still work (via temporary alias)
4. `convert_all.py` can import topology_repair and style_exporter

---

### Step 3: Create `ftth_converter.py` -- Extract L3-L5 FTTH Converter

**Goal:** Create `/home/cat/projects/CAD2GIS/experiment/python/ftth_converter.py` containing all FTTH-domain logic from converter.py. Imports from `cad_common`, `schema_config`, and `domain_vocab`.

**Source:** Copy and adapt remaining sections from `/home/cat/projects/CAD2GIS/experiment/python/converter.py`

**Symbols to include:** Every symbol listed in Section 5.0.1 as "ftth_converter" (every symbol NOT assigned to cad_common).

**Key modifications:**

1. **Import cad_common at module top:**
   ```python
   # Dual-mode import: works as standalone script or package member
   try:
       import cad_common
   except ImportError:
       from . import cad_common
   ```

2. **Replace all direct references to moved functions with `cad_common.` prefix throughout the file.**
   This is the single highest-risk step. The executor must verify every call site. A non-exhaustive list of functions that MUST be prefixed:
   - All CRS functions: `_reproject_point`, `_to_wgs84`, `_valid_coord`, `_in_region_bounds`, `_meters_to_units`, `_line_length_m`
   - All geometry functions: `_extract_wkt`, `_extract_dimension`, `_wkt_point`, `_wkt_linestring`, `_wkt_polygon_exterior`, `_circle_points`, `_arc_points`
   - All DWG access functions: `_init_libredwg`, `_entity_utf8_text`, `_parse_dwg_color`, `_resolve_effective_color`, `_cstr`, `_layer_name`, `_lwpoline_points`, `_adaptive_chord_tolerance`
   - All utility functions: `_cluster_points`, `_haversine`, `_haversine_length`, `_centroid`, `_sha256_file`, `_safe_layer_name`
   - All DWG type constants: `cad_common.DWG_TYPE_LINE`, `cad_common.DWG_TYPE_LWPOLYLINE`, etc.
   - All tolerance constants: `cad_common.DEFAULT_FRAGMENT_CLUSTER_TOL_M`, etc.
   - All CRS state globals: `cad_common._CRS_TRANSFORM`, `cad_common._TARGET_IS_GEOGRAPHIC`, etc.

3. **CRS initialization in main():** Replace lines 2683-2713 (direct global mutation) with:
   ```python
   cad_common.init_crs(args.source_crs, args.target_crs)
   ```

4. **Cross-module global patching in read_dwg():** Per Section 5.1.2, patch `cad_common.DWG_TYPE_*`, `cad_common.DIMENSION_TYPE_UNION`, `cad_common.CONTROL_TYPES`.

5. **`sys.path.insert(0, "/usr/local/lib/python3.12/dist-packages")`** (line 1200) stays inside `read_dwg()` in ftth_converter -- this is LibreDWG SWIG import path setup, specific to the converter runtime.

6. **Schema config imports:** Use dual-mode pattern:
   ```python
   try:
       from schema_config import (REQUIRED_LAYERS, LAYER_PATTERN_MAP, ...)
   except ImportError:
       from .schema_config import (REQUIRED_LAYERS, LAYER_PATTERN_MAP, ...)
   ```

7. **Topology/Style import aliasing in main():** After Step 2 renames `topology_builder.py` → `topology_repair.py` and `style_builder.py` → `style_exporter.py`, update main()'s conditional imports (currently at lines ~2873 and ~2963) to use the new module names with backward-compatible aliases:
   ```python
   try:
       import topology_repair as topology_builder
   except ImportError:
       from . import topology_repair as topology_builder

   try:
       import style_exporter as style_builder
   except ImportError:
       from . import style_exporter as style_builder
   ```
   This preserves all internal references (`topology_builder.chain_edges_gpkg()`, `style_builder.build_styles()`) while importing from the renamed files.

**Acceptance criteria:**
1. `python3 ftth_converter.py --help` displays usage
2. `python3 ftth_converter.py --config /home/cat/projects/CAD2GIS/experiment/config/hutabohu.json --input <dwg> --output /tmp/test_ftth_only.gpkg --skip-topology --skip-styles --skip-legend-detection` completes successfully
3. Output GPKG has all 8 FTTH layers
4. `ftth_converter.py` contains ZERO DWG_TYPE_* literal definitions (all through cad_common)
5. `grep "DWG_TYPE_" ftth_converter.py` only shows `cad_common.DWG_TYPE_*` references, no local definitions
6. `cad_common.init_crs()` is the ONLY place CRS state globals are modified (no direct `cad_common._CRS_TRANSFORM = ...` anywhere in ftth_converter)

---

### Step 4: Create `convert_all.py` -- Thin Orchestrator

**Goal:** Create `/home/cat/projects/CAD2GIS/experiment/python/convert_all.py` that chains all 4 stages for batch convenience.

**Design:**
```
convert_all.py
  |-- Stage 1: ftth_converter.main() equivalent (DWG -> raw GPKG)
  |     |-- Calls: cad_common (CRS), ftth_converter (read_dwg + write_geopackage)
  |-- Stage 2: topology_repair.repair_gpkg() (topology repair)
  |     |-- Calls: topology_repair.py directly
  |-- Stage 3: style_exporter.build_styles() (QGIS styling)
  |     |-- Calls: style_exporter.py via subprocess (QGIS isolation)
  |-- Stage 4: Evidence + verification summary
        |-- Prints per-layer counts, CONV-SUM, SHA256
```

**CLI interface:**
```
python3 convert_all.py --config project.json --input project.dwg --output project.gpkg
                      [--skip-extract] [--skip-topology] [--skip-styles]
                      [--snap-tol 5.0] [--isolation-threshold 30.0]
                      [--source-crs EPSG:3857] [--target-crs EPSG:3857]
                      [--temp-dir /tmp/geoformer]
```

**NOTE on style_exporter integration:** The current style_exporter uses QGIS Python bindings which can segfault on exit. The orchestrator should call it via `subprocess.run([sys.executable, 'style_exporter.py', ...])` to isolate the crash. On failure, emit warning but do not fail the pipeline.

**Acceptance criteria:**
1. `python3 convert_all.py --help` displays all options
2. `python3 convert_all.py --config /home/cat/projects/CAD2GIS/experiment/config/hutabohu.json --input <dwg> --output /tmp/test_all.gpkg` completes end-to-end
3. `--skip-extract`, `--skip-topology`, `--skip-styles` flags all work correctly
4. Pipeline exit code is non-zero if any non-skippable stage fails
5. Style stage failure does NOT fail the pipeline (warning only)

---

### Step 5: Update converter.py to Backward-Compatible Shim

**Goal:** Modify `/home/cat/projects/CAD2GIS/experiment/python/converter.py` to be a thin re-export shim that delegates to `ftth_converter` and `cad_common` for backward compatibility.

**Changes:**
```python
"""
Backward-compatibility shim.
Prefer: from python.ftth_converter import main, read_dwg, write_geopackage
        from python.cad_common import _extract_wkt, _parse_dwg_color, ...
"""
import sys
# Re-export everything from the split modules
from .cad_common import *
from .ftth_converter import *

if __name__ == "__main__":
    from .ftth_converter import main
    sys.exit(main())
```

**Note:** The shim re-exports `cad_common` FIRST so cad_common symbols win in case of name collisions (both modules may define some same-named globals at the module level, e.g., `DWG_TYPE_LINE` constants -- cad_common's are the canonical versions).

**Verification:** Any script that previously did `from python.converter import write_geopackage` or `python3 -m python.converter` continues to work.

**Acceptance criteria:**
1. `python3 -m python.converter --help` displays the same CLI as before
2. `from python.converter import read_dwg, write_geopackage` works
3. Shim file is <30 lines (no duplicated logic)

---

### Step 6: End-to-End Regression Verification

**Goal:** Run the full pipeline on the Hutabohu DWG and verify output matches the baseline exactly.

**Verification command:**
```bash
cd /home/cat/projects/CAD2GIS/experiment
python3 python/convert_all.py \
  --config config/hutabohu.json \
  --input "data/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" \
  --output /tmp/hutabohu_regression_test.gpkg \
  --snap-tol 5.0 --isolation-threshold 30.0
```

**Validation checklist:**
- [ ] Pipeline exits with code 0
- [ ] Output GPKG has exactly the same layer list as baseline: `ogrinfo /tmp/hutabohu_regression_test.gpkg | grep "Layer name:"`
- [ ] Per-layer feature counts match:
  - `ogrinfo /tmp/hutabohu_regression_test.gpkg BOITE | grep "Feature Count:"` -> 45
  - `ogrinfo /tmp/hutabohu_regression_test.gpkg CABLE | grep "Feature Count:"` -> 166
  - `ogrinfo /tmp/hutabohu_regression_test.gpkg PTECH | grep "Feature Count:"` -> 167
- [ ] Conservation ledger CONV-SUM = 6942 (sum of all entity weights across all dispositions)
- [ ] Compare against `evaluator.py`: `python3 python/evaluator.py --gpkg /tmp/hutabohu_regression_test.gpkg`
  - Note: `evaluator.py` reads GPKG files only (no Python imports from converter). No code changes needed.
- [ ] All evaluator rules pass with same statuses as in `output/hutabohu_verification_report.json`
- [ ] Standalone mode works: `python3 python/ftth_converter.py --config config/hutabohu.json ...`
- [ ] Standalone topology works: `python3 python/topology_repair.py --gpkg /tmp/...`
- [ ] Standalone style export works: `python3 python/style_exporter.py --gpkg /tmp/...`

**Acceptance criteria:**
1. 20/20 layers identical between old and new output
2. CONV-SUM = 6942 preserved
3. All 4 standalone scripts run independently
4. convert_all.py runs end-to-end in one command

---

## 6. Risk and Mitigation Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `TELECOM_ANNOTATION_KEYWORDS` accidentally copied to cad_common (contains `"nro"`, `"pm"` -- FTTH terms) | Medium | High | Explicitly excluded in Step 1 and Section 5.0.1 table. Grep verification in acceptance criteria catches leakage. |
| `aci_to_rgb` import causes dependency on schema_config in cad_common | Low | High | Copy the full chain (_hsv_bytes, _generate_aci_table, ACI_TO_RGB dict, aci_to_rgb, DEFAULT_COLOR_RGB) from schema_config into cad_common. Zero FTTH symbols in the chain. |
| `_resolve_effective_color` references layer_style_table dict built in read_dwg | Low | Medium | The function accepts layer_style_table as parameter; no direct dependency. Already verified. |
| CRS functions called before init_crs() produces silently wrong coordinates | Medium | High | `init_crs()` sets `_CRS_INITIALIZED` flag. All CRS-dependent functions raise RuntimeError if called before init. Turns silent corruption into loud failure. |
| `read_dwg()` patches `cad_common.DWG_TYPE_*` but forgets `DIMENSION_TYPE_UNION` or `CONTROL_TYPES` | Medium | High | Section 5.1.2 provides explicit contract with code template. Acceptance criteria verify correct patching. |
| Standalone vs package imports break on first run | Medium | Medium | Every new .py file uses try/except ImportError dual-mode pattern. |
| Existing scripts break because converter.py is now a shim | Low | Medium | Shim preserves full re-export; test before removing anything. |
| `style_exporter.py` subprocess call segfaults in convert_all | Medium | Low | Catch subprocess error, emit warning, continue (styles are non-critical). |
| Function moved to wrong module (cad_common vs ftth_converter) | Low | High | Section 5.0.1 table is the single source of truth. Executor must follow it exactly. |
| `sys.path.insert` inside read_dwg() accidentally removed during extraction | Low | High | Explicitly noted in Section 5.0.1 table as staying in ftth_converter. Code review catches this. |

---

## 7. File Inventory (Post-Refactoring)

```
experiment/python/
|-- cad_common.py          NEW: Shared L1-L2 library (~650 lines, zero FTTH symbols)
|-- ftth_converter.py      NEW: FTTH converter L3-L5 (~2,850 lines, imports cad_common)
|-- convert_all.py         NEW: Thin orchestrator (~150 lines)
|-- topology_repair.py     RENAMED: from topology_builder.py (~1,378 lines)
|-- style_exporter.py      RENAMED: from style_builder.py (~473 lines)
|-- converter.py           MODIFIED: Backward-compat shim (~20 lines)
|-- topology_builder.py    KEPT (transitional): Original, removed after verification
|-- style_builder.py       KEPT (transitional): Original, removed after verification
|-- schema_config.py       UNCHANGED: FTTH pattern definitions (~2,680 lines)
|-- domain_vocab.py        UNCHANGED: Domain vocab validation (~352 lines)
|-- legend_detector.py     UNCHANGED: Legend detection (~524 lines)
|-- layout_miner.py        UNCHANGED: Paper-space mining (~755 lines)
|-- evidence_ledger.py     UNCHANGED: Evidence writing (~420 lines)
|-- evaluator.py           UNCHANGED: Quality verification (~1,687 lines; reads GPKG only, no code changes needed)
|-- __init__.py            MODIFIED: Updated docstring
```

---

## 8. Open Questions

1. **Should `converter.py` shim be permanent or temporary?** -- Depends on whether external scripts import from `python.converter`. If so, keep permanently. If only internal, remove after verification.

2. **Should `evaluator.py` be updated to import from `ftth_converter`?** -- RESOLVED: No. `evaluator.py` reads GPKG files only (no Python imports from converter at all). No code changes needed.

3. **Should the old `topology_builder.py` and `style_builder.py` be deleted immediately?** -- Keep for one release cycle as transitional copies, then remove.

---

**Does this plan capture your intent?**
- "proceed" -- Begin implementation via `/oh-my-claudecode:start-work ralplan-consolidation-fidelity`
- "adjust [X]" -- Return to interview to modify specific sections
- "restart" -- Discard and start fresh
