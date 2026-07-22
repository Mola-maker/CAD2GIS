#!/usr/bin/env python3
"""
FTTH GIS Verification Engine — FiberHome Project 2
====================================================
Complete rule-based validator implementing all 7 verification rule groups
from FTTH_GIS_Technical_Standards.md Part III.

Architecture:
  VerificationReport: immutable report with per-rule pass/fail + summary
  run_verification(gpkg_path, extended=False, agent_reports_dir=None) -> VerificationReport

Rule Groups:
  1 — File Integrity (1.1-1.9): layer presence, geometry types, naming
  2 — CRS Consistency (2.0): all layers share one CRS; if --expected-crs is
      configured (default EPSG:3857) it must match
  3 — Empty Layer (3.0): all 8 layers have >=1 feature
  4 — Field Existence & Non-Null (4.1-4.16): mandatory field + CODE uniqueness
  5 — Referential Integrity / Isolation (5.1-5.4): FK bidirectional checks
  6 — Geometric Checks (6.1-6.6): overlap, containment, endpoint coincidence
  7 — Data Validation (7.1-7.2): capacity + PM port balance
  8 — Evidence / Provenance Gate (8.1-8.3): conservation_ledger SUM invariant,
      field_provenance coverage, annotation candidates consistency.
      Default: warning level (WARN recorded, exit code unchanged);
      --strict-provenance escalates violations to FAIL (fail-closed).

Usage:
  python evaluator.py --gpkg output/FiberHome_P2_FTTH.gpkg [--output report.json]
  python evaluator.py --gpkg ... --expected-crs EPSG:3857 --endpoint-tol 1.0
  python evaluator.py --gpkg ... --extended --agent-reports-dir <path> [--quiet]
  python evaluator.py --gpkg ... --strict-provenance
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict

from osgeo import ogr

from .schema_config import (
    BOITE, CABLE, PTECH, INFRASTRUCTURE_FC, SITE, ZNRO, ZPM, IMB,
    FEATURE_CLASS_BY_NAME,
    REQUIRED_LAYERS,
    REQUIRED_GEOM,
    REQUIRED_GEOM_NAMES,
    SEVERITY,
    FIELD_NAME_CROSSWALK,
    DOMAIN_VOCABULARIES,
    FIELD_DOMAIN_MAP,
    VERIFICATION_RULES,
    VERIFICATION_RULES_BY_ID,
    FK_DEPENDENCY_GRAPH,
    WKB_POINT,
    WKB_LINESTRING,
    WKB_POLYGON,
)

from .domain_vocab import validate_domain_value

from .evidence_ledger import (
    CONSERVATION_TABLE,
    CANDIDATES_TABLE,
    PROVENANCE_TABLE,
    META_TOTAL_DISPOSITION,
    CANDIDATE_STATUSES,
    PROVENANCE_KINDS,
)

# ── Geometry imports ──────────────────────────────────────────────────────────
from shapely.geometry import Point, LineString, Polygon, shape
from shapely.strtree import STRtree
HAS_SHAPELY = True

# ── Runtime configuration (set by run_verification / CLI) ─────────────────────

DEFAULT_EXPECTED_CRS = "EPSG:3857"
DEFAULT_ENDPOINT_TOL = 1.0  # metres (geographic CRS) / CRS units (projected CRS)

_RUNTIME = {
    "expected_crs": DEFAULT_EXPECTED_CRS,
    "endpoint_tol": DEFAULT_ENDPOINT_TOL,
    "strict_provenance": False,
    "allow_ptech_endpoints": False,
}

# ── Layer name normalization ──────────────────────────────────────────────────

def normalize_layer_name(name):
    """Strip prefix/suffix markers and spaces, return uppercase base name."""
    name = name.strip().upper()
    for prefix in ('_', '-', '.'):
        if name.startswith(prefix):
            name = name[1:]
    for suffix in ('_', '-', '.'):
        if name.endswith(suffix):
            name = name[:-1]
    return name


def resolve_layer_name(name):
    """Map a GDAL layer name to a canonical layer key (BOITE, CABLE, etc.)."""
    base = normalize_layer_name(name)
    for canonical in REQUIRED_LAYERS:
        if base == canonical or base.endswith(f"_{canonical}") or base.endswith(canonical):
            return canonical
    return None


# ── Field name resolution ─────────────────────────────────────────────────────

def resolve_field(lyr_defn, field_name, fc_name=None):
    """
    Try full field name first, then fall back to truncated name via crosswalk.
    Returns (resolved_name, field_index) or (None, -1) if not found.
    """
    # Try full name
    idx = lyr_defn.GetFieldIndex(field_name)
    if idx >= 0:
        return field_name, idx

    # Try truncated name from crosswalk
    for truncated, full in FIELD_NAME_CROSSWALK.items():
        if full == field_name:
            idx2 = lyr_defn.GetFieldIndex(truncated)
            if idx2 >= 0:
                return truncated, idx2

    # Try the truncated name directly
    idx3 = lyr_defn.GetFieldIndex(field_name[:10])
    if idx3 >= 0:
        return field_name[:10], idx3

    return None, -1


def get_field_value(feature, lyr_defn, field_name, fc_name=None):
    """Get a field value from a feature, trying full and truncated names."""
    resolved, idx = resolve_field(lyr_defn, field_name, fc_name)
    if idx < 0:
        return None
    value = feature.GetField(idx)
    return value


# ── VerificationReport ────────────────────────────────────────────────────────

class VerificationReport:
    """Immutable verification result container."""

    def __init__(self, gpkg_path):
        self.gpkg = gpkg_path
        self.rules = {}         # rule_id -> {severity, status, detail}
        self.summary = {
            "errors": 0,
            "warnings": 0,
            "info": 0,
            "verdict": "PASS",
            "rules_passed": 0,
            "rules_failed": 0,
        }

    def add_rule(self, rule_id, severity, status, detail=None):
        entry = {"severity": severity, "status": status}
        if detail:
            entry["detail"] = detail
        self.rules[rule_id] = entry
        if status == "PASS":
            self.summary["rules_passed"] += 1
        else:
            self.summary["rules_failed"] += 1
            if severity == "C":
                self.summary["errors"] += 1
            elif severity == "E":
                self.summary["errors"] += 1
            elif severity == "W":
                self.summary["warnings"] += 1
            elif severity == "I":
                # Informational (e.g. 8.x provenance gate in warning mode):
                # recorded but never changes the verdict / exit code.
                self.summary["info"] += 1

    def finalize(self):
        """Compute final verdict from rule results."""
        if self.summary["errors"] > 0:
            self.summary["verdict"] = "FAIL"
        elif self.summary["warnings"] > 0:
            self.summary["verdict"] = "QUARANTINE"
        else:
            self.summary["verdict"] = "PASS"
        return self

    def to_dict(self):
        return {"gpkg": self.gpkg, "rules": self.rules, "summary": self.summary}


# ── Helper: build layer lookup ────────────────────────────────────────────────

def _build_layer_map(ds):
    """Build dict mapping canonical layer name -> (ogr_layer, geometry_type_name)."""
    layer_map = {}
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayerByIndex(i)
        name = lyr.GetName()
        canonical = resolve_layer_name(name)
        if canonical:
            geom_type = ogr.GeometryTypeToName(lyr.GetGeomType())
            layer_map[canonical] = (lyr, geom_type)
    return layer_map


# ── Rule Group 1: File Integrity ──────────────────────────────────────────────

def check_1_1_layer_presence(report, ds, layer_map):
    """1.1: All 8 required layers present in GeoPackage."""
    missing = [lyr for lyr in REQUIRED_LAYERS if lyr not in layer_map]
    if missing:
        report.add_rule("1.1", "C", "FAIL", f"Missing layers: {', '.join(sorted(missing))}")
        return
    report.add_rule("1.1", "C", "PASS")


def _check_layer_geom_naming(report, rule_id, layer_map, canonical, expected_geom_type, expected_geom_names):
    """Generic rule 1.2-1.9: check geometry type for a layer."""
    if canonical not in layer_map:
        report.add_rule(rule_id, "E", "FAIL", f"Layer {canonical} not found")
        return
    _lyr, geom_name = layer_map[canonical]
    geom_name_upper = geom_name.upper() if geom_name else ""
    expected_upper = [n.upper() for n in expected_geom_names]
    if any(geom_name_upper == e for e in expected_upper) or geom_name_upper in expected_upper:
        report.add_rule(rule_id, "E", "PASS")
    else:
        report.add_rule(rule_id, "E", "FAIL",
                        f"Expected {expected_geom_names}, got {geom_name}")


def check_1_2_imb_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.2", layer_map, "IMB", WKB_POINT, ["Point", "Polygon", "Multi Polygon"])

def check_1_3_site_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.3", layer_map, "SITE", WKB_POINT, ["Point"])

def check_1_4_boite_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.4", layer_map, "BOITE", WKB_POINT, ["Point"])

def check_1_5_cable_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.5", layer_map, "CABLE", WKB_LINESTRING, ["Line String", "Multi Line String"])

def check_1_6_ptech_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.6", layer_map, "PTECH", WKB_POINT, ["Point"])

def check_1_7_infrastructure_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.7", layer_map, "INFRASTRUCTURE", WKB_LINESTRING, ["Line String", "Multi Line String"])

def check_1_8_znro_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.8", layer_map, "ZNRO", WKB_POLYGON, ["Polygon", "Multi Polygon"])

def check_1_9_zpm_naming(report, ds, layer_map):
    _check_layer_geom_naming(report, "1.9", layer_map, "ZPM", WKB_POLYGON, ["Polygon", "Multi Polygon"])


# ── Rule Group 2: CRS Consistency ─────────────────────────────────────────────

def _srs_epsg_code(srs):
    """Extract the EPSG code string from an OGR spatial reference, or None."""
    if srs is None:
        return None
    return (srs.GetAuthorityCode(None)
            or srs.GetAuthorityCode("PROJCS")
            or srs.GetAuthorityCode("GEOGCS"))


def _parse_expected_crs(expected):
    """Normalize 'EPSG:3857' / '3857' to '3857'; '', 'any', 'none' disable the check."""
    if expected is None:
        return None
    text = str(expected).strip().upper()
    if text in ("", "ANY", "NONE"):
        return None
    return text.replace("EPSG:", "").strip()


def check_2_0_crs_consistency(report, ds, layer_map):
    """2.0: All layers share one CRS; if expected_crs is configured it must match."""
    expected_code = _parse_expected_crs(_RUNTIME.get("expected_crs"))

    codes = defaultdict(list)
    no_crs = []
    for canonical, (lyr, _geom_name) in layer_map.items():
        code = _srs_epsg_code(lyr.GetSpatialRef())
        if code is None:
            no_crs.append(canonical)
        else:
            codes[code].append(canonical)

    issues = []
    if no_crs:
        issues.append(f"layers without CRS: {', '.join(sorted(no_crs))}")
    if len(codes) > 1:
        desc = "; ".join(f"EPSG:{c}: {', '.join(sorted(ls))}" for c, ls in sorted(codes.items()))
        issues.append(f"inconsistent CRS across layers ({desc})")
    if expected_code and codes and set(codes) != {expected_code}:
        found = ", ".join(f"EPSG:{c}" for c in sorted(codes))
        issues.append(f"expected EPSG:{expected_code}, found {found}")

    if issues:
        report.add_rule("2.0", "C", "FAIL", "; ".join(issues))
    else:
        report.add_rule("2.0", "C", "PASS")


# ── Rule Group 3: Empty Layer ─────────────────────────────────────────────────

def check_3_0_empty_layer(report, ds, layer_map):
    """3.0: No layer may be empty — each must have >= 1 feature."""
    empty = []
    for canonical in REQUIRED_LAYERS:
        if canonical not in layer_map:
            empty.append(f"{canonical} (missing)")
        elif layer_map[canonical][0].GetFeatureCount() == 0:
            empty.append(canonical)
    if empty:
        report.add_rule("3.0", "C", "FAIL", f"Empty layers: {', '.join(empty)}")
    else:
        report.add_rule("3.0", "C", "PASS")


# ── Rule Group 4: Field Existence & Non-Null ──────────────────────────────────

def _check_mandatory_fields(report, rule_id, lyr, fc_name, mandatory_fields):
    """Check that all mandatory fields exist and are non-null across all features."""
    lyr_defn = lyr.GetLayerDefn()
    lyr.ResetReading()

    missing_fields = []
    null_counts = defaultdict(int)
    total_features = 0

    for field_name in mandatory_fields:
        resolved, idx = resolve_field(lyr_defn, field_name, fc_name)
        if idx < 0:
            missing_fields.append(field_name)

    if missing_fields:
        report.add_rule(rule_id, "E", "FAIL",
                        f"Missing fields: {', '.join(missing_fields)}")
        return

    lyr.ResetReading()
    for feat in lyr:
        total_features += 1
        for field_name in mandatory_fields:
            resolved, idx = resolve_field(lyr_defn, field_name, fc_name)
            if idx >= 0:
                value = feat.GetField(idx)
                if value is None or (isinstance(value, str) and value.strip() == ''):
                    null_counts[field_name] += 1

    if null_counts:
        details = []
        for fname, count in sorted(null_counts.items(), key=lambda x: -x[1]):
            details.append(f"{count} features have null {fname}")
        report.add_rule(rule_id, "E", "FAIL", "; ".join(details[:5]))
    else:
        report.add_rule(rule_id, "E", "PASS")


def _check_code_uniqueness(report, rule_id, lyr, fc_name):
    """Check that CODE is unique within the layer."""
    lyr_defn = lyr.GetLayerDefn()
    resolved, idx = resolve_field(lyr_defn, "CODE", fc_name)
    if idx < 0:
        report.add_rule(rule_id, "E", "FAIL", "CODE field not found")
        return
    lyr.ResetReading()
    seen = {}
    duplicates = set()
    for feat in lyr:
        fid = feat.GetFID()
        val = feat.GetField(idx)
        if val is not None and val != '':
            v = str(val).strip()
            if v in seen:
                duplicates.add(v)
            else:
                seen[v] = fid
    if duplicates:
        report.add_rule(rule_id, "E", "FAIL",
                        f"{len(duplicates)} duplicate CODE values")
    else:
        report.add_rule(rule_id, "E", "PASS")


def _check_fields_and_uniqueness(report, fields_rule_id, unique_rule_id, layer_map, canonical):
    """Combined mandatory field + CODE uniqueness check for a layer."""
    fc_config = FEATURE_CLASS_BY_NAME.get(canonical)
    if not fc_config or canonical not in layer_map:
        return
    lyr = layer_map[canonical][0]
    _check_mandatory_fields(report, fields_rule_id, lyr, canonical, fc_config["mandatory_fields"])
    _check_code_uniqueness(report, unique_rule_id, lyr, canonical)


def check_4_1_imb_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.1", "4.2_IMB", layer_map, "IMB")

def check_4_3_boite_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.3", "4.4_BOITE", layer_map, "BOITE")

def check_4_5_cable_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.5", "4.6_CABLE", layer_map, "CABLE")

def check_4_7_ptech_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.7", "4.8_PTECH", layer_map, "PTECH")

def check_4_9_infrastructure_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.9", "4.10_INFRA", layer_map, "INFRASTRUCTURE")

def check_4_11_zpm_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.11", "4.12_ZPM", layer_map, "ZPM")

def check_4_13_znro_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.13", "4.14_ZNRO", layer_map, "ZNRO")

def check_4_15_site_fields(report, ds, layer_map):
    _check_fields_and_uniqueness(report, "4.15", "4.16_SITE", layer_map, "SITE")

def check_4_code_uniqueness(report, ds, layer_map):
    """Generic CODE uniqueness — dispatched by check_function for CODE-only rules."""
    pass


# ── Rule Group 5: Referential Integrity / Isolation ───────────────────────────

def _build_code_set(lyr, lyr_defn, field_name, fc_name, filter_field=None, filter_value=None):
    """Build a set of (uppercase) values from a layer, optionally filtered."""
    resolved, idx = resolve_field(lyr_defn, field_name, fc_name)
    if idx < 0:
        return set()
    codes = set()
    lyr.ResetReading()
    for feat in lyr:
        if filter_field is not None:
            f_resolved, f_idx = resolve_field(lyr_defn, filter_field, fc_name)
            if f_idx >= 0:
                fv = feat.GetField(f_idx)
                if fv is None:
                    continue
                if str(fv).strip().upper() != filter_value.upper():
                    continue
        val = feat.GetField(idx)
        if val is not None and str(val).strip() != '':
            codes.add(str(val).strip().upper())
    return codes


def check_5_1_site_zpm_isolation(report, ds, layer_map):
    """5.1: SITE(TYPE=PM) <-> ZPM bidirectional isolation."""
    issues = []

    # SIDE A: every TYPE=PM SITE must have matching ZPM.CODE
    if "SITE" in layer_map and "ZPM" in layer_map:
        site_lyr = layer_map["SITE"][0]
        site_defn = site_lyr.GetLayerDefn()
        pm_codes = _build_code_set(site_lyr, site_defn, "CODE", "SITE", "TYPE", "PM")

        zpm_lyr = layer_map["ZPM"][0]
        zpm_defn = zpm_lyr.GetLayerDefn()
        zpm_codes = _build_code_set(zpm_lyr, zpm_defn, "CODE", "ZPM")

        pm_without_zpm = pm_codes - zpm_codes
        if pm_without_zpm:
            issues.append(f"{len(pm_without_zpm)} PM SITE(s) with no matching ZPM")

        # SIDE B: every ZPM.CODE must have matching TYPE=PM SITE.CODE
        zpm_without_pm = zpm_codes - pm_codes
        if zpm_without_pm:
            issues.append(f"{len(zpm_without_pm)} ZPM(s) with no matching PM SITE")
    elif "SITE" not in layer_map:
        issues.append("SITE layer missing")
    elif "ZPM" not in layer_map:
        issues.append("ZPM layer missing")

    if issues:
        report.add_rule("5.1", "E", "FAIL", "; ".join(issues))
    else:
        report.add_rule("5.1", "E", "PASS")


def check_5_2_site_boite_isolation(report, ds, layer_map):
    """5.2: SITE(PM) <-> BOITE(PBO) master-slave isolation."""
    issues = []

    if "SITE" not in layer_map:
        report.add_rule("5.2", "E", "FAIL", "SITE layer missing")
        return
    if "BOITE" not in layer_map:
        report.add_rule("5.2", "E", "FAIL", "BOITE layer missing")
        return

    site_lyr = layer_map["SITE"][0]
    site_defn = site_lyr.GetLayerDefn()
    pm_codes = _build_code_set(site_lyr, site_defn, "CODE", "SITE", "TYPE", "PM")

    boite_lyr = layer_map["BOITE"][0]
    boite_defn = boite_lyr.GetLayerDefn()

    # Every TYPE=PBO BOITE.REF_PM must resolve to SITE(TYPE=PM).CODE
    pbo_refs = set()
    boite_lyr.ResetReading()
    type_resolved, type_idx = resolve_field(boite_defn, "TYPE", "BOITE")
    ref_resolved, ref_idx = resolve_field(boite_defn, "REF_PM", "BOITE")
    if type_idx < 0 or ref_idx < 0:
        report.add_rule("5.2", "E", "FAIL", "Required field(s) not found in BOITE")
        return

    orphan_pbos = []
    for feat in boite_lyr:
        btype = feat.GetField(type_idx)
        if btype is None:
            continue
        if str(btype).strip().upper() != "PBO":
            continue
        ref_pm = feat.GetField(ref_idx)
        if ref_pm is None or str(ref_pm).strip() == '':
            orphan_pbos.append(str(feat.GetFID()))
        elif str(ref_pm).strip().upper() not in pm_codes:
            orphan_pbos.append(f"{ref_pm} (FID={feat.GetFID()})")
        pbo_refs.add(str(ref_pm).strip().upper() if ref_pm else '')

    if orphan_pbos:
        issues.append(f"{len(orphan_pbos)} PBO with invalid/missing REF_PM")

    # Every PM must have >= 1 PBO
    pms_without_pbo = pm_codes - pbo_refs
    if pms_without_pbo:
        issues.append(f"{len(pms_without_pbo)} PM(s) with no PBO BOITE")

    if issues:
        report.add_rule("5.2", "E", "FAIL", "; ".join(issues))
    else:
        report.add_rule("5.2", "E", "PASS")


def check_5_3_site_cable_isolation(report, ds, layer_map):
    """5.3: SITE(PM) <-> CABLE(DISTRIBUTION) master-slave isolation."""
    issues = []

    if "SITE" not in layer_map:
        report.add_rule("5.3", "E", "FAIL", "SITE layer missing")
        return
    if "CABLE" not in layer_map:
        report.add_rule("5.3", "E", "FAIL", "CABLE layer missing")
        return

    site_lyr = layer_map["SITE"][0]
    site_defn = site_lyr.GetLayerDefn()
    pm_codes = _build_code_set(site_lyr, site_defn, "CODE", "SITE", "TYPE", "PM")

    cable_lyr = layer_map["CABLE"][0]
    cable_defn = cable_lyr.GetLayerDefn()

    type_resolved, type_idx = resolve_field(cable_defn, "TYPE_CABLE", "CABLE")
    ref_resolved, ref_idx = resolve_field(cable_defn, "REF_PM", "CABLE")
    if type_idx < 0 or ref_idx < 0:
        report.add_rule("5.3", "E", "FAIL", "Required field(s) not found in CABLE")
        return

    orphan_cables = []
    cable_refs = set()
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        ctype = feat.GetField(type_idx)
        if ctype is None:
            continue
        if str(ctype).strip().upper() != "DISTRIBUTION":
            continue
        ref_pm = feat.GetField(ref_idx)
        if ref_pm is None or str(ref_pm).strip() == '':
            orphan_cables.append(str(feat.GetFID()))
        elif str(ref_pm).strip().upper() not in pm_codes:
            orphan_cables.append(f"{ref_pm} (FID={feat.GetFID()})")
        cable_refs.add(str(ref_pm).strip().upper() if ref_pm else '')

    if orphan_cables:
        issues.append(f"{len(orphan_cables)} DISTRIBUTION cable(s) with invalid REF_PM")

    # Every PM must have >= 1 DISTRIBUTION cable
    pms_without_cable = pm_codes - cable_refs
    if pms_without_cable:
        issues.append(f"{len(pms_without_cable)} PM(s) with no DISTRIBUTION cable")

    if issues:
        report.add_rule("5.3", "E", "FAIL", "; ".join(issues))
    else:
        report.add_rule("5.3", "E", "PASS")


def check_5_4_cable_endpoint_isolation(report, ds, layer_map):
    """5.4: CABLE endpoints <-> BOITE/SITE isolation (4 sub-checks)."""
    issues = []

    if "CABLE" not in layer_map:
        report.add_rule("5.4", "E", "FAIL", "CABLE layer missing")
        return

    cable_lyr = layer_map["CABLE"][0]
    cable_defn = cable_lyr.GetLayerDefn()

    # Build endpoint lookup sets
    boite_codes = set()
    if "BOITE" in layer_map:
        boite_lyr = layer_map["BOITE"][0]
        boite_defn = boite_lyr.GetLayerDefn()
        boite_codes = _build_code_set(boite_lyr, boite_defn, "CODE", "BOITE")

    site_pm_codes = set()
    if "SITE" in layer_map:
        site_lyr = layer_map["SITE"][0]
        site_defn = site_lyr.GetLayerDefn()
        site_pm_codes = _build_code_set(site_lyr, site_defn, "CODE", "SITE", "TYPE", "PM")

    ptech_codes = set()
    if "PTECH" in layer_map:
        ptech_lyr = layer_map["PTECH"][0]
        ptech_codes = _build_code_set(ptech_lyr, ptech_lyr.GetLayerDefn(), "CODE", "PTECH")

    all_endpoint_targets = boite_codes | site_pm_codes
    # Company rule 5.4 recognizes BOITE/SITE endpoints only, but aerial cables
    # legitimately terminate on PTECH poles. Endpoints resolving to a PTECH
    # code are therefore counted separately (informational, not a violation);
    # --allow-ptech-endpoints folds them into the resolved set outright.
    if _RUNTIME.get("allow_ptech_endpoints", False):
        all_endpoint_targets = all_endpoint_targets | ptech_codes

    type_resolved, type_idx = resolve_field(cable_defn, "TYPE_CABLE", "CABLE")
    orig_resolved, orig_idx = resolve_field(cable_defn, "ORIGINE", "CABLE")
    ext_resolved, ext_idx = resolve_field(cable_defn, "EXTREMITE", "CABLE")

    if orig_idx < 0 or ext_idx < 0:
        report.add_rule("5.4", "E", "FAIL", "ORIGINE or EXTREMITE field not found in CABLE")
        return

    # 5.4.1 Forward: Each DISTRIBUTION CABLE.ORIGINE/EXTREMITE must exist as BOITE.CODE or SITE(PM).CODE
    # Cables with null TYPE_CABLE stay in scope: a null type cannot claim exemption
    # from the endpoint check (eliminates the vacuous-pass hole on all-null data).
    # Three-way counting: resolved (BOITE/SITE) / resolved_ptech (INFO) /
    # truly unresolved (violation).
    unresolved_origins = []
    unresolved_ends = []
    resolved_ptech = 0
    null_origins = 0
    null_ends = 0
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        if type_idx >= 0:
            ctype = feat.GetField(type_idx)
            if ctype is not None and str(ctype).strip().upper() != "DISTRIBUTION":
                continue
        orig = feat.GetField(orig_idx)
        ext = feat.GetField(ext_idx)
        if orig is None or str(orig).strip() == '':
            null_origins += 1
        elif str(orig).strip().upper() not in all_endpoint_targets:
            if str(orig).strip().upper() in ptech_codes:
                resolved_ptech += 1
            else:
                unresolved_origins.append(f"{orig} (FID={feat.GetFID()})")
        if ext is None or str(ext).strip() == '':
            null_ends += 1
        elif str(ext).strip().upper() not in all_endpoint_targets:
            if str(ext).strip().upper() in ptech_codes:
                resolved_ptech += 1
            else:
                unresolved_ends.append(f"{ext} (FID={feat.GetFID()})")

    info_notes = []
    if resolved_ptech:
        info_notes.append(
            f"5.4.1 INFO: {resolved_ptech} endpoint(s) resolved to PTECH poles "
            f"(aerial termination; not a violation, use --allow-ptech-endpoints "
            f"to fold into resolved)")
    if null_origins or null_ends:
        issues.append(f"5.4.1: {null_origins} null/empty ORIGINE, {null_ends} null/empty EXTREMITE")
    if unresolved_origins or unresolved_ends:
        issues.append(f"5.4.1: {len(unresolved_origins)} unresolved ORIGINE, {len(unresolved_ends)} unresolved EXTREMITE")

    # 5.4.2 Reverse A: Every PM SITE.CODE appears in >= 1 DISTRIBUTION CABLE ORIGINE/EXTREMITE
    cable_endpoint_pm_refs = set()
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        if type_idx >= 0:
            ctype = feat.GetField(type_idx)
            if ctype is not None and str(ctype).strip().upper() != "DISTRIBUTION":
                continue
        orig = feat.GetField(orig_idx)
        ext = feat.GetField(ext_idx)
        if orig is not None:
            cable_endpoint_pm_refs.add(str(orig).strip().upper())
        if ext is not None:
            cable_endpoint_pm_refs.add(str(ext).strip().upper())

    pms_not_in_cable = site_pm_codes - cable_endpoint_pm_refs
    if pms_not_in_cable:
        issues.append(f"5.4.2: {len(pms_not_in_cable)} PM(s) not referenced by any DISTRIBUTION CABLE endpoint")

    # 5.4.3 Reverse B: Every BOITE(BPE/PBO).CODE appears in >= 1 DISTRIBUTION CABLE endpoint
    boite_bpe_pbo_codes = set()
    if "BOITE" in layer_map:
        boite_lyr = layer_map["BOITE"][0]
        boite_defn = boite_lyr.GetLayerDefn()
        boite_bpe_pbo_codes = _build_code_set(boite_lyr, boite_defn, "CODE", "BOITE")

        # Filter to only BPE and PBO
        boite_lyr.ResetReading()
        type_b_resolved, type_b_idx = resolve_field(boite_defn, "TYPE", "BOITE")
        code_b_resolved, code_b_idx = resolve_field(boite_defn, "CODE", "BOITE")
        bpe_pbo_codes = set()
        if type_b_idx >= 0 and code_b_idx >= 0:
            for feat in boite_lyr:
                bt = feat.GetField(type_b_idx)
                if bt is None:
                    continue
                bt = str(bt).strip().upper()
                if bt in ("BPE", "PBO"):
                    bc = feat.GetField(code_b_idx)
                    if bc is not None and str(bc).strip() != '':
                        bpe_pbo_codes.add(str(bc).strip().upper())
        boite_bpe_pbo_codes = bpe_pbo_codes

    bpe_pbo_not_in_cable = boite_bpe_pbo_codes - cable_endpoint_pm_refs
    if bpe_pbo_not_in_cable:
        issues.append(f"5.4.3: {len(bpe_pbo_not_in_cable)} BPE/PBO(s) not referenced by any DISTRIBUTION CABLE endpoint")

    if issues:
        report.add_rule("5.4", "E", "FAIL", "; ".join(issues + info_notes))
    else:
        report.add_rule("5.4", "E", "PASS", "; ".join(info_notes) or None)


# ── Rule Group 6: Geometric Checks ────────────────────────────────────────────

_EARTH_RADIUS_M = 6371008.8


def _crs_distance(x1, y1, x2, y2, is_geographic):
    """
    True distance between two coordinates, branching on the layer CRS:
      geographic (e.g. EPSG:4326) -> haversine, metres
      projected  (e.g. EPSG:3857) -> planar Euclidean, CRS units (metres)
    """
    if is_geographic:
        phi1, phi2 = math.radians(y1), math.radians(y2)
        dphi = phi2 - phi1
        dlmb = math.radians(x2 - x1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
        return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))
    return math.hypot(x2 - x1, y2 - y1)


def check_6_1_znro_no_overlap(report, ds, layer_map):
    """6.1: ZNRO polygons must not overlap."""
    if not HAS_SHAPELY:
        report.add_rule("6.1", "E", "FAIL", "shapely not available")
        return
    if "ZNRO" not in layer_map:
        report.add_rule("6.1", "E", "FAIL", "ZNRO layer missing")
        return
    lyr = layer_map["ZNRO"][0]
    lyr.ResetReading()
    polys = []
    for feat in lyr:
        geom = feat.GetGeometryRef()
        if geom is not None:
            polys.append(shape(json.loads(geom.ExportToJson())))
    overlaps = []
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            if polys[i].overlaps(polys[j]):
                overlaps.append(f"ZNRO FID={i} overlaps FID={j}")
    if overlaps:
        report.add_rule("6.1", "E", "FAIL", f"{len(overlaps)} overlap(s)")
    else:
        report.add_rule("6.1", "E", "PASS")


def check_6_2_zpm_no_overlap(report, ds, layer_map):
    """6.2: ZPM polygons must not overlap."""
    if not HAS_SHAPELY:
        report.add_rule("6.2", "E", "FAIL", "shapely not available")
        return
    if "ZPM" not in layer_map:
        report.add_rule("6.2", "E", "FAIL", "ZPM layer missing")
        return
    lyr = layer_map["ZPM"][0]
    lyr.ResetReading()
    polys = []
    for feat in lyr:
        geom = feat.GetGeometryRef()
        if geom is not None:
            polys.append(shape(json.loads(geom.ExportToJson())))
    overlaps = []
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            if polys[i].overlaps(polys[j]):
                overlaps.append(f"ZPM FID={i} overlaps FID={j}")
    if overlaps:
        report.add_rule("6.2", "E", "FAIL", f"{len(overlaps)} overlap(s)")
    else:
        report.add_rule("6.2", "E", "PASS")


def check_6_3_site_in_zpm(report, ds, layer_map):
    """6.3: SITE(TYPE=PM) point must be within its matching ZPM polygon."""
    if not HAS_SHAPELY:
        report.add_rule("6.3", "E", "FAIL", "shapely not available")
        return
    if "SITE" not in layer_map or "ZPM" not in layer_map:
        report.add_rule("6.3", "E", "FAIL", "SITE or ZPM layer missing")
        return

    site_lyr = layer_map["SITE"][0]
    site_defn = site_lyr.GetLayerDefn()
    zpm_lyr = layer_map["ZPM"][0]
    zpm_defn = zpm_lyr.GetLayerDefn()

    # Build ZPM polygon lookup by CODE
    zpm_polys = {}
    zpm_lyr.ResetReading()
    code_resolved, code_idx = resolve_field(zpm_defn, "CODE", "ZPM")
    if code_idx < 0:
        report.add_rule("6.3", "E", "FAIL", "ZPM.CODE field not found")
        return
    for feat in zpm_lyr:
        code = feat.GetField(code_idx)
        geom = feat.GetGeometryRef()
        if code is not None and geom is not None:
            zpm_polys[str(code).strip().upper()] = shape(json.loads(geom.ExportToJson()))

    # Check each PM SITE
    site_lyr.ResetReading()
    type_resolved, type_idx = resolve_field(site_defn, "TYPE", "SITE")
    code_resolved, code_idx = resolve_field(site_defn, "CODE", "SITE")
    if type_idx < 0 or code_idx < 0:
        report.add_rule("6.3", "E", "FAIL", "SITE fields not found")
        return

    violations = []
    for feat in site_lyr:
        stype = feat.GetField(type_idx)
        if stype is None or str(stype).strip().upper() != "PM":
            continue
        code = feat.GetField(code_idx)
        geom = feat.GetGeometryRef()
        if code is None or geom is None:
            continue
        code_upper = str(code).strip().upper()
        site_pt = shape(json.loads(geom.ExportToJson()))
        zpm_poly = zpm_polys.get(code_upper)
        if zpm_poly is None:
            violations.append(f"SITE {code} has no matching ZPM polygon")
        elif not zpm_poly.contains(site_pt):
            violations.append(f"SITE {code} not within ZPM {code_upper}")

    if violations:
        report.add_rule("6.3", "E", "FAIL", f"{len(violations)} violation(s)")
    else:
        report.add_rule("6.3", "E", "PASS")


def check_6_4_boite_in_zpm(report, ds, layer_map):
    """6.4: BOITE(TYPE=PBO) point must be within its parent PM's ZPM polygon."""
    if not HAS_SHAPELY:
        report.add_rule("6.4", "E", "FAIL", "shapely not available")
        return
    if "BOITE" not in layer_map or "ZPM" not in layer_map:
        report.add_rule("6.4", "E", "FAIL", "BOITE or ZPM layer missing")
        return

    boite_lyr = layer_map["BOITE"][0]
    boite_defn = boite_lyr.GetLayerDefn()
    zpm_lyr = layer_map["ZPM"][0]
    zpm_defn = zpm_lyr.GetLayerDefn()

    # Build ZPM polygon lookup by CODE (ZPM.CODE = SITE.CODE for PM-type)
    zpm_polys = {}
    zpm_lyr.ResetReading()
    code_resolved, code_idx = resolve_field(zpm_defn, "CODE", "ZPM")
    if code_idx < 0:
        report.add_rule("6.4", "E", "FAIL", "ZPM.CODE field not found")
        return
    for feat in zpm_lyr:
        code = feat.GetField(code_idx)
        geom = feat.GetGeometryRef()
        if code is not None and geom is not None:
            zpm_polys[str(code).strip().upper()] = shape(json.loads(geom.ExportToJson()))

    type_resolved, type_idx = resolve_field(boite_defn, "TYPE", "BOITE")
    ref_resolved, ref_idx = resolve_field(boite_defn, "REF_PM", "BOITE")
    if type_idx < 0 or ref_idx < 0:
        report.add_rule("6.4", "E", "FAIL", "BOITE fields not found")
        return

    violations = []
    boite_lyr.ResetReading()
    for feat in boite_lyr:
        btype = feat.GetField(type_idx)
        if btype is None or str(btype).strip().upper() != "PBO":
            continue
        ref_pm = feat.GetField(ref_idx)
        geom = feat.GetGeometryRef()
        if ref_pm is None or geom is None:
            continue
        ref_pm_upper = str(ref_pm).strip().upper()
        boite_pt = shape(json.loads(geom.ExportToJson()))
        zpm_poly = zpm_polys.get(ref_pm_upper)
        if zpm_poly is None:
            violations.append(f"PBO REF_PM={ref_pm} has no matching ZPM")
        elif not zpm_poly.contains(boite_pt):
            violations.append(f"PBO at {ref_pm} not within ZPM {ref_pm_upper}")

    if violations:
        report.add_rule("6.4", "E", "FAIL", f"{len(violations)} violation(s)")
    else:
        report.add_rule("6.4", "E", "PASS")


