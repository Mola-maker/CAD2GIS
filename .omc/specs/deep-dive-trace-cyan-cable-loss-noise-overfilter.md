# Deep Dive Trace: cyan-cable-loss-noise-overfilter

## Observed Result
四项目 auto-convert 后三个问题：1) 青色线缆全丢失；2) 三项目 OSM 坐标 (0,0) 附近需人工匹配；3) 噪声去除不彻底（Hutabohu 图例残留/lamteh_main/kletek 注释框→BOITE）或过分（SF 主体被滤）。

## Ranked Hypotheses
| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | SF "过去噪" 是评估误判——CABLE/PTECH 未过滤，真正瓶颈是分类 abstention | High | Strong | conservation_ledger + delivery_counts 直接证据 |
| 2 | (0,0) 根因 = assess_coordinate_domain 对 EPSG:3857 全局 CRS 失效 + validate_project magnitude 启发式是死代码 | High | Strong | 3 文件代码路径 + manifest 证据 |
| 3 | 图例污染 = centroid gap 聚类无法捕获与主体空间交错的图例（Hutabohu 右上角） | High | Strong | cluster bbox vs body_bbox 对比 |
| 4 | INSERT→BOITE 误分类 = insert_layer_families fallthrough（匿名块 *U + Title Block 在 FAT/CLOSURE 层） | High | Strong | mapping_registry + inventory 证据 |
| 5 | 青色线缆丢失 = regex 缺口（hutabohu GRT 前缀）+ SF 1 条 FO 被 LLM 误判 legend | Medium | Moderate | GPKG 数据确认 2+1 条丢失 |

## Evidence Summary by Hypothesis

**H1 (SF 评估误判)**: delivery_counts={"BOITE":1,"CABLE":15,"PTECH":7} — 主体特征成功交付。coverage_records: 7 PTECH 缺 label、30 CABLE 层不匹配 route regex——分类 abstention 而非去噪。LC-001/LC-002 排除的 198 实体中无 CABLE/PTECH 交付特征。

**H2 ((0,0) CRS 死代码)**: coordinate_domain.py:56-75 对 EPSG:3857 只查 area-of-use（全局→恒过）；project_profile.py:822-841 的 max_abs<100k 检查在 validate_project 中，convert 管线（pipeline.py:1210-1214）直接调 assess_coordinate_domain 绕过。三项目 manifest 均显示 PLAUSIBLE_DECLARED_CRS_DOMAIN passed:true。

**H3 (图例交错)**: hutabohu body_bbox=[13680802..13688042, 68391..70649]，LC-001 bbox=[0,0,310,187]——右上角图例 centroids 在 body 范围内无法分离。lamteh_main LC-001 Y 范围 [-9283,281] 与 body Y [-9598,-7820] 完全重叠，仅 X 分离捕获。

**H4 (BOITE 误分类)**: lamteh_main insert_layer_families.BOITE=["Basic Map","CLOSURE","FAT","FAT Arar","FAT CODE","FAT_Info"]；inventory 显示匿名块 *U1612-*U1631 在 FAT 层、Title Block 在 FAT CODE 层——block_name 不在 block_families 但 layer 命中 fallthrough（semantics.py:440-448）。76 个 BOITE 特征来自 FAT(38)+FAT_Info(38)。

**H5 (线缆丢失)**: 实际交付 CABLE: hutabohu=7, lamteh_main=16, lamteh_sf=15, kletek=7。真实丢失: (a) hutabohu 2 条 GRT.100.0X01 MAINFEEDER/SUBFEEDER CABLE（25m，layer 不在 regex）→ unmatched_route_layer; (b) lamteh_sf 1 条 FO 24 CORE LINE C - FDT 1（1290.5m）被 LLM assist 判 legend 排除。positive_route_color_aci 退役正确（ACI=4 实体 = SLING WIRE/FDT STRUCTURE 非光纤）。

## Evidence Against / Missing Evidence
- **H1**: SF 只有 23 个交付特征，体感"太少"真实存在；209 个未标记未分类实体中是否含应分类 CABLE 的几何未验证
- **H2**: 无法排除 DWG 有意以本地坐标保存 + CGEOCS 作文档标记；AI 在 onboarding 时看不到 assess_coordinate_domain
- **H3**: 未直接测量 hutabohu 右上角图例实体数
- **H4**: 注释框实体类型（LEADER vs INSERT）未直接验证
- **H5**: 未视觉确认 SF 被排除的 FO 线是真缆线还是图例示意图元素

