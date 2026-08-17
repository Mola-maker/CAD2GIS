# -*- coding: utf-8 -*-
"""tests_ambiguity.py — CAD2GIS 路网匹配 P2：弃权门禁测试（纯脚本，确定性，可复跑）。

用例：
  A) 合成网格城市：程序生成的规则格网道路 + 一段与晶格周期（200 m）一致的
     缆线图案（3 条竖线 x=0/200/400）。晶格平移下得分完全相同
     → 必须判 AMBIGUOUS。硬标准：false-pass=0，绝不 ACCEPT。
  B) 无道路区域 / 空 OSM → NO_MATCH。
  C) hutabohu 真实数据正常案例：复用 P1 result.json 的 top5_fine + 抛光假设，
     用 shapely 精确 F1 重评分 → ACCEPT（正常案例不误弃权）。
  D) 注入扰动：删 50% 缆线（确定性保留索引 0,2,4）→ 记录分数下降后的实际判定。

评分定义与 P1 一致：变换后缆线 15 m 缓冲 ∩ OSM 道路 15 m 缓冲的 Dice F1。
覆盖度 = Top1 假设下落入道路缓冲的缆线长度 / 缆线总长（scale=1，局部系长度即世界长度）。

运行：conda run -n cad2gis python tools/road_match_p2/tests_ambiguity.py
输出：results/ambiguity_tests.json、results/case_grid_city.svg
退出码：0 = 全部硬标准通过；1 = 存在失败。
无随机源、无网络请求（OSM 复用 P1 落盘缓存，只读）。
"""
import json
import math
import pathlib
import sys

import numpy as np
import shapely

HERE = pathlib.Path(__file__).resolve().parent
P1_DIR = HERE.parent / "road_match_p1"
sys.dont_write_bytecode = True   # P1 目录只读复用：导入 road_match_p1 不得留下 __pycache__
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(P1_DIR))
if hasattr(sys.stdout, "reconfigure"):   # Windows 控制台 GBK 乱码防护（JSON 文件本就 UTF-8）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gates                  # noqa: E402  P2 门禁模块（本目录）
import road_match_p1 as p1    # noqa: E402  P1 只读复用：数据加载/变换/常量

RESULTS = HERE / "results"
BUFFER_M = p1.BUFFER_M        # 15.0，与 P1 评分定义保持一致


# ---------------------------------------------------------------- 评分工具
def score_hypotheses(local_pts, road_buf, road_area, hypotheses):
    """对假设列表逐个计算精确 Dice F1 与覆盖度，返回按分数降序的结果列表。

    local_pts: 局部坐标系缆线 [(n,2) ndarray]；hypotheses: {"theta_deg","t"}。
    """
    local_lines = [shapely.LineString(pts) for pts in local_pts]
    total_len = float(sum(ln.length for ln in local_lines))
    scored = []
    for h in hypotheses:
        moved = [shapely.LineString(p1.apply_sim(pts, h["theta_deg"], np.asarray(h["t"], dtype=float)))
                 for pts in local_pts]
        cb = shapely.union_all([ln.buffer(BUFFER_M) for ln in moved])
        denom = cb.area + road_area
        inter = cb.intersection(road_buf).area if road_area > 0 else 0.0
        f1 = (2.0 * inter / denom) if denom > 0 else 0.0
        cov = (shapely.union_all(moved).intersection(road_buf).length / total_len
               if road_area > 0 and total_len > 0 else 0.0)
        scored.append({"theta_deg": float(h["theta_deg"]),
                       "t": [float(h["t"][0]), float(h["t"][1])],
                       "score": float(f1), "coverage": float(cov),
                       "cable_len_m": total_len})
    scored.sort(key=lambda h: -h["score"])
    return scored


def run_gate(scored, n_roads):
    """取 Top-10 送门禁；stats.coverage = Top1 覆盖度。"""
    top = scored[:10]
    stats = {"n_roads": n_roads,
             "coverage": (top[0]["coverage"] if top else 0.0)}
    verdict = gates.decide(
        [{"score": h["score"], "theta_deg": h["theta_deg"], "t": h["t"]} for h in top],
        stats)
    return verdict


