# ROAD_MATCH_LOCATOR — 路网匹配自动定位

> 状态：spec / 路线图（尚未实现）
> 分支：`feature/road-match-locator`
> 依赖阅读：[ARCHITECTURE.md](ARCHITECTURE.md)、
> [REGISTRATION_AND_SCENE_ARCHITECTURE.md](REGISTRATION_AND_SCENE_ARCHITECTURE.md)、
> [ROBUSTNESS_VALIDATION.md](ROBUSTNESS_VALIDATION.md)

本文档定义 CAD2GIS 的「路网匹配自动定位」（road-match locator）功能：用转换产物的
网络几何与参考路网做**图案匹配**，自动解算相似变换（similarity / Helmert）候选参数，
把今天「人工点 ≥4 个 GCP」的流程变成「**机器提候选、人确认**」。

两条不可逾越的边界继承自主架构：

1. **精度哲学不变。** 无论自动匹配得分多高，OSM 导出的定位候选永远是
   `RELATIVE_OSM_REFERENCE_ONLY`。它产出的是**配准假设**，不是绝对精度证据。
   绝对精度声明仍然只属于测量级 / 权威控制点 + 独立检查点门禁。
2. **确定性边界不变。** 本功能全部由确定性代码实现，落点是 CLI / review server /
   MCP 中复用 `gcp_workflow` 与 `semantic_anchor` 的「typed candidate → 人工批准」
   框架。没有任何 AI/模型被允许直接产出坐标或变换参数。

---

## 1. 背景与问题定义

转换流水线在「保真」一侧已经相当完整：source-record fidelity、曲线/几何/拓扑/长度
四类独立验证、名义 CRS 转换、GCP 拟合与独立检查点门禁（见 ARCHITECTURE.md 的
Accuracy Claims）。当前的**最大缺口是绝对定位（absolute localization）**——把一张
使用本地工程坐标的 DWG 落到真实地面位置上。现有三种手段及各自局限：

| 现有手段 | 模块 | 做了什么 | 局限 |
|---|---|---|---|
| 文件名地名锚点 | `cad2gis_v3/osm_anchor.py` | 从 DWG 文件名抽取地名 → Nominatim 查询 → 以 bbox 中心做**粗平移锚点** | 只有平移（无旋转/缩放），精度取决于地名粒度（村/街道级，误差常达数百米至公里级）；地名歧义、文件命名不规范时直接失败 |
| 双窗格人工 GCP | `review_server.py` + `gcp_workflow.py` | 操作员在 CAD 窗格与地图窗格各点同名位置，≥4 训练点 + ≥3 独立检查点，服务端确定性拟合 | 完全依赖人工：每张图 10–30 分钟，要求操作员同时认识 CAD 图面与现实地理，乡村无地标区域很难选点 |
| 语义锚点框架 | `cad2gis_v3/semantic_anchor.py` | typed candidate + 模型决策 + 人工批准的 GCP binding 框架 | 只是框架（候选由外部供给），目前没有自动的候选生成器喂给它 |

三条路径的共同缺口：**没有任何机制从转换产物自身的几何中自动推断位置**。
而这正是 FTTH 图纸特有的可利用结构：

- 光缆沿路敷设——转换出的 `CABLE_SEGMENT` 网络与道路网络高度同构；
- ODP/建筑沿街排列——点状设施与道路的相对位置关系携带方位信息；
- 一张村级 FTTH 图通常覆盖数条道路交叉口——交叉口拓扑（度数序列、
  夹角模式）在数公里范围内近似唯一。

因此本功能要解决的问题可以精确表述为：

> 给定转换产物中的网络几何 **G_src**（本地工程坐标，尺度/旋转/平移均未知或仅有
> 名义声明），在一个以地名锚点为中心的搜索窗内，从参考路网 **G_ref**（OSM 等，
> EPSG:4326 → 目标投影 CRS）中找出最优相似变换 **T = (t, s, θ)**，使
> T(G_src) 与 G_ref 的图案匹配度最大；若最优解不显著优于次优解，则**弃权**
> 而不是硬猜。

## 2. 概念分野：本系统不做什么

「map matching」一词在工业界有两类完全不同的含义，必须在选型前先切开：

