# Deep Interview Spec: 五缺陷修复 —— 拓扑保真 + CAD色样式链路 + SPAN标注 + 解耦闭环

## Metadata
- Interview ID: dd-20260717-topology-color-fix
- Rounds: 3 (+ Round 0 拓扑门)
- Final Ambiguity Score: 15%
- Type: brownfield / Generated: 2026-07-17
- Threshold: 0.2 / Threshold Source: default
- Status: PASSED
- Trace Path: .omc/specs/deep-dive-trace-topology-color-label-fix-compare.md

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal | 0.88 | 0.35 | 0.31 |
| Constraints | 0.84 | 0.25 | 0.21 |
| Success Criteria | 0.80 | 0.25 | 0.20 |
| Context | 0.88 | 0.15 | 0.13 |
| **Total Clarity** | | | **0.85** |
| **Ambiguity** | | | **0.15** |

## Topology
| Component | Status | Description | Coverage Note |
|-----------|--------|-------------|---------------|
| T 拓扑修复 | active | **分析先行**：三方对照实验→技术文档→实施"拓扑保真优先（禁桥）" | Round 0 用户附加前置条件；Round 2 锁定目标形态 |
| S CAD色→QML 样式链路 | active | 实体色提取+ByLayer解算+8层QML+**三轨输出**（sidecar QML + .qgz 工程 + layer_styles 表内嵌 gpkg）含 FDT 分图层组 | Round 3 锁定双轨；用户追问后升级三轨（gpkg 自包含样式） |
| P SPAN 标注 | active | 跨距独立标注线层 + 悬空外键修复 | Round 1 锁定形态 |
| X 解耦确认闭环 | active | LC-001/002 确认排除 + anchor 词表扩充 | Round 0 确认（用户已在 QGIS 目验两簇为非主体） |

## Goal
修复五缺陷：(T) 先做上版/禁桥/约束桥三方对照分析并产出 T 组件技术文档，随后实施"拓扑保真优先"——默认禁用 gap-bridge（仅保留节点截断+0.5m 端点焊接），消灭 117 次跨缆种错接与 203 巨型分量，恢复 FDT_ID 域打标覆盖率（缺陷1+2 数据侧）；(S) 从零搭建 CAD色→QGIS 样式链路（两架构共同盲区，参考 v3 styles.py）：DWG 实体色提取（ACI/truecolor+ByLayer 回退解算）→ 8 FC 层+跨距层 categorized QML → .qgz 工程文件内嵌样式与 FDT-01/FDT-02/LINK 分图层组（缺陷4+缺陷2 呈现侧）；(P) span_records 升级为独立可视标注线层，display_label="49.1 m" 格式，修复悬空外键（缺陷3）；(X) LC-001/LC-002 写入 legend_exclusions.json 走确认闭环，anchor 词表补 FDT/LAYOUT 类词，非主体要素退出 8 FC 交付层（缺陷5）。

## Constraints
- T 严格分析先行：三方实验（上版等效 --skip-chaining / 禁桥 / 同缆种+方向≥cos30° 约束桥）用三指标（跨缆种错接率/FDT覆盖率/分量形态）打分，结果写入技术文档（experiment/guide/ 下新建）后才实施修复；最终形态已拍板为禁桥，实验数据用于论证与验收基准标定
- 颜色提取注意 UTF-16 教训（wiki: libredwg-swig-utf-16）——color 是数值字段应无碍，但任何伴随文本读取必须走 dynapi/dwgread 通道
- S 的 ACI→RGB 映射、ByLayer 解算参考（只读）newmodel/experiment/py_scripts/cad2gis_v3/styles.py:17-59 与 autocad_reader.py:245-253 的解算逻辑；QML 生成复用现有 BOITE.qml 外壳模式
- P 的跨距线层几何沿 DIMENSION 定义点（converter.py:487 act_measurement 已提取）；display_label 格式 "{value:.1f} m"；外键写 gpkg 真 FID（经 source_fragments 映射）
- X 不改三步政策机制本身——只是替用户走完确认步骤（用户已目验两簇为非主体）；SPLICING/FDT/LAYOUT/KETERANGAN 类词入 anchor 配置
- 全链保持既有成果不回归：真标签绑定（43/118/682）、BOITE 融合 43、FDT_ID 机制、证据三表、CRS 参数化默认 3857
- 工程文件（.qgz）生成需在无 QGIS GUI 的 WSL 下可行——用 XML 模板/pyqgis 均可，产物在桌面 QGIS 3.x 打开可用即可

