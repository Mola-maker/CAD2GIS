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
- 启动并控制一个专用的可见 QGIS 桌面会话，加载 run、检查图层、切换显隐、缩放并导出当前视图。
- 通过 Evidence Graph 和受约束 Decision Pack 审查候选修复。
- 在首次结果不理想时，把用户语言意见、标注截图和 run visual region 绑定为反馈证据，
  通过有限次数的新 run 自迭代，并保留接受/拒绝与回退记录。

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
  -> lossless CAD Scene Graph（source 与 plan-domain 双视图）
  -> 每个 Layout 的 overview/detail 图、hit-map 与精确文字/块上下文
  -> hash-bound Scene Interpretation Plan（规则和 AI 都不能自动删除实体）
  -> source-bound AI onboarding（只排序已有 graph ID）
  -> 确定性映射编译与 fresh dry-run
  -> 几何 / 拓扑 / 长度独立校验
  -> 名义 CRS / 可选 GCP 相似变换
  -> source.gpkg + evidence.gpkg + delivery.gpkg
  -> QML + Evidence Graph + manifest + run_status
  -> 独立 Web review workspace
```

AI 位于控制平面，几何与坐标计算位于确定性数据平面。插件不会维护另一套转换
实现。

自迭代也位于控制平面：它更新 source-bound 的解释、映射或决策输入，每次都生成
新的 immutable candidate run。它不会让模型直接重写插件代码、原始 DWG 或已有
run。

## 环境要求

- Windows 10/11，或具备兼容 LibreDWG reader 的系统。
- Python 3.11–3.12；GIS 生产环境推荐使用项目固定的 `cad2gis` conda 环境。
- GDAL、PROJ、pyproj 及项目环境中声明的 reader。
- 默认使用 LibreDWG 读取 DWG；Windows AutoCAD/Core Console 仅作显式备用或并行核验。
- MCP 客户端需支持 `stdio` 或 Streamable HTTP。

QGIS 桌面会话会自动从 PATH 或 Windows 开始菜单发现；也可由宿主环境显式指定：

```powershell
$env:CAD2GIS_QGIS_EXECUTABLE = "E:\bin\qgis-bin.exe"
```

项目环境安装：

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
pip install -e ".[mcp,review,test]"
cad2gis doctor --deep --strict --json
```

仅在 LibreDWG 已返回可分类失败，或需要显式并行核验时，为该次运行选择 AutoCAD
reader：

```powershell
$env:CAD2GIS_READER_BACKEND = "autocad"
```

不要把 `CAD2GIS_READER_BACKEND=autocad` 固化到插件 `.mcp.json`。AutoCAD 运行应
保留独立 provenance，且不能在 LibreDWG 失败后静默切换。完成恢复/对比后清除该
变量，使后续任务继续以 LibreDWG 为主通道。

Codex 桌面端可能使用与交互桌面不同的隔离 Windows 账户。若
`accoreconsole.exe` 报告无法设置当前 profile，请在桌面 AutoCAD 的 Options →
Profiles 中导出一个专用 `.arg` 文件，并显式配置：

```powershell
$env:CAD2GIS_AUTOCAD_PROFILE = "E:\cad2gis\profiles\cad2gis.arg"
```

也可以使用插件内置的受限导出助手：

1. 在已经正常初始化的桌面 AutoCAD 中运行 `APPLOAD`。
2. 加载
   `skills\convert-cad-to-gis\scripts\export-autocad-profile.lsp`。
3. 运行 `CAD2GIS_EXPORT_PROFILE`，将新文件保存到
   `CAD2GIS_PROJECT_ROOTS` 允许的项目目录，例如
   `E:\cad2gis-project\tmp\autocad-profile\cad2gis.arg`。
4. 设置上面的环境变量，再运行
   `cad2gis doctor --deep --strict --json` 和 `inspect_source`。

助手调用 Autodesk 官方 ActiveX `ExportProfile`，只导出当前活动 profile；若目标
文件已经存在会拒绝覆盖。它不会切换、导入或重置 profile，不直接写注册表，也不
打开、保存或修改图纸。

插件只把该 profile 交给 AutoCAD 官方 `/p` 启动参数，不复制或改写注册表；未提供
profile 时仍会尝试启动账户自己的当前 profile，并在 `doctor` 中明确提示这一边界。

限制 MCP 可访问的项目根目录：

```powershell
$env:CAD2GIS_PROJECT_ROOTS = "E:\cad2gis-project"
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

### 5. 视觉与语言证据驱动的自迭代

当第一次转换不理想时：

```text
inspect_run
  -> start_feedback_iteration
  -> record_iteration_feedback
  -> prepare_iteration_context
  -> 使用既有 Scene/Semantic/Evidence/GCP 工具生成最小变更
  -> run_conversion（必须是新的 run_dir）
  -> evaluate_iteration_candidate
  -> decide_iteration_candidate(accept/reject/revise)
  -> export_iteration_learning