def slim(scored, k=5):
    return [{"theta_deg": round(h["theta_deg"], 4),
             "t": [round(h["t"][0], 2), round(h["t"][1], 2)],
             "score": round(h["score"], 6),
             "coverage": round(h["coverage"], 4)} for h in scored[:k]]


# ---------------------------------------------------------------- 用例 A：合成网格城市
GRID_SPACING = 200.0   # 晶格周期 m
GRID_X_LINES = [k * GRID_SPACING for k in range(-3, 6)]    # 9 条竖线 x=-600..1000
GRID_Y_LINES = [k * GRID_SPACING for k in range(-3, 13)]   # 16 条横线 y=-600..2400
GRID_X_EXT = (-600.0, 1000.0)
GRID_Y_EXT = (-600.0, 2400.0)


def case_grid_city():
    roads = ([shapely.LineString([(x, GRID_Y_EXT[0]), (x, GRID_Y_EXT[1])]) for x in GRID_X_LINES]
             + [shapely.LineString([(GRID_X_EXT[0], y), (GRID_X_EXT[1], y)]) for y in GRID_Y_LINES])
    road_buf = shapely.union_all([r.buffer(BUFFER_M) for r in roads])
    road_area = float(road_buf.area)

    # 缆线图案：3 条竖线 x=0/200/400，y=0..1800 —— 与晶格周期 200m 一致，
    # 任何 (dx=200k, dy) 晶格平移都完全落在竖直道路上，得分完全相同。
    cable_local = [np.array([[x, 0.0], [x, 1800.0]]) for x in (0.0, 200.0, 400.0)]

    hyps = []
    for dx in (-400.0, -200.0, 0.0, 200.0, 400.0):       # 晶格平移 → 并列最高分
        for dy in (-400.0, 0.0, 400.0):
            hyps.append({"theta_deg": 0.0, "t": [dx, dy]})
    for dx in (-100.0, 100.0):                           # 非晶格平移 → 只在交叉点沾边
        hyps.append({"theta_deg": 0.0, "t": [dx, 0.0]})
    hyps.append({"theta_deg": 0.0, "t": [50.0, 50.0]})   # 双轴非晶格 → 几乎无重叠

    scored = score_hypotheses(cable_local, road_buf, road_area, hyps)
    verdict = run_gate(scored, n_roads=len(roads))
    write_case_grid_svg(roads, cable_local, scored)
    return {
        "description": "合成网格城市：晶格周期平移产生 15 个完全同分的竞争假设",
        "expected": "AMBIGUOUS",
        "decision": verdict["decision"],
        "pass": verdict["decision"] == gates.AMBIGUOUS,
        "false_pass": verdict["decision"] == gates.ACCEPT,
        "top_scores": slim(scored),
        "n_tied_top": sum(1 for h in scored if abs(h["score"] - scored[0]["score"]) < 1e-9),
        "verdict": verdict,
    }


