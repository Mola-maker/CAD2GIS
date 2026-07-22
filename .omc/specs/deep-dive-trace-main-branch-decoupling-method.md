# Deep Dive Trace: main-branch-decoupling-method

## Observed Result

User designed a decoupling method in main branch (pure WSL2 environment) where hardcoded number counts were externalized to config while retaining APD field-matching validation. The robustness workspace (inherited from newmodel) currently has 28 hardcoded `expected_census` numbers in `source_profile.json` and APD-specific logic in `apd_rules.py` — the opposite of decoupled. Decoupling is a fundamental robustness metric.

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | **Config 外化**: main 分支通过 `experiment/config/hutabohu.json` 将项目专属值外化到 JSON | High | Strong | `_apply_project_config()` 完整展示覆盖机制；tolerances/code_prefix/fdt_value/fat_default/label_families/layer_pattern_map 全部外化 |
| 2 | **共享库 + 项目覆盖**: `cad_common.py` 零领域符号，通用 DWG 操作可复用 | High | Strong | `cad_common.py` 头部明确声明 "Contains ZERO FTTH domain symbols... Reusable for any DWG-to-GIS pipeline" |
| 3 | **Schema/校验分离**: main 的 schema_config.py 无硬编码计数，校验数字在 JSON | High | Strong | main schema_config.py 只有字段描述；robustness 的 `expected_census` 有 28 个硬编码数字 |
| 4 | **改动量 + 跨平台兼容性**: main 的 experiment/python/ 与 newmodel 的 cad2gis_v3/ 架构根本不同 | High | Strong | main 13 文件 ~15,000 行新代码；newmodel 31 文件 v3 包；架构哲学完全不同 |

## Evidence Summary by Hypothesis

### Hypothesis 1: Config 外化（`hutabohu.json`）

`experiment/config/hutabohu.json` 包含：

| 配置节 | 内容 | 对应 robustness 硬编码 |
|--------|------|------------------------|
| `tolerances` | fragment_cluster_m=50, annotation_link_m=15, boite_fusion_m=5, snap_m=5, isolation_m=30, chain_m=0.5, site_snap_max_m=50 | 散落在各 Python 文件中的容差参数 |
| `code_prefix` | SITE=PM, BOITE=PBO, PTECH=PT, IMB=IMB, CABLE=CBL, INFRASTRUCTURE=INF, ZPM=ZPM, ZNRO=ZNR | 硬编码在 schema_config.py |
| `fdt_value` | FDT-01=48, FDT-02=72 | 无对应（robustness 无多 FDT 支持） |
| `fat_default` | 16 | 无对应 |
| `label_families` | 3 组正则：fat→BOITE, pole→PTECH, pole_ext→PTECH | 硬编码在 apd_rules.py |
| `layer_pattern_map` | 13 组正则映射 DWG 图层到 FC | 硬编码在 schema_config.py |
| `negative_evidence_layers` | 25 个排除图层 | 无对应 |
| `fragment_aggregation_layers` | 2 组聚合规则 | 无对应 |

`_apply_project_config()`（ftth_converter.py:2056-2130）展示覆盖机制：
- `layer_pattern_map` 采用 **prepend** 策略（项目模式优先于默认模式）
- `negative_evidence_layers` 采用 **merge** 策略（并集）
- `label_families` 采用 **full replacement** 策略
- tolerances 逐项覆盖并同步到 CLI args

### Hypothesis 2: 共享库 + 项目覆盖

`experiment/python/` 架构：

```
cad_common.py (676 行)          # L1-L2 共享库，零 FTTH 符号
  ├── L1: DWG type constants, ctypes bridge, geometry, dimension, clustering
  └── L2: CRS parameterisation, coordinate transforms, colour parsing, geodesy

ftth_converter.py (3016 行)     # FTTH 领域转换器
  ├── imports cad_common
  ├── _PROJECT_CONFIG 全局变量
  ├── _apply_project_config() 覆盖机制
  └── 8-FC 分类、标注链接、GPKG 写入

converter.py (3418 行)          # 独立版（cad_common 函数内嵌）
layout_miner.py (755 行)        # 多 layout paper space 挖掘
legend_detector.py (524 行)     # 参数化 legend 检测
topology_repair.py (1378 行)    # 拓扑修复
style_exporter.py (473 行)      # QML 样式导出
evaluator.py (1687 行)          # 规则验证引擎
convert_all.py (125 行)         # 4-stage pipeline orchestrator
```

`cad_common.py` 头部声明：
> "Contains ZERO FTTH domain symbols (BOITE, CABLE, PTECH, FAT, FDT, DMPH, NRO, PM, ZNRO, IMB etc.). Reusable for any DWG-to-GIS pipeline."

