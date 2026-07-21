# Deep Dive Trace: dwg-layer-topology-gpkg-pipeline

## Observed Result
用户要求详述 experiment/guide 技术指导 + experiment/py_scripts 四脚本如何从 .dwg 分离图层、经聚类/严格拓扑/地理定位整合为 .gpkg。三条并行分道取证后发现：**用户问题中预设的"聚类、严格拓扑"中间过程在当前 experiment 管线中并未实现**——它们存在于 guide 的设计规范（Agent 4 "Topology Surgeon"）和被 gitignore 的遗留插件 `plugincad2gis/` 中，但四个脚本的实际数据流是线性的"读取→分类→过滤→写出"。

## Ranked Hypotheses（对管线实际机制的解释）
| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | experiment 管线 = LibreDWG 直读 DWG → 两级图层名/文本分类 → 空间过滤 → OGR 直写 8 层 GPKG（EPSG:4326 恒等配准）；聚类/拓扑为 guide 设计规范，未落地 | High | Strong（三分道代码直证 + 输出 gpkg 实测） | grep 全文无聚类/吸附/拓扑代码；guide:76,280 明说 Hutabohu 版移除 DBSCAN；实测 0% 光缆端点吸附 |
| 2 | "CABLE_ALL Topology Done"（commit 324cb12）指 CAD/QGIS 侧人工拓扑整理，产物未入库 | Medium | Moderate（排除法） | 仓库所有产物中 "CABLE_ALL" 零命中；gpkg 实测 100% FLOATING_CABLE 否证"gpkg 拓扑已修复"说 |
| 3 | 历史线索（Regime A/B、3857、Y过滤）描述的是 demo/ 旧变体，与当前 experiment 管线混淆 | High | Strong（代码直证） | Regime A/B 仅存于 demo/converter.py:235-243；当前脚本 guide:392-394 明文 "IDENTITY…No regime classification" |

## Evidence Summary by Hypothesis

### Lane 1 — 图层分离机制（代码直证为主）
- **DWG 读取**：GNU LibreDWG 直读，无 DXF 中转、无 ezdxf、无 ODA。双通道：ctypes 直连 libredwg.so（converter.py:53-77）+ SWIG 绑定 `dwg_read_file`（converter.py:609-627, 659）。入口 `read_dwg()`（converter.py:601-915）。
- **实体遍历**：非 modelspace 遍历，线性扫描 `for i in range(data.num_objects)`（:668），按 `DWG_SUPERTYPE_ENTITY` 过滤（:673），用坐标合理性过滤替代纸空间区分（:681-709）。
- **提取类型**：LINE/LWPOLYLINE/CIRCLE/ARC/TEXT/MTEXT/INSERT/POINT + R11 遗留变体（`_extract_wkt` :347-423）。不提取 HATCH/POLYLINE_2D/3D。
- **INSERT 块**：不炸块、不读 ATTRIB，仅取插入点为 POINT（:406-413）；块名匹配英文正则 INSERT_BLOCK_PATTERN（:146-149），不匹配整体丢弃（:742）。
- **分类**：仅用图层名+文本，不用颜色/线型。两级 `_assign_fc`（:493-508）：①负证据门 NEGATIVE_EVIDENCE_LAYERS（schema_config.py:1835-1866，含 "0"/"LEGEND"）→fc_misc；②Tier-1 图层名正则 LAYER_PATTERN_MAP（schema_config.py:1872-1930，置信 0.9）；③Tier-2 注记文本关键词（converter.py:152-159, 467-490，置信 0.5）。
- **注记挂接**：TEXT/MTEXT 不独立成要素，最近邻挂接（σ=0.0001°≈11m，:568-596, 884-885）。
- **domain_vocab.py**：法语词表（Morocco 遗产），仅用于属性域校验（validate_domain_value :265-293），不参与图层分类。
- **实测**（output gpkg manifest）：total=3616，misc=3378（48.3% 被弃）；Tier-2 零命中；出现 SEQEND_r11/JUMP_r11 伪要素（R11 类型码冲突实锤）。

