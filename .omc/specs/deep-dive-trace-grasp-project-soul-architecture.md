# Deep Dive Trace: grasp-project-soul-architecture

## Observed Result

任务陈述：深入研究 Workspace-Main 对话上下文，对照工作区根目录 `.omc` 中已有的文件，全面把握 CAD2GIS 项目的"灵魂"（业务本质）和"架构"（技术全景 + 当前状态）。

三条并行调查线路：(1) 业务灵魂；(2) 技术架构；(3) 现状与轨迹。本 trace 为理解型调查，非缺陷调查。

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | 技术架构：experiment/ 为主线的分层确定性管线（L1-L5），五轨道分工明确，生产路径仍是 converter.py 单体 + cad_common 库并存 | High (~80%) | Strong | git HEAD、文件结构、consolidation report 三方互证；运行时代码零 LLM 调用 |
| 2 | 现状轨迹：处于"后回退决策点"——五缺陷修复与重构均已提交，ftth_converter 回归已记录，多方向开放 | High | Strong | git status clean + consolidation-report-2026-07-19.md 直接记载回退与方案 C |
| 3 | 业务灵魂：烽火 XA-202610 竞赛驱动的历史 CAD→GIS ≥90% 自动化转换工程，三工作流中仅"历史→GIS"落地 | Medium | Moderate | 竞赛 PDF + 项目自述强证据链；但"定制 QGIS 内部系统""三工作流并重"为单一手工笔记孤证 |

## Evidence Summary by Hypothesis

- **Hypothesis 1（技术架构）**：主管线 = `.dwg → LibreDWG(SWIG+ctypes) → L1/L2 cad_common(读图/几何/CRS) → L3/L4 ftth_converter(分类/属性/写出) → topology_repair + style_exporter → L5 convert_all(编排/验证) → GPKG+QML/QGZ`。五轨道：demo/=重庆东溪早期探索（旧）；experiment/py_scripts/=单体验证线（仍被修改）；experiment/python/=解耦模块（已存在于 main 但未成为主路径）；official/validation/=摩洛哥 JAD-MARJANE 快照；newmodel/cad2gis_v3/=证据优先 v3 实验（origin/newmodel 分支，未合并）；plugincad2gis/=QGIS 插件长期目标架构（8 阶段，ezdxf 路线，与现管线差异大）。已验证基线：Hutabohu CABLE=203、FDT 151/51/1、CONV-SUM=6942。
- **Hypothesis 2（现状轨迹）**：时间线 07-05 初始化 → 07-07 分层 demo(4dc2627) → 07-11 newmodel 多布局研究 → 07-15 摩洛哥转换器(cec6a0a) → 07-16 Hutabohu CABLE_ALL 拓扑完成(324cb12) → 07-18 五缺陷修复提交(43129f3) → 07-19 解耦重构提交(3e5be1a) 但 ftth_converter 独立运行回归（CABLE=0, ZPM=0, 167 ISOLATED_NODE；根因=Python `from X import Y` 绑定快照 vs 跨模块可变全局冲突），实际采用方案 C（单体 converter.py + cad_common 库并存）→ 07-20 本轮研究会话。14 个 E 级 FAIL 全部 = 法标业务字段（REF_PLAQUE/REGION/PROVINCE/CODE_PTC/NATURE 等）DWG 无源，backlog 确认。方法论已固化：deep-dive 定因 → deep-interview 定规 → team-exec 实施 → team-verify 复核（handoffs/team-exec.md、team-plan.md）。
- **Hypothesis 3（业务灵魂）**：一手竞赛文件（docs/XA-202610…比赛方案.pdf 第 6 页）将"历史 CAD 图纸→GIS 自动转换准确率 ≥90%"列为"数据贯通"硬指标，赛题 2 = 多源异构工程数据融合。plugincad2gis README/technical_plan 定位相同，且明确 "AI offline-only, never in runtime path"。project-memory 三工作流（AI 设计→DWG / 历史 DWG→GIS / 数据反哺 AI 训练+数字资产）中仅历史→GIS 有代码落地。

## Evidence Against / Missing Evidence

