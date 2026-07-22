# Deep Dive Spec: CAD2GIS 项目灵魂与架构全面把握（含 main→newmodel 转移决策）

## Metadata
- Interview ID: dd-20260720-grasp-soul-arch
- Trace: deep-dive-trace-grasp-project-soul-architecture.md
- Rounds: 5 (+Round 0 拓扑确认)
- Final Ambiguity Score: 13%
- Type: brownfield / Threshold: 0.2 / Threshold Source: default / Status: PASSED
- Generated: 2026-07-20

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.92 | 0.35 | 0.322 |
| Constraint Clarity | 0.80 | 0.25 | 0.200 |
| Success Criteria | 0.85 | 0.25 | 0.213 |
| Context Clarity | 0.88 | 0.15 | 0.132 |
| **Total Clarity** | | | **0.867** |
| **Ambiguity** | | | **0.133** |

## Topology
| Component | Status | Description | Coverage |
|-----------|--------|-------------|----------|
| 业务灵魂确认 | active | 项目本质定位 | R2 确认：生产转化优先（竞赛为契机） |
| 技术架构确认 | active | 两路线对比与终局 | R1 产出六维对比；R3 确认终局：newmodel=生产线，main 归档 |
| 现状与下一步 | active | 转移范围与成果形态 | R4 确认转移范围；R5 确认交付三件套与 main_archive/ 位置 |

## Goal

全面把握 CAD2GIS 项目的灵魂与架构，并将理解固化为三件套交付物：

1. **本 spec**（`.omc/specs/deep-dive-grasp-project-soul-architecture.md`，main 仓库）——理解固化与决策链记录
2. **wiki 架构页更新**——修正 `.omc/wiki/cad2gis-converter-pipeline.md` 的过期描述（补全两路线格局与终局决策）
3. **综合分析转移文档**——在 **newmodel 分支新建 `main_archive/` 文件夹**存放，面向 Windows 侧组员，为 newmodel 鲁棒性提升铺路

### 已确认的项目灵魂（一句话）

CAD2GIS 是以烽火 XA-202610 竞赛为契机、以**生产转化为真实目标**的历史 CAD/DWG→GIS 高精度转换工程；其落地核心是确定性、证据优先、可审计的转换管线，≥90% 自动化准确率为竞赛硬指标，生产环境为烽火内部（定制）QGIS 体系。

### 已确认的架构终局（决策链）

1. **生产转化优先**（R2）——竞赛是契机，内部生产系统集成是目标
2. **newmodel = 生产线与后续开发主场**（R3）——main 的优点是本机快速迭代，但与 Windows 端/Web 隔绝
3. **main 归档，归档前完成知识转移**（R3）——载体=综合分析文档
4. **转移范围 = 领域知识 + 可移植算法；LibreDWG 读取链埋掉**（R4）——LibreDWG 有难以克服的局限；main 的拓扑聚合/业务聚合交付哲学不转移（违反 newmodel 源几何不可变原则）

## Constraints

- 转移文档必须能被 Windows 侧/newmodel 团队直接消费（中文 markdown，不依赖 main 的 WSL2 环境上下文）
- 转移的算法资产须以"参考实现/思路"形态表述，不得暗示直接替换 newmodel 的 fail-closed 机制
- LibreDWG 读取链（ctypes 桥、converter.py 单体、cad_common）不进入转移范围
- 转移文档置于 newmodel 分支**新建的 `main_archive/` 文件夹**（不污染 newmodel 既有目录结构）
- wiki 更新仅限修正过期描述，不重写全部 33 页

## Non-Goals

- 不在本次执行 ftth_converter 回归修复（main 归档使其失去意义）
- 不处理 14 个 E 级源数据缺口（属 newmodel 侧 GP/数据完备性议题，且 DWG 无源字段无法补）
- 不合并分支、不删除 main（仅"归档"决策记录，物理归档后续另行）
- 不评估 webdemo 交付系统实现（仅记录其存在）

## Acceptance Criteria