| | GPS 轨迹匹配（trajectory map-matching） | 网络对齐 / 融合（network alignment / conflation） |
|---|---|---|
| 输入 | 带时间序的噪声 GPS 点列 | 两套完整的矢量网络 |
| 已有条件 | **已知近似位置**（GPS 误差通常 <50 m），道路拓扑已知 | 位置、旋转、尺度可能全部未知 |
| 求解 | 每点在附近道路上找投影（HMM / 几何最近邻） | 全局变换 / 逐要素对应关系 |
| 代表库 | fmm、GraphHopper、Valhalla、mappymatch | Hootenanny、slide、本系统 |

**本系统属于后者，且是其中最困难的一种情形：变换参数未知。**

- fmm / GraphHopper / Valhalla / mappymatch 全部假设「轨迹已经在正确道路附近」，
  它们的搜索半径是米级；我们的初始误差是百米到公里级，直接套用等于把
  全局搜索问题误当局部精化问题。
- Hootenanny（conflation）假设两侧数据**已大致配准**，做的是要素级合并与属性
  转移；它不解算初始变换。
- 学术上与本问题同构的是 point-pattern / graph-pattern matching（Li & Briggs 的
  拓扑点模式匹配）与模板匹配。slide（paulmach/slide）把矢量栅格化后做相关
  扫描的思路，与我们「假设扫描加速」的需求最契合。

结论：**不采购任何现成库作为核心**，只借鉴算法结构；数据层直接走 OSM
Overpass / 离线 extract，不引入 osmnx（本原型环境未安装，且其网络模型面向
路径分析而非几何图案匹配）。

## 3. 多参照源策略

参考路网不是单一来源。按可信层级与用途分工：

| 参照源 | 角色 | 精度声明 | 适用场景 | 接入方式（规划） |
|---|---|---|---|---|
| **OSM**（Overpass / Geofabrik extract） | 默认主参照 | `RELATIVE_OSM_REFERENCE_ONLY`（永久，不可升级） | 所有基线与 MVP；城市覆盖好，印尼乡村覆盖参差 | Overpass API 按 bbox 拉取 `highway=*`；离线 pbf 切片缓存 |
| **Overture Maps**（transportation theme） | 第二参照 / 交叉验证 | 视同 OSM 级，标记 `RELATIVE_REFERENCE_ONLY` | OSM 覆盖空洞区的补充；schema 稳定、可离线 parquet | 按 bbox 读 GeoParquet |
| **印尼 BIG 官方 RBI 图**（Badan Informasi Geospasial） | 权威参照 | 可支撑更高精度声明（仍须独立检查点确认） | 正式交付、需要权威背书的场景 | WMS/WFS 或授权数据；P3 起接入 |
| **已验证的自有交付成果互锚** | 内部参照 | 继承被锚 run 的精度等级 | 相邻片区新图：用已通过检查点门禁的旧 run 的 `delivery.gpkg` 路网当参照 | 直接读既有 `delivery.gpkg` |
| **卫星影像底图**（Esri/Bing 等 WMTS） | 不用于自动匹配 | — | 仅进 review UI 地图窗格，供人工确认候选时对照影像 | review_server 底图切换 |

精度哲学（重复，因为它容易被后续实现者遗忘）：

> OSM/Overture 匹配出的变换即使残差再小、评分再高，published profile 的
> accuracy 类别**永远**是 `RELATIVE_OSM_REFERENCE_ONLY`。要声明绝对精度，
> 必须换用权威/测量控制点并通过独立检查点门禁——这与
> `gcp_workflow.py` 现行规则完全一致，本功能只是给这条规则增加一个
> 自动候选生成器，而不是放松它。

多参照源在评分层的用法：主参照（OSM）产出候选后，用第二参照（Overture 或
RBI）对 Top-K 候选做**独立重评分**；双源一致的候选在 review UI 中标记
`cross_source_consistent=true`，作为人工确认时的排序依据而非自动通过理由。