`layout_miner.py` 的多 layout 支持：
- Layout 角色分类：topology / legend / equipment / plan（正则族）
- `FDT-01`, `FDT-02` 等 plan layout 被识别为 FDT_ID 标签源
- `FDT LAYOUT` 被分类为 equipment，不会误归入 plan
- 明确不移植 newmodel 的 "len(components) == len(layouts)" 硬断言

`legend_detector.py` 的参数化设计：
- 明确避免 "legend is always rightmost" 假设
- 明确避免硬编码 sheet 比例（如 0.2 阈值）
- 所有 knob 可配置：gap_min=100.0, gap_k=0.15, min_features=10, max_cluster_fraction=0.5 等

### Hypothesis 3: Schema/校验分离

**main 分支**：
- `schema_config.py` 只有字段描述（"Connectable households count", "Splice count (>= 0)"）
- 无 `EXPECTED_COUNTS` / `expected_census` 硬编码
- 计数期望值在 `hutabohu.json` 中（fat_default=16, fdt_value={FDT-01:48, FDT-02:72}）

**robustness 工作区**：
- `source_profile.json` 有 28 个 `expected_census` 硬编码数字
- `verify/replay.py` 有 `EXPECTED_DELIVERY = {"BOITE": 43, "CABLE": 6, ...}` 硬编码
- `apd_rules.py` 有 APD 专属 block 名映射和电信设施判断

**对比结论**：main 的校验是"结构在 Python、数值在 JSON"；robustness 是"结构和数值都在 JSON/Python 中硬编码"。

### Hypothesis 4: 改动量 + 跨平台兼容性

**架构差异**：

| 维度 | main (`experiment/python/`) | newmodel (`experiment/py_scripts/cad2gis_v3/`) |
|------|------------------------------|--------------------------------------------------|
| 文件数 | 13 | 31 |
| 核心行数 | ~15,000 | ~19,702 |
| 架构 | 共享库 + 项目覆盖 | 单体式 v3 包 |
| DWG reader | LibreDWG (ctypes) | AutoCAD (accoreconsole) + LibreDWG (robustness 新增) |
| 配置 | `hutabohu.json` 外化 | `source_profile.json` + `mapping_registry.json` |
| 多 layout | `layout_miner.py` 支持 | 不支持（仅 Model space） |
| Legend 检测 | `legend_detector.py` 参数化 | `autocad_reader.py:551-608` 硬编码 |
| 跨平台 | 纯 WSL2/Linux | Windows-only (AutoCAD) + Linux (LibreDWG) |

**跨平台兼容性**：
- main 的 `cad_common.py` 是纯 Python + ctypes (LibreDWG)，跨平台
- `layout_miner.py` 使用 `dwgread` 二进制（LibreDWG 工具链），跨平台
- `legend_detector.py` 纯 Python，跨平台
- 无 Windows COM 依赖

**移植到 robustness 的改动量评估**：
- `cad_common.py` → 可直接复用，适配 `src/cad2gis/reader/libredwg.py` 的 ctypes 桥
- `layout_miner.py` → 可移植到 `src/cad2gis/cad2gis_v3/` 或 `verify/`，需适配 `SourceEntity` 模型
- `legend_detector.py` → 可移植到 `src/cad2gis/cad2gis_v3/`，替代 autocad_reader.py 的硬编码 legend 检测
- `hutabohu.json` 配置模式 → 可替代 `source_profile.json` 的 `expected_census` 硬编码
- `evaluator.py` 验证引擎 → 与 robustness 的 `verify/` 测试互补

## Evidence Against / Missing Evidence

- **Hypothesis 1**: main 的 config 外化仍保留 APD 字段匹配（label_families 正则含 DMPH 前缀），不是完全项目无关
- **Hypothesis 2**: `converter.py` (3418 行) 是 `cad_common` 函数内嵌的独立版，与 `ftth_converter.py` 存在代码重复
- **Hypothesis 3**: main 的 `evaluator.py` 仍从 `schema_config.py` 导入 `VERIFICATION_RULES`，规则结构本身未外化
- **Hypothesis 4**: main 的 `topology_repair.py` (1378 行) 未在 newmodel 中找到对应，可能是 main 独有

## Per-Lane Critical Unknowns

