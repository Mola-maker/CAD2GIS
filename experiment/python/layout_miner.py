#!/usr/bin/env python3
"""
Layout Miner — FTTH CAD-to-GIS Pipeline (spec component C)
==========================================================
Paper-space layout channel for LibreDWG (WSL side, no AutoCAD COM):

  1. Layout enumeration + role classification (regex family transplanted from
     newmodel autocad_reader.py:157-172, adapted so "FDT LAYOUT" is classified
     as equipment, never as a plan sheet).
  2. Layout entity mining: TEXT/MTEXT/INSERT+ATTRIB read from each layout's
     *Paper_Space block (entities are attribute/annotation evidence only —
     geometry is never taken from paper space).
  3. Layout facts: FDT_ID / FAT_SEQUENCE attribute harvest per plan/topology
     layout (transplant of newmodel topology.py:140-164 semantics).
  4. Layout <-> CABLE connected-component matching with abstention semantics
     (unique best match in BOTH directions or no assignment; the newmodel
     "len(components) == len(layouts)" hard assertion is deliberately NOT
     transplanted — spec Non-Goals).
  5. topology_evidence records for TOPOLOGY layouts (written to the GPKG as an
     attribute-only side table, never into the 8 FC delivery layers).

Usage:
  python layout_miner.py --dwg FILE.dwg [--json-cache dump.json]
      [--gpkg output/FILE.gpkg] [--write-evidence]
      [--fdt-attr FDT_ID] [--fat-attr FAT_SEQUENCE] [--metrics out.json]
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

DWGREAD_BIN = os.environ.get("DWGREAD_BIN", "dwgread")

DEFAULT_ATTR_CONFIG = {
    "fdt_id_attribute": "FDT_ID",
    "fat_sequence_attribute": "FAT_SEQUENCE",
}

EVIDENCE_TABLE = "topology_evidence"

# ── Role classification (regex family, order matters) ────────────────────────
# Transplanted from newmodel autocad_reader.py:157-172. topology/legend/
# equipment are tested BEFORE plan so that "FDT-01 TOPOLOGY" and "FDT LAYOUT"
# never fall into the plan bucket.
ROLE_PATTERNS = (
    ("topology", re.compile(r"(?i)(TOPOLOGY|SPLICING|SCHEMATIC|DIAGRAM)")),
    ("legend", re.compile(r"(?i)(LEGEND|CABLE[ _-]*TYPE|SYMBOL)")),
    ("equipment", re.compile(r"(?i)(FDT[ _-]*LAYOUT|EQUIPMENT)")),
    ("plan", re.compile(r"(?i)^(FDT[-_ ]?(ALL|\d+)|PLAN|NETWORK)")),
)

_SPLICING = re.compile(r"(?i)SPLICING")

TEXT_ENTITIES = ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")


def classify_layout_role(layout_name):
    name = (layout_name or "").strip()
    if name.casefold() == "model":
        return "model"
    for role, pattern in ROLE_PATTERNS:
        if pattern.search(name):
            return role
    return "other"


def layout_disposition(role, layout_name):
    """Delivery disposition per spec: plan -> FDT_ID tagging source, TOPOLOGY
    sheets -> evidence table, SPLICING/legend/equipment -> excluded from any
    QGIS delivery, Model -> geometry source."""
    if role == "model":
        return "geometry_source"
    if role == "plan":
        return "fdt_tagging"
    if role == "topology":
        return "exclude" if _SPLICING.search(layout_name or "") else "evidence"
    return "exclude"


# ── dwgread JSON channel ──────────────────────────────────────────────────────

def run_dwgread(dwg_path, json_cache=None):
    """Dump the DWG to JSON via the dwgread CLI and parse it.

    json_cache: optional path; if it already exists it is reused, otherwise
    dwgread writes there (kept for later runs). Without a cache path a
    temporary file is used and removed.
    """
    if json_cache and os.path.exists(json_cache) and os.path.getsize(json_cache) > 0:
        with open(json_cache, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)

    if not os.path.exists(dwg_path):
        raise FileNotFoundError(dwg_path)

    out_path = json_cache
    tmp = None
    if out_path is None:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="dwgread_")
        os.close(fd)
        out_path = tmp
    try:
        proc = subprocess.run(
            [DWGREAD_BIN, "-O", "json", "-o", out_path, dwg_path],
            capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not os.path.exists(out_path) \
                or os.path.getsize(out_path) == 0:
            raise RuntimeError(
                f"dwgread failed (rc={proc.returncode}): {proc.stderr[-500:]}")
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.remove(tmp)


def _handle_value(ref):
    """dwgread handles are [code, size, value(, absolute)] — last item is the
    resolvable absolute value."""
    if isinstance(ref, list) and ref:
        return ref[-1]
    return None


def index_objects(dump):
    """Return (by_handle, layouts, layer_names) indexes over dump['OBJECTS']."""
    objects = dump.get("OBJECTS", [])
    by_handle = {}
    layouts = []
    layer_names = {}
    for obj in objects:
        h = _handle_value(obj.get("handle"))
        if h is not None:
            by_handle[h] = obj
        kind = obj.get("object")
        if kind == "LAYOUT":
            layouts.append(obj)
        elif kind == "LAYER":
            layer_names[h] = obj.get("name")
    layouts.sort(key=lambda o: o.get("tab_order", 0))
    return by_handle, layouts, layer_names


def _entity_type(obj):
    return obj.get("entity") or obj.get("object")


def _color_fields(obj):
    color = obj.get("color")
    if not isinstance(color, dict):
        return None, None
    rgb = color.get("rgb")
    if isinstance(rgb, str) and len(rgb) == 8:
        rgb = rgb[-6:]
    return color.get("index"), rgb


def enumerate_layouts(dump):
    """List the drawing's layouts with role, disposition and block linkage."""
    by_handle, layouts, _ = index_objects(dump)
    result = []
    for layout in layouts:
        name = layout.get("layout_name") or layout.get("name") or ""
        role = classify_layout_role(name)
        bh_handle = _handle_value(layout.get("block_header"))
        block_header = by_handle.get(bh_handle)
        n_entities = len(block_header.get("entities", [])) if block_header else 0
        result.append({
            "layout_name": name,
            "role": role,
            "disposition": layout_disposition(role, name),
            "tab_order": layout.get("tab_order"),
            "layout_handle": _handle_value(layout.get("handle")),
            "block_header_handle": bh_handle,
            "block_name": block_header.get("name") if block_header else None,
            "entity_count": n_entities,
        })
    return result


