# DSH 插件工业化改进方案与推荐

> 工作区：`CAD2GIS-robustness`
> 文档状态：调研稿（本 session 只写文档，不写代码）
> 目标：利用 DSH 插件生态，把 CAD2GIS 项目已有的确定性/证据优先工程能力，组织成可复现、可审计、可验收的工业化工作流。

---

## 1. 范围与结论

本工作区是一个**证据优先、可重放的 DWG → GIS 转换系统**。工程核心是确定性 Python 流水线，AI 只负责清点、解释、选择证据和组织工作流。

工业标准化诉求可归纳为七类：

1. 证据链与产物血缘可追溯
2. 可复现基准与回归证据
3. 配置、规则、版本可回滚
4. 完成验收与质量门禁
5. 多项目批量编排
6. 安全、权限、凭据与审计
7. 文档、报告、可视化与可观测性

结论：**不重写项目核心，先用 DSH 插件补齐治理层，再把项目已有 `cad2gis-agent` 服务以 MCP/Skill/Native Bundle 三条路线接入 DSH。**

---

## 2. 工作区资产盘点

### 2.1 文件规模

排除 `.venv`、`.git`、`.pytest_cache`、`__pycache__` 后：

| 类型 | 数量 | 说明 |
|---|---:|---|
| `.json` | 129 | 配置、manifest、证据图、审查结果 |
| `.py` | 97 | Python 核心与测试 |
| `.qml` | 88 | QGIS 图层样式 |
| `.png` | 80 | 视觉证据与命中图 |
| `.gpkg` | 29 | GeoPackage 证据/交付/源数据 |
| `.md` | 13+ | 架构与 specs（另有 `.omc` 下 113 篇） |
| `.dwg` | 5 | AutoCAD 源图纸 |
| `.sqlite3` | 4 | 审查会话库 |
| `.log` | 8+ | 运行与 dsh-funnel 日志 |
| 其他 | — | `.qgz`、`.toml`、`.lsp`、`.scr` 等 |

### 2.2 无法直接文本解析的文件：按文件名语义与格式推断领域

| 扩展名 | 格式 | 推断领域 |
|---|---|---|
| `.dwg` | AutoCAD 二进制图纸 | 源数据；由 LibreDWG/AutoCAD reader 解析 |
| `.gpkg` | OGC GeoPackage，SQLite 容器 | source / evidence / delivery 空间数据 |
| `.qgz` | QGIS 工程压缩包 | `V3_Evaluation.qgz`：人工验收与可视化工程 |
| `.qml` | QGIS Layer Style XML | 八类 FTTH 要素的可移植样式 |
| `.sqlite3` | SQLite 数据库 | `run.review/review.sqlite3`：交互式审查状态 |
| `.2345` | 配置文件备份 | `spatial_regions.json.bak.2345`：旧版空间区域配置备份 |
| `.lsp` / `.scr` | AutoLISP / AutoCAD script | AutoCAD 探针脚本，用于实体清点与诊断 |
| `.png` | 栅格图像 | 多尺度 CAD 渲染、实体 ID 命中图、视觉证据 |
| `.qgz/.qml/.gpkg` 家族 | GIS 交换格式 | 交付、样式、验收三件套 |

### 2.3 关键文档摘要

| 文档 | 核心内容 |
|---|---|
| `README.md` | CAD2GIS 证据优先 DWG→GIS 系统；CLI/Python/Web 审查/MCP 共用 canonical pipeline |
| `docs/ARCHITECTURE.md` | 边界、Reader Contract、Plan-Domain Contract、测试分层、精度声明 |
| `docs/LLM_AGENT_ARCHITECTURE.md` | LLM 是流程规划者与语义推理者；CAD 事实、几何、拓扑由确定性核心负责 |
| `docs/REGISTRATION_AND_SCENE_ARCHITECTURE.md` | 场景划分、坐标域准入、GCP 配准、双栏审查、AI 边界 |
| `docs/ROBUSTNESS_VALIDATION.md` | 鲁棒性验证矩阵、reader 生命周期、APD 端到端证据 |
| `docs/RECONCILIATION.md` | 基线 reconciliation、漂移策略 |
| `docs/PORTABILITY.md` | LibreDWG 跨平台 reader、运行时可移植性 |
| `.omc/specs/` | 历史 deep-dive/trace/repair specs，是领域决策和根因的知识库 |
| `.omc/wiki/` | 管线、LibreDWG、会话日志等长期知识 |

