# -*- coding: utf-8 -*-
"""run_topo_hutabohu.py — P2 拓扑种子恢复实验（hutabohu 基线）。

流程：
  1. 复用 P1：load_truth_cables / simulate_local_frame（SEED=20240818，
     与 P1 同一 T_true 与约定）/ FROM_4326 等（sys.path 引入，只读，不修改 P1）。
  2. topo_seed 生成 Top-N（50）不变量排序假设，取 Top-10。
  3. 对 Top-10 每个假设：先做一次精确 F1（shapely，与 P1 抛光同一定义），
     再用与 P1 完全相同的爬山调度（步长 5m/0.5°，÷2.5 递减至 <1m）抛光。
  4. 取抛光后精确 F1 最高者为恢复结果，对 T_true 求残差。
  5. 评估量核算（诚实约定，见 results JSON 的 eval_counting_convention）：
     - P2 精确 F1 评估次数 = 种子评分 10 次 + 10 次抛光的全部爬山评估；
     - P1 全程评估量 = 栅格 F1 假设评估（粗扫 72 + 细扫 33 幅相关图 ×
       搜索域内有效平移像素数，精确重算）+ P1 抛光精确 F1 评估次数
       （按同一调度从 P1 result.json 的 top5_fine[0] 确定性重放计数）。

输出 results/topo_hutabohu.json + topo_overlay.geojson / topo_overlay.png。
确定性：全部随机源来自 P1 的 SEED；无其他随机。
"""
import json
import math
import pathlib
import sys
import time

import numpy as np
import shapely
from PIL import Image, ImageDraw

P1_DIR = pathlib.Path(__file__).resolve().parent.parent / "road_match_p1"
sys.path.insert(0, str(P1_DIR))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import road_match_p1 as p1  # noqa: E402
import topo_seed as ts  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "results"
TOP_N_SEEDS = 50          # 不变量排序候选池
TOP_K_POLISH = 10         # 进入精确 F1 抛光的假设数
BUFFER_M = p1.BUFFER_M    # 15m，与 P1 一致
GRID_RADIUS = p1.GRID_RADIUS


# ---------------------------------------------------------------- 精确 F1（计数）
class ExactScorer:
    """精确矢量 F1（Dice），与 P1 抛光完全一致的定义；自带评估计数。"""

    def __init__(self, local_pts, road_buf, road_area):
        self.local_pts = local_pts
        self.road_buf = road_buf
        self.road_area = road_area
        self.n_evals = 0

    def __call__(self, theta, t):
        self.n_evals += 1
        moved = [shapely.LineString(p1.apply_sim(pts, theta, t)) for pts in self.local_pts]
        cb = shapely.union_all([ln.buffer(BUFFER_M) for ln in moved])
        inter = cb.intersection(self.road_buf).area
        return 2.0 * inter / (cb.area + self.road_area)


def hill_climb(scorer, theta0, t0):
    """与 P1 phase_scan 抛光相同的调度：步长 5m/0.5°，÷2.5 递减至 <1m。
    返回 (theta, t, best_f1, n_evals_used)。"""
    th_hat, t_hat = float(theta0), np.array(t0, dtype=float)
    step_m, step_deg = 5.0, 0.5
    cur = scorer(th_hat, t_hat)
    while step_m >= 1.0:
        improved = False
        for dth in (0.0, step_deg, -step_deg):
            for dt in ((0, 0), (step_m, 0), (-step_m, 0), (0, step_m), (0, -step_m),
                       (step_m, step_m), (step_m, -step_m), (-step_m, step_m), (-step_m, -step_m)):
                cand_th, cand_t = th_hat + dth, t_hat + np.array(dt, dtype=float)
                f = scorer(cand_th, cand_t)
                if f > cur + 1e-9:
                    cur, th_hat, t_hat, improved = f, cand_th, cand_t, True
        if not improved:
            step_m /= 2.5
            step_deg /= 2.5
    return th_hat, t_hat, cur


