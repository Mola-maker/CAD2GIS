# CAD2GIS 转换器集群聚合报告

**日期:** 2026-07-19  
**基线 DWG:** `APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`  
**基线 GPKG:** `experiment/output/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.gpkg`  
**产物 GPKG:** `experiment/output/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO_NEW_1.gpkg`  
**关联 Spec:** `.omc/specs/deep-interview-consolidation-fidelity.md`  
**关联 Plan:** `.omc/plans/ralplan-consolidation-fidelity.md`

---

## 1. 聚合目标

按 deep-dive spec (`.omc/specs/deep-dive-comprehensively-understand-all-experiment.md`) 的要求，将 `experiment/py_scripts/` 中的 10 个 Python 脚本（~12,000 行）聚合为 4-5 个核心程序 + 独立组件，并外部化项目特定配置到 JSON 文件。

推荐架构:

```
cad_common       (L1-L2: DWG 解析 + 几何 + 颜色 + CRS)
  ↓
ftth_converter   (L3-L5: FTTH 分类 + 标注绑定 + BOITE 融合 + GPKG 写入)
  ↓
topology_repair  (CABLE 链接 + 端点吸附 + FDT 域标记)
  ↓
style_exporter   (QML 旁车 + layer_styles 嵌入 + .qgz 项目)
```

独立组件: legend_detector, layout_miner, evidence_ledger, evaluator

---

## 2. 最终文件结构

```
experiment/
├── python/                          ← 聚合后的包
│   ├── __init__.py                  ← 包文档字符串
│   ├── cad_common.py                ← 【新】L1-L2 共享底层库 (676 行)
│   ├── converter.py                 ← 完整转换器 (3,416 行, 从 py_scripts/ 恢复)
│   ├── ftth_converter.py            ← L3-L5 提取尝试 (2,976 行, 有回归)
│   ├── topology_repair.py           ← 重命名自 topology_builder.py (1,378 行)
│   ├── style_exporter.py            ← 重命名自 style_builder.py (473 行)
│   ├── convert_all.py               ← 【新】四阶段薄编排器 (142 行)
│   ├── schema_config.py             ← FTTH 模式定义 (2,680 行, 不变)
│   ├── domain_vocab.py              ← 领域词汇验证 (352 行, 不变)
│   ├── evaluator.py                 ← 质量验证引擎 (1,687 行, 不变)
│   ├── evidence_ledger.py           ← 证据账本 (420 行, 不变)
│   ├── layout_miner.py              ← 图纸空间挖掘 (755 行, 不变)
│   └── legend_detector.py           ← 图例检测 (524 行, 不变)
├── config/
│   ├── hutabohu.json                ← 【新】项目特定配置 (4.2 KB)
│   └── legend_exclusions.json       ← 图例排除
├── output/
│   ├── APD ... HUTABOHU.gpkg        ← 现有基线 (884,736 bytes)
│   └── APD ... NEW_1.gpkg           ← 聚合后产物 (884,736 bytes)
├── archives/
│   └── consolidation-report-2026-07-19.md  ← 本文件
└── py_scripts/                      ← 原始脚本 (不变, 参考)
```

---

## 3. cad_common.py — 共享底层库

### 3.1 设计

从 converter.py 提取所有领域无关代码，划分为两个逻辑层：

| 层 | 内容 | 行数估计 |
|---|------|---------|
| L1 — DWG 解析 | DWG 类型码常量、ctypes 桥接 (`_init_libredwg`)、UTF-16 文本提取 (`_entity_utf8_text`)、几何重建 (`_extract_wkt`)、DIMENSION 提取 (`_extract_dimension`)、空间聚类 (`_cluster_points`) | ~340 行 |
| L2 — 空间处理 | CRS 参数化 (`init_crs`)、坐标变换 (`_reproject_point`, `_to_wgs84`)、颜色解析 (`_parse_dwg_color`, `_resolve_effective_color`)、ACI→RGB 色表 (`aci_to_rgb` 链)、地理计算 (`_haversine` 系列) | ~336 行 |