# ── Layout entity mining ──────────────────────────────────────────────────────

def mine_layout_entities(dump, layout_names=None):
    """Extract per-layout entity records from each layout's *Paper_Space block.

    Returns {layout_name: [record, ...]} where each record carries
    entity_type / block_name / text / attributes / layer / color / handle.
    Attributes and annotation only — no geometry is harvested here.
    """
    by_handle, layouts, layer_names = index_objects(dump)
    wanted = set(layout_names) if layout_names else None
    mined = {}
    for layout in layouts:
        name = layout.get("layout_name") or layout.get("name") or ""
        if wanted is not None and name not in wanted:
            continue
        block_header = by_handle.get(_handle_value(layout.get("block_header")))
        records = []
        for ref in (block_header or {}).get("entities", []):
            ent = by_handle.get(_handle_value(ref))
            if ent is None:
                continue
            records.append(_entity_record(ent, name, by_handle, layer_names))
        mined[name] = records
    return mined


def _entity_record(ent, layout_name, by_handle, layer_names):
    etype = _entity_type(ent)
    color_index, color_rgb = _color_fields(ent)
    record = {
        "layout": layout_name,
        "entity_type": etype,
        "handle": _handle_value(ent.get("handle")),
        "layer": layer_names.get(_handle_value(ent.get("layer"))),
        "color_index": color_index,
        "color_rgb": color_rgb,
        "block_name": None,
        "text": None,
        "attributes": {},
        "ins_pt": None,
    }
    ins = ent.get("ins_pt")
    if isinstance(ins, list) and len(ins) >= 2:
        record["ins_pt"] = [ins[0], ins[1]]

    if etype == "TEXT":
        record["text"] = ent.get("text_value")
    elif etype == "MTEXT":
        record["text"] = ent.get("text")
    elif etype == "INSERT":
        block = by_handle.get(_handle_value(ent.get("block_header")))
        record["block_name"] = block.get("name") if block else None
        attrs = {}
        for aref in ent.get("attribs") or []:
            attrib = by_handle.get(_handle_value(aref))
            if attrib is None:
                continue
            tag = (attrib.get("tag") or "").strip()
            if tag:
                attrs[tag.upper()] = attrib.get("text_value")
        record["attributes"] = attrs
    return record


