# CAD2GIS experiment（APD Hutabohu compatibility pack）

本目录保存 APD Hutabohu 的 architecture-v3 后端、已审 legacy-schema 配置、真实
DWG 回归材料与历史运行快照。它现在是安装包 `cad2gis` 的 **APD project-pack /
后端兼容层**，不是另一套公共 CLI，也不是可直接套用到其他 CAD 的模板。

公开操作统一从仓库根目录调用 `cad2gis`。转换仍以
[`ARCHITECTURE_V3.md`](ARCHITECTURE_V3.md) 和 `config/` 中的版本化配置为准：
先保留 DWG 原生事实，再做确定性的语义、拓扑、坐标和 GeoPackage 发布。

## 范围与唯一输入

- 唯一权威输入 DWG：`APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`
- SHA-256：`557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557`
- 源声明 CRS：`EPSG:3857`（WGS84.PseudoMercator），`INSUNITS=6`（米）
- 交付 CRS：`EPSG:9481`（SRGI2013 / UTM zone 51N）
- 源配置：`config/apd_source_profile.json`
- 语义配置：`config/apd_mapping_registry.json`
- GCP 配置：`config/apd_gcp_profile.json`

源哈希、实体 census、路线/跨度分区、注记归属和 CRS 回归不符合已审配置时，
转换应停止。不要将目录中的旧 GPKG 当作另一个权威输入。

## 安装与 canonical APD 转换

使用 `env/environment.yml` 固定的 Python 3.12 Conda 环境。GDAL/OGR、pyproj、
ezdxf 等 GIS 依赖应在该环境中；系统 Python 3.14 不是目标运行时：

```powershell
conda activate cad2gis
Set-Location E:\branch_CAD2GIS\CAD2GIS
pip install -e .
cad2gis doctor
cad2gis validate --project experiment --json
```

APD 是旧版 reviewed compatibility pack，因此 `validate` 会返回
`reviewed_ready_legacy_compatibility`，并明确提示它没有新项目 onboarding 的
inventory sidecar；该兼容状态只对上面绑定的 APD SHA-256 有效。

用 APD pack 转换时，指定一个尚不存在的新 run directory：

```powershell
cad2gis convert `
  'experiment\APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg' `
  --run-dir '<NEW_APD_RUN_DIR>' `
  --project 'experiment' `
  --json
```

`experiment/py_scripts/convert_v3.py` 只保留为委托到 `cad2gis.cli` 的兼容包装；
canonical 文档和自动化不应调用它。旧的 `converter.py` 不属于 v3 APD 交付路径，
默认由 `CAD2GIS_ENABLE_LEGACY` guard 禁用。显式设置
`CAD2GIS_ENABLE_LEGACY=1` 只用于复现旧行为，不会把旧产物升级为 v3 交付。

`config/apd_source_profile.json` 与 `config/apd_mapping_registry.json` 绑定上面这份
DWG 的 SHA-256。另一份 CAD 必须从根目录依次运行 `cad2gis inspect`、
`cad2gis bootstrap`、人工审查与 `cad2gis validate`；不得复用 APD 规则。完整流程见
[根 README](../README.md)。

## 交付与证据文件

成功运行会在所选的新 run directory 中以同卷暂存后原子发布：

- `apd_delivery.gpkg`：面向业务的交付 GeoPackage；含 8 个基础合同图层和规范化的
  `CABLE_SEGMENT` 逐段业务层，共 9 个物理交付图层。
- `apd_evidence.gpkg`：独立的审计、来源、拓扑、span、未解决项和 lineage 证据。
- `qgis/styles/*.qml`：可移植的 QGIS 样式副本。
- `qgis/styles/style_manifest.json`：样式哈希和 `geometry_simplification=disabled_for_source_fidelity`。
- `run_manifest.json`：源/配置/实现指纹、CRS 操作、校验摘要和产物哈希。

APD reviewed regression contract 的 8 个基础业务层及规范化逐段层如下。计数是这份
特定 DWG 的 source-bound 期望，不是其他 CAD 的默认值，也不表示任意历史 run 与
当前代码指纹一致：

| 图层 | 当前计数 | 几何/用途 |
| --- | ---: | --- |
| `BOITE` | 43 | PBO 点 |
| `CABLE` | 6 | 不修改源顶点的光缆路线 |
| `PTECH` | 167 | 电杆/支撑点 |
| `INFRASTRUCTURE` | 0 | 合同占位层，可为空 |
| `SITE` | 2 | FDT/PM 站点 |
| `ZNRO` | 0 | 合同占位层，可为空 |
| `ZPM` | 0 | 合同占位层，可为空 |
| `IMB` | 682 | Homepass/建筑点 |
| `CABLE_SEGMENT` | 139 | 每条 CABLE 源 polyline 的规范化逐段 LineString |

