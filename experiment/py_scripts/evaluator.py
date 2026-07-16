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
  2 — CRS Consistency (2.0): all layers EPSG:4326
  3 — Empty Layer (3.0): all 8 layers have >=1 feature
  4 — Field Existence & Non-Null (4.1-4.16): mandatory field + CODE uniqueness
  5 — Referential Integrity / Isolation (5.1-5.4): FK bidirectional checks
  6 — Geometric Checks (6.1-6.6): overlap, containment, endpoint coincidence
  7 — Data Validation (7.1-7.2): capacity + PM port balance

Usage:
  python evaluator.py --gpkg output/FiberHome_P2_FTTH.gpkg [--output report.json]
  python evaluator.py --gpkg ... --extended --agent-reports-dir <path> [--quiet]
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict

from osgeo import ogr

from schema_config import (
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

from domain_vocab import validate_domain_value

# ── Geometry imports ──────────────────────────────────────────────────────────
from shapely.geometry import Point, LineString, Polygon, shape
from shapely.strtree import STRtree
HAS_SHAPELY = True

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

def check_2_0_crs_consistency(report, ds, layer_map):
    """2.0: All layers must be EPSG:4326."""
    bad_layers = []
    for canonical, (lyr, _geom_name) in layer_map.items():
        srs = lyr.GetSpatialRef()
        if srs is None:
            bad_layers.append(f"{canonical} (no CRS)")
            continue
        authority = srs.GetAuthorityCode("GEOGCS") or srs.GetAuthorityCode(None) or srs.GetAuthorityCode("PROJCS")
        if authority != "4326":
            bad_layers.append(f"{canonical} (EPSG:{authority})")
    if bad_layers:
        report.add_rule("2.0", "C", "FAIL", f"Non-EPSG:4326 layers: {', '.join(bad_layers)}")
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

    all_endpoint_targets = boite_codes | site_pm_codes

    type_resolved, type_idx = resolve_field(cable_defn, "TYPE_CABLE", "CABLE")
    orig_resolved, orig_idx = resolve_field(cable_defn, "ORIGINE", "CABLE")
    ext_resolved, ext_idx = resolve_field(cable_defn, "EXTREMITE", "CABLE")

    if orig_idx < 0 or ext_idx < 0:
        report.add_rule("5.4", "E", "FAIL", "ORIGINE or EXTREMITE field not found in CABLE")
        return

    # 5.4.1 Forward: Each DISTRIBUTION CABLE.ORIGINE/EXTREMITE must exist as BOITE.CODE or SITE(PM).CODE
    unresolved_origins = []
    unresolved_ends = []
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        if type_idx >= 0:
            ctype = feat.GetField(type_idx)
            if ctype is None or str(ctype).strip().upper() != "DISTRIBUTION":
                continue
        orig = feat.GetField(orig_idx)
        ext = feat.GetField(ext_idx)
        if orig is not None and str(orig).strip() != '':
            if str(orig).strip().upper() not in all_endpoint_targets:
                unresolved_origins.append(f"{orig} (FID={feat.GetFID()})")
        if ext is not None and str(ext).strip() != '':
            if str(ext).strip().upper() not in all_endpoint_targets:
                unresolved_ends.append(f"{ext} (FID={feat.GetFID()})")

    if unresolved_origins or unresolved_ends:
        issues.append(f"5.4.1: {len(unresolved_origins)} unresolved ORIGINE, {len(unresolved_ends)} unresolved EXTREMITE")

    # 5.4.2 Reverse A: Every PM SITE.CODE appears in >= 1 DISTRIBUTION CABLE ORIGINE/EXTREMITE
    cable_endpoint_pm_refs = set()
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        if type_idx >= 0:
            ctype = feat.GetField(type_idx)
            if ctype is None or str(ctype).strip().upper() != "DISTRIBUTION":
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
        report.add_rule("5.4", "E", "FAIL", "; ".join(issues))
    else:
        report.add_rule("5.4", "E", "PASS")


# ── Rule Group 6: Geometric Checks ────────────────────────────────────────────

def _haversine_distance(lon1, lat1, lon2, lat2):
    """Haversine distance in degrees (approximate for small distances)."""
    return math.sqrt((lon2 - lon1) ** 2 + (lat2 - lat1) ** 2)


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
                if code is not None and geom is not None:
                    g = json.loads(geom.ExportToJson())
                    node_geoms[str(code).strip().upper()] = (g["coordinates"][0], g["coordinates"][1])

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
                if code is not None and geom is not None:
                    g = json.loads(geom.ExportToJson())
                    node_geoms[str(code).strip().upper()] = (g["coordinates"][0], g["coordinates"][1])

    violations_6a = []
    violations_6b = []
    cable_lyr.ResetReading()
    for feat in cable_lyr:
        orig = feat.GetField(orig_idx)
        ext = feat.GetField(ext_idx)
        if orig is None or ext is None:
            continue
        orig_s = str(orig).strip().upper()
        ext_s = str(ext).strip().upper()

        # 6.6a: self-loop
        if orig_s == ext_s and orig_s != '':
            violations_6a.append(f"CABLE FID={feat.GetFID()} ORIGINE=EXTREMITE={orig_s}")

        # 6.6b: endpoint coincidence
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        gjson = json.loads(geom.ExportToJson())
        coords = gjson["coordinates"]
        start_pt = coords[0]
        end_pt = coords[-1]

        # Check origin
        if orig_s in node_geoms:
            nx, ny = node_geoms[orig_s]
            dist = _haversine_distance(start_pt[0], start_pt[1], nx, ny)
            if dist > 0.0001:
                violations_6b.append(f"CABLE FID={feat.GetFID()} ORIGINE={orig_s} distance {dist:.6f}")

        # Check extremity
        if ext_s in node_geoms:
            nx, ny = node_geoms[ext_s]
            dist = _haversine_distance(end_pt[0], end_pt[1], nx, ny)
            if dist > 0.0001:
                violations_6b.append(f"CABLE FID={feat.GetFID()} EXTREMITE={ext_s} distance {dist:.6f}")

    issues = []
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

def run_verification(gpkg_path, extended=False, agent_reports_dir=None):
    """
    Run the full FTTH GIS verification pipeline.

    Args:
        gpkg_path: Path to the GeoPackage to verify.
        extended: If True, compute Agent 8 Q1-Q5 extended metrics.
        agent_reports_dir: Directory with intermediate pipeline JSON reports.

    Returns:
        VerificationReport with per-rule results and summary verdict.
    """
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
  2 — CRS Consistency (EPSG:4326)
  3 — Empty Layer Check
  4 — Field Existence & Non-Null + CODE Uniqueness
  5 — Referential Integrity / Isolation
  6 — Geometric Checks (overlap, containment, coincidence)
  7 — Data Validation (capacity, port balance)

Exit codes: 0=PASS, 1=FAIL, 2=QUARANTINE
        """
    )
    parser.add_argument("--gpkg", required=True,
                        help="Path to GeoPackage to verify (e.g., output/FiberHome_P2_FTTH.gpkg)")
    parser.add_argument("--output", "-o", default=None,
                        help="Write verification report JSON to this path")
    parser.add_argument("--extended", action="store_true",
                        help="Enable extended mode (Agent 8 Q1-Q5 metrics)")
    parser.add_argument("--agent-reports-dir", default=None,
                        help="Directory with intermediate pipeline JSON reports (for extended mode)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress per-rule console output")

    args = parser.parse_args()

    report = run_verification(
        args.gpkg,
        extended=args.extended,
        agent_reports_dir=args.agent_reports_dir,
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
