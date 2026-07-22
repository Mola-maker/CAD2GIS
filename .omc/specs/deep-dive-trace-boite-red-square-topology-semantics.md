# Deep Dive Trace: boite-red-square-topology-semantics

## Observed Result
43 个 BOITE 全部来自 FAT DWG 层（#FF0000, block+circle, 正确 DMPH 标签）。用户通过 QGIS 检查发现：这些红色方形"不应理解为现实铺设需要经过的点，而是相关数字标注的定位点"——元素文档条目 4（正方形+箭头+数字=距离标签）。它们被 topology_builder 当作 CABLE 必经节点，强制缆端吸附（36 个端点距离 0.000m），偏转中位仅 6.4°（缆穿盒而过，非终止），58%（25/43）为零度孤立节点。Lane 2 实证：CABLE 成链在 BOITE 节点处被 node_capture_tol 截断（topology_builder.py:949），span_annotations 走 DIMENSION 定义线天然避开此干扰。Lane 3 定性地：INFRASTRUCTURE=映射遗漏（DROP DUCT+HH PIT），ZNRO=源图缺失。

## Ranked Hypotheses
| Rank | 结论 | Confidence | Evidence |
|------|------|------------|----------|
| 1 | BOITE 语义误解：LABEL_FAMILIES 将跨段标注 DMPH-* 路由到 FAT DWG 块→BOITE→NODE_LAYERS，拓扑截断缆线 | High | Strong（代码直证 ×3 + 产物实测 ×4 + 元素文档 ×2 条目） |
| 2 | CABLE 链在 BOITE 处被 node_capture_tol 截断，Span 走 DIMENSION 定义线准确 | High | Strong（产物实测 + 代码行精确指证 topology_builder.py:949） |
| 3 | EXISTING POLE 49 杆仅 3 个标签——ATTRIB 文本未被正确读取/或格式不匹配 pole 正则 | High | Strong（conservation_ledger + candidates 表交叉验证）|

## Evidence Summary

### Lane 1 — 红色 BOITE 语义
- **DWG 身份**：FAT DWG 层 INSERT 块，ACI 1（红色），representation="block+circle"。元素文档条目 2：DMPH-1.010.C04 = "电缆/跨段标识符"。条目 4：带数字方形+箭头 = "距离标签/跨距长度标记"
- **映射→污染链**（3 步）：LABEL_FAMILIES `target_fc="BOITE"`（schema_config.py:2615）→ BOITE 进入 NODE_LAYERS（topology_builder.py:51）→ _split_coords_at_nodes（L690）+ repair_edges snap（L270）——36 端点距离 0.000m
- **量化**：18 degree=2（缆穿过，偏转中位 6.4°）；25 ISOLATED_NODE（58%，拓扑垃圾）；36 CABLE 端距离恰好 0.000m（吸附实证）

### Lane 2 — CABLE vs span + PTECH
- **BOITE 截断**：topology_builder.py:949 `node_distance <= node_capture_tol` → node_cut，永不链通。36 个 BOITE 在缆路径 0.5m 内成为截断点
- **Span 更准**：几何来自 DIMENSION xline1→xline2（converter.py:525-543），完全绕过 BOITE 路由干扰
- **碎段保真**：--skip-chaining 绕过 node_capture_tol，保留 DWG 原始折线
- **EXISTING POLE**：49 个 INSERT 仅 25 条标注文本被消耗，仅 3 条匹配 `^MR\.DMPH\.P\d+$`——其余或为 ATTRIB 未读、或在 0 号层被 block_definition 过滤
- **NEW POLE 缺失**：3 个 synthetic（PT0001/PT0003/PT0035），8 条 lost 候选（11-15m），Hungarian 距离竞争败

### Lane 3 — INFRA/ZNRO 空层
- **INFRASTRUCTURE = 映射遗漏**：DROP DUCT（1 LINE）被 CABLE `.*drop.*` 先截获；HH PIT 三层（各 1 INSERT）正则缺 pit/handhole 关键词
- **ZNRO = 源图缺失**：无 NRO/zone/coverage 图层；BOUNDARY CLUSTER 概念相近但无 NRO 语义标注
- 两项均被用户明确：暂缓，先修拓扑与标注

## Per-Lane Critical Unknowns
- **Lane 1**：FAT DWG 块的 AutoCAD 块定义名与块内几何形态（无 AutoCAD 环境，dwgread JSON 未遍历到层内实体详情）——但 gpkg 派生字段已足够论证
- **Lane 2**：EXISTING POLE 的 ATTRIB 标签文本确切内容与格式——需 LibreDWG 诊断脚本攻破
- **Lane 3**：暂缓

## Most Likely Explanation
FAT DWG 红色方形是**跨距标注定位块**（带 FAT 编码的注释块），不是物理闭合设备。LABEL_FAMILIES 把 DMPH 格式文本分配给 BOITE，使它进入 NODE_LAYERS，topology_builder 强制 CABLE 吸附——污染了拓扑（25 孤立垃圾 + 18 虚假截断）。Span 层走 DIMENSION 定义线天然避开此问题。EXISTING POLE 的标签在为 block_definition/ATTRIB 提取路径中丢失。

## Critical Unknown
EXISTING POLE 的 46 条缺失标签的真实原因——是 ATTRIB 未被 LibreDWG 暴露、在 0 号层被过滤、还是文本格式（EXT.MR.xxx 等变体）不匹配 pole 正则？

## Recommended Discriminating Probe
用一个独立的 dwgread 诊断脚本：遍历 entmode=2 的 ATTRIB 实体（entity="ATTRIB"），提取 tag/text_value/所在块名，专门统计 EXISTING POLE 相关 ATTRIB——直接裁决标签数据是否存在与格式。