def check_6_5_cable_in_zpm(report, ds, layer_map):
    """6.5: CABLE(TYPE_CABLE=DISTRIBUTION) all vertices must be within parent ZPM polygon."""
    if not HAS_SHAPELY:
        report.add_rule("6.5", "E", "FAIL", "shapely not available")
        return
    if "CABLE" not in layer_map or "ZPM" not in layer_map:
        report.add_rule("6.5", "E", "FAIL", "CABLE or ZPM layer missing")
        return

    cable_lyr = layer_map["CABLE"][0]
    cable_defn = cable_lyr.GetLayerDefn()
    zpm_lyr = layer_map["ZPM"][0]
    zpm_defn = zpm_lyr.GetLayerDefn()

    zpm_polys = {}
    zpm_lyr.ResetReading()
    code_resolved, code_idx = resolve_field(zpm_defn, "CODE", "ZPM")
    if code_idx < 0:
        report.add_rule("6.5", "E", "FAIL", "ZPM.CODE field not found")
        return
    for feat in zpm_lyr:
        code = feat.GetField(code_idx)
        geom = feat.GetGeometryRef()
        if code is not None and geom is not None:
            zpm_polys[str(code).strip().upper()] = shape(json.loads(geom.ExportToJson()))

    type_resolved, type_idx = resolve_field(cable_defn, "TYPE_CABLE", "CABLE")
    ref_resolved, ref_idx = resolve_field(cable_defn, "REF_PM", "CABLE")
    if type_idx < 0 or ref_idx < 0:
        report.add_rule("6.5", "E", "FAIL", "CABLE fields not found")
        return

    violations = []
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        ctype = feat.GetField(type_idx)
        if ctype is None or str(ctype).strip().upper() != "DISTRIBUTION":
            continue
        ref_pm = feat.GetField(ref_idx)
        geom = feat.GetGeometryRef()
        if ref_pm is None or geom is None:
            continue
        ref_pm_upper = str(ref_pm).strip().upper()
        zpm_poly = zpm_polys.get(ref_pm_upper)
        if zpm_poly is None:
            violations.append(f"CABLE REF_PM={ref_pm} has no matching ZPM")
            continue
        cable_shape = shape(json.loads(geom.ExportToJson()))
        coords = list(cable_shape.coords)
        for i, (x, y) in enumerate(coords):
            if not zpm_poly.contains(Point(x, y)):
                violations.append(f"CABLE FID={feat.GetFID()} vertex {i} outside ZPM {ref_pm_upper}")
                break

    if violations:
        report.add_rule("6.5", "E", "FAIL", f"{len(violations)} violation(s)")
    else:
        report.add_rule("6.5", "E", "PASS")


