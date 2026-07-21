# Deep Interview Spec: 转换脚本聚合方案修正

## Metadata
- Interview ID: di-20260719-consolidation-fidelity
- Rounds: 3 (+ Round 0)
- Final Ambiguity: 20.5%
- Type: brownfield
- Threshold: 0.2 / Source: default
- Status: PASSED (at threshold)

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.85 | 0.35 | 0.30 |
| Constraint Clarity | 0.75 | 0.25 | 0.19 |
| Success Criteria | 0.70 | 0.25 | 0.18 |
| Context Clarity | 0.90 | 0.15 | 0.14 |
| **Total Clarity** | | | **0.80** |
| **Ambiguity** | | | **0.20** |

## Topology
| Component | Status | Description |
|-----------|--------|-------------|
| 效率评估对比 | resolved | 两种方案的差异已澄清：package 方案胜在一条命令跑完，独立脚本方案胜在架构强制力和分段可重跑。选定方案需调和两者优势 |
| 选定方案实施 | active | 实施混合方案：共享底层库 + 独立入口脚本 + 薄编排器保证批量简洁度 |

## Goal

修正当前 `experiment/python/` 的实现——它只是将 10 个原始模块复制到包内并修改 import，并未按 deep-dive spec 的要求实际拆分 converter.py。目标结构：

1. **共享底层库 `cad_common.py`** — 从 converter.py 提取 L1-L2 层：DWG 类型码、ctypes 桥接、几何重建（`_extract_wkt`）、颜色解析（`_parse_dwg_color`）、CRS 转换函数、WKT 写入辅助。不依赖任何 FTTH 领域知识。
2. **`ftth_converter.py`** — 从 converter.py 提取 L3-L5 层：两层分类（`_classify_entity_tier1/2`）、片段聚合、匈牙利标注分配、BOITE 多表示融合、GeoPackage 写入。import cad_common。合并 schema_config.py 和 domain_vocab.py 的 FTTH 配置逻辑。通过 `--config project.json` 加载项目特定值。
3. **`topology_repair.py`** — 保持当前 topology_builder.py 的独立 CLI，重命名为更语义化的名称。
4. **`style_exporter.py`** — 保持当前 style_builder.py 的独立 CLI。
5. **辅助组件** — legend_detector.py, layout_miner.py, evidence_ledger.py, evaluator.py 保留为独立脚本。

## Constraints

- **不改动已验证基线**：Hutabohu 转换结果必须 20/20 层与现有 GPKG 匹配（CABLE=166, PTECH=167, BOITE=45, CONV=6942）
- **JSON 配置机制保留**：`--config project.json` 外部化方案继续工作，新项目只需改 JSON
- **不引入新依赖**：纯 Python stdlib + 现有依赖（LibreDWG ctypes, GDAL/OGR, Shapely, QGIS for style_builder only）
- **现有 py_scripts/ 不变**：原目录保留作为参考
- **入口脚本格式**：每个核心脚本有 `if __name__ == "__main__"` 入口 + `argparse` CLI，支持直接 `python3 xxx.py` 执行
- **薄编排器**：提供一个 `convert_all.py` 串联四个阶段的管道，保证批量场景的一条命令简洁度

## Non-Goals

- 不重写为 GeoFormer 9-agent DAG 架构
- 不修改辅组组件（legend_detector, layout_miner, evidence_ledger, evaluator）的逻辑
- 不改变配置文件格式（hutabohu.json 保持有效）
- 不优化 converter.py 内部的算法实现
- 不移除已验证的任何功能

## Acceptance Criteria

- [ ] `cad_common.py` 可独立运行（至少可 import，所有函数无 FTTH 特定引用）
- [ ] `ftth_converter.py` import cad_common 后，通过 `--config hutabohu.json` 可独立完成 DWG→GPKG 转换（分类+标注+融合+写入）
- [ ] `topology_repair.py` 可独立在已写入的 GPKG 上运行拓扑修复
- [ ] `style_exporter.py` 可独立在已修复的 GPKG 上生成 QML/QGZ
- [ ] `convert_all.py` 一条命令串联四个阶段，批量场景只需：`python3 convert_all.py --config project.json --input project.dwg --output project.gpkg`
- [ ] Hutabohu 端到端回归：通过 `convert_all.py` 转换后，20/20 层与现有 GPKG 计数一致
- [ ] 新项目场景验证：用 `--config hutabohu.json` 跑现有 DWG，结果必须与基线一致
- [ ] cad_common.py 不含任何 FTTH 领域符号（BOITE, CABLE, PTECH, FAT, FDT, DMPH 等）
- [ ] ftth_converter.py 不含任何 DWG 类型码字面量（DWG_TYPE_LINE=0 等），全部通过 cad_common 引用