`apd_evidence.gpkg` 中的 `cable_span_segments` 是 `CABLE_SEGMENT` 的审计对应层，
同样包含 139 条 EPSG:9481 逐段证据线；它不替代交付层，但可用于核对来源和审计。
40 条
`SLING WIRE` 支撑跨度留在证据域，不提升为 CABLE。

## 第 9 个业务层：`CABLE_SEGMENT`

`convert_v3.py` 将每条不可变父级 `CABLE` 的源顶点链规范化为一条逐段业务记录，
并在 `apd_delivery.gpkg` 中发布 `CABLE_SEGMENT`。每条记录与
`cable_span_metrics`/`cable_span_segments` 审计证据一一对应，几何为 EPSG:9481
LineString；源段顺序和长度闭合在发布前校验。

| 字段 | 类型/单位 | 约束与含义 |
| --- | --- | --- |
| `route_key` | text | 稳定的路线业务 ID |
| `source_entity_key` | text | DWG 源实体 ID |
| `source_handle` | text | AutoCAD 源 handle |
| `source_layer` | text | 源 DWG 图层 |
| `source_segment_key` | text | 源段证据 ID |
| `segment_index` | integer | 沿源 polyline 的 0-based 顺序；不可重排 |
| `source_native_length_m` | numeric, m | 源坐标段长度；不是 DIMENSION 值，不可用默认值替换 |
| `dimension_entity_key` | text/null | 匹配的 `SPAN CABLE` DIMENSION 源 ID |
| `measurement_native_m` | numeric/null, m | DIMENSION 原生测量；无 DIMENSION 时必须为 `NULL` |
| `measurement_delta_m` | numeric/null, m | `measurement_native_m - source_native_length_m`；未测量时为 `NULL` |
| `delivery_grid_length_m` | numeric, m | 直接转换到 EPSG:9481 后的平面段长 |
| `geodesic_length_m` | numeric, m | 对应段的地理测地长度 |
| `status` | enum | APD 当前为 `measured` 或 `unmeasured_no_dimension` |
| `length_value_m` | numeric, m | 显示/汇总长度，按下述 status 规则选择 |
| `length_label` | text | 人读标签，例如 `12.500 m` |
| `length_source` | enum | `dwg_dimension` 或 `delivery_grid_fallback_unmeasured` |
| `unit` | text | 固定为 `m` |
| `schema_version` | text | 逐段 schema 版本标识 |
| `parent_cable_code` | text/null | 父级 CABLE 的业务 CODE |
| `parent_display_label` | text/null | 父级 CABLE 的显示标签 |
| `parent_label_provenance` | text/null | 父级标签来源/规则 provenance |

交付层还保留通用的 `display_label`、`label_provenance`、`geometry_role`、样式
字段和 `lineage_json`，用于 QGIS 显示及回放；`LONGUEUR` 是该段
`delivery_grid_length_m` 的 EPSG:9481 几何长度。

长度选择是数据契约，不是估算策略：

- `status=measured`：`length_value_m = measurement_native_m`（DWG `DIMENSION` 的
  原生测量），且 `length_source = dwg_dimension`；必须有
  `dimension_entity_key`，`measurement_delta_m` 必须等于测量值减源段长度。
- `status=unmeasured_no_dimension`：`measurement_native_m` 和
  `measurement_delta_m` 均保持 `NULL`，
  `length_value_m = delivery_grid_length_m`，且
  `length_source = delivery_grid_fallback_unmeasured`；这是明确标注的非测量网格
  fallback，不能冒充 CAD 实测。
- 本图纸共有 **130 measured + 9 unmeasured = 139** 个光缆源段。生产实现不得
  用 15 m（或 23 m）填补缺失值；15 m/23 m 仅是 `fat`、`pole_new`、
  `pole_existing` 的注记搜索半径，绝不是跨度或光缆长度。

## CRS、GCP 与精度边界

生产链使用直接 `EPSG:3857 -> EPSG:9481` 坐标操作，不创建中间的
`EPSG:4326` 几何。`config/apd_gcp_profile.json` 当前 `enabled=false` 且
`controls=[]`，所以不会做残差校准；这只证明可复现的名义投影，不证明绝对地面
精度。`run_manifest.json` 应保留 `absolute_accuracy_validation` 为
“not independently verified; no surveyed GCP supplied”。只有人工审查、带独立
check 点且满足数值门槛的版本化 GCP profile 才能启用校准并重新运行。

## APD 覆盖、精度边界与可复现性

source profile v4 的 GCP 覆盖范围来自 plan-domain 的 model-space
`SourceEntity` 原生几何，包含未分类几何，不只统计已分类交付要素；它排除 paper
space、block definitions、style legend/title 等非绘图域材料，以及当前 reader 只
能提供放置 sentinel 的非 materialized `HATCH`。APD 当前覆盖范围为 **27,041 个
顶点**，原生范围约 **5,513.460 × 2,830.612 m**。这些是 GCP 空间覆盖门的绘图
范围，不是绝对地面精度；GCP 仍为 disabled/无 controls，因此绝对精度未验证。

