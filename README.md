# CAD2GIS · CAD → GIS for AI agents

[![CI](https://github.com/Mola-maker/CAD2GIS/actions/workflows/ci.yml/badge.svg)](https://github.com/Mola-maker/CAD2GIS/actions/workflows/ci.yml)
[![License Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE.md)
[![Python 3.11–3.12](https://img.shields.io/badge/Python-3.11–3.12-3776AB)](pyproject.toml)

**把 DWG/DXF 转成可复核的 GIS，并让 AI Agent 检查、编辑 CAD 副本和交付 QGIS 项目。**

[官方网站](https://cad2gis.molamaker.cn/) · [九图效果展示](https://mola-maker.github.io/CAD2GIS/deliveries/) · [安装指南](https://mola-maker.github.io/CAD2GIS/install.html) · [发行版本](https://github.com/Mola-maker/CAD2GIS/releases) · [English](README.en.md)

CAD2GIS 将源实体、语义映射、几何、拓扑、长度与坐标证据串成可重放流程。
CLI、Python API、审查工作台和 `cad2gis-agent` MCP 插件使用同一条转换流水线。
适合 CAD/GIS 工程师，以及使用 Codex、Claude Code、Cursor、VS Code 的 Agent 工作流。

## 0.8.0 能做什么

| 能力 | 入口与条件 |
| --- | --- |
| DWG/DXF 来源检查 | DWG 使用托管 LibreDWG；DXF 在 CLI/API/MCP 中原生读取，保留实体 handle、块、属性和源哈希 |
| CAD → GeoPackage | 先复核语义、单位和 CRS，再转换；输出几何、拓扑、长度及位置验收证据 |
| CAD 副本编辑 | 可选独立开源 CAD 运行时；4 个 MCP 入口受控调用 15 个已实测 CAD 工具 |
| OSM 与 GCP 配准 | OSM 相对匹配、冻结快照、人工控制点与二次匹配；绝对精度需要可靠控制与检查点 |
| QGIS 交付 | GeoPackage、QGZ/QML、SVG 对照及校验清单；独立渲染验证需要 QGIS runtime |
| 多 Agent 接入 | Codex/Claude Code 仓库 marketplace，以及 Cursor/VS Code 标准 MCP 配置 |

旁路固定使用 [U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP) 的 MIT 许可
无界面 DXF 引擎，独立安装。它只编辑副本，结果重新进入 CAD2GIS 验收。
详见 [CAD 能力与安装](docs/CAD_BUNDLES.md)。

## 先看效果

[![原图与 GIS 交付叠加效果](https://mola-maker.github.io/CAD2GIS/deliveries/drawing-01/process/source-delivery-overlay.png)](https://mola-maker.github.io/CAD2GIS/deliveries/)

[查看九张图的效果、图层与过程](https://mola-maker.github.io/CAD2GIS/deliveries/)。
展示复用既有 Pages 内容；九图仍为 `CONDITIONAL`，没有验证测量级绝对定位精度。
图纸声明 CRS 或视觉重合，均不能单独证明位置准确。

## 三步接入 Agent

先安装 [uv](https://docs.astral.sh/uv/)，使用 Python 3.12：

```shell
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis-agent-mcp --help
cad2gis doctor --deep --profile full --json
```

DWG 转换另运行 `cad2gis runtime install`。DXF 检查不需要 LibreDWG；GIS 转换仍需要 GIS runtime。

Codex：

```shell
codex plugin marketplace add Mola-maker/CAD2GIS --ref main
codex plugin add cad2gis-agent@cad2gis
```

Claude Code：

```shell
claude plugin marketplace add Mola-maker/CAD2GIS
claude plugin install cad2gis-agent@cad2gis-tools
```

其他客户端使用 [MCP 配置模板](plugins/cad2gis-agent/clients/README.md)。
让 Agent 从 `get_capabilities`、`get_runtime_status`、`inspect_source` 开始。
可选 CAD 编辑另调用 `get_cad_bundle_status`，按 [旁路参考](docs/CAD_BUNDLES.md) 安装。

## 下载与版本

0.8.0 是本次 Python/Agent 源码发行；桌面安装包按其实际版本提供。

| 平台 | 已有可下载版本 |
| --- | --- |
| Windows x64 | [0.7.8 安装程序](https://github.com/Mola-maker/CAD2GIS/releases/download/v0.7.8/CAD2GIS-0.7.8-windows-x64-setup.exe) |
| Linux x86_64 | [0.7.0-3 `.deb`](https://github.com/Mola-maker/CAD2GIS/releases/download/v0.7.0-linux.3/cad2gis_0.7.0-3_amd64.deb) · [便携包](https://github.com/Mola-maker/CAD2GIS/releases/download/v0.7.0-linux.3/cad2gis-0.7.0-3-linux-x86_64.tar.gz) |
| macOS Apple Silicon / Intel | [0.7.0 安装包](https://github.com/Mola-maker/CAD2GIS/releases/tag/v0.7.0) |

Linux 安装要求见 [安装说明](docs/INSTALL_LINUX.md)。预发布请使用明确版本链接。
旧安装包保持其原始许可与验证范围；Apache-2.0 适用于本次新版原创代码。

## 文档与参与

[插件指南](plugins/cad2gis-agent/README.md) · [贡献指南](CONTRIBUTING.md) ·
[问题反馈](https://github.com/Mola-maker/CAD2GIS/issues) · [发现与分发渠道](docs/DISCOVERY_CHANNELS.md) ·
[OSM 匹配](docs/OSM_STANDARD_MATCHING.md) · [SVG 与 QGZ](docs/OPTIONAL_SYMBOL_ASSETS.md) ·
[多分区复核](docs/MULTI_DISTRICT_VERIFICATION_2026-09-10.md) · [Docker](docs/DOCKER.md)

原创代码采用 [Apache-2.0](LICENSE.md)，允许商用、修改和分发。
第三方组件与客户图纸保留各自权利，见 [NOTICE](NOTICE.md)。
欢迎用合成或可公开分享的图纸提交复现与新工作流；不要在公开 issue 上传客户原图。

---

## 术语

- **APD = As Plan Drawing**：建筑工程领域的“按计划图纸”，记录的是
  **as-planned 设计**，不是 as-built 竣工实测；也不是“接入点设备”。
- **SF = Subfeeder**：光缆/电缆网络中的副馈线 / 分支配电线；文件名中的
  `- SF` 表示该图是网络的 subfeeder 部分。
- 外部语料中 4 张 DWG 是建立算法流程的开发/基线集，后加入的 6 张是
  **验证集**，必须按新图各自 source-bound 处理，不得复用基线规则或数量门禁。

完整定义与两份清单见 [docs/GLOSSARY.md](docs/GLOSSARY.md)。

## 快速开始

容器部署与本地镜像构建见 [Docker 运行说明](docs/DOCKER.md)。

1. 按下方“安装”创建固定运行环境并执行 `cad2gis doctor`；
2. 安装 [`plugins/cad2gis-agent`](plugins/cad2gis-agent)，或把对应
   [`clients`](plugins/cad2gis-agent/clients) 模板加入现有智能体；
3. 在 DWG 项目目录中启动智能体，让它先调用 `get_capabilities`、
   `get_runtime_status` 和 `inspect_source`；
4. 默认转换执行 OSM 相对匹配和 SVG 图例候选提取；用 `cad2gis review` 打开结果，
   检查图层、标签、长度、拓扑和位置，并复核 SVG 与原图实体的对应关系；
5. 位置需要修正时，添加 CAD → 地图控制点查看人工校正预览，再点击
   “二次匹配并生成新结果”。完成后工作台自动加载新 GIS，保留控制点供继续迭代。

已有通过验证的项目配置时，可直接运行以下命令。尖括号中的内容是需要替换的
本地路径；每次转换使用新的输出目录。

```shell
cad2gis convert "<SOURCE.dwg>" --project "<PROJECT_DIR>" --run-dir "<NEW_RUN_DIR>" --json
cad2gis review "<NEW_RUN_DIR>" --workspace "<REVIEW_DIR>" --port 8765
```

打开 [本地工作台](http://127.0.0.1:8765/)。最终 `delivery.gpkg` 使用当前匹配坐标，
并记录 `relative_osm` 来源；人工选点保存时只改变预览，确认二次匹配才生成新 GIS。
OSM 匹配包含单位、投影转换和平移，不自动旋转、拉伸或把缆线改画成道路。
离线道路快照使用 `--osm-data "<OVERPASS.json>"`；需要显式地点提示时使用
`--osm-place "<PLACE_NAME>"`。完整参数、分区行为和复现方法见
[OSM 标准匹配说明](docs/OSM_STANDARD_MATCHING.md)。

`convert`、`auto-convert`、MCP 和批量转换默认使用 `svg_mode=candidate`，在
`<NEW_RUN_DIR>/svg-candidates/` 保存 `symbols.sqlite3`、`correspondence.json` 和
`correspondence.html`。SVG 与 GIS 完整生成后一起发布；提取失败时不发布新 run，
已有结果保留。候选只供复核，不会自动替换交付样式。原图需要指定字体时可重复使用
`--svg-font-dir "<FONT_DIR>"`；显式 `--svg-mode off` 关闭提取。候选来源、哈希及
人工绑定方式见 [SVG 提取说明](docs/OPTIONAL_SYMBOL_ASSETS.md)。

插件是 canonical Python 流水线的薄接入层。没有安装 GIS runtime、DWG reader
或没有授权项目根目录时，插件会失败关闭，而不会输出伪成功 GeoPackage。

下面的命令不需要先克隆仓库。先按操作系统安装同一个可移植
`cad2gis-agent-mcp` 控制面运行时，再按智能体客户端注册插件。安装命令使用
`main.zip` 而不是完整 Git 历史，并用 `--force` 主动替换旧版本；因此它也能修复
指向已删除 worktree 的 editable 安装。

Windows 更新或修复前，先退出正在使用 CAD2GIS 的 MCP 客户端并停止
`cad2gis review`。运行中的 Python 进程会锁住 uv 工具目录，使 `--force` 以
`os error 32` 失败；安装完成后再重启客户端。

### 1. 安装本地运行时

Windows PowerShell：

```powershell
winget install --id=astral-sh.uv -e
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis runtime install
Get-Command cad2gis-agent-mcp
cad2gis-agent-mcp --help
cad2gis doctor --deep --profile full --json
```

macOS：

```bash
brew install uv
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis runtime install
command -v cad2gis-agent-mcp
cad2gis-agent-mcp --help
cad2gis doctor --deep --profile full --json
```

Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis runtime install  # 无 Homebrew 时从校验过的官方源码构建到用户缓存
command -v cad2gis-agent-mcp
cad2gis-agent-mcp --help
cad2gis doctor --deep --profile full --json
```

WSL 使用上面的 Linux 命令，但不把 WSL → Windows GUI 作为受支持的
AutoCAD/QGIS 会话控制通道。需要操控 Windows AutoCAD 或 Windows QGIS
桌面会话时，请在 Windows 原生 Codex/CLI 环境中安装运行时。

`cad2gis-agent-mcp --help` 验证插件实际调用的 console script 可以导入并启动。
`cad2gis doctor --deep --profile full --json` 会同时检查 MCP 入口、WebUI、
bundled GDAL/OGR、ezdxf 和 LibreDWG。
`[agent]` extra 的平台 wheel 提供 GDAL/OGR；`cad2gis runtime install` 把官方
LibreDWG CLI 安装到用户缓存（Windows），在 macOS/Linux 上优先复用 Homebrew，
否则从固定版本、固定 SHA-256 的官方源码进行无 root 构建。源码回退需要系统已有
C 编译器和 `make`；AutoCAD 与 Conda 都不是默认转换链路的前置条件。

### 2. 安装智能体客户端插件

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

Cursor/VS Code 模板通过 `${workspaceFolder}` 自动使用当前工作区，无需填写机器
相关的绝对路径；下载后重启客户端。完整模板见
[`plugins/cad2gis-agent/clients`](plugins/cad2gis-agent/clients)。

### 3. 验证安装

先验证本地运行时，再验证正在使用的客户端。只使用 Codex 或 Claude Code 其中
一个时，不需要执行另一个客户端的列表命令。

```shell
cad2gis-agent-mcp --help
cad2gis doctor --deep --profile full --json
codex plugin list
claude plugin list
```

重启客户端并新建任务后，再让智能体调用 `get_capabilities`，确认 MCP 能力和
当前工作区授权目录已加载。完整 GIS 环境还应执行
`cad2gis doctor --deep --strict --profile full --json` 并确认
`selected_profile_ready: true`；这同时覆盖转换、MCP 与 Review WebUI。

### 4. 更新

Windows 上先停止 CAD2GIS MCP/WebUI 进程，再替换运行时。

```shell
# 运行时：同一条命令同时负责首次安装、更新和修复悬空 editable 安装
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis runtime install

# Codex
codex plugin marketplace upgrade cad2gis
codex plugin add cad2gis-agent@cad2gis

# Claude Code
claude plugin marketplace update cad2gis-tools
claude plugin update cad2gis-agent@cad2gis-tools
```

若客户端显示 `CONNECTION_CLOSED`，且 `cad2gis` 或 `cad2gis-agent-mcp` 报
`ModuleNotFoundError: No module named 'cad2gis'`，不要手工修改 uv 的 `.pth` 或
插件缓存。直接重新执行上面的 `uv tool install ... --force`，再确认
`cad2gis-agent-mcp --help` 成功，最后重启客户端并新建任务。

Cursor 与 VS Code / Copilot 使用者请重新下载上面的动态工作区 MCP 模板。

### 5. 卸载

```shell
# Codex
codex plugin remove cad2gis-agent@cad2gis

# Claude Code
claude plugin uninstall cad2gis-agent@cad2gis-tools

# 本地运行时
uv tool uninstall cad2gis
```

安装、验证、更新、卸载或修改 MCP 配置后，请重启客户端并新建任务，使 skills 和
tools 从新会话边界加载。

## 架构

```text
DWG reader (AutoCAD / LibreDWG / source-bound records)
  -> 不可变实体清单与样式、标签、曲线事实
  -> plan-domain 与 geometry-first 场景分区
  -> source-bound AI onboarding（只选择源中已观察到的标识）
  -> 确定性语义编译与精确 census gates
  -> 曲线、几何、拓扑、长度四类独立验证
  -> 单位与基础坐标操作 + 默认 OSM 相对平移匹配
  -> 人工控制点预览 -> 确认后二次匹配，生成新的 GIS 版本
     （显式 GCP 配准与 nominal 兼容路径仍可选）
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
uv sync --extra agent --extra test
uv run cad2gis runtime install
uv run cad2gis doctor --deep --strict --profile full --json
```

需要复现传统原生 GIS 开发栈时，`env/environment.yml` 仍作为可选环境保留：

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
pip install -e ".[agent,test]"
cad2gis doctor --deep --strict --profile full --json
```

支持 Python 3.11–3.12。MCP、证据协议和 GeoPackage 交付结构跨 Windows、Linux、
macOS 一致；LibreDWG CLI 是默认 reader，AutoCAD/Core Console 仅是 Windows 上
显式选择的可选回退。安装后可直接使用 `cad2gis-agent-mcp` 启动 MCP 服务。

Windows 使用 AutoCAD reader：

```powershell
$env:CAD2GIS_READER_BACKEND = "autocad"
```

## 新图纸的推荐流程

每个新 DWG 都必须建立自己的 source-bound profile 和 mapping registry：

```powershell
cad2gis inspect "<SOURCE.dwg>" --json
cad2gis bootstrap "<SOURCE.dwg>" --project "<PROJECT_DIR>" --json
```

检查并完成生成的源配置与语义映射后，再验证和转换：

```powershell
cad2gis validate --project "<PROJECT_DIR>" --json
cad2gis convert "<SOURCE.dwg>" `
  --project "<PROJECT_DIR>" `
  --run-dir "<NEW_RUN_DIR>" `
  --json
```

`convert` 和 `auto-convert` 默认使用 `--matching osm`。需要沿用标称坐标兼容流程时，
显式选择 `--matching nominal`；需要应用测量 GCP 配置时，显式传入
`--gcp-profile "<GCP_PROFILE.json>"`。标准 OSM 模式不会自动启用项目目录中发现的
GCP 文件。有效坐标域、地点提示或离线 OSM 快照提供匹配搜索参考；没有任何地理
参考的本地 CAD 仍需补充定位输入。自动匹配不确定但地域已知时，会保留待人工检查的粗定位。

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

## 项目目录与期望产物结构

每张 DWG 对应 `baselines/<项目>/` 下的一套 source-bound 产物，本工作区十个基线
即采用该结构：

```text
baselines/<project>/
├── config/                        # 源绑定与语义映射，人工评审后冻结
│   ├── source_profile.json        # 源配置：CRS/CGEOCS、GEODATA 配准、单位与图层门槛
│   ├── mapping_registry.json      # 图层/实体 → 要素类映射与元素计数门禁
│   ├── spatial_regions.json       # 独立分区契约（如 Manado 的 EMR 面板）；无分区时缺省
│   ├── osm_anchor.json            # 可选：地点锚点，供缺少地理坐标的图纸定位
│   ├── matching-manual-gcp.json   # 可选：冻结的人工匹配 revision 记录
│   └── .osm-cache/                # 可选：Overpass 路网快照缓存（内容寻址命名）
├── run/                           # 标准转换 run；不存放任何 QGZ
│   ├── source.gpkg                # 原生 CAD 事实快照
│   ├── evidence.gpkg              # 证据层
│   ├── delivery.gpkg              # 交付几何与属性（权威字节，打包后字节不变）
│   ├── osm_matching.json          # OSM 匹配记录：status/revision/translation/search_bbox
│   ├── run_manifest.json          # 运行状态与全部产物的 SHA-256
│   ├── qgis/styles/*.qml          # 每图层 QML 与 style_manifest.json
│   ├── svg-candidates/            # 源 SVG 候选：correspondence.{json,html}、selection.json、
│   │                              # manifest.json、index.html、symbols/、symbols.sqlite3
│   ├── reasoning/                 # cad_scene_graph.json、evidence_graph.json.gz、
│   │                              # evidence_index.sqlite3、visual/（分块渲染与命中索引）
│   └── <PARTITION>/               # 独立分区 run：delivery.gpkg、partition_manifest.json、qgis/
├── run.review/                    # 使用 Web 工作台后才出现
│   ├── review.sqlite3             # 人工控制点 revision store
│   ├── matching-active.json       # 当前生效匹配版本的指针
│   └── matching/r<N>-<uuid>/      # 每轮二次匹配：match-profile.json、controls.json、run/
└── delivery/                      # 便携交付包，QGZ 与其 GeoPackage 成组存放
    ├── <6 个文件>                 # 标准 run 的交付
    ├── r<N>/<6 个文件>            # 人工复核 revision 的交付（cad2gis publish-match）
    └── <PARTITION>/<6 个文件>     # 分区交付，与主体同构
```

交付包固定为六个文件：`delivery.gpkg`、`delivery.qgz`、`delivery.review.json`、
`osm-basemap.gpkg`、`view-with-source-SVG.qgz`、`view-with-source-SVG.review.json`。
`delivery.gpkg` 与对应 run 的字节完全一致，QML/SVG 只是呈现层，不改写数据库值与几何精度。
原始 DWG 不进入项目目录，保存在仓库 `raw/` 下；`run/` 与 `run.review/` 永不存放 QGZ 交付文件。

## 长度语义

`CABLE_SEGMENT` 同时保留三种不同含义的长度：

- `source_native_length_m`：DWG 源曲线长度；
- `measurement_native_m`：匹配到的独立 DWG `DIMENSION` 数值；当 DWG 标注文本存在时使用**渲染后的标注值**（例如黄色整数 `50 m`），而不是 `act_measurement` 的未舍入小数；
- `source_cad_length_m`：CABLE_SEGMENT 的名义源长度——有 DIMENSION 时等于标注值，无 DIMENSION 时回退为不可变 CAD 曲线长度；
- `delivery_grid_length_m` / `geodesic_length_m`：坐标转换后的网格与椭球长度。

没有 `DIMENSION` 时，线段仍然具有可靠的 CAD 曲线长度，`measurement_state=cad_geometry_only`，
但 QGIS 中不显示任何长度标签：原图没有标注的跨度不会被补上系统发明的文字。
尺寸匹配采用：

1. 精确线段端点；
2. 尺寸端点与缆线端点落在同一对唯一支撑设施上。

第二种规则解决真实 CAD 中尺寸延伸线与缆线端点存在偏移的问题，并保留完整
rule/provenance。

## Web 配准、坐标传送与审查

```powershell
cad2gis review "<RUN_DIR>" --workspace "<REVIEW_DIR>" --port 8765
```

打开 `http://127.0.0.1:8765/`：

1. 右图先显示当前已应用的 GIS 匹配结果，与 OSM 底图对照；
2. 左图点击 CAD 实体，系统吸附到真实几何；右图点击同一位置，或输入
   EPSG:4326 经度/纬度；
3. 保存至少两个位置不同的训练点后查看人工平移校正预览。独立检查点只评估残差，
   不参与拟合；足够且分布合理的检查点有助于识别局部偏差；
4. 确认预览后点击“二次匹配并生成新结果”，由正式转换管线创建新的 GIS；
5. 完成后自动加载新结果，保留控制点；可继续修改、预览和再次匹配；
6. 复核版交付用 `cad2gis publish-match "<新RUN_DIR>"` 发布，见
   [QGIS 交付](#qgis-交付)。

工作台会显示本次保存的匹配配置和可复制转换命令。每轮从原始 CAD 坐标及同一
基础坐标操作重新计算总平移，避免反复叠加前一轮偏移。独立图框分别匹配；
从某个分区发起的人工校正只生成该分区的新交付。失败时继续保留当前结果和控制点。

界面还提供 `?demo=1` 合成交互模式，用于公开展示图层开关、CAD 几何吸附和
坐标传送，不读取或上传真实 DWG，也不会伪装成已执行转换。例如：
`http://127.0.0.1:8765/?demo=1`。真实产物仍必须由本地 CLI/MCP 流水线生成。

公开的合成演示由 GitHub Pages 发布：
[ULTRA CAD2GIS Web Demo](https://mola-maker.github.io/CAD2GIS/?demo=1)。该页面只含
`demo-fixture.js` 中的合成对象；真实 CAD/GIS 处理仍在本地运行。

左侧 ToC 将源事实、语义映射、空间配准、独立校验和 GIS 交付分开显示；右侧
“证据 / 控制台 / AI 协作”标签只记录有效审查动作，并提供目标坐标、转换命令和
受约束 AI 提示词的复制功能。

人工选点只写独立 revision store 并更新预览；确认二次匹配会创建新的 run，
原始 CAD 与之前的 delivery GeoPackage 保持不变。OSM 控制点的精度类别固定为
`RELATIVE_OSM_REFERENCE_ONLY`；若要证明绝对精度，必须替换为测量或权威控制点。
复制或归档 run 后，服务只接受 SHA-256 与 manifest 完全一致的同目录产物；若
原 DWG 或绑定配置不可用，则允许查看已有结果，二次转换需先恢复对应输入。

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

MCP 只能访问客户端工作区或显式授权根目录中的文件。通常无需配置；只有需要授权
工作区外的数据目录时才设置：

```powershell
$env:CAD2GIS_PROJECT_ROOTS = "D:\survey-data;D:\shared-cad"
```

## QGIS 交付

需要排查底图偏移时，可使用独立的 [QGIS / OSM 复核入口](docs/qgis-osm-review.md)，
为工程加入离线参考底图并生成真实 QGIS 快照。原图坐标标注约束的可选校正只应用于复核工程显示，不改写原始数据库或长度，也不代替测量控制点验收。

### 复核版交付（`delivery/r<N>/`）

Web 工作台二次匹配产生的新 run 不是标准 run，其便携交付按版本发布在项目 `delivery/`
下，标准 run 的交付仍在 `delivery/` 根，两者互不覆盖：

```powershell
# RUN_DIR 位于 <PROJECT_DIR>/run.review/matching/r<N>-<uuid>/run 时自动推导交付目录
cad2gis publish-match "<RUN_DIR>"

# 其他位置需显式指定
cad2gis publish-match "<RUN_DIR>" --delivery-root "<PROJECT_DIR>/delivery"
```

- 版本目录名取自该 run 的 `osm_matching.revision`（`r2`、`r3`……）；已存在的版本目录会被拒绝，不覆盖旧版本。
- 每个版本目录与标准交付同构：`delivery.gpkg`（与 run 字节一致）、`delivery.qgz`、
  `delivery.review.json`、`osm-basemap.gpkg`、`view-with-source-SVG.qgz`、
  `view-with-source-SVG.review.json`；分区图纸的面板交付位于 `delivery/r<N>/<分区>/`。
- SVG 符号绑定取自 run 的 `svg-candidates`；OSM 底图按各 scope 的要素范围获取，
  也可用 `--osm-data "[SCOPE=]PATH"` 复用已复核的 Overpass JSON 快照（离线环境或分区各一份）。
- 该步骤需要能 `import qgis.core` 的解释器，依次尝试 `--qgis-python`、环境变量
  `CAD2GIS_QGIS_PYTHON`、当前解释器、`/usr/bin/python3`。
- 转换 run 与 review 工作区都不存放 QGZ 交付文件：发布过程在系统临时目录完成组装，
  成功后原子改名入库，失败即清理，不留半成品。

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
- OSM 相对匹配状态、版本、来源与人工校正残差；
- GCP train residual 与独立 check-point 精度。

`$INSUNITS` 是块插入缩放提示，不自动等同于 WCS 坐标单位。当 DWG 的
`CGEOCS` 明确绑定投影 CRS 时，其线性轴单位控制 WCS→米的尺度，避免把 UTM
坐标错误缩小 1000 倍。

外部 `E:\branch_CAD2GIS\APD_test` 仅是兼容性压力输入，不是训练集、规则模板或
准确率真值。同理，外部语料中新增的 6 张 APD（As Plan Drawing）验证图也必须
各自建立 source-bound profile，不得沿用四张开发基线图的规则或数量门禁。
没有 authoritative GCP 的结果必须保持 `CONDITIONAL` 或
`not independently verified`。

## 验证

```powershell
python -m pip install -e ".[agent,test]"
python -m pytest -q

$env:CAD2GIS_FULL_DWG_TESTS = "1"
python -m pytest tests/test_apd_test_compatibility.py -q
```

仓库 CI 在 Ubuntu/Python 3.11、Ubuntu/Python 3.12、Windows/Python 3.12
和 macOS/Python 3.12 上安装 agent 依赖与锁版 LibreDWG runtime，
再分别执行安装、锁版 Ruff、Python 编译、WebDemo JavaScript 语法、MCP/插件
契约和完整 pytest 回归。Pages CD 会重新验证浏览器契约，使用
`scripts/build_pages.py` 构建 `_site`。合成演示与用户授权的派生九图演示分开校验。
九图、QGZ 和 SVG 复验包来自 `docs/derived-release.json` 指定的 Release，下载后检查
大小、SHA256、目录边界和完整文件清单；原始 DWG/DXF 不进入公开站点。

仓库目录：

- `src/cad2gis/`：唯一生产实现、CLI、reader、MCP、review server
- `tests/`：自动化契约与回归测试
- `baselines/`：source-bound 基线配置；大型 GeoPackage/清单证据可作为外部语料挂载
- 原始 DWG 与生成输出放在仓库外，通过显式路径或批量输入契约挂载
- `experiment/config/`：APD reviewed 兼容配置，不含原始图纸
- `plugins/cad2gis-agent/`：智能体插件和 MCP 客户端模板
- `docs/`：架构、鲁棒性、可移植性、术语与对账说明
- `env/`：固定 GIS 运行环境

本地重建包含九图的 Pages：先运行 `python scripts/fetch_derived_release.py`，再运行
`python scripts/build_pages.py --output _site`。`pages-delivery/` 是忽略的下载缓存，
只提交核心源码、必要测试配置、文档和小型发布清单。