### 2.4 现有服务与工程能力

按模块 docstring 归纳（不深入算法）：

| 服务 | 文件 | 工业化意义 |
|---|---|---|
| Canonical CLI | `src/cad2gis/cli.py` | 唯一确定性入口 |
| MCP adapter | `src/cad2gis/agent_mcp.py` | 已有智能体接入面 |
| Doctor | `src/cad2gis/doctor.py` | 依赖与环境体检 |
| Profile / config | `src/cad2gis/profile.py`, `cad2gis_v3/config.py` | 源 SHA-256 绑定配置 |
| Reader | `reader/libredwg.py`, `reader/autocad.py`, `reader/contracts.py` | 跨平台读取，不可变记录契约 |
| 场景/配准 | `coordinate_domain.py`, `calibration.py`, `gcp_workflow.py` | CRS/GCP 权威链 |
| 拓扑/长度 | `topology.py`, `curve_geometry.py`, `units.py` | 源几何不可变 |
| 决策与验证 | `decision_validation.py`, `verify/matrix.py`, `verify/claims.py` | fail-closed 决策 |
| 审查 | `review_server.py` | Web 交互式审查 |
| 可视化证据 | `visual_evidence.py`, `styles.py` | 命中图、QGIS 样式 |
| 交付 | `warehouse.py`, `source_gpkg.py` | 原子化八层 GeoPackage |
| 推理图 | `evidence_graph.json` | 节点/边证据图 |
| 运行 manifest | `run_manifest.json` | 源绑定、实现 SHA、制品清单、策略 |

### 2.5 已有的“类插件”资产

`plugins/cad2gis-agent/` 已是多客户端 agent 插件：

- `.mcp.json`：stdio MCP server
- `clients/*.json`：Claude Code、Cursor、VS Code、streamable-http 配置
- `.claude-plugin/`、`.codex-plugin/`：插件清单
- `skills/convert-cad-to-gis/`：SKILL.md + references + agent YAML

这说明**项目已经把核心服务包装成 agent 能力，但目前缺 DSH 侧入口**。

---

## 3. 工业标准化缺口

对照现有资产，缺口主要在 DSH 运行层：

| 缺口 | 证据 | 需要的 DSH 能力 |
|---|---|---|
| 验收只靠人工约定 | 现有 `pytest` 和 run_manifest，但没有 goal completion gate | 目标完成前强制跑验证命令 |
| 产物血缘是项目内 JSON，DSH 会话外不可见 | `evidence_graph.json`、`run_manifest.json` | DSH 侧 content-addressed lineage ledger |
| 回归基准未版本钉死 | `baselines/*` 大量手工目录 | revision-pinned benchmark 与 compare 报告 |
| 配置变更无回滚 | profile patch 多轮修改，`.2345` 是手工备份 | DSH 配置 snapshot/undo |
| 多图纸批量是人工逐个跑 | `raw/*.dwg` + `baselines/<site>` | waves/lanes 多任务编排 |
| 权限策略较宽 | `~/.dsh/settings.yaml` 为 danger-full-access | 声明式 permission rules |
| 模型行为与工具调用无统一审计 | 仅 session log | trajectory governance / session audit |
| 凭据与安全配置无持续审计 | 全局 settings 中的明文 key 历史 | fleet audit / mcpguard |
| 报告与证据可视化不足 | `evidence_graph` 10MB JSON、PNG | 对话内可视化、markdown/HTML 报告 |

---

## 4. DSH 插件推荐

> 检索方法：`~/.dsh/skills/find-plugins/scripts/search-topic.mjs` 实时扫描 GitHub `dsh-plugin` topic（当前 999 个仓库），并交叉核对 awesome-dsh-plugin registry。
> 所有推荐安装前仍应过 `judge_plugin` / 读源码；本文只给建议，不执行安装。

### 4.1 已经安装的基础插件

| 插件 | 作用 |
|---|---|
| `dsh-web-search-tavily` | Tavily 联网搜索 |
| `dsh-funnel` | 工具输出进上下文前裁剪 |
| `@dsh-plugin/dsh-auxiliary` | compact 走 deepseek-v4-flash 专用路由 |
| `@liustack/modlens` | 图片/截图视觉证据读取 |
| `@dsh-external/dsh-visualize` | 对话内生成式 UI，展示证据卡片 |
| `dsh-balance-plugin` | 余额与用量看板 |
| `dsh-chat-import` | 外部 agent 历史迁移 |
| `dsh-session-doctor` | 会话诊断、解卡、跨会话消息 |
| `dsh-at-file` | `@path` 引用 |
| `dsh-plugin-judge` | 插件装前/装后审计（当前 Web RPC 405，命令/工具可用） |
| `find-plugins` skill | 社区插件发现 |

