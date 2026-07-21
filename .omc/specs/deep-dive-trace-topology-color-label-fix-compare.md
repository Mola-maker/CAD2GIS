# Deep Dive Trace: topology-color-label-fix-compare

## Observed Result
对当前交付 gpkg（SHA e06aaca5）的五缺陷研学 + 双架构对比。**五个缺陷被三分道逐一定量实证，其中三个根因定位为一手代码直证，一个缺损属已判决待确认，一个属于空外键+产出形态缺口**。

## Ranked Hypotheses（对各缺陷根因的裁决）
| 缺陷 | 根因 | Confidence | Evidence | Why |
|---|---|---|---|---|
| 1 拓扑回退 | gap-bridge 无方向/缆种约束，394/429 桥跨源缆种（Service Core×Expansion Core 等），117 跨缆种错接；桥接在复杂路口制造虚假连接，导致 203 节点巨型分量把 FDT-01(151)+FDT-02(51)+LINK(1) 熔为一体 | High | Strong[代码直证+受控复现] | topology_builder.py L875 只验 `d<=bridge_tol and d<nda and d<ndb`，零方向/缆种过滤 |
| 2 CABLE未按FDT分图 | FDT_ID 字段**已在 gpkg 中**（151+51+1+424空），68% 为空因分域 Dijkstra 被 203 巨型分量吞没（同根因）；QGIS 缺 subsetString 过滤规则与分图层主题渲染 | High | Strong[产物实测+代码直证] | .qgz 全层 singleSymbol，0 个 subsetString；qgis/styles/ 仅 BOITE.qml |
| 3 SPAN标签缺单位 | span_records 的 `nearest_cable_id` 是**悬空外键**（指向内部 global_id 不写入 CABLE 层）；170 跨映射到仅 22 条成链 CABLE；CABLE 627 条 display_label 全空→QML 标注无内容 | High | Strong[产物实测+代码直证] | converter.py:1926 SPAN_M 为裸浮点无 "m" 后缀；:2845 写 global_id 非 gpkg FID |
| 4 样式颜色随机 | converter.py 全程**未读 DWG 实体 color 属性**（仅 layout_miner 的 topology_evidence 有 color）；BOITE 用硬编码调色板轮转；其余 7/8 层无 QML，QGIS 随机配色 | High | Strong[代码直证+产物实测] | converter.py 无 color 读取代码；grep "color" 证实 |
| 5 非主体未解耦 | 两簇**已被 legend_detector 检出**（LC-001 1125成员 "SPLICING"面板 + LC-002 67成员图例），但确认机制未走——`legend_exclusions.json` 不存在，三步政策卡在第一步 | Medium | Moderate[产物实测] | converter.py:2100-2106 仅 review-only；无确认文件 |

## Evidence Summary by Hypothesis

### Lane 1 — 拓扑回退与分图
- **对照基线**：上一版 gpkg（commit 324cb12 产物 CABLE 541 有效线）来自旧转换器，非"990 碎段+三态吸附"版——该版不在 git。等效对照通过 --skip-chaining 生成。
- **差异实测**：当前 302 连通分量 vs 上版 697，但**最大分量 203（占 32%）**涵盖 FDT-01+02+LINK 全部域、跨 3 种以上缆种。上版最大分量仅 23。
- **gap-bridge 误差模式**（topology_builder.py L739-1035）：429 桥中 394 在 105 个多缆交会组（≥3 开放端）贪心配对；282/429 双端转角>90°（侧向焊接平行缆）；117/429 跨 DWG 缆种错接。
- **FDT 分图**：FDT_ID 字段在 gpkg schema 中存在——缺口是 424/627 空值（巨型分量淹没种子）+ QGIS 侧无 subsetString/分类样式，不是字段缺失。

### Lane 2 — 标注/样式/解耦
- **SPAN 标签**：span_records 表 SPAN_M 字段有数值（如 49.0986）；两硬伤 = 外键悬空 + 170→仅成链至 22 条 CABLE 上。CABLE 627 条 display_label 全空，QML 标注绑定 display_label→地图无跨距标签。
- **颜色/样式**：gpkg 各层 schema**无任何 color 字段**——converter.py 全程不读实体颜色。newmodel 可取核心设计：实体 ACI==256→图层回退解算 + truecolor 优先 + ACI→RGB 映射 + 每层 categorized QML + layer_styles 表内嵌 gpkg 为默认样式（styles.py:138-212）。**重要发现：AgentPrompts 全文（1563行）对 color/style/QML 无任何规定——这是两份架构共同的盲区，但 v3 自补了。**
- **SPLICING/FDT "未解耦"其实是"已检出但未确认"**：LC-001（1125成员, 'SPLICING'锚词, conf=0.92）和 LC-002（67成员, conf=0.6）覆盖了两个面板——legend_exclusions.json 不存在，确认闭环未走。