- **Hypothesis 1**：v3 在 newmodel 分支内自称 APD Hutabohu 的 canonical direct-DWG 路径并禁用旧 converter.py——在分支语境下它意图取代 experiment 主线；plugincad2gis 的 8 阶段管线（DXF/ezdxf、GCP、network QC）与现有 LibreDWG-ctypes 管线差异大，长期取代可能未排除。experiment/ 内 py_scripts/ 与 python/ 两套 3416+ 行 converter.py 并存，收敛未完成。
- **Hypothesis 2**：notepad "未提交"与 ralplan 头部 "pending approval" 均过期——git 实际 clean，重构已提交于 3e5be1a。团队对 ftth_converter 回归的处置意图无直接记录（consolidation report 附录 A/B 修复方案是否被采纳未知）。
- **Hypothesis 3**："内部定制 QGIS 系统"仅 project-memory 一句孤证；"三工作流并重"无代码佐证；数据集跨印尼/摩洛哥/法国标准，说明项目同时是跨案例转换工程方法研究，不唯一绑定烽火某一具体部署。

## Per-Lane Critical Unknowns

- **Lane 1（业务灵魂）**：该项目究竟是竞赛提交原型，还是正在接入烽火真实内部系统的生产转化项目？（决定后续工作的验收标准与交付形态）
- **Lane 2（技术架构）**：newmodel 分支的 cad2gis_v3 是否计划合并进 main 以替换 experiment/python/，还是永久作为独立验证线？
- **Lane 3（现状轨迹）**：团队是否已决定放弃 ftth_converter 完整拆分（接受方案 C 混合形态），还是计划按 consolidation report 附录 A/B 修复其回归？

## Lane 3 Misplacement / SoT Ownership Scope

| Source | Candidate destination | ownership_scope | Boundary relationship | Default? | Warning |
|--------|-----------------------|-----------------|-----------------------|----------|---------|
| N/A — 本 trace 为代码库内理解型调查，Lane 3 无 MOVE 候选 | — | — | — | — | — |

## Rebuttal Round

- **Best rebuttal to leader（对 H1 的反驳，来自 Lane 3）**：Lane 2 声称 "experiment/python/ 解耦后的 4 核心程序是 main 最新收敛方向"，但 Lane 3 的 consolidation-report-2026-07-19.md 直接记载：ftth_converter.py 独立运行出现基线回归（CABLE=0, ZPM=0, 167 ISOLATED_NODE），团队实际回退到方案 C——converter.py 单体仍是唯一已验证生产路径，解耦模块仅作为库并存。
- **Why leader held**：反驳被接纳为限定条件而非推翻——H1 修正为"架构*意图*是 4 核心解耦，但*实际*生产路径仍是单体 + 库混合形态"。文件结构与 HEAD 证据仍支持 H1 的方向性判断，仅"已成为主路径"的措辞被降级为"目标形态"。两 lane 证据合流后互相加强。

## Convergence / Separation Notes

- Lane 2 与 Lane 3 在"重构状态"上表面冲突（"已收敛" vs "已回退"），归并后为同一机制：**物理上解耦代码已入库，逻辑上生产路径未切换**——合并表述为单一结论。
- Lane 1 与其余两 lane 无冲突，但其置信度受孤证限制；Lane 2/3 的"多轨道/多国家案例"证据反向支持 Lane 1 的限定（项目是跨案例方法研究，不止烽火部署）。

## Most Likely Explanation

CAD2GIS 是一个**以烽火 XA-202610 竞赛为驱动、以"历史 CAD/DWG → QGIS-ready GeoPackage ≥90% 自动化转换"为落地核心的确定性验证管线工程**。技术上以 experiment/ 为主线的 L1-L5 分层管线（LibreDWG ctypes 桥 → cad_common → FTTH 分类/写出 → 拓扑修复 → 样式导出 → convert_all 编排），生产路径当前是 converter.py 单体 + cad_common 库混合形态；五条目录轨道分别承担早期探索（demo）、主验证（experiment）、快照（official）、下一代实验（newmodel v3，未合并分支）、长期插件架构（plugincad2gis）。当前处于"后回退决策点"：五缺陷修复与重构代码均已提交，ftth_converter 回归待处置，14 个 E 级源数据缺口挂 backlog，APD 专用包计划完整但未启动。

## Critical Unknown

团队对三条开放路径的真实优先级：**(a) 修复 ftth_converter 回归完成解耦切换，(b) 启动 newmodel APD 专用包 / 推进 v3 合并，(c) 处理 14 个 E 级缺口或转向新 DWG 目标（摩洛哥 AGA-Al Baraka）**。此未知直接决定项目下一步走向，且 .omc 内无已决记录。

## Recommended Discriminating Probe

grep `ftth_converter` 在 experiment/python/__init__.py、convert_all.py 及最近 session-log/handoff 中的引用，确认其被标记为废弃还是仍有修复计划；同时向用户直接询问三个 critical unknown（这正是 Phase 4 访谈的首批问题）。
