# Web Demo 软件演示交付系统设计

**日期**: 2026-07-19  
**项目**: CAD2GIS (E:\branch_CAD2GIS\CAD2GIS)  
**设计状态**: 待用户审查  
**关联文档**: `docs/APD_CAD2GIS_EXECUTION_PLAN.md`, `docs/APD_CAD2GIS_HANDOFF.md`

---

## 1. 背景与目标

当前 CAD2GIS 项目的核心转换能力位于 `experiment/py_scripts/cad2gis_v3/`，`docs/APD_CAD2GIS_EXECUTION_PLAN.md` 已将其规划为后续需包化的 `src/cad2gis` 工程。

本设计在**不改动现有执行计划范围**的前提下，为项目增加一个独立的 **Web Demo 软件演示交付系统**：

- **A 部分（演示系统）**：以 3D 交互方式展示 CAD2GIS pipeline 的原理、中间产物与最终交付结果。
- **B 部分（转换前端）**：通过 Web 界面调用现有 v3 pipeline，完成上传、配置、运行、下载的完整交付流程。

本系统的目标用户是评委、客户与内部工程师，强调**工程可信度**而非营销视觉效果。

---

## 2. 范围与边界

### 2.1 包含内容

- `webdemo/` 目录下的完整前后端代码。
- FastAPI 后端对 `experiment/py_scripts/cad2gis_v3/cli.py` 的调用封装。
- React + React Three Fiber 前端，包含：
  - 3D Pipeline 流程视图
  - 3D GIS 结果勘测视图
  - CAD 分层解剖视图
  - 转换任务管理与下载界面
- Docker Compose 一键运行能力。
- 预生成 `sample-run/` 用于离线演示 A 部分。

### 2.2 不包含内容

- 不迁移或重构 `experiment/py_scripts/cad2gis_v3/` 的核心转换逻辑。
- 不实现 `src/cad2gis` 包化（执行计划内的独立工作）。
- 不修改 `official/`、`experiment/output/` 等受保护目录中的现有文件。
- 不做通用多 DWG 支持，系统以 Hutabohu APD 为首要目标。
- 不将 Web UI 作为产品级转换主入口，它始终是演示与辅助工具。

### 2.3 受保护状态

- 不上传、不修改、不删除 `official/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg` 及其 `.dwl/.dwl2` 锁文件。
- 不依赖外部 `E:\aaaCAD2GIS` 目录。
- 运行输出隔离在 `webdemo/runs/{run_id}/`。

---

