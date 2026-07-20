# main 分支知识转移综合分析（归档前吸收总结）

> **本文定位**：main 分支归档前的知识转移文档，供 newmodel 分支团队吸收 main 验证线上
> 可复用的领域知识与算法思路，为 newmodel 的鲁棒性提升铺路。
>
> **验证时点**：本文最后验证于 main 分支 HEAD `3e5be1a`（2026-07-20）。文中行号引用
> 以该时点为准；资产定位以**文件路径为主、行号为辅**。
>
> **阅读约定**：文中所有路径均为仓库相对路径。标注"（main 分支）"的资产需在 main
> 分支 checkout 中查看；newmodel 分支同名文件可能是不同实现。

## 目录

1. [项目灵魂与生产定位](#1-项目灵魂与生产定位)
2. [两路线六维对比](#2-两路线六维对比)
3. [可转移资产清单](#3-可转移资产清单)
4. [五缺陷教训 → newmodel 鲁棒性启示](#4-五缺陷教训--newmodel-鲁棒性启示)
5. [不转移项及理由](#5-不转移项及理由)
6. [基线对照与诚实边界](#6-基线对照与诚实边界)

---

## 1. 项目灵魂与生产定位

CAD2GIS 是以烽火通信 XA-202610「通信基建工程数智化设计与交付关键技术」比赛为
契机启动的**历史 CAD/DWG → GIS 高精度自动转换**工程。竞赛方案将「历史 CAD 图纸
向 GIS 平台自动转换准确率 ≥90%」列为「数据贯通」硬指标（赛题 2：多源异构工程数据
融合）。

2026-07-20 项目访谈确认的**真实定位**：

- **生产转化优先**。竞赛是展示与验证的契机，终极目标是接入烽火内部（基于 QGIS
  定制）的生产系统长期使用。
- 三条工作流中仅「历史 DWG → GIS」已落地；「AI 辅助设计生成 DWG」与「转化数据
  反哺 AI 训练」是愿景与下游收益，不构成当前交付义务。
- 因此工程价值观的排序是：**可维护性、数据安全、批量操作的门控与审计能力**
  高于演示效果；准确性（含诚实的精度边界声明）高于表面通过率。

这一定位是理解本文全部取舍的前提：main 路线在演示与业务可读性上走得很远，
newmodel 路线在工程化与诚实承诺上走得更远；生产转化需要的是后者为主体、
前者为养分。

---

## 2. 两路线六维对比

同一目标（历史 DWG → GIS ≥90%）下的两种工程载体。对比结论经由项目访谈确认。

| 维度 | main 路线 | newmodel 路线 |
| --- | --- | --- |
| **工程环境** | WSL2 Ubuntu 24.04；系统 Python 3.12；QGIS 3.44 LTR（apt） | Windows；conda Python 3.12（`env/environment.yml` 固定）；AutoCAD 2027 Core Console |
| **依赖** | 全 OSS：LibreDWG 源码自编译（SWIG+ctypes）、GDAL/ogr2ogr、pyproj、Shapely、QGIS。零许可成本 | 商业读取器（AutoCAD 2027）+ OSS GIS 栈（ezdxf/GDAL/PROJ/Shapely）；可选云 LLM 仅离线 curate，不进生产链 |
| **批量操作性** | `convert_all.py` 单命令四阶段；改项目 JSON 即可跑新图纸；无多图纸验证机制；无人工门 | 每图纸 inspect→bootstrap→**人工审查**→validate→convert；`verify <MATRIX.json>` 版本化多 CAD 矩阵；交付物字节级可复现，CI 按报告内容门控 |
| **可移植性** | Linux-only：硬编码 `/usr/local/lib/libredwg.so`；迁 Windows 需重写读取层 | 读取器 Windows-only；架构多端：canonical CLI + QGIS 插件薄适配器（`qgis_plugin/cad2gis_plugin/adapter.py`）+ webdemo 交付系统设计（`docs/superpowers/2026-07-19-webdemo-delivery-system-design.md`） |
| **应用软件前景** | 脚本集群（10 模块 ~12,000 行），converter.py 3,510 行单体；无包级测试；解耦重构一次回归后冻结 | pip 可安装包 `src/cad2gis`；entrypoint 治理测试；105+116 项测试通过；原子发布（同卷暂存+os.replace）；距产品化差「证据完备性」 |
| **数据安全** | 全离线、无网络代码，结构天然安全；但无签名/无绑定/验证报告曾未绑哈希 | `convert` 网络禁用；唯一云出口 `curate` 硬限制（timeout 60s/并发 4/输入 ≤256KB）；mapping registry Ed25519 签名+内容寻址；源/配置/产物全程 SHA-256 绑定；客户 DWG 不出本机 |

**一句话总结**：main 证明了「业务上能做到什么」（样式还原、拓扑聚合、业务可读
交付），newmodel 证明了「工程上敢承诺什么」（每对象守恒、源保真、诚实边界）。
生产转化以后者为主体，前者为养分——这正是本文第 3、4 节存在的意义。

### 2.1 工程环境

main 的引擎是零成本开源但能力残缺的 LibreDWG：R2018 中文 UTF-16 文本截断需
ctypes 绕过、HATCH 实体只能放置 sentinel、点数组被 SWIG 包装为单元素需直接
调 C API 取回。newmodel 的引擎是权威完备但绑定商业许可的 AutoCAD 2027：原生
读取 6,940 个模型实体约 17-23 秒，与 GPKG 的顶点对账最大差 0.0 m。环境的
选择实质是「许可成本」与「读取权威性」的交换，生产定位下权威性胜出。

### 2.2 依赖

main 全 OSS（libredwg/GDAL/pyproj/Shapely/QGIS），可自由分发，但 libredwg
硬编码编译路径，新型 DWG 兼容风险自担。newmodel 为「商业读取器 + OSS GIS
栈」混合，每个运行席位需 AutoCAD 许可；架构上保留了后端抽象
（`CAD2GIS_BACKEND_PATH`），理论上读取器可替换——但当前唯一生产实现是
AutoCAD。评估生产部署成本时，席位许可应计入 TCO。

### 2.3 批量操作性

main 是「研究员批量」：`convert_all.py` 一条命令四阶段，改项目 JSON 即可跑
新图纸，快、灵活、无门——快但靠自觉。newmodel 是「生产批量」：每份新 DWG
强制 inspect→bootstrap→人工审查→validate→convert，draft 配置
`conversion_allowed=false`，付出单图纸人工审查成本，换来 fail-closed、
版本化验证矩阵与字节级可复现产物（`gpkg_contents.last_change` 归一化 +
VACUUM），CI 可按报告内容门控而非退出码门控。

### 2.4 可移植性

main 是 Linux-only：LibreDWG 编译链、硬编码 .so 路径、QGIS apt 源都是 Linux
假设，迁 Windows 需重写读取层。newmodel 的读取器 Windows-only，但架构本身
多端：pip 可安装包、canonical CLI（含 entrypoint 治理测试）、QGIS 插件薄
适配器、webdemo 交付系统设计（2026-07-19 文档）。即「前端可移植、后端读取
不跨 OS」。

### 2.5 应用软件前景

main 距应用软件还差「工程化整个维度」：无打包、无包级测试、无治理，一次
解耦重构回归后冻结。newmodel 距应用软件只差「证据完备性」：pip 可安装、
105+116 项测试、原子发布、签名 registry；缺口是真实 GCP（绝对精度
not_verified）、跨 CAD 第二样本、安装引导体验。main 侧的 plugincad2gis
插件骨架（8 阶段 ezdxf 路线）与 newmodel 的包设计高度神似——newmodel
实质上就是该愿景的落地版。

### 2.6 数据安全

main 结构天然安全（全离线、无网络代码、OSS 引擎无厂商回传），但无正式
治理：配置无签名、产物无防篡改绑定、验证报告曾与产物哈希脱节。newmodel
是正式安全架构：`convert` 网络禁用，唯一云出口 `cad2gis curate` 有硬限制
（timeout 60s/并发 4/重试 3/输入 ≤256KB/输出 ≤4096 tokens），mapping
registry Ed25519 签名且内容寻址，源/配置/产物全程 SHA-256 绑定，客户 DWG
不出本机。代价是依赖 AutoCAD 商业软件本身（企业需另行评估其遥测策略）。

---

## 3. 可转移资产清单

以下资产均位于 **main 分支**，经项目访谈确认值得转移到 newmodel。转移形态分
三类：**直接用**（数据/规则文件可直接搬入并版本化）、**改造用**（思路可用，
实现需按 newmodel 的 fail-closed/源保真哲学重写）、**仅思路**（教训与模式，
不搬代码）。

### 3.1 领域知识

| # | 资产 | 来源路径（main 分支） | 转移形态 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | FTTH 领域词汇表加载与校验 | `experiment/py_scripts/domain_vocab.py`（约 352 行） | 改造用 | 动态 CSV 加载 + 词汇一致性校验的思路；newmodel 可将其纳入 mapping registry 审查项 |
| 2 | 图层名 → 要素类正则映射体系 | `experiment/py_scripts/schema_config.py` 的 `LAYER_PATTERN_MAP`（约 1870-1933 行） | 直接用 | 法标 FTTH 图层命名模式集合；覆盖 BOITE/CABLE/PTECH/ZNRO/ZPM/IMB/SITE/INFRASTRUCTURE 八类 |
| 3 | 标注文本家族正则 | `experiment/py_scripts/schema_config.py` 的 `LABEL_FAMILIES`（约 2614-2618 行） | 直接用 | 「xx.x m」跨度、设备编号、地址等标注族模式；与 newmodel 标签族机制互补 |
| 4 | 非主体图层负证据清单 | `experiment/py_scripts/schema_config.py` 的 `NEGATIVE_EVIDENCE_LAYERS`（约 1835-1861 行） | 直接用 | 图例/标题/汇总表等干扰图层名的排除经验，是 T 缺陷修复的直接产物 |
| 5 | 法标 FTTH 验证规则库 | `experiment/evaluation_standards/` 全目录（VERIFICATION_RULE.csv、BOITE.csv、CABLE.csv、PTECH.csv、SITE.csv、ZNRO.csv、ZPM.csv、IMB.csv、INFRASTRUCTURE.csv 等） | 直接用 | 业务字段级验证规则（必填/取值域/计数期望）；可接入 newmodel 验证矩阵的业务维度 |
| 6 | 拓扑修复技术复盘 | `experiment/guide/T_TOPOLOGY_REPAIR_ANALYSIS.md` | 仅思路 | 伪桥根因分析（图例碎片诱发）与「单一网络合法性」论证，是 newmodel 拓扑门控设计的对照实验 |
| 7 | 图例碎片自动检测 | `experiment/py_scripts/legend_detector.py`（约 524 行）+ `experiment/config/legend_exclusions.json` | 改造用 | 纯空间聚类算法，识别图例面板的 596 个碎片块；思路与 newmodel「legend 终态 disposition」一致，检测算法可借鉴 |

### 3.2 算法参考（参考思路，非替换实现）

| # | 算法 | 来源路径（main 分支） | 说明 |
| --- | --- | --- | --- |
| 8 | 匈牙利一对一标注分配 | `experiment/py_scripts/converter.py` 的 `_minimum_cost_assignment`（约 777-831 行） | 标注→几何的全局最优二分匹配（而非最近邻贪心），避免跨街区误关联；与 newmodel「同类候选二分图最大匹配」同族，可对照实现 |
| 9 | 三轨样式方案 | `experiment/py_scripts/style_builder.py`（约 473 行）+ converter 内 `_resolve_effective_color` | 实体色 ByLayer 解算（100% 覆盖）→ 每层 QML 旁车 → GeoPackage `layer_styles` 内嵌（useAsDefault=1）；newmodel 已内嵌样式，此资产的增量在 **ByLayer/ByBlock 颜色解算细节**与 **QML 生成模板** |
| 10 | 跨度注记持久化 | `experiment/py_scripts/converter.py` 的 `_write_span_annotations`（约 3183-3303 行） | 170 条「xx.x m」标注落库为 span_annotations 表并带真 FID 外键；与 newmodel `CABLE_SEGMENT.dimension_entity_key` 机制互为印证，可作为标注-段关联的审计字段设计参考 |
| 11 | 无头 pyqgis 子进程隔离 | `experiment/py_scripts/style_builder.py` 的 QGIS 调用方式 | pyqgis 无头模式在 Qt teardown 阶段 segfault；根治=样式生成放子进程、崩溃不影响主管线。newmodel 若引入 QGIS 进程内调用，此教训直接适用 |

**使用约束**：以上算法资产请以「参考思路」阅读——main 的实现服务于业务聚合
哲学，直接搬进 newmodel 可能引入与 fail-closed/源几何不可变原则冲突的默认
行为（如自动桥接、默认长度填补）。搬思路，审实现。

---

## 4. 五缺陷教训 → newmodel 鲁棒性启示

main 验证线在 2026-07-18 完成五缺陷修复（commit `43129f3`）。每个缺陷的根因
对 newmodel 都是一次免费的鲁棒性预警。

### T — 拓扑回退：图例碎片诱发伪桥

- **现象**：拓扑修复产生 429 条桥接边；排除图例面板碎片（596 个）后归零。
- **根因**：图例/标题区域的线段碎片被当作网络候选参与桥接；修复算法没有
  「这片几何是不是真网络」的先行判断。
- **对 newmodel 的启示**：newmodel 的终态 disposition（`legend/title/frame`
  不进要素层）已在制度上防住此缺陷。建议做一次**对照验证**：确认
  `FDT-ALL`、`FDT-01 TOPOLOGY`、`SPLICING FDT` 等布局与 LEGEND 面板的排除
  在当前 source profile 下是完备的——main 的 `NEGATIVE_EVIDENCE_LAYERS`
  （资产 #4）与 legend_exclusions（资产 #7）可作为核对清单。
- **附带结论**：main 侧 203 段 CABLE 经 `LINK CBL0005-S7` 连通为单一网络是
  **合法业务形态**，不是过度桥接。区分「伪桥」（图例污染）与「合法连通」
  （业务拓扑）的判据值得写入 newmodel 拓扑审查指南。

### S — 样式：三轨制与无头 segfault

- **现象**：样式丢失（实体颜色未还原）+ pyqgis 无头模式进程退出时崩溃。
- **根因**：①颜色定义在图层（ByLayer）而 QML 生成只看实体色；②Qt 对象
  销毁顺序在主进程内不可控。
- **修复**：ByLayer 颜色解算 100% 覆盖 + 每层 QML + `layer_styles` 内嵌；
  样式阶段放子进程。
- **对 newmodel 的启示**：newmodel 的 8/8 内嵌样式已覆盖交付侧；增量启示
  是「**区分存储误差与渲染误差**」的验收手段——main 用关闭几何简化的
  验收 QML 达成，与 newmodel history.md 建议架构第 3 条同源，可直接互认。

### P — 跨度注记：标签与几何的持久化关联

- **现象**：170 条跨度标注（「xx.x m」）只存在于图纸，未入库、未关联光缆段。
- **修复**：span_annotations 表 + 真 FID 外键（标注 → 要素）。
- **对 newmodel 的启示**：与 `CABLE_SEGMENT.dimension_entity_key`（130
  measured / 9 unmeasured）是同一问题的两种实现。newmodel 的
  `unmeasured_no_dimension` 显式标注比 main 的默认填补更诚实——**这一
  差异本身值得保留为设计对照案例**：同一源数据，「业务可读」与「证据
  诚实」两种交付策略的具体分歧点。

### X — 排除闭环：真值台账

- **现象**：SITE 计数在 2 与 3 之间漂移（编码归一问题 CBL0001）。
- **修复**：确立 SITE 真值=2 并闭环验证。
- **对 newmodel 的启示**：真值不靠记忆靠台账。newmodel 的 source-bound
  profile（绑定 SHA-256 的期望计数）已是台账制度；main 的教训是台账
  还要覆盖「**编码归一化前后的映射关系**」——同一实体的两种编码
  （如 CBL0001 的归一前后形态）若不入账，审计时会表现为计数漂移。

### 第五缺陷（样式/拓扑之外的整体性教训）

五缺陷的共同模式：**「能跑通」曾长期冒充「正确」**——空 ORIGINE/EXTREMITE
的拓扑曾报告 PASS；未绑哈希的验证报告曾与产物脱节。main 后期已修复验证器
（14 个 E 级如实 FAIL），而 newmodel 从设计上就是 fail-closed。这组对照是
两条路线哲学差异的最小案例，建议在团队内部作为评审文化材料保留。

---

## 5. 不转移项及理由

以下 main 资产经项目访谈确认**不转移**，随 main 归档一并封存：

| # | 不转移项 | 来源路径 | 理由 |
| --- | --- | --- | --- |
| 1 | LibreDWG ctypes 读取链（含 SWIG 桥、`_lwpoline_points`、`_entity_utf8_text`） | `experiment/py_scripts/converter.py`、`experiment/py_scripts/cad_common.py` | LibreDWG 存在难以克服的局限（R2018 UTF-16 文本截断、HATCH 仅 sentinel、点数组包装缺陷）；AutoCAD 官方引擎读取在完备性与权威性上全面占优 |
| 2 | 业务聚合拓扑（203 段 CABLE、桥接/吸附/片段聚合） | `experiment/py_scripts/topology_builder.py`（约 1,378 行） | 与 newmodel「源几何不可变、派生网络独立成层」原则直接冲突；其合法部分（单一网络连通性论证）已提炼为思路（资产 #6/#T） |
| 3 | converter.py 单体及 cad_common/ftth_converter 解耦残骸 | `experiment/py_scripts/converter.py`、`experiment/python/` 全目录 | 解耦重构存在未修复回归（from-import 绑定快照 vs 跨模块可变全局）；main 归档后无维护主体 |
| 4 | EPSG:3857 作为交付 CRS 的选择 | 各 converter 配置 | 演示便利（Tianditu 叠加）而非工程正确；newmodel 的 EPSG:9481（SRGI2013/UTM 51N）才是 APD 项目所在地的正确交付基准 |
| 5 | 重庆东溪坐标双 regime 变换参数 | `demo/converter.py`、`demo/converter_3857.py` | 特定图纸的配参（ΔX=+292,539 等），不可泛化；newmodel 的 GCP 工作流是此类问题的正规解法 |

**边界声明**：不转移 ≠ 否定。main 的这些实现在其约束（零许可成本、无
AutoCAD 环境、快速验证业务可行性）下是合理工程决策；它们不转移是因为
newmodel 的约束与哲学不同，而非因为它们「错了」。

---

## 6. 基线对照与诚实边界

### 6.1 同一 DWG、两种交付（APD Hutabohu）

| 维度 | main 交付（2026-07-18） | newmodel 交付（v3 快照） |
| --- | --- | --- |
| CABLE | 203 段（聚合，0 伪桥） | 6 条源线（145 顶点，对账 0.0 m 差）+ CABLE_SEGMENT 139 段 |
| BOITE | 45 | 43 |
| PTECH | 167 | 167 |
| SITE/FDT | 2（FDT 值 151/51/1） | 2 |
| IMB | —（并入注记体系） | 682 |
| 跨度长度 | span_annotations 170 条（「xx.x m」+FID 外键） | 130 measured（DIMENSION 原生）+ 9 unmeasured（显式标注，投影长回退） |
| 守恒校验 | CONV-SUM=6942（含权重账本） | 逐对象终态 disposition + 原生长度闭合（≤1.25e-8 m） |
| 交付 CRS | EPSG:3857 | EPSG:9481 |
| 验证口径 | evaluator 余 14 个 E 级 FAIL（法标业务字段 DWG 无源，backlog） | 13 unresolved 台账；GCP disabled → 绝对精度 not_verified |
| 测试 | 无包级测试 | 105 + 116 项测试通过 |

计数差异（如 BOITE 45 vs 43、CABLE 203 vs 6）不是一方错误，而是**交付语义
不同**：main 聚合业务网络，newmodel 保真源几何并规范化派生层。审计时请按
各自口径解读，不要横向比大小。

### 6.2 诚实边界（两路线共同承认）

- 当前只有 **APD Hutabohu 一份真实 DWG** 的项目级回归基线；任何「跨 CAD
  已通过」「支持任意供应商 DWG」的表述都不成立。
- **绝对地面精度未验证**：无 surveyed GCP；相对 OSM/影像的目视配准不能
  转述为测量级精度。
- main 的 14 个 E 级 FAIL 是**源数据完备性缺口**（法标业务字段在 DWG 中
  无源），不是转换器缺陷；newmodel 面对同一 DWG 时这些字段同样不可得，
  应以 `UNAVAILABLE` 字段溯源处理，不得填补。
- main 的全部成果基于本机 WSL2 环境快速迭代，未经多环境复现；newmodel
  继承其领域知识时应视为「待按 newmodel 门禁复核的候选规则」，而非
  已验证事实。

---

## 附：main 侧验证证据索引（备查）

- 五缺陷修复提交：`43129f3`（2026-07-18）；交付 gpkg SHA 前缀 `10a89d6e` + .qgz
- 解耦重构提交：`3e5be1a`（2026-07-19）；回归复盘见 `experiment/archives/consolidation-report-2026-07-19.md`
- 拓扑修复分析：`experiment/guide/T_TOPOLOGY_REPAIR_ANALYSIS.md`
- 验证报告（含 14 E 级明细）：`experiment/output/hutabohu_verification_report.json`

> 本文完。后续开发以 newmodel 分支为主场；main 分支在本文被消化确认后归档。
