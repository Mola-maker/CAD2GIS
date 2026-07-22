# Deep Dive Spec: CAD2GIS Python 转换器集群 — 可复用性评估与聚合方案

## Metadata
- Interview ID: dd-20260719-py-reusability
- Trace: deep-dive-trace-comprehensively-understand-all-experiment.md
- Final Ambiguity: 8%
- Type: brownfield research / Threshold: 0.2 / Source: default / Status: PASSED

## Topology
| Component | Status | Description |
|-----------|--------|-------------|
| 可复用性量化评估 | resolved | 三线调查收敛：对 FTTH DWG 85-90% 可复用，对非电信 DWG 55-60% 可复用 |
| 架构分层与聚合方案 | resolved | 自然聚合为 4-5 个核心程序，其余已是解耦组件 |
| 指南与实现差距 | resolved | 指南是理想化架构愿景，非当前实现的文档；验证指南最对齐 |

## Goal

回答三个核心问题：
1. 这集群 Python 转换器对其它 .dwg 文件是否可复用 → **可以，有边界条件**
2. 可复用的程度 → **FTTH 项目 85-90%，非电信 DWG→GIS 55-60%**
3. 能否聚合为少数几个程序 → **可聚合为 4-5 个核心程序 + 独立组件**

## Trace Findings

详见 `deep-dive-trace-comprehensively-understand-all-experiment.md`。

### Lane 1: 代码耦合度量化 (HIGH confidence)

对 4 个核心文件（schema_config.py, converter.py, topology_builder.py, domain_vocab.py）的全部 1,200+ 硬编码值进行分类：

| Bucket | 数量 | 占比 | 含义 |
|--------|------|------|------|
| A — 领域无关 | ~45 | 3.7% | DWG 类型码、地球半径、数学默认值 |
| B — FTTH 领域 | ~650 | 54.2% | 法标 FTTH 特征类模式、领域词汇、拓扑规则 |
| C — 项目特定 | ~120 | 10.0% | Hutabohu 标签正则、印尼坐标边界、FDT 值映射 |
| D — 可配置默认值 | ~385 | 32.1% | CLI 参数默认值、容差、字段名 |

### Lane 2: 架构边界分析 (HIGH confidence)

10 个模块中 5 个是天然独立组件（legend_detector, layout_miner, evidence_ledger, style_builder, domain_vocab）。converter.py 横跨全部 7 个架构层。核心的"CAD 读取 → 分类 → 几何 → 写入"管道（L1-L3）高度可复用。

### Lane 3: 指南与实现差距 (HIGH confidence)

35 项 GeoFormer 能力矩阵：11 项已实现、14 项未实现、7 项部分实现、3 项被取代。12 项能力存在于代码但不出现在指南中。验证指南（VERIFICATION_LOOP_AGENT_PROMPTS.md）对齐度最高（8/10）。T_TOPOLOGY_REPAIR_ANALYSIS.md 是最准确的技术文档。

## 在新 DWG 文件上运行的最小改动清单

### 必须修改（不改跑不通）

1. **LAYER_PATTERN_MAP** (`schema_config.py:1870-1933`)：新 DWG 的图层命名习惯几乎必然不同。先用 `dwgread -O json` dump 图层列表，然后为每个图层写新的正则映射。
2. **LABEL_FAMILIES** (`schema_config.py:2614-2618`)：如果新项目的标注文本格式不同，需要重写 pattern 正则。无标签需求可跳过。
3. **--source-crs / --target-crs** (CLI)：已参数化，传不同 EPSG 代码即可，无需改代码。

### 应该修改（不改能跑但不正确）

4. **NEGATIVE_EVIDENCE_LAYERS** (`schema_config.py:1835-1861`)：新 CAD 作者的非主体图层名不同
5. **REGION_BOUNDS_WGS84** (`converter.py:131`)：适配新项目的地理范围
6. **FRAGMENT_AGGREGATION_LAYERS** (`schema_config.py:1942-1945`)：FDT/FAT 结构图图层名可能不同

### 不需要修改

全部 DWG 实体提取、几何重建、匈牙利标注分配、空间聚类、拓扑修复、QGIS 样式引擎、验证引擎、证据账本、图例检测器 — 这些全部是领域无关或领域可配置的。

## 聚合方案

### 推荐架构：4 个核心程序 + 已解耦组件