# ── Layout facts (transplant of newmodel topology.py:140-164) ─────────────────

def extract_layout_facts(mined, layout_roles, attr_config=None):
    """Harvest FDT_ID / FAT_SEQUENCE facts from plan+topology layout records.

    Unlike the newmodel original, layouts with ambiguous facts are not
    silently dropped: every mined layout gets a fact record with a `usable`
    flag and a reason, so downstream abstention is auditable.
    """
    cfg = dict(DEFAULT_ATTR_CONFIG)
    if attr_config:
        cfg.update(attr_config)
    fdt_attr = cfg["fdt_id_attribute"].upper()
    fat_attr = cfg["fat_sequence_attribute"].upper()

    facts = {}
    for layout_name, records in mined.items():
        role = layout_roles.get(layout_name)
        if role not in ("plan", "topology"):
            continue
        fdt_ids, sequences, evidence = set(), set(), []
        for rec in records:
            attrs = rec.get("attributes") or {}
            hit = False
            fdt_val = (attrs.get(fdt_attr) or "").strip()
            if fdt_val:
                fdt_ids.add(fdt_val.upper())
                hit = True
            fat_val = (attrs.get(fat_attr) or "").strip()
            if fat_val:
                sequences.add(fat_val.upper())
                hit = True
            if hit:
                evidence.append(rec["handle"])
        if len(fdt_ids) == 1 and sequences:
            usable, reason = True, None
        elif not fdt_ids and not sequences:
            usable, reason = False, "no_attributes"
        elif len(fdt_ids) != 1:
            usable, reason = False, f"fdt_id_not_unique({len(fdt_ids)})"
        else:
            usable, reason = False, "no_sequences"
        facts[layout_name] = {
            "layout": layout_name,
            "role": role,
            "fdt_ids": sorted(fdt_ids),
            "fdt_id": next(iter(fdt_ids)) if len(fdt_ids) == 1 else None,
            "sequences": sorted(sequences),
            "sequence_count": len(sequences),
            "evidence_handles": evidence,
            "usable": usable,
            "reason": reason,
        }
    return facts


# ── topology_evidence records ─────────────────────────────────────────────────

def build_topology_evidence(mined, layout_infos, include_dispositions=("evidence",)):
    """Flatten TOPOLOGY-sheet content into evidence records for the GPKG side
    table (never part of the 8 FC delivery layers)."""
    info_by_name = {li["layout_name"]: li for li in layout_infos}
    records = []
    for layout_name, ent_records in mined.items():
        info = info_by_name.get(layout_name)
        if info is None or info["disposition"] not in include_dispositions:
            continue
        for rec in ent_records:
            records.append({
                "layout_name": layout_name,
                "layout_role": info["role"],
                "entity_type": rec["entity_type"],
                "block_name": rec["block_name"],
                "layer": rec["layer"],
                "color_index": rec["color_index"],
                "color_rgb": rec["color_rgb"],
                "text": rec["text"],
                "attributes_json": json.dumps(rec["attributes"], ensure_ascii=False)
                                   if rec["attributes"] else None,
                "src_handle": rec["handle"],
            })
    return records


