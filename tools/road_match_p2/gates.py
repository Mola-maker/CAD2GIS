# -*- coding: utf-8 -*-
"""gates.py — CAD2GIS 路网匹配 P2：弃权门禁（abstention gate）。

定位匹配器在"多个不可区分的竞争假设"或"没有任何可靠匹配"时必须弃权，
而不是硬选一个相似变换交给下游（gcp_workflow 人工确认）。本模块只做纯判定：
输入 Top-N 假设分数列表 + 匹配统计，输出三态判定 ∈ {ACCEPT, AMBIGUOUS, NO_MATCH}。

判定规则（按序求值，命中即返回）：
  R1  路网为空（OSM 未覆盖测区 / 缓存缺失 / 域内 0 条 way）   → NO_MATCH
  R2  无候选假设，或 Top1 分数 < TAU_MIN                      → NO_MATCH
  R3  Top1 覆盖度 < MIN_COVERAGE                              → NO_MATCH
  R4  存在第二个"不同模式"且 s1 - s2 < TAU_MARGIN             → AMBIGUOUS
  R5  其余                                                    → ACCEPT

"不同模式"：两假设若 |Δθ| ≤ MODE_THETA_TOL_DEG 且 |Δt| ≤ MODE_TRANSLATION_TOL_M
视为同一模式的近邻细化点（细扫描步长 1°、栅格 5 m/px、缓冲半径 15 m），
只有聚类后不同模式之间的分数竞争才构成歧义——否则同一峰的相邻采样点
会把任何正常案例误判成 AMBIGUOUS。

接口约定：
  hypotheses: list[dict]，每项至少 {"score": float}；
              可选 "theta_deg": float、"t": [x, y]（米制）用于模式聚类。
  stats:      {"n_roads": int, "coverage": float|None}
              coverage = Top1 假设下匹配上（落入道路缓冲）的缆线长度占比。
decide() 返回 dict：decision / reasons / evidence / thresholds，全部 JSON 可序列化。
"""
from __future__ import annotations

import math

ACCEPT = "ACCEPT"
AMBIGUOUS = "AMBIGUOUS"
NO_MATCH = "NO_MATCH"

# ---------------------------------------------------------------- 阈值常量
# 初值全部来自 P1 hutabohu 实测分布（tools/road_match_p1/results/result.json）：
#   真模式细扫描 F1 = 0.114（抛光后精确 F1 同量级），最佳错误模式 = 0.053。
# 换测区/换评分定义时必须用该基线的 sanity 重叠与扫描分布重新标定。
TAU_MIN = 0.08
"""最低可接受分数。真模式 0.114 与最佳错误模式 0.053 的中点 ≈ 0.0835，
下舍到 0.08：错误模式（≤0.053）无法通过，真模式保留 ~40% 余量。
注意 Dice F1 绝对值受域内道路总量稀释（P1 README 已知限制），
道路稀疏区真模式可能低于此值——届时应结合 R3 覆盖度复核而非简单放宽。"""

TAU_MARGIN = 0.03
"""Top1 与 Top2（不同模式）的最小分离度。P1 实测分离 0.114-0.053 = 0.061，
取半 ≈ 0.03。分离不足说明两个模式对该缆线图案不可区分（如格网城市
的晶格周期平移），必须弃权交人工，而不是掷硬币硬选。"""

MIN_COVERAGE = 0.50
"""Top1 覆盖度下限（匹配上的缆线长度占比）。P1 sanity 实测：真值位置
87.7% 缆线缓冲落在 OSM 道路缓冲内；要求至少一半，防止把缆线网贴到
只沾边的小片路网上凑出虚高 F1。"""

MODE_THETA_TOL_DEG = 2.0
"""模式聚类角度容差：细扫描步长 1° 的 2 倍——同一峰相邻 1° 采样不算竞争假设。"""

MODE_TRANSLATION_TOL_M = 30.0
"""模式聚类平移容差：缓冲半径 15 m 的 2 倍——平移差小于缓冲带宽时两假设的
缆线缓冲大面积重合，属同一模式而非竞争假设。"""