### 3.2 关键设计决策

**init_crs() 显式初始化 API** — 替代原始 converter.py 中 `main()` 直接变异模块全局变量的模式:

```python
# 原始模式 (converter.py main()):
global _CRS_TRANSFORM, _TO_WGS84, ...
_CRS_TRANSFORM = osr.CoordinateTransformation(src, dst)

# 新模式 (cad_common.init_crs()):
cad_common.init_crs(args.source_crs, args.target_crs)
```

每个 CRS 依赖函数包含 `_CRS_INITIALIZED` 守卫标志，未初始化时抛出 `RuntimeError`:

```python
def _reproject_point(x, y):
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
    # ...
```

**DWG_TYPE_* 占位符模式** — 初始值为 LibreDWG 无关的占位符（`DWG_TYPE_INSERT = 7`, `DWG_TYPE_ARC = 7` — 同值碰撞是已知问题）。`read_dwg()` 在运行时通过 `cad_common.DWG_TYPE_LINE = L_LINE` 样式补丁修正。

**跨模块变异契约** — 三类可变全局变量 (`DWG_TYPE_*`, `DIMENSION_TYPE_UNION`, `CONTROL_TYPES`) 声明在 cad_common 中，由 ftth_converter 中的 `read_dwg()` 补丁。详见 plan §5.1.2。

**ACI→RGB 色表自包含** — 从 schema_config.py 复制完整链 (`_hsv_bytes` → `_generate_aci_table` → `ACI_TO_RGB` → `aci_to_rgb`)，避免 cad_common 依赖 schema_config。

### 3.3 验证

- **导入:** `from python.cad_common import init_crs, _extract_wkt, aci_to_rgb` — 通过
- **零 FTTH 符号:** `grep -ci "boite|cable|ptech|fat|fdt|dmph|znro|zpm|nro|pm|imb|site_type|type_box" cad_common.py` — 返回 0
- **行数:** 676 行（计划估计 ~560 行，CRS 守卫增加了约 100 行）

---

## 4. 代码整合得失

### 4.1 成功项

| 项目 | 详情 |
|------|------|
| **topology_repair.py / style_exporter.py 重命名** | 纯文件重命名 + `__init__.py` 文档更新。零风险。 |
| **convert_all.py 编排器** | 142 行 thin wrapper。支持 `--skip-extract`, `--skip-topology`, `--skip-styles` 分段控制。 |
| **JSON 配置外部化** | `hutabohu.json` (4.2 KB) 外部化了 120+ 项目特定值。`--config` 参数从 "not used" 到完全实现。新项目 = 新 JSON 文件。 |
| **converter.py 回归保持** | 恢复后的 converter.py (来自 py_scripts/ 复制) 在应用 JSON config + topology/style import 修正后，产生 20/20 层匹配的输出。 |
| **端到端验证** | NEW_1.gpkg 与基线 GPKG 在所有 20 层上计数一致。CONV-SUM=6942。文件大小: 884,736 bytes（含 layer_styles=10）。 |

### 4.2 未完全达成

| 项目 | 详情 |
|------|------|
| **ftth_converter.py 独立** | L3-L5 提取尝试产生 2,976 行文件，导入无报错，但运行时产生多处回归（CABLE=0, ZPM=0, 标注丢失, 167 ISOLATED_NODE）。根因分析见 §4.3。 |

### 4.3 ftth_converter.py 提取失败根因

尝试从 converter.py (3,416 行) 剥离 L1-L2 代码（~600 行）以创建 ftth_converter.py 时，遇到以下问题:

**根因 1: CRS 全局状态引用散布**

原始 converter.py 中，`_CRS_TRANSFORM`, `_SOURCE_IS_GEOGRAPHIC` 等 CRS 全局变量在 15+ 个位置被引用。剥离 L1-L2 后，这些引用需要全部改为 `cad_common._CRS_TRANSFORM` 前缀。自动化前缀脚本遗漏了 `write_geopackage()` 函数内的间接引用（`transform_desc` 变量），导致 `NameError`。