### 4.2 P0：证据链、质量门、可复现

#### 4.2.1 `dsh-verify-judge`（强烈推荐，最先装）

- 仓库：`zriyox/dsh-verify-judge`
- 当前提交：`7471508302b62be3a6d2411ac33bcffc5211251c`
- 类型：bundle
- 作用：拦截 `update_goal(complete)`，只有配置的验证命令全部退出 0 才允许关闭目标。
- 映射到本项目：

```yaml
- id: verify-judge
  config:
    commands: ["pytest -q", "ruff check"]
    onUndetected: deny
    timeoutMs: 300000
    outputTailChars: 4000
    gateTurnEnd: true
```

安装：

```bash
dsh plugin --profile web add 'github:zriyox/dsh-verify-judge#7471508302b62be3a6d2411ac33bcffc5211251c'
```

#### 4.2.2 `dsh-lineage`（强烈推荐，对齐 evidence-first）

- 仓库：`dongsheng123132/dsh-lineage`
- 当前提交：`bb9932ef5c2533c00697a27825109f1590217453`
- 类型：bundle
- 作用：内容寻址的 artifact / fact / action / report 血缘图；只存 typed id + workspace-relative ref + expected SHA-256，不存聊天文本。
- 映射：把 `run_manifest.json`、`evidence_graph.json`、GPKG、PNG 证据注册为 artifact/fact，后续 agent 修改、验证、报告都可查询 upstream/downstream，verify 失败时退出码区分。
- 安装：

```bash
dsh plugin --profile web add 'github:dongsheng123132/dsh-lineage#bb9932ef5c2533c00697a27825109f1590217453'
```

#### 4.2.3 `dsh-benchmark`（强烈推荐，对齐 baselines）

- 仓库：`dongsheng123132/dsh-benchmark`
- 当前提交：`3c2eedee2ee3cdb26975744bb826a7321288a4d1`
- 类型：bundle
- 作用：固定 runner、固定 case、内容寻址报告的确定性 benchmark；`run` / `compare` / `inspect`。
- 映射：把 `baselines/<site>/run_manifest.json` 和 `tests/` 固化为基准 manifest；每次流水线改动跑 compare，只接受达到阈值的回归证据。
- 安装：

```bash
dsh plugin --profile web add 'github:dongsheng123132/dsh-benchmark#3c2eedee2ee3cdb26975744bb826a7321288a4d1'
```

#### 4.2.4 `dsh-backup` 与 `dsh-undo-plugin`

- `xiaoyuyu6420/dsh-backup`：备份/恢复 DSH 用户数据，校验、轮转、定时。
- `lire1131/dsh-undo-plugin`：plugin/skin/settings 配置 snapshot 与 undo/redo；DSH 起不来时还有离线 CLI。
- 映射：profile 和 session 是工业配置资产，不能只靠手工 `.bak.2345`。
- 安装前读 README，安装命令示例：

```bash
dsh plugin --profile web add github:xiaoyuyu6420/dsh-backup
dsh plugin --profile web add github:lire1131/dsh-undo-plugin
```

### 4.3 P1：治理、权限、审计

#### 4.3.1 `PerryLink/dsh-permission-rules`

Claude Code 风格声明式权限规则：allow / deny / ask + glob/regex + workspace-path，在 `tools/pre-execute` 上生效，并写 session-log audit。适合把当前 `danger-full-access` 收紧为“工作区写 + 关键命令审批”。

#### 4.3.2 `LeslieWylie/dsh-fleet-audit`

只读凭据卫生审计：credentials 文件权限、git remote 内嵌凭据、provider token 字面量计数。对齐本项目历史上明文 key 的教训。

#### 4.3.3 `ChenLaoshiYF/dsh-mcpguard`

扫描 skill 与 MCP 配置中的提示注入、同形字、Unicode 隐形字符、危险 shell、凭据泄露。在接入 `cad2gis` MCP 和其他第三方 MCP 前执行。

#### 4.3.4 `dfycaly98931680/dsh-trajectory-governance`

