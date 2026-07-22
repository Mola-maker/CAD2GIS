# Deep Dive Spec: main-branch-decoupling-method

## Metadata
- Interview ID: dd-20260722-main-decoupling
- Rounds: 1 (trace injection + 3 critical unknowns resolved)
- Final Ambiguity Score: ~15% (early exit; user gave explicit directives on all 4 lanes)
- Type: brownfield (main branch has complete decoupling implementation)
- Generated: 2026-07-22
- Threshold: 0.2 (20%)
- Status: BELOW_THRESHOLD_EARLY_EXIT

## Trace Findings

### Most Likely Explanation

main 分支的解耦方法是三层架构：
1. **L1-L2 共享层**（`cad_common.py`，676 行，31 函数）：零领域符号，纯 DWG/几何/CRS 操作，可复用于任何项目
2. **L3 领域层**（`ftth_converter.py` / `layout_miner.py` / `legend_detector.py`）：FTTH 领域逻辑，通过 `_PROJECT_CONFIG` + `--config` JSON 注入项目专属值
3. **配置层**（`hutabohu.json`）：所有项目专属数字（tolerances、code_prefix、fdt_value、fat_default、label_families、layer_pattern_map）外化到 JSON

### Per-Lane Critical Unknowns Resolved

| Lane | Critical Unknown | Resolution |
|------|-----------------|------------|
| 1. Config 外化 | 如何映射到 robustness 的 SourceProfile？ | 原位替换：`expected_census` 硬编码数字改为从 JSON 加载的接口 |
| 2. 共享库复用 | cad_common 与 libredwg 如何共存？ | 不是替换而是分层：cad_common 提供通用函数，libredwg（重命名为 dwg_extractor.py）导入并保留 reader 契约 |
| 3. 多 layout 支持 | 如何集成到 pipeline？ | 独立组件，按需调用；verify 阶段子图纸用于呈现联动和拓扑辅助，但需解耦 |
| 4. 改动量 + 兼容性 | 移植到 robustness 的改动量？ | 见 Implementation Steps |

## Goal

将 main 分支的"数值外化、结构保留"解耦模式迁移到 robustness 工作区，实现：
1. **配置外化**：`expected_census` 28 个硬编码数字从 `source_profile.json` 原位替换为从项目规则 JSON 加载的接口
2. **共享库抽离**：从 `libredwg.py` 抽离通用函数到 `cad_common.py`，`libredwg.py` 重命名为 `dwg_extractor.py` 并导入共享库
3. **多 layout 组件化**：`layout_miner.py` 作为独立组件集成到 `cad2gis_v3/`，按需调用，与主 pipeline 解耦

## Constraints

### 必须保持兼容
- **reader 契约不变**：`extract_dwg_records(source_path) -> DWGRecordInventory` 接口不变，确保 `cad2gis_v3/ingest.py` 和网页后端接入不受影响
- **测试基线**：`pytest tests verify` 53 项保持通过
- **跨平台**：纯 ctypes 路径优先，减少 SWIG 依赖
- **APD 基线**：`baselines/apd_hutabohu/` 的 delivery/evidence/records 不改动

### 命名统一
- `src/cad2gis/reader/libredwg.py` → `src/cad2gis/reader/dwg_extractor.py`
- `extraction_backend` 标记从 `"libredwg"` 改为 `"dwg_extractor"`
- 新增 `src/cad2gis/cad_common.py`（零领域符号共享库）

### 配置外化规则
- 新建 `baselines/apd_hutabohu/config/project_rules.json`，包含：
  - `tolerances`（7 项，从 hutabohu.json 移植）
  - `code_prefix`（8 项）
  - `expected_census`（28 项，从 source_profile.json 迁移）
  - `label_families`（3 组正则）
  - `layer_pattern_map`（13 组正则）
- `source_profile.json` 保留 schema 验证但数值从 `project_rules.json` 加载

### 多 layout 组件化规则
- `layout_miner.py` 移植到 `src/cad2gis/cad2gis_v3/layout_miner.py`
- 作为独立组件，通过 `CAD2GIS_ENABLE_LAYOUT_MINER=1` env 显式启用
- 不参与默认 pipeline，仅在 verify/replay 阶段按需调用
- 子图纸（FDT-01/FDT-02）用于呈现联动和拓扑辅助验证

