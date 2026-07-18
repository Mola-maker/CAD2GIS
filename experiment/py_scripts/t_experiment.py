#!/usr/bin/env python3
"""
T-component three-way topology experiment driver (analysis only, no
production code changes).

Arms (all run on the same exclusions-effective base gpkg):
  A  skip-chain     — previous-release equivalent: raw fragments + graded snap
  B  no-bridge      — chaining with node cut + 0.5 m weld, gap bridge OFF
  C  constrained    — gap bridge kept but only same dwg_layer + both-end
                      continuation direction dot >= cos(30 deg)
  D  free-bridge    — current production behaviour (distance-only bridge),
                      included as the like-for-like degraded reference

Each arm: copy base -> [chain] -> graded snap repair -> FDT tagging ->
metric extraction (cross-species joins, FDT_ID coverage, component
morphology, LINK preservation).

Usage:
  python3 t_experiment.py --base /tmp/t_exp/base.gpkg \
      --dwg "../APD - ....dwg" --workdir /tmp/t_exp [--arms A,B,C,D]
  python3 t_experiment.py --validate --base /tmp/raw_frags.gpkg
"""

import argparse
import json
import math
import os
import shutil
import sys
import time
from collections import defaultdict, Counter

from osgeo import ogr

ogr.UseExceptions()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topology_builder as tb

COS30 = math.cos(math.radians(30.0))


# ── Instrumented chain (copy of tb.chain_edges + species/direction filters) ──