- [ ] 本 spec 存在于 `.omc/specs/deep-dive-grasp-project-soul-architecture.md`，含完整决策链与六维对比
- [ ] `.omc/wiki/cad2gis-converter-pipeline.md` 更新：反映两路线格局、newmodel 生产线地位、main 归档决策与转移范围
- [ ] newmodel 分支出现 `main_archive/` 文件夹，内含综合分析文档，覆盖：(a) 项目灵魂与生产定位；(b) 六维对比（工程环境/依赖/批量操作性/可移植性/应用软件前景/数据安全）；(c) main 的可转移资产清单（领域知识+算法参考，附来源文件路径）；(d) 五缺陷教训对 newmodel 鲁棒性的具体启示；(e) 明确不转移项及理由
- [ ] 转移文档不包含任何"继续维护 main 管线"的暗示

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| notepad"未提交"与 ralplan"pending approval"是当前状态 | Lane 3 git 核查 | 均过期：git clean；五缺陷修复=43129f3；重构=3e5be1a 已提交但回归回退方案 C |
| 三工作流并重（AI设计/历史转换/反哺训练） | Lane 1 孤证检验 | 仅"历史 CAD→GIS"落地；其余为愿景 |
| 项目是竞赛原型 | R2 直接提问 | **生产转化优先**，竞赛是契机 |
| 两路线会长期并存或合流 | R3 直接提问 | **newmodel 取代**：main 归档，开发移步 newmodel |
| main 资产都值得转移 | R4 Contrarian 挑战 | 部分成立：领域知识+算法转移；LibreDWG 链与聚合哲学埋掉 |
| ftth_converter 回归需要修复 | Lane 3 关键未知 | R3 后失去意义——main 整体归档 |

## Technical Context

### 两路线六维对比（R1 产出，用户确认方向）

| 维度 | main 路线 | newmodel 路线 |
|------|-----------|---------------|
| 工程环境 | WSL2 Ubuntu 24.04，系统 Py3.12，LibreDWG 源码自编译，QGIS 3.44 LTR | Windows，conda Py3.12（env.yml 固定），AutoCAD 2027 Core Console |
| 依赖 | 全 OSS（libredwg.so/GDAL/pyproj/Shapely/QGIS），零许可成本 | AutoCAD 2027 商业许可 + OSS GIS 栈；可选云 LLM 仅离线 curate |
| 批量操作性 | convert_all 单命令，改 JSON 即跑，无多图纸机制 | inspect→bootstrap→人工审查→validate→convert 门控；verify MATRIX 版本化矩阵；字节级可复现 |
| 可移植性 | Linux-only（硬编码 /usr/local/lib/libredwg.so） | 读取器 Windows-only；架构多端（CLI/QGIS插件/webdemo 设计） |
| 应用软件前景 | 脚本集群无包级测试；解耦重构回归回退 | pip 可安装包、canonical CLI+entrypoint 治理测试、105+116 测试、原子发布 |
| 数据安全 | 全离线天然安全，无正式治理 | convert 网络禁用、curate 硬限制、Ed25519 签名 registry、SHA-256 全程绑定 |

### 基线对照（同一 APD Hutabohu DWG，两种交付哲学）

- **main**：CABLE=203（聚合段，T 修复后 0 桥）、BOITE=45、FDT 151/51/1、CONV-SUM=6942、EPSG:3857、span_annotations 170 条
- **newmodel v3**：CABLE=6（源线不可变，145 顶点 0.0m 差）、CABLE_SEGMENT=139（130 measured + 9 unmeasured）、BOITE=43、PTECH=167、IMB=682、SITE=2、EPSG:9481、13 unresolved、GCP disabled（绝对精度 not_verified）

### main 可转移资产清单（R4 确认范围）

