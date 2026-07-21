# CAD2GIS

CAD2GIS 是一个确定性、证据优先的 CAD/DWG 到 GIS 转换系统。公开入口只有安装包
提供的 `cad2gis` 命令和 `cad2gis.pipeline` API；DWG 读取、语义映射、曲线保真、
拓扑、坐标处理、GeoPackage 发布与验证都通过同一编排边界。

当前仓库只有 **APD Hutabohu** 一份经过项目级规则审查的真实 DWG 回归基线。
这可以支持该输入的回归和名义 CRS 转换检查，但不能证明系统已经跨多种 CAD 通过，
也不能证明地面绝对精度。APD 的 GCP profile 目前为 `enabled=false`、
`controls=[]`，因此绝对精度状态必须保持 `not_verified`。

## Robustness 工作区声明

本工作区是 **robustness 独立工作区**，与 `newmodel` 分支解耦。它继承了
`newmodel` 的有用文件，但不再 merge 回 `newmodel`；后续开发以此工作区为唯一
主场。工作区仅保留三类内容：

- **核心算法**：`src/cad2gis/` canonical 包（pipeline、reader、semantic、
  topology、calibration、verification、CLI）
- **架构知识**：`docs/` 设计决策、跨平台部署指南、对账口径说明
- **闭环比对验证**：`verify/` 契约测试、跨平台等价性测试、records bundle
  驱动的端到端对账测试

### 目录语义

| 目录 | 语义 |
|------|------|
| `src/` | 核心算法与 canonical 包 |
| `verify/` | 闭环比对验证（契约 / 跨平台 / 对账） |
| `docs/` | 架构知识与部署指南 |
| `baselines/` | APD 基线（delivery / evidence / records / config） |
| `tests/` | canonical 回归测试 |

### Reader 升格

Reader 角色已从 Windows-only canonical 升格为跨平台 primary：

- **LibreDWG**（`src/cad2gis/reader/libredwg.py`）是默认跨平台 reader
- **AutoCAD**（`src/cad2gis/reader/autocad.py`）保留为可选 Windows-only
  fallback，通过 `CAD2GIS_READER_BACKEND=autocad` 显式启用
- **Contracts**（`src/cad2gis/reader/contracts.py`）定义 reader 抽象接口

### 闭环比对（A 方案）

闭环比对不依赖原始 DWG，以 `baselines/apd_hutabohu/records/readcad_review_bundle.json`
为输入驱动 pipeline，输出到 `baselines/apd_hutabohu/output/`，并对账 baseline
GPKG。入口：`verify/replay.py`。

## 环境与安装

目标 GIS 环境是 `env/environment.yml` 固定的 Python 3.12、GDAL、PROJ、ezdxf 与
Shapely 栈；系统 Python 3.14 不是目标运行时。

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
pip install -e .
cad2gis doctor
```

`cad2gis doctor` 是不执行转换的轻量检查。部署或 CI 可进一步运行：

```powershell
cad2gis doctor --deep --strict
```

`--deep` 会导入原生 GIS 依赖和后端，`--strict` 会在当前部署不能转换时返回非零
状态。安装包本身不把 `experiment/` 后端塞入 wheel；支持的后端部署方式是：已安装
且可导入的 `cad2gis.cad2gis_v3`、指向包含 `cad2gis.cad2gis_v3` 及其同级 reader 模块目录的
`CAD2GIS_BACKEND_PATH`，或本仓库的 editable checkout。以 `doctor` 输出为准，
不要根据当前工作目录猜测后端是否可用。

## 新 CAD 的 canonical 工作流

每份不同内容的 CAD 都必须单独 onboarding。不得把 APD 的 source profile 或
mapping registry 复制给另一份 DWG；这些文件绑定源 SHA-256 和 inventory hash。

```powershell
cad2gis inspect "<SOURCE.dwg>" --json
cad2gis bootstrap "<SOURCE.dwg>" --project "<PROJECT_DIR>" --json
cad2gis validate --project "<PROJECT_DIR>" --json
cad2gis convert "<SOURCE.dwg>" --run-dir "<NEW_RUN_DIR>" --project "<PROJECT_DIR>" --json
```

各阶段含义：

1. `inspect` 只生成只读 source inventory；它不猜业务含义、绘图单位、源 CRS 或
   目标 CRS。
2. `bootstrap` 写入 `config/source_profile.json`、
   `config/mapping_registry.json`、`review/source_inventory.json` 和
   `review/unsupported_inventory.json`。这些文件初始状态是 `draft`，明确
   `conversion_allowed=false`。
3. 操作员必须用权威资料审查源哈希、reader 覆盖、曲线事实、单位和比例、源/目标
   CRS、语义与样式规则、拓扑/段落门限以及 unsupported allowlist，并补齐 reviewer、
   时间和 provenance。`validate` 只检查这些绑定和状态，不会替人批准草稿。
4. `convert` 只接受 reviewed 且适用于当前源的配置；`--run-dir` 应指向新的运行
   目录。草稿、歧义配置或失配源会在昂贵的 CAD ingest 之前被拒绝。

`validate` 成功生成报告不等于 `conversion_allowed=true`；自动化必须读取 JSON 的
状态字段。同理，`gcp status` 可正常报告 `blocked/not_verified`，`verify` 可正常
生成 `FAIL/WATCH` report；CI 应按报告内容 gate，而不是只看“命令成功执行”。

如配置没有放在 project pack 内，也可显式传入已审文件：

```powershell
cad2gis convert "<SOURCE.dwg>" --run-dir "<NEW_RUN_DIR>" `
  --source-profile "<SOURCE_PROFILE.json>" `
  --mapping-registry "<MAPPING_REGISTRY.json>" `
  --gcp-profile "<GCP_PROFILE.json>" --json
```