def write_topology_evidence_table(gpkg_path, records, table_name=EVIDENCE_TABLE):
    """(Re)write evidence records as an attribute-only GPKG table."""
    from osgeo import ogr
    ogr.UseExceptions()
    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage for update: {gpkg_path}")
    try:
        for i in range(ds.GetLayerCount()):
            if ds.GetLayerByIndex(i).GetName() == table_name:
                ds.DeleteLayer(i)
                break
        lyr = ds.CreateLayer(table_name, geom_type=ogr.wkbNone)
        for fname in ("layout_name", "layout_role", "entity_type", "block_name",
                      "layer", "color_rgb", "text", "attributes_json"):
            lyr.CreateField(ogr.FieldDefn(fname, ogr.OFTString))
        lyr.CreateField(ogr.FieldDefn("color_index", ogr.OFTInteger))
        lyr.CreateField(ogr.FieldDefn("src_handle", ogr.OFTInteger64))
        defn = lyr.GetLayerDefn()
        for rec in records:
            feat = ogr.Feature(defn)
            for key in ("layout_name", "layout_role", "entity_type", "block_name",
                        "layer", "color_rgb", "text", "attributes_json"):
                if rec.get(key) is not None:
                    feat.SetField(key, str(rec[key]))
            if rec.get("color_index") is not None:
                feat.SetField("color_index", int(rec["color_index"]))
            if rec.get("src_handle") is not None:
                feat.SetField("src_handle", int(rec["src_handle"]))
            lyr.CreateFeature(feat)
            feat = None
    finally:
        ds = None
    return len(records)


# ── CABLE component extraction from a written GPKG ───────────────────────────

def extract_components_from_gpkg(gpkg_path,
                                 edge_layers=("CABLE",),
                                 node_layers=("BOITE", "PTECH", "SITE"),
                                 endpoint_tol=0.001,
                                 label_fields=("display_label", "CODE")):
    """Build CABLE connected components and collect their label evidence.

    Union-find over edge endpoints: endpoints sharing an ORIGINE/EXTREMITE
    code are one node; otherwise endpoints within endpoint_tol are merged
    geometrically (post-snap endpoints coincide). Component labels are the
    referenced node codes plus display_label/CODE of matching node features.

    Returns [{"component_id", "labels", "edge_count", "asset_count"}, ...]
    where asset_count is the number of distinct BOITE codes in the component.
    """
    from osgeo import ogr
    ogr.UseExceptions()
    ds = ogr.Open(gpkg_path, 0)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage: {gpkg_path}")

    def find_layer(canonical):
        for i in range(ds.GetLayerCount()):
            lyr = ds.GetLayerByIndex(i)
            base = lyr.GetName().strip().upper().strip("_-.")
            if base == canonical or base.endswith(f"_{canonical}") \
                    or base.endswith(canonical):
                return lyr
        return None

    # node code -> (fc, display_label)
    node_info = {}
    for fc in node_layers:
        lyr = find_layer(fc)
        if lyr is None:
            continue
        defn = lyr.GetLayerDefn()
        idxs = {f: defn.GetFieldIndex(f) for f in ("CODE",) + tuple(label_fields)}
        lyr.ResetReading()
        for feat in lyr:
            code_i = idxs.get("CODE", -1)
            code = feat.GetField(code_i) if code_i >= 0 else None
            if code is None or not str(code).strip():
                continue
            code = str(code).strip().upper()
            labels = set()
            for f in label_fields:
                i = idxs.get(f, -1)
                if i >= 0:
                    val = feat.GetField(i)
                    if val is not None and str(val).strip():
                        labels.add(str(val).strip().upper())
            node_info[code] = (fc, labels)

    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    grid = defaultdict(list)  # spatial hash for endpoint merging
    cell = max(endpoint_tol, 1e-9)

    def geo_key(x, y):
        gx, gy = math.floor(x / cell), math.floor(y / cell)
        key = ("pt", round(x, 9), round(y, 9))
        if key not in parent:
            parent[key] = key
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (ox, oy, okey) in grid[(gx + dx, gy + dy)]:
                        if math.hypot(ox - x, oy - y) <= endpoint_tol:
                            union(key, okey)
            grid[(gx, gy)].append((x, y, key))
        return key

    edges = []  # (edge_id, node_key_a, node_key_b, codes)
    for fc in edge_layers:
        lyr = find_layer(fc)
        if lyr is None:
            continue
        defn = lyr.GetLayerDefn()
        oi = defn.GetFieldIndex("ORIGINE")
        ei = defn.GetFieldIndex("EXTREMITE")
        lyr.ResetReading()
        for feat in lyr:
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty() \
                    or ogr.GT_Flatten(geom.GetGeometryType()) != ogr.wkbLineString:
                continue
            pts = geom.GetPoints()
            if not pts or len(pts) < 2:
                continue
            keys, codes = [], set()
            for pos, field_idx in ((0, oi), (-1, ei)):
                x, y = pts[pos][0], pts[pos][1]
                key = geo_key(x, y)
                code = feat.GetField(field_idx) if field_idx >= 0 else None
                if code is not None and str(code).strip():
                    code = str(code).strip().upper()
                    codes.add(code)
                    ckey = ("code", code)
                    parent.setdefault(ckey, ckey)
                    union(key, ckey)
                keys.append(key)
            edges.append((f"{fc}:{feat.GetFID()}", keys[0], keys[1], codes))
    ds = None

    for _, ka, kb, _ in edges:
        union(ka, kb)

    comp_edges = defaultdict(list)
    comp_codes = defaultdict(set)
    for edge_id, ka, kb, codes in edges:
        root = find(ka)
        comp_edges[root].append(edge_id)
        comp_codes[root].update(codes)

    components = []
    roots = sorted(comp_edges, key=lambda r: -len(comp_edges[r]))
    for idx, root in enumerate(roots, start=1):
        labels, asset_count = set(comp_codes[root]), 0
        for code in comp_codes[root]:
            info = node_info.get(code)
            if info:
                fc, extra = info
                labels.update(extra)
                if fc == "BOITE":
                    asset_count += 1
        components.append({
            "component_id": f"COMP-{idx:02d}",
            "labels": sorted(labels),
            "edge_count": len(comp_edges[root]),
            "asset_count": asset_count,
        })
    return components


