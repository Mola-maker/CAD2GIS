# Deep Dive Trace: newmodel-gogogo-multi-layout-study

## Observed Result
研学 newmodel 分支 gogogo 提交（已提取至 /newmodel，50 文件）：另一团队基于同一 Hutabohu DWG 的独立重构（cad2gis_v3），带三个用户已知问题进行对照取证。**三项前提修正 + 我方两项重大缺陷实锤**。

## Ranked Hypotheses（对三个已知问题的裁决）
| Rank | 结论 | Confidence | Evidence Strength | Why |
|------|------|------------|-------------------|-----|
| 1 | 问题2成立且比预想严重：我方真实 CAD 标签命中率 ≈ **0%**（非"部分丢失"）；v3 靠"家族门+匈牙利全局指派"达成 BOITE 43/43、IMB 682/682 | High | Strong（双方 gpkg 产物实测） | 我方 annotation_text 中匹配真标签模式 DMPH-*/MR.DMPH.P* 的 = 0 条；CODE 100% 填充全是合成序号（PBO0001…）造成的假象 |
| 2 | 问题3需要修正：newmodel **不是**"两图纸各自配准导致衔接错位"——它读同一 DWG 的 Model 空间几何，FDT-01/02 布局只供标签；断连根因是"衔接段在源图中只有 SLING WIRE 折线+SPAN CABLE 'EMR' 尺寸标注，v3 政策拒绝提升为缆且回滚了 v2 的桥接" | High | Strong（代码直证+产物实测） | georef.py:14-30 单一变换器；衔接带实测无重复缆线；v2 曾有 bridge（缝了又拆） |
| 3 | 问题1需要修正：newmodel 的多图纸利用是"几何仍全取 Model 空间，纸空间布局**降级为属性/证据源**"（FDT-* 布局采掘 FDT_ID/FAT_SEQUENCE，Topology 布局只入证据，LEGEND 定样式）——不是从多图纸取几何 | High | Strong（代码直证） | semantics.py:222 仅 cad_role=="model" 成 GIS 要素；autocad_reader.py:983-990 纸空间几何判重复弃用 |

## Evidence Summary by Hypothesis

### Lane 1 — 多图纸提取与主体解耦
- **读取器**：AutoCAD 2027 Core Console + AutoLISP（`(ssget "_X")` 全布局实体，DXF 410 组码取布局名，autocad_reader.py:26-141,289-336）+ win32com 回退（:893-959）。**纯 Windows 方案**，非 Windows 直接 raise（:998-999）；cer.log 证实 AutoCAD 真实运行且崩溃过。
- **布局角色分类器** `classify_layout_role`（:157-172）：Model→几何唯一来源；FDT-ALL/FDT-\d+/PLAN→plan（几何降级、属性采掘：topology.py:140-164 提取 FDT_ID/FAT_SEQUENCE，:187-201 布局↔连通分量按 FAT 基数全排列唯一最优匹配，非唯一即弃权）；*Topology*→topology_evidence（永不成 GIS）；LEGEND/CABLE TYPE→style_legend。规则注册于 apd_mapping_registry.json:121-130。
- **非主体解耦五层**：布局隔离门控（:731-792）→ 图纸内空间围栏（LEGEND 锚文本+右 10% 缓冲带、闭合线跨度≥85% 判 frame，:551-585）→ 块名正则 ETIKET|TITLE|FRAME|BORDER→ 模型空间图例簇 X 向间隙聚类（:588-608，分离 10 图例样本/212 平面符号）→ 白名单正向注册表+源 SHA-256 绑定 fail-closed（config.py:92-94）+ 每实体 terminal_disposition 守恒记账 + census 回归门（ingest.py:27-35）。
- **架构差异**：我方=单段直写无留痕；对方=不可变事实采集→角色分割→hash 绑定注册表→弃权式拓扑→交付/证据双 GPKG。

### Lane 2 — v3 标签命中机制
- **v3 四级流水**（semantics.py）：①文本家族门（正则 fullmatch 才参赛：fat=`^DMPH-\d+\.\d+\.[A-Z]\d{2}$`→BOITE，pole=`^MR\.DMPH\.P\d+$`→PTECH，registry:133-134）②候选边（同家族、≤15m、次优差≤0.01m 判 multiple_optima 弃权，:80-116）③**矩形匈牙利全局一对一指派**（:22-154，纯 Python 零依赖）④写回 CODE/display_label/provenance + labels Relation（:297-312）。旁路：BOITE.CAPACITE 直读块属性 ATTRIB `FAT`；IMB 由 HOME NUMBER 文字自身成要素。
- **v1→v2→v3 演进**：v1 贪心最近邻（BOITE 21/43，多对一碰撞+平局弃权是败因）；v2 标签原地踏步、伪造 3 条桥接段（几何造假）；v3 同样 15m 容差下 BOITE 43/43、PTECH 118/167（49 根杆源图无标签，置 UNAVAILABLE）、SITE 2/2、CABLE 6/6、IMB 682/682。
- **evidence 体系**：独立 apd_evidence.gpkg 18 表——annotation_assignment_candidates（每条候选边留痕）、field_provenance（非空字段无来源即 RuntimeError，evidence.py:376-382）、conservation_ledger（9391 实体处置守恒，SUM 校验，:492-497）。
- **我方实锤缺陷**（产物实测）：真标签命中 0 条（annotation_text 样本为"D"等噪声）；BOITE 95 vs 真值 43（过检出）；CABLE 990 碎段 vs 源 6 条整线；合成 CODE 造成 100% 填充假象。我方机制缺陷位点：converter.py:194（15m）、:648-671（贪心、跨家族、目标已有文本即不写）。