def chain_edges_x(ds, chain_tol, node_capture_tol, bridge_mode="free",
                  bridge_tol=None, angle_cos=COS30,
                  edge_layers=("CABLE",), node_layers=tb.NODE_LAYERS):
    """Instrumented variant of topology_builder.chain_edges.

    bridge_mode: "off" | "free" (production distance-only) | "constrained"
    (same dwg_layer + both-end continuation cos >= angle_cos).

    Returns (metrics, detail) where detail carries the bridge log and the
    per-chain species composition.
    """
    sx, sy = tb._metric_scale(ds, layers=tuple(edge_layers) + tuple(node_layers))
    node_pts = [(x * sx, y * sy) for x, y in tb._collect_node_points(ds, node_layers)]
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    node_tree = STRtree([Point(x, y) for x, y in node_pts]) if node_pts else None

    def node_distance(x, y):
        if node_tree is None:
            return float("inf")
        nx, ny = node_pts[int(node_tree.nearest(Point(x, y)))]
        return math.hypot(nx - x, ny - y)

    metrics = {"input_fragments": 0, "node_splits": 0, "parts_after_split": 0,
               "output_segments": 0, "chains_merged": 0,
               "fragments_absorbed": 0, "gap_bridges": 0,
               "new_features_created": 0,
               "junctions": {"total": 0, "node_cut": 0, "degree_cut": 0,
                             "pass_through": 0},
               "longest_chain_fragments": 0}
    detail = {"bridge_log": [], "bridge_rejects": {"species": 0, "angle": 0},
              "mixed_species_chains": [], "chains_total": 0}

    for fc in edge_layers:
        lyr = tb._find_layer(ds, fc)
        if lyr is None:
            continue
        code_idx = tb._field_idx(lyr, "CODE")
        long_idx = tb._field_idx(lyr, "LONGUEUR")
        src_idx = tb._ensure_field(lyr, tb.CHAIN_SOURCE_FIELD)
        code_idx = tb._field_idx(lyr, "CODE")
        layer_idx = tb._field_idx(lyr, "dwg_layer")

        originals = {}
        used_codes = set()
        lyr.ResetReading()
        for feat in lyr:
            code = feat.GetField(code_idx) if code_idx >= 0 else None
            if code:
                used_codes.add(str(code))
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty() \
                    or ogr.GT_Flatten(geom.GetGeometryType()) != ogr.wkbLineString:
                continue
            coords = [(p[0] * sx, p[1] * sy) for p in geom.GetPoints()]
            if len(coords) < 2:
                continue
            dwg_layer = feat.GetField(layer_idx) if layer_idx >= 0 else None
            originals[feat.GetFID()] = {"coords": coords, "code": code,
                                        "layer": dwg_layer or ""}
        metrics["input_fragments"] += len(originals)

        # phase 1: node split
        parts = []
        min_spacing = max(chain_tol, 1e-6)
        for fid, orig in originals.items():
            coords = orig["coords"]
            xs = [p[0] for p in coords]
            ys = [p[1] for p in coords]
            pad = node_capture_tol
            nearby = [(nx, ny) for nx, ny in node_pts
                      if min(xs) - pad <= nx <= max(xs) + pad
                      and min(ys) - pad <= ny <= max(ys) + pad]
            pieces = tb._split_coords_at_nodes(coords, nearby, node_capture_tol,
                                               min_spacing)
            metrics["node_splits"] += len(pieces) - 1
            for k, piece in enumerate(pieces):
                parts.append({"parent": fid, "coords": piece,
                              "length": tb._line_length(piece, False),
                              "part_no": k + 1, "n_parts": len(pieces),
                              "layer": orig["layer"]})
        metrics["parts_after_split"] += len(parts)

        # phase 2: weld + (optional) gap bridge
        clusters = tb._Clusters(chain_tol)
        for part in parts:
            clusters.add(*part["coords"][0])
            clusters.add(*part["coords"][-1])

        def build_incident():
            part_ends, incident, cluster_xy = {}, defaultdict(list), {}
            for pid, part in enumerate(parts):
                ca = clusters.root(*part["coords"][0])
                cb = clusters.root(*part["coords"][-1])
                part_ends[pid] = (ca, cb)
                incident[ca].append((pid, 0))
                incident[cb].append((pid, 1))
                cluster_xy.setdefault(ca, part["coords"][0])
                cluster_xy.setdefault(cb, part["coords"][-1])
            return part_ends, incident, cluster_xy

        part_ends, incident, cluster_xy = build_incident()

        def end_direction(pid, end):
            c = parts[pid]["coords"]
            if end == 0:
                ax, ay = c[0]
                bx, by = c[1]
            else:
                ax, ay = c[-1]
                bx, by = c[-2]
            dx, dy = ax - bx, ay - by
            n = math.hypot(dx, dy)
            return (dx / n, dy / n) if n > 0 else (0.0, 0.0)

        eff_bridge_tol = None
        if bridge_mode != "off":
            eff_bridge_tol = bridge_tol if bridge_tol is not None else node_capture_tol
        if eff_bridge_tol and eff_bridge_tol > chain_tol:
            open_ends = []
            for cid, ends in incident.items():
                if len(ends) != 1:
                    continue
                x, y = cluster_xy[cid]
                nd = node_distance(x, y)
                if nd > node_capture_tol:
                    open_ends.append((cid, x, y, nd, ends[0][0], ends[0][1]))
            cell = eff_bridge_tol
            bgrid = defaultdict(list)
            for i, oe in enumerate(open_ends):
                bgrid[(math.floor(oe[1] / cell), math.floor(oe[2] / cell))].append(i)
            candidates = []
            for i, (ca, xa, ya, nda, pa, ea) in enumerate(open_ends):
                gx, gy = math.floor(xa / cell), math.floor(ya / cell)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for j in bgrid[(gx + dx, gy + dy)]:
                            if j <= i:
                                continue
                            cb, xb, yb, ndb, pb, eb = open_ends[j]
                            d = math.hypot(xb - xa, yb - ya)
                            if not (d <= eff_bridge_tol and d < nda and d < ndb):
                                continue
                            la, lb = parts[pa]["layer"], parts[pb]["layer"]
                            if d > 0:
                                vx, vy = (xb - xa) / d, (yb - ya) / d
                                ua = end_direction(pa, ea)
                                ub = end_direction(pb, eb)
                                cos_a = ua[0] * vx + ua[1] * vy
                                cos_b = -(ub[0] * vx + ub[1] * vy)
                            else:
                                cos_a = cos_b = 1.0
                            if bridge_mode == "constrained":
                                if la != lb:
                                    detail["bridge_rejects"]["species"] += 1
                                    continue
                                if cos_a < angle_cos or cos_b < angle_cos:
                                    detail["bridge_rejects"]["angle"] += 1
                                    continue
                            candidates.append((round(d, 3), i, j,
                                               la, lb, cos_a, cos_b))
            candidates.sort(key=lambda t: (t[0], t[1], t[2]))
            used = set()
            for d, i, j, la, lb, cos_a, cos_b in candidates:
                if i in used or j in used:
                    continue
                ca, cb = open_ends[i][0], open_ends[j][0]
                if incident[ca][0][0] == incident[cb][0][0]:
                    continue
                clusters._union(ca, cb)
                used.add(i)
                used.add(j)
                metrics["gap_bridges"] += 1
                detail["bridge_log"].append({
                    "d": d, "layer_a": la, "layer_b": lb,
                    "cross_species": la != lb,
                    "cos_a": round(cos_a, 3), "cos_b": round(cos_b, 3)})
            if used:
                part_ends, incident, cluster_xy = build_incident()

        pass_through = set()
        for cid, ends in incident.items():
            metrics["junctions"]["total"] += 1
            if len(ends) != 2 or ends[0][0] == ends[1][0]:
                metrics["junctions"]["degree_cut"] += 1
                continue
            x, y = cluster_xy[cid]
            if node_distance(x, y) <= node_capture_tol:
                metrics["junctions"]["node_cut"] += 1
                continue
            pass_through.add(cid)
            metrics["junctions"]["pass_through"] += 1

        # phase 3: chain walk
        def other_end(pid, cid):
            ca, cb = part_ends[pid]
            return cb if cid == ca else ca

        visited = set()
        chains = []
        for pid in range(len(parts)):
            if pid in visited:
                continue
            visited.add(pid)
            chain = [pid]
            for direction in (1, 0):
                cid = part_ends[pid][direction]
                cur = pid
                while cid in pass_through:
                    a, b = incident[cid]
                    nxt = b[0] if a[0] == cur else a[0]
                    if nxt in visited:
                        break
                    visited.add(nxt)
                    if direction == 1:
                        chain.append(nxt)
                    else:
                        chain.insert(0, nxt)
                    cid = other_end(nxt, cid)
                    cur = nxt
            chains.append(chain)

        detail["chains_total"] += len(chains)
        for chain in chains:
            species = sorted({parts[p]["layer"] for p in chain})
            if len(species) > 1:
                detail["mixed_species_chains"].append({
                    "n_fragments": len(chain), "species": species})

        # write back
        def part_label(pid):
            part = parts[pid]
            base = str(originals[part["parent"]]["code"]
                       or f"fid:{part['parent']}")
            if part["n_parts"] > 1:
                return f"{base}#p{part['part_no']}"
            return base

        def merged_coords(chain):
            pts = None
            for i, pid in enumerate(chain):
                coords = parts[pid]["coords"]
                if i == 0:
                    if len(chain) > 1:
                        shared = None
                        for cid in part_ends[pid]:
                            if cid in part_ends[chain[1]]:
                                shared = cid
                                break
                        if shared is not None \
                                and clusters.root(*coords[-1]) != shared:
                            coords = list(reversed(coords))
                    pts = list(coords)
                    continue
                tail = pts[-1]
                d_first = math.hypot(coords[0][0] - tail[0],
                                     coords[0][1] - tail[1])
                d_last = math.hypot(coords[-1][0] - tail[0],
                                    coords[-1][1] - tail[1])
                if d_last < d_first:
                    coords = list(reversed(coords))
                    d_first = d_last
                pts.extend(coords[1:] if d_first <= chain_tol else coords)
            return pts

        def unique_split_code(base):
            n = 1
            candidate = f"{base}-S{n}"
            while candidate in used_codes:
                n += 1
                candidate = f"{base}-S{n}"
            used_codes.add(candidate)
            return candidate

        defn = lyr.GetLayerDefn()
        reused_fids = set()
        order = sorted(range(len(chains)),
                       key=lambda ci: -sum(parts[p]["length"] for p in chains[ci]))
        for ci in order:
            chain = chains[ci]
            pts = merged_coords(chain)
            longest_pid = max(chain, key=lambda p: parts[p]["length"])
            parent_fid = parts[longest_pid]["parent"]
            sources = ",".join(part_label(p) for p in chain)
            length = round(tb._line_length(pts, False), 3)

            new_geom = ogr.Geometry(ogr.wkbLineString)
            for x, y in pts:
                new_geom.AddPoint_2D(x / sx, y / sy)

            if parent_fid not in reused_fids:
                feat = lyr.GetFeature(parent_fid)
                feat.SetGeometry(new_geom)
                if long_idx >= 0:
                    feat.SetField(long_idx, length)
                feat.SetField(src_idx, sources)
                lyr.SetFeature(feat)
                reused_fids.add(parent_fid)
            else:
                parent = lyr.GetFeature(parent_fid)
                feat = ogr.Feature(defn)
                feat.SetFrom(parent)
                feat.SetFID(-1)
                feat.SetGeometry(new_geom)
                if code_idx >= 0:
                    base = originals[parent_fid]["code"] or f"{fc}{parent_fid}"
                    feat.SetField(code_idx, unique_split_code(str(base)))
                if long_idx >= 0:
                    feat.SetField(long_idx, length)
                feat.SetField(src_idx, sources)
                lyr.CreateFeature(feat)
                metrics["new_features_created"] += 1

            if len(chain) > 1:
                metrics["chains_merged"] += 1
                metrics["fragments_absorbed"] += len(chain) - 1
            metrics["output_segments"] += 1
            metrics["longest_chain_fragments"] = max(
                metrics["longest_chain_fragments"], len(chain))

        for fid in originals:
            if fid not in reused_fids:
                lyr.DeleteFeature(fid)

    return metrics, detail