```

`record_iteration_feedback` 同时支持：

- 用户语言证据：哪里不对、期望变成什么；
- 已有 run 的 visual region ID：自动校验 manifest 与 render SHA-256；
- 用户标注图片：复制为 session 内的内容寻址证据；
- 已观察到的 Evidence Graph node ID。

默认最多 3 次候选运行。候选不能降低 `run_status`、增加 unresolved、改变 source
entity count，或破坏原先通过的 validation gate。即使 gate 全部通过，也必须由
用户看过新的视觉结果后显式确认，插件不会自动提升候选。

接受后的学习注册表只作为同一 source SHA-256 的 onboarding 建议。后续可调用：

```text
prepare_ai_onboarding(project_dir, learning_registry=".../iteration-learning.json")
```

它不会跨 DWG 自动泛化，也不会直接写入 proposal。完整设计见
[`docs/architecture/self-iteration-v1.md`](../../docs/architecture/self-iteration-v1.md)。

## MCP 工具

### 只生成源事实包

`export_source(source, run_dir, source_crs?, force?)` 只执行权威 DWG 读取、
实体守恒清点、CAD Scene Graph/视觉证据与 `source.gpkg` 写入。它不会进行语义
映射、拓扑修复、长度推断、CRS/GCP 配准，也不会生成 `delivery.gpkg`。没有可信
CRS 时应省略 `source_crs`；清单会明确记录 `native_cad_unregistered`。

```powershell
cad2gis source drawing.dwg --run-dir output/source-run --json
```

### 源事实到语义图层

该阶段单独生成 `semantic.gpkg`，仍保持 CAD 原生坐标，不提前进行 CRS/GCP
配准。宿主 AI 只能选择准备包中已经存在的组合、实体、类别、标签和证据 ID；
几何、坐标、原生长度、样式及标签文本由确定性编译器从 `source.gpkg` 复制。
第二版准备包为每条源线提供端点精确连接、仅邻近关系、附近业务节点和 CAD
图例样式匹配。网络线决策必须引用这些实际证据，且必须保留正数的 CAD 原生
长度；模型输出的自由文本说明不能代替证据。

```powershell
cad2gis semantic prepare output/source-run --json
cad2gis semantic compile output/source-run `
  --prepare-manifest output/source-run/semantic_prepare/manifest.json `
  --decision-pack decisions.json --json
cad2gis semantic validate output/source-run/semantic.gpkg --json
```

每个源实体必须进入且只进入以下一种状态：

- `CONSUMED_BY_FEATURE`
- `RETAINED_AS_REFERENCE`
- `EXCLUDED_AS_DOCUMENTATION`
- `UNRESOLVED`

没有决策的实体保持 `UNRESOLVED`，不会为了提高转换率被强制分类。

MCP 主流程：

1. `prepare_semantic_batches`
2. `list_semantic_batches`
3. `summarize_semantic_batch`
4. `list_semantic_candidates`
5. `create_semantic_decision_pack`
6. `compile_semantic_layers`
7. `inspect_semantic_coverage`

决策包同时绑定源 DWG SHA-256 与候选 JSONL SHA-256；旧候选或跨图纸决策会
被拒绝。块定义、图例、图框和标题栏可以整批标记为文档实体，但整批决策不能
生成业务要素。`semantic.gpkg` 还保存 `semantic_candidate_evidence` 审计表；
`inspect_semantic_coverage` 会报告网络要素数量、具备源长度的数量和源长度总和。

| 分组 | 工具 |
|---|---|
| 能力发现 | `get_capabilities` |
| Source onboarding | `inspect_source`, `bootstrap_project`, `prepare_ai_onboarding`, `apply_ai_onboarding`, `auto_onboard_and_convert`, `validate_project` |
| 转换与运行 | `run_conversion`, `inspect_run` |
| CAD Scene Graph | `list_cad_scene_nodes`, `get_cad_scene_node` |
| Scene understanding | `list_scene_visual_regions`, `get_scene_visual_region_context`, `create_scene_interpretation_plan`, `validate_scene_interpretation_plan` |
| Evidence Graph | `list_evidence_nodes`, `get_evidence_node`, `list_visual_regions`, `resolve_visual_hit` |
| 候选修复 | `list_registered_operations`, `list_endpoint_join_candidates`, `list_network_repair_candidates` |
| Decision Pack | `create_decision_pack`, `validate_decision_pack` |
| 反馈自迭代 | `start_feedback_iteration`, `record_iteration_feedback`, `prepare_iteration_context`, `evaluate_iteration_candidate`, `decide_iteration_candidate`, `inspect_iteration`, `export_iteration_learning` |
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

打开双地图审查工作台：

```text
http://127.0.0.1:8765/workspace
```

产品首页位于 `http://127.0.0.1:8765/`，逐步安装教学页位于
`http://127.0.0.1:8765/install`。

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

QGIS 是后期审阅与微调界面，不是 DWG 主读取器或第二套转换引擎。只有在 canonical
pipeline 已生成候选 run 后才加载 QGIS；在 QGIS 中发现的问题应回写为 source-bound
反馈并生成新的 immutable candidate run，不能直接把 GUI 状态当作正式交付依据。

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

智能体需要现场检查时使用受控会话：

```text
start_qgis_desktop_session
  -> load_qgis_conversion_run
  -> inspect_qgis_desktop_session
  -> set_qgis_desktop_layer_visibility / zoom_qgis_desktop_full_extent
  -> export_qgis_desktop_view
  -> stop_qgis_desktop_session（仅结束该专用会话）
```

该 bridge 只监听 `127.0.0.1`，使用随机会话令牌和项目根路径校验，只暴露固定动作，
不提供任意 Python `eval`/`exec`。架构说明见
[`docs/architecture/local-software-adapters-v1.md`](../../docs/architecture/local-software-adapters-v1.md)。

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
- 自迭代状态采用原子写入和内容摘要；候选 run 不会自动覆盖 active run。
- 学习注册表默认 source-bound、suggestions-only，不能作为跨图纸的硬编码规则库。

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
├── agents/
├── clients/
├── scripts/
└── skills/
    ├── convert-cad-to-gis/
    └── iterate-cad-to-gis/
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
