# CAD2GIS architecture-v3 technical plan

状态：implementation-aligned，更新于 2026-07-19。本文定义公开边界、onboarding、
验证和发布计划；它不是精度证书。当前证据状态见
[verification_report.md](verification_report.md)，APD 细节见
[ARCHITECTURE_V3.md](../experiment/ARCHITECTURE_V3.md)。

## 1. 目标与非目标

目标是把不同来源的 CAD/DWG 转换成可在 QGIS 使用且可审计的 GeoPackage，同时
保留源事实、显式表达未知状态，并让同一已审输入得到可复现的语义结果。

架构必须保证：

- 一个 canonical CLI/API，不在 demo、official、experiment 或 QGIS 插件复制流程；
- 每份 CAD 先 inventory，再建立绑定该源 hash 的 project pack；
- reader、曲线、单位/CRS、语义、样式、拓扑、GCP 与发布分别留证；
- 所有关键歧义 fail closed 或成为可审计的 unsupported/abstain；
- GCP 缺失时只允许名义 CRS 表述，绝对精度为 `not_verified`；
- LLM 只能提供离线建议，不能进入生产几何、坐标、长度、拓扑或 GCP 数值链；
- 跨 CAD 能力只由多输入验证矩阵证明，不由 APD 或合成 fixture 外推。

非目标包括：自动猜源 CRS、按地图观感平移图层、为缺失测量填默认值、把所有交叉
当连接、把任意 CAD 文件存在仓库中当作已验证，以及声称当前已有 surveyed GCP。

## 2. Public architecture

```text
cad2gis CLI / cad2gis.pipeline / QGIS thin adapter
                    |
                    v
       project discovery + deployment contract
                    |
                    v
  inspect -> draft pack -> human review -> validate
                    |
                    v
 reader -> inventory -> semantics -> curve/topology -> nominal CRS
                    |                         |
                    |                         +-> optional reviewed GCP residual
                    v
 delivery.gpkg + evidence.gpkg + QML + run_manifest.json
                    |
                    +-> QGIS review
                    +-> read-only verification matrix
```

### 2.1 Canonical package

`src/cad2gis` 是轻量公共包：

| 模块 | 责任 |
| --- | --- |
| `cli.py` | 稳定命令、机器可读输出、无 `--debug` 时的简洁错误边界 |
| `doctor.py` | Python/依赖/AutoCAD/backend 部署 readiness |
| `pipeline.py` | source/config 解析和唯一转换 facade |
| `runtime.py` | installed backend、`CAD2GIS_BACKEND_PATH`、editable checkout 三种部署 |
| `gcp_workflow.py` | operator GCP sidecar 的公开适配器；status 不依赖 GDAL 拟合 |
| `verify/` | 只读、多 CAD、版本化验证矩阵与 claim policy |

wheel 不捆绑 `experiment/` 后端。部署必须提供可导入的 `cad2gis_v3`，或让
`CAD2GIS_BACKEND_PATH` 指向同时包含 `cad2gis_v3` 与同级 reader 模块的目录；
editable checkout 可以使用仓库内兼容后端。错误部署由 `doctor` 暴露，不扫描当前
目录猜测实现。

### 2.2 Backend and APD pack

`experiment/py_scripts/cad2gis_v3` 实现 architecture-v3 stages；
`experiment/config/apd_*` 是绑定 APD DWG hash 的 reviewed legacy-schema profiles。
这一目录是 APD compatibility project pack，不是公共入口，也不能复用于第二份
CAD。`experiment/py_scripts/convert_v3.py` 仅委托 `cad2gis.cli`。

QGIS adapter 只调用 `cad2gis.pipeline.convert_project` 并通过 OGR 加载已有
GeoPackage。它不得导入 backend stage、内置 mapping 或另写 warehouse。

## 3. CLI contract

目标环境由 `env/environment.yml` 固定为 Python 3.12：

```powershell
conda activate cad2gis
pip install -e .
cad2gis doctor
```

部署门：

```powershell
cad2gis doctor --deep --strict
```

Project lifecycle：

```powershell
cad2gis inspect "<SOURCE.dwg>" --json
cad2gis bootstrap "<SOURCE.dwg>" --project "<PROJECT_DIR>" --json
cad2gis validate --project "<PROJECT_DIR>" --json
cad2gis convert "<SOURCE.dwg>" --run-dir "<NEW_RUN_DIR>" --project "<PROJECT_DIR>" --json
```