## Non-Goals
- 不做 CABLE 杆距段粒度重构（Round 1 已否）；不做约束桥保长链（Round 2 已否，除非 T 分析实验推翻——届时以技术文档为准）
- 不解析 CTB/STB 打印样式表（v3 也未做；实体/图层色已足够）
- 不实现 AgentPrompts A4 的 CLIP/DEDUP/CLOSE/SLIVER（数据需求未证实）与 A1 quadtree/A5 LLM bridge（Lane 3 判定不该学）
- 不把 v3 census 逐数硬编码合同照搬（绑死单图）

## Acceptance Criteria
### T 拓扑修复（分析先行）
- [ ] 三方对照实验完成，报告含三指标量化表（跨缆种错接率/FDT_ID 覆盖率/连通分量形态对比上版 697-23 与当前 302-203）
- [ ] T 组件技术文档产出（experiment/guide/ 新建，含实验数据、决策依据、修复设计）
- [ ] 实施后：跨缆种错接 = 0；巨型分量瓦解（最大分量成员不再横跨 FDT-01+FDT-02+LINK）；FDT_ID 空值率显著下降（具体基准由实验标定并写入文档）
- [ ] LINK 段（域间连接）语义保留——禁桥不得切断真实的青色 MR.DMHP 跨域杆链段
- [ ] --enable-gap-bridge 开关保留约束桥实现供后续实验（默认关）
### S CAD色→QML 样式链路
- [ ] converter 提取实体 color（ACI index + truecolor）与所在图层色，ByLayer(ACI==256) 回退解算为有效色，存入各 FC 层字段（如 color_rgb/style_key）
- [ ] 8 FC 层 + 跨距标注层全部生成 categorized QML（分类依据 CAD 有效色/线型），style_manifest.json 更新
- [ ] **layer_styles 表内嵌 gpkg**：每层 QML 写入 gpkg 的 layer_styles 表且 useAsDefault=1（参考 v3 styles.py:138-212）——单独把 gpkg 拖入 QGIS 即自动应用 CAD 配色，交付自包含
- [ ] .qgz 工程文件生成：加载全部交付层+QML 样式，含 FDT-01/FDT-02/LINK 三个过滤图层组（subsetString 按 FDT_ID），桌面 QGIS 打开即见 CAD 配色与分图
- [ ] Hutabohu 实测：CABLE 层颜色不再随机——同源图层/缆种的线色与源 DWG 一致（抽样 5 处对照 dwgread color 值）
### P SPAN 标注
- [ ] span_annotations 可视线层（LineString 沿 DIMENSION 定义线，170 条）+ display_label "xx.x m" 格式
- [ ] nearest_cable_id 悬空外键修复：写 gpkg 真 FID（或同时保留 CODE 引用），span→CABLE 关联可 JOIN 验证
- [ ] .qgz 工程中该层默认开启标注，逐跨数值可见
### X 解耦确认闭环
- [ ] LC-001/LC-002 写入 experiment/config/legend_exclusions.json（用户已目验确认）
- [ ] anchor 词表补 FDT/LAYOUT/KETERANGAN 类词（配置化）
- [ ] 重跑后：非主体要素退出 8 FC 层（CABLE 降至禁桥后的域内段数量级、PTECH≈167、SITE≈6）；图例抢号消失（CBL0001 等首批编码归真实要素）；conservation SUM 仍守恒；quarantine_review 中两簇状态为 confirmed-excluded

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| B2 成链是改进 | 用户 QGIS 实测拓扑回退 + Lane 1 复现 429 桥中 117 跨缆种错接 | 长链目标让位拓扑保真；禁桥为默认，桥接留开关 |
| "0 命中"叙事 | 用户澄清：此前 gpkg 其实带标签（未在 QGIS 点开查看） | 修正记录：历史标签为 UTF-16 截断的单字符+合成号，"带标签"与"绑对标签"是两回事；现真标签 161 已实证 |
| 非主体未解耦 = 检测失败 | Lane 2 实测两簇已检出且 LC-001 conf=0.92 | 实为确认闭环未走（json 不存在）；X 组件补走流程 |
| 样式应由验证架构规定 | Lane 3 grep 证实 AgentPrompts 全文无 color/style 规定 | 盲区自补：以 v3 styles.py 为参考搭建 S 组件 |
| SPAN 值缺失 | 实测 SPAN_M 数值健在 | 真问题=悬空外键+呈现层缺失；P 组件重定形态 |