def write_case_grid_svg(roads, cable_local, scored):
    """用 SVG 画出格网 + Top1/Top2 两个同分晶格位置（歧义的可视化证据）。"""
    x0, x1 = -700.0, 1100.0
    y0, y1 = -700.0, 2500.0
    scale = 800.0 / (y1 - y0)
    W, H = int((x1 - x0) * scale), 800

    def sx(x): return (x - x0) * scale
    def sy(y): return H - (y - y0) * scale

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'style="background:#faf8f5">',
             f'<rect x="0" y="0" width="{W}" height="{H}" fill="#faf8f5"/>']
    for ln in roads:
        (ax, ay), (bx, by) = ln.coords[0], ln.coords[-1]
        parts.append(f'<line x1="{sx(ax):.1f}" y1="{sy(ay):.1f}" x2="{sx(bx):.1f}" '
                     f'y2="{sy(by):.1f}" stroke="#b0a89f" stroke-width="1"/>')

    def draw_cable(theta, t, color, width, dash=""):
        for pts in cable_local:
            m = p1.apply_sim(pts, theta, np.asarray(t, dtype=float))
            d = "M" + " L".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in m)
            dd = f' stroke-dasharray="{dash}"' if dash else ""
            parts.append(f'<path d="{d}" stroke="{color}" stroke-width="{width}" '
                         f'fill="none"{dd}/>')

    h1, h2 = scored[0], scored[1]
    draw_cable(h2["theta_deg"], h2["t"], "#c2452f", 2, "6,4")   # Top2 同分竞争：红虚
    draw_cable(h1["theta_deg"], h1["t"], "#2e7d52", 3)          # Top1：绿
    parts.append(f'<text x="14" y="26" font-family="sans-serif" font-size="15" fill="#333">'
                 f'case A grid city — Top1 F1={h1["score"]:.4f} vs Top2 F1={h2["score"]:.4f}'
                 f'（同分晶格位置）→ 必须 AMBIGUOUS</text>')
    parts.append(f'<text x="14" y="{H - 14}" font-family="sans-serif" font-size="12" fill="#777">'
                 f'灰=格网道路 绿=Top1 缆线 红虚=Top2 缆线（t 相差整数倍晶格周期）</text>')
    parts.append('</svg>')
    (RESULTS / "case_grid_city.svg").write_text("".join(parts), encoding="utf-8")


# ---------------------------------------------------------------- 用例 B：空 OSM
def case_empty_osm():
    v1 = gates.decide([], {"n_roads": 0, "coverage": 0.0})
    v2 = gates.decide([{"score": 0.0, "theta_deg": 0.0, "t": [0.0, 0.0]}],
                      {"n_roads": 0, "coverage": 0.0})
    ok = v1["decision"] == gates.NO_MATCH and v2["decision"] == gates.NO_MATCH
    return {
        "description": "无道路区域/空 OSM：R1（无假设）与 R2（零分假设）两条路径都须 NO_MATCH",
        "expected": "NO_MATCH",
        "decision": v1["decision"] if v1["decision"] == v2["decision"] else "INCONSISTENT",
        "pass": bool(ok),
        "sub_verdicts": {"no_hypotheses": v1, "zero_score_hypotheses": v2},
    }


# ---------------------------------------------------------------- 用例 C/D：hutabohu 真实数据
def load_hutabohu():
    """复用 P1 落盘数据（只读）：真值缆线、OSM 缓存、扫描结果、模拟局部系。"""
    truth = p1.load_truth_cables()
    roads, cache = p1.load_roads_3857()
    local, t_meta = p1.simulate_local_frame(truth)   # 种子固定，确定性复现
    p1_result = json.loads((P1_DIR / "results" / "result.json").read_text(encoding="utf-8"))

    anchor = np.array(t_meta["anchor_3857"])
    domain = shapely.box(anchor[0] - p1.GRID_RADIUS, anchor[1] - p1.GRID_RADIUS,
                         anchor[0] + p1.GRID_RADIUS, anchor[1] + p1.GRID_RADIUS)
    roads_clip = [r for r in roads if r.intersects(domain)]
    road_buf = shapely.union_all([r.intersection(domain).buffer(BUFFER_M) for r in roads_clip])
    road_area = float(road_buf.area)

    hyps = [{"theta_deg": h["theta_deg"], "t": h["t_3857"]} for h in p1_result["top5_fine"]]
    hyps.append({"theta_deg": p1_result["T_recovered"]["theta_deg"],
                 "t": p1_result["T_recovered"]["t_3857"]})   # 抛光后最优假设
    return local, t_meta, roads_clip, road_buf, road_area, hyps, p1_result, str(cache)