### Lane 3 — 跨图纸拓扑断连实证
- **实测**：v3 CABLE 6 条恒 2 连通分量（3+3），分量间最近端点距 426.57m、最近几何距 116.55m；v2 曾以 3 条 CABLE-SPAN 桥接段并为 1 分量（method=`DWG_DERIVED:span-dimension-route-component-bridge`，沿电杆链 P117→P118→…），v3 主动回滚（ARCHITECTURE_V3.md:137-139）。
- **根因**：衔接带源图仅有 2 条 SLING WIRE 折线（被判 graphic_only 弃置）+ 6 条 SPAN CABLE 层 "EMR" 尺寸标注（23.54+44.88+50.00=118.42m ≈ 缺口 116.55m）；v3 政策三连 `dimension_is_cable_geometry=false`/`generic_line_is_cable=false`/`force_route_components_connected=false` ⇒ 衔接路由从交付完全消失。
- **结构性风险**：`_match_groups_to_layouts` 硬要求分量数==布局数（topology.py:189）——断连是 FDT 身份推断的承重结构，census 契约把 source_route_components:2 钉死为验收门（缺陷固化）。
- **可取设计**（独立于断连）：多最优弃权 `_nearest_unique`（:21-30）；确定性关系键 sha256（:16-18）；源图自诊断 `_source_graph_stats`（重复/反向段检测，:75-111）；跨距标注-几何交叉验证（:355-360）；段级守恒（:409-419）；不动源几何、记录 displacement_m。

## Evidence Against / Missing Evidence
- H1：我方 0 命中的**根因**未判定（提取丢失/被当要素消费/单位失真三选一，探针未跑）。
- H2："断连即出错"未终裁——v3 文档主张"2 分量=2 个 FDT 服务域是设计事实"；反证偏向出错（标注在 SPAN CABLE 层而非 SPAN SLING 层、死端距桥链仅 4.89m）。
- H3：LibreDWG 侧纸空间/块属性等价提取能力未验证。

## Per-Lane Critical Unknowns
- **Lane 1**：LibreDWG 能否等价取得 (a)纸空间布局实体+布局名 (b)匿名动态块 EffectiveName 与 ATTRIB（FDT_ID/FAT_SEQUENCE/FAT）——决定布局采掘与块属性直读的移植可行性（Windows+AutoCAD 不可作为我方生产依赖？需用户确认环境约束）。
- **Lane 2**：我方真标签 0 命中的根因三选一：(a)LibreDWG 提取阶段 TEXT 丢失/图层错标 (b)标签文本被分类阶段当要素消费 (c)_meters_to_units 容差换算失真。
- **Lane 3**：EMR 跨距链上是否实际敷设光缆（FDT-02 是否经此受馈）——决定"2 分量"是设计真实还是转换丢失，即我方是否应保持整体连通、newmodel 双域论是否成立。

## Lane 3 Misplacement / SoT Ownership Scope
N/A —— 研学任务，无 MOVE 候选。

## Rebuttal Round
- 对 H1 最强反驳："我方 annotation_text 有 59/95 填充，说明挂接在工作"。驳回：填充值为"D"等噪声文本，真标签模式匹配 0 条——挂上的是错误文本，等价于全丢。
- 对 H2 最强反驳（newmodel 文档立场）："2 分量是设计事实非缺陷"。保留：需 EMR 段敷缆事实终裁（探针：对照 DESIGN SUMMARY 声明缆长与 route_dimension_sums）。

## Convergence / Separation Notes
- 三分道收敛于同一元结论：**newmodel 的价值不在"多图纸取几何"（它没这么做）而在三件事——布局角色化利用（属性采掘）、全局指派标签绑定、证据留痕/守恒记账体系**；其致命依赖（Windows+AutoCAD）与断连政策不可照搬。
- 我方优势确认：Model 整体几何提取 + WSL/LibreDWG 可运行性 + 三态拓扑吸附；我方新增已知缺陷：标签 0 命中、BOITE 过检出（95 vs 43）、CABLE 碎段化（990 vs 6）。

## Most Likely Explanation
newmodel 与我方读的是**同一张 DWG**：它以 AutoCAD COM/AutoLISP 独占多布局访问，把纸空间布局用作属性/证据源而非几何源；标签命中的关键是家族正则门+匈牙利全局指派（同 15m 容差下 v1 21/43→v3 43/43）；衔接断连是"源图衔接段无正版缆折线+v3 政策拒绝推断"的产物，且被其布局匹配算法结构性依赖。我方管线在标签绑定上全面落后（0 命中+合成 CODE 假象+BOITE 过检出+CABLE 碎段），在环境可移植性与整体几何连续性上占优。

## Critical Unknown
（综合）我方 0 命中根因（Lane 2 三选一）+ LibreDWG 纸空间/块属性能力（Lane 1）+ EMR 段敷缆事实（Lane 3）——三者分别决定"怎么修标签"、"能移植多少"、"拓扑保持什么政策"。

## Recommended Discriminating Probe
三探针并行：①我方管线 dump annotations 列表 grep DMPH-* 文本（一步三分 0 命中根因）；②WSL 用 dwgread dump 纸空间布局实体+块 EffectiveName/ATTRIB 与 newmodel census 比对（判移植域）；③读 v2 gpkg cad_design_summary_evidence 对照 DESIGN SUMMARY 声明缆长 vs route_dimension_sums（裁决 EMR 段性质）。