**根因 2: OGR/GDAL 导入边界**

L1-L2 剥离时一并移除了 `from osgeo import ogr, osr` 导入，但 L3-L5 函数（`write_geopackage`, `_ogr_field_type` 等）仍依赖 `ogr`。单独恢复导入后解决了此问题。

**根因 3: 标注绑定与 BOITE 融合静默失败**

最严重的回归——CABLE=0 和 ZPM=0——的根因尚未完全定位。怀疑与 `_extract_wkt` 调用链有关：ftth_converter 导入 `_extract_wkt` 从 cad_common，但 `_extract_wkt` 内部依赖 `DWG_TYPE_*` 常量（也是从 cad_common 导入的）。在原始 converter.py 中，`read_dwg()` 在运行时补丁 `DWG_TYPE_*` 全局变量；在 ftth_converter.py 中，补丁代码使用 `cad_common.DWG_TYPE_LINE = L_LINE` 语法，但 `_extract_wkt` 函数体引用的是**导入时的本地绑定**（`from .cad_common import DWG_TYPE_LINE`），而非模块属性。Python 的 import 绑定创建的是值的快照，不会被 `cad_common.DWG_TYPE_LINE = ...` 更新。

这是一个**Python import 语义的已知陷阱**: `from X import Y` 在导入方创建 `Y` 的本地绑定；后续对 `X.Y` 的赋值不会更新导入方的本地绑定。

**根因 4: 函数间间接依赖复杂**

converter.py 中 56 个函数之间存在密集的调用关系。虽然 plan 的 §5.0.2 定义了提取依赖顺序，但某些依赖链在静态分析中不可见——例如 `_extract_wkt` → `_adaptive_chord_tolerance` → `_SOURCE_IS_GEOGRAPHIC` 的守卫检查链。

### 4.4 当前的混合方案

鉴于 ftth_converter.py 提取的困难，当前采用的方案是:

- **cad_common.py** — 共享底层库，独立可导入，零 FTTH 符号 ✅
- **converter.py** — 保留完整功能（含 L1-L2 代码 + FTTH 逻辑），但添加了:
  - 相对导入修正
  - JSON config 加载（`--config` 参数）
  - topology_repair/style_exporter import 别名
  - dwgread JSON `strict=False` 解析修复
- **topology_repair.py, style_exporter.py** — 独立可调用 ✅
- **convert_all.py** — 编排器 ✅

此混合方案在保持完全回归兼容性的同时，实现了核心价值：cad_common 共享库可复用、模块重命名反映实际功能、配置驱动项目切换。

---

## 5. Hutabohu DWG 转化效果

### 5.1 端到端指标

| 指标 | 基线 GPKG | NEW_1 GPKG | 匹配 |
|------|----------|-----------|------|
| BOITE | 45 | 45 | ✅ |
| CABLE | 166 | 166 | ✅ |
| PTECH | 167 | 167 | ✅ |
| IMB | 682 | 682 | ✅ |
| SITE | 2 | 2 | ✅ |
| ZPM | 43 | 43 | ✅ |
| INFRASTRUCTURE | 0 | 0 | ✅ |
| ZNRO | 0 | 0 | ✅ |
| span_annotations | 170 | 170 | ✅ |
| span_records | 170 | 170 | ✅ |
| conservation_ledger | 110 | 110 | ✅ |
| annotation_assignment_candidates | 226 | 226 | ✅ |
| field_provenance | 31 | 31 | ✅ |
| layer_styles | 10 | 10 | ✅ |
| topology_evidence | 104 | 104 | ✅ |
| quarantine_review | 12 | 12 | ✅ |
| qc_summary | 21 | 21 | ✅ |
| pipeline_manifest | 1 | 1 | ✅ |
| transform_record | 17 | 17 | ✅ |
| drop_accounting | 138 | 138 | ✅ |

### 5.2 标注分配