### Lane 2 — 聚类与拓扑（否证性发现）
- **不存在**：DBSCAN/包围盒聚类、端点吸附、split/merge/dedup、悬挂点处理、节点-边图——grep 全文零命中[代码直证]。
- **实际中间处理仅有**：弧离散化（自适应弦容差 extent*0.001，converter.py:245-248）、三重空间过滤（:814-816, :820-828, :833-834, :1079-1092）、标注挂接（:568-596）。
- **标注挂接两个缺陷**：`linked` 集合从未 add（:580 判重死代码），全部标注重复写为 fc_misc（:595）；用质心而非最近点，长光缆标签必然超距失配。
- **guide Agent 4 规范（:484-634）全部未实现**：SNAP(0.0001°)→CLIP(0.0005°)→DEDUP(Hausdorff 1e-5°)→CLOSE→SLIVER，连通图 FLOATING_CABLE/ISOLATED_NODE 标记，瓦片边界端点合并（guide:954-959）。
- **实测**：CABLE 575 条端点 1082 个，0 个落在任何节点 0.0001° 容差内（最近 11.16m，中位 98.7m）→ 按 guide 定义 100% FLOATING_CABLE；全部 CABLE 的 ORIGINE 为 null；BOITE 2626 中 2268 个来自 'FDT STRUCTURE' 图层逐碎片成点（反证无聚合）。
- **evaluator 5.4/6.6 规则**（evaluator.py:571-642, 880-977）：属性为 null 即 continue（:936-937）→ 空属性下空洞通过。
- 唯一真实聚类/吸附代码在遗留插件 plugincad2gis/src/cad2gis/network.py（build_network snap_tol=1.0、_cluster_points）与 topology.py，不参与本管线。

### Lane 3 — 地理定位与 GPKG 集成
- **当前变体为恒等配准**：DWG 原生即 WGS84（X=经度 Y=纬度）。`--source-crs` 默认 EPSG:4326（converter.py:1228-1231）；非 4326 时 osr.CoordinateTransformation→4326（:1234-1243）+ 轴序纠正（:109-123, :227-242, :783-812）。输出 SRS 固定 EPSG:4326（:999-1000）。guide:392-394 明文 "IDENTITY…No coordinate offset. No pyproj chain. No regime classification"。
- **Regime A/B 属旧变体 demo/converter.py**：逐实体 bbox 中心 Y>100000 分界（非按图层！demo/converter.py:235-243）；默认偏移 A(292539,-405) B(589239,3203295)，CRS=EPSG:32648；demo/converter_3857.py:99-112 走 pyproj 32648→3857。参考点 (11872757,3346826) 属重庆东溪 GCP（demo/config/gcp_dongxi.json），与 Hutabohu 无关。
- **过滤**：|lon|>180/|lat|>90 跳过（:704-709, :815）；印尼硬边界 lat[-11,7]/lon[95,141] 直接丢弃（:833-834）——与 guide:304-306 "warning, NOT halt" 及代码自身注释（:100-103 "never discarded"）双重矛盾；写出阶段跨度>50° 或 (0,0) 端点+跨度>10° 丢弃（:1079-1092）。"Y<-100K 过滤"在两代码中均未找到（线索记忆混淆，实为 demo 的 Y>100K 制式分界）。EPSG3857_MIN_REAL_X（:98）为死代码。
- **GPKG 写出**：纯 GDAL/OGR GPKG driver（:994-997），非 geopandas/fiona。8 个 FTTH 图层（write_geopackage :992-1211）：点 3（BOITE/PTECH/SITE）+ 线 2（CABLE/INFRASTRUCTURE）+ 面 2（ZNRO/ZPM）+ IMB（闭合线转面 :960-966）。字段来自 schema_config 各 FC `fields[].full_name`（法语命名，全 ASCII）；计算字段 X/Y（:1113-1116）、LONGUEUR haversine（:1119-1121）；元数据字段 :1060-1063。另写 pipeline_manifest（SHA256，:1140-1161）、transform_record（:1164-1184，恒写 identity 即使启用了重投影——记录失真）、qc_summary（:1187-1208）三张表。
- **evaluator.py 角色**：只读事后合规验证引擎（evaluator.py:1209-1254），7 规则组（图层存在/CRS 一致 4326/空层/必填字段+CODE 唯一/CABLE 引用完整性/几何检查/容量端口平衡）。不做与参考真值的精度比对。退出码 0/1/2=PASS/FAIL/QUARANTINE（:1258+），无自动回灌 converter 的迭代回路，反馈靠人工。
- **实测**：输出范围 (122.897-122.961, 0.614-0.635) 落在 guide 部署框内，恒等假设对该 DWG 成立；PTECH/SITE/ZNRO/ZPM 四层为空。