## 3. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Web Demo 交付系统                         │
├─────────────────────────┬───────────────────────────────────────┤
│      A: 演示系统         │           B: 转换系统                  │
│  ┌───────────────────┐  │   ┌──────────────┐  ┌──────────────┐  │
│  │ 3D Pipeline 舞台   │  │   │  转换参数表单  │  │  任务/结果列表  │  │
│  │ (R3F Three.js)    │  │   └──────────────┘  └──────────────┘  │
│  └─────────┬─────────┘  │              │                         │
│            │            │              ▼                         │
│  ┌─────────▼─────────┐  │   ┌─────────────────────────────────┐  │
│  │ 3D GIS 结果视窗    │  │   │      FastAPI Backend            │  │
│  │ (MapLibre/Cesium) │  │   │  /upload  /convert  /status     │  │
│  └─────────┬─────────┘  │   │  /download  /artifacts  /layers │  │
│            │            │   └─────────────────────────────────┘  │
│  ┌─────────▼─────────┐  │              │                         │
│  │ CAD 分层解剖器     │  │              ▼                         │
│  │ (Three.js DWG     │  │   ┌─────────────────────────────────┐  │
│  │  wireframe)       │  │   │   cad2gis_v3 CLI / Python       │  │
│  └───────────────────┘  │   │   (experiment/py_scripts/...)   │  │
│                         │   └─────────────────────────────────┘  │
└─────────────────────────┴───────────────────────────────────────┘
```

### 3.1 目录结构

```
webdemo/
├── frontend/                   # React + Vite + TypeScript
│   ├── src/
│   │   ├── app/                # 路由、布局、主题
│   │   ├── scenes/             # 3D Pipeline 流程视图
│   │   ├── gis/                # 3D GIS 结果勘测视图
│   │   ├── cad/                # CAD 分层解剖视图
│   │   ├── converter/          # B 部分：上传、参数、任务、下载
│   │   ├── api/                # 后端 API 封装 + SSE
│   │   └── shared/             # 组件、hooks、类型、工具
│   ├── public/
│   │   └── sample-run/         # 预生成数据，供离线演示
│   ├── package.json
│   └── vite.config.ts
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── routers/
│   │   │   ├── upload.py
│   │   │   ├── convert.py
│   │   │   ├── artifacts.py
│   │   │   └── layers.py
│   │   ├── services/
│   │   │   └── cad2gis_runner.py
│   │   ├── models.py
│   │   └── state.py
│   ├── requirements.txt
│   └── Dockerfile
├── runs/                       # 运行时上传与输出（gitignored）
└── docker-compose.yml
```

---

## 4. A 部分：3D 演示系统

### 4.1 视觉基调

- **配色**：浅灰/米白背景 + 深蓝/赭石/深绿强调色，类似蓝图与勘测图。
- **字体**：等宽字体用于数据，无衬线用于标题。
- **布局**：三栏固定（左：阶段导航 / 中：3D 视口 / 右：数据面板），不像营销页全屏滚动。
- **3D 风格**：实体着色 + 柔和阴影 + 明确的比例尺/坐标轴，拒绝霓虹发光与悬浮粒子。

### 4.2 Pipeline 流程视图（工程剖面图风格）

把 CAD2GIS 流程做成一张可交互的工程流程剖面图：

- 每个阶段是一块"钢板"/"图纸托盘"，边缘有工程细节（折角、螺栓）。
- 托盘之间用实体导管连接，导管内流动的不是粒子，而是缩小的数据对象（线段、block、标签）。
- 节点上刻印阶段名称、输入/输出数量、运行时间。
- 点击节点 → 托盘展开，露出该阶段的中间产物表格。

**阶段节点**

| 节点 | 3D 表现 | 下钻内容 |
|---|---|---|
| DWG 输入 | 卷起的 CAD 图纸 | 文件名、SHA-256、图层统计 |
| Census 清点 | 扫描网格 + 实体图标 | 222 INSERT / 170 DIMENSION / 4265 LWPOLYLINE |
| Evidence 证据 | 证据托盘与堆叠卡片 | 候选特征、角色分类、指纹摘要 |
| Topology 拓扑 | 节点-连线图 | 连接关系、支撑关系、容差 |
| Delivery 交付 | 八层 GIS 塔楼 | 每层高度 = 要素数量 |
| QGIS 出图 | 打开的图鉴 | QML/SVG 样式预览 |

### 4.3 GIS 三维勘测视图

把转换出的 GeoPackage 八层数据渲染到带地形的 3D 场景中，风格为勘测级三维剖面：

- 地面用等高线 + 少量纹理表示，强调高程而非视觉奇观。
- 杆路是真实比例的圆柱，材质区分混凝土/钢/既有。
- 光缆沿地面以上 0.5m 悬空布线，显示垂度与跨越关系。
- 每个要素旁边有编号标签和来源 provenance（`DWG_DIRECT`、`DWG_DERIVED:<rule-id>`）。
- 右下角始终显示：CRS、比例尺、当前鼠标坐标、选中要素的 handle。

**图层映射**

| 层 | 3D 表现 |
|---|---|
| PTECH | 圆柱体立柱，顶部带 billboard 图标 |
| CABLE | 沿路径 3D 管线，24C/48C 用不同粗细/颜色 |
| BOITE | 小箱体模型，悬停显示 FAT_ID |
| SITE | 地面 footprint 多边形 + 标签 |
| IMB | homepass footprint 多边形 + 标签 |
| INFRASTRUCTURE | 按需显示 sling/support |
| ZNRO | 空层显示占位提示 |
| ZPM | 边界多边形 |

### 4.4 CAD 分层解剖视图

类似专业 CAD 审查工具：

- 左侧是图层树，复用 DWG 真实图层名。
- 中间是 3D 线框，默认显示全部，选中图层高亮其余淡化。
- 提供剖切平面滑块和正交/透视切换。
- 底部是实体属性表：handle、layer、type、block effective name、transform 摘要。
- 不添加任何"AI 自动标注"动画，只展示原始数据。

### 4.5 统一的证据优先叙事

三个视图共享同一个右侧信息面板，显示当前选中对象的来源链：

```
DWG handle: 1A3F
Census record: INSERT *U13
Evidence candidate: PTECH-0001
Mapping rule: pole_family / effective_name=*U13
Delivery layer: PTECH
Provenance: DWG_DERIVED:pole_family
```

---

## 5. B 部分：Web 转换前端

### 5.1 上传与校验

- 拖拽上传 DWG，显示文件名、大小、SHA-256 计算进度。
- 上传后立即与 `official/` 下的已知 APD 图纸 hash 对比，给出"匹配 / 新图纸"提示。
- 若是新图纸，提示"当前 demo 针对 APD Hutabohu 优化，其他图纸结果可能不完整"。

### 5.2 参数配置面板

参数分组显示，默认从 `experiment/config/` 加载：

| 分组 | 字段 |
|---|---|
| Source Profile | source CRS、calibration GCP profile（可选） |
| Mapping Registry | `apd_mapping_registry.json` 路径/版本 |
| Conversion | source CRS → target CRS（默认 EPSG:3857 → EPSG:4326） |
| Cloud Curation | base URL、model、是否启用（可选） |
| Output | run directory 前缀、是否生成 QGIS 工程 |

每个字段旁边有"来源"标签（配置文件 / 用户覆盖 / 默认）。

### 5.3 任务执行与进度

- 提交后生成 `run_id`，后端调用 `experiment/py_scripts/cad2gis_v3/cli.py`。
- 前端通过 **Server-Sent Events** 接收实时日志行。
- 进度条不虚构百分比，而是显示当前阶段名称 + 已处理实体数。
- 支持取消运行（SIGTERM 子进程）。

### 5.4 结果页面

任务完成后显示：

- **交付摘要**：八层 GIS 要素数量、CRS、边界框。
- **文件下载**：`apd_evidence.gpkg`、`apd_delivery.gpkg`、QGIS 工程压缩包、`run_manifest.json`。
- **验证报告**：调用现有 `official/validation/evaluator.py` 或等效检查，列出通过/警告/错误。
- **一键进入 A 部分**：把本次结果加载到 3D GIS 视图。

---

## 6. 后端 API

```
POST   /api/v1/upload                  # 上传 DWG，返回 upload_id + hash
POST   /api/v1/runs                   # 创建转换任务，返回 run_id
GET    /api/v1/runs/{id}              # 任务状态 + 元数据
GET    /api/v1/runs/{id}/logs         # SSE 日志流
POST   /api/v1/runs/{id}/cancel       # 取消任务
GET    /api/v1/runs/{id}/artifacts/{name}  # 下载产物
GET    /api/v1/runs/{id}/layers       # GeoJSON 摘要（供 A 部分 GIS 视图）
GET    /api/v1/runs/{id}/census-summary    # CAD 分层解剖数据
```

### 6.1 任务状态机

```
pending -> running -> completed
                 -> failed
                 -> cancelled
