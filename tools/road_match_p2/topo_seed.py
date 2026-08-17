# -*- coding: utf-8 -*-
"""topo_seed.py — Li & Briggs 式拓扑点模式匹配，为路网定位生成假设种子（P2）。

方法（点模式匹配 / 图不变量播种，替代暴力网格扫描）：
  1. 缆线汇合点图：CABLE 线要素端点 snap 聚类 + 线线内部交点分裂，
     度数≥3 的节点为汇合点；记录不变量 (度数, 交会边方位角序列, 边比例距离)。
  2. OSM 路口图：按共享 node id 计度数（way 内部经过计 2，端点计 1），
     度数≥3 为路口；再把间距 ≤CLUSTER_M 的路口合并为"超路口"（吸收短连接边，
     修正 OSM 把一个物理路口拆成多个相邻节点的情况）。
  3. 基线配对（主通道，需缆线汇合点 ≥2）：缆线汇合点对 (c1,c2) 的距离是不变量
     （scale 锁 1）；对每个有序 OSM 超路口对 (o1,o2)，若 |d(o1,o2)−d(c1,c2)| 在容差内，
     则 θ = bearing(o1→o2) − bearing(c1→c2)，t = o1 − R(θ)·c1，
     再用两端点的交会边方位角序列做子集匹配验证打分。
  4. 单点配对（兜底通道，缆线汇合点 <2 或基线通道无产出时）：
     单汇合点方位角序列对齐直接解 θ、t，用其余汇合点位置验证。
  5. 输出按不变量一致性排序的 Top-N 假设。

确定性：无随机源；排序全部带稳定 tie-break。坐标约定与 P1 一致：
假设把"模拟本地系"映射到 EPSG:3857 世界系，world = R(θ)·local + t，scale=1。
"""
import itertools
import json
import math
import pathlib

import numpy as np
from shapely import LineString

# ---------------------------------------------------------------- 参数（写死，确定性）
SNAP_TOL_M = 2.0          # 缆线端点聚类容差
BEARING_SAMPLE_M = 30.0   # 沿边采样方位角的弧长（抗顶点噪声）
CLUSTER_M = 30.0          # OSM 路口合并半径（超路口）
ANGLE_TOL_DEG = 25.0      # 交会边方位角匹配容差
BASELINE_RTOL = 0.08      # 基线长度比例容差
BASELINE_ATOL_M = 30.0    # 基线长度绝对容差
W_ANG = 1.0               # 方位角残差权重（score 单位≈度）
W_DIST = 0.5              # 基线长度残差权重（每米）
W_LEN = 10.0              # 悬空边矛盾罚分权重


# ---------------------------------------------------------------- 几何工具
def _bearing_between(p0, p1):
    return math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0])) % 360.0


def _point_along(coords, s_m):
    """折线上距起点弧长 s_m 处的插值点（超出范围则钳到端点）。"""
    pts = np.asarray(coords, dtype=float)
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    s_m = min(max(s_m, 0.0), float(cum[-1]))
    i = int(np.searchsorted(cum, s_m, side="right") - 1)
    i = max(0, min(i, len(pts) - 2))
    f = (s_m - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
    return pts[i] + f * (pts[i + 1] - pts[i])


def _circ_dev_deg(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def rot_mat(deg):
    r = math.radians(deg)
    return np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])


