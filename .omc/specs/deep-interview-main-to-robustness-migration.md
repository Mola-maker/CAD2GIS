# Deep Interview Spec: main → robustness 迁移文件表

## Metadata
- Interview ID: 288e00ba-baa8-46b4-8acf-af9e4e32b3b2
- Rounds: 6 (Round 0 + 5)
- Final Ambiguity Score: 13.7%
- Type: brownfield
- Generated: 2026-07-28
- Threshold: 0.2 (20%)
- Threshold Source: default
- Initial Context Summarized: no
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.85 | 0.35 | 0.298 |
| Constraint Clarity | 0.90 | 0.25 | 0.225 |
| Success Criteria | 0.85 | 0.25 | 0.213 |
| Context Clarity | 0.85 | 0.15 | 0.128 |
| **Total Clarity** | | | **0.864** |
| **Ambiguity** | | | **13.6%** |

## Topology

| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| Migration file inventory | active | 从 main 拉取到 robustness 的具体文件清单，分类为新增/覆盖/删除，按能力分组 | 三类文件表已产出 |
| Conflict resolution strategy | active | main 全量覆盖 robustness 算法代码；`.omc/` 保留；`cad_common.py` 和 `verify/replay.py` 审查后确认可删除 | 合并规则已定 |
| .omc/ preservation | active | 54 wiki 页面、specs、plans、sessions、state、project-memory 完整保留不动 | 保留清单已确认 |

## Goal

将 main 分支上经过 196 tests 验证的生产级代码（确定性数据平面、AI 控制平面、Web 审查界面、测试层、工具链、文档、插件系统）全量迁移到 robustness 分支。迁移后使用 `/raw/` 中的 4 张异构 DWG 全部走通 `bootstrap → validate → convert` 流程，生成完整的 `source.gpkg + evidence.gpkg + delivery.gpkg + run_manifest.json + run_status`。大模型监督暂不检验。

## Constraints

1. **main 全量覆盖 robustness 算法代码**：所有 main 与 robustness 共有的源文件，以 main 版本为准
2. **保留 `.omc/` 全部内容**：54 wiki 页面、specs、plans、sessions、state、project-memory 不得被覆盖或删除
3. **保留 `raw/` 目录**：4 张异构 DWG 源文件不动
4. **保留 `baselines/` 目录**：已有基线数据不动（apd_hutabohu、lamteh_main、lamteh_sf 等）
5. **LLM Agent/MCP 代码迁入但不验证**：agent_mcp.py、plugins/、onboarding.py 等 AI 控制平面代码照常迁入，但验收不检验其运行
6. **只做文件迁移方案，不实际执行迁移**：本次产出为迁移文件表（spec），实际 git 操作留待用户批准后执行

## Non-Goals

- 不在 robustness 分支上实际执行 `git merge` 或 `git cherry-pick`
- 不修改 `.omc/` 中的任何文件
- 不在此次迁移中验收 LLM provider 调用或 MCP 服务
- 不修改 main 分支
- 不迁入 main 上的 `.omc/` 相关文件（main 已清理掉的历史 specs/plans/wiki 不复制到 robustness）

## Acceptance Criteria

- [ ] 迁移文件表覆盖所有 main 相对 robustness 的差异文件（新增、修改、删除）
- [ ] 新增文件按能力分组：数据平面、控制平面、Web 审查、测试、工具链、文档、插件、实验数据
- [ ] 覆盖文件明确标注 main 版本直接覆盖
- [ ] 删除文件逐项说明删除理由
- [ ] 保留文件（.omc/、raw/、baselines/）在表中标记为"不迁移/保留"
- [ ] 迁移后 4 张 DWG 全部完成 `bootstrap → validate → convert`
- [ ] 每张 DWG 输出目录包含 `source.gpkg`、`evidence.gpkg`、`delivery.gpkg`、`run_manifest.json`、`run_status`

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| "三个能力都迁"意味着全量迁移 | Contrarian Round 4: 反问题——如果 robustness 特有文件要不要删 | 逐文件审查：`cad_common.py` 职责被 main reader 内联覆盖，`verify/replay.py` 被 accounting+run_status 替代，均可删除 |
| "跑通"等同于 inspect 不丢行 | Round 5: 追问验收精确定义 | 必须走完完整 bootstrap→validate→convert，产出 5 类文件 |
| main 的 LLM 能力需要验证 | Round 2: 质疑大模型是否需要检验 | 明确排除：大模型监督暂不检验 |

## Technical Context