def check_6_6_cable_endpoint_coincidence(report, ds, layer_map):
    """6.6: CABLE endpoint self-loop and coincidence checks."""
    if not HAS_SHAPELY:
        report.add_rule("6.6", "E", "FAIL", "shapely not available")
        return
    if "CABLE" not in layer_map:
        report.add_rule("6.6", "E", "FAIL", "CABLE layer missing")
        return

    cable_lyr = layer_map["CABLE"][0]
    cable_defn = cable_lyr.GetLayerDefn()

    orig_resolved, orig_idx = resolve_field(cable_defn, "ORIGINE", "CABLE")
    ext_resolved, ext_idx = resolve_field(cable_defn, "EXTREMITE", "CABLE")
    if orig_idx < 0 or ext_idx < 0:
        report.add_rule("6.6", "E", "FAIL", "ORIGINE or EXTREMITE field not found")
        return

    # Build node geometry lookup (BOITE + SITE PM)
    def _node_xy(ogr_geom):
        """Representative (x, y) for a node: the centroid (identical to the point itself for Point geometries)."""
        c = shape(json.loads(ogr_geom.ExportToJson())).centroid
        return (c.x, c.y)

    node_geoms = {}
    if "BOITE" in layer_map:
        boite_lyr = layer_map["BOITE"][0]
        boite_defn = boite_lyr.GetLayerDefn()
        code_resolved, code_idx = resolve_field(boite_defn, "CODE", "BOITE")
        boite_lyr.ResetReading()
        if code_idx >= 0:
            for feat in boite_lyr:
                code = feat.GetField(code_idx)
                geom = feat.GetGeometryRef()
                if code is not None and geom is not None and not geom.IsEmpty():
                    node_geoms[str(code).strip().upper()] = _node_xy(geom)

    if "SITE" in layer_map:
        site_lyr = layer_map["SITE"][0]
        site_defn = site_lyr.GetLayerDefn()
        type_resolved, type_idx = resolve_field(site_defn, "TYPE", "SITE")
        code_resolved, code_idx = resolve_field(site_defn, "CODE", "SITE")
        site_lyr.ResetReading()
        if code_idx >= 0:
            for feat in site_lyr:
                if type_idx >= 0:
                    stype = feat.GetField(type_idx)
                    if stype is None or str(stype).strip().upper() != "PM":
                        continue
                code = feat.GetField(code_idx)
                geom = feat.GetGeometryRef()
                if code is not None and geom is not None and not geom.IsEmpty():
                    node_geoms[str(code).strip().upper()] = _node_xy(geom)

    violations_null = []
    violations_6a = []
    violations_6b = []
    srs = cable_lyr.GetSpatialRef()
    is_geographic = bool(srs.IsGeographic()) if srs is not None else False
    endpoint_tol = _RUNTIME.get("endpoint_tol", DEFAULT_ENDPOINT_TOL)
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        orig = feat.GetField(orig_idx)
        ext = feat.GetField(ext_idx)
        orig_s = str(orig).strip().upper() if orig is not None else ''
        ext_s = str(ext).strip().upper() if ext is not None else ''

        # Null/empty ORIGINE or EXTREMITE is a violation (rule 4.5 non-null),
        # not a silent skip — eliminates the vacuous-pass hole.
        if orig_s == '' or ext_s == '':
            violations_null.append(f"CABLE FID={feat.GetFID()} null/empty "
                                   f"{'ORIGINE' if orig_s == '' else ''}"
                                   f"{'/' if orig_s == '' and ext_s == '' else ''}"
                                   f"{'EXTREMITE' if ext_s == '' else ''}")

        # 6.6a: self-loop
        if orig_s == ext_s and orig_s != '':
            violations_6a.append(f"CABLE FID={feat.GetFID()} ORIGINE=EXTREMITE={orig_s}")

        # 6.6b: endpoint coincidence
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        gjson = json.loads(geom.ExportToJson())
        if gjson.get("type") != "LineString":
            # Non-line cable geometry has no defined endpoints; geometry-type
            # conformance is rule 1.5's responsibility.
            continue
        coords = gjson["coordinates"]
        start_pt = coords[0]
        end_pt = coords[-1]

        # Check origin
        if orig_s in node_geoms:
            nx, ny = node_geoms[orig_s]
            dist = _crs_distance(start_pt[0], start_pt[1], nx, ny, is_geographic)
            if dist > endpoint_tol:
                violations_6b.append(f"CABLE FID={feat.GetFID()} ORIGINE={orig_s} distance {dist:.6f}")

        # Check extremity
        if ext_s in node_geoms:
            nx, ny = node_geoms[ext_s]
            dist = _crs_distance(end_pt[0], end_pt[1], nx, ny, is_geographic)
            if dist > endpoint_tol:
                violations_6b.append(f"CABLE FID={feat.GetFID()} EXTREMITE={ext_s} distance {dist:.6f}")

    issues = []
    if violations_null:
        issues.append(f"6.6: {len(violations_null)} cable(s) with null/empty ORIGINE or EXTREMITE")
    if violations_6a:
        issues.append(f"6.6a: {len(violations_6a)} self-loop(s)")
    if violations_6b:
        issues.append(f"6.6b: {len(violations_6b)} endpoint coincidence violation(s)")

    if issues:
        report.add_rule("6.6", "E", "FAIL", "; ".join(issues))
    else:
        report.add_rule("6.6", "E", "PASS")