# ── Layout <-> component matching with abstention ─────────────────────────────

def _match_score(labels, fact):
    """(exact FAT-sequence hits, fdt-prefix-only hits) combined score."""
    sequences = set(fact["sequences"])
    fdt_id = fact["fdt_id"]
    exact = sum(1 for lab in labels if lab in sequences)
    prefix = 0
    if fdt_id:
        for lab in labels:
            if lab not in sequences and (lab == fdt_id or lab.startswith(fdt_id + ".")):
                prefix += 1
    return 2 * exact + prefix, exact, prefix


def match_components_to_layouts(components, layout_facts):
    """Unique-best matching between CABLE components and plan-layout facts.

    Semantics transplanted from newmodel topology.py:187-201 WITHOUT the
    "len(groups) == len(layouts)" hard assertion (spec Non-Goals): every
    non-unique or evidence-free pairing abstains with a recorded reason.

    components: [{"component_id", "labels", "asset_count"?, ...}]
    layout_facts: output of extract_layout_facts (only usable plan facts are
    considered; topology sheets duplicate plan attributes and would make every
    match ambiguous).
    Returns {"assignments": [...], "abstentions": [...], "layouts_unmatched": [...]}
    """
    usable = {name: f for name, f in layout_facts.items()
              if f["usable"] and f["role"] == "plan"}
    assignments, abstentions = [], []

    scored = {}  # component_id -> sorted [(score, exact, prefix, layout_name, fact)]
    for comp in components:
        labels = {str(l).strip().upper() for l in comp.get("labels", []) if str(l).strip()}
        ranked = []
        for name, fact in usable.items():
            score, exact, prefix = _match_score(labels, fact)
            ranked.append((score, exact, prefix, name, fact))
        ranked.sort(key=lambda t: (-t[0], t[3]))
        scored[comp["component_id"]] = ranked

    def _cardinality_gap(comp, fact):
        if comp.get("asset_count") is None:
            return None
        return abs(comp["asset_count"] - fact["sequence_count"])

    provisional = {}
    for comp in components:
        cid = comp["component_id"]
        ranked = scored[cid]
        if not ranked or ranked[0][0] <= 0:
            abstentions.append({
                "component_id": cid, "status": "no_evidence",
                "detail": "no label overlaps any usable plan-layout fact",
            })
            continue
        best = ranked[0]
        ties = [r for r in ranked if r[0] == best[0]]
        if len(ties) > 1:
            # secondary evidence: FAT cardinality gap must isolate a unique winner
            gaps = [(_cardinality_gap(comp, r[4]), r) for r in ties]
            if any(g is None for g, _ in gaps):
                gaps = []
            gaps.sort(key=lambda t: t[0])
            if gaps and gaps[0][0] < gaps[1][0]:
                best = gaps[0][1]
            else:
                abstentions.append({
                    "component_id": cid, "status": "multiple_optima",
                    "detail": f"tied layouts: {[r[3] for r in ties]}",
                })
                continue
        provisional[cid] = best

    # reverse uniqueness: a layout claimed by several components is contested
    claims = defaultdict(list)
    for cid, best in provisional.items():
        claims[best[3]].append((best[0], cid))
    for layout_name, claimants in claims.items():
        claimants.sort(reverse=True)
        if len(claimants) > 1 and claimants[0][0] == claimants[1][0]:
            for _, cid in claimants:
                abstentions.append({
                    "component_id": cid, "status": "contested_layout",
                    "detail": f"layout {layout_name} claimed with equal score by "
                              f"{[c for _, c in claimants]}",
                })
            continue
        winner_score, winner_cid = claimants[0]
        for score, cid in claimants[1:]:
            abstentions.append({
                "component_id": cid, "status": "outscored_on_layout",
                "detail": f"layout {layout_name} assigned to {winner_cid} "
                          f"(score {winner_score} > {score})",
            })
        best = provisional[winner_cid]
        assignments.append({
            "component_id": winner_cid,
            "layout": best[3],
            "fdt_id": best[4]["fdt_id"],
            "score": best[0],
            "exact_sequence_hits": best[1],
            "fdt_prefix_hits": best[2],
            "layout_sequence_count": best[4]["sequence_count"],
        })

    matched_layouts = {a["layout"] for a in assignments}
    layouts_unmatched = sorted(set(usable) - matched_layouts)
    return {
        "assignments": sorted(assignments, key=lambda a: a["component_id"]),
        "abstentions": abstentions,
        "layouts_unmatched": layouts_unmatched,
    }