```
┌─────────────────────────────────────────────────┐
│                 cad_extractor                    │
│  (DWG 读取 + 几何提取 + 颜色解析 + CRS 转换)      │
│  来源：converter.py 的 L1-L2 层 (~800 lines)     │
│  性质：通用库，可独立发布                          │
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│               ftth_converter                     │
│  (FTTH 分类 + 标注绑定 + 片段聚合 + BOITE 融合)   │
│  来源：converter.py L3-L5 + schema_config.py     │
│        + domain_vocab.py (~1800 lines)           │
│  性质：FTTH 领域主程序，配置驱动                   │
│  配置：通过 --config project.json 加载项目特定值   │
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│              topology_repair                     │
│  (CABLE 链接 + 端点吸附 + FDT 域标记)             │
│  来源：topology_builder.py (~1300 lines)          │
│  性质：独立组件，已解耦                            │
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│              style_exporter                      │
│  (QML 旁车 + layer_styles 嵌入 + .qgz 项目)      │
│  来源：style_builder.py (~450 lines)             │
│  性质：独立组件，已解耦                            │
└─────────────────────────────────────────────────┘

已解耦的独立组件（可单独使用或作为插件加载）：
  ├── legend_detector.py   (图例集群检测)
  ├── layout_miner.py      (图纸空间属性挖掘)
  ├── evidence_ledger.py   (证据账本)
  ├── evaluator.py         (质量验证引擎)
  └── t_experiment.py      (实验分析工具)
```

### 配置外部化策略

当前 10% 的项目特定耦合（Bucket C, ~120 项）集中在约 5 个位置。推荐外部化到单一 JSON 项目配置文件：

```json
{
  "project": "hutabohu",
  "region_bounds_wgs84": [-11.0, 7.0, 95.0, 141.0],
  "source_crs": "EPSG:3857",
  "target_crs": "EPSG:3857",
  "label_families": [...],
  "layer_pattern_map": {...},
  "negative_evidence_layers": [...],
  "fragment_aggregation_layers": [...],
  "code_prefix": {...},
  "fdt_value": {"FDT-01": 48, "FDT-02": 72},
  "tolerances": {
    "fragment_cluster": 50.0,
    "annotation_link": 15.0,
    "boite_fusion": 5.0,
    "snap": 5.0,
    "isolation": 30.0
  }
}
```

新项目 = 新 JSON 文件，converter.py 代码不改。

### 不需要的聚合

以下模块不建议合并：
- **schema_config.py + domain_vocab.py**：可以合并为 ftth_config.py，但当前分离是有意义的（schema 是静态模式定义，vocab 是动态 CSV 加载）
- **evidence_ledger.py + evaluator.py**：虽然 evaluator 读取 evidence 表，但语义不同（一个是写入者，一个是读取验证者），分离是正确的
- **legend_detector.py 与任何模块**：纯空间算法，完全独立

## Constraints

- 不改动当前已验证的 Hutabohu 转换结果（CABLE=203, gap_bridges=0, FDT 151/51/1, SUM 6942）
- 外部化不引入新的依赖（纯 JSON + stdlib json 模块）
- 聚合不改变管线执行顺序
- converter.py 的 `--config` 参数已存在但未实现——实现它而非新增参数

## Non-Goals

- 不重写为 GeoFormer 9-agent DAG 架构（该架构从未实现过，且当前顺序管线已验证正确）
- 不实现 Kvisimine 四叉树瓦片分解（单 DWG 文件不需要）
- 不实现 LLM 语义桥接（当前确定性方法已足够）
- 不新写 GeoFormer AgentPrompts 文档（应标记为"架构愿景"并新建基于实际代码的文档）

## Acceptance Criteria

- [ ] 三个核心问题的答案已在本文档中明确记录，有量化证据支持
- [ ] 最小改动清单已列出具体文件路径和行号
- [ ] 聚合方案明确了每个核心程序的职责、来源、行数估计
- [ ] 配置外部化策略有具体的 JSON schema 示例
- [ ] 10 条工程经验教训已从 15 个 spec 的 trace→interview→implement 周期中提取

## Assumptions Exposed
| Assumption | Resolution |
|------------|------------|
| DWG 图层命名在不同 FTTH 项目间高度一致 | 未经验证——这是最关键的不确定性。需要用 3-5 个不同项目的 DWG 做 LAYER_PATTERN_MAP 覆盖测试 |
| FTTH 特征类模式（BOITE/CABLE/PTECH 等）在其他 FTTH 项目中可复用 | 高置信度——这些是法标 FTTH 标准，但印尼项目可能使用不同术语 |
| 当前顺序管线对单个 DWG 已足够，不需要并行 | 已验证——Hutabohu 单文件 3,416 行 converter 运行时间在分钟级 |

## Technical Context
- 10 个 Python 脚本总计 ~12,000 行
- 核心依赖：LibreDWG ctypes, GDAL/OGR, Shapely, QGIS (style_builder only)
- 4 个 guide 文档, 15 个 spec (6 interview + 9 trace)
- 已验证基线：Hutabohu DWG → 8 FC GPKG, CABLE=203, FDT 151/51/1, SUM=6942