# ── Rule Group 7: Data Validation ─────────────────────────────────────────────

def check_7_1_pbo_capacity(report, ds, layer_map):
    """7.1: For PBO, NB_FIBRE_UTIL <= CAPACITE."""
    if "BOITE" not in layer_map:
        report.add_rule("7.1", "E", "FAIL", "BOITE layer missing")
        return

    boite_lyr = layer_map["BOITE"][0]
    boite_defn = boite_lyr.GetLayerDefn()
    type_resolved, type_idx = resolve_field(boite_defn, "TYPE", "BOITE")
    fibre_resolved, fibre_idx = resolve_field(boite_defn, "NB_FIBRE_UTIL", "BOITE")
    cap_resolved, cap_idx = resolve_field(boite_defn, "CAPACITE", "BOITE")

    if fibre_idx < 0 or cap_idx < 0:
        report.add_rule("7.1", "E", "FAIL", "NB_FIBRE_UTIL or CAPACITE field not found in BOITE")
        return

    violations = []
    boite_lyr.ResetReading()
    for feat in boite_lyr:
        if type_idx >= 0:
            btype = feat.GetField(type_idx)
            if btype is None or str(btype).strip().upper() != "PBO":
                continue
        fibre = feat.GetField(fibre_idx)
        cap = feat.GetField(cap_idx)
        if fibre is not None and cap is not None:
            try:
                if int(fibre) > int(cap):
                    violations.append(f"CODE={feat.GetField(resolve_field(boite_defn, 'CODE', 'BOITE')[1])} FID={feat.GetFID()} NB_FIBRE_UTIL={fibre} > CAPACITE={cap}")
            except (ValueError, TypeError):
                pass

    if violations:
        report.add_rule("7.1", "E", "FAIL", f"{len(violations)} capacity violation(s)")
    else:
        report.add_rule("7.1", "E", "PASS")