`convert` 也支持显式 `--source-profile`、`--mapping-registry` 和可选
`--gcp-profile`。`--input` 与 positional project 参数只为旧调用兼容；新文档统一
使用 positional `SOURCE` 与 `--project`。所有详细选项以相应 `--help` 为准。

GCP lifecycle：

```powershell
cad2gis gcp status --project "<DIR>" --json
cad2gis gcp prepare --project "<DIR>" --json
cad2gis gcp diagnose --project "<DIR>" --json
cad2gis gcp export --project "<DIR>" --json
```

Matrix evaluation：

```powershell
cad2gis verify "<MATRIX.json>" --json
```

## 4. Different-CAD onboarding

### 4.1 Inspect

`inspect` 必须只读取和规范化源事实，输出至少包括 source SHA-256、reader protocol、
layout/entity/block/curve inventory、document metadata 与 unsupported reasons。它不
分配 GIS feature class，不从文件名猜单位或 CRS，也不写 project files。

### 4.2 Bootstrap

`bootstrap` 原子写入：

- `config/source_profile.json`；
- `config/mapping_registry.json`；
- `review/source_inventory.json`；
- `review/unsupported_inventory.json`。

所有生成配置为 `draft`，source profile 与 registry 同时绑定 source hash 和
inventory hash，默认 semantic/style coverage policy 为 `fail`。`--force` 只替换
这些 managed files，不能自动继承人工批准。

### 4.3 Human review

操作员必须至少审查：

1. source identity、reader backend/protocol 与 unsupported inventory；
2. 原生曲线 primitive、顶点/bulge/闭合/高程/normal/extrusion 与 native length；
3. `$INSUNITS`、坐标数值含义和 metre scale；毫米、英尺等需显式、reviewed scale；
4. source/target CRS；unknown/local coordinates 需 authoritative registration，不能
   直接假设 EPSG；
5. block/layer/annotation/field/style rules 及 provenance；
6. source geometry、topology、segment、delivery counts 和数值 tolerance；
7. unsupported allowlist 的逐项 reason/type 范围；
8. reviewer、reviewed_at 和外部依据。

`validate` 验证 hash、schema、review state、unit/CRS contract 与 conversion_allowed，
但永不把 draft 改为 reviewed。任何自动审批都会破坏 source-bound contract。

## 5. Loss-aware conversion gates

### 5.1 Reader

reader protocol 必须版本化并保留行号/字段级错误。malformed row 或未声明的兼容
策略失败关闭。AutoCAD CoreConsole 是 APD 的权威 reader 路径；语义不同的 COM
fallback 只有显式 `CAD2GIS_ALLOW_COM_FALLBACK=1` 才能使用，并必须记录 provenance。

### 5.2 Curve and geometry

源 geometry 永远不可由 topology、semantic 或 LLM 修改。直线和 bulge arc 的
source segment kind、native length 和曲线事实必须保留；delivery 可物化曲线用于
显示，但不能把 arc 静默换成 chord。未知 curve primitive、缺失 transform facts、
3D/normal/extrusion 冲突按 project policy fail 或 abstain，并进入 evidence。

### 5.3 Units and CRS

单位换算和坐标参考是两个合同。`INSUNITS` 只提供源证据，不自动证明坐标数值与
CRS axis 一致。直接 CRS 变换只在 reviewed unit scale 与 projected CRS contract
允许时执行；unknown/local drawing 必须先建立权威 registration。名义投影引擎一致、
往返误差和 EPSG operation accuracy 都不是地面绝对精度。

### 5.4 Semantics, styles and topology

unknown INSERT、unmatched route layer、unrecognized asset ID 或 unknown linetype 必须
产出结构化 coverage record。`policy=fail` 阻止发布；reviewed allowlist 可将特定记录
保留为 WATCH/abstain，但不能删除证据。

交叉不自动成为连接，support 不自动成为 optical node，最近对象不自动成为端口。
ambiguous candidate 保持 abstain。source route vertices、components、segment order、
原生/网格/测地/DIMENSION 长度分别守恒；缺少 DIMENSION 时值保持 null 或显式
unmeasured，不能填 15 m/23 m 等搜索半径。

## 6. Publication and QGIS

run directory 必须是新目录。发布 bundle 包括：

- delivery GeoPackage：业务层；APD contract 为 8 个基础层加
  `CABLE_SEGMENT` 业务明细层；