## 4. 七层架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│ L1 特征提取   delivery.gpkg/source.gpkg → 候选网络                    │
│   CABLE_SEGMENT/支撑设施几何 → 节点(交叉口/端点)+边(折线) 图 G_src     │
│   尺度先验: $INSUNITS/CGEOCS/DIMENSION 长度事实 → 名义尺度 s0        │
├─────────────────────────────────────────────────────────────────────┤
│ L2 参考数据   搜索窗内的参考路网                                       │
│   地名锚点(osm_anchor) 或操作员给定中心 → bbox(半径可配,默认 5 km)     │
│   Overpass/离线extract/Overture/RBI → 同一内部图模型 G_ref            │
│   投影到 run manifest 的目标 CRS；按 highway 等级加权                 │
├─────────────────────────────────────────────────────────────────────┤
│ L3 假设生成   候选变换种子 {T_i}                                       │
│   a) 拓扑种子: Li & Briggs 点模式匹配——G_src 交叉口度数/夹角指纹      │
│      对 G_ref 交叉口做候选节点配对 → 每对给出一个 (t,θ) 种子          │
│   b) 栅格相关: slide 式矢量化→栅格模板, 对 (t,θ) 粗网格做相关扫描,    │
│      取局部峰作为补充种子                                             │
│   尺度 s 默认取名义尺度 s0，仅在其 ±15% 内离散扫描                    │
├─────────────────────────────────────────────────────────────────────┤
│ L4 匹配评分   每个 T_i 的适配度                                        │
│   主评分: 缓冲带重叠 F1（T(G_src) 的边与 G_ref 边互为缓冲相交,        │
│   缓冲半径默认 25 m,按道路等级分档）                                   │
│   辅评分: 逐段 Fréchet/Hausdorff 形状距离的中位数                     │
│   综合分 = w1·F1 + w2·shape_score，权重固定并写入 manifest            │
├─────────────────────────────────────────────────────────────────────┤
│ L5 精化       Top-K(K≤5) 候选局部优化                                  │
│   RANSAC 采样边对应 → 相似变换最小二乘(Helmert) 精化                   │
│   输出每候选的 RMSE、inlier 率、有效匹配边数                           │
├─────────────────────────────────────────────────────────────────────┤
│ L6 门禁       歧义弃权与质量门槛（fail-closed）                        │
│   - 最优分 < τ_min → REJECT                                           │
│   - (最优分-次优分) < τ_margin → AMBIGUOUS，弃权并附 Top-K 供人工选    │
│   - inlier 率 < τ_inlier 或匹配边覆盖率 < τ_cover → REJECT            │
│   - 通过者写入 typed candidate，accuracy=RELATIVE_OSM_REFERENCE_ONLY  │
├─────────────────────────────────────────────────────────────────────┤
│ L7 产出       候选-批准闭环                                            │
│   → locate_candidates.json（source-bound,含全部证据与评分明细）        │
│   → review UI 假设对比视图 → 人工确认/改选/拒绝                        │
│   → 确认后转为 gcp_workflow 的 paired GCP（虚拟控制点对）              │
│   → 走既有 calibration/georef 确定性拟合 + 独立检查点门禁              │
└─────────────────────────────────────────────────────────────────────┘
```

关键性质：

- **L1–L6 全部确定性、可重放**，输入 hash 相同则输出逐字节相同（参考数据按
  extract 快照 hash 固定）。
- **L7 的人工批准不可省略**。自动链路最多走到「typed candidate」，与
  `semantic_anchor.py` 的 binding 纪律一致：候选只携带 ID 与证据引用，
  批准事件单独落盘。
- 弃权（ABSTAIN）是一等公民产出，不是失败。歧义场景下交出 Top-K 让人挑，
  正是「机器提候选、人确认」的设计本意。

## 5. 算法选型

| 层 | 选型 | 理由 | 明确不选 |
|---|---|---|---|
| 假设种子 | **Li & Briggs 拓扑点模式匹配**：以交叉口节点为点集，用节点的度、邻边夹角序列、边长比构成旋转/尺度不变指纹，先在指纹空间检索候选节点配对 | 交叉口指纹在村级范围内近似唯一；天然对 (t,θ,s) 未知鲁棒；计算量随交叉口数而非像素数增长 | 纯几何最近邻（初始误差太大）；全图子图同构（NP 难且对缺边敏感） |
| 粗扫描加速 | **slide 式栅格相关**：把 T(G_src) 与 G_ref 各自栅格化为二值/加权模板，对 (t,θ) 网格做相关，FFT 加速平移维 | 把 O(变换空间×边数) 降为 O(变换空间×栅格卷积)；对局部缺边/多出私家路有容忍度；paulmach/slide 已验证工程可行性 | 逐边 Hausdorff 全空间扫描（太慢） |
| 主评分 | **缓冲带重叠 F1**：ref 边建缓冲带为正类区，T(src) 边落入比例 = precision；反向 = recall；F1 为综合 | 对「网线大体重合但节点不精确对齐」宽容；同时惩罚漏匹配与错匹配；阈值语义直观 | IoU（对缓冲区外大错不敏感）；纯 RMSE（依赖先有点对应） |
| 形状辅评分 | **逐段 Fréchet / Hausdorff 距离**：对 F1 匹配上的边对，计算离散 Fréchet 与 Hausdorff，取中位数与 P90 | 区分「平行贴邻的另一条路」与「真同路」；给人工确认提供逐段证据 | 全局单一 Hausdorff（对离群段过度敏感） |
| 精化 | **RANSAC + 相似变换最小二乘（Helmert）**：从候选匹配的边对应中采样最小子集解 (t,s,θ)，按 inlier 最大化迭代，最后对 inlier 做加权最小二乘 | 与 `calibration.py` 的模型选择纪律一致（默认 shape-preserving，不引入 shear）；对 OSM 错边/漏边鲁棒 | 直接全量最小二乘（被错对应拖偏）；affine/TPS（违反 gated 模型升级纪律） |
| 歧义处理 | **弃权门禁**：score margin、inlier 率、覆盖率三闸门任一不过即 REJECT/AMBIGUOUS | 错定位的代价远高于弃权；人工从 Top-K 里挑一个的成本约 1 分钟 | argmax 硬选（对称街区/平行路纹场景必翻车） |

尺度处理：相似变换的 s 不是自由搜索的。优先取图纸名义尺度（`CGEOCS` 轴单位 +
`$INSUNITS` 证据 + DIMENSION 标注一致性），只在 ±15% 内离散扫描；超出该范围
说明图纸本身尺度不可信，直接弃权转人工——这与 REGISTRATION 文档中
「translation → similarity → gated affine」的保守升级路径同源。

## 6. GitHub 现有库盘点

调研结论：没有任何一个库能直接解决「变换参数未知的网络对齐」。各库可借鉴点与
不可用原因如下（star 数为调研时点快照，仅作量级参考）：

| 库 | Stars | 语言 | 它解什么问题 | 能借鉴什么 | 为何不能直接用 |
|---|---|---|---|---|---|
| [fmm](https://github.com/cyang-kth/fmm) | ~1043 | C++/Py | GPS 轨迹 HMM map-matching | 路网索引（R-tree/网格）与候选边检索的工程结构 | 假设轨迹已在道路附近（米级搜索半径）；不解算全局变换 |
| [GraphHopper](https://github.com/graphhopper/graphhopper) | ~6635 | Java | 路由引擎，含 map-matching 子模块 | OSM 数据解析与道路等级建模 | 匹配基于已知位置的轨迹；引入整套 Java 路由栈成本远超收益 |
| [Valhalla](https://github.com/valhalla/valhalla) | ~6075 | C++ | 路由引擎，含 Meili 轨迹匹配 | 瓦片化路网存储思路 | 同上；服务化部署对单图定位过重 |
| [mappymatch](https://github.com/NREL/mappymatch) | ~127 | Py | 学术向轨迹匹配（LCSS 等） | 纯 Python 的可读实现，匹配器接口抽象 | 同样是轨迹问题；依赖 osmnx 拉网 |
| [Hootenanny](https://github.com/ngageoint/hootenanny) | ~388 | Java/JS | 网络 conflation（要素合并） | conflation 评分中「几何+拓扑+属性」多证据融合的思想 | 假设两侧已配准；不解算初始变换；依赖链庞大 |
| [slide](https://github.com/paulmach/slide) | ~256 | Go | 矢量到栅格的模板匹配定位 | **核心借鉴对象**：把配准假设转成栅格相关 + 粗到细的 (t,θ) 扫描 | Go 实现且面向其自家数据模型；只借鉴算法，用 Python/numpy+FFT 重写 |
| [osmnx](https://github.com/bossie/osmnx) | ~5813 | Py | OSM 网络建模与分析 | Overpass 查询模式、`highway` 标签体系经验 | 本原型环境未安装；其图模型面向最短路分析而非几何图案匹配；如需其功能直接调 Overpass API 更轻 |

补充参考：Li, L. & Briggs, W. 的 topological point-pattern matching（以节点度
与邻接几何关系做不变量检索）为本系统 L3 拓扑种子的理论来源；其实现按论文
描述自行落地，不引入第三方代码。

## 7. 与现有代码的集成点

### 7.1 CLI

新增子命令（argparse，与现有 `inspect/bootstrap/convert/gcp/review` 同层）：

```powershell
cad2gis locate --project "<PROJECT_DIR>" --run-dir "<RUN_DIR>" `
  [--reference osm|overture|rbi|prior-run:<RUN_DIR>] `
  [--search-radius-km 5] [--json]