## Evidence Against / Missing Evidence
- **Hypothesis 1**：无反证。缺口：未实际插桩运行 converter 验证每步计数。
- **Hypothesis 2**：DWG 为压缩二进制，strings 未命中 "CABLE_ALL" 不具决定性——可能是 DWG 内部图层名。
- **Hypothesis 3**：无反证；notepad/历史线索与 demo/ 代码逐项对上。

## Per-Lane Critical Unknowns
- **Lane 1（图层分离）**：被弃的 3378 个 misc（48.3%）+ 静默跳过的未识别 INSERT 块中，埋没了多少本应属于 PTECH/SITE/IMB 的真实设施——召回损失是正则覆盖缺口还是图纸本无此类要素？
- **Lane 2（聚类拓扑）**："CABLE_ALL Topology Done" 的产物在哪里——若拓扑是人工在 CAD/QGIS 侧完成，管线自动化主张与实际分工将完全改写。
- **Lane 3（地理定位/GPKG）**：PTECH/SITE/ZNRO/ZPM 四层为空的原因无法区分——源图纸无此类实体 / 分类未命中 / 印尼边界+图框过滤误杀（evaluator 规则 3.0 必 FAIL 但不能定位原因）。

## Lane 3 Misplacement / SoT Ownership Scope
N/A —— 本次调查为机制研读，无 MOVE 候选。

## Rebuttal Round
- **对领先假设的最强反驳**：commit "CABLE_ALL Topology Done" 暗示拓扑已完成，可能存在未被调查覆盖的拓扑代码/产物。
- **领先假设为何仍成立**：三分道独立 grep + 输出 gpkg 直接实测（0% 端点吸附、ORIGINE 全 null）从代码与产物两侧证实拓扑不在 committed 管线中；反驳只能退守为"拓扑在仓库之外完成"（即 Hypothesis 2），与领先假设兼容而非冲突。

## Convergence / Separation Notes
- 三分道收敛于同一结构性结论：**guide（设计规范，含 8 个 Agent 分工的完整管线蓝图）≠ 四脚本（实际实现，仅覆盖 guide 的 Agent 1-3 + 部分 Agent 5-6 检查）**。聚类/拓扑（Agent 4）、瓦片合并（Agent 7）、自动校准回路（Agent 8 部分）均为"纸面管线"。
- Hypothesis 2 与 Hypothesis 1 合并：拓扑缺位（H1）的补偿机制即人工侧完成（H2）。

## Most Likely Explanation
当前 experiment 管线是一条**线性转换器**：LibreDWG 直读 DWG 实体 → 图层名正则两级分类（Tier-1 图层名 0.9 / Tier-2 注记文本 0.5）+ 注记最近邻挂接 → 三重空间过滤（合理性/图框/印尼边界）→ 恒等地理配准（DWG 原生 WGS84）→ OGR 直写 8 层 EPSG:4326 GPKG + 3 张元数据表 → evaluator.py 事后 7 组规则合规验证。用户问题中的"聚类、严格拓扑"是 guide 规范中 Agent 4 的职责，代码未实现；"确定地理位置"在本数据集退化为恒等变换（历史 Regime A/B 平移方案属 demo/ 旧变体）。

## Critical Unknown
**综合**：管线的真实召回率不明——48.3% misc 丢弃 + 未识别块静默跳过 + 印尼边界过滤丢弃，三个丢弃口叠加，无法区分"四个空图层与高丢弃率"是源数据特性还是管线缺陷；这直接决定"guide 未实现部分"中哪些是急需补的。

## Recommended Discriminating Probe
对 Hutabohu DWG 做一次只读盘点运行：插桩 read_dwg（或用 `dwgread -O json`/`dwglayers`）输出①完整图层名直方图、②INSERT 块名直方图、③每个丢弃口（misc/块跳过/边界过滤/图框过滤）的逐图层计数。一次运行同时解决三条分道的 critical unknown（正则覆盖缺口、CABLE_ALL 是否为 CAD 图层名、四空层归因）。