# ── Orchestrator ──────────────────────────────────────────────────────────────

def mine_dwg(dwg_path, json_cache=None, attr_config=None):
    """Full layout-mining pass: enumeration, entity mining, facts, evidence.

    Returns {"layouts", "mined_counts", "facts", "evidence", "mined"}.
    """
    dump = run_dwgread(dwg_path, json_cache=json_cache)
    layouts = enumerate_layouts(dump)
    mined = mine_layout_entities(dump)
    roles = {li["layout_name"]: li["role"] for li in layouts}
    facts = extract_layout_facts(mined, roles, attr_config=attr_config)
    evidence = build_topology_evidence(mined, layouts)
    mined_counts = {
        name: {
            "entities": len(records),
            "texts": sum(1 for r in records if r["text"]),
            "attributed_inserts": sum(1 for r in records if r["attributes"]),
            "attributes": sum(len(r["attributes"]) for r in records),
        }
        for name, records in mined.items()
    }
    return {
        "dwg": dwg_path,
        "layouts": layouts,
        "mined_counts": mined_counts,
        "facts": facts,
        "evidence": evidence,
        "mined": mined,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_report(result, match_result=None):
    print("== Layout roles ==")
    print(f"{'layout':<20} {'role':<10} {'disposition':<16} {'entities':>8}")
    for li in result["layouts"]:
        print(f"{li['layout_name']:<20} {li['role']:<10} "
              f"{li['disposition']:<16} {li['entity_count']:>8}")

    print("\n== Mined attribute counts ==")
    for name, c in result["mined_counts"].items():
        print(f"{name:<20} entities={c['entities']:<5} texts={c['texts']:<5} "
              f"attributed_inserts={c['attributed_inserts']:<4} "
              f"attributes={c['attributes']}")

    print("\n== Layout facts (plan/topology) ==")
    for name, f in result["facts"].items():
        print(f"{name:<20} usable={f['usable']} fdt_id={f['fdt_id']} "
              f"sequences={f['sequence_count']} reason={f['reason']}")

    print(f"\n== topology_evidence records: {len(result['evidence'])} ==")

    if match_result is not None:
        print("\n== Layout <-> component matching ==")
        for a in match_result["assignments"]:
            print(f"ASSIGNED  {a['component_id']} -> {a['layout']} "
                  f"(FDT_ID={a['fdt_id']}, score={a['score']}, "
                  f"exact={a['exact_sequence_hits']}, prefix={a['fdt_prefix_hits']})")
        for ab in match_result["abstentions"]:
            print(f"ABSTAIN   {ab['component_id']}: {ab['status']} — {ab['detail']}")
        if match_result["layouts_unmatched"]:
            print(f"UNMATCHED layouts: {match_result['layouts_unmatched']}")


def main():
    parser = argparse.ArgumentParser(
        description="Paper-space layout miner: roles, FDT/FAT facts, "
                    "topology evidence, layout<->component matching")
    parser.add_argument("--dwg", required=True, help="Source DWG file")
    parser.add_argument("--json-cache", default=None,
                        help="Reuse/persist the dwgread JSON dump here")
    parser.add_argument("--gpkg", default=None,
                        help="GeoPackage for component matching / evidence write")
    parser.add_argument("--write-evidence", action="store_true",
                        help="Write topology_evidence table into --gpkg")
    parser.add_argument("--fdt-attr", default=DEFAULT_ATTR_CONFIG["fdt_id_attribute"])
    parser.add_argument("--fat-attr", default=DEFAULT_ATTR_CONFIG["fat_sequence_attribute"])
    parser.add_argument("--endpoint-tol", type=float, default=0.001,
                        help="Endpoint merge tolerance in CRS units")
    parser.add_argument("--metrics", default=None, help="Write metrics JSON here")
    args = parser.parse_args()

    attr_config = {"fdt_id_attribute": args.fdt_attr,
                   "fat_sequence_attribute": args.fat_attr}
    result = mine_dwg(args.dwg, json_cache=args.json_cache, attr_config=attr_config)

    match_result = None
    if args.gpkg:
        components = extract_components_from_gpkg(
            args.gpkg, endpoint_tol=args.endpoint_tol)
        print(f"Components from {args.gpkg}: "
              f"{[(c['component_id'], c['edge_count'], c['asset_count']) for c in components]}")
        match_result = match_components_to_layouts(components, result["facts"])

    _print_report(result, match_result)

    if args.write_evidence:
        if not args.gpkg:
            parser.error("--write-evidence requires --gpkg")
        n = write_topology_evidence_table(args.gpkg, result["evidence"])
        print(f"\nWrote {n} records to {EVIDENCE_TABLE} in {args.gpkg}")

    if args.metrics:
        metrics = {
            "dwg": result["dwg"],
            "layouts": result["layouts"],
            "mined_counts": result["mined_counts"],
            "facts": {k: {kk: vv for kk, vv in v.items() if kk != "evidence_handles"}
                      for k, v in result["facts"].items()},
            "evidence_records": len(result["evidence"]),
            "matching": match_result,
        }
        with open(args.metrics, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
