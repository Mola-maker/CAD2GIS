# Deep Dive Spec: zpm-cyan-edge-ptech-label-noise

> 2026-08-02 | interview_id: zpm-cyan-edge-20260802 | threshold 0.2 | trace: deep-dive-trace-zpm-cyan-edge-ptech-label-noise.md

## Goal

修复四项目 auto-convert 产线的三个缺陷，使 ZPM 边缘青色吊线不丢失、PTECH 标签正常显示、注释/图例噪声不再进入 delivery 且配色统一。

## Constraints

- **禁止硬编码用户提供的坐标**——只能基于坐标定位问题特征，修复必须走通用算法/配置机制
- 禁止硬编码层名（kletek 标签分散三层，须用共同特征匹配）
- 修复基于 registry 角色/约束，代码不改 AI onboarding 流程
- expectations 同步更新（classify 剔除噪声后计数变化必须显式确认）
- 四项目产线：hutabohu / lamteh_main / lamteh_sf / kletek

## Non-Goals

- 不解决 lamteh_sf 的去噪误删有效 BOITE 点（用户明确此次修复无法顾及）
- 不重构 legend detector 的 gap 聚类算法（FDT-Info 交错问题改走块属性判定）
- 不重写 styles.py 的 QML 生成架构（只加排除机制）
- 不处理 INSERT 的 libredwg_block_attributes_unread 数据质量问题（独立调查）

## Acceptance Criteria

### AC1：ZPM 边缘青色吊线不丢失
- [ ] hutabohu 用户报告的 3 处区域、lamteh_main 1 处区域的青色吊线出现在 delivery CABLE（aci=4）
- [ ] boundary_exempt_layers 同时含 zpm_boundary + sling_wire 角色（pipeline.py + onboarding.py）
- [ ] 四项目重跑后 CABLE 计数含新增吊线，expectations 同步
- [ ] kletek 无回归（原本无丢失现象）

### AC2：PTECH display_label 正常
- [ ] 四项目 PTECH 表 display_label 非空，label_provenance 有来源
- [ ] 标签匹配规则 = 文本结构 `(EXT\.)?MR\..*\.P\d+` + 层名含 POLE 语义（不硬编码层名）
- [ ] registry 补 PTECH display_label_rules（attribute-field 指向 CODE）作 fallback
- [ ] hutabohu 的 family target_layer_pattern 重叠排除修复（或文本结构规则替代）
- [ ] kletek 三层标签（POLE ID 2.5 / POLE ID FDT 2 73 / EXT POLE）全部命中
- [ ] lamteh_main 模型空间 POLE ID 层（208 条）命中

### AC3：噪声剔除与配色
- [ ] FDT-Info 信息卡块不再归 SITE（块属性特征判定，registry 属性约束）
- [ ] BOITE 注释点（属性空 {} 或全文档占位 D/F/L）classify 剔除，不进 delivery.gpkg
- [ ] 有效 BOITE 统一 #FF7F00（hutabohu 从 #FF0000 改）
- [ ] styles.py 支持按 aci/source_layer/属性排除（样式门）
- [ ] SITE 层 #FF0000 噪声点不再写样式（数据剔除后自然消失）
- [ ] 四项目 expectations.feature_counts 同步更新为剔除后实际值

## Assumptions Exposed

- 有效 BOITE 属性特征跨项目一致：FAT/FAT DWG 块 attrs `{"F":"1"}`（有效值），注释块 attrs `{}` 或 `{"D":..,"F":..,"L":..}`（占位）
- lamteh_main 模型空间 POLE ID 层（208 条 accepted TEXT）是 PTECH 标签真源；block_definition 里的 Pole ID HC 是图例示例
- 青色吊线 = SLING WIRE 层 model 角色线（与参考实现一致：全部产出，不按长度过滤）

## Technical Context

### 根因链（trace 确认）

1. **问题 1**：spatial_filter.py:49 `_ANNOTATION_FRAME_TYPES` 含 LWPOLYLINE；boundary_band 检测（spatial_filter.py:79-96）把 body bbox 3% 边缘带内所有线实体判为注释引出线排除。豁免只覆盖 zpm_boundary 角色，SLING WIRE 不在豁免名单 → ZPM 边缘吊线系统性误杀。修复：boundary_exempt_layers 加 sling_wire（已验证 4/4 恢复）。
2. **问题 2**：三项目各自断裂：
   - lamteh_main/sf：POLE ID 文本在 block_definition 被 model 过滤 + registry family 层名错配（Pole ID HC 图例层 vs 模型空间 POLE ID 层）
   - hutabohu：family target_layer_pattern="(?i).+" 全重叠 → 172 PTECH 全部被 overlapping 排除
   - kletek：require_same_layer=true 但标签层≠INSERT 层
   - 四项目 display_label_rules 仅 IMB，PTECH 无 fallback