`--project` 与显式 profile 参数都属于同一个 canonical CLI；不应绕过它直接调用
`src/cad2gis` 中的实现模块。

## Fail-closed、unsupported 与 abstain

系统不会通过猜测把“不知道”变成“已通过”。以下情况会停止或阻止发布：

- 源文件、profile、registry、inventory 或 manifest 的 hash/binding 不一致；
- draft/unreviewed 配置，缺失或歧义配置；
- reader 记录格式损坏、未声明的兼容协议或读取能力缺失；
- 未经审查的毫米/英尺比例、未知/地理源 CRS，或缺少权威 local registration；
- 曲线原语、顶点顺序、原生长度、段落闭合或 topology contract 无法守恒；
- 语义/样式 unsupported 记录不在已审 allowlist，或 policy 为 `fail`；
- 启用 GCP 后缺少训练/独立 check 点、阈值、空间覆盖、provenance，或数值门失败。

`unsupported` 表示 reader、语义、样式或几何事实当前无法按合同解释；它必须保留
为结构化证据。`abstain` 表示系统有候选但没有足够证据选择；它不是错误的同义词，
也不是成功分类。只有明确的项目 policy 与逐项 allowlist 才能让某些记录以
`WATCH`/abstain 继续；不得静默丢弃、自动吸附、伪造端点或用默认长度填空。

## GCP 与绝对精度

GCP 是名义 CRS 转换之后、由操作员提供控制数据的独立工作流。`DIR` 应是包含
发布 manifest、delivery/evidence GeoPackage 及相关 GCP sidecar 的工作目录；产物
不共址时，使用各子命令 `--help` 中的显式路径参数。

```powershell
cad2gis gcp status --project "<DIR>" --json
cad2gis gcp prepare --project "<DIR>" --json
cad2gis gcp diagnose --project "<DIR>" --json
cad2gis gcp export --project "<DIR>" --json
```

- `prepare` 创建可在 QGIS 编辑的 control capture，不移动已发布几何。
- `diagnose` 只比较候选模型和残差，仍不授权发布。
- `export` 冻结人工审查后的 profile；导出本身不等于绝对精度通过。
- 必须用真实 surveyed/approved authoritative 训练点和独立 check 点重新
  `convert`，并由新 manifest 记录 accepted calibration 后，`status` 才可能为
  `verified`。相对 OSM/影像目视配准即使显式允许，也只能保持
  `not_verified`，不能转述为 survey-grade accuracy。

APD pack 当前没有 surveyed GCP，所有 APD 文档和演示都应把名义投影、PROJ
往返误差、图层叠加观感与绝对地面精度分开。

## 验证矩阵

跨 CAD 结论只能来自版本化矩阵，而不是从一份图纸外推：

```powershell
cad2gis verify "<MATRIX.json>" --json
```

验证器是只读的，并分别评估输入身份、reader/曲线、几何、拓扑、语义、样式、
长度、名义 CRS 与 GCP 独立检查。两条路径若 SHA-256 相同，仍只算一个输入；
inventory-only 样本不能产生精度结论；没有 surveyed GCP 的样本在绝对精度维度
必定失败/未验证。

当前证据边界如下：

| 样本/证据 | source-bound profile | 几何/拓扑/语义回归 | surveyed GCP + independent checks | 可作的最强表述 |
| --- | --- | --- | --- | --- |
| APD Hutabohu | 有，绑定单一真实 DWG hash | 仓库唯一真实 DWG 回归基线 | 无；profile disabled、controls 为空 | 单输入、名义 CRS 范围内的回归；绝对精度 `not_verified` |
| 其他仓库 CAD 文件 | 未逐一建立 reviewed pack | 未形成独立真实 CAD 验证行 | 未核实 | inventory only 或未评估 |
| 合成测试 fixtures | 只测试合同分支 | 可测试 fail-closed/curve/unit/CRS 等代码路径 | 不构成测量证据 | 不得计为第二份真实 CAD |

因此当前不得声称“跨 CAD 已通过”“支持任意供应商 DWG”或“达到某个绝对精度”。
矩阵格式和审计口径见 [验证报告](docs/verification_report.md)。

## 在 QGIS 中加载

转换成功后，在 QGIS 中选择“图层 → 添加图层 → 添加矢量图层”，打开
`<NEW_RUN_DIR>/apd_delivery.gpkg`（项目可采用其他前缀），选择需要的业务图层。
需要追溯来源、unsupported/abstain、拓扑或 GCP 时，再加载同目录的
`apd_evidence.gpkg`。GeoPackage 中的 `layer_styles` 可作为默认样式；若未自动
应用，可在图层属性中从 `<NEW_RUN_DIR>/qgis/styles/<LAYER>.qml` 手动加载。