- **Lane 1 (Config 外化)**: 如何将 main 的 `hutabohu.json` 配置结构映射到 robustness 的 `SourceProfile` / `MappingRegistry` schema？两套配置体系是否兼容？
- **Lane 2 (共享库)**: `cad_common.py` 能否直接导入 robustness 的 `src/cad2gis/`，还是需要适配 `SourceEntity` / `DWGRecordInventory` 模型？
- **Lane 3 (Schema/校验分离)**: 如何用 main 的"结构在 Python、数值在 JSON"模式替换 robustness 的 `expected_census` 硬编码，同时保持 `ingest.py` 的 census 验证契约？
- **Lane 4 (改动量 + 兼容性)**: 将 main 的 `layout_miner.py` 和 `legend_detector.py` 移植到 robustness 的 `cad2gis_v3` 包中，需要多大的接口适配工作量？

## Lane 3 Misplacement / SoT Ownership Scope

| Source | Candidate destination | ownership_scope | Boundary relationship | Default? | Warning |
|--------|-----------------------|-----------------|-----------------------|----------|---------|
| main `experiment/python/cad_common.py` | robustness `src/cad2gis/cad_common.py` | project-scoped | same-scope | yes | — |
| main `experiment/python/layout_miner.py` | robustness `src/cad2gis/cad2gis_v3/layout_miner.py` | project-scoped | same-scope | yes | — |
| main `experiment/python/legend_detector.py` | robustness `src/cad2gis/cad2gis_v3/legend_detector.py` | project-scoped | same-scope | yes | — |
| main `experiment/config/hutabohu.json` | robustness `baselines/apd_hutabohu/config/hutabohu.json` | project-scoped | same-scope | yes | — |
| main `experiment/python/ftth_converter.py` | robustness `src/cad2gis/ftth_converter.py` | project-scoped | same-scope | no | 与 `cad2gis_v3/pipeline.py` 功能重叠，需合并而非并存 |
| main `experiment/python/converter.py` | robustness `src/cad2gis/converter.py` | project-scoped | same-scope | no | 与 `cad_common.py` 重复，应抽离共享函数而非复制 |
| main `experiment/python/evaluator.py` | robustness `verify/evaluator.py` | project-scoped | same-scope | no | 与 `verify/reconciliation/` 测试重叠，需评估互补性 |

## Rebuttal Round

- **Best rebuttal to leader**: "main 的解耦仍保留 APD 字段匹配（DMPH 正则），不是真正项目无关" — 成立，但用户明确说"没有脱离针对 apd 项目的字段匹配校验，不过写死的数字个数解耦了"，说明这是有意为之的折中。
- **Why leader held**: 用户的设计目标不是完全项目无关，而是"数字解耦 + 字段保留"，main 分支精确实现了这一目标。

## Convergence / Separation Notes

四条 lane 收敛到同一结论：main 分支通过 **"共享库零领域化 + 配置外化 + 数值参数化"** 实现了解耦，而 robustness 工作区（newmodel 继承）是 **"单体式包 + 硬编码数值"** 的反模式。

## Most Likely Explanation

main 分支的解耦方法是三层架构：
1. **L1-L2 共享层**（`cad_common.py`）：零领域符号，纯 DWG/几何/CRS 操作，可复用于任何项目
2. **L3 领域层**（`ftth_converter.py` / `layout_miner.py` / `legend_detector.py`）：FTTH 领域逻辑，通过 `_PROJECT_CONFIG` + `--config` JSON 注入项目专属值
3. **配置层**（`hutabohu.json`）：所有项目专属数字（tolerances、code_prefix、fdt_value、fat_default、label_families、layer_pattern_map）外化到 JSON

这与 robustness 工作区的 `expected_census` 28 个硬编码数字形成鲜明对比。main 的解耦是"数值外化、结构保留"，robustness 是"数值和结构都硬编码"。

## Critical Unknown

如何将 main 的"数值外化、结构保留"解耦模式迁移到 robustness 工作区，同时保持与 newmodel 继承的 `cad2gis_v3` 包和 `SourceEntity` 模型的兼容性？具体而言：
1. `hutabohu.json` 的配置 schema 能否直接替代 `source_profile.json` 的 `expected_census`？
2. `cad_common.py` 的函数能否直接复用到 `src/cad2gis/reader/libredwg.py`？
3. `layout_miner.py` 的多 layout 支持如何集成到 `cad2gis_v3/pipeline.py`？

## Recommended Discriminating Probe

创建一个概念验证：将 `hutabohu.json` 的 `tolerances` 和 `code_prefix` 加载到 robustness 的 `SourceProfile` 中，替换 `expected_census` 的硬编码数字，运行 `pytest tests/` 验证是否保持通过。如果通过，说明配置外化路径可行；如果失败，暴露出 schema 不兼容的具体位置。