- **领域知识**：`experiment/py_scripts/domain_vocab.py`（领域词汇 CSV 加载）、`schema_config.py` LAYER_PATTERN_MAP/LABEL_FAMILIES/NEGATIVE_EVIDENCE_LAYERS 正则体系、`experiment/evaluation_standards/*.csv`（VERIFICATION_RULE 等 8 份法标规则）、`guide/T_TOPOLOGY_REPAIR_ANALYSIS.md`
- **算法参考**：匈牙利标注分配（`_minimum_cost_assignment`）、样式三轨方案（实体色 ByLayer 解算→QML→layer_styles 内嵌）、span_annotations 跨度注记（"xx.x m"+真 FID 外键）、图例面板排除法（legend_detector + legend_exclusions.json）
- **五缺陷教训**：T=图例碎片诱发伪桥（排除图例面板后 429→0 桥）；S=三轨样式与 pyqgis 无头 segfault 子进程隔离；P=跨度注记外键；X=SITE 真值编码闭环
- **埋掉项**：LibreDWG ctypes 读取链、converter.py 单体、业务聚合拓扑（桥接）、EPSG:3857 交付选择

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| main 路线 | core | WSL2/LibreDWG/converter.py/CABLE=203/CONV-SUM=6942 | 将被归档；资产转移至 newmodel |
| newmodel 路线 | core | Windows/AutoCAD2027/cad2gis包/v3后端/EPSG:9481 | 生产线与后续开发主场 |
| XA-202610 竞赛 | external | ≥90% 转换准确率硬指标/赛题2 数据贯通 | 项目契机，非终极目标 |
| 烽火内部生产系统 | external | 定制 QGIS 体系 | 生产转化的目标环境 |
| main 基线 | supporting | 五缺陷修复/43129f3/gpkg SHA 10a89d6e | 验证 main 业务能力的证据 |
| v3 基线 | supporting | BOITE=43/CABLE=6/CABLE_SEGMENT=139/105+116 测试 | newmodel 当前能力边界 |
| 六维对比框架 | supporting | 工程环境/依赖/批量/可移植/应用前景/数据安全 | R1 用户指定的阐释框架 |
| 综合分析文档 | core | 中文 markdown/main_archive/ | 知识转移载体，本 spec 的交付物之一 |
| 转移资产清单 | supporting | 领域知识+算法参考 | R4 确认的转移范围 |
| main_archive/ | supporting | newmodel 分支新建文件夹 | 转移文档的存放位置 |
| ftth_converter 回归 | supporting | CABLE=0/ZPM=0/from-import 绑定快照根因 | 因 main 归档而失去修复意义 |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 6 | 6 | - | - | N/A |
| 2 | 7 | 1 | 0 | 6 | 86% |
| 3 | 9 | 2 | 0 | 7 | 89% |
| 4 | 10 | 1 | 0 | 9 | 90% |
| 5 | 11 | 1 | 0 | 10 | 91% |

## Trace Findings

Trace 报告全文：`.omc/specs/deep-dive-trace-grasp-project-soul-architecture.md`。

- **最可能解释**（被访谈全部确认并强化）：项目=竞赛驱动的历史 CAD→GIS ≥90% 确定性验证管线；架构=experiment/ 主线 L1-L5 分层管线；当前处于后回退决策点
- **Lane 1 关键未知**（竞赛 vs 生产）→ R2 解决：生产转化优先
- **Lane 2 关键未知**（newmodel 合并计划）→ R3 解决：newmodel 即生产线，main 归档
- **Lane 3 关键未知**（ftth_converter 回归处置）→ R3 后消解：main 归档使修复失去意义
- **Round 0 用户注入**：newmodel 分支 `experiment/history.md`（4,699 行，组员思路全记录）与 README 纳入调查——成为 R1-R4 的关键证据源

## Interview Transcript
<details>
<summary>Full Q&A (5 rounds + Round 0)</summary>

### Round 0（拓扑确认）
**Q:** 三组件拓扑（业务灵魂/技术架构/现状与下一步）是否正确？
**A:** 确认，并补充：newmodel 分支更新了文件，experiment/history.md 值得从头到尾把握（组员的思路），分支 README 也纳入调查范围。

### Round 1（技术架构 / Context）
**Q:** 两条路线的终局意图是什么（取代/并存/合流/未定）？
**A:**（未直接选择）现总体阐释两条路线架构的不同之处：工程环境、依赖、批量操作性、可移植性、未来转化为应用软件的前景、数据安全。
**行动:** 产出六维对比阐释。**Ambiguity:** 100%→59%

### Round 2（业务灵魂 / Goal）
**Q:** 项目真实定位——竞赛答辩原型还是生产转化项目？
**A:** 生产转化优先。**Ambiguity:** 45%

### Round 3（技术架构 / Goal）
**Q:** 生产定位下，生产线归属是否有定论？
**A:** main 优点=本机快速迭代，但与 win 端/web 隔绝；做吸收总结的综合分析文档传到 newmodel 分支为其鲁棒性铺路；此后 main 归档，开发移步 newmodel。**Ambiguity:** 32%

### Round 4（现状与下一步 / Contrarian）
**Q:** 反向假设 main 大部分资产对 newmodel 有害——真正值得转移的核心资产是什么？
**A:** 领域知识+可移植算法都转移；LibreDWG 有难以克服的局限性，归档埋掉。**Ambiguity:** 26%

### Round 5（现状与下一步 / Criteria）
**Q:** 本次会话完成判据的形态？
**A:** 选项 3 形态（spec+wiki 更新+转移文档），传到 newmodel，新建 main_archive 文件夹中。**Ambiguity:** 13% ✅
</details>