## Assumptions Exposed
| Assumption | Resolution |
|------------|------------|
| converter.py 的 L1-L2 可以干净地从 L3-L5 中分离 | 待实施验证——函数间可能存在未发现的耦合（如 _extract_wkt 内部引用了 FTTH 特定的 layer 判断） |
| cad_common.py 的 ~800 行估计准确 | 待实施验证——实际提取时可能因边界模糊而增减 |
| 薄编排器 `convert_all.py` 不牺牲分段重跑的灵活性 | 编排器应既支持"全量跑"也支持 `--skip-extract` 等分段控制 |

## Technical Context

**当前状态：** `experiment/python/` 包含 10 个模块（~11,791 行），仅为 `py_scripts/` 的 import 修正副本。`converter.py` 仍为 3,510 行的单体文件，横跨 L1-L7 全部架构层。

**关键代码位置：**
- L1 DWG 解析：`converter.py:62-95` (_init_libredwg), `:456-523` (_extract_wkt), `:287-330` (_parse_dwg_color)
- L2 空间处理：`converter.py:356-363` (_adaptive_chord_tolerance), `:548-593` (union-find), `:773-826` (Hungarian)
- L3 分类：`converter.py:611-669` (_classify_entity_tier1/2, _assign_fc)
- L4 FTTH 融合：`converter.py:1070-1179` (_fuse_boite_representations)
- L5 标注绑定：`converter.py:762-915` (_assign_family_annotations)
- schema_config.py: LAYER_PATTERN_MAP (:1870), LABEL_FAMILIES (:2614), NEGATIVE_EVIDENCE_LAYERS (:1835)
- topology_builder.py: chain_edges (:741), repair_edges (:270), tag_fdt_domains (:1105)

## Recommended File Structure

```
experiment/python/
├── cad_common.py          ← 共享底层库 (L1-L2, ~800行, 无FTTH符号)
├── ftth_converter.py      ← FTTH主转换器 (L3-L5, ~1800行, import cad_common)
├── topology_repair.py     ← 拓扑修复 (原 topology_builder.py, ~1300行)
├── style_exporter.py      ← 样式导出 (原 style_builder.py, ~450行)
├── schema_config.py       ← FTTH模式定义 (保留, ftth_converter导入)
├── domain_vocab.py        ← 领域词汇验证 (保留, ftth_converter导入)
├── legend_detector.py     ← 图例检测 (独立组件, 不变)
├── layout_miner.py        ← 图纸空间挖掘 (独立组件, 不变)
├── evidence_ledger.py     ← 证据账本 (独立组件, 不变)
├── evaluator.py           ← 质量验证 (独立组件, 不变)
└── convert_all.py         ← 薄编排器 (串联四个阶段)
```

## Ontology (Key Entities)
| Entity | Type | Description |
|--------|------|-------------|
| cad_common | core | 共享底层库：DWG类型码、ctypes桥接、几何重建、颜色解析、CRS转换 |
| ftth_converter | core | FTTH领域转换器：分类、标注绑定、片段聚合、BOITE融合 |
| topology_repair | core | 拓扑修复：CABLE链接、端点吸附、FDT域标记 |
| style_exporter | core | QGIS样式：QML/QGZ生成、layer_styles嵌入 |
| convert_all | orchestrator | 薄编排器：串联四个阶段的一条命令入口 |
| Project Config | config | JSON配置文件：LABEL_FAMILIES、LAYER_PATTERN_MAP、CRS等 |

## Interview Transcript
<details>
<summary>Full Q&A (3 rounds)</summary>

### Round 0 (Topology)
**Q:** 5个组件: cad_extractor, ftth_converter, topology_repair, style_exporter, 辅助组件 — 拓扑正确？
**A:** 评估基于python语言特性下两种文件结构对于大批次处理同公司不同地点基建dwg文件的效率

### Round 1
**Q:** 判断哪种文件结构更优时，最看重哪个效率维度？
**A:** 多维度综合：批量脚本编写便利性、代码去重复用、启动运行开销都需要

### Round 2
**Q:** 4个核心脚本之间应该共享底层代码到什么程度？
**A:** 共享底层库 + 独立入口脚本 (Recommended)

### Round 3
**Q:** 评估两种方案优劣时，哪个可衡量的结果最重要？
**A:** 批量场景的代码简洁度 (Recommended)

</details>
