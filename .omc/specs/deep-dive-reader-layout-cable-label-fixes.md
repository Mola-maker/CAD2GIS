# Deep Dive Spec: Reader 布局/缆线/标签修复

## Metadata
- Interview Rounds: Round 0 + 4
- Final Ambiguity: ~10%
- Type: brownfield
- Threshold: 0.2
- Status: PASSED

## Trace Findings

### Lane 1: 青色 CABLE 灾难性漏提
- Hutabohu: 87 根 ACI=4 (Cyan) LWPOLYLINE 在 "FDT STRUCTURE" 层，正则不匹配
- Lamteh Main: 50 根 ACI=4 (层色) 在 "SLING WIRE" 层
- 根因: `positive_route_layer_regex` 纯按层名匹配，不看颜色

### Lane 2: Label 匈牙利算法
- Linux 版 `schema_config.py:2614` 有完整 `LABEL_FAMILIES`（fat/pole/pole_ext）
- Linux 版 `converter.py:776` 有 `_minimum_cost_assignment` (Hungarian)
- CAD2GIS 管线已有 `global-minimum-cost-family-assignment` 算法支持
- mapping_registry 中 `annotation_families` 未配置

### Lane 3: 布局权属链
- entmode==1 实体的 ownerhandle 返回 None（ctypes 桥限制）
- DWG 中有 "APD - SF" / "Layout2" 命名布局但无法读取

## Topology

| 组件 | 状态 | 说明 |
|------|------|------|
| `positive_route_color_aci` 机制 | active | 新 mapping_registry 字段，[4]=cyan |
| AI 标注样本→候选正则→人确认 | active | 通用 annotation_families 生成流程 |
| 布局权属链 ctypes + `--layout` CLI | active | 读取+CLI 选择 |

## Repair Plan

### 修复 1: `positive_route_color_aci`

**文件**: `src/cad2gis/cad2gis_v3/pipeline.py` — `_validate_source_geometry`
**配置**: `mapping_registry.json` 新字段 `"positive_route_color_aci": [4]`

逻辑: 除 `positive_route_layer_regex` 匹配的层之外，任何 model-space LWPOLYLINE 实体若其 effective ACI 在 `positive_route_color_aci` 列表中，也视为缆线。

### 修复 2: 布局权属链 + `--layout` CLI

**文件**: `src/cad2gis/reader/libredwg.py`
- `_init_libredwg`: 注册 `dwg_ent_owner_get` C API
- `_resolve_layout`: entmode==1 时读取 ownerhandle
- 配合已有的 `_read_layout_names_json` 映射 handle→布局名

**文件**: `src/cad2gis/cli.py`
- `cad2gis bootstrap --layout <name>` 限定转换源
- `cad2gis inspect --layouts` 列出可用布局

### 修复 3: Label 通用机制

**文件**: `src/cad2gis/cad2gis_v3/onboarding.py`
- `prepare_onboarding_bundle`: 提取 TEXT 实体文本样本（按 layer 分组，去重，前 N 个）
- AI onboarding 生成 candidate `annotation_families`（text_pattern + target_class + max_distance）
- 人工确认后 `apply_ai_onboarding` 写入 mapping_registry

## Acceptance Criteria

- [ ] `positive_route_color_aci: [4]` 生效后 Hutabohu CABLE 含青色线
- [ ] `cad2gis inspect --layouts` 列出 DWG 所有布局名
- [ ] `cad2gis bootstrap --layout "APD - SF"` 只提取指定布局
- [ ] AI onboarding 产出包含 `annotation_families` 候选
- [ ] PTECH/BOITE `display_label` 字段非空