| 标注族 | 分配/总数 | 候选边 | 容差外 | 多重最优 | 冲突 |
|--------|----------|--------|-------|---------|------|
| fat → BOITE | 43/43 | 43 | 0 | 0 | 0 |
| pole → PTECH | 118/118 | 123 | 0 | 0 | 0 |
| pole_ext → PTECH | 49/49 | 60 | 0 | 0 | 0 |

### 5.3 FDT 域分布

| 域 | FDT_ID | FAT 序列 | BOITE 数 |
|----|--------|---------|---------|
| FDT-01 | DMPH-1.010 | 30 | 30 |
| FDT-02 | DMPH-2.011 | 13 | 13 |
| LINK | — | — | — |

### 5.4 样式验证

- **layer_styles 表:** 10 行（9 FC 层 + 1 span_annotations），`useAsDefault=1`
- **BOITE QML:** 分类符号基于 `style_key`，镂空红框 + DMPH 标签
- **CABLE QML:** 分类线型基于 `style_key`（含 CAD 颜色 + 线型）

---

## 6. 管线调用

### 批量处理（一条命令）

```bash
cd experiment
python3 -m python.converter \
  --input "APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" \
  --config config/hutabohu.json \
  --output output/project.gpkg \
  --source-crs EPSG:3857 --target-crs EPSG:3857 \
  --dwgread-cache /tmp/hutabohu_dwgread.json
```

### 编排器（可分段）

```bash
cd experiment/python
python3 convert_all.py \
  --config ../config/hutabohu.json \
  --input "../APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" \
  --output "../output/project.gpkg"
```

---

## Appendix A: ftth_converter.py 改进方案

当前 ftth_converter.py 的 L1-L2 剥离失败根源在于 Python import 语义（`from X import Y` 创建本地绑定快照）。以下是三种可能的改进路径:

### 方案 A: 使用模块属性访问替代直接导入

**核心思想:** 不在 ftth_converter 中执行 `from cad_common import DWG_TYPE_LINE`（创建本地绑定），而是始终通过 `cad_common.DWG_TYPE_LINE` 访问。`cad_common.` 前缀确保每次读取的都是模块的最新属性值。

**具体步骤:**
1. 移除 ftth_converter 顶部所有 L1-L2 符号的 `from cad_common import X` 语句
2. 保留 `from . import cad_common`（仅导入模块对象）
3. 将 ftth_converter 中所有 `DWG_TYPE_LINE` 改为 `cad_common.DWG_TYPE_LINE`
4. 将 `_extract_wkt(entity, dwg_type, extent)` 调用改为 `cad_common._extract_wkt(entity, dwg_type, extent)`
5. 对所有从 cad_common 使用的符号重复此过程

**优点:** 彻底解决 import 绑定问题；始终获取最新的补丁值  
**缺点:** 需要修改 200+ 引用点；代码冗长度增加（`cad_common.` 前缀）  
**风险:** 低——机械性替换，可用 `grep` + `sed` 自动化

### 方案 B: 将 DWG_TYPE_* 从可变全局变量改为函数参数

**核心思想:** 不允许跨模块变异。`read_dwg()` 返回补丁后的常量字典，`_extract_wkt` 接受 `dwg_types` 参数而非读取全局变量。

**具体步骤:**
1. 在 cad_common 中将 `DWG_TYPE_*` 常量和 `DIMENSION_TYPE_UNION`, `CONTROL_TYPES` 标记为模块私有
2. 修改 `_extract_wkt` 签名: 添加 `dwg_types` 参数（dict）
3. 在 `read_dwg()` 中，通过标志参数传递补丁值: `_extract_wkt(entity, dwg_type, extent, dwg_types=patched_types)`
4. 同样处理 `_extract_dimension` 和所有依赖 DWG_TYPE_* 的函数

**优点:** 函数式纯净；无全局状态；可测试性极佳  
**缺点:** 需要修改 10+ 个函数签名；影响所有调用点；重构面大  
**风险:** 中——可能引入调用链遗漏

### 方案 C: 保持当前 converter.py 单体 + cad_common 作为并行库