def check_7_2_pm_port_balance(report, ds, layer_map):
    """7.2: Per PM, sum(PBO CAPACITE) <= sum(DISTRIBUTION cable CAPACITE from PM)."""
    if "BOITE" not in layer_map or "CABLE" not in layer_map:
        report.add_rule("7.2", "E", "FAIL", "BOITE or CABLE layer missing")
        return

    boite_lyr = layer_map["BOITE"][0]
    boite_defn = boite_lyr.GetLayerDefn()
    cable_lyr = layer_map["CABLE"][0]
    cable_defn = cable_lyr.GetLayerDefn()

    # Accumulate PBO CAPACITE by REF_PM
    type_resolved, type_idx = resolve_field(boite_defn, "TYPE", "BOITE")
    ref_resolved, ref_idx = resolve_field(boite_defn, "REF_PM", "BOITE")
    cap_resolved, cap_idx = resolve_field(boite_defn, "CAPACITE", "BOITE")
    if ref_idx < 0 or cap_idx < 0:
        report.add_rule("7.2", "E", "FAIL", "REF_PM or CAPACITE field not found in BOITE")
        return
    pbo_capacities = defaultdict(int)
    boite_lyr.ResetReading()
    for feat in boite_lyr:
        if type_idx >= 0:
            btype = feat.GetField(type_idx)
            if btype is None or str(btype).strip().upper() != "PBO":
                continue
        ref_pm = feat.GetField(ref_idx)
        cap = feat.GetField(cap_idx)
        if ref_pm is not None and cap is not None:
            try:
                pbo_capacities[str(ref_pm).strip().upper()] += int(cap)
            except (ValueError, TypeError):
                pass

    # Accumulate DISTRIBUTION CABLE CAPACITE by ORIGINE (PM reference)
    type_c_resolved, type_c_idx = resolve_field(cable_defn, "TYPE_CABLE", "CABLE")
    orig_resolved, orig_idx = resolve_field(cable_defn, "ORIGINE", "CABLE")
    cap_c_resolved, cap_c_idx = resolve_field(cable_defn, "CAPACITE", "CABLE")
    if orig_idx < 0 or cap_c_idx < 0:
        report.add_rule("7.2", "E", "FAIL", "ORIGINE or CAPACITE field not found in CABLE")
        return
    cable_capacities = defaultdict(int)
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        if type_c_idx >= 0:
            ctype = feat.GetField(type_c_idx)
            if ctype is None or str(ctype).strip().upper() != "DISTRIBUTION":
                continue
        orig = feat.GetField(orig_idx)
        cap = feat.GetField(cap_c_idx)
        if orig is not None and cap is not None:
            try:
                cable_capacities[str(orig).strip().upper()] += int(cap)
            except (ValueError, TypeError):
                pass

    # Find all PMs
    all_pms = set(pbo_capacities.keys()) | set(cable_capacities.keys())

    violations = []
    for pm in all_pms:
        pbo_sum = pbo_capacities.get(pm, 0)
        cable_sum = cable_capacities.get(pm, 0)
        if pbo_sum > cable_sum:
            violations.append(f"PM {pm}: PBO total={pbo_sum} > CABLE total={cable_sum}")

    if violations:
        report.add_rule("7.2", "E", "FAIL", f"{len(violations)} PM balance violation(s)")
    else:
        report.add_rule("7.2", "E", "PASS")


