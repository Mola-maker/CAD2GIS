# CAD2GIS

CAD2GIS 是一个证据优先、可重放的 DWG → GIS 转换系统。CLI、Python API、
Web 审查界面和 `cad2gis-agent` MCP 服务共享 `src/cad2gis` 中的同一条
canonical 流水线，不为某张测试图维护硬编码分支。

> DWG 中声明了 CRS，不代表实体已经正确落在该 CRS 的真实地面位置。
> 源几何、拓扑、长度和坐标精度会分别验证。缺少可信控制点时，系统不会把
> OSM 视觉重合宣传为测量级绝对精度。

##怎么使用呢宝贝
直接把插件喂给AI叫它给你装就好了

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

## 安装

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
pip install -e ".[mcp,review,test]"
cad2gis doctor --deep --strict --json
```

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
$env:CAD2GIS_DEEPSEEK_API_KEY = "<secret>"
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
- `measurement_native_m`：匹配到的独立 DWG `DIMENSION` 数值；
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

Web 修改只写独立 revision store，不会修改源文件、evidence 或原 delivery
GeoPackage。OSM 控制点的精度类别固定为
`RELATIVE_OSM_REFERENCE_ONLY`；若要证明绝对精度，必须替换为测量或权威控制点。

## MCP 与主流智能体

MCP 服务支持标准 `stdio` 和 Streamable HTTP：

```powershell
# stdio（通常由智能体自动启动）
python -m cad2gis.agent_mcp --transport stdio

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
准确率真值。没有 authoritative GCP 的结果必须保持 `CONDITIONAL` 或
`not independently verified`。

## 验证

```powershell
conda activate cad2gis
python -m pytest -q

$env:CAD2GIS_FULL_DWG_TESTS = "1"
python -m pytest tests/test_apd_test_compatibility.py -q
```

仓库目录：

- `src/cad2gis/`：唯一生产实现、CLI、reader、MCP、review server
- `tests/`：自动化契约与回归测试
- `baselines/`：不可变 source-bound 回归证据
- `experiment/`：APD reviewed 兼容项目
- `plugins/cad2gis-agent/`：智能体插件和 MCP 客户端模板
- `docs/`：架构、鲁棒性、可移植性与对账说明
- `env/`：固定 GIS 运行环境
