# Deep Interview Spec: BOITE 语义纠正 + EXISTING POLE 标签 + INFRA 填充

## Metadata
- Interview ID: dd-20260719-boite-semantics
- Rounds: 2 (+ Round 0)
- Final Ambiguity: 12%
- Type: brownfield / Generated: 2026-07-19
- Threshold: 0.2 / Source: default / Status: PASSED
- Trace: .omc/specs/deep-dive-trace-boite-red-square-topology-semantics.md

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal | 0.92 | 0.35 | 0.32 |
| Constraints | 0.85 | 0.25 | 0.21 |
| Criteria | 0.82 | 0.25 | 0.21 |
| Context | 0.90 | 0.15 | 0.14 |
| **Total** | | | **0.88** |
| **Ambiguity** | | | **0.12** |

## Topology
| Component | Status | Description |
|-----------|--------|-------------|
| R BOITE 退出路由节点 | active | 从 NODE_LAYERS 移除 BOITE，CABLE 不再被红色方形强制吸附 |
| P EXISTING POLE 标签补齐 | active | 新增 `EXT.MR.*.P*` 杆标签家族 |
| I INFRA 微小填充 | active | DROP DUCT + HH PIT 映射到 INFRASTRUCTURE |

## Goal
(R) 从 topology_builder.py NODE_LAYERS 移除 BOITE，消除 25 个孤立节点和 18 处虚假截断对 CABLE 拓扑的污染——红色方形 FAT DWG 块保留为 BOITE 点要素（含 DMPH FAT 标签），但不产生路由约束；(P) 为 EXISTING POLE 49 根杆新增标签家族 `^EXT\.MR\.\w+\.\w+\.\w+\.P\d+$` → PTECH，补齐标签 46→49 命中且回收 3 个被错配到 #FF0000 节点的 MR.DMPH.P 标签还给 #00FFFF 节点；(I) INFRASTRUCTURE 正则增补 `.*pit.*|.*handhole.*|.*chamber.*` 并确保 `.*duct.*` 在 `.*drop.*` 之前评估。

## Constraints
- R: 不删除 BOITE 要素本身（CODE/display_label/DMPH 标签保留），仅移出 NODE_LAYERS 元组
- R: --snap-tol 等 CLI 参数保持，BOITE 移除不影响其他拓扑参数
- P: 新增 family 不得与现有 pole 家族（`^MR\.DMPH\.P\d+$`）冲突；两个家族均目标 PTECH，Hungarian 在同一家族内指派
- I: INFRASTRUCTURE 正则只增不删；`.*duct.*` 提前于 `.*drop.*` 可通过调序或前缀锚定实现
- 既有成果不回归：真标签 43/118/682、QML 三轨、span 层、排除闭环

## Non-Goals
- 不重新设计 BOITE/PTECH 分类体系（用户选择"全部 BOITE 退出路由"而非条件过滤）
- 不处理 EXT.MR.XXX.XXX.PXXX 占位标签（1 条）
- 不调查 EPSG:3857 OSM 纬度偏移问题（用户明确推后）
- 不修改成链/禁桥策略（本轮焦点不是 CABLE 链的构型，而是移除虚假路由节点）

## Acceptance Criteria
### R BOITE 退出路由
- [ ] NODE_LAYERS 从 ("SITE","BOITE","PTECH") 改为 ("SITE","PTECH")
- [ ] 端到端后：BOITE 的 ISOLATED_NODE=0（不再跟踪 BOITE 节点度）、CABLE 端点在 BOITE 处的强制吸附归零
- [ ] CABLE 不再因 BOITE 而截断成多余段（node_capture_tol 对应的 node_cut 不含 BOITE）
- [ ] BOITE 要素本身保留（CODE/display_label/DMPH #FF0000 颜色不变）
### P EXISTING POLE 标签
- [ ] LABEL_FAMILIES 新增 family="pole_ext" pattern=`^EXT\.MR\.\w+\.\w+\.\w+\.P\d+$` target_fc="PTECH"
- [ ] 端到端后：PTECH #FF0000（EXISTING POLE）label_provenance="annotation-assigned" 从 3 增至 ≥49（预期 49）
- [ ] PTECH #00FFFF（NEW POLE）label_provenance="annotation-assigned" 回到 71 条全命中（3 条 MR.DMPH.P 回收）
- [ ] 两个 pole 家族的 label_provenance 分布统计正确（annotation-assigned vs synthetic）
### I INFRASTRUCTURE 填充
- [ ] INFRASTRUCTURE 正则增补 pit/handhole/chamber；.*duct.* 类图层不再被 CABLE 截获
- [ ] 端到端后：INFRASTRUCTURE 要素数 ≥ 3（DROP DUCT 1 LINE + HH PIT 3 INSERT）
- [ ] evaluator 规则 3.0 INFRASTRUCTURE 层不再空

## Assumptions Exposed
| Assumption | Resolution |
|------------|------------|
| BOITE = FAT 物理闭合设备 | 元素文档条目 4 + Lane 1 偏转角/孤立度证实为跨距标注定位块 |
| EXISTING POLE 无标签 | 探针实证 50 条 EXT.MR.* 文本在模型空间，格式不匹配现有正则 |
| 3 个 MR 标签属于 EXISTING POLE | 被错配到近处 #FF0000——修复后回收给 #00FFFF |

## Technical Context
- R 位点: topology_builder.py:51 NODE_LAYERS 元组；影响 _split_coords_at_nodes(L690)+repair_edges snap(L859)
- P 位点: schema_config.py:2614 LABEL_FAMILIES 新增条目；converter.py Hungarian 指派无需改动
- I 位点: schema_config.py:1921 INFRASTRUCTURE 正则
- 探针已证：50 EXT.MR.MF.LBB.S02.P* 文本全在 entmode=2 模型空间，格式稳定
- 已有失败模式：DUPLICATE_CODE 冲突（若 MR 词与 EXT 词的杆编号交叉，assign_code 处理后唯一化）

## Trace Findings
- 红色 BOITE 语义误解是当前拓扑最大问题：all 43 为 FAT DWG 红色标注块（元素文档条目 4），不是物理节点
- EXISTING POLE 50 标签已在 DWG 且格式明确（EXT.MR.MF.LBB.S02.P057），管线的正则竟一次都没碰到
- 完整证据链见 trace 报告