### 分支差异统计
- main 领先 robustness 8 个 commits
- 186 files changed: +17,828 / -7,686 lines
- robustness 当前 HEAD: `67cc4a3`

### 迁移文件分类

#### A. 新增文件（来自 main，robustness 不存在）— 58 个

**确定性数据平面 (cad2gis_v3/):**
| 文件 | 行数 | 用途 |
|------|------|------|
| `src/cad2gis/cad2gis_v3/accounting.py` | - | 终端实体会计 |
| `src/cad2gis/cad2gis_v3/coordinate_domain.py` | - | WCS 坐标域与 CRS 区域一致性门 |
| `src/cad2gis/cad2gis_v3/decision_executor.py` | - | 注册操作确定性执行器 |
| `src/cad2gis/cad2gis_v3/decision_validation.py` | - | 独立几何/拓扑/长度验证 |
| `src/cad2gis/cad2gis_v3/evidence_graph.py` | - | 内容寻址证据图 |
| `src/cad2gis/cad2gis_v3/geometry_repairs.py` | - | 受约束网络修复候选 |
| `src/cad2gis/cad2gis_v3/onboarding.py` | - | Source-bound AI onboarding 合约 |
| `src/cad2gis/cad2gis_v3/plan_domain.py` | - | 不可变库存+块递归展开+仿射变换 |
| `src/cad2gis/cad2gis_v3/repair_decisions.py` | - | 修复决策注册表 |
| `src/cad2gis/cad2gis_v3/run_status.py` | - | 5 态运行状态 |
| `src/cad2gis/cad2gis_v3/scene_partition.py` | - | Geometry-first 场景分区 |
| `src/cad2gis/cad2gis_v3/source_dependencies.py` | - | 外部参考依赖类型图 |
| `src/cad2gis/cad2gis_v3/source_gpkg.py` | - | 源实体 → GeoPackage 发布 |
| `src/cad2gis/cad2gis_v3/visual_evidence.py` | - | 视觉证据渲染+hit map |

**AI 控制平面:**
| 文件 | 行数 | 用途 |
|------|------|------|
| `src/cad2gis/agent_mcp.py` | - | MCP 服务入口（17 tools） |

**Web 审查界面:**
| 文件 | 行数 | 用途 |
|------|------|------|
| `src/cad2gis/review_server.py` | 1328 | OpenLayers 双地图审查后端 |
| `src/cad2gis/webdemo/app.js` | 484 | 审查前端逻辑 |
| `src/cad2gis/webdemo/index.html` | 109 | 审查前端页面 |
| `src/cad2gis/webdemo/styles.css` | 10 | 审查前端样式 |

**插件系统 (plugins/cad2gis-agent/):**
| 文件 | 用途 |
|------|------|
| `plugins/cad2gis-agent/.claude-plugin/plugin.json` | Claude Code 插件配置 |
| `plugins/cad2gis-agent/.codex-plugin/plugin.json` | Codex 插件配置 |
| `plugins/cad2gis-agent/.mcp.json` | MCP 配置 |
| `plugins/cad2gis-agent/README.md` | 插件文档 |
| `plugins/cad2gis-agent/clients/README.md` | 客户端配置说明 |
| `plugins/cad2gis-agent/clients/claude-code.mcp.json` | Claude Code MCP 模板 |
| `plugins/cad2gis-agent/clients/codex.config.toml` | Codex 配置模板 |
| `plugins/cad2gis-agent/clients/cursor.mcp.json` | Cursor MCP 模板 |
| `plugins/cad2gis-agent/clients/streamable-http.json` | 通用 HTTP MCP 模板 |
| `plugins/cad2gis-agent/clients/vscode.mcp.json` | VS Code MCP 模板 |
| `plugins/cad2gis-agent/scripts/cad2gis_mcp.py` | MCP stdio 入口 |
| `plugins/cad2gis-agent/skills/convert-cad-to-gis/SKILL.md` | 工作流约束 |
| `plugins/cad2gis-agent/skills/convert-cad-to-gis/agents/openai.yaml` | OpenAI 代理配置 |
| `plugins/cad2gis-agent/skills/convert-cad-to-gis/references/decision-contract.md` | 决策合约 |