def case_hutabohu_normal():
    local, t_meta, roads_clip, road_buf, road_area, hyps, p1_result, cache = load_hutabohu()
    local_pts = p1.lines_to_points(local)
    scored = score_hypotheses(local_pts, road_buf, road_area, hyps)
    verdict = run_gate(scored, n_roads=len(roads_clip))
    return {
        "description": "hutabohu 真实数据正常案例：P1 top5_fine + 抛光假设精确重评分",
        "expected": "ACCEPT",
        "decision": verdict["decision"],
        "pass": verdict["decision"] == gates.ACCEPT,
        "top_scores": slim(scored),
        "p1_reference": {
            "osm_cache": cache,
            "p1_top1_raster_f1": p1_result["top5_fine"][0]["raster_f1"],
            "p1_polished_exact_f1": p1_result["T_recovered"]["exact_f1"],
        },
        "verdict": verdict,
    }


def case_hutabohu_perturbed():
    local, t_meta, roads_clip, road_buf, road_area, hyps, p1_result, cache = load_hutabohu()
    local_pts = p1.lines_to_points(local)
    keep = [0, 2, 4]   # 确定性删 50% 缆线（6 → 3 条）
    perturbed = [local_pts[i] for i in keep]
    full_len = float(sum(shapely.LineString(p).length for p in local_pts))
    kept_len = float(sum(shapely.LineString(p).length for p in perturbed))
    scored = score_hypotheses(perturbed, road_buf, road_area, hyps)
    verdict = run_gate(scored, n_roads=len(roads_clip))
    return {
        "description": "注入扰动：删 50% 缆线（保留索引 0,2,4），观察分数下降时门禁行为",
        "expected": "记录实际判定（分数显著下降时 NO_MATCH/AMBIGUOUS 均符合弃权语义）",
        "decision": verdict["decision"],
        "pass": verdict["decision"] != gates.ACCEPT or scored[0]["score"] >= gates.TAU_MIN,
        "kept_cable_indices": keep,
        "kept_length_share": kept_len / full_len,
        "top_scores": slim(scored),
        "verdict": verdict,
    }


# ---------------------------------------------------------------- 主流程
def main():
    RESULTS.mkdir(exist_ok=True)
    cases = {
        "A_grid_city_ambiguous": case_grid_city(),
        "B_empty_osm_no_match": case_empty_osm(),
        "C_hutabohu_normal_accept": case_hutabohu_normal(),
        "D_hutabohu_perturbed_50pct": case_hutabohu_perturbed(),
    }
    ambiguous_false_pass = sum(1 for c in cases.values() if c.get("false_pass"))
    hard = {
        "ambiguous_false_pass_count": ambiguous_false_pass,
        "ambiguous_false_pass_must_be_0": ambiguous_false_pass == 0,
        "normal_case_accepted": cases["C_hutabohu_normal_accept"]["decision"] == gates.ACCEPT,
        "empty_osm_no_match": cases["B_empty_osm_no_match"]["decision"] == gates.NO_MATCH,
    }
    overall = (all(c.get("pass", False) for c in cases.values())
               and ambiguous_false_pass == 0
               and hard["normal_case_accepted"] and hard["empty_osm_no_match"])
    out = {
        "experiment": "road-match P2: abstention gate (ACCEPT/AMBIGUOUS/NO_MATCH) tests",
        "deterministic": True,
        "score_definition": "变换后缆线 15m 缓冲 ∩ OSM 道路 15m 缓冲的 Dice F1（同 P1）",
        "coverage_definition": "Top1 假设下落入道路缓冲的缆线长度 / 缆线总长",
        "thresholds": dict(gates.THRESHOLDS),
        "cases": cases,
        "hard_criteria": hard,
        "overall_pass": bool(overall),
    }
    (RESULTS / "ambiguity_tests.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {k: {"decision": c["decision"], "expected": c["expected"], "pass": c["pass"]}
               for k, c in cases.items()}
    print(json.dumps({"cases": summary, "hard_criteria": hard,
                      "overall_pass": bool(overall)}, indent=2, ensure_ascii=False))
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
