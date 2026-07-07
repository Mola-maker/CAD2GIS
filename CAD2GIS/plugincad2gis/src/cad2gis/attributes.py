"""Structured attribute extraction (story G11c — accuracy maximization, attribute dimension).

Comms labels encode structured specs that a GIS should hold as typed fields, not opaque strings.
The nearest-text the hit-vector already captured (e.g. a duct symbol's `3孔PVC110`) parses into:
  holes=3, material=PVC, diameter_mm=110  — a duct-bank spec.
Manhole/point IDs (WC-108, T131, DX027) and cable/route metrics parse similarly. Filling these
required schema fields is exactly what the attribute-completeness accuracy dimension measures.

Pure regex — deterministic, no guessing. Unparseable labels leave fields empty (never fabricated).
"""
from __future__ import annotations

import re
from typing import Optional

# 3孔PVC110 / 12孔PVC110 / 3孔BD100 / 6孔PVC110  -> holes, material, diameter(mm)
_DUCT_RE = re.compile(r"(?P<holes>\d+)\s*孔\s*(?P<mat>PVC|BD|PE|HDPE|BWFRP|管)?\s*(?P<dia>\d+)?")
# DN300 / DN400-L=30m-i=3‰  -> nominal diameter + optional length + slope
_DN_RE = re.compile(r"DN\s*(?P<dn>\d+)(?:-L=(?P<len>\d+\.?\d*)m)?(?:-i=(?P<slope>\d+\.?\d*)‰)?")
# point / node IDs: WC-108, T131, DX027, WE5, AX007
_ID_RE = re.compile(r"^(?:WC-?\d+|T\d+|DX\d+|WE\d+|AX\d+|GPS\w*|KZD\w*)$", re.IGNORECASE)


def parse_duct_spec(text: Optional[str]) -> dict:
    """Parse a duct-bank spec label into structured fields. Returns {} if nothing matches."""
    if not text:
        return {}
    out: dict = {}
    m = _DUCT_RE.search(text)
    if m and m.group("holes"):
        out["holes"] = int(m.group("holes"))
        if m.group("mat"):
            out["material"] = m.group("mat")
        if m.group("dia"):
            out["diameter_mm"] = int(m.group("dia"))
    dn = _DN_RE.search(text)
    if dn:
        out["dn_mm"] = int(dn.group("dn"))
        if dn.group("len"):
            out["run_length_m"] = float(dn.group("len"))
        if dn.group("slope"):
            out["slope_permille"] = float(dn.group("slope"))
    if out:
        out["spec"] = text
    return out


def parse_point_id(text: Optional[str]) -> dict:
    """Parse a node/control-point ID label (WC-108, T131, DX027...). Returns {} if not an ID."""
    if not text:
        return {}
    t = text.strip()
    if _ID_RE.match(t):
        return {"point_id": t}
    return {}


def enrich_feature(f) -> int:
    """Populate structured attribute fields on one Feature from its captured label evidence.

    Reads the hit-vector's matched_label (or the feature's own text/nearest_text) and fills spec
    fields for ducts and IDs for point features. Returns the number of fields added (for QC).
    """
    added = 0
    ev = f.attributes.get("_map_evidence") or {}
    label = ev.get("matched_label") or f.attributes.get("text") or f.attributes.get("nearest_text")

    if f.feature_class == "duct":
        spec = parse_duct_spec(label)
        for k, v in spec.items():
            if f.attributes.get(k) in (None, ""):
                f.attributes[k] = v
                added += 1
    if f.feature_class in ("manhole", "control_point"):
        pid = parse_point_id(label) or parse_point_id(f.attributes.get("text"))
        for k, v in pid.items():
            if f.attributes.get(k) in (None, ""):
                f.attributes[k] = v
                added += 1
    return added


def enrich_collection(coll) -> int:
    """Enrich every feature in the collection; return total fields added."""
    total = 0
    for f in coll.features:
        total += enrich_feature(f)
    return total