def chain_edges_x_gpkg(gpkg_path, chain_tol, node_capture_tol, **kw):
    ds = ogr.Open(gpkg_path, 1)
    ds.StartTransaction()
    try:
        metrics, detail = chain_edges_x(ds, chain_tol, node_capture_tol, **kw)
        ds.CommitTransaction()
    except Exception:
        ds.RollbackTransaction()
        raise
    finally:
        ds = None
    return metrics, detail


# ── Post-pipeline graph analysis ─────────────────────────────────────────────

def analyze_gpkg(gpkg_path, endpoint_tol=0.5):
    """Connected-component morphology + species/domain mixing + FDT coverage
    + LINK preservation, computed the same way tag_fdt_domains builds its
    vertex space (ORIGINE/EXTREMITE codes first, geometric clusters else).

    endpoint_tol is the geometric clustering tolerance for coding-less
    endpoints. 0.5 m (weld tol) measures the actually-delivered topology;
    5.0 m reproduces the FDT-flood adjacency the production tagger sees
    (which re-connects any gap <= 5 m regardless of bridging)."""
    ds = ogr.Open(gpkg_path)
    sx, sy = tb._metric_scale(ds)

    node_codes = set()
    for fc in tb.NODE_LAYERS:
        lyr = tb._find_layer(ds, fc)
        if lyr is None:
            continue
        ci = tb._field_idx(lyr, "CODE")
        if ci < 0:
            continue
        lyr.ResetReading()
        for feat in lyr:
            c = feat.GetField(ci)
            if c and str(c).strip():
                node_codes.add(str(c).strip().upper())

    clusters = tb._Clusters(endpoint_tol)
    cab = tb._find_layer(ds, "CABLE")
    oi, ei = tb._field_idx(cab, "ORIGINE"), tb._field_idx(cab, "EXTREMITE")
    li = tb._field_idx(cab, "dwg_layer")
    fi = tb._field_idx(cab, "FDT_ID")
    lgi = tb._field_idx(cab, "LONGUEUR")
    cdi = tb._field_idx(cab, "CODE")

    edges = []
    cab.ResetReading()
    for feat in cab:
        geom = feat.GetGeometryRef()
        if geom is None or geom.IsEmpty() \
                or ogr.GT_Flatten(geom.GetGeometryType()) != ogr.wkbLineString:
            continue
        pts = geom.GetPoints()
        vk = []
        for pos, idx in ((0, oi), (-1, ei)):
            code = feat.GetField(idx) if idx >= 0 else None
            code = str(code).strip().upper() if code and str(code).strip() else None
            if code and code in node_codes:
                vk.append(("n", code))
            else:
                vk.append(("g", clusters.add(pts[pos][0] * sx, pts[pos][1] * sy)))
        edges.append({
            "fid": feat.GetFID(),
            "code": feat.GetField(cdi) if cdi >= 0 else None,
            "a": vk[0], "b": vk[1],
            "layer": (feat.GetField(li) or "") if li >= 0 else "",
            "fdt": feat.GetField(fi) if fi >= 0 else None,
            "length": feat.GetField(lgi) if lgi >= 0 else None,
        })
    n_cable = cab.GetFeatureCount()
    ds = None

    for e in edges:
        for k in ("a", "b"):
            if e[k][0] == "g":
                e[k] = ("g", clusters._find(e[k][1]))

    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        ra, rb = find(e["a"]), find(e["b"])
        if ra != rb:
            parent[ra] = rb

    comps = defaultdict(list)
    for e in edges:
        comps[find(e["a"])].append(e)

    comp_stats = []
    for root, members in comps.items():
        species = sorted({m["layer"] for m in members})
        domains = sorted({m["fdt"] for m in members if m["fdt"]})
        comp_stats.append({
            "n_edges": len(members),
            "species": species,
            "domains": domains,
            "length": round(sum(m["length"] or 0 for m in members), 1)})
    comp_stats.sort(key=lambda c: -c["n_edges"])

    fdt_dist = Counter((e["fdt"] or "<null>") for e in edges)
    total_len = sum(e["length"] or 0 for e in edges)
    null_len = sum(e["length"] or 0 for e in edges if not e["fdt"])
    links = [e for e in edges if e["fdt"] == "LINK"]
    mixed_species = [c for c in comp_stats if len(c["species"]) > 1]
    cross_domain = [c for c in comp_stats
                    if len([d for d in c["domains"] if d != "LINK"]) > 1]

    largest = comp_stats[0] if comp_stats else None
    return {
        "cable_features": n_cable,
        "graph_edges": len(edges),
        "components": len(comp_stats),
        "largest_component": largest,
        "top5_sizes": [c["n_edges"] for c in comp_stats[:5]],
        "mixed_species_components": len(mixed_species),
        "cross_domain_components": len(cross_domain),
        "cross_domain_details": cross_domain[:5],
        "fdt_distribution": dict(fdt_dist),
        "fdt_null_rate": round(fdt_dist.get("<null>", 0) / len(edges), 4) if edges else None,
        "fdt_null_length_rate": round(null_len / total_len, 4) if total_len else None,
        "total_length_m": round(total_len, 1),
        "link_segments": [{"fid": e["fid"], "code": e["code"],
                           "layer": e["layer"], "length": e["length"]}
                          for e in links],
    }