拓扑 inventory 明确区分 `proper_interior_crossing`、
`shared_source_segment_endpoint`、`source_endpoint_on_segment`、
`collinear_overlap` 和 `collinear_endpoint_on_segment`。这些记录分别保留为
观察或候选，永远不会仅因相交而生成连接；route-group 与 source-segment graph
的 component 定义不一致时，校验失败并停止发布。

交付和证据 GeoPackage 在写入后会按确定性的主键顺序，将
`gpkg_contents.last_change` 与 `layer_styles.update_time` 规范化为固定 UTC
时间 `1970-01-01T00:00:00.000Z`，再 `VACUUM` 清除旧时钟值所在的 SQLite 页。
在相同 DWG/config/实现指纹和软件栈下，GPKG 与样式的哈希因此可作
byte-reproducibility 校验；这不改变要素、范围、CRS 或样式载荷。
`run_manifest.json` 会绑定这些哈希，但也有意记录显式输入/输出路径，因此不承诺
跨 checkout 或不同 run directory 的 manifest 文件本身逐字节相同。

## 在 QGIS 中检查

1. 在 QGIS 选择“图层 → 添加图层 → 添加矢量图层”，打开
   `<NEW_APD_RUN_DIR>/apd_delivery.gpkg`，选择所需业务层，
   包括 `CABLE_SEGMENT`。
2. GeoPackage 内注册的 `layer_styles` 会提供默认样式；`CABLE_SEGMENT` 使用
   `length_label` 标注，其余图层使用 `display_label`。若需手动复用样式，可在
   图层属性的“符号系统 → 样式 → 加载样式”选择
   `<NEW_APD_RUN_DIR>/qgis/styles/<LAYER>.qml`，其中包含
   `CABLE_SEGMENT.qml`。
3. 需要逐段审计时，再加载
   `<NEW_APD_RUN_DIR>/apd_evidence.gpkg` 中的
   `cable_span_segments`；它是交付 `CABLE_SEGMENT` 的证据 counterpart，
   其 `length_label`、`status` 和源 ID 可用于核对。

仓库的 `qgis_plugin/cad2gis_plugin/adapter.py` 只委托 canonical
`cad2gis.pipeline` 并通过 OGR 加载 GeoPackage；它没有复制转换逻辑。能在 QGIS
显示不等于 source geometry、拓扑或绝对精度已经通过。

## 测试

顶层 CLI/API 合同测试从仓库根目录运行；后端专项测试仍位于
`experiment/py_scripts`。本页只列现有测试入口，不把“测试文件存在”当作真实 CAD
或绝对精度证据：

```powershell
conda activate cad2gis
Set-Location E:\branch_CAD2GIS\CAD2GIS
python -m pytest tests -q
python -m pytest experiment/py_scripts -q
```

涉及逐段长度、转换和样式时可先运行针对性测试，再运行全套：

```powershell
python -m pytest `
  experiment/py_scripts/test_span_metrics_v3.py `
  experiment/py_scripts/test_cad2gis_v3.py `
  experiment/py_scripts/test_style_fidelity_v3.py -q
```

测试通过不等于绝对地面精度通过；应同时检查 run manifest 的 source/topology/
measurement/coordinate 摘要和 evidence GeoPackage。

## LLM 与生产边界

LLM 不进入生产几何/坐标链，也不是 `cad2gis convert` 的依赖。AutoCAD/DWG 读取、
拓扑、长度、CRS、GCP 计算和发布必须由本地确定性代码完成。可选的离线 curation
只能在已生成的、内容寻址的 CAD 事实之上做人工/模型的选择、排序或 abstain；模型
不得解析二进制 DWG、生成/修改坐标、几何、长度、`CABLE_SEGMENT` 的
`measurement_native_m`/`length_value_m`、CRS、GCP 或直接改写交付物；尤其不得
由 LLM authored length 或 geometry 进入生产层。
任何建议都必须经过本地严格校验和人工审查，再进入新的版本化配置并重新运行。

## 已知未解决项

现有 APD regression snapshot 的 manifest 记录 **13 个 unresolved**。这些记录是
证据中的已知待审
项，不是失败后可自动填补的缺失值；交付不会为模糊的设备端点或跨越关系伪造
`ORIGINE`/`EXTREMITE`，也不会为未测量跨度伪造 DIMENSION。需要追踪时查看
`apd_evidence.gpkg` 的 unresolved/topology/membership 相关表及 `run_manifest.json`。

该数字是 APD snapshot 的事实，不是跨 CAD 质量指标。当前只有这一份真实 DWG
回归基线；APD GCP 仍禁用且无 controls，绝对精度为 `not_verified`。跨 CAD 的
验证声明必须使用根目录所述的 `cad2gis verify <MATRIX.json>`，不能把合成 fixture
或同一 DWG 的副本算成第二份真实输入。
