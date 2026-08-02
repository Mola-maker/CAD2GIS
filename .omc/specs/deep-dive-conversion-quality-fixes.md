# Deep Dive Spec: 四项目转换质量修复

## Metadata
- Deep Dive ID: 9c4e3d12-a7b5-4f8c-b2d1-e6f0c9a8b347
- Trace Rounds: 3 lanes
- Interview Rounds: 4 (Round 0 + 3)
- Final Ambiguity Score: 10.2%
- Type: brownfield
- Generated: 2026-07-28
- Threshold: 0.2 (20%)
- Threshold Source: default
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.95 | 0.35 | 0.333 |
| Constraint Clarity | 0.85 | 0.25 | 0.213 |
| Success Criteria | 0.90 | 0.25 | 0.225 |
| Context Clarity | 0.85 | 0.15 | 0.128 |
| **Total Clarity** | | | **0.898** |
| **Ambiguity** | | | **10.2%** |

## Trace Findings

### Lane 1: CRS 位移

**根因**：`assess_coordinate_domain()` 在 `coordinate_domain.py` 中完整实现，但从未被任何 pipeline 阶段调用。AI onboarding 盲信 DWG 的 `CGEOCS` 声明选出 EPSG:3857，但 Lamteh/Kletek 的 WCS 坐标是本地工程坐标（0-25k），不是 Web Mercator（13M+）。

| DWG | WCS X 范围 | 实际坐标系 | 被当作 EPSG:3857 后的位置 |
|-----|-----------|-----------|------------------------|
| Hutabohu | 13,679,653..13,688,052 | Web Mercator | 印尼苏拉威西 ✓ |
| Lamteh SF | 1,360..24,622 | 本地工程 | 几内亚湾 |
| Lamteh Main | 1,060..24,323 | 本地工程 | 几内亚湾 |
| Kletek | -263..274 | 本地工程 | (0,0) 几内亚湾 |

**修复**：在 validate 阶段接线 `assess_coordinate_domain()`，返回 `LOCAL_OR_MISREGISTERED` 时自动设 `source_crs=null, local_registration_strategy="gcp_required"`。

### Lane 2: 场景分区 / 图例噪声

**根因**：AI 生成的 mapping_registry 全部 `coverage.policy: "abstain"` + 空 allowlist，Paper 空间图例/标题块/示例点未被过滤。Experiment 人工审查版用 `policy: "fail"` + 精确 allowlist。

**修复**：以 experiment config 为底，merge AI 识别的项目特定层/块名。

### Lane 3: 标签/显示规则

**根因**：AI 只生成了 IMB 的 `display_label_rules`，PTECH/BOITE/CABLE_SEGMENT 无规则。`annotation_families` 全部为 0。

**修复**：从 experiment config merge `field_rules`、`display_label_rules`、`annotation_families`、`decision_rules`、`labels`。

### Lane 5: 青色 CABLE 未提取

**根因**：`positive_route_layer_regex` 未覆盖 `Expansion Core`、`Service Core`、`moniter core` 等实际含缆线几何的 layer。

**修复**：扩充 regex 覆盖所有含 model-space LWPOLYLINE 的缆线相关层。

---

## Topology

| Component | Status | Description |
|-----------|--------|-------------|
| coordinate_domain 接线 | active | validate 阶段调用 `assess_coordinate_domain()`，LOCAL 结果自动降级 |
| coverage/mapping/label 补全 | active | experiment config 为底 + AI 项目特定数据 merge → 四项目 mapping_registry |
| 验收验证 | active | 自动化指标 + 人工审阅 GPKG |

## Goal

修复四项目（Hutabohu、Lamteh SF、Lamteh Main、Kletek）转换中的五类问题：CRS 位移、图例噪声、BOX/双图噪声、标签丢失、青色 CABLE 漏提。修复范围限于一条 pipeline 接线 + 四份 mapping_registry 配置补全，不修改算法逻辑。

## Constraints

1. **不改变 `src/` 算法体系**：只加一条 `assess_coordinate_domain()` 调用线 + 改 mapping_registry.json
2. **已有 Hutabohu 基线不被破坏**：delivery_counts 变化须可解释（如 BOITE 43→46 是因为 INSERT 属性修复）
3. **experiment config 为权威模板**：field_rules/display_label_rules/annotation_families/decision_rules/labels/policy/thresholds/coverage 优先从 experiment 取
4. **项目特定数据由 AI onboarding 产出**：block_families、layers、positive_route_layer_regex 用 AI 识别的值

## Non-Goals

- 不修改 CIRCLE/ARC/ELLIPSE 几何提取
- 不修改 mapping_registry 的 v1 ↔ v2 schema 迁移（已在之前完成）
- 不触及其他 `src/` 模块
- 不对 Lamteh/Kletek 做 GCP 校准（那是后续 Web 审查步骤）

## Acceptance Criteria

### 自动检验