把平铺 session log 重建为多分支轨迹树，识别死锁/无效重试/目标漂移，带成本归因与 Web Tab。适合长任务转换与审查流程。

#### 4.3.5 `bwndlct/dsh-session-audit`

会话执行分析：步骤、工具调用、失败、重复动作、token 用量、验证信号，输出 Markdown/JSON。可直接作为“每轮验收记录”的补充。

### 4.4 P2：编排与可观测性

#### 4.4.1 `february2015/dsh-taskswarm`

- 当前提交：`985826c929b292fcf32f351429bd2413ac576ca2`
- 类型：bundle（0.2.22）
- 作用：任务 DAG 分层为 waves，每个 lane 在独立 git worktree 并行执行，自动 review 与 merge。
- 映射：`raw/` 下四个 APD 项目（hutabohu / lamteh_main / lamteh_sf / kletek）天然是四个 task packet，可并行完成 source inventory → decision pack → convert → review。
- 安装：

```bash
dsh plugin --profile web add 'github:february2015/dsh-taskswarm#985826c929b292fcf32f351429bd2413ac576ca2'
```

#### 4.4.2 `linyp/dsh-plugin-langfuse`

把 DSH 会话导出为 OpenTelemetry trace trees（GenAI semconv）到 Langfuse。适合在工业部署中做跨项目模型调用审计。

#### 4.4.3 `omdsh-dev/dsh-advisor`

按会话运行的副模型评审器，注入 nit/concern/blocker 建议。建议只在有第二模型预算、且不希望仅靠规则 gate 时启用。

#### 4.4.4 `hyqhyq3/dsh-mcp-manager`

Web 设置面板管理 MCP server。接入 `cad2gis` MCP 后，用于可视化启停、编辑配置。

### 4.5 暂不推荐

- 自进化类：`timwhitez/dsh-self-evolving`、`dsh-continual-evolve`：机制重，先观察。
- 娱乐/桌宠/皮肤：与本项目工业目标无关。
- 重复的市场/管理面板：已有 find-plugins、judge 和官方插件页，避免重复安装。
- `dsh-plugin-judge` Web RPC 405 是当前兼容问题；继续用 `/plugin-audit` 和 `judge_plugin`。

---

## 5. 把项目已有服务转写为 DSH 插件

### 5.1 现状

`plugins/cad2gis-agent` 已经完成了三件事：

1. 定义 agent 工作流：`skills/convert-cad-to-gis/SKILL.md`
2. 暴露 MCP server：`python -m cad2gis.agent_mcp`
3. 支持 Claude Code / Codex / Cursor / VS Code

DSH 侧缺三样东西：

- DSH 可发现的 skill
- DSH profile 中的 MCP 挂载
- 可选的原生 DSH bundle（tool/command/settings）

### 5.2 Phase 0：零代码接入（推荐先做）

#### Step A：复制 skill 到 DSH 全局技能根

```bash
mkdir -p ~/.dsh/skills/convert-cad-to-gis
cp -a plugins/cad2gis-agent/skills/convert-cad-to-gis/.   ~/.dsh/skills/convert-cad-to-gis/
```

Web 和 TUI 的 standard/code preset 都会发现 `~/.dsh/skills`。

#### Step B：用 `@deepseek-ai/dsh-mcp-client` 挂载现有 MCP

在 `~/.dsh/profiles/web/cordis.patch.yml`（以及需要的 TUI profile）中加入：

```yaml
- insert:
    - id: mcp-cad2gis
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: cad2gis
        transport: stdio
        command: conda
        args:
          - run
          - --no-capture-output
          - -n
          - cad2gis
          - python
          - -m
          - cad2gis.agent_mcp
          - --transport
          - stdio
        env:
          CAD2GIS_PROJECT_ROOT: !!js process.cwd()
```

工具会以 `mcp__cad2gis__*` 形式出现。

#### Step C：验证

```bash
dsh --profile web --dump-config | grep -A15 mcp-cad2gis
```

然后在 standard/code 新会话中要求模型调用 `mcp__cad2gis__*` 工具做一次 doctor/inspect。

### 5.3 Phase 1：原生 DSH bundle（需要 code 模式实现）

建议包名：`@cad2gis/dsh-cad2gis`，只做薄封装，不迁移算法。