```

- 输入：run 的 `delivery.gpkg`（或 `source.gpkg` + profile），参考源选择，
  搜索窗（默认取 `osm_anchor` 的项目锚点为中心）。
- 输出：`<RUN_DIR>/locate/locate_candidates.json`，source-bound（绑定
  source SHA-256 + 参考 extract 快照 hash），含 Top-K 候选的
  (t,s,θ)、F1、形状分、RMSE、inlier 率、弃权原因、逐边匹配证据引用。
- 无任何候选通过门禁时退出码非零，但候选文件照常落盘（Top-K 供人工选）。

### 7.2 review_server 假设对比 UI

- 新增 `/locate/candidates` 端点读取 `locate_candidates.json`。
- 地图窗格增加「候选对比」模式：Top-K 候选以不同色相的半透明叠加渲染
  转换网络，操作员可切换候选、查看逐边匹配高亮（F1 命中/未命中边分色）、
  切换卫星影像底图对照。
- 操作员确认某候选（或手动改选）后，服务端把该候选的匹配边对**物化为
  paired GCP 集合**（虚拟控制点：取匹配边对的节点/特征点），写入既有
  revision store，走与手点 GCP 完全相同的 `web_gcp_profile.json` 发布路径
  ——accuracy 类别自动锁定为 `RELATIVE_OSM_REFERENCE_ONLY`。

### 7.3 MCP 工具

在 `agent_mcp.py` 暴露 `locate_run(run_dir, reference, ...)` 与
`list_locate_candidates(run_dir)`。纪律不变：MCP 工具只能触发确定性计算与
读取候选文件，**批准动作只存在于 review server 的人工交互路径**，agent 不可
代为批准。`get_capabilities` 中如实声明该工具的 `RELATIVE_OSM_REFERENCE_ONLY`
精度上限。

### 7.4 复用既有框架

| 既有模块 | 复用方式 |
|---|---|
| `calibration.py` / `georef.py` | 候选确认后的拟合与门禁完全走现有 translation→similarity→gated affine 管线，不另立数学路径；本功能自身 RANSAC 精化仅用于排序候选，不进发布链路 |
| `gcp_workflow.py` | 候选批准后物化为标准 paired GCP，沿用其 status/prepare/publish 与 manifest 记录格式 |
| `semantic_anchor.py` | `locate_candidates.json` 采用与 semantic anchor candidate 同构的 typed-candidate 纪律：content-addressed ID、facts digest、候选只携带 ID 与证据引用、批准事件携带同一 anchor identity |
| `osm_anchor.py` | 其项目锚点作为默认搜索窗中心；locate 成功后回写更精确的中心供后续 run 缩小搜索半径 |

新代码落点（规划）：`src/cad2gis/cad2gis_v3/locate/`（`features.py`、
`reference.py`、`hypotheses.py`、`scoring.py`、`refine.py`、`gates.py`、
`emit.py`），测试在 `tests/locate/`。遵循 ARCHITECTURE.md 边界：全部在
`src/cad2gis` 内实现，CLI/MCP/review 只做委托。

## 8. 分阶段路线与验收标准

基线资产：`baselines/` 下 4 张已有已知正确位置证据的图纸——
**hutabohu、kletek、lamteh_main、lamteh_sf**（测试策略见 §10）。

| 阶段 | 范围 | 量化验收标准 |
|---|---|---|
| **P1 单机 MVP** | L1+L2(OSM only)+L3b 栅格相关+L4(F1)+L5+L6+L7(仅 JSON 产出，人工用 QGIS 确认)。无拓扑种子、无 UI | **hutabohu 基线**：自动定位候选 Top-1 即正确解，独立检查点残差 **< 50 m**；管线端到端可重放（同输入两次运行候选文件逐字节一致） |
| **P2 全基线 + 弃权正确性** | 补 L3a Li&Briggs 拓扑种子；多参照源接口抽象（Overture 接入）；弃权门禁调参 | **4 个基线全部命中**（正确解出现在 Top-K 且 margin 通过时位于 Top-1）；**歧义注入测试**（人为旋转/镜像/平移制造双解场景）时系统**正确弃权**率 = 100%，无误报通过 |
| **P3 精度收紧 + 权威源** | RBI/互锚参照接入；形状辅评分参与排序；缓冲半径分档调优 | 4 基线独立检查点残差 **< 15 m**；双参照源交叉一致性标记上线；单图 locate 耗时（含 Overpass 拉取，缓存命中后）**< 3 min** |
| **P4 人工确认闭环** | review server 候选对比 UI + 批准物化 GCP + MCP 工具上线 | 从 `cad2gis locate` 到 review UI 确认、生成 `web_gcp_profile.json`、重跑 convert 通过独立检查点门禁的**完整闭环在 4 基线上各跑通一次**；批准后 profile 的 accuracy 类别自动为 `RELATIVE_OSM_REFERENCE_ONLY`（测试断言） |

每阶段退出前更新本文档状态行；P1 验收不过不得进入 P2（stage-gate）。

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| **OSM 乡村覆盖不足**（印尼村级道路缺失/未数字化） | F1 全面偏低，候选 REJECT | 多参照源（Overture/RBI/卫星影像人工兜底）；覆盖率预检——搜索窗内参考路网密度低于阈值时直接报 `REFERENCE_TOO_SPARSE` 转人工，不硬跑；互锚机制让已验证相邻片区当参照 |
| **图纸现势性**（图上的路还没建/已改道，与现势 OSM 不符） | 局部错配拉低 inlier 率，或匹配到「规划路」 | RANSAC 对局部错配天然鲁棒；形状分逐段证据在 UI 暴露给人工；inlier 率闸门拒绝整体强行通过 |
| **对称街区/平行路纹歧义**（规则网格街区存在多个等价变换） | argmax 会随机选中错误解 | 这正是弃权门禁存在的原因：margin 闸门 + AMBIGUOUS 状态 + Top-K 人工选；P2 验收专门注入此类场景断言 100% 弃权 |
| 参考数据漂移（Overpass 两次拉取内容不同） | 不可重放 | 参考 extract 落盘缓存并以快照 hash 绑定候选文件；重放必须命中缓存 |
| 名义尺度错误（图纸单位误判） | 正确位置在错误尺度下得分低 | s 只在 ±15% 扫描，超范围弃权转人工而非放大搜索窗；尺度证据冲突在 L1 即诊断 |
| 性能（搜索窗 5 km 内路网边数万级 × 变换空间） | locate 慢到不可用 | 栅格相关 FFT 加速平移维；拓扑种子先行剪枝；粗到细两级 (t,θ) 网格；P3 的 <3 min 为硬指标 |

## 10. 测试策略

核心原则：**用已知正确答案做端到端残差测试，而不是只测各层单元。**

4 个基线（hutabohu、kletek、lamteh_main、lamteh_sf）均已有人工确认过的正确
定位（既有 GCP profile / 检查点证据）。对每个基线：

1. **剥离真实变换**：取转换产物的本地坐标网络，移除/忽略其既有定位信息，
   作为 locate 的输入 G_src；
2. **加噪声扰动**（鲁棒性用例）：对 G_src 施加已知扰动——随机删边（模拟
   提取缺失）、加点抖动（σ=5 m）、整体旋转一个任意角度——模拟真实不利
   条件；
3. **重新定位**：跑完整 locate 管线（参考数据用固定快照，禁网）；
4. **比残差**：把机器解算的 T_machine 与基线真值 T_truth 分别作用于独立
   检查点集，比较逐点偏差，断言满足所属阶段的阈值（P1 <50 m，P3 <15 m）。

补充测试层：

- **弃权正确性**（P2 起）：构造镜像/旋转对称场景的合成网络与真实基线的
  人工双解变体，断言输出 `AMBIGUOUS` 且 Top-K 含正确解；构造空覆盖区断言
  `REFERENCE_TOO_SPARSE`；断言任何注入用例不产生「错误但通过」的假阳性
  （false-pass 数 = 0 是硬门槛）。
- **重放性**：同输入 + 同快照跑两次，`locate_candidates.json` 逐字节一致。
- **单元层**：指纹不变量（旋转/尺度/平移下指纹距离不变）、F1 评分对已知
  合成偏移的单调性、RANSAC 对 30% 错对应的恢复率、门禁边界值。
- **集成纪律**：全部测试走 `tests/locate/`，参考数据快照作为测试夹具入库
  （小 bbox，<2 MB），CI 禁网可跑。

---

## 附录 A. 术语

- **G_src**：从转换产物提取的待定位网络图（本地坐标）。
- **G_ref**：参考路网图（目标投影 CRS）。
- **T = (t, s, θ)**：相似变换（平移、统一尺度、旋转），即 Helmert/4-param。
- **弃权（ABSTAIN/AMBIGUOUS）**：门禁主动拒绝给出唯一答案，交出 Top-K 候选
  转人工。是本系统的正常产出，不是错误。
- **RELATIVE_OSM_REFERENCE_ONLY**：精度类别，表示「相对 OSM 视觉/几何一致，
  未由权威控制点独立验证」。本功能全部自动产出物永久携带该标记。