# ---------------------------------------------------------------- 缆线汇合点图
def build_cable_graph(lines, snap_tol=SNAP_TOL_M):
    """从线要素构建汇合点图。

    返回 nodes: [{"id","pos","degree","edges":[{"bearing","length","dangling","line"]}]}]
    处理：(a) 端点 snap 聚类；(b) 线-线内部交点（距端点聚类 >tol 的）也作为节点
    并切断相关边（本数据集无内部交点，逻辑保留通用性）。
    """
    ep = []  # (line_idx, end 0/1, xy)
    for li, ln in enumerate(lines):
        ep.append((li, 0, np.asarray(ln.coords[0], dtype=float)))
        ep.append((li, 1, np.asarray(ln.coords[-1], dtype=float)))
    parent = list(range(len(ep)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(ep)):
        for j in range(i + 1, len(ep)):
            if np.hypot(*(ep[i][2] - ep[j][2])) <= snap_tol:
                pi, pj = find(i), find(j)
                if pi != pj:
                    parent[pj] = pi

    cluster_pts = {}
    for i in range(len(ep)):
        cluster_pts.setdefault(find(i), []).append(ep[i][2])
    cluster_mean = [np.mean(v, axis=0) for _, v in
                    sorted(cluster_pts.items(), key=lambda kv: kv[0])]

    # --- 内部交点 ---
    cuts = {li: [] for li in range(len(lines))}
    interior_pts = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            inter = lines[i].intersection(lines[j])
            if inter.is_empty:
                continue
            plist = []
            if inter.geom_type == "Point":
                plist = [inter]
            elif inter.geom_type == "MultiPoint":
                plist = list(inter.geoms)
            for p in plist:
                xy = np.array([p.x, p.y])
                if any(np.hypot(*(xy - m)) <= snap_tol + 1.0 for m in cluster_mean):
                    continue
                cuts[i].append(float(lines[i].project(p)))
                cuts[j].append(float(lines[j].project(p)))
                interior_pts.append(xy)

    # --- 节点表 ---
    nodes = [{"pos": np.asarray(m, dtype=float), "edges": []} for m in cluster_mean]
    nodes += [{"pos": np.asarray(xy, dtype=float), "edges": []} for xy in interior_pts]

    def node_of_point(xy):
        best, best_d = None, snap_tol + 1.0
        for nd in nodes:
            d = float(np.hypot(*(nd["pos"] - xy)))
            if d <= best_d:
                best, best_d = nd, d
        return best

    # --- 边：每条线按 cuts 切段，段两端挂到节点 ---
    for li, ln in enumerate(lines):
        pts = np.asarray(ln.coords, dtype=float)
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        total = float(cum[-1])
        cuts_i = sorted(c for c in (round(c, 3) for c in cuts[li]) if 0.5 < c < total - 0.5)
        segs, prev = [], 0.0
        for s in cuts_i:
            segs.append((prev, s))
            prev = s
        segs.append((prev, total))
        for s0, s1 in segs:
            if s1 - s0 < 1.0:
                continue
            p0 = _point_along(pts, s0)
            p1 = _point_along(pts, s1)
            n0 = node_of_point(p0)
            n1 = node_of_point(p1)
            if n0 is not None:
                ahead = _point_along(pts, s0 + BEARING_SAMPLE_M)
                n0["edges"].append({
                    "bearing": _bearing_between(p0, ahead),
                    "length": float(s1 - s0),
                    "dangling": n1 is None,
                    "line": li,
                })
            if n1 is not None:
                ahead = _point_along(pts[::-1], (total - s1) + BEARING_SAMPLE_M)
                n1["edges"].append({
                    "bearing": _bearing_between(p1, ahead),
                    "length": float(s1 - s0),
                    "dangling": n0 is None,
                    "line": li,
                })

    nodes = [nd for nd in nodes if nd["edges"]]
    for nd in nodes:
        nd["edges"].sort(key=lambda e: (round(e["bearing"], 3), round(e["length"], 1)))
        nd["degree"] = len(nd["edges"])
    nodes.sort(key=lambda nd: (nd["pos"][0], nd["pos"][1]))
    for i, nd in enumerate(nodes):
        nd["id"] = i
    return nodes


# ---------------------------------------------------------------- OSM 路口图
def load_osm_ways(cache_path, from_4326):
    """解析 Overpass 缓存 → [{"nodes":[id...], "pts":(n,2) 3857, "tags":{}}]。"""
    obj = json.loads(pathlib.Path(cache_path).read_text(encoding="utf-8"))
    ways = []
    for el in obj.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el or "nodes" not in el:
            continue
        geo = np.array([[p["lon"], p["lat"]] for p in el["geometry"]], dtype=float)
        if len(geo) < 2 or len(geo) != len(el["nodes"]):
            continue
        x, y = from_4326.transform(geo[:, 0], geo[:, 1])
        ways.append({"nodes": list(el["nodes"]),
                     "pts": np.column_stack([x, y]),
                     "tags": el.get("tags", {})})
    return ways


def build_osm_graph(ways):
    """按共享 node id 构建路口图（度数≥3），返回节点列表（未做超路口合并）。"""
    from collections import defaultdict
    inc = defaultdict(list)  # node_id -> [(way_idx, pos)]
    for wi, w in enumerate(ways):
        for pos, nid in enumerate(w["nodes"]):
            inc[nid].append((wi, pos))

    deg = {}
    for nid, occ in inc.items():
        d = 0
        for wi, pos in occ:
            n = len(ways[wi]["nodes"])
            d += 1 if (pos == 0 or pos == n - 1) else 2
        deg[nid] = d
    junc_ids = {nid for nid, d in deg.items() if d >= 3}

    nodes = {}
    for nid in sorted(junc_ids):
        wi, pos = inc[nid][0]
        nodes[nid] = {"pos": ways[wi]["pts"][pos].astype(float),
                      "edges": [], "degree": deg[nid]}

    for wi, w in enumerate(ways):
        nids, pts = w["nodes"], w["pts"]
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        jpos = [p for p, nid in enumerate(nids) if nid in junc_ids]
        for p in jpos:
            nd = nodes[nids[p]]
            # 前向边（向下一个路口或 way 终点）
            nxt = next((p2 for p2 in jpos if p2 > p), None)
            s_end = cum[nxt] if nxt is not None else cum[-1]
            if s_end - cum[p] >= 1.0:
                ahead = _point_along(pts, cum[p] + BEARING_SAMPLE_M)
                nd["edges"].append({
                    "bearing": _bearing_between(pts[p], ahead),
                    "length": float(s_end - cum[p]),
                    "dangling": nxt is None,
                    "way": wi, "other": (nids[nxt] if nxt is not None else None),
                })
            # 后向边
            prv = next((p2 for p2 in reversed(jpos) if p2 < p), None)
            s_end = cum[prv] if prv is not None else cum[0]
            if cum[p] - s_end >= 1.0:
                ahead = _point_along(pts, cum[p] - BEARING_SAMPLE_M)
                nd["edges"].append({
                    "bearing": _bearing_between(pts[p], ahead),
                    "length": float(cum[p] - s_end),
                    "dangling": prv is None,
                    "way": wi, "other": (nids[prv] if prv is not None else None),
                })

    out = []
    for nid in sorted(nodes):
        nd = nodes[nid]
        nd["node_id"] = int(nid)
        nd["edges"].sort(key=lambda e: (round(e["bearing"], 3), round(e["length"], 1)))
        out.append(nd)
    for i, nd in enumerate(out):
        nd["id"] = i
    return out


def cluster_osm_junctions(nodes, radius=CLUSTER_M):
    """把间距 ≤radius 的路口合并为超路口；团内边被丢弃，团外边挂到超路口。

    返回超节点列表（degree = 团外关联边数，只保留 degree≥3 之外的全部节点，
    由调用方按 degree 过滤）。
    """
    n = len(nodes)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pos = np.array([nd["pos"] for nd in nodes])
    for i in range(n):
        for j in range(i + 1, n):
            if abs(pos[i, 0] - pos[j, 0]) > radius or abs(pos[i, 1] - pos[j, 1]) > radius:
                continue
            if np.hypot(*(pos[i] - pos[j])) <= radius:
                pi, pj = find(i), find(j)
                if pi != pj:
                    parent[pj] = pi

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    idx_to_super = {}
    supers = []
    for root in sorted(groups, key=lambda r: min(groups[r])):
        members = groups[root]
        member_set = {nodes[i]["node_id"] for i in members}
        pts = pos[members]
        sp = {"pos": pts.mean(axis=0), "members": [int(nodes[i]["node_id"]) for i in members],
              "edges": []}
        sid = len(supers)
        for i in members:
            idx_to_super[nodes[i]["node_id"]] = sid
            for e in nodes[i]["edges"]:
                if e["other"] is not None and e["other"] in member_set:
                    continue  # 团内短连接边，丢弃
                sp["edges"].append(dict(e))
        sp["edges"].sort(key=lambda e: (round(e["bearing"], 3), round(e["length"], 1)))
        sp["degree"] = len(sp["edges"])
        supers.append(sp)
    for i, sp in enumerate(supers):
        sp["id"] = i
    return supers


# ---------------------------------------------------------------- 不变量匹配
def bearing_set_match(c_bearings, o_bearings, tol=ANGLE_TOL_DEG):
    """缆线方位角序列（已旋转到世界系）对 OSM 方位角集合的子集匹配。

    每条缆线边必须找到一条未占用的 OSM 边且偏差 ≤tol。
    返回 (ok, mean_res, max_res, mapping)；mapping[k]=j 或 None。
    """
    dc = len(c_bearings)
    do = len(o_bearings)
    if do < dc:
        return False, 180.0, 180.0, [None] * dc
    best = None
    for perm in itertools.permutations(range(do), dc):
        devs = [_circ_dev_deg(c_bearings[k], o_bearings[perm[k]]) for k in range(dc)]
        mx = max(devs)
        if mx > tol:
            continue
        mn = sum(devs) / dc
        key = (round(mx, 6), round(mn, 6), perm)
        if best is None or key < best[0]:
            best = (key, mn, mx, list(perm))
    if best is None:
        return False, 180.0, 180.0, [None] * dc
    _, mn, mx, perm = best
    return True, mn, mx, perm


def _length_penalty(c_edges, o_edges, mapping):
    """悬空边矛盾检查：缆线悬空边比对应 OSM 边长很多（道路提前遇到路口）则罚。"""
    pen = 0.0
    cnt = 0
    for k, j in enumerate(mapping):
        if j is None:
            continue
        lc = c_edges[k]["length"]
        lo = o_edges[j]["length"]
        if lo < 1.0:
            continue
        cnt += 1
        if c_edges[k]["dangling"]:
            pen += min(1.0, max(0.0, (lc - lo - 25.0) / max(lc, 1.0)))
    return pen / max(cnt, 1)


def generate_hypotheses(cable_nodes, osm_super, top_n=50,
                        angle_tol=ANGLE_TOL_DEG,
                        baseline_rtol=BASELINE_RTOL, baseline_atol=BASELINE_ATOL_M):
    """生成 Top-N 拓扑假设（基线通道优先，单点通道兜底）。"""
    cj = [n for n in cable_nodes if n["degree"] >= 3]
    oj = [n for n in osm_super if n["degree"] >= 3]
    hyps = []
    channel = "baseline"

    if len(cj) >= 2:
        # -------- 基线通道：缆线汇合点对 ↔ OSM 超路口有序对 --------
        oj_pos = np.array([n["pos"] for n in oj])
        cpairs = []
        for i in range(len(cj)):
            for j in range(i + 1, len(cj)):
                d = float(np.hypot(*(cj[i]["pos"] - cj[j]["pos"])))
                cpairs.append((i, j, d))
        for ci, cj2, dc in cpairs:
            c1, c2 = cj[ci], cj[cj2]
            base_c = _bearing_between(c1["pos"], c2["pos"])
            tol = max(baseline_atol, baseline_rtol * dc)
            for a in range(len(oj)):
                for b in range(len(oj)):
                    if a == b:
                        continue
                    dv = oj_pos[b] - oj_pos[a]
                    dd = float(np.hypot(*dv))
                    if abs(dd - dc) > tol:
                        continue
                    o1, o2 = oj[a], oj[b]
                    for (na, nb, ca, cb, base) in (
                            (o1, o2, c1, c2, base_c),
                            (o1, o2, c2, c1, (base_c + 180.0) % 360.0)):
                        theta = (_bearing_between(na["pos"], nb["pos"]) - base) % 360.0
                        R = rot_mat(theta)
                        # 端点方位角序列验证（两端都必须过）
                        ok_all, res_sum, res_max = True, 0.0, 0.0
                        maps = []
                        for (cnod, onod) in ((ca, na), (cb, nb)):
                            cb_rot = [(e["bearing"] + theta) % 360.0 for e in cnod["edges"]]
                            ob = [e["bearing"] for e in onod["edges"]]
                            ok, mn, mx, mp = bearing_set_match(cb_rot, ob, angle_tol)
                            if not ok:
                                ok_all = False
                                break
                            res_sum += mn
                            res_max = max(res_max, mx)
                            maps.append((cnod, onod, mp))
                        if not ok_all:
                            continue
                        t = na["pos"] - R @ ca["pos"]
                        len_pen = sum(_length_penalty(cn["edges"], on["edges"], mp)
                                      for cn, on, mp in maps)
                        score = (W_ANG * (res_sum + 0.5 * res_max)
                                 + W_DIST * abs(dd - dc)
                                 + W_LEN * len_pen)
                        hyps.append({
                            "theta_deg": float(theta),
                            "t": [float(t[0]), float(t[1])],
                            "score": float(score),
                            "diag": {
                                "channel": "baseline",
                                "cable_nodes": [int(ca["id"]), int(cb["id"])],
                                "osm_members": [na["members"], nb["members"]],
                                "baseline_cable_m": float(dc),
                                "baseline_osm_m": float(dd),
                                "ang_res_mean_deg": float(res_sum / 2.0),
                                "ang_res_max_deg": float(res_max),
                                "length_penalty": float(len_pen),
                            },
                        })

    if not hyps:
        # -------- 单点兜底通道：单汇合点方位角对齐 + 其余汇合点位置验证 --------
        channel = "single-node-fallback"
        oj_pos = np.array([n["pos"] for n in oj]) if oj else np.zeros((0, 2))
        for c in cj:
            cb = [e["bearing"] for e in c["edges"]]
            for o in oj:
                if len(o["edges"]) < len(cb):
                    continue
                ob = [e["bearing"] for e in o["edges"]]
                for perm in itertools.permutations(range(len(ob)), len(cb)):
                    thetas = [((ob[perm[k]] - cb[k]) % 360.0) for k in range(len(cb))]
                    # 圆均值
                    r = np.radians(thetas)
                    theta = math.degrees(math.atan2(np.sin(r).mean(),
                                                    np.cos(r).mean())) % 360.0
                    res = max(_circ_dev_deg(x, theta) for x in thetas)
                    if res > angle_tol:
                        continue
                    R = rot_mat(theta)
                    t = o["pos"] - R @ c["pos"]
                    n_ver = 0
                    for c2 in cj:
                        if c2 is c:
                            continue
                        p2 = R @ c2["pos"] + t
                        if len(oj_pos) == 0:
                            break
                        dists = np.hypot(oj_pos[:, 0] - p2[0], oj_pos[:, 1] - p2[1])
                        if float(dists.min()) <= 60.0:
                            n_ver += 1
                    score = res + 2.0 * res - 15.0 * n_ver
                    hyps.append({
                        "theta_deg": float(theta),
                        "t": [float(t[0]), float(t[1])],
                        "score": float(score),
                        "diag": {"channel": channel,
                                 "cable_nodes": [int(c["id"])],
                                 "osm_members": [o["members"]],
                                 "ang_res_mean_deg": float(res),
                                 "second_verified": n_ver},
                    })

    # --- 去重（θ 0.5° / t 25m 网格）+ 稳定排序 ---
    hyps.sort(key=lambda h: (round(h["score"], 9), round(h["theta_deg"], 3),
                             round(h["t"][0], 1), round(h["t"][1], 1)))
    seen, out = set(), []
    for h in hyps:
        key = (round(h["theta_deg"] * 2), round(h["t"][0] / 25.0), round(h["t"][1] / 25.0))
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= top_n:
            break
    return out, channel


# ---------------------------------------------------------------- CLI（独立调试）
def main():
    import argparse
    import sys
    P1 = pathlib.Path(__file__).resolve().parent.parent / "road_match_p1"
    sys.path.insert(0, str(P1))
    import road_match_p1 as p1  # noqa: E402

    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent
                                         / "results" / "topo_seeds_debug.json"))
    args = ap.parse_args()

    truth = p1.load_truth_cables()
    local, t_meta = p1.simulate_local_frame(truth)
    cache = p1.find_osm_cache()
    ways = load_osm_ways(cache, p1.FROM_4326)

    c_nodes = build_cable_graph(local)
    o_nodes = build_osm_graph(ways)
    o_super = cluster_osm_junctions(o_nodes)
    hyps, channel = generate_hypotheses(c_nodes, o_super, top_n=args.top_n)

    payload = {
        "channel": channel,
        "cable_junctions": [
            {"id": n["id"], "pos_local": [float(v) for v in n["pos"]],
             "degree": n["degree"],
             "bearings": [round(e["bearing"], 2) for e in n["edges"]],
             "lengths": [round(e["length"], 1) for e in n["edges"]]}
            for n in c_nodes if n["degree"] >= 3],
        "osm_junction_count_deg_ge3": len([n for n in o_nodes if n["degree"] >= 3]),
        "osm_super_count_deg_ge3": len([n for n in o_super if n["degree"] >= 3]),
        "hypothesis_count": len(hyps),
        "hypotheses": hyps,
        "T_true": t_meta,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "channel": channel,
        "cable_junctions": payload["cable_junctions"],
        "osm_junctions_deg_ge3": payload["osm_junction_count_deg_ge3"],
        "osm_super_deg_ge3": payload["osm_super_count_deg_ge3"],
        "hypotheses": len(hyps),
        "top5": [{"theta": round(h["theta_deg"], 2), "score": round(h["score"], 2),
                  "diag": h["diag"]} for h in hyps[:5]],
        "theta_true": round(t_meta["theta_true_deg"], 3),
        "out": str(out),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
