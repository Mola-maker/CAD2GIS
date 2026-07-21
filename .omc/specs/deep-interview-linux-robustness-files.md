# Deep Interview Spec: robustness 工作区重构（架构知识 + 核心算法 + 闭环比对验证）

## Metadata
- Interview ID: di-20260721-linux-robustness-files
- Rounds: 3 (Round 0 topology confirmed, Round 1 granularity=B, Round 2 dev-reader=A, Round 3 closure=A)
- Final Ambiguity Score: ~22% (early exit; user gave explicit execution directives)
- Type: brownfield (workspace originated from newmodel, treated as independent robustness work area)
- Generated: 2026-07-21
- Threshold: 0.2 (20%)
- Threshold Source: default
- Status: BELOW_THRESHOLD_EARLY_EXIT (user explicitly directed execution path on all 4 components)

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.85 | 0.35 | 0.298 |
| Constraint Clarity | 0.70 | 0.25 | 0.175 |
| Success Criteria | 0.70 | 0.25 | 0.175 |
| Context Clarity | 0.90 | 0.15 | 0.135 |
| **Total Clarity** | | | **0.783** |
| **Ambiguity** | | | **0.217** |

## Topology
| Component | Status | Description | Coverage |
|-----------|--------|-------------|----------|
| goal-definition | resolved-in-R1 | "需要哪些文件" → B粒度 = 跨平台 reader 替换 + Win/Linux 等价性测试 | R1 锁定 |
| dev-reader-path | resolved-in-R2 | `_dev` 前缀/env gate/marker/synthetic 四件套全部去除，LibreDWG 升格为跨平台 primary | R2 锁定 A |
| canonical-carrier | active | ingest.py 加 reader 切换逻辑（env/CLI/profile 选 LibreDWG vs AutoCAD）；autocad_reader.py 标 deprecation 但保留 | R4 待执行细化 |
| infrastructure | resolved-in-R3 | 闭环比对验证用 A 方案：records bundle → pipeline → GPKG 对账 | R3 锁定 A |

## Goal

把当前工作区（git branch `robustness`）从"继承自 newmodel 的混合树"重构为**最大化精简的独立工作区**，仅保留三类内容：

1. **架构知识**：架构文档、设计决策记录、跨平台部署指南、对账口径说明
2. **核心算法**：canonical 代码（pipeline / reader / semantic / topology / calibration / verification / CLI）
3. **闭环比对验证**：契约测试、Win/Linux 跨平台等价性测试、records bundle 驱动的端到端对账测试、对账基线（delivery/evidence GPKG + review bundle）

reader 角色**从 dev-only 升格为跨平台 primary**：`libredwg_dev_reader.py` 改名去 `_dev` 前缀；`cad2gis_v3/ingest_dev.py` 并入主 `ingest.py`（reader 通过 env/CLI/profile 切换）；`autocad_reader.py` 保留但标 deprecation，作为可选 Windows-only fallback。

**工作区定位声明**：本工作区不再是 newmodel 分支的延伸，而是 robustness 独立工作区——继承了 newmodel 的有用文件，但与 newmodel 的演进解耦。后续开发以此工作区为唯一主场；newmodel 网页后端另起工作区，不在本工作区范围内。

## Constraints

