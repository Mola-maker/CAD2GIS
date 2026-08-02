# Deep Dive Spec: 图例聚类 / 标签 / annotation_families

## Trace Findings

1. **SITE 噪声**：`.*fdt.*` 匹配 FDT STRUCTURE 结构线→fragment clustering 缺失；图例 *U7 块未被过滤→需 legend_detector 排除
2. **CABLE_SEGMENT 标签**：显示的是 delivery_grid_length_m 计算值，不是 DWG DIMENSION 原生数据
3. **Label 缺失**：annotation_families 未配置，AI onboarding 未生成

## Repair Plan

### 修复 1: legend_detector 移植

- 从 Linux 版 `legend_detector.py` 移植 `detect_legend_clusters()` 到 `src/cad2gis/cad2gis_v3/legend_detector.py`
- 在 reader 返回 records 后、ingest 前调用
- 输入：record dict 的 `centroid`、`text`、`layer`、`entity_key`
- 输出：被标记的 entity_key 列表，从 records 中移除
- 默认参数：gap_min=100, gap_k=0.15, anchor_terms=LEGEND/SYMBOL/DESIGN SUMMARY 等

### 修复 2: CABLE_SEGMENT display_label

- `mapping_registry.json` 的 `display_label_rules` 增加：
```json
"CABLE_SEGMENT": {
    "rule_id": "APD-CABLE-SEGMENT-LABEL-001",
    "kind": "attribute-format",
    "template": "{measurement_native_m:.2f} m",
    "required_fields": ["measurement_native_m"],
    "provenance": "DWG_DIRECT:dimension|RULE:APD-CABLE-SEGMENT-LABEL-001"
}
```
- 无 DIMENSION 的线段（measurement_native_m=null）不显示标签

### 修复 3: annotation_families AI 提取

- `onboarding.py` 扩展 `prepare_onboarding_bundle`：TEXT 实体按 layer 分组去重，取前 N 样本
- AI 生成 candidate `annotation_families`（family_id + text_pattern + target_class + max_distance）
- `apply_ai_onboarding` 写入 mapping_registry

## Acceptance Criteria

- [ ] Hutabohu SITE count = 2（排除图例和噪声后）
- [ ] CABLE_SEGMENT 层：有 DIMENSION 的线段显示原生值标签，无标注的不显示
- [ ] PTECH/BOITE display_label 非空
- [ ] Hutabohu label 与 Linux 版 LABEL_FAMILIES 正则匹配：`^DMPH-\d+\.\d+\.[A-Z]\d{2}$` BOITE、`^MR\.DMPH\.P\d+$` PTECH_new、`^EXT\.MR\.\w+\.\w+\.\w+\.P\d+$` PTECH_ext
- [ ] Label 数量对标 Linux 版：BOITE 43（含标签 16）、PTECH 167