- [ ] Lamteh SF delivery 中心坐标不再在 (0,0)~几内亚湾区域
- [ ] Lamteh Main delivery 中心坐标不再在 (0,0)~几内亚湾区域  
- [ ] Kletek delivery 中心坐标不再在 (0,0)
- [ ] PTECH 要素的 label 字段非空（至少部分有值）
- [ ] BOITE 要素的 label 字段非空（至少部分有值）
- [ ] CABLE_SEGMENT 的 dimension 标签在 label 字段中可读
- [ ] delivery_counts 中图例/示例点类别的计数为 0 或显著下降
- [ ] 四项目 `pytest tests/ -q` 不引入新失败

### 人工审阅

- [ ] QGIS 打开四项目 delivery.gpkg，确认地理中心位置移至印尼本土
- [ ] 确认 PTECH/BOITE 点要素有标签文字
- [ ] 确认 CABLE 线段有 `{CAPACITE}C/{MODULO}T` 标签
- [ ] 确认图例彩色线段不出现在 delivery 中
- [ ] 确认 Hutabohu 青色 CABLE 被提取

## Technical Context

### 修复 1：coordinate_domain 接线

**文件**：`src/cad2gis/cad2gis_v3/project_profile.py` — `validate_project()`

**插入点**：L731（source_sha256 校验通过之后，inventory_sha256 校验之前）

```python
# 插入位置：source binding 校验通过后
from .coordinate_domain import assess_coordinate_domain
from .model import SourceEntity

# 只有 source_crs 非空才做坐标域检查
if profile.source_crs:
    domain = assess_coordinate_domain(entity_list, profile.source_crs)
    if domain["status"] == "LOCAL_OR_MISREGISTERED_COORDINATES":
        # 降级：清空 source_crs，标记为需要 GCP 本地配准
        profile.source_crs = None
        profile.local_registration_strategy = "gcp_required"
```

**降级行为**：`source_crs=None` → pipeline 跳过名义 CRS 变换 → 坐标保持本地工程值 → 后续通过 Web 审查 + GCP 相似变换映射到地图。

### 修复 2：mapping_registry 补全

**策略**：以 experiment config 为底，覆盖项目特定段

**merge 规则**：

| 段 | 来源 | 原因 |
|----|------|------|
| `field_rules` | experiment（全部） | Telkom Indonesia 统一 |
| `display_label_rules` | experiment + AI | experiment 的 CABLE/IMB + AI 的项目特定层名 |
| `annotation_families` | 手写（按项目 DWG 库存分析） | 标注编号方案完全项目特定 |
| `decision_rules` | experiment（全部） | 算法选择 |
| `labels` | experiment（全部） | FTTH 领域常数 |
| `thresholds_native_m` | experiment（全部） | 领域容差 |
| `policy` | experiment（全部） | 安全策略 |
| `coverage` | experiment（全部） | 通用噪声过滤 |
| `block_families` | AI onboarding | 项目特定块名 |
| `layers` | AI onboarding（保留）+ 人工审查 | 项目特定层名 |
| `positive_route_layer_regex` | AI + 人工扩充 | 需覆盖 Expansion/Service/moniter Core |

### 修复 3：positive_route_layer_regex 扩充

Hutabohu 的 regex 需从：
```
(?i)Cable Line [A-C] \(FO Cable (?:24|48)C[_/][0-9]+T\)
→ (?i)(?:Cable Line [A-C]|GRT\.\d+\.\w+\ -\ .*CABLE|FO\ \d+\ CORE|Expansion\ Cores?|Service\ Core|moniter\ core)
```

### 验证命令

```bash
# 自动检验
.venv/bin/python3 -c "
import json, math

for p in ['hutabohu','lamteh_sf','lamteh_main','kletek']:
    m = json.load(open(f'baselines/{p}/run/run_manifest.json'))
    # Check CRS
    crs = m.get('crs',{})
    sc = crs.get('source_crs')
    print(f'{p}: source_crs={sc}')
    
    # Check delivery center
    # (would need to read GPKG geometry)
"

# 人工审阅
# 用 QGIS 打开 baselines/*/run/delivery.gpkg
```

## Ontology

| Entity | Type | Relationships |
|--------|------|---------------|
| `assess_coordinate_domain` | orphan gate function | wired into `validate_project()` |
| `experiment/config/apd_mapping_registry.json` | authority template | merged over AI-generated configs |
| `mapping_registry.json` | per-project config | merge of experiment template + AI block/layer data |
| `source_profile.json` | per-project config | `source_crs` may be null after domain check |
| `positive_route_layer_regex` | cable route filter | expanded to cover all core layers |

## Interview Transcript

<details>
<summary>Full Q&A (4 rounds)</summary>

### Round 0 (Topology)
**Q:** 三个修复组件确认？
**A:** 对

### Round 1
**Q:** coordinate_domain 返回 LOCAL 后行为——阻断还是降级？
**A:** B. 降级标记，自动设 source_crs=None, local_registration_strategy="gcp_required"

### Round 2
**Q:** coverage/label 修复——重新跑 AI onboarding 还是以 experiment 为模板人工适配？
**A:** 先深入调查 experiment config 可复用性

→ 调查结论：70% 直接复用，25% 需适配，5% 项目特定

### Round 3
**Q:** 怎么验证修复成功？
**A:** 自动检验指标 + 人工审阅四项目 GPKG

</details>
