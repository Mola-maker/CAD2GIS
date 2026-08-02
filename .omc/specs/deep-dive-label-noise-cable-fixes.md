# Deep Dive Spec: 标签/噪声/CABLE 修复

## Trace Findings

### 1. SITE 错配（Hutabohu +1）
- 实验 config `block_families.SITE: ["*U7"]` 匹配了图例中的 SITE 样式块
- 根因：coverage policy="abstain" 不过滤任何实体

### 2. CABLE regex 不完整（Hutabohu）
- 当前 regex 只匹配 `Cable Line [A-C] (FO Cable ##C_#T)` 格式的 2 个 layer
- 未匹配的实际缆线层：`GRT...MAINFEEDER CABLE`、`GRT...SUBFEEDER CABLE`、`Expansion Core(s)`、`Service Core`、`moniter core`、`Line`
- "Line" 层有 411 个 LINE/LWPOLYLINE 实体——青色 CABLE 在其中

### 3. Label 缺失 + 噪声
- experiment `display_label_rules` 只有 CABLE 和 IMB，无 PTECH/BOITE/SITE
- coverage="abstain" 让 Paper 空间图例/标题块全部进入转换

## 修复方案

全部修改 `baselines/hutabohu/config/mapping_registry.json`：
1. 扩充 `positive_route_layer_regex` 覆盖所有 cable 层
2. 添加 PTECH/BOITE/SITE 的 `display_label_rules`
3. coverage semantics policy → "warn" + allowlist 排除已知噪声类型