# ── Rule Group 8: Evidence / Provenance Gate ──────────────────────────────────
#
# Default policy is warning level: violations are recorded with severity "I"
# and status "WARN" so the exit code is unchanged (spec: D-gate first phase).
# With --strict-provenance the same violations become severity "E" FAIL
# (v3-style fail-closed).

# Pipeline-metadata columns on the 8 FC layers; every other attribute column
# is treated as a business field for provenance coverage (rule 8.2).
_NON_BUSINESS_FIELDS = {
    "source_file", "dwg_layer", "dwg_type", "classification_method",
    "annotation_text", "label_provenance",
    "color_aci", "color_rgb", "style_key",
}


def _add_gate_rule(report, rule_id, ok, detail=None):
    """Record an 8.x result honoring the warning/strict provenance policy."""
    strict = _RUNTIME.get("strict_provenance", False)
    severity = "E" if strict else "I"
    if ok:
        report.add_rule(rule_id, severity, "PASS", detail)
    else:
        report.add_rule(rule_id, severity, "FAIL" if strict else "WARN", detail)


def check_8_1_conservation_ledger(report, ds, layer_map):
    """8.1: conservation_ledger present and SUM(entity_count) == expected total."""
    lyr = ds.GetLayerByName(CONSERVATION_TABLE)
    if lyr is None:
        _add_gate_rule(report, "8.1", False, f"{CONSERVATION_TABLE} table not present")
        return
    expected = None
    total = 0
    rows = 0
    lyr.ResetReading()
    for feat in lyr:
        disposition = feat.GetField("disposition")
        count = feat.GetField("entity_count") or 0
        if disposition == META_TOTAL_DISPOSITION:
            expected = int(count)
        else:
            total += int(count)
            rows += 1
    if rows == 0:
        _add_gate_rule(report, "8.1", False, f"{CONSERVATION_TABLE} is empty")
    elif expected is None:
        _add_gate_rule(report, "8.1", False,
                       f"no {META_TOTAL_DISPOSITION} meta row; sum={total} unverifiable")
    elif total != expected:
        _add_gate_rule(report, "8.1", False,
                       f"SUM mismatch: sum={total} != expected={expected}")
    else:
        _add_gate_rule(report, "8.1", True, f"sum={total} == expected ({rows} rows)")