- evidence GeoPackage：source inventory、provenance、unsupported/abstain、拓扑、
  span、curve 与 GCP lineage；
- `qgis/styles/*.qml` 与 style manifest；
- `run_manifest.json`：source/config/implementation/toolchain 指纹、gate summary 与
  artifact hashes。

临时文件应在同卷写入、关闭、验证后原子发布。已有 `runs/` 目录只是 snapshot；
名称包含 `complete` 或 `validation` 不代表与当前代码 digest 一致。

QGIS 验收分两层：

1. 用 Data Source Manager/Vector 打开 delivery GeoPackage，检查 layer count、CRS、
   labels、colors、linetypes、rotation 和 embedded default styles；必要时加载 QML；
2. 加载 evidence GeoPackage，抽查 source IDs、curve/segment lineage、unsupported、
   topology 和 GCP status。

实际 QGIS 显示检查不能替代 source/hash/gate 验证；XML/SQLite 单元测试也不能替代
真实 QGIS 渲染验收。

## 7. GCP and absolute-accuracy boundary

GCP control 必须有 point ID、CAD 坐标、target CRS 坐标、train/check role、来源、
accuracy、weight 和 review state。启用 profile 至少要通过 source/CRS binding、
非重复控制、空间覆盖、训练集拟合、独立 check、物理合理性与模型复杂度门。
translation → similarity → affine 按最简单通过模型选择；非线性 rubber-sheet/TPS
不在当前生产合同中。

`prepare` 不移动几何，`diagnose` 不授权发布，`export` 不等于验证。只有使用真实
surveyed/approved authoritative controls 重新转换，且新 manifest 记录 accepted
calibration、独立 check 通过及 hash-matched profile 后，`gcp status` 才可能为
`verified`。OSM/imagery 目视点始终是 relative reference，不支持绝对精度声明。

APD 当前 profile disabled、controls 为空；技术计划不得把 surveyed GCP 当作已有
依赖或完成项。

## 8. LLM boundary

production conversion 不导入 provider/curation 模块。可选离线模型只能对预先生成、
内容绑定的 candidate ID 做 select/rank/abstain 建议，或帮助操作员整理审阅材料。
输出是不可信 proposal，需本地 schema/binding 验证和人工批准，再提升为新的版本化
配置并重新运行。

LLM 不得解析 binary DWG、创建/修改 vertex、geometry、length、CRS、GCP control、
weight、model parameter、inlier decision 或直接写 delivery。仓库也不把外部影像
landmark discovery、survey-sheet OCR 或残差解释描述成现有端到端能力。

## 9. Verification strategy

每条真实 CAD matrix row 必须包含 distinct input hash、vendor/version/units/CRS、
layout/block/curve inventory、独立 reviewed profile、gold/reference 来源，以及
geometry/topology/semantics/style/length/nominal CRS/GCP 的分维度状态。

Claim ladder：

1. inventory only：只证明看见输入；
2. single-input nominal CRS：一份真实、source-bound 输入通过适用维度，绝对精度可
   仍为未验证；
3. cross-CAD nominal fidelity：至少两份 distinct hashes、各自独立 reviewed packs
   和适用维度通过；
4. absolute accuracy：逐样本拥有 surveyed controls、独立 checks 和 accepted
   calibration，不能由前三级推导。

同一 DWG 的副本或不同路径只算一份；合成 fixture 用于合同分支，不算真实 CAD。
当前 APD 是唯一真实回归行，因此最多支持 single-input 范围内的表述。

## 10. Compatibility and completion gates

`demo/`、`official/validation/` 与旧 experiment converters 默认禁用，只有 exact
`CAD2GIS_ENABLE_LEGACY=1` opt-in 可复现，并必须显示弃用警告。旧路径不参与
canonical verification。

架构升级完成门：

- root docs 与全部 `cad2gis ... --help` 一致；
- package、experiment wrapper 与 QGIS adapter 只有一个 conversion facade；
- 新 CAD draft 在 reader 前被拒绝转换；
- reader/curve/unit/CRS/unsupported/abstain/GCP contracts 有自动测试；
- APD regression 由新 manifest 绑定当前实现，且明确无 surveyed GCP；
- 至少第二份 distinct、独立 reviewed real CAD matrix row 通过前，不发布跨 CAD
  成功声明；
- QGIS 实际加载证据与自动化 gate 一起归档，但不互相替代。