# ---------------------------------------------------------------- P1 评估量核算
def p1_raster_eval_count_from_local(meta, local_pts):
    """精确重算 P1 栅格扫描的假设评估数：每幅相关图在搜索域掩膜内的有效
    平移像素数 × 图数（粗 72 + 细 33）。与 P1 phase_scan 的索引公式一致。"""
    RES = p1.RES
    N = int(2 * p1.GRID_RADIUS / RES)
    anchor = np.array(meta["truth_center_3857"], dtype=float)
    all_local = np.concatenate(local_pts)
    half_span = float(np.abs(all_local).max()) + p1.BUFFER_M + 4 * RES
    M = int(2 * half_span / RES)
    x_min, y_max = anchor[0] - p1.GRID_RADIUS, anchor[1] + p1.GRID_RADIUS
    iy_grid, ix_grid = np.mgrid[0: N - M, 0: N - M]
    tx_grid = x_min + (ix_grid + M / 2) * RES
    ty_grid = y_max - (iy_grid + M / 2) * RES
    mask = ((tx_grid - anchor[0]) ** 2 + (ty_grid - anchor[1]) ** 2) <= p1.SEARCH_RADIUS ** 2
    valid = int(mask.sum())
    n_coarse = int(360.0 / p1.COARSE_THETA_STEP)          # 72
    n_fine = p1.TOP_K_COARSE * int(2 * p1.FINE_THETA_RANGE / p1.FINE_THETA_STEP + 1)  # 3*11=33
    return {"valid_translations_per_map": valid,
            "coarse_maps": n_coarse, "fine_maps": n_fine,
            "raster_evals": valid * (n_coarse + n_fine)}


def p1_polish_eval_count(local_pts, road_buf, road_area):
    """确定性重放 P1 抛光（起点 = P1 result.json 的 top5_fine[0]），计评估次数。"""
    res1 = json.loads((P1_DIR / "results" / "result.json").read_text(encoding="utf-8"))
    h0 = res1["top5_fine"][0]
    scorer = ExactScorer(local_pts, road_buf, road_area)
    hill_climb(scorer, h0["theta_deg"], h0["t_3857"])
    return scorer.n_evals