def check_8_2_field_provenance(report, ds, layer_map):
    """8.2: field_provenance covers every non-empty business field per FC layer."""
    lyr = ds.GetLayerByName(PROVENANCE_TABLE)
    if lyr is None:
        _add_gate_rule(report, "8.2", False, f"{PROVENANCE_TABLE} table not present")
        return

    prov_counts = defaultdict(int)
    unknown_kinds = defaultdict(int)
    lyr.ResetReading()
    for feat in lyr:
        fc = feat.GetField("fc")
        field = feat.GetField("field")
        kind = feat.GetField("provenance")
        count = int(feat.GetField("count") or 0)
        prov_counts[(fc, field)] += count
        if kind not in PROVENANCE_KINDS:
            unknown_kinds[kind] += count

    missing = []
    mismatched = []
    covered = 0
    for canonical in sorted(REQUIRED_LAYERS):
        if canonical not in layer_map:
            continue
        fc_lyr = layer_map[canonical][0]
        defn = fc_lyr.GetLayerDefn()
        business_fields = [
            defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())
            if defn.GetFieldDefn(i).GetName() not in _NON_BUSINESS_FIELDS
        ]
        nonnull = defaultdict(int)
        fc_lyr.ResetReading()
        for feat in fc_lyr:
            for fname in business_fields:
                value = feat.GetField(fname)
                if value is not None and not (isinstance(value, str) and value.strip() == ""):
                    nonnull[fname] += 1
        for fname, n in sorted(nonnull.items()):
            recorded = prov_counts.get((canonical, fname))
            if recorded is None:
                missing.append(f"{canonical}.{fname} ({n} non-empty)")
            elif recorded != n:
                mismatched.append(f"{canonical}.{fname} (recorded {recorded} != actual {n})")
            else:
                covered += 1

    issues = []
    if missing:
        issues.append(f"{len(missing)} field(s) without provenance: "
                      + "; ".join(missing[:5]))
    if mismatched:
        issues.append(f"{len(mismatched)} field(s) with count mismatch: "
                      + "; ".join(mismatched[:5]))
    if unknown_kinds:
        issues.append("unknown provenance kind(s): "
                      + "; ".join(f"{k} ({v})" for k, v in sorted(unknown_kinds.items())))
    if issues:
        _add_gate_rule(report, "8.2", False, " | ".join(issues))
    else:
        _add_gate_rule(report, "8.2", True, f"{covered} non-empty business field(s) covered")


def check_8_3_candidates_consistency(report, ds, layer_map):
    """8.3: annotation candidates spot-check against the delivered label result."""
    lyr = ds.GetLayerByName(CANDIDATES_TABLE)
    if lyr is None:
        _add_gate_rule(report, "8.3", False, f"{CANDIDATES_TABLE} table not present")
        return

    code_sets = {}
    for canonical in REQUIRED_LAYERS:
        if canonical in layer_map:
            fc_lyr = layer_map[canonical][0]
            code_sets[canonical] = _build_code_set(
                fc_lyr, fc_lyr.GetLayerDefn(), "CODE", canonical)

    total = 0
    bad_status = defaultdict(int)
    selected_by_key = defaultdict(int)
    unresolved = []
    lyr.ResetReading()
    for feat in lyr:
        total += 1
        status = feat.GetField("status")
        if status not in CANDIDATE_STATUSES:
            bad_status[status] += 1
        if not feat.GetField("selected"):
            continue
        key = feat.GetField("annotation_key")
        if key:
            selected_by_key[key] += 1
        target_fc = feat.GetField("target_fc")
        target_code = feat.GetField("target_code")
        codes = code_sets.get(target_fc)
        if codes is not None and target_code and str(target_code).strip().upper() not in codes:
            unresolved.append(f"{target_fc}:{target_code}")

    multi_selected = [k for k, v in selected_by_key.items() if v > 1]
    issues = []
    if total == 0:
        issues.append(f"{CANDIDATES_TABLE} is empty")
    if bad_status:
        issues.append("invalid status value(s): "
                      + "; ".join(f"{k} ({v})" for k, v in sorted(bad_status.items())))
    if multi_selected:
        issues.append(f"{len(multi_selected)} annotation(s) with >1 selected edge: "
                      + "; ".join(multi_selected[:5]))
    if unresolved:
        issues.append(f"{len(unresolved)} selected edge(s) whose target_code is not a "
                      f"delivered CODE: " + "; ".join(unresolved[:5]))
    if issues:
        _add_gate_rule(report, "8.3", False, " | ".join(issues))
    else:
        _add_gate_rule(report, "8.3", True,
                       f"{total} candidate(s), {sum(selected_by_key.values())} keyed selection(s) consistent")


# ── Domain Vocabulary Validation ──────────────────────────────────────────────

def check_domain_vocabularies(report, layer_map):
    """Validate STATUT, TYPE_CABLE, BOITE.TYPE, SITE.TYPE, PTECH.TYPE, etc. against vocabularies."""
    total_violations = 0
    violated_fields = []

    for canonical, (lyr, _geom_name) in layer_map.items():
        fc_config = FEATURE_CLASS_BY_NAME.get(canonical)
        if not fc_config:
            continue
        lyr_defn = lyr.GetLayerDefn()
        for field_def in fc_config["fields"]:
            fname = field_def["name"]
            domain_values = field_def.get("domain_values")
            if not domain_values:
                # Check FIELD_DOMAIN_MAP for additional fields
                key = f"{canonical}.{fname}"
                if key not in FIELD_DOMAIN_MAP:
                    continue
                vocab_key = FIELD_DOMAIN_MAP[key]
                domain_values = list(DOMAIN_VOCABULARIES.get(vocab_key, []))

            if not domain_values:
                continue

            resolved, idx = resolve_field(lyr_defn, fname, canonical)
            if idx < 0:
                continue

            import unicodedata
            def _norm(s):
                # Uppercase + strip diacritics (Ç→C, É→E, etc.)
                return unicodedata.normalize('NFKD', s.upper()).encode('ASCII', 'ignore').decode()
            domain_set = {_norm(v) for v in domain_values}
            lyr.ResetReading()
            count = 0
            for feat in lyr:
                val = feat.GetField(idx)
                if val is not None:
                    v = str(val).strip()
                    if v != '' and _norm(v) not in domain_set:
                        count += 1

            if count > 0:
                total_violations += count
                violated_fields.append(f"{canonical}.{fname} ({count} violations)")

    if total_violations > 0:
        report.add_rule("domain_vocab", "W", "FAIL",
                        f"{total_violations} domain value violations: {'; '.join(violated_fields[:5])}")
    else:
        report.add_rule("domain_vocab", "W", "PASS")


# ── Extended Mode: Agent 8 Q1-Q5 Metrics ──────────────────────────────────────