### 必须删除
- **原始 DWG**（`experiment/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`，1.7MB）—— 网页后端另有
- **paper/** 整目录（5 个 PDF 参考论文）—— 与代码无关
- **demo/** 整目录（早期 prototype `converter.py`/`converter_3857.py`/`geoformer.py`/`DS_02_GIS.qgz`）—— 历史兼容层
- **official/** 整目录（参考 Shape 文件）—— 不在主线
- **Standard_qgis_projects/** 整目录（153 个标准 QGIS 工程）—— 演示便利
- **main_archive/**（main 分支归档综合分析已结晶为本 spec 的 Goal/Constraints 节，物理文件不需保留）
- **plugincad2gis/**（legacy，.gitignore 已排除）
- **tmp/** `test-artifacts/`（临时/产物）
- **webdemo/runs/**（web 后端工作区）
- **qgis_plugin/cad2gis_plugin/**（薄适配器移至新工作区或并入 docs/）
- **experiment/ErrorReports/**（已 .gitignore，但 tracked 历史需清）
- **experiment/output/**（早期输出）
- **experiment/guide/**（已并入 main_archive 知识）
- **experiment/py_scripts/ErrorReports/**（同上）
- **experiment/py_scripts/converter.py**（legacy converter 92KB，ctypes 桥已并入 reader；其他逻辑已被 cad2gis_v3 包取代）
- **experiment/py_scripts/convert_v3.py** `curate_v3.py` `apd_rules.py` `evaluator.py` `gcp_tool.py` `domain_vocab.py` `schema_config.py`（legacy 脚本；如逻辑已被 cad2gis_v3 包覆盖则删，否则并入 src/）
- **experiment/py_scripts/test_*.py** 中重复/被 v3 测试取代的（保留 7 项契约测试 + 新增 portability + reconciliation）
- **experiment/config/llm_provider.env.example**（LLM provider 配置属 curate，本工作区不涉及）
- **docs/XA-202610...pdf**（比赛方案）
- **docs/superpowers/**（webdemo 相关文档）
- **.omc/state/ .omc/sessions/ .omc/project-memory.json**（transient，gitignore）
- **所有以 `_dev` 命名的文件**（reader/wrapper/profile/report 全部重命名）

### 必须保留并改造
- **src/cad2gis/** canonical 包（17 文件，pipeline/reader/semantic/topology/calibration/verification/cli 等）
- **tests/** canonical 51 项合同测试
- **experiment/py_scripts/cad2gis_v3/** 31 文件 v3 包（已通过 116 项测试）
- **experiment/runs/apd_architecture_v3_complete/** baseline（保留 delivery.gpkg 872KB + evidence.gpkg 28MB + readcad_review_bundle.json 33MB）
- **experiment/runs/apd_architecture_v3_gcp_ready/** baseline
- **experiment/config/apd_source_profile.json** `apd_mapping_registry.json` `apd_gcp_profile.json`（canonical 配置三件套）
- **experiment/APD_HUTABOHU_cad2gis.gpkg**（早期产物，可保留作为对账历史样本）
- **experiment/APD_HUTABOHU_verification.json**（同上）
- **experiment/ARCHITECTURE_V3.md** `experiment/README.md** `experiment/history.md**（合并进 ARCHITECTURE.md / docs/PORTABILITY.md / docs/RECONCILIATION.md）
- **docs/technical_plan.md** `docs/verification_matrix.md** `docs/verification_report.md** `docs/Literature_review_lite.md** `docs/APD_CAD2GIS_EXECUTION_PLAN.md** `docs/APD_CAD2GIS_HANDOFF.md**（精简合并）
- **pyproject.toml** `pyrightconfig.json** `.gitignore**（保留）
- **env/environment.yml**（conda 配置保留，跨平台说明可能并入 docs/PORTABILITY.md）

### 命名统一规则
- 顶层目录：仅 `src/` `verify/` `docs/` `baselines/` `tests/` 五个（按"代码 / 验证 / 文档 / 基线 / 测试"语义），其他全删或并入
- reader 子包：`src/cad2gis/reader/` 统一 reader 实现（libredwg.py + autocad.py + contracts.py）
- 配置文件：`baselines/apd_hutabohu/source_profile.json` `mapping_registry.json` `gcp_profile.json`（去 `apd_` 前缀——只此一个 DWG）
- reader 入口：`src/cad2gis/pipeline.py` `ingest()` 通过 `CAD2GIS_READER_BACKEND=libredwg|autocad` env 切换（默认 libredwg）
- 测试入口：`tests/` 顶层 + `verify/` 闭环比对入口
- 闭环比对：`verify/replay.py`（A 方案 records bundle 驱动） + `baselines/apd_hutabohu/{records,delivery,evidence}/`

### 设计原则
- **`_dev` 前缀全去**：reader/wrapper/profile/report 全部重命名，承认 LibreDWG 升格为跨平台 primary
- **AutoCAD 降级**：保留 `autocad_reader.py` 但标 deprecation；通过 `CAD2GIS_READER_BACKEND=autocad` 显式 opt-in 启用；不在默认路径
- **canonical 边界收缩**：当前 `experiment/py_scripts/cad2gis_v3/ingest.py` 是零触碰边界（合并泄漏构造性消除的产物）；重构后该边界应移至 `src/cad2gis/ingest.py`，reader 切换逻辑封装在 ingest.py 内（不再需要单独 wrapper）
- **合并界面声明**：本工作区与 newmodel 网页后端工作区解耦；本工作区代码不再 merge 回 newmodel；APD baseline 物理文件可被双方引用作为对账标准

## Non-Goals

- newmodel 网页后端开发（另起工作区）
- 真实 GCP 测量数据采集
- AutoCAD 商业读取器新功能开发（仅做 deprecation 标识，不优化）
- conda env.yml 严格对齐（可选核验项，非目标）
- main 分支任何新开发（已归档）
- 第三方 DWG 样本获取（外部资源）

## Acceptance Criteria

### 工作区结构
- [ ] 顶层目录仅 `src/` `verify/` `docs/` `baselines/` `tests/` 五个（外加 `README.md` `pyproject.toml` `pyrightconfig.json` `.gitignore` `env/`）
- [ ] 原始 DWG 文件已从工作区删除（untracked 或 git rm）
- [ ] `paper/` `demo/` `official/` `Standard_qgis_projects/` `main_archive/` `plugincad2gis/` `webdemo/` `qgis_plugin/` `tmp/` `test-artifacts/` `experiment/ErrorReports/` 等目录已物理删除
- [ ] `_dev` 前缀文件全去（reader/wrapper/profile/report）
- [ ] `.omc/state/` `.omc/sessions/` `.omc/project-memory.json` 等 transient 已 gitignore

### 命名统一
- [ ] reader 路径：`src/cad2gis/reader/libredwg.py` `src/cad2gis/reader/autocad.py` `src/cad2gis/reader/contracts.py`
- [ ] 配置路径：`baselines/apd_hutabohu/source_profile.json` 等
- [ ] 测试入口：`verify/replay.py`（A 方案 records bundle 驱动）+ `tests/` 顶层单元测试
- [ ] README 增补节："本工作区是 robustness 独立工作区，与 newmodel 解耦"

### Reader 升格
- [ ] `libredwg_dev_reader.py` → `src/cad2gis/reader/libredwg.py`（rename + 调整 import 路径）
- [ ] `cad2gis_v3/ingest_dev.py` 删除，逻辑并入 `src/cad2gis/ingest.py`（reader 切换通过 env）
- [ ] `extraction_backend` 标记从 `"libredwg_dev"` 改为 `"libredwg"`
- [ ] `CAD2GIS_DEV_READER=1` env gate 去除；LibreDWG 是合法 primary reader
- [ ] synthetic metadata 标记改为 reader 不可读的常规 typed unsupported（去掉特殊 opt-in 开关）
- [ ] `autocad_reader.py` 移到 `src/cad2gis/reader/autocad.py` 并加 deprecation docstring
- [ ] `src/cad2gis/ingest.py` 集成 reader 切换：`CAD2GIS_READER_BACKEND` env（默认 `libredwg`）

### 闭环比对验证（A 方案）
- [ ] `verify/replay.py`：以 `baselines/apd_hutabohu/records/readcad_review_bundle.json` 为输入驱动 pipeline，输出到 `baselines/apd_hutabohu/output/`（不含原始 DWG）
- [ ] `verify/contract/`：7 项契约测试（reader 行为、records 完整性、env 切换、跨平台加载）
- [ ] `verify/portability/`：Win/Linux 等价性测试（OS 检测 + ctypes 跨平台加载 + 输出 schema 一致）
- [ ] `verify/reconciliation/`：replay 输出 vs `baselines/apd_hutabohu/delivery/` `evidence/` GPKG 对账
- [ ] `tests/` 51 项 canonical 合同测试保持通过
- [ ] `tests/` 116 项 v3 测试保持通过
- [ ] A 方案闭环：records bundle → ingest → pipeline → delivery.gpkg → SQL count vs baseline

### 基线保留
- [ ] `baselines/apd_hutabohu/delivery/apd_delivery.gpkg` 保留
- [ ] `baselines/apd_hutabohu/evidence/apd_evidence.gpkg` 保留
- [ ] `baselines/apd_hutabohu/records/readcad_review_bundle.json` 保留（作为 A 方案闭环比对输入）
- [ ] `baselines/apd_hutabohu/config/{source_profile,mapping_registry,gcp_profile}.json` 保留

### 文档精简
- [ ] `docs/ARCHITECTURE.md`：核心架构说明（替代 experiment/ARCHITECTURE_V3.md + main_archive/MAIN_BRANCH_SYNTHESIS.md 的核心内容）
- [ ] `docs/PORTABILITY.md`：跨平台 reader 部署指南（替代 docs/APD_CAD2GIS_EXECUTION_PLAN.md 的相关节）
- [ ] `docs/RECONCILIATION.md`：对账口径说明（替代 docs/verification_matrix.md + docs/verification_report.md 的相关节）
- [ ] 删除 docs/superpowers/ docs/XA-202610...pdf
- [ ] README 主入口：架构图 + 三个目录语义 + reader 升格声明 + 闭环比对入口

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| "需要 dev reader 因为 AutoCAD 是 canonical" | R1 用户澄清 AutoCAD 非必需 | LibreDWG 升格为跨平台 primary，AutoCAD 降级为可选 |
| "_dev 前缀应保留以示历史兼容" | R2 用户选择全部去除 | reader/wrapper/profile/report 全部重命名 |
| "闭环比对需要原始 DWG" | R3 用户选 A 方案 | records bundle（`readcad_review_bundle.json`）替代 DWG 作为闭环比对输入 |
| "工作区是 newmodel 的子分支" | R4 用户澄清 | 工作区独立于 newmodel，是 robustness 主场 |
| "保留 demo/paper/main_archive" | 用户明确删除 | 与代码无关或已结晶入本 spec 的内容全部删除 |
| "QGIS plugin 是核心交付" | 用户指示网页后端另起 | qgis_plugin 移出本工作区或并入 docs/ |

## Technical Context

### newmodel 继承的工作流
- src/cad2gis/ canonical 包 17 文件（pipeline/reader/semantic/topology/calibration/verification/cli）
- experiment/py_scripts/cad2gis_v3/ v3 包 31 文件（已通过 116 项测试）
- experiment/runs/apd_architecture_v3_complete/ baseline（readcad_review_bundle.json 9391 对象元数据 + delivery/evidence GPKG）
- experiment/config/ 三件套（source_profile/mapping_registry/gcp_profile，绑定 SHA-256）
- tests/ 51 项 canonical 合同测试

### reader 升格技术锚点
- 当前：libredwg_dev_reader.py 1162 行（ctypes bridge + dwgread JSON 侧通道 + 191 个匿名 block 名解析）
- 升级后：src/cad2gis/reader/libredwg.py（去 _dev 前缀，调整 import 路径）
- env 切换：CAD2GIS_READER_BACKEND=libredwg|autocad（默认 libredwg）
- AutoCAD fallback：src/cad2gis/reader/autocad.py（deprecation 标识，仍可显式启用）

### A 方案闭环比对技术锚点
- 输入：baselines/apd_hutabohu/records/readcad_review_bundle.json（canonical records bundle）
- 流程：bundle → ingest.from_record() → pipeline → 输出 GPKG
- 对账：SQL count vs baselines/apd_hutabohu/delivery/apd_delivery.gpkg + evidence/apd_evidence.gpkg
- 不依赖原始 DWG；reader 不在此回路（reader 由契约测试 + 等价性测试覆盖）

### 闭环比对 vs reader 验证分层
- **契约层**（reader 行为）：单元测试 mock records → 验证 typed unsupported 契约
- **等价层**（reader 跨平台）：OS 检测 + ctypes 跨平台加载验证
- **闭环层**（pipeline 行为）：A 方案 records bundle → pipeline → GPKG 对账
- **回归层**（canonical 合同）：tests/ 51 项 + cad2gis_v3/ 116 项保持通过

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| robustness 工作区 | core | 独立工作区 / 继承 newmodel / 与 newmodel 解耦 | 与 newmodel 共享历史但演进独立 |
| Reader (LibreDWG) | core | ctypes bridge / extraction_backend="libredwg" / 跨平台 | 是 primary reader，替代 autocad |
| Reader (AutoCAD) | supporting | accoreconsole / Windows-only / deprecation | 可选 fallback，需显式 env 启用 |
| Records Bundle | core | readcad_review_bundle.json / 9391 对象元数据 | A 方案闭环比对的输入 |
| Pipeline | core | ingest → semantic → topology → calibration → output | 不依赖原始 DWG |
| Baseline | core | delivery.gpkg / evidence.gpkg / records bundle | 闭环比对的对照标准 |
| Contract Test | core | 7 项契约 / reader 行为 / typed unsupported | reader 验证的主路径 |
| Portability Test | supporting | OS 检测 / ctypes 加载 / 输出 schema 一致 | 跨平台等价性验证 |
| Reconciliation Test | supporting | records bundle → pipeline → GPKG count | A 方案闭环验证 |
| 架构知识 | supporting | docs/ARCHITECTURE.md / PORTABILITY.md / RECONCILIATION.md | 工作区语义入口 |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 6 | 6 | - | - | N/A |
| 2 | 9 | 3 | 0 | 6 | 67% |
| 3 | 10 | 1 | 0 | 9 | 90% |

## Interview Transcript
<details>
<summary>Full Q&A (3 rounds + Round 0)</summary>

### Round 0（拓扑确认）
**Q:** "需要哪些文件"的顶层组件？
**A:** 4 个 active：goal-definition / dev-reader-path / canonical-carrier / infrastructure

### Round 1（粒度）
**Q:** "需要哪些文件"的粒度选择？
**A:** **B** = 跨平台 reader 替换 + Win/Linux 等价性测试

### Round 2（dev-reader 边界）
**Q:** `_dev` 前缀/env gate/marker/synthetic 四件套怎么处理？
**A:** **A** = 全部去除；LibreDWG 升格为跨平台 primary

### Round 3（闭环比对输入）
**Q:** 删原始 DWG 后闭环比对如何闭环？
**A:** **A** = pre-extracted records baseline（`readcad_review_bundle.json`）

### 用户最终定位
- 工作区是 robustness 独立工作区，与 newmodel 解耦
- 仅保留架构知识 + 核心算法 + 闭环比对验证
- 命名统一 + 旧版本摈弃

</details>

## Execution Notes

本 spec 涉及大规模文件结构重构 + 多文件重命名 + 文档合并 + 基线移动。**不是简单的内容增删**，是工作区形态的重新定义。

执行时需注意：
1. **不要直接删除 tracked 文件**——先用 `git rm` 走 commit 记录历史
2. **重命名要走 `git mv`**——保留 blame 链
3. **`.omc/state/` 等 transient 必须 gitignore**——避免污染下次提交
4. **APD baseline 文件是大对象（28MB GPKG + 33MB JSON）**——保留 tracked 但确保 .gitattributes 正确
5. **canonical 边界测试是回归门槛**——执行期间任何时刻 `pytest tests/` + `pytest verify/` 必须保持基线
6. **AutoCAD 文件保留为 deprecation**——不要删除，仅加注释 + env 显式 opt-in 入口
7. **闭环比对 A 方案要求 `readcad_review_bundle.json` 内容稳定**——bundle 内容变更即视为基线漂移，需重审
</content>
</invoke>