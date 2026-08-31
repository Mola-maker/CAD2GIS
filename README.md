# CAD2GIS

[![CAD2GIS CI](https://github.com/Mola-maker/CAD2GIS/actions/workflows/ci.yml/badge.svg)](https://github.com/Mola-maker/CAD2GIS/actions/workflows/ci.yml)
[![Web Demo](https://github.com/Mola-maker/CAD2GIS/actions/workflows/pages.yml/badge.svg)](https://mola-maker.github.io/CAD2GIS/?demo=1)

CAD2GIS 是一个证据优先、可重放的 DWG → GIS 转换系统。CLI、Python API、
Web 审查界面和 `cad2gis-agent` MCP 服务共享 `src/cad2gis` 中的同一条
canonical 流水线，不为某张测试图维护硬编码分支。

> DWG 中声明了 CRS，不代表实体已经正确落在该 CRS 的真实地面位置。
> 源几何、拓扑、长度和坐标精度会分别验证。缺少可信控制点时，系统不会把
> OSM 视觉重合宣传为测量级绝对精度。

## 术语

- **APD = As Plan Drawing**：建筑工程领域的“按计划图纸”，记录的是
  **as-planned 设计**，不是 as-built 竣工实测；也不是“接入点设备”。
- **SF = Subfeeder**：光缆/电缆网络中的副馈线 / 分支配电线；文件名中的
  `- SF` 表示该图是网络的 subfeeder 部分。
- `raw/` 中 4 张 DWG 是建立算法流程的开发/基线集，后加入的 6 张是
  **验证集**，必须按新图各自 source-bound 处理，不得复用基线规则或数量门禁。

完整定义与两份清单见 [docs/GLOSSARY.md](docs/GLOSSARY.md)。

## 快速开始

1. 按下方“安装”创建固定运行环境并执行 `cad2gis doctor`；
2. 安装 [`plugins/cad2gis-agent`](plugins/cad2gis-agent)，或把对应
   [`clients`](plugins/cad2gis-agent/clients) 模板加入现有智能体；
3. 显式设置 `CAD2GIS_PROJECT_ROOTS`，然后让智能体先调用
   `get_capabilities` 和 `inspect_source`；
4. 转换后用 `cad2gis review` 打开 ToC 审查控制台，检查图层、标签、长度、
   拓扑和地图定位，再生成新的校准 run。

插件是 canonical Python 流水线的薄接入层。没有安装 GIS runtime、DWG reader
或没有授权项目根目录时，插件会失败关闭，而不会输出伪成功 GeoPackage。

下面的命令不需要先克隆仓库。先按操作系统安装同一个
`cad2gis-agent-mcp` 运行时，再按智能体客户端注册插件。

### 1. 安装本地运行时

Windows PowerShell：

```powershell
winget install --id=astral-sh.uv -e
uv tool install --python 3.12 "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git"
Get-Command cad2gis-agent-mcp
cad2gis doctor --deep --strict --json
```

macOS：

```bash
brew install uv
uv tool install --python 3.12 "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git"
command -v cad2gis-agent-mcp
cad2gis doctor --deep --strict --json
```

Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git"
command -v cad2gis-agent-mcp
cad2gis doctor --deep --strict --json
```

WSL 使用上面的 Linux 命令，但不把 WSL → Windows GUI 作为受支持的
AutoCAD/QGIS 会话控制通道。需要操控 Windows AutoCAD 或 Windows QGIS
桌面会话时，请在 Windows 原生 Codex/CLI 环境中安装运行时。

### 2. 连接智能体客户端

Codex（Windows/macOS/Linux 命令相同）：

```shell
codex plugin marketplace add Mola-maker/CAD2GIS --ref main
codex plugin add cad2gis-agent@cad2gis
codex plugin list
```

Claude Code（Windows/macOS/Linux 命令相同）：

```shell
claude plugin marketplace add Mola-maker/CAD2GIS
claude plugin install cad2gis-agent@cad2gis-tools
claude plugin list
```

Cursor：

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force .cursor | Out-Null
Invoke-WebRequest "https://raw.githubusercontent.com/Mola-maker/CAD2GIS/main/plugins/cad2gis-agent/clients/cursor.mcp.json" -OutFile ".cursor/mcp.json"
```

```bash
# macOS / Linux
mkdir -p .cursor
curl -fsSL "https://raw.githubusercontent.com/Mola-maker/CAD2GIS/main/plugins/cad2gis-agent/clients/cursor.mcp.json" -o .cursor/mcp.json
```

VS Code / GitHub Copilot agent mode：

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force .vscode | Out-Null
Invoke-WebRequest "https://raw.githubusercontent.com/Mola-maker/CAD2GIS/main/plugins/cad2gis-agent/clients/vscode.mcp.json" -OutFile ".vscode/mcp.json"
```

```bash
# macOS / Linux
mkdir -p .vscode
curl -fsSL "https://raw.githubusercontent.com/Mola-maker/CAD2GIS/main/plugins/cad2gis-agent/clients/vscode.mcp.json" -o .vscode/mcp.json
```

Cursor/VS Code 下载模板后，必须将 `<ABSOLUTE_PROJECT_ROOT>` 替换为 DWG
项目的绝对路径，然后重启客户端。完整模板见
[`plugins/cad2gis-agent/clients`](plugins/cad2gis-agent/clients)。

### 3. 更新或移除插件

```shell
# Codex
codex plugin marketplace upgrade cad2gis
codex plugin add cad2gis-agent@cad2gis
codex plugin remove cad2gis-agent@cad2gis

# Claude Code
claude plugin marketplace update cad2gis-tools
claude plugin update cad2gis-agent@cad2gis-tools
claude plugin uninstall cad2gis-agent@cad2gis-tools
```

安装、更新或修改 MCP 配置后，请重启客户端并新建任务，使 skills 和
tools 从新会话边界加载。

## 架构

```text
DWG reader (AutoCAD / LibreDWG / source-bound records)
  -> 不可变实体清单与样式、标签、曲线事实
  -> plan-domain 与 geometry-first 场景分区
  -> source-bound AI onboarding（只选择源中已观察到的标识）
  -> 确定性语义编译与精确 census gates
  -> 曲线、几何、拓扑、长度四类独立验证
  -> 名义 CRS 转换
  -> 可选 GCP 相似变换 + 独立检查点验证
  -> source.gpkg + evidence.gpkg + delivery.gpkg
  -> QML + evidence graph + run_manifest.json + run_status
```

AI 是控制平面的规划器，不是坐标或几何生成器。它可以完成实体清点、语义候选、
证据解释和 typed Decision Pack 提议；坐标、曲线、长度、变换参数、残差和交付
均由确定性代码计算和验证。

详细设计：

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [REGISTRATION_AND_SCENE_ARCHITECTURE.md](docs/REGISTRATION_AND_SCENE_ARCHITECTURE.md)
- [LLM_AGENT_ARCHITECTURE.md](docs/LLM_AGENT_ARCHITECTURE.md)
- [ROBUSTNESS_VALIDATION.md](docs/ROBUSTNESS_VALIDATION.md)

## 开发者安装

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
pip install -e ".[mcp,review,test]"
cad2gis doctor --deep --strict --json
```

支持 Python 3.11–3.12。MCP、证据协议和 GeoPackage 交付结构跨 Windows、Linux、
macOS 一致；AutoCAD/Core Console reader 仅限 Windows，其他系统需配置项目支持的
LibreDWG/ODA reader。安装后还可直接使用 `cad2gis-agent-mcp` 启动 MCP 服务。

Windows 使用 AutoCAD reader：

```powershell
$env:CAD2GIS_READER_BACKEND = "autocad"
```

## 新图纸的推荐流程

每个新 DWG 都必须建立自己的 source-bound profile 和 mapping registry：

```powershell
cad2gis inspect "<SOURCE.dwg>" --json
cad2gis bootstrap "<SOURCE.dwg>" --project "<PROJECT_DIR>" --json
cad2gis validate --project "<PROJECT_DIR>" --json
cad2gis convert "<SOURCE.dwg>" `
  --project "<PROJECT_DIR>" `
  --run-dir "<NEW_RUN_DIR>" `
  --json
```

使用 DeepSeek 自动完成受约束的 onboarding：

```powershell
$env:DEEPSEEK_API_KEY = "<secret>"
cad2gis auto-convert "<SOURCE.dwg>" `
  --project "<PROJECT_DIR>" `
  --run-dir "<NEW_RUN_DIR>" `
  --provider deepseek `
  --force-bootstrap `
  --json
```

密钥不会写入项目、日志或 manifest。New API 聚合网关可通过
`--provider new-api` 及对应环境变量接入。

## 长度语义

`CABLE_SEGMENT` 同时保留三种不同含义的长度：

- `source_native_length_m`：DWG 源曲线长度；
- `measurement_native_m`：匹配到的独立 DWG `DIMENSION` 数值；当 DWG 标注文本存在时使用**渲染后的标注值**（例如黄色整数 `50 m`），而不是 `act_measurement` 的未舍入小数；
- `source_cad_length_m`：CABLE_SEGMENT 的名义源长度——有 DIMENSION 时等于标注值，无 DIMENSION 时回退为不可变 CAD 曲线长度；
- `delivery_grid_length_m` / `geodesic_length_m`：坐标转换后的网格与椭球长度。

没有 `DIMENSION` 时，线段仍然具有可靠的 CAD 曲线长度。此时
`measurement_state=cad_geometry_only`，标签显示
`[CAD geometry; no DIMENSION]`，不再误导为“没有长度”。尺寸匹配采用：

1. 精确线段端点；
2. 尺寸端点与缆线端点落在同一对唯一支撑设施上。

第二种规则解决真实 CAD 中尺寸延伸线与缆线端点存在偏移的问题，并保留完整
rule/provenance。

## Web 配准、坐标传送与审查

```powershell
cad2gis review "<RUN_DIR>" --workspace "<REVIEW_DIR>" --port 8765
```

打开 `http://127.0.0.1:8765/`：

1. 左图点击 CAD 实体，系统吸附到最近真实几何，拒绝空白区控制点；
2. 右图点击对应位置，或输入 EPSG:4326 经度/纬度；
3. 服务端立即把经纬度转换到 run manifest 的目标投影 CRS；
4. 使用至少 4 个分布合理的训练点和 3 个独立检查点；
5. 生成 source-bound `web_gcp_profile.json` 和下一次转换命令。
6. 在“控制台”标签复制命令；执行后生成新的校准 GeoPackage，原 run 保持不变。

界面还提供 `?demo=1` 合成交互模式，用于公开展示图层开关、CAD 几何吸附和
坐标传送，不读取或上传真实 DWG，也不会伪装成已执行转换。例如：
`http://127.0.0.1:8765/?demo=1`。真实产物仍必须由本地 CLI/MCP 流水线生成。

公开的合成演示由 GitHub Pages 发布：
[ULTRA CAD2GIS Web Demo](https://mola-maker.github.io/CAD2GIS/?demo=1)。该页面只含
`demo-fixture.js` 中的合成对象；真实 CAD/GIS 处理仍在本地运行。

左侧 ToC 将源事实、语义映射、空间配准、独立校验和 GIS 交付分开显示；右侧
“证据 / 控制台 / AI 协作”标签只记录有效审查动作，并提供目标坐标、转换命令和
受约束 AI 提示词的复制功能。

Web 修改只写独立 revision store，不会修改源文件、evidence 或原 delivery
GeoPackage。OSM 控制点的精度类别固定为
`RELATIVE_OSM_REFERENCE_ONLY`；若要证明绝对精度，必须替换为测量或权威控制点。
复制或归档 run 后，服务只接受 SHA-256 与 manifest 完全一致的同目录产物；若
原 DWG 未重新附加，则允许审查但不生成一条必然失败的校准转换命令。

## MCP 与主流智能体

MCP 服务支持标准 `stdio` 和 Streamable HTTP：

```powershell
# stdio（通常由智能体自动启动）
cad2gis-agent-mcp --transport stdio

# 本机 Streamable HTTP
python -m cad2gis.agent_mcp `
  --transport streamable-http `
  --host 127.0.0.1 `
  --port 8768
```

HTTP endpoint 为 `http://127.0.0.1:8768/mcp`。默认只允许本机 loopback；
网络部署必须增加认证反向代理。

Claude Code、Cursor、VS Code/GitHub Copilot、Codex 和通用 HTTP 客户端模板位于
[`plugins/cad2gis-agent/clients`](plugins/cad2gis-agent/clients)。服务暴露
`get_capabilities`，智能体可先读取转换边界、transport 和精度声明，再调用
inspection、onboarding、conversion、evidence、repair 与 review 工具。
转换完成后调用 `audit_run`，可校验每个产物的 SHA-256、实际 GeoPackage 图层
计数与 manifest census 是否一致，并单独报告源 DWG 是否仍可重放。

MCP 只能访问下列根目录中的文件：

```powershell
$env:CAD2GIS_PROJECT_ROOTS = "E:\branch_CAD2GIS"
```

## QGIS 交付

成功 run 包含：

- `source.gpkg`
- `evidence.gpkg`
- `delivery.gpkg`
- QML 与 style manifest
- evidence graph 与视觉索引
- `run_manifest.json`

在 QGIS 中可直接拖入 `delivery.gpkg`，或通过“数据源管理器 → GeoPackage”连接。
加载随包生成的 QML 后可恢复 CAD 图层颜色、线型、点符号和标签。

## 精度边界

系统分别报告：

- reader/source record fidelity；
- geometry/curve fidelity；
- topology 与 cable segment conservation；
- CAD 曲线长度与独立 DIMENSION 覆盖；
- nominal CRS transformation；
- GCP train residual 与独立 check-point 精度。

`$INSUNITS` 是块插入缩放提示，不自动等同于 WCS 坐标单位。当 DWG 的
`CGEOCS` 明确绑定投影 CRS 时，其线性轴单位控制 WCS→米的尺度，避免把 UTM
坐标错误缩小 1000 倍。

外部 `E:\branch_CAD2GIS\APD_test` 仅是兼容性压力输入，不是训练集、规则模板或
准确率真值。同理，`raw/` 下新增的 6 张 APD（As Plan Drawing）验证图也必须
各自建立 source-bound profile，不得沿用四张开发基线图的规则或数量门禁。
没有 authoritative GCP 的结果必须保持 `CONDITIONAL` 或
`not independently verified`。

## 验证

```powershell
conda activate cad2gis
python -m pytest -q

$env:CAD2GIS_FULL_DWG_TESTS = "1"
python -m pytest tests/test_apd_test_compatibility.py -q
```

仓库 CI 在 Ubuntu/Python 3.11、Ubuntu/Python 3.12、Windows/Python 3.12
和 macOS/Python 3.12 上用 Micromamba 创建包含 GDAL/PROJ 的原生 GIS runtime，
再分别执行安装、锁版 Ruff、Python 编译、WebDemo JavaScript 语法、MCP/插件
契约和完整 pytest 回归。Pages CD 会重新验证浏览器契约，使用
`tools/build_webdemo.py` 构建严格限定的 `_site`，只发布合成 HTML/CSS/JS，拒绝
DWG、DXF、GeoPackage、QGIS 工程或审查数据库进入公开 artifact。

仓库目录：

- `src/cad2gis/`：唯一生产实现、CLI、reader、MCP、review server
- `tests/`：自动化契约与回归测试
- `baselines/`：source-bound 基线配置；大型 GeoPackage/清单证据可作为外部语料挂载
- `raw/`：开发基线 APD 4 张 + 验证集 APD 6 张
- `experiment/`：APD reviewed 兼容项目
- `plugins/cad2gis-agent/`：智能体插件和 MCP 客户端模板
- `docs/`：架构、鲁棒性、可移植性、术语与对账说明
- `env/`：固定 GIS 运行环境