# ── Arm runner ───────────────────────────────────────────────────────────────

ARMS = {
    "A": {"label": "skip-chain (prev-release equivalent)", "chain": None},
    "B": {"label": "no-bridge (node cut + 0.5m weld)",
          "chain": {"bridge_mode": "off"}},
    "C": {"label": "constrained bridge (same layer + cos30)",
          "chain": {"bridge_mode": "constrained", "bridge_tol": 5.0}},
    "D": {"label": "free bridge (current production)",
          "chain": {"bridge_mode": "free", "bridge_tol": 5.0}},
}


def mine_domain_prefixes(dwg_path, cache_path=None):
    if cache_path and os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    import layout_miner
    mined = layout_miner.mine_dwg(dwg_path)
    prefixes = {name: fact["fdt_id"] for name, fact in mined["facts"].items()
                if fact["usable"] and fact["role"] == "plan"}
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(prefixes, f, ensure_ascii=False, indent=2)
    return prefixes


def run_arm(arm, base_gpkg, workdir, domain_prefixes,
            chain_tol=0.5, snap_tol=5.0, isolation=30.0):
    cfg = ARMS[arm]
    out = os.path.join(workdir, f"arm_{arm}.gpkg")
    shutil.copyfile(base_gpkg, out)
    t0 = time.time()
    result = {"arm": arm, "label": cfg["label"], "gpkg": out}

    if cfg["chain"] is not None:
        metrics, detail = chain_edges_x_gpkg(
            out, chain_tol=chain_tol, node_capture_tol=snap_tol, **cfg["chain"])
        result["chain_metrics"] = metrics
        result["bridge_rejects"] = detail["bridge_rejects"]
        blog = detail["bridge_log"]
        result["bridges"] = len(blog)
        result["cross_species_bridges"] = sum(1 for b in blog if b["cross_species"])
        result["mixed_species_chains"] = len(detail["mixed_species_chains"])
        result["mixed_species_chain_details"] = detail["mixed_species_chains"][:10]
        result["bridge_log_sample"] = blog[:20]
    else:
        result["chain_metrics"] = None
        result["bridges"] = 0
        result["cross_species_bridges"] = 0
        result["mixed_species_chains"] = 0

    repair = tb.repair_gpkg(out, snap_tol=snap_tol, isolation_threshold=isolation)
    result["endpoints"] = repair["endpoints"]
    result["floating_cables"] = repair["network"]["floating_cables"]

    if domain_prefixes:
        fdt = tb.tag_fdt_domains_gpkg(out, domain_prefixes, endpoint_tol=snap_tol)
        result["fdt_metrics"] = fdt

    result["analysis"] = analyze_gpkg(out, endpoint_tol=chain_tol)
    result["analysis_flood_5m"] = analyze_gpkg(out, endpoint_tol=snap_tol)
    result["runtime_s"] = round(time.time() - t0, 1)
    return result