**新增测试 (tests/):**
| 文件 | 行数 | 用途 |
|------|------|------|
| `tests/data/apd_test_manifest.json` | 45 | APD 测试清单 |
| `tests/test_ai_onboarding.py` | 287 | AI onboarding 测试 |
| `tests/test_apd_test_compatibility.py` | 178 | 真实 DWG 兼容测试 |
| `tests/test_baseline_reconciliation.py` | 64 | 基线对账测试 |
| `tests/test_calibration_policy.py` | 55 | 校准策略测试 |
| `tests/test_coordinate_domain.py` | 46 | 坐标域测试 |
| `tests/test_llm_decision_core.py` | 911 | LLM 决策核心测试 |
| `tests/test_llm_providers.py` | 134 | LLM provider 测试 |
| `tests/test_mcp_stdio.py` | 76 | MCP stdio 测试 |
| `tests/test_plan_domain.py` | 308 | Plan-domain 测试 |
| `tests/test_reader_capabilities.py` | 592 | Reader 能力隔离测试 |
| `tests/test_review_server.py` | 202 | 审查服务测试 |
| `tests/test_run_status.py` | 269 | 运行状态测试 |
| `tests/test_scene_partition.py` | 93 | 场景分区测试 |
| `tests/test_source_dependencies.py` | 42 | 源依赖测试 |
| `tests/test_source_gpkg.py` | 316 | Source GPKG 测试 |
| `tests/test_source_inspection.py` | 86 | 源检查测试 |
| `tests/test_terminal_accounting.py` | 151 | 终端会计测试 |
| `tests/test_visual_evidence.py` | 156 | 视觉证据测试 |

**工具链 (tools/):**
| 文件 | 用途 |
|------|------|
| `tools/build_reproducible_wheel.py` | 可复现轮子构建 |
| `tools/diagnostics/autocad_inventory_probe.lsp` | AutoCAD 库存探针 |
| `tools/diagnostics/autocad_inventory_probe.scr` | AutoCAD 脚本探针 |
| `tools/diagnostics/installed_mcp_probe.py` | MCP 安装探测 |
| `tools/diagnostics/plan_domain_probe.py` | Plan-domain 独立探针 |

**新增文档:**
| 文件 | 用途 |
|------|------|
| `docs/LLM_AGENT_ARCHITECTURE.md` | LLM Agent 架构（控制平面/数据平面双层设计） |
| `docs/REGISTRATION_AND_SCENE_ARCHITECTURE.md` | 场景分区+配准+审查架构 |
| `docs/ROBUSTNESS_VALIDATION.md` | 2026-07-26 完整验证报告 |

**实验/官样数据:**
| 文件 | 用途 |
|------|------|
| `experiment/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg` | Hutabohu 实验图 |
| `experiment/README.md` | 实验说明 |
| `experiment/config/apd_source_profile.json` | 源配置 |
| `experiment/config/apd_mapping_registry.json` | 映射注册表 |
| `experiment/config/apd_gcp_profile.json` | GCP 配置 |
| `official/AGA-Al Baraka TR2.dwg` | 官样图 |

#### B. 覆盖文件（main 版本覆盖 robustness）— 32 个

| 文件 | 覆盖理由 |
|------|----------|
| `README.md` | main 正式版中英文文档替换旧版简介 |
| `docs/ARCHITECTURE.md` | main 新 workspace layout + reader elevation |
| `docs/PORTABILITY.md` | main 更新 |
| `docs/RECONCILIATION.md` | main 更新 |
| `pyproject.toml` | main 新 extras (mcp, review, review-postgis) + v3 backend 声明 |
| `src/cad2gis/__init__.py` | main 版本 |
| `src/cad2gis/cad2gis_v3/calibration.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/config.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/curation_providers/config.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/implementation.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/ingest.py` | main 新 canonical boundary |
| `src/cad2gis/cad2gis_v3/pipeline.py` | main 重写 |
| `src/cad2gis/cad2gis_v3/ports.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/project_profile.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/schema_config.py` | main 新 contract 基于 |
| `src/cad2gis/cad2gis_v3/semantics.py` | main 重写 |
| `src/cad2gis/cad2gis_v3/topology.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/units.py` | main 增强 |
| `src/cad2gis/cad2gis_v3/warehouse.py` | main 增强 |
| `src/cad2gis/cli.py` | main 新增 auto-convert、--llm observe/assist、review 子命令 |
| `src/cad2gis/gcp_workflow.py` | main 增强 |
| `src/cad2gis/ingest.py` | main 新 canonical boundary |
| `src/cad2gis/pipeline.py` | main 重写 |
| `src/cad2gis/reader/autocad.py` | main reader lifecycle 修复 |
| `src/cad2gis/reader/contracts.py` | main 增强 |
| `src/cad2gis/reader/records_adapter.py` | main 增强 |
| `src/cad2gis/runtime.py` | main 增强 |
| `src/cad2gis/verify/__init__.py` | main 版本 |
| `src/cad2gis/verify/matrix.py` | main 扩展 |
| `tests/test_canonical_cli.py` | main 扩展 |
| `tests/test_crosscad_contracts.py` | main 扩展 |
| `tests/test_gcp_workflow.py` | main 版本 |
| `tests/test_verification_matrix.py` | main 扩展 |