## Technical Context
- 根因位点：topology_builder.py chain_edges L739-1035（桥接条件 L875）、tag_fdt_domains L1058-1231；converter.py 无 color 读取、:1926 SPAN_M 写入、:2845 悬空外键、:2100-2106 排除确认读取；legend_detector.py:88 max_cluster_fraction、:96-100 anchor 词表
- 参考实现（只读）：newmodel/cad2gis_v3/styles.py（ACI→RGB :17-38、QML :83-135、layer_styles 内嵌 :138-212）、autocad_reader.py:245-253（有效色解算）；对照产物 /tmp/preB2_snap.gpkg、/tmp/raw_frags.gpkg（Lane 1 生成）
- 双架构对照与三栏清单全文见 trace 报告 Lane 3 节

## Trace Findings
- 五缺陷根因全部定位（4 个代码直证 + 1 个流程未走）；Lane 1 受控复现 429 桥精确匹配产线记录
- 战略发现：AgentPrompts 无样式规定（盲区）；v3 自补样式全链路可移植
- 该学未学 H 级清单：CAD色→QML→layer_styles（S 组件承接）、provenance fail-closed 默认化（暂不动，维持 warning-first）
- 完整证据链：.omc/specs/deep-dive-trace-topology-color-label-fix-compare.md

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| gap-bridge | core (T, 待禁) | bridge_tol/方向/缆种 | 429 桥→117 错接的根源 |
| 三方对照实验 | deliverable (T) | 三指标评分表 | 产出 T 技术文档 |
| 有效CAD色 | core (S) | ACI/truecolor/ByLayer解算 | 驱动 QML 分类与 .qgz |
| .qgz 工程文件 | deliverable (S) | 8层样式+FDT分图层组+标注开关 | S/P/缺陷2 呈现的统一载体 |
| span_annotations 层 | deliverable (P) | LineString+display_label "xx.x m"+真FID外键 | 源自 DIMENSION 170 条 |
| legend_exclusions.json | supporting (X) | LC-001/002 bbox | 确认闭环载体 |
| FDT分图层组 | deliverable (S) | subsetString by FDT_ID | 依赖 T 修复后的覆盖率 |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 7 | 7 | - | - | N/A |
| 2 | 8 | 1 | 0 | 7 | 88% |
| 3 | 8 | 0 | 0 | 8 | 100%（收敛） |

## Interview Transcript
<details>
<summary>Full Q&A (Round 0 + 3 rounds)</summary>

### Round 0（拓扑门）
**Q:** T/S/P/X 哪些 active？ **A:** 全选 + T 附加前置："先比对分析查找原因，再写 T 组件技术文档"
### Round 1（P / Goal）
**Q:** SPAN 标注最终形态？ **A:** 跨距独立标注线层 **Ambiguity:** 33%
### Round 2（T / Criteria）
**Q:** 拓扑目标形态（保真禁桥 / 约束桥保长链 / 实验裁决）？ **A:** 拓扑保真优先（禁桥） **Ambiguity:** 19.5%
### Round 3（S / Constraints）
**Q:** 样式输出载体？ **A:** 工程文件双轨（QML sidecar + .qgz 内嵌） **Ambiguity:** 15% ✓
</details>
