# CAD2GIS Agent Plugin

面向智能体的、证据优先的 DWG/CAD → GIS 转换插件。

**文档状态：正式版**  
**插件 ID：`cad2gis-agent`**  
**运行方式：Codex / Claude Code 插件，或任意兼容 MCP 的智能体**

CAD2GIS Agent 将智能体的语义理解能力与确定性的 CAD/GIS 工程流水线结合起来：
智能体负责清点、解释、选择已有证据和组织工作流；Python 核心负责读取实体、还原
曲线、计算长度、构建拓扑、执行 CRS/GCP 变换、验证结果并生成 GeoPackage。

## 核心原则

- **源文件绑定**：每个项目配置都绑定 DWG 的 SHA-256，不能把一张图纸的规则套到
  另一张图纸。
- **源几何不可变**：AI 不直接编造或移动 CAD 坐标、曲线顶点和长度。
- **确定性执行**：CLI、MCP、Web 审查和插件调用同一条 canonical pipeline。
- **证据可追溯**：输出包含 source、evidence、delivery、manifest、QML 和运行状态。
- **失败关闭**：缺少 reader、CRS、映射或必要证据时返回明确错误，不生成伪成功包。
- **精度分层**：源几何、拓扑、长度、名义 CRS 和地面定位精度分别验证。

## 能做什么

- 读取和清点 DWG 实体、图层、块、标签、颜色、线型、线宽和曲线事实。
- 为新图纸生成只绑定当前 source 的 project profile 和 mapping registry。
- 让宿主智能体对已观察到的图层/块标识提出语义映射，并由确定性编译器复核。
- 保留圆弧、polyline/bulge 和源曲线长度，生成 CABLE/CABLE_SEGMENT。
- 分别校验几何、拓扑、线段长度、DIMENSION 覆盖和坐标精度。
- 使用名义 CRS，或使用成对 GCP 与独立检查点执行保持形状的相似变换。
- 生成 QGIS 可直接加载的 GeoPackage、QML、manifest 和 run status。
- 通过 Web 双地图界面进行 CAD 点吸附、经纬度传送、地图叠加和 GCP 配准。
- 通过 Evidence Graph 和受约束 Decision Pack 审查候选修复。

## 不会做什么

- 不把 LLM/VLM 的视觉猜测当作真实坐标、长度或 CRS。
- 不把 OSM 视觉重合声明为测量级绝对精度。
- 不在原始 DWG 或已有 GeoPackage 上进行不可逆覆盖。
- 不把 `APD_test` 或任何测试图纸当作通用训练集或硬编码规则库。
- 不在缺少独立检查点时把结果标记为地面精度已验证。

## 系统架构

```text
DWG reader
  -> 不可变实体清单 / 样式 / 标签 / 曲线事实
  -> plan-domain 与 geometry-first 场景分区
  -> source-bound AI onboarding
  -> 确定性映射编译与 fresh dry-run
  -> 几何 / 拓扑 / 长度独立校验
  -> 名义 CRS / 可选 GCP 相似变换
  -> source.gpkg + evidence.gpkg + delivery.gpkg
  -> QML + Evidence Graph + manifest + run_status
  -> 独立 Web review workspace
```

AI 位于控制平面，几何与坐标计算位于确定性数据平面。插件不会维护另一套转换
实现。

## 环境要求

- Windows 10/11，或具备兼容 LibreDWG reader 的系统。
- Python 3.11–3.12；GIS 生产环境推荐使用项目固定的 `cad2gis` conda 环境。
- GDAL、PROJ、pyproj 及项目环境中声明的 reader。
- Windows 原生 DWG 读取可使用 AutoCAD/Core Console reader。
- MCP 客户端需支持 `stdio` 或 Streamable HTTP。

项目环境安装：

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
pip install -e ".[mcp,review,test]"
cad2gis doctor --deep --strict --json
```

Windows 选择 AutoCAD reader：

```powershell
$env:CAD2GIS_READER_BACKEND = "autocad"
```

限制 MCP 可访问的项目根目录：

```powershell
$env:CAD2GIS_PROJECT_ROOTS = "E:\branch_CAD2GIS"
```

## 智能体接入

### Codex 插件

插件安装后，在新任务中直接描述目标，例如：

```text
使用 CAD2GIS Agent 检查这张 DWG，创建 source-bound 项目配置，
运行转换并报告几何、拓扑、长度和坐标精度状态。
```

插件提供 `convert-cad-to-gis` skill 和 `cad2gis-agent` MCP server。更新插件后应
启动新任务，使 Codex 重新加载 skills 与 tools。

### 通用 MCP：stdio

```powershell
conda run --no-capture-output -n cad2gis `
  python -m cad2gis.agent_mcp `
  --transport stdio
```

### 通用 MCP：Streamable HTTP

```powershell
conda run --no-capture-output -n cad2gis `
  python -m cad2gis.agent_mcp `
  --transport streamable-http `
  --host 127.0.0.1 `
  --port 8768