3. **问题 3**：FDT-Info 信息卡块（aci=1 #FF0000，属性 D/F/L 占位）被 insert_layer_families['SITE'] 无条件归类 SITE；BOITE 注释点（属性空/占位）与真实点（F=1）未区分；styles.py 无按颜色/来源排除机制。

### 关键文件
- src/cad2gis/cad2gis_v3/spatial_filter.py（boundary_band、_ANNOTATION_FRAME_TYPES、boundary_exempt_layers）
- src/cad2gis/cad2gis_v3/semantics.py（classify_entities：model 过滤、family 匹配、overlapping 排除、display_label 赋值）
- src/cad2gis/cad2gis_v3/pipeline.py（_validate_source_geometry、spatial_filter 调用）
- src/cad2gis/cad2gis_v3/onboarding.py（_compile_registry、spatial_filter 调用）
- src/cad2gis/cad2gis_v3/styles.py（write_styles、_qml、marker 样式）
- src/cad2gis/cad2gis_v3/config.py（MappingRegistry：block_families/insert_layer_families/layers 解析）
- 四项目 config/mapping_registry.json + source_profile.json（expectations）

## Ontology

| 概念 | 定义 | 稳定 |
|---|---|---|
| zpm_boundary 层角色 | FAT AREA 层（ZPM 多边形边界） | ✅ 已入 onboarding |
| sling_wire 层角色 | SLING WIRE 层（青色吊线=CABLE 源） | ✅ 已入 onboarding |
| boundary_exempt_layers | boundary_band 豁免层（zpm_boundary + sling_wire） | 本次扩展 |
| PTECH 标签文本结构 | `(EXT\.)?MR\..*\.P\d+` 杆位标识 | 跨项目稳定 |
| 有效 BOITE 块 | 块属性含有效值（F=1） | 跨项目稳定 |
| 注释 BOITE 块 | 属性空 {} 或全文档占位（D/F/L） | 跨项目稳定 |
| display_label_rules | registry 的 label fallback 规则 | 本次补 PTECH |

## Trace Findings

三条 lane 全部收敛：
- **Lane 1**（cyan-sling-edge-loss）：boundary_band 误杀，证据 4/4 用户坐标 → flag=boundary_band；修复后 4/4 恢复（高置信）
- **Lane 2**（ptech-label-chain）：双失效（匹配链路断 + 无 fallback 规则），三项目各自机制，warehouse 写路径正常（高置信）
- **Lane 3**（legend-cluster-noise-ownership）：聚类有效（97% 排除率）但对交错注释块结构性盲区；FDT-Info 错分 SITE；styles 无排除机制（中高置信）

## Interview Transcript

1. Q: 问题 2 修复范围？→ A: 双修（链路+规则）
2. Q: lamteh_main block_definition 文本如何处理？→ A: 用户指出 POLE ID 是纯净 PTECH 标签层；验证发现模型空间 POLE ID 层 208 条是真源，registry family 层名错配
3. Q: kletek 标签规则？→ A: 文本结构+POLE 语义（三层共同特征，不硬编码）
4. Q: 问题 3i FDT-Info？→ A: 块属性特征判定（registry 约束）
5. Q: 问题 3ii BOITE 配色？→ A: 方向 2（classify 剔除+样式门），目标注释点不进 gpkg
6. Q: expectations 变化？→ A: 同步更新（显式确认新值）
7. Q: BOITE 注释点判定特征？→ A: registry 属性约束（有效 F=1 vs 占位 D/F/L）

## Implementation Plan（Phase 5 执行参考）

1. **问题 1（已完成代码，待重跑验证）**：pipeline.py + onboarding.py 的 boundary_exempt_layers 加 sling_wire
2. **问题 2**：
   a. semantics.py：annotation 匹配扩展——文本结构规则（层名含 POLE + text 匹配 `(EXT\.)?MR\..*\.P\d+`）作为 PTECH 标签 family 的通用匹配；修复 hutabohu overlapping 排除（target_layer_pattern 收紧或按 require_same_layer 分组）
   b. registry：四项目补 PTECH display_label_rules（attribute-field 指向 CODE）
   c. kletek family 扩展覆盖三层（或由 a 的通用规则覆盖）
3. **问题 3**：
   a. registry block_families 支持属性约束（有效 BOITE 判定特征）
   b. semantics classify：BOITE 注释点（属性空/占位）abstain 剔除；FDT-Info 不再归 SITE
   c. styles.py：新增按 aci/source_layer/属性排除机制
   d. 配色：有效 BOITE 统一 #FF7F00（若通过样式层，写 QML 时统一色）
4. **expectations**：四项目 feature_counts 更新（BOITE 减少注释点数、SITE 减少 FDT-Info 数、CABLE 增加吊线数）
5. 重跑四项目 auto-convert 验证 AC1-AC3