## Per-Lane Critical Unknowns
- **Lane 1 (代码路径)**: SF 被排除的 FO 24 CORE LINE C - FDT 1（1290.5m）是真缆线还是图例示意图？Hutabohu 2 条 GRT 线（25m）是连接桩还是真路由？
- **Lane 2 (配置/编排)**: magnitude 启发式缺失是有意架构决策（convert 保持纯几何）还是疏漏？
- **Lane 3 (测量/假设错配)**: SF 的 209 个未分类 plan_domain 实体中，多少是应分类为 CABLE 的几何？

## Rebuttal Round
- 最佳反驳 H1（leader）: SF plan_domain 仅 430 实体 vs MAIN 3497（raw 实体数相近 59369 vs 63483）——plan_domain 选择本身可能上游受限，非仅分类 abstention
- 为何 H1 仍成立: plan_domain 选择与空间去噪是独立阶段；证据显示去噪排除的 198 实体不含交付特征，CABLE 15 成功交付

## Convergence / Separation Notes
- H2 与 H3 独立（CRS 问题 vs 空间检测问题）
- H3 与 H4 部分收敛：图例污染 = 空间检测（H3）+ 分类配置（H4）双层问题——同层同 block 名使任何逃逸空间检测的图例必进输出
- H1 与 H5 独立但都涉及"丢失感知"：H1 是分类 abstention，H5 是 regex 缺口 + 去噪误判

## Most Likely Explanation
1. **"青色线缆全丢失" 与证据不符**——实际 2+1 条丢失（hutabohu GRT regex 缺口 + SF 1 条 LLM 误判），非全丢
2. **(0,0) 坐标** = assess_coordinate_domain 对 EPSG:3857 失效（全局 CRS 恒过）+ convert 绕过 validate_project 的 magnitude 启发式（死代码）
3. **噪声问题** = 三独立机制: (a) centroid gap 聚类无法捕获空间交错图例（Hutabohu 右上角）; (b) insert_layer_families fallthrough 把匿名块/Title Block 归为 BOITE（lamteh_main/kletek 注释框）; (c) SF "过去噪"是评估误判——主体未滤，真问题是分类 abstention

## Critical Unknown
SF 被 LLM 排除的 FO 24 CORE LINE C - FDT 1 实体（1290.5m）是真缆线还是图例示意图——决定 H5 是"去噪误判"还是"正确去噪"。

## Recommended Discriminating Probe
视觉检查 SF DWG 该实体区域（坐标 x~[4000-23000], y~[-9800,-8100]），或比对 LC-001 member_ids 与 source_route_evidence entity_keys 交集。

## 补充取证（2026-08-01 访谈确认）
- 用户确认：SF 被 LLM 排除的 FO 24 CORE LINE C - FDT 1（1290.5m）是**真实缆线** → H5 坐实"去噪误杀真缆线"，修复方向=route 实体豁免（方案 A）
- SF unmatched_route_layer 30 条分布：DESIGN SUMMARY 19（LINE 18+LWPOLYLINE 1，handle 连续 1CCF00-1CCF15，长度一致 255.8m→示意图图元）+ TITLE BLOCK 10（图框线）+ DROP DUCT 1（疑似真缆线）
- DESIGN SUMMARY 线 legend_flag=''——**未被 LLM 标记**（gap 聚类未分离），留在 source.gpkg 但正确 abstain 不进 CABLE
- 结论：SF 过去噪=评估误判（主体交付成功）；"图例保留"=示意图线从未被检测器标记

## Lane 2 附加交付：人工 web 匹配启动链
```
前置: auto-convert 完成 + pip install cad2gis[review]
Step 1: cad2gis review baselines/lamteh_main/run --port 8765
Step 2: 浏览器打开 http://127.0.0.1:8765（左=CAD坐标窗格, 右=OSM底图窗格）
Step 3: 左窗格点 CAD 参考点 → 右窗格点对应 OSM 位置（≥4 train + 3 check，跨两轴）
Step 4: 点 Export → 生成 {workspace}/web_gcp_profile.json
Step 5: cad2gis convert <DWG> --run-dir <run_registered> --project <proj> \
        --gcp-profile <workspace>/web_gcp_profile.json
```
关键文件: review_server.py:1282 (入口), 1078 (GCP 导出 API), georef.py:259 (DeliveryTransformer), calibration.py (GCPProfile)