THRESHOLDS = {
    "tau_min": TAU_MIN,
    "tau_margin": TAU_MARGIN,
    "min_coverage": MIN_COVERAGE,
    "mode_theta_tol_deg": MODE_THETA_TOL_DEG,
    "mode_translation_tol_m": MODE_TRANSLATION_TOL_M,
    "threshold_source": "P1 hutabohu 实测：真模式 F1=0.114 vs 最佳错误模式 0.053；"
                        "sanity 恒等覆盖率 87.7%（tools/road_match_p1/results/）。",
}


def _angdiff(a, b):
    """角度差（度），结果 ∈ [0, 180]。"""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _same_mode(a, b):
    """两假设是否属同一模式（角度与平移双容差；字段缺失的维度不判）。"""
    if "theta_deg" in a and "theta_deg" in b:
        if _angdiff(a["theta_deg"], b["theta_deg"]) > MODE_THETA_TOL_DEG:
            return False
    ta, tb = a.get("t"), b.get("t")
    if ta is not None and tb is not None:
        if math.hypot(ta[0] - tb[0], ta[1] - tb[1]) > MODE_TRANSLATION_TOL_M:
            return False
    return True


def distinct_modes(hypotheses):
    """把按分数降序的假设聚成"不同模式"，返回每模式的代表（分数最高者）。"""
    cands = sorted((h for h in hypotheses if h.get("score") is not None),
                   key=lambda h: -h["score"])
    modes = []
    for h in cands:
        if not any(_same_mode(h, m) for m in modes):
            modes.append(h)
    return modes


def decide(hypotheses, stats):
    """门禁判定。hypotheses/stats 约定见模块 docstring；返回 JSON 可序列化 dict。"""
    stats = stats or {}
    n_roads = int(stats.get("n_roads", 0))
    coverage = stats.get("coverage", None)
    cands = sorted((h for h in hypotheses if h.get("score") is not None),
                   key=lambda h: -h["score"])
    modes = distinct_modes(cands)
    s1 = cands[0]["score"] if cands else None
    s2 = modes[1]["score"] if len(modes) >= 2 else None

    evidence = {
        "n_hypotheses": len(cands),
        "n_distinct_modes": len(modes),
        "top1_score": s1,
        "top2_distinct_score": s2,
        "margin_top1_top2": (s1 - s2) if (s1 is not None and s2 is not None) else None,
        "coverage_top1": coverage,
        "n_roads": n_roads,
    }

    def verdict(decision, reasons):
        return {"decision": decision, "reasons": reasons,
                "evidence": evidence, "thresholds": dict(THRESHOLDS)}

    if n_roads <= 0:
        return verdict(NO_MATCH, ["R1: 路网为空（OSM 未覆盖测区或缓存缺失），无匹配对象"])
    if not cands:
        return verdict(NO_MATCH, ["R2: 无候选假设"])
    if s1 < TAU_MIN:
        return verdict(NO_MATCH,
                       [f"R2: Top1 分数 {s1:.4f} < TAU_MIN={TAU_MIN}，无任何可靠匹配"])
    if coverage is None:
        cov_note = "coverage 未提供，R3 跳过（调用方应提供 Top1 覆盖度）"
    else:
        cov_note = None
        if coverage < MIN_COVERAGE:
            return verdict(NO_MATCH,
                           [f"R3: Top1 覆盖度 {coverage:.3f} < MIN_COVERAGE={MIN_COVERAGE}，"
                            f"匹配上的缆线长度占比不足"])
    if len(modes) >= 2 and (s1 - s2) < TAU_MARGIN:
        return verdict(AMBIGUOUS,
                       [f"R4: Top1={s1:.4f} 与 Top2（不同模式）={s2:.4f} 分离度 "
                        f"{s1 - s2:.4f} < TAU_MARGIN={TAU_MARGIN}，竞争假设不可区分，弃权"])
    reasons = [f"R5: Top1={s1:.4f} ≥ TAU_MIN，覆盖度与分离度达标，通过"]
    if cov_note:
        reasons.append(cov_note)
    if len(modes) >= 2:
        reasons.append(f"次优不同模式 {s2:.4f}，分离度 {s1 - s2:.4f}")
    else:
        reasons.append("仅一个不同模式，无竞争假设")
    return verdict(ACCEPT, reasons)