## Non-Goals

- 完全项目无关的通用 CAD2GIS converter（当前仍为 APD 专用）
- 替换 `cad2gis_v3` 包为 main 的 `ftth_converter.py` 架构
- 网页后端开发（另起工作区）
- 多 DWG 并行处理（当前仅支持单 DWG 多 layout）

## Acceptance Criteria

### 配置外化
- [ ] `baselines/apd_hutabohu/config/project_rules.json` 存在，包含 tolerances/code_prefix/expected_census/label_families/layer_pattern_map
- [ ] `source_profile.json` 的 `expected_census` 数值从 `project_rules.json` 加载，不再硬编码
- [ ] `verify/replay.py` 的 `EXPECTED_DELIVERY` 从 `project_rules.json` 加载，不再硬编码
- [ ] `git grep -n "6940\|222\|170\|167\|43\|682" src/ verify/ tests/` 仅命中 project_rules.json 和注释

### 共享库抽离
- [ ] `src/cad2gis/cad_common.py` 存在，包含 12 个重叠函数 + 18 个通用函数，零 FTTH 符号
- [ ] `src/cad2gis/reader/dwg_extractor.py` 存在（重命名自 libredwg.py），导入 cad_common，无重复函数
- [ ] `src/cad2gis/reader/libredwg.py` 不再存在
- [ ] `grep -c "def " src/cad2gis/reader/dwg_extractor.py` ≈ 14（仅契约函数）
- [ ] `grep -c "def " src/cad2gis/cad_common.py` ≈ 30（通用函数）

### 多 layout 组件化
- [ ] `src/cad2gis/cad2gis_v3/layout_miner.py` 存在，移植自 main 分支
- [ ] `CAD2GIS_ENABLE_LAYOUT_MINER=1` env 显式启用，默认关闭
- [ ] `verify/replay.py` 支持 `--with-layout-miner` 可选调用
- [ ] FDT-01/FDT-02 layout 的实体被正确分类为 plan/equipment/topology/legend

### 测试基线
- [ ] `pytest tests verify -q` ≥53 通过
- [ ] `pytest verify/contract -q` 7 项通过
- [ ] `pytest verify/portability -q` ≥1 通过
- [ ] `pytest verify/reconciliation -q` ≥1 通过

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| "libredwg.py 应该被 cad_common 替换" | 用户指出两者都用 LibreDWG，但职能不同 | 不是替换而是分层：cad_common 为共享库，dwg_extractor.py 为契约实现 |
| "多 layout 应该默认启用" | 用户指出信息密度不确定，泛用性不明朗 | 独立组件，env 显式启用，按需调用 |
| "expected_census 应该完全删除" | 用户说"硬编码数字要不得，原位替换为从 json 导入的接口" | 保留 census 验证契约，数值从 project_rules.json 加载 |

## Technical Context

### main 分支解耦架构（继承源）

```
experiment/python/
├── cad_common.py          # L1-L2 共享库，676 行，31 函数，零 FTTH 符号
├── ftth_converter.py      # L3 领域转换器，3016 行，_PROJECT_CONFIG 覆盖
├── layout_miner.py        # 多 layout 挖掘，755 行，角色分类
├── legend_detector.py     # legend 检测，524 行，参数化
├── evaluator.py           # 验证引擎，1687 行
└── convert_all.py         # 4-stage orchestrator，125 行

experiment/config/
└── hutabohu.json          # 项目配置：tolerances/code_prefix/fdt_value/fat_default/label_families/layer_pattern_map
```

### robustness 当前架构（待改造）

```
src/cad2gis/
├── reader/
│   ├── libredwg.py        # 1170 行，26 函数，含 12 个与 cad_common 重叠的函数
│   ├── autocad.py         # deprecated fallback
│   ├── contracts.py       # reader 抽象接口
│   └── records_adapter.py # A 方案适配层
├── cad2gis_v3/            # 31 模块 v3 包
└── ingest.py              # canonical 入口

baselines/apd_hutabohu/config/
├── source_profile.json    # 28 个 expected_census 硬编码数字
└── source_profile_libredwg.json
```

### 目标架构