### Lane 3 — 双架构对比
- **逐阶段对照表**已产出（六阶段×四维度），关键发现：① AgentPrompts 无样式体系规定（盲区），v3 自补 styles.py 全链路；② 两份架构对多布局利用的设计思想根本差异——AgentPrompts 仅丢弃纸空间（guide:123），v3 将布局作拓扑证据；③ 验收门禁强度相反——v3 fail-closed（RuntimeError），我方 warning-first（evaluator 8.x 默认 INFO）。
- **三栏清单**：该学未学 7 项（H: CAD色→QML 全链路、字段provenance fail-closed默认化；M: census回归合同、SPAN尺寸签名分区、端口候选审查），已学 6 项，不该学 4 项。

## Evidence Against / Missing Evidence
- H1：gap-bridge 中"合法续接"与"错接"的真值比例未定——方向/缆种代理指标已给出，但用户 QGIS 检查才能终裁哪些连接属于"道路拓扑错误"。
- H3：期望形态（pole-to-pole 粒度挂 span 文本）的具体 UI 交互方式待用户确认。
- H5：两布局 VIEWPORT 指向的 model 区域是否精确对应 LC-001/LC-002 待实证（dwgread 反算 IoU）。

## Per-Lane Critical Unknowns
- **Lane 1**: 429 桥中合法续接比例——加方向/缆种约束后的巨型分量瓦解程度与 FDT 覆盖率
- **Lane 2**: 期望的 SPAN 标签最终呈现形态（CABLE 段 label 字段 vs 独立标注层 vs QGIS 数据定义叠加）
- **Lane 3**: layer_styles 内嵌方案在当前 gpkg 是否已存在入口（gpkg_contents 有 layer_styles 表？）

## Rebuttal Round
- 对 H1 反驳："上版 CABLE 990 碎段 697 分量也是拓扑混乱"。成立但上版碎段化是纯碎段不假连接，当前是把碎段**错接**成虚假超长缆——后者更难在 QGIS 中发现和修正。
- 对 H4 反驳："converter.py 写了 BOITE.qml"。驳回：QML 用硬编码调色板轮转，非 CAD 实体色；其余 7 层完全无样式——链条从 DWG 实体色到 QGIS 默认样式不存在。

## Convergence / Separation Notes
三个 lane 独立发现但高度协同：Lane 1 的 gap-bridge 错接（拓扑回退）与 Lane 2 的 SPAN 外键悬空 + 颜色缺失是三个独立机制断点；Lane 3 揭示的"AgentPrompts 无样式规定"解释了 H4 的深层原因。**五缺陷中前四个需要修复管线代码，第五个仅需补齐确认机制流程（半自动化）。**

## Most Likely Explanation
B2 成链的 gap-bridge 条件（topology_builder.py L875）在一个多缆种碰撞、间距<5m 的街区产生了 429 次桥接，其中 117 次跨缆种错接、394 次在复杂路口贪心配对——系统性制造了虚假连接，将 FDT-01/FDT-02/LINK 熔成一个 203 巨分量。颜色缺失根因是 converter.py 全程不读实体 color 属性。SPAN 标签缺失是外键写错+挂接粒度不符。非主体"未解耦"实为检出正确但确认未走。

## Critical Unknown
gap-bridge 加方向+缆种约束后的拓扑改善幅度——将直接决定 B2 修复策略是"加约束"还是"回退重做"

## Recommended Discriminating Probe
对 raw_frags 重跑成链（gap_tol=5m 不变），bridge 候选追加两过滤条件：同 dwg_layer + 双端延续方向内积≥cos(30°)。对比修复前后的巨型分量尺寸、FDT 覆盖率、跨缆种错接率变化——一次运行裁定修复方向有效性。同时跑 `ogrinfo -sql "SELECT name, type FROM sqlite_master WHERE name='layer_styles'"` 确认当前 gpkg 有无 layer_styles 表。
