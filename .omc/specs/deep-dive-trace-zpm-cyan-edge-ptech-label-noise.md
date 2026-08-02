# Deep Dive Trace: zpm-cyan-edge-ptech-label-noise

## Observed Result

三问题（auto-convert 已跑完，ZPM 划区正确前提下）：

1. **青色吊线在 ZPM 区域/边界附近丢失**：
   - hutabohu 三处（EPSG:3857）：(13680836.70,69844.95)-(13680901.25,69838.29) 之间、(13680919.91,70208.91)-(13680976.74,70271.43) 之间、(13683132.26,68441.34)-(13683153.51,68417.89) 之间
   - lamteh_main 一处：(10612863.63,606848.07)-(10612857.92,606914.02) 之间
   - kletek 无此现象
2. **所有项目 PTECH display_label 全缺失**（delivery.gpkg 中 PTECH 表 display_label=''，label_provenance='UNAVAILABLE'）
3. **噪声**：
   - i. 图例特征未被识别清除，仍与 CABLE/PTECH/BOITE/SITE 耦合，坐标空间聚类效用存疑
   - ii. BOITE 真实点 vs 注释点未区分（LLM 已分配颜色：hutabohu 有效=#FF0000、lamteh_main/kletek=#FF7F00）；lamteh_main/kletek 的 SITE 层 #FF0000 点实为 BOITE 噪声

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | 青色吊线被 boundary_band 误杀（_ANNOTATION_FRAME_TYPES 含 LWPOLYLINE） | High | Strong | 4/4 丢失线全部 flag=boundary_band；豁免 sling_wire 后 4/4 恢复 |
| 2 | PTECH 标签双失效：annotation family 零 assignment + registry 无 PTECH display_label 规则 | High | Strong | 四项目 annotation_assignments={}；display_label_rules 仅 IMB；warehouse 写路径正常（IMB 有标签） |
| 3 | 图例聚类 gap 阈值过宽，交错注释块不可见 | Medium-High | Strong | FDT-Info 块距主体 <267m（Y）/1274m（X）阈值内不可见；legend_flag="" 未被任何 detector 捕获 |

## Evidence Summary by Hypothesis

### H1：boundary_band 误杀青色吊线（问题 1）
- 用户 4 处坐标回溯：hutabohu fid 2489/1616/2771、lamteh_main fid 5912，全部 source.gpkg accepted + model + aci=4，全部不在 delivery CABLE
- spatial_filter.py:49 `_ANNOTATION_FRAME_TYPES = {TEXT, MTEXT, LEADER, MLEADER, MULTILEADER, LINE, LWPOLYLINE}`——LWPOLYLINE 被纳入"注释引出线"候选
- spatial_filter.py:79-96：body bbox 3% 边界带内所有线实体 centroid 命中即 flag boundary_band
- 之前的豁免只含 zpm_boundary 角色（FAT AREA 层），SLING WIRE 不在豁免名单 → 被排除
- **修复验证**：boundary_exempt_layers 加入 sling_wire 角色后，4/4 条线 post-denoise 保留；lamteh_main 5912 → CABLE-CAD-1B1FD6

### H2：PTECH 标签双失效（问题 2）
- warehouse.py:751 写入 display_label 正常（IMB 有标签）→ 排除写入环节
- semantics.py:445 `model_entities = [e for e in entities if e.cad_role == "model"]` → annotation 循环只遍历 model 实体
- 三项目各自机制：
  - **lamteh_main/sf**：POLE ID TEXT 在 block_definition（cad_role=block_definition, BLOCKDEF:*U41），被 model 过滤 → annotation_discovery_failure_counts={} 零发现
  - **hutabohu**：annotation 是 model 角色但 4 个 family 的 target_layer_pattern="(?i).+" 全部重叠 → 172 个 PTECH 全部被 overlapping_target 排除（semantics.py:639-656）→ annotation_assignments={}
  - **kletek**：require_same_layer=true 但 annotation 在 POLE ID 层、INSERT 在 NEW POLE FDT 1 层 → 41 outside_tolerance
- semantics.py:391-406 `_registry_display_label` 无 PTECH 规则 → ("", "UNAVAILABLE")；四项目 display_label_rules 仅 IMB
- 特征创建（semantics.py:516-531）锁死空 label，全程无后续覆盖