```
src/cad2gis/
├── cad_common.py          # 新增：零领域符号共享库（~30 函数）
├── reader/
│   ├── dwg_extractor.py   # 重命名自 libredwg.py，导入 cad_common，~14 契约函数
│   ├── autocad.py         # deprecated fallback
│   ├── contracts.py       # reader 抽象接口
│   └── records_adapter.py # A 方案适配层
├── cad2gis_v3/
│   ├── layout_miner.py    # 新增：多 layout 组件（可选）
│   └── ...                # 31 模块不变
└── ingest.py              # canonical 入口，数值从 project_rules.json 加载

baselines/apd_hutabohu/config/
├── source_profile.json    # schema 保留，数值从 project_rules.json 加载
├── project_rules.json     # 新增：tolerances/code_prefix/expected_census/label_families/layer_pattern_map
└── source_profile_libredwg.json
```

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| cad_common.py | core | 零领域符号 / 通用 DWG 操作 / 几何 / CRS | 被 dwg_extractor.py 导入 |
| dwg_extractor.py | core | reader 契约 / extract_dwg_records / DWGRecordInventory | 导入 cad_common.py，被 ingest.py 调用 |
| project_rules.json | core | tolerances / code_prefix / expected_census / label_families / layer_pattern_map | 被 source_profile.json 引用 |
| layout_miner.py | supporting | 多 layout 挖掘 / 角色分类 / FDT-01/FDT-02 | 可选组件，verify 阶段调用 |
| SourceProfile | core | schema 验证 / census 契约 | 数值从 project_rules.json 加载 |
| LibreDWG | supporting | ctypes bridge / dwg_read_file / dynapi | cad_common 和 dwg_extractor 共用 |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 6 | 6 | - | - | N/A |

## Interview Transcript

<details>
<summary>Full Q&A (1 round + trace injection)</summary>

### Round 1（trace 注入后）

**Q1:** main 分支通过 hutabohu.json 外化配置，robustness 的 expected_census 28 个硬编码数字如何映射？
**A1:** 原位替换为从 JSON 导入的接口。硬编码数字肯定是要不得的。

**Q2:** cad_common.py 与 libredwg.py 如何共存？
**A2:** 认真比对后发现两者都用 LibreDWG 但职能不同。cad_common 是通用函数库，libredwg 是 reader 契约实现。不是替换而是分层：抽离共享函数到 cad_common，libredwg 重命名为 dwg_extractor.py 并导入。

**Q3:** layout_miner.py 多 layout 支持如何集成？
**A3:** 多图纸处理在 main 分支是 DWG 提取阶段的可选设计，按需调用，需要解耦。verify 阶段子图纸用于呈现联动和拓扑辅助，但泛用性不明朗，独立组件可行，也需解耦。

**Q4:** 重命名事宜
**A4:** 既然两者都用 LibreDWG，且要从 cad_common 移植通用函数到 libredwg，应重命名 libredwg 为 dwg_extractor.py，抓住"从 DWG 提取记录"的最本质特征。

</details>

## Execution Notes

本 spec 涉及**三项独立但可并行的改动**：

1. **配置外化**（低风险）：新建 project_rules.json，修改 source_profile.json 加载逻辑，修改 verify/replay.py 的 EXPECTED_DELIVERY 加载逻辑
2. **共享库抽离**（中风险）：新建 cad_common.py，抽离函数，重命名 libredwg.py → dwg_extractor.py，更新所有引用
3. **多 layout 组件化**（低风险）：移植 layout_miner.py，添加 env 开关，集成到 verify/replay.py

三项改动可以按 1→2→3 顺序执行，也可以并行。建议按顺序执行以降低风险。

**执行时需注意**：
1. 不要直接删除 `libredwg.py` —— 先用 `git mv` 重命名为 `dwg_extractor.py`，再修改内容
2. `cad_common.py` 的函数从 `libredwg.py` 抽离，不是从 main 分支复制（避免引入 FTTH 符号）
3. `project_rules.json` 的 `expected_census` 数值必须与当前 `source_profile.json` 完全一致，确保测试基线不变
4. `layout_miner.py` 默认关闭，通过 `CAD2GIS_ENABLE_LAYOUT_MINER=1` 显式启用