```

Endpoint：

```text
http://127.0.0.1:8768/mcp
```

HTTP 服务默认只允许 loopback。需要跨机器访问时，必须使用带身份认证、TLS、
Origin 校验和访问控制的反向代理。

主流客户端模板：

| 客户端 | 模板 |
|---|---|
| Codex | [`clients/codex.config.toml`](clients/codex.config.toml) |
| Claude Code | [`clients/claude-code.mcp.json`](clients/claude-code.mcp.json) |
| Cursor | [`clients/cursor.mcp.json`](clients/cursor.mcp.json) |
| VS Code / GitHub Copilot | [`clients/vscode.mcp.json`](clients/vscode.mcp.json) |
| 通用 Streamable HTTP | [`clients/streamable-http.json`](clients/streamable-http.json) |

完整说明见 [`clients/README.md`](clients/README.md)。

## 标准工作流

### 1. 新 DWG

智能体必须按以下顺序执行：

1. `get_capabilities`
2. `inspect_source`
3. `bootstrap_project`
4. `prepare_ai_onboarding`
5. 宿主模型只选择响应中已经观察到的图层、块和确定性 CRS candidate
6. `apply_ai_onboarding`
7. `validate_project`
8. `run_conversion`
9. `inspect_run`

模型不得在 proposal 中发明图层、正则表达式、坐标、长度、GCP、CRS 或期望数量。
编译器会重新读取 DWG、重新清点并执行 fresh dry-run；任何绑定不一致都会恢复为
draft。

### 2. 已有 reviewed/auto-accepted 项目

```text
validate_project -> run_conversion -> inspect_run
```

每次转换都应使用新的 `run_dir`，避免覆盖历史证据。

### 3. Provider 自动 onboarding

配置 DeepSeek 或兼容聚合网关后，可调用：

```text
auto_onboard_and_convert
```

该工具仍然执行确定性编译与 admission gate；provider 返回内容不会直接成为
GeoPackage。

### 4. 证据审查与受约束修复

```text
list_evidence_nodes
  -> get_evidence_node
  -> list_visual_regions / resolve_visual_hit
  -> list_registered_operations
  -> list_endpoint_join_candidates 或 list_network_repair_candidates
  -> create_decision_pack
  -> validate_decision_pack
  -> run_conversion(llm="observe" 或 "assist")
```

Decision Pack 只能引用系统返回的 evidence node ID、candidate ID 和注册操作；
不能携带任意坐标、WKT 或自定义几何。

## MCP 工具

| 分组 | 工具 |
|---|---|
| 能力发现 | `get_capabilities` |
| Source onboarding | `inspect_source`, `bootstrap_project`, `prepare_ai_onboarding`, `apply_ai_onboarding`, `auto_onboard_and_convert`, `validate_project` |
| 转换与运行 | `run_conversion`, `inspect_run` |
| Evidence Graph | `list_evidence_nodes`, `get_evidence_node`, `list_visual_regions`, `resolve_visual_hit` |
| 候选修复 | `list_registered_operations`, `list_endpoint_join_candidates`, `list_network_repair_candidates` |
| Decision Pack | `create_decision_pack`, `validate_decision_pack` |
| Web 审查 | `prepare_review_workspace` |

`get_capabilities` 应作为跨客户端接入后的首次调用，用于读取 transport、精度边界和
宿主 AI 的权限范围。

## Web 地图配准

准备审查空间：

```text
prepare_review_workspace(run_dir)
```

或使用 CLI：

```powershell
cad2gis review "<RUN_DIR>" `
  --workspace "<REVIEW_DIR>" `
  --port 8765