`qgis_plugin/cad2gis_plugin/adapter.py` 是薄适配器：转换调用
`cad2gis.pipeline.convert_project`，加载已有 GeoPackage 使用 QGIS OGR provider；
它不复制另一套转换算法。QGIS 中“能显示”是交付检查之一，但不是 source geometry、
拓扑或绝对精度通过的替代证据。

## APD compatibility project pack 与旧入口

[`experiment/`](docs/ARCHITECTURE.md) 保存 APD Hutabohu 的 reviewed legacy-schema
profiles、architecture-v3 backend 和真实数据回归材料。它是 canonical package 的
**APD project-pack/后端兼容层**，不是第二套公共 CLI，也不是新 CAD 的模板。
APD 运行应从仓库根目录调用 `cad2gis convert ... --project experiment`。

`src/cad2gis/convert_v3.py` 仅保留为委托到 `cad2gis.cli` 的兼容包装。
`demo/` 与 `official/validation/` 下的旧 converter 默认禁用；只有显式设置
`CAD2GIS_ENABLE_LEGACY=1` 才能进入，并会显示弃用警告。该 opt-in 只用于复现旧
行为，不授权用旧结果作为 v3 交付或精度证明。

## 文档

- [APD project pack 快速说明](docs/ARCHITECTURE.md)
- [Architecture v3 详细设计](docs/ARCHITECTURE.md)
- [技术计划与边界](docs/technical_plan.md)
- [验证报告与多 CAD 矩阵口径](docs/verification_report.md)

`docs/APD_CAD2GIS_EXECUTION_PLAN.md` 与 `docs/APD_CAD2GIS_HANDOFF.md` 记录早期
单图纸工作，可能包含已经被 v3 替代的命令、CRS 或八层假设；新操作以本 README、
CLI `--help` 和 Architecture v3 为准。

## Reader 与闭环比对

Reader 角色已从 Windows-only canonical 升格为跨平台 primary。LibreDWG 是默认
reader；AutoCAD 保留为可选 Windows-only fallback，通过
`CAD2GIS_READER_BACKEND=autocad` 显式启用。

### 本机运行（WSL2）

```bash
# 安装 LibreDWG（系统 .so）
sudo apt install libredwg-dev    # 或本地 build；`dwgread -v` 应可执行

# 默认使用 LibreDWG reader
PYTHONPATH=src /tmp/cad2gis-venv/bin/python -c "
from cad2gis.ingest import ingest
from cad2gis.cad2gis_v3.config import SourceProfile
p = SourceProfile.load('baselines/apd_hutabohu/config/source_profile_libredwg.json')
entities, diag = ingest('baselines/apd_hutabohu/records/readcad_review_bundle.json', p)
print(diag['census'], diag['reader_protocol']['extraction_backend'])
"

# 跑 7 项契约测试
PYTHONPATH=src /tmp/cad2gis-venv/bin/python -m pytest verify/contract/ -v

# 跑跨平台等价性测试
PYTHONPATH=src /tmp/cad2gis-venv/bin/python -m pytest verify/portability/ -v

# 跑 A 方案闭环对账
PYTHONPATH=src /tmp/cad2gis-venv/bin/python verify/replay.py
```

### 对账口径

`verify/replay.py` 以 `baselines/apd_hutabohu/records/readcad_review_bundle.json`
为输入驱动 pipeline，输出到 `baselines/apd_hutabohu/output/`，并与 baseline
GPKG 做 SQL count 对账：

| 层 | 来源 | 期望（基线） |
| --- | --- | --- |
| delivery | `apd_delivery.gpkg` 表计数 | BOITE=43 / CABLE=6 / PTECH=167 / IMB=682 / SITE=2 |
| evidence | `apd_evidence.gpkg` 表计数 | cable_span_segments=139 / physical_span_evidence=170 / source_route_evidence=6 |

### 合并界面（隔离声明）

robustness 工作区特有的变更：

- `.gitignore`：增加 `.omc/state/` `.omc/sessions/` `.omc/project-memory.json`
  transient 排除
- `.omc/` 目录：`specs/plans/wiki/notepad.md` 已提交
- `src/cad2gis/reader/libredwg.py`：跨平台 primary reader
- `src/cad2gis/reader/autocad.py`：deprecated AutoCAD fallback（env opt-in）
- `src/cad2gis/reader/contracts.py`：reader 抽象接口
- `src/cad2gis/reader/records_adapter.py`：records bundle 适配层
- `verify/contract/test_libredwg_reader.py`：7 项契约测试
- `verify/portability/test_cross_platform.py`：跨平台等价性测试
- `verify/reconciliation/test_records_loop.py`：A 方案闭环对账测试
- `verify/replay.py`：闭环比对驱动

**canonical 边界**：`src/cad2gis/cad2gis_v3/ingest.py` 与
`src/cad2gis/reader/autocad.py` 保留 deprecation 与 env 守卫，不删除。