def _compute_extended_metrics(report, layer_map, agent_reports_dir):
    """Compute Agent 8 Q1-Q5 metrics from intermediate pipeline data if available."""
    metrics = {}

    # Q1: Feature count per layer
    feature_counts = {}
    for canonical, (lyr, _geom_name) in layer_map.items():
        feature_counts[canonical] = lyr.GetFeatureCount()
    metrics["feature_counts"] = feature_counts

    # Q2-Q5: Try to load from agent report files if available
    if agent_reports_dir and os.path.isdir(agent_reports_dir):
        for fname in os.listdir(agent_reports_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(agent_reports_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                key = os.path.splitext(fname)[0]
                metrics[key] = data
            except (json.JSONDecodeError, IOError):
                continue

    report.summary["extended_metrics"] = metrics


# ── Check function dispatch table ─────────────────────────────────────────────

_CHECK_FUNCTIONS = {
    "check_1_1_layer_presence": check_1_1_layer_presence,
    "check_1_2_imb_naming": check_1_2_imb_naming,
    "check_1_3_site_naming": check_1_3_site_naming,
    "check_1_4_boite_naming": check_1_4_boite_naming,
    "check_1_5_cable_naming": check_1_5_cable_naming,
    "check_1_6_ptech_naming": check_1_6_ptech_naming,
    "check_1_7_infrastructure_naming": check_1_7_infrastructure_naming,
    "check_1_8_znro_naming": check_1_8_znro_naming,
    "check_1_9_zpm_naming": check_1_9_zpm_naming,
    "check_2_0_crs_consistency": check_2_0_crs_consistency,
    "check_3_0_empty_layer": check_3_0_empty_layer,
    "check_4_1_imb_fields": check_4_1_imb_fields,
    "check_4_3_boite_fields": check_4_3_boite_fields,
    "check_4_5_cable_fields": check_4_5_cable_fields,
    "check_4_7_ptech_fields": check_4_7_ptech_fields,
    "check_4_9_infrastructure_fields": check_4_9_infrastructure_fields,
    "check_4_11_zpm_fields": check_4_11_zpm_fields,
    "check_4_13_znro_fields": check_4_13_znro_fields,
    "check_4_15_site_fields": check_4_15_site_fields,
    "check_4_code_uniqueness": check_4_code_uniqueness,
    "check_5_1_site_zpm_isolation": check_5_1_site_zpm_isolation,
    "check_5_2_site_boite_isolation": check_5_2_site_boite_isolation,
    "check_5_3_site_cable_isolation": check_5_3_site_cable_isolation,
    "check_5_4_cable_endpoint_isolation": check_5_4_cable_endpoint_isolation,
    "check_6_1_znro_no_overlap": check_6_1_znro_no_overlap,
    "check_6_2_zpm_no_overlap": check_6_2_zpm_no_overlap,
    "check_6_3_site_in_zpm": check_6_3_site_in_zpm,
    "check_6_4_boite_in_zpm": check_6_4_boite_in_zpm,
    "check_6_5_cable_in_zpm": check_6_5_cable_in_zpm,
    "check_6_6_cable_endpoint_coincidence": check_6_6_cable_endpoint_coincidence,
    "check_7_1_pbo_capacity": check_7_1_pbo_capacity,
    "check_7_2_pm_port_balance": check_7_2_pm_port_balance,
}


# ── Main Orchestrator ─────────────────────────────────────────────────────────

def run_verification(gpkg_path, extended=False, agent_reports_dir=None,
                     expected_crs=DEFAULT_EXPECTED_CRS, endpoint_tol=DEFAULT_ENDPOINT_TOL,
                     strict_provenance=False, allow_ptech_endpoints=False):
    """
    Run the full FTTH GIS verification pipeline.

    Args:
        gpkg_path: Path to the GeoPackage to verify.
        extended: If True, compute Agent 8 Q1-Q5 extended metrics.
        agent_reports_dir: Directory with intermediate pipeline JSON reports.
        expected_crs: CRS all layers must match (e.g. "EPSG:3857"); "any"/"none"
            disables the expected check, leaving only cross-layer consistency.
        endpoint_tol: Rule 6.6 endpoint coincidence tolerance — metres for
            geographic layers (haversine), CRS units for projected layers.
        strict_provenance: If True, rule group 8.x violations are FAIL
            (fail-closed); default False records them as WARN without
            changing the exit code.
        allow_ptech_endpoints: If True, rule 5.4.1 counts CABLE endpoints
            resolving to PTECH pole codes as fully resolved; default False
            reports them as a separate informational count (never a
            violation either way).

    Returns:
        VerificationReport with per-rule results and summary verdict.
    """
    _RUNTIME["expected_crs"] = expected_crs
    _RUNTIME["endpoint_tol"] = endpoint_tol
    _RUNTIME["strict_provenance"] = strict_provenance
    _RUNTIME["allow_ptech_endpoints"] = allow_ptech_endpoints

    if not os.path.isfile(gpkg_path):
        print(f"ERROR: GeoPackage not found: {gpkg_path}", file=sys.stderr)
        sys.exit(2)

    report = VerificationReport(gpkg_path)

    ds = ogr.Open(gpkg_path, 0)  # read-only
    if ds is None:
        print(f"ERROR: Cannot open GeoPackage: {gpkg_path}", file=sys.stderr)
        sys.exit(2)

    try:
        layer_map = _build_layer_map(ds)

        # Execute all verification rules in order
        for rule_def in VERIFICATION_RULES:
            func_name = rule_def["check_function"]
            func = _CHECK_FUNCTIONS.get(func_name)
            if func is None:
                continue
            func(report, ds, layer_map)

        # Domain vocabulary validation (WARNING level)
        check_domain_vocabularies(report, layer_map)

        # Rule Group 8: Evidence / Provenance gate (warning level by default;
        # --strict-provenance escalates violations to FAIL)
        check_8_1_conservation_ledger(report, ds, layer_map)
        check_8_2_field_provenance(report, ds, layer_map)
        check_8_3_candidates_consistency(report, ds, layer_map)

        # Extended metrics (Agent 8)
        if extended:
            _compute_extended_metrics(report, layer_map, agent_reports_dir)

    finally:
        ds = None  # close

    return report.finalize()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="FTTH GIS Verification Engine — FiberHome Project 2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Rule Groups:
  1 — File Integrity (layer presence, geometry, naming)
  2 — CRS Consistency (cross-layer + configurable expected CRS)
  3 — Empty Layer Check
  4 — Field Existence & Non-Null + CODE Uniqueness
  5 — Referential Integrity / Isolation
  6 — Geometric Checks (overlap, containment, coincidence)
  7 — Data Validation (capacity, port balance)
  8 — Evidence / Provenance Gate (warning level; --strict-provenance -> FAIL)

Exit codes: 0=PASS, 1=FAIL, 2=QUARANTINE
        """
    )
    parser.add_argument("--gpkg", required=True,
                        help="Path to GeoPackage to verify (e.g., output/FiberHome_P2_FTTH.gpkg)")
    parser.add_argument("--expected-crs", default=DEFAULT_EXPECTED_CRS,
                        help="Expected CRS for all layers (default EPSG:3857); "
                             "'any' or 'none' checks cross-layer consistency only")
    parser.add_argument("--endpoint-tol", type=float, default=DEFAULT_ENDPOINT_TOL,
                        help="Rule 6.6 endpoint coincidence tolerance: metres for geographic "
                             "layers, CRS units for projected layers (default 1.0)")
    parser.add_argument("--output", "-o", default=None,
                        help="Write verification report JSON to this path")
    parser.add_argument("--extended", action="store_true",
                        help="Enable extended mode (Agent 8 Q1-Q5 metrics)")
    parser.add_argument("--agent-reports-dir", default=None,
                        help="Directory with intermediate pipeline JSON reports (for extended mode)")
    parser.add_argument("--strict-provenance", action="store_true",
                        help="Escalate rule group 8.x (evidence/provenance) violations "
                             "from WARN to FAIL (fail-closed)")
    parser.add_argument("--allow-ptech-endpoints", action="store_true",
                        help="Rule 5.4.1: count CABLE endpoints resolving to PTECH pole "
                             "codes as resolved (default: separate INFO count, "
                             "not a violation)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress per-rule console output")

    args = parser.parse_args()

    report = run_verification(
        args.gpkg,
        extended=args.extended,
        agent_reports_dir=args.agent_reports_dir,
        expected_crs=args.expected_crs,
        endpoint_tol=args.endpoint_tol,
        strict_provenance=args.strict_provenance,
        allow_ptech_endpoints=args.allow_ptech_endpoints,
    )

    # Console output
    if not args.quiet:
        print(f"GeoPackage: {report.gpkg}")
        print(f"Rules executed: {len(report.rules)}")
        print(f"  Passed: {report.summary['rules_passed']}")
        print(f"  Failed: {report.summary['rules_failed']}")
        print(f"Verdict: {report.summary['verdict']}")
        print(f"  Errors:   {report.summary['errors']}")
        print(f"  Warnings: {report.summary['warnings']}")
        print(f"  Info:     {report.summary['info']}")
        print()

        # Print failed rules
        failed = [(rid, entry) for rid, entry in report.rules.items() if entry["status"] != "PASS"]
        if failed:
            print("Failed rules:")
            for rid, entry in failed:
                sev = entry["severity"]
                detail = entry.get("detail", "")
                print(f"  [{sev}] {rid}: {detail}")

    # JSON output
    output_path = args.output
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"\nReport written to: {output_path}")

    # Exit code
    verdict = report.summary["verdict"]
    if verdict == "FAIL":
        sys.exit(1)
    elif verdict == "QUARANTINE":
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