```

打开：

```text
http://127.0.0.1:8765/
```

操作流程：

1. 在左侧 CAD 图点击缆线、设施或可识别拐点；系统吸附到最近真实几何。
2. 在右侧地图点击同一地面位置，或输入 EPSG:4326 经度/纬度。
3. 服务端把经纬度转换为 run manifest 中的目标投影 CRS。
4. 至少选择 4 个分布合理的训练点和 3 个独立检查点。
5. 生成 `web_gcp_profile.json` 和重新转换命令。
6. 在新的 run directory 中生成校准后的新 GeoPackage。

选点只写独立的 `review.sqlite3`，不会立即改变原有 GPKG。执行重新转换后，系统
生成新的 `delivery.gpkg`，原 run 保持不可变。

OSM 控制点固定标记为：

```text
RELATIVE_OSM_REFERENCE_ONLY
```

需要绝对定位精度时，应使用测量或权威控制点替换 OSM 参考点。

## 缆线长度

`CABLE_SEGMENT` 分开保存：

| 字段 | 含义 |
|---|---|
| `source_native_length_m` | CAD 源曲线长度 |
| `measurement_native_m` | 匹配到的独立 DWG DIMENSION |
| `delivery_grid_length_m` | 目标投影 CRS 中的网格长度 |
| `geodesic_length_m` | 椭球测地线长度 |
| `length_value_m` | 交付显示使用的有证据长度 |

没有 DIMENSION 不等于没有长度。此时：

```text
measurement_state = cad_geometry_only
length_source = dwg_curve_geometry
```

显示标签为：

```text
31.574 m [CAD geometry; no DIMENSION]
```

DIMENSION 匹配先使用精确端点；若尺寸延伸线与缆线端点有偏移，则使用唯一支撑
设施点对进行匹配，并保留匹配方法和 provenance。

## 交付物与 QGIS

标准 run 包含：

```text
source.gpkg
evidence.gpkg
delivery.gpkg
run_manifest.json
QML / style manifest
Evidence Graph
visual evidence index
run_status
```

在 QGIS 中通过“数据源管理器 → GeoPackage”加载 `delivery.gpkg`，或直接拖入
QGIS。加载随包生成的 QML 后，可恢复转换后的颜色、线型、点符号和标签。

## 运行状态

| 状态 | 含义 |
|---|---|
| `VERIFIED` | 所有启用的发布 gate 已通过 |
| `CONDITIONAL` | 产物可用于审查，但存在未独立验证的精度或证据边界 |
| `UNSAFE` | 关键 gate 失败，不能作为正式地图交付 |
| `FAILED` | 转换未完成，不能发布交付物 |
| `READER_UNAVAILABLE` | 当前环境缺少可用 DWG reader |

`VERIFIED` 也只代表配置中声明的 gate 通过；它不自动等同于测量单位签发的绝对
精度认证。

## 精度报告

必须分别报告：

- reader/source record fidelity；
- geometry/curve fidelity；
- topology 和 segment conservation；
- CAD 曲线长度与独立 DIMENSION 覆盖；
- nominal CRS transformation；
- GCP train residual；
- independent check-point residual；
- absolute accuracy validation。

缺少 authoritative GCP 与独立检查点时，绝对精度必须保持：

```text
not independently verified
```

## 安全边界

- MCP 文件访问受 `CAD2GIS_PROJECT_ROOT` / `CAD2GIS_PROJECT_ROOTS` 限制。
- JSON artifact 有大小限制，路径逃逸和不存在的文件会被拒绝。
- HTTP MCP 默认仅绑定 `127.0.0.1`。
- Review workspace 与 immutable run artifacts 分离。
- DeepSeek/New API 密钥只从环境变量读取，不写入配置、日志或 manifest。
- MCP tool 输出应视为结构化工程证据，宿主智能体不得越权修改其事实字段。

## 常见问题

### 智能体看不到工具

1. 确认使用的是新任务；已有任务不会自动重新加载插件。
2. 检查 conda 环境和 `cad2gis.agent_mcp` 是否可启动。
3. 调用 MCP `tools/list`，确认存在 `get_capabilities`。
4. 检查 `CAD2GIS_PROJECT_ROOTS` 是否包含目标项目。
5. stdio server 不能向 stdout 输出非 MCP 文本；诊断日志应写 stderr。

### 返回 `READER_UNAVAILABLE`

运行：

```powershell
cad2gis doctor --deep --strict --json
```

确认 AutoCAD/Core Console 或 LibreDWG reader 可用，并检查
`CAD2GIS_READER_BACKEND`。

### 项目一直是 draft

检查 source hash、inventory hash、CRS candidate、语义 confidence 和 fresh
dry-run 结果。不要复制其他 DWG 的 registry。

### QGIS 中整体漂移

这是坐标定位问题，不应通过移动单条缆线修补。使用分布合理的 GCP 和独立检查点
重新生成新 run，并分别检查 source geometry 与 coordinate accuracy。

### 大量 `unmeasured`

旧标签中的 `unmeasured` 通常表示没有匹配到独立 DIMENSION，而不是缺少 CAD
曲线长度。正式版使用 `cad_geometry_only` 明确区分两种情况。

### ZNRO/ZPM 只有标签没有实例

检查 scene partition、模型空间实体 census、块实例展开和 mapping registry。
标签证据不能替代几何实例，缺失实例应保持显式 unresolved。

## 验证与发布检查

开发环境的最小验证：

```powershell
conda activate cad2gis
python -m pytest tests/test_mcp_stdio.py tests/test_review_server.py -q
python -m cad2gis.agent_mcp --help
cad2gis doctor --deep --strict --json
```

真实 DWG 兼容性测试：

```powershell
$env:CAD2GIS_FULL_DWG_TESTS = "1"
python -m pytest tests/test_apd_test_compatibility.py -q
```

真实图纸兼容测试只证明 reader 和流水线能够处理输入，不替代有 GCP/真值数据的
精度验收。

## 插件目录

```text
cad2gis-agent/
├── .codex-plugin/plugin.json
├── .claude-plugin/plugin.json
├── .mcp.json
├── README.md
├── clients/
├── scripts/
└── skills/
    └── convert-cad-to-gis/
```

工作流约束的权威说明位于：

- [`skills/convert-cad-to-gis/SKILL.md`](skills/convert-cad-to-gis/SKILL.md)
- [`skills/convert-cad-to-gis/references/decision-contract.md`](skills/convert-cad-to-gis/references/decision-contract.md)

## 版本策略

- 插件版本前缀遵循 manifest 中的语义版本。
- 本地开发更新使用单一 `+codex.<cachebuster>` 后缀。
- cachebuster 只用于强制客户端重新加载，不改变转换算法版本。
- run manifest、source/profile hash 和 Evidence Graph hash 才是工程结果的可重放
  依据。