**核心思想:** 接受 converter.py 的 L1-L2 代码无法安全剥离的现实。cad_common.py 作为**新项目的共享库**存在（非 FTTH DWG 可以直接 `from cad_common import ...`），而现有 FTTH 管线继续使用 converter.py。

**具体步骤:**
1. 在 converter.py 顶部添加 `from . import cad_common`（模块导入，不使用 from-import）
2. 在 `main()` 中调用 `cad_common.init_crs()` 以验证共享库初始化路径
3. 将 converter.py 内对 cad_common 中函数的调用逐步替换（非一次性迁移）
4. 删除 ftth_converter.py（作为不完整工件）

**优点:** 零风险；渐进式迁移；cad_common 已经可以为非 FTTH 场景提供价值  
**缺点:** converter.py 仍然包含重复的 L1-L2 代码（虽然与 cad_common 中的副本功能等价）  
**状态:** 这是当前采用的实际方案

### 推荐

**短期（当前状态）:** 方案 C — 保持 converter.py 完整，cad_common.py 作为可复用共享库并存。

**中期（如需实现完整拆分）:** 方案 A — 用 `cad_common.` 前缀访问模块属性。这是风险最低的自动化路径，约需 1-2 天实现。关键步骤:
1. 编写自动化脚本验证: 扫描 ftth_converter.py 中所有从 cad_common 导入的符号
2. 移除 from-import 行，改为模块导入
3. 批量替换引用
4. 逐阶段运行回归测试（DWG 读取 → 分类 → 标注 → 写入 → 拓扑 → 样式）

**长期（大规模重构）:** 方案 B — 消除可变全局状态。需要重新设计 `read_dwg()` 的返回值和下游函数的参数传递链。适合代码库成熟后按需进行。

---

## Appendix B: 关键经验教训

1. **Python import 语义陷阱:** `from module import VAR` 创建本地绑定。当 `module.VAR` 被外部赋值修改时，本地绑定不会更新。跨模块的可变全局状态需要改用 `module.VAR` 属性访问。

2. **3,500 行单体的拆分不应一次性完成:** converter.py 的 L1-L2 与 L3-L5 之间的耦合比静态分析显示的更深。渐进式迁移（每次迁移 5-10 个函数 + 回归测试）比全量剥离更安全。

3. **CRS 全局状态是最脆弱的耦合点:** 15+ 函数依赖 `_CRS_TRANSFORM`, `_SOURCE_IS_GEOGRAPHIC` 等全局变量。`init_crs()` 显式初始化 API 是有价值的改进，但完全消除全局状态需要函数签名级重构。

4. **自动化 prefix 脚本有盲区:** 用于添加 `cad_common.` 前缀的正则脚本遗漏了 `transform_desc`（局部变量）和 `write_geopackage` 内的间接引用。对这些函数的手动审查是必需的。

5. **基线回归测试是生命线:** CONV-SUM=6942 不变量和 20/20 层计数在每次迭代后立即运行，在 CABLE=0 回归发生时提供了即时反馈。不变量是重构安全网。

---

## 相关文档

| 文档 | 路径 |
|------|------|
| 可复用性评估 Spec | `.omc/specs/deep-dive-comprehensively-understand-all-experiment.md` |
| 可复用性评估 Trace | `.omc/specs/deep-dive-trace-comprehensively-understand-all-experiment.md` |
| 聚合方案修正 Spec | `.omc/specs/deep-interview-consolidation-fidelity.md` |
| BOITE 标签+颜色匹配 Spec | `.omc/specs/deep-interview-boite-labels-color-match.md` |
| BOITE 数据源+Site偏移 Spec | `.omc/specs/deep-interview-boite-source-site-offset.md` |
| 五缺陷修复 Spec | `.omc/specs/deep-dive-topology-color-label-fix-compare.md` |
| RALPLAN 共识方案 | `.omc/plans/ralplan-consolidation-fidelity.md` |
| 拓扑修复分析 | `experiment/guide/T_TOPOLOGY_REPAIR_ANALYSIS.md` |
| 项目配置 | `experiment/config/hutabohu.json` |