#### C. 删除文件（robustness 有但 main 没有，确认可删）— 6 个

| 文件 | 删除理由 |
|------|----------|
| `src/cad2gis/cad_common.py` | 7 个共享函数（`_cstr`、ACI 调色板、`_chord_length`、`_centroid`、`_flush_cursor`）已在 main 的 `reader/libredwg.py` 中内联；ACI 颜色改用 `ezdxf.colors.aci2rgb` |
| `verify/replay.py` | 硬编码绑定 `apd_hutabohu` 的回放对账脚本，职责被 main 的 `accounting.py`（行级会计）+ `run_status.py`（completion marker）+ `test_baseline_reconciliation.py` 覆盖 |
| `verify/contract/test_libredwg_reader.py` | main 已删除（由 `test_reader_capabilities.py` 替代） |
| `verify/portability/test_cross_platform.py` | main 已删除 |
| `verify/reconciliation/test_records_loop.py` | main 已删除 |
| `src/cad2gis.egg-info/` | main 不再纳入版本控制 |

#### D. 保留文件（robustness 独有，不参与迁移）— 不操作

| 路径 | 保留理由 |
|------|----------|
| `.omc/` 全部内容 | 用户明确要求保留（54 wiki + specs + plans + sessions + state + project-memory） |
| `raw/` 4 张 DWG | 异构验证的源文件 |
| `baselines/` 已有基线 | apd_hutabohu、lamteh_main、lamteh_sf、hutabohu_live 等 |
| `baselines/hutabohu_live/` (untracked) | 留存 |
| `baselines/lamteh_main/output/` (untracked) | 留存 |
| `baselines/lamteh_sf/output/` (untracked) | 留存 |

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| MigrationFileTable | core domain | category, files, operation(add/modify/delete) | contains MigrationCategory |
| MigrationCategory | core domain | name, file_count | belongs to MigrationFileTable |
| MainBranch | external system | commits, test_count(196) | source of migration |
| RobustnessBranch | core domain | head_commit, .omc_size(54) | target of migration |
| DWGSource | core domain | name, size, path | used in SuccessCriteria |
| ConversionOutput | core domain | source_gpkg, evidence_gpkg, delivery_gpkg, manifest, status | produced by Pipeline |
| Pipeline | core domain | bootstrap, validate, convert | processes DWGSource → ConversionOutput |
| OMCArtifacts | supporting | wiki_pages(54), specs, plans, sessions | preserved during migration |
| DeletedFile | supporting | name, reason | inverse of MigrationFileTable |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 8 | 8 | - | - | N/A |
| 2 | 9 | 2 | 0 | 7 | 78% |
| 3 | 9 | 0 | 1 | 8 | 89% |
| 4 | 9 | 0 | 0 | 9 | 100% |
| 5 | 9 | 0 | 0 | 9 | 100% |

## Interview Transcript

<details>
<summary>Full Q&A (6 rounds)</summary>

### Round 0 (Topology)
**Q:** 3 个顶层组件：迁移文件清单、冲突合并策略、.omc/ 保留计划。拓扑确认？
**A:** 是的（强中文约束）

### Round 1
**Q:** 三个能力（数据平面、控制平面、Web 审查）中想拉取哪几类？
**A:** 在审查过均能在本机环境运行的情况下，三个能力都要迁移

### Round 2
**Q:** 迁移后怎么验证成功？跑哪些测试？
**A:** 本分支源测试文件在 /raw/ 中，main 只跑过 hutabohu。迁移后四个文件全部重新跑通，大模型监督暂不检验

### Round 3
**Q:** 两边都修改过的文件，合并策略？
**A:** 本分支算法全部由 main 分支替代

### Round 4 (Contrarian)
**Q:** robustness 有但 main 没有的文件（cad_common.py、verify/replay.py），删还是留？
**A:** 审查后 cad_common.py 职责已被 main reader 内联覆盖，verify/replay.py 被 accounting+run_status 替代，可删除

### Round 5
**Q:** "跑通"的具体定义？
**A:** 每张图都要走完 bootstrap → validate → convert 生成 source.gpkg + evidence.gpkg + delivery.gpkg + run_manifest.json + run_status

</details>