### H3：图例聚类与噪声归属（问题 3）
- 聚类**确实生效**：lamteh_main 1548 flagged / 1538 auto-excluded（97%），LC-001(521)/LC-002(1027)
- FDT-Info 注释块（18 INSERT + 98 TEXT，aci=1 #FF0000，属性 D:-/F:X/B/L:- 文档字段）与主体交错（<267m Y / <1274m X gap 阈值）→ gap 聚类结构性不可见
- 因果链：FDT-Info 交错 → gap 聚类不可见 → 无 anchor 文本 → legend_flag="" → 未在 confirmed cluster → semantics 的 insert_layer_families['SITE']（FDT/FDT-Info/HUB/OLT）无条件归类 SITE → styles.py 无按颜色/来源过滤 → #FF0000 渲染进 SITE
- styles.py:349-428 write_styles 无 aci/source_layer 排除机制

## Evidence Against / Missing Evidence

- **H1**：kletek 无此现象的原因未验证（推测 kletek SLING WIRE 不在 body 边界带内——body bbox 边缘与吊线分布无交集，或 SLING 线少 6 条恰好不在带内）
- **H2**：lamteh_main INSERT 的 libredwg_block_attributes_unread（属性读取失败）是否导致 POLE ID 文本进不了 INSERT 属性——未确认；pole ID 是 ATTDEF 解析还是 model 空间独立 TEXT 未定
- **H3**：BOITE 注释点（FAT_Info aci=170）与真实点（FAT aci=30）的"两种颜色"对应关系（用户说 LLM 已分配）在代码中的落点未确认；styles 排除机制的接口设计未定

## Per-Lane Critical Unknowns

- **Lane 1（cyan-sling-edge-loss）**：kletek 不丢失的边界条件（吊线是否在 3% 边界带外）；修复后四项目重跑确认无回归
- **Lane 2（ptech-label-chain）**：lamteh_main 的 pole ID 文本在 DWG 中是 ATTDEF（需解引用 INSERT 属性）还是独立 TEXT；统一修复应改 semantics（model 过滤/重叠排除）还是 registry（display_label_rules/require_same_layer）
- **Lane 3（legend-cluster-noise-ownership）**：图例聚类改进方向（降 gap 阈值 vs 非空间信号如层名黑名单）；BOITE 注释点区分信号（颜色？层？块属性？）；SITE 误归类修复面（insert_layer_families 移除 FDT-Info？）

## Lane 3 Misplacement / SoT Ownership Scope

（本问题无 MOVE 候选——全部为项目内代码修复，无跨边界迁移）

## Rebuttal Round

- **Best rebuttal to leader H1**：会不会是 route_exempt 或 legend cluster 排除的？——反证：三线 flag 明确为 boundary_band（flag_map 直接可见），且只豁免 sling_wire 后即恢复，排除其他机制
- **Best rebuttal to leader H2**：会不会是 topology/coordinate_domain 覆盖了 label？——反证：run_manifest 显示 annotation_assignments={}（赋值发生在匹配环节之前），拓扑只改几何不改 label
- **Why leaders held**：均为直接可复现的数据/诊断证据，非推测

## Convergence / Separation Notes

- H1 与 H3 独立（H1 是 boundary_band 对网络层的误杀，H3 是聚类对交错注释块的结构性盲区）
- H2 的三项目机制不同但收敛于同一结论：annotation 链路整体未生效 + 无 display_label fallback 规则——修复需分层（语义链路 + registry 规则）

## Most Likely Explanation

1. **问题 1**：boundary_band 检测器把 body bbox 3% 边界带内的所有 LWPOLYLINE（含 SLING WIRE 吊线）当注释引出线排除；之前的豁免只覆盖 zpm_boundary 角色未覆盖 sling_wire → ZPM 边缘/区域内的青色吊线被系统性误杀（已修复并验证 4/4 恢复）
2. **问题 2**：annotation 匹配链路三项目各自断裂（block_definition 过滤 / family 重叠排除 / require_same_layer 错配）+ 四项目 registry 均无 PTECH display_label 规则 → display_label 全程空
3. **问题 3**：图例聚类对交错注释块结构性不可见（gap 阈值过宽）；FDT-Info 无条件归类 SITE；styles 无按颜色/来源过滤

## Critical Unknown

问题 2 的统一修复策略（改 semantics 匹配逻辑 vs 补 registry 规则 vs 两者）——需要 interview 确认用户对 lamteh_main pole ID 文本来源（ATTDEF/独立 TEXT）的认知与期望修复范围。

## Recommended Discriminating Probe

对 lamteh_main 跑一次性语义补丁（model_entities 放宽到含 block_definition 的 annotation carrier），验证 assignment 是否出现；同时检查 DWG 中 pole ID 文本实体的真实来源（ATTDEF vs TEXT）。