def summary_table(results):
    rows = []
    header = ("arm", "CABLE", "bridges", "x-species-bridge", "mixed-chains",
              "comp@0.5m", "largest", "largest-domains", "FDT-null%",
              "null-len%", "LINK")
    rows.append(header)
    for r in results:
        a = r["analysis"]
        lg = a["largest_component"] or {}
        rows.append((
            f"{r['arm']} {r['label'][:28]}",
            a["cable_features"],
            r["bridges"],
            r["cross_species_bridges"],
            r["mixed_species_chains"],
            a["components"],
            lg.get("n_edges"),
            "+".join(lg.get("domains", [])) or "-",
            f"{100 * (a['fdt_null_rate'] or 0):.1f}",
            f"{100 * (a['fdt_null_length_rate'] or 0):.1f}",
            len(a["link_segments"]),
        ))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(header))]
    lines = []
    for k, row in enumerate(rows):
        lines.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
        if k == 0:
            lines.append("  ".join("-" * widths[i] for i in range(len(header))))
    return "\n".join(lines)


def validate(base_gpkg, workdir):
    """Fidelity check: instrumented constrained mode must reproduce the
    production constrained bridge (topology_builder gap_bridge=True) on the
    same base. (Production free mode was removed by the T fix; the original
    free-mode parity run against the pre-fix code is recorded in
    guide/T_TOPOLOGY_REPAIR_ANALYSIS.md §2.3.)"""
    os.makedirs(workdir, exist_ok=True)
    prod = os.path.join(workdir, "val_prod.gpkg")
    mine = os.path.join(workdir, "val_inst.gpkg")
    shutil.copyfile(base_gpkg, prod)
    shutil.copyfile(base_gpkg, mine)
    m_prod = tb.chain_edges_gpkg(prod, chain_tol=0.5, node_capture_tol=5.0,
                                 gap_bridge=True, bridge_tol=5.0)
    m_inst, detail = chain_edges_x_gpkg(mine, chain_tol=0.5, node_capture_tol=5.0,
                                        bridge_mode="constrained", bridge_tol=5.0)
    keys = ("input_fragments", "node_splits", "parts_after_split",
            "output_segments", "gap_bridges", "chains_merged",
            "fragments_absorbed", "new_features_created")
    diff = {k: (m_prod[k], m_inst[k]) for k in keys if m_prod[k] != m_inst[k]}
    print("validation:", "IDENTICAL" if not diff else f"DIFF {diff}")
    print("  production:", {k: m_prod[k] for k in keys})
    print("  instrumented:", {k: m_inst[k] for k in keys})
    print("  cross-species bridges (instrumented):",
          sum(1 for b in detail["bridge_log"] if b["cross_species"]),
          "of", len(detail["bridge_log"]))
    return 0 if not diff else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True,
                    help="Base gpkg produced by converter --skip-topology")
    ap.add_argument("--dwg", default=None,
                    help="Source DWG for layout-fact mining (FDT prefixes)")
    ap.add_argument("--workdir", default="/tmp/t_exp")
    ap.add_argument("--arms", default="A,B,C,D")
    ap.add_argument("--out", default=None, help="Results JSON path")
    ap.add_argument("--validate", action="store_true",
                    help="Only run the instrumented-vs-production fidelity check")
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    if args.validate:
        return validate(args.base, args.workdir)

    domain_prefixes = {}
    if args.dwg:
        domain_prefixes = mine_domain_prefixes(
            args.dwg, cache_path=os.path.join(args.workdir, "layout_facts.json"))
        print("domain prefixes:", domain_prefixes)

    results = []
    for arm in [a.strip().upper() for a in args.arms.split(",") if a.strip()]:
        print(f"\n=== arm {arm}: {ARMS[arm]['label']} ===")
        r = run_arm(arm, args.base, args.workdir, domain_prefixes)
        results.append(r)
        a = r["analysis"]
        print(f"  CABLE={a['cable_features']} bridges={r['bridges']} "
              f"(x-species {r['cross_species_bridges']}) "
              f"mixed-chains={r['mixed_species_chains']} "
              f"components={a['components']} largest={a['largest_component']}")
        print(f"  FDT: {a['fdt_distribution']} null-rate={a['fdt_null_rate']}")
        print(f"  LINK segments: {a['link_segments']}")

    print("\n" + summary_table(results))
    out = args.out or os.path.join(args.workdir, "t_experiment_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nresults JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