```

### 6.2 后端服务封装

`services/cad2gis_runner.py` 负责：

1. 接收 run_id、input 路径、参数。
2. 构建 v3 CLI 命令行或 module 调用。
3. 以子进程方式启动，stdout/stderr 逐行推送到 SSE。
4. 子进程退出后检查输出文件完整性。
5. 读取 GeoPackage 生成 GeoJSON 摘要。

---

## 7. 数据流

```
用户上传 DWG
    │
    ▼
FastAPI /upload ──► 保存到 webdemo/runs/{run_id}/input/
    │                计算 SHA-256
    ▼
FastAPI /runs ──► 调用 cad2gis_v3.cli.main() 作为子进程
    │              参数：input, run-dir, source-profile, registry, gcp
    ▼
v3 pipeline 写入 webdemo/runs/{run_id}/output/
    │
    ├── apd_evidence.gpkg
    ├── apd_delivery.gpkg
    ├── qgis/
    ├── run_manifest.json
    └── validation_report.json
    │
    ▼
FastAPI 读取 GeoPackage ──► 生成 GeoJSON / 图层摘要 / census 摘要
    │
    ▼
React 前端加载 ──► A 部分 3D 视图 / B 部分下载列表
```

### 7.1 前端状态管理

- **TanStack Query**：管理后端 API 缓存、轮询任务状态。
- **Zustand**：管理全局 UI 状态（当前选中的 run_id、A 部分当前视图、CAD 图层开关）。
- **SSE 日志流**：按行追加到转换面板，不做复杂状态归约。

### 7.2 后端状态

- 进程级内存字典：`RUNS[run_id] = {process, status, created_at, logs}`。
- 单实例 demo 足够；如需多 worker，可改为 Redis + Celery。
- 文件系统作为唯一持久化：运行目录即状态。

---

## 8. 错误处理与可靠性

| 场景 | 处理 |
|---|---|
| DWG hash 与官方不一致 | 警告但仍允许运行，标记 `non-canonical` |
| v3 pipeline 崩溃 | 捕获 stderr，前端显示"失败" + 日志尾行 |
| 子进程超时 | 30 分钟默认，超时强杀并标记 |
| 输出文件缺失 | 后端 integrity check 失败，任务标记失败 |
| 上传恶意大文件 | 100MB 限制，保存前检查 magic bytes |
| 网络调用被拒绝 | convert 阶段网络 deny guard；curate 可选 |
| 浏览器刷新 | run_id 保留在 URL，可恢复查看 |

---

## 9. 部署与运行

### 9.1 开发模式

```bash
# 后端
cd webdemo/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端
cd webdemo/frontend
npm install
npm run dev
```

### 9.2 生产/演示模式

```bash
docker-compose up --build
```

- `frontend` 容器：Nginx 托管 React 构建产物。
- `backend` 容器：FastAPI + Gunicorn + Uvicorn workers。
- `runs` volume：持久化上传与输出。

### 9.3 离线演示

- 预生成一次 `sample-run/` 放入 `frontend/public/sample-run/`。
- A 部分默认加载 sample-run，无需 AutoCAD/Core Console 也能展示。
- B 部分需要本地有 AutoCAD 2027 Core Console 才能执行真实转换。

---

## 10. 技术依赖

### 10.1 前端

- React 18 + TypeScript
- Vite
- Tailwind CSS
- React Three Fiber + @react-three/drei
- MapLibre GL JS（或 CesiumJS，如需要真三维地形）
- TanStack Query
- Zustand

### 10.2 后端

- Python 3.12
- FastAPI
- Uvicorn
- python-multipart
- aiofiles
- pyproj / shapely / osgeo（复用 v3 环境）

### 10.3 运行时依赖

- AutoCAD 2027 Core Console（执行真实转换）
- v3 pipeline 的 conda/venv 环境

---

## 11. 风险与排除项

| 风险 | 缓解 |
|---|---|
| v3 pipeline 不稳定 | Web 层只封装，不修复；失败时完整暴露日志 |
| AutoCAD 未安装 | B 部分不可用，但 A 部分可用 sample-run 演示 |
| 大文件导致内存/超时 | 100MB 上传限制 + 30 分钟超时 |
| 3D 性能不足 | 对线数据进行采样/简化；按需加载图层 |
| 与执行计划范围冲突 | Web UI 严格隔离在 `webdemo/`，不进入 `src/cad2gis` |

---

## 12. 成功标准

- [ ] `webdemo/frontend` 和 `webdemo/backend` 目录结构清晰，可独立运行。
- [ ] A 部分三个视图（Pipeline / GIS / CAD）均可加载 sample-run 数据展示。
- [ ] B 部分能上传 DWG、配置参数、触发转换、实时查看日志、下载结果。
- [ ] 转换产物可通过"一键进入 A 部分"在 3D GIS 视图中查看。
- [ ] `docker-compose up --build` 能启动完整系统。
- [ ] 不修改 `official/`、`experiment/output/`、`src/cad2gis/` 等受保护目录。
- [ ] 后端通过 integrity check 拒绝不完整输出，失败任务不伪装成功。

---

## 13. 待决策事项

1. 是否使用 CesiumJS 替代 MapLibre GL JS 做 GIS 三维地形？
2. `sample-run/` 是否允许放入 git（较大）还是首次启动时由脚本生成？
3. 是否需要用户登录/权限，还是单用户 demo？
4. 是否需要把 QGIS 工程打包为 zip 下载？