| 模型工具 | 底层调用 |
|---|---|
| `cad2gis_doctor` | `python -m cad2gis.doctor` |
| `cad2gis_inspect` | reader inventory / source inspection |
| `cad2gis_build_plan` | plan-domain / decision pack 生成 |
| `cad2gis_convert` | canonical pipeline |
| `cad2gis_review` | review server / review bundle |
| `cad2gis_verify` | verification matrix |
| `cad2gis_reconcile` | baseline reconciliation |
| `cad2gis_evidence_graph` | 读取/查询 evidence graph |
| `cad2gis_visual_evidence` | 渲染命中图 |
| `cad2gis_run_status` | run manifest 状态 |

插件声明 `dsh.bundle.patch`，并带 Web settings section：项目目录、conda env、reader 选择、GCP 开关、验证命令。所有执行走 CLI，避免在插件内复制 Python 逻辑。

### 5.4 Phase 2：Web 审查集成

现有 `review_server.py` 是独立 Web 服务。两种方式：

1. 保守：MCP 工具给出 `mcp_url`，agent 打开外部 review URL。
2. 进阶：DSH bundle 在 host 侧用 `webServer` 注册 `/cad2gis/review` 代理，或复用官方 dsh-mcp-client streamable-http transport 连接 `http://127.0.0.1:8768/mcp`。

本阶段留给 code 模式评估，不在本 session 实现。

---

## 6. 标准化实施路线图

| 阶段 | 动作 | 负责 |
|---|---|---|
| P0-A | 安装 verify-judge、lineage、benchmark、backup、undo | 后续 DSH 会话 |
| P0-B | 将 `~/.dsh/profiles/web/cordis.patch.yml` 和 `package.json` 复制进仓库 `config/dsh/web/`，新增 bootstrap 脚本 | code 模式 |
| P0-C | 复制 cad2gis skill 到 `~/.dsh/skills`，挂载 MCP | 本 session 可指导，安装由后续会话执行 |
| P1-A | 配置 permission-rules，收紧 `danger-full-access` | code 模式 |
| P1-B | 跑 fleet-audit、mcpguard，清理凭据 | code 模式 |
| P1-C | 对 `baselines/<site>` 建立 dsh-lineage artifact registry 与 dsh-benchmark manifests | code 模式 |
| P2-A | 用 taskswarm 跑四项目批量转换试点 | code 模式 |
| P2-B | 评估 Langfuse / infra-observability | 运维 |
| P2-C | 实现 `@cad2gis/dsh-cad2gis` 原生 bundle | code 模式 |

### 6.1 推荐的新会话 preset

以下工作必须在 **standard 或 code preset** 的新会话中执行，因为当前 minimal preset 没有 compaction、goal tools 与完整 workflow：

```text
standard：常规安装、配置、审查
code：批量转换、多步验证、生成 DSH bundle 代码
```

---

## 7. 交接给 code 模式的决策记录

- DSH-001：DSH 侧只允许薄封装 CLI / MCP，禁止在插件内重写 reader、拓扑、校准、验证算法。
- DSH-002：所有 source-bound 配置继续以 `source_sha256` 为第一身份；插件不得按 DWG 文件名分支。
- DSH-003：DSH 运行产生的 profile/config 变更必须纳入 Git，`~/.dsh` 不作为唯一真源。
- DSH-004：新增 DSH 插件前必须过 `judge_plugin`，安装后保留版本、commit、审查记录。
- DSH-005：任何“验收完成”必须经过 verify-judge 的确定性命令，不能只由模型声称完成。
- DSH-006：证据、交付、报告必须进入 lineage；只认 SHA-256 内容寻址，不认路径。
- DSH-007：不要在 host 会话内先杀后启；重启 3080 前备份 session 并做 seq 体检。

---

## 8. 风险与边界

- 社区插件质量波动大，很多同一天发布。P0 插件均需先锁 commit、读源码、隔离验证。
- `dsh-plugin-judge` Web RPC 当前 405，只影响设置面板，不影响 `/plugin-audit` 和 `judge_plugin`。
- 之前出现的 session seq 重复问题根因方向是 DSH rc.6 恢复竞态；任何重启前必须备份 session 文件。
- `taskswarm` 使用 git worktree 并行隔离，适合四个独立 APD 项目；不要在同一工作树并行修改同一套 baseline。
- MCP 挂载的 `conda run` 依赖本机 conda env `cad2gis`；换机器时需把环境定义与 `env/environment.yml` 一并版本化。