# ---------------------------------------------------------------- 主流程
def main():
    t_start = time.perf_counter()
    RESULTS.mkdir(exist_ok=True)
    meta = json.loads((p1.DATA / "truth_meta.json").read_text(encoding="utf-8"))
    truth = p1.load_truth_cables()
    roads, cache = p1.load_roads_3857()
    anchor = np.array(meta["truth_center_3857"], dtype=float)

    # --- 与 P1 完全相同的模拟本地系 / T_true ---
    local, t_meta = p1.simulate_local_frame(truth)
    t_true = np.array(t_meta["t_true_3857"], dtype=float)
    theta_true = t_meta["theta_true_deg"]
    local_pts = p1.lines_to_points(local)

    # --- 道路缓冲域（与 P1 抛光相同） ---
    domain = shapely.box(anchor[0] - GRID_RADIUS, anchor[1] - GRID_RADIUS,
                         anchor[0] + GRID_RADIUS, anchor[1] + GRID_RADIUS)
    roads_in_domain = [r for r in roads if r.intersects(domain)]
    road_buf = shapely.union_all([r.buffer(BUFFER_M) for r in roads_in_domain])
    road_area = float(road_buf.area)

    # --- 拓扑种子 ---
    t_seed0 = time.perf_counter()
    c_nodes = ts.build_cable_graph(local)
    ways = ts.load_osm_ways(cache, p1.FROM_4326)
    o_nodes = ts.build_osm_graph(ways)
    o_super = ts.cluster_osm_junctions(o_nodes)
    hyps, channel = ts.generate_hypotheses(c_nodes, o_super, top_n=TOP_N_SEEDS)
    t_seed1 = time.perf_counter()
    seeds = hyps[:TOP_K_POLISH]

    # --- 精确 F1 评分 + 抛光 Top-10 ---
    scorer = ExactScorer(local_pts, road_buf, road_area)
    t_polish0 = time.perf_counter()
    for h in seeds:
        h["exact_f1_seed"] = float(scorer(h["theta_deg"], h["t"]))
    n_seed_evals = scorer.n_evals
    for h in seeds:
        before = scorer.n_evals
        th, tt2, f1 = hill_climb(scorer, h["theta_deg"], h["t"])
        h["polished"] = {"theta_deg": float(th % 360.0),
                         "t_3857": [float(tt2[0]), float(tt2[1])],
                         "exact_f1": float(f1),
                         "polish_evals": scorer.n_evals - before}
    t_polish1 = time.perf_counter()
    p2_exact_evals = scorer.n_evals

    ranked = sorted(seeds, key=lambda h: (-h["polished"]["exact_f1"],
                                          h["score"], h["theta_deg"]))
    best = ranked[0]
    th_hat = best["polished"]["theta_deg"]
    t_hat = np.array(best["polished"]["t_3857"])
    dtheta = (th_hat - theta_true + 180.0) % 360.0 - 180.0
    dt_m = float(np.hypot(*(t_hat - t_true)))

    # --- P1 评估量核算 ---
    raster = p1_raster_eval_count_from_local(meta, local_pts)
    p1_polish_evals = p1_polish_eval_count(local_pts, road_buf, road_area)
    p1_total = raster["raster_evals"] + p1_polish_evals
    ratio = p2_exact_evals / p1_total
    speedup = p1_total / max(p2_exact_evals, 1)

    ok_t = dt_m < 50.0
    ok_r = abs(dtheta) < 5.0
    ok_budget = p2_exact_evals <= 0.05 * p1_total

    result = {
        "experiment": "road-match P2: Li&Briggs 拓扑点模式匹配播种 + Top-10 精确 F1 抛光",
        "seed": p1.SEED,
        "channel": channel,
        "T_true": {
            "theta_deg": theta_true,
            "t_3857": [float(t_true[0]), float(t_true[1])],
            "scale": t_meta["scale_true"],
            "convention": t_meta["convention"],
        },
        "T_recovered": {
            "theta_deg": float(th_hat),
            "t_3857": [float(t_hat[0]), float(t_hat[1])],
            "scale": 1.0,
            "exact_f1": best["polished"]["exact_f1"],
            "from_seed_rank": int(seeds.index(best)),
            "seed_invariant_score": best["score"],
            "seed_diag": best["diag"],
        },
        "residuals": {
            "translation_m": dt_m,
            "rotation_deg": float(dtheta),
            "rotation_deg_abs": float(abs(dtheta)),
        },
        "success_criteria": {
            "translation_residual_lt_50m": bool(ok_t),
            "rotation_residual_lt_5deg": bool(ok_r),
            "exact_f1_evals_le_5pct_of_p1": bool(ok_budget),
            "pass": bool(ok_t and ok_r and ok_budget),
        },
        "graphs": {
            "cable_junctions_deg_ge3": [
                {"id": n["id"], "pos_local": [float(v) for v in n["pos"]],
                 "degree": n["degree"],
                 "bearings": [round(e["bearing"], 2) for e in n["edges"]],
                 "edge_lengths_m": [round(e["length"], 1) for e in n["edges"]]}
                for n in c_nodes if n["degree"] >= 3],
            "osm_junctions_deg_ge3": len([n for n in o_nodes if n["degree"] >= 3]),
            "osm_super_junctions_deg_ge3": len([n for n in o_super if n["degree"] >= 3]),
            "osm_cluster_radius_m": ts.CLUSTER_M,
            "angle_tol_deg": ts.ANGLE_TOL_DEG,
            "baseline_tol": {"rtol": ts.BASELINE_RTOL, "atol_m": ts.BASELINE_ATOL_M},
        },
        "hypotheses": {
            "generated_top_n": len(hyps),
            "polished_top_k": len(seeds),
            "top10": [
                {"seed_rank": i, "theta_deg": h["theta_deg"], "t_3857": h["t"],
                 "invariant_score": h["score"], "diag": h["diag"],
                 "exact_f1_seed": h["exact_f1_seed"],
                 "polished": h["polished"],
                 "seed_residual_vs_true": {
                     "translation_m": float(np.hypot(h["t"][0] - t_true[0],
                                                     h["t"][1] - t_true[1])),
                     "rotation_deg_abs": float(abs((h["theta_deg"] - theta_true
                                                    + 180.0) % 360.0 - 180.0))}}
                for i, h in enumerate(seeds)],
        },
        "eval_counts": {
            "p2_exact_f1_evals_total": p2_exact_evals,
            "p2_seed_scoring_evals": n_seed_evals,
            "p2_polish_evals": p2_exact_evals - n_seed_evals,
            "p1_total_f1_evals": p1_total,
            "p1_raster_evals": raster,
            "p1_exact_polish_evals_replayed": p1_polish_evals,
            "p2_over_p1_ratio": ratio,
            "speedup_x": speedup,
            "eval_counting_convention": (
                "1 次评估 = 对 1 个 (θ,t) 假设做 1 次 F1 评分。P1 全程评估量 = "
                "栅格 F1 假设评估（粗扫 72 + 细扫 33 幅 FFT 相关图，每幅对搜索域掩膜内 "
                "全部 5m 像素平移评分，有效平移数由 P1 的索引公式精确重算）+ P1 抛光阶段的 "
                "精确 F1 评估（从 result.json top5_fine[0] 按同一爬山调度确定性重放计数）。"
                "P2 = Top-10 种子的精确 F1 评分 + 10 次抛光的全部爬山评估。"),
        },
        "timing_s": {
            "seed_generation": round(t_seed1 - t_seed0, 2),
            "seed_scoring_and_polish": round(t_polish1 - t_polish0, 2),
            "total": round(t_polish1 - t_start, 2),
        },
        "provenance": {
            "delivery_gpkg": p1.DELIVERY_GPKG,
            "delivery_sha256": meta["source_delivery_sha256"],
            "osm_cache": str(cache),
            "truth_frame": "EPSG:3857",
            "p1_result": str(P1_DIR / "results" / "result.json"),
            "p1_reuse": "sys.path 只读 import road_match_p1（未修改 P1 任何文件）",
        },
    }
    (RESULTS / "topo_hutabohu.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    write_overlay(truth, local, local_pts, th_hat, t_hat, anchor, result,
                  roads_in_domain, o_super, c_nodes, seeds)
    print(json.dumps({
        "channel": channel,
        "T_recovered": result["T_recovered"],
        "residuals": result["residuals"],
        "success_criteria": result["success_criteria"],
        "eval_counts": {k: v for k, v in result["eval_counts"].items()
                        if k not in ("p1_raster_evals", "eval_counting_convention")},
        "p1_raster_evals": raster,
        "timing_s": result["timing_s"],
    }, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- 可视化
def write_overlay(truth, local, local_pts, th_hat, t_hat, anchor, result,
                  roads, o_super, c_nodes, seeds):
    def to4326_coords(xy):
        lon, lat = p1.FROM_3857.transform(xy[:, 0], xy[:, 1])
        return [[float(a), float(b)] for a, b in zip(lon, lat)]

    recovered = [shapely.LineString(p1.apply_sim(pts, th_hat, t_hat)) for pts in local_pts]
    feats = []
    for name, lines, color in (
            ("osm_road", roads, "#999999"),
            ("cable_truth", truth, "#1a9850"),
            ("cable_recovered", recovered, "#d73027"),
            ("cable_local_at_anchor",
             [shapely.LineString(pts + anchor) for pts in local_pts], "#4575b4")):
        for ln in lines:
            feats.append({"type": "Feature",
                          "properties": {"layer": name, "stroke": color},
                          "geometry": {"type": "LineString",
                                       "coordinates": to4326_coords(np.asarray(ln.coords))}})
    for sp in o_super:
        if sp["degree"] < 3:
            continue
        lon, lat = p1.FROM_3857.transform(sp["pos"][0], sp["pos"][1])
        feats.append({"type": "Feature",
                      "properties": {"layer": "osm_super_junction", "degree": sp["degree"]},
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}})
    for i, h in enumerate(seeds):
        lon, lat = p1.FROM_3857.transform(h["t"][0], h["t"][1])
        feats.append({"type": "Feature",
                      "properties": {"layer": "topo_seed_top10", "rank": i,
                                     "theta_deg": h["theta_deg"],
                                     "invariant_score": h["score"],
                                     "exact_f1_polished": h["polished"]["exact_f1"]},
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}})
    for name, pt in (("anchor_truth_center", anchor),
                     ("T_true_t", np.array(result["T_true"]["t_3857"])),
                     ("T_recovered_t", t_hat)):
        lon, lat = p1.FROM_3857.transform(pt[0], pt[1])
        feats.append({"type": "Feature", "properties": {"layer": name},
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}})
    gj = {"type": "FeatureCollection",
          "properties": {"note": "EPSG:4326; topo_seed_top10=Top-10 拓扑假设的 t 位置"},
          "features": feats}
    (RESULTS / "topo_overlay.geojson").write_text(json.dumps(gj), encoding="utf-8")

    # --- PNG（PIL，4x 超采样抗锯齿） ---
    W = H = 900
    span = GRID_RADIUS * 2

    def sx(x):
        return (x - anchor[0]) / span * W + W / 2

    def sy(y):
        return H / 2 - (y - anchor[1]) / span * H

    S2 = 4
    img = Image.new("RGB", (W * S2, H * S2), (250, 248, 245))
    dr = ImageDraw.Draw(img)

    def draw_lines(lines, color, width):
        for ln in lines:
            xy = np.asarray(ln.coords)
            dr.line([(sx(p[0]) * S2, sy(p[1]) * S2) for p in xy],
                    fill=color, width=width * S2)

    def draw_dot(pt, color, r, fill=True):
        cxp, cyp = sx(pt[0]) * S2, sy(pt[1]) * S2
        box = [cxp - r * S2, cyp - r * S2, cxp + r * S2, cyp + r * S2]
        if fill:
            dr.ellipse(box, fill=color)
        else:
            dr.ellipse(box, outline=color, width=2 * S2)

    draw_lines(roads, (176, 168, 159), 1)
    draw_lines([shapely.LineString(pts + anchor) for pts in local_pts], (123, 156, 196), 1)
    draw_lines(truth, (46, 125, 82), 3)
    draw_lines(recovered, (194, 69, 47), 2)
    for sp in o_super:
        if sp["degree"] >= 3:
            draw_dot(sp["pos"], (120, 110, 100), 2)
    for i, h in enumerate(seeds):
        draw_dot(np.array(h["t"]), (240, 170, 60), 5, fill=(i < 3))
    draw_dot(np.array(result["T_true"]["t_3857"]), (46, 125, 82), 7, fill=False)
    draw_dot(t_hat, (194, 69, 47), 6, fill=False)
    res = result["residuals"]
    dr.text((14 * S2, 12 * S2),
            f"road-match P2 topo-seed — dt={res['translation_m']:.1f}m "
            f"dth={res['rotation_deg']:.2f}deg F1={result['T_recovered']['exact_f1']:.3f} "
            f"evals={result['eval_counts']['p2_exact_f1_evals_total']}",
            fill=(40, 40, 40))
    img = img.resize((W, H), Image.LANCZOS)
    img.save(RESULTS / "topo_overlay.png")


if __name__ == "__main__":
    main()
