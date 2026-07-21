# Deep Dive Spec: newmodel 鲁棒性提升（本机 WSL2，无 win 端附件）— robustness 分支与工作包

## Metadata
- Interview ID: dd-20260721-newmodel-robust-local
- Trace: deep-dive-trace-newmodel-robustness-local-no-win.md（含实证探针）
- Rounds: 6 (+Round 0 拓扑确认)
- Final Ambiguity Score: 11.5%
- Type: brownfield / Threshold: 0.2 / Threshold Source: default / Status: PASSED
- Generated: 2026-07-21

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.90 | 0.35 | 0.315 |
| Constraint Clarity | 0.88 | 0.25 | 0.220 |
| Success Criteria | 0.85 | 0.25 | 0.213 |
| Context Clarity | 0.92 | 0.15 | 0.138 |
| **Total Clarity** | | | **0.886** |
| **Ambiguity** | | | **0.115** |

## Topology
| Component | Status | Description | Coverage |
|-----------|--------|-------------|----------|
| 可行性结论确认 | resolved-in-R1 | 原始定界（85-95%）被用户拒绝 → 鲁棒性必须含真实 ingest 相关 | R1 转向 |
| 中间地带决策 | active | 真实 DWG 读取本机化机制 | R2 决策：LibreDWG dev-reader |
| 范围与优先级 | active | 鲁棒性工作包 | R3：A+B+C+D 全选 |
| 工作区重构 | active | robustness 分支形态 | R4-R6：分支+.omc入库+轻裁剪 |

## Goal

在本机 WSL2（不安装任何 win 端附件）建立并完成 newmodel 鲁棒性提升工作，载体为新建的 **`robustness` 分支**（从 newmodel `ff41501` 拉出）。两大目标：

1. **工作区重构**：robustness 分支 = newmodel 全量树（轻裁剪）+ main 的 `.omc` 知识库（提交入库）
2. **鲁棒性提升四工作包**（全部本机可执行）：
   - **A. LibreDWG dev-reader + APD 重放**：实现 v3 reader 契约（`extract_dwg_records` → records + compatibility diagnostics）的 Linux 后端，复用 main 分支久经考验的 ctypes 桥；CAD2GIS_BACKEND_PATH 插入，**非 canonical**；LibreDWG 局限（R2018 UTF-16/HATCH）转化为 v3 typed unsupported 记录；APD DWG 端到端重放并与 run bundle 基线对账
   - **B. main 转移资产落地 + 拓扑门控**：图层正则/LABEL_FAMILIES/负证据层/8 份验证规则 CSV 接入 newmodel 验证体系；图例检测、匈牙利标注、三轨样式、跨度注记以参考思路落地；拓扑门控强化（legend/title 排除完备性、crossing 类型分离）
   - **C. GCP 框架 + 验证矩阵**：gcp adapter/status/diagnose/export 完善与测试；verify matrix schema/claim ladder 扩充（真实 GCP 采集与新 DWG 样本属外部资源，不在本包）
   - **D. UX + 测试扩充**：doctor/安装引导/错误边界；本机全链路回归测试套件扩充

### 分工界面（用户确认）
- **用户（本机/robustness 分支）**：通过不同 DWG 数据验证 + 改进准确度
- **组员（Windows/newmodel 分支）**：延申结构写 webUI + 后端；AutoCAD canonical ingest；真实 GCP 与新 DWG 样本的外部获取

## Constraints

- dev-reader **永不成为 canonical 生产读取器**：交付权威仍属 AutoCAD 路径；dev-reader 产物的 `extraction_backend` 必须显式标记（如 `libredwg_dev`），仅用于开发/重放/测试
- 裁剪力度=**轻裁剪**：仅删可再生垃圾（`.pytest-tmp-*`/`build/`/`ErrorReports*/`，459+ 文件 ~25MB）+ 旧 run bundles（保留 `apd_architecture_v3_complete` 与 `apd_architecture_v3_gcp_ready` 两个基线，省 ~250MB）；`demo/`、`official/`、`paper/`、`Standard_qgis_projects/`、`tmp/` **保留不删**（避免删除传播到 newmodel 影响组员）
- `.gitignore` 补防：`.pytest-tmp-*`、`build/`、`ErrorReports/` 防止垃圾再积累
- `.omc` 复制并**提交入 robustness 分支**（知识库随分支版本化）
- 本机环境：venv(--system-site-packages)+系统 GDAL 3.8.4 已实证可行（51/51+281/282 测试）；conda 严格对齐 env.yml（GDAL 3.10/PROJ 9.8）列为**可选核验项**而非前提
- 与 newmodel 的合并界面：robustness 的验证/准确度改进经组员评审后合并回 newmodel；删除操作仅限轻裁剪清单

## Non-Goals

- 真实 GCP 数据采集（外部测量资源）
- 新 DWG 样本获取（外部资源；读取能力由 dev-reader 解决，样本本身需外部提供）
- webUI/后端开发（组员工作）
- LibreDWG reader 升级为 canonical / 替代 AutoCAD 生产路径
- main 分支任何新开发（已归档决策不变）
- conda 严格对齐（可选核验，非目标）

## Acceptance Criteria

### 工作区重构
- [ ] `robustness` 分支存在，基于 `ff41501`（newmodel 最新），树中无 `.pytest-tmp-*`/`build/`/`ErrorReports*/`；`experiment/runs/` 仅保留 `apd_architecture_v3_complete` + `apd_architecture_v3_gcp_ready`
- [ ] `.gitignore` 含 `.pytest-tmp-*`、`build/`、`ErrorReports/` 条目
- [ ] `.omc/`（specs/plans/wiki/notepad/state 精华）在分支中已提交；`demo/`、`official/`、`paper/` 仍在树中（轻裁剪）
- [ ] main 工作区（/home/cat/projects/CAD2GIS）git status 无已追踪改动；robustness 工作在新 worktree/目录进行

### 工作包 A（dev-reader + APD 重放）
- [ ] dev-reader 实现 `extract_dwg_records` 契约（records + compatibility diagnostics），经 `CAD2GIS_BACKEND_PATH` 可插入
- [ ] LibreDWG 不可读项全部产生 typed unsupported 记录（零静默丢失）
- [ ] APD DWG 本机重放成功；与 `apd_architecture_v3_complete` 基线对账（BOITE=43/CABLE=6/CABLE_SEGMENT=139/PTECH=167/IMB=682；偏差项全部有解释记录）
- [ ] 既有测试套件不回退（281/282 基线之上）

### 工作包 B/C/D（概要，执行期细化）
- [ ] B：转移资产接入点明确，验证规则 CSV 在 verify 体系生效；拓扑门控新增规则有测试
- [ ] C：gcp 子命令测试通过；verify matrix 支持多维度 claim 聚合 schema
- [ ] D：doctor 在本机输出正确的 Linux 能力报告；新增回归测试覆盖四包改动

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| "85-95% 定界+三类归 win 端"可接受 | R1 用户拒绝 | 鲁棒性必须含真实 ingest 相关 → 读取本机化成为核心 |
| 真实 GCP 需要 Windows | R2 分析 | 否——GCP 采集是外部测量，校准框架纯 Python；唯一阻塞也是 ingest |
| LibreDWG"埋掉"=永不可用 | R2 用户选 dev-reader | 埋的是 canonical 角色；dev-reader 角色（非交付权威）与 fail-closed 兼容 |
| 重构=切换目录 | R4 Contrarian | 否——新建 robustness 分支，内容策略问题 |
| .pytest-tmp 需要迁移 | R5 用户质疑+实测 | 否——459 文件/24MB 测试临时垃圾，删除+.gitignore 防再积累 |
| 裁剪越狠越好 | R6 trade-off 分析 | 轻裁剪——demo/official/paper 保留，避免删除传播到 newmodel |

## Technical Context

### 实证基线（2026-07-21 本机实测）
- venv(--system-site-packages)+ezdxf 1.4.4+pytest 9.1.1+系统 GDAL 3.8.4/shapely 2.1.2
- `tests/` 顶层合同套件 **51/51 通过**；`experiment/py_scripts` 全套 **281 通过/1 失败**（唯一失败=`autocad_reader.py:3577` Windows 守卫，设计行为）；`test_geopackage_reproducibility_v3` 2/2 通过
- worktree `/tmp/newmodel-trace`（newmodel@ff41501）仍存在，可供执行期使用

### dev-reader 技术锚点
- 契约：`experiment/py_scripts/cad2gis_v3/ingest.py:8,17`（`from autocad_reader import extract_dwg_records`）；返回 records + compatibility diagnostics
- 后端加载：`src/cad2gis/runtime.py:31`（CAD2GIS_BACKEND_PATH）、`:198-222`（模块契约加载 `cad2gis_v3.pipeline`）
- 复用资产：main 分支 `experiment/py_scripts/converter.py` 的 ctypes 桥（`_init_libredwg`/`_lwpoline_points`/`_entity_utf8_text`/`_extract_wkt` 等，约 505 行 read_dwg 相关）
- Windows 守卫点（dev-reader 不受影响）：`autocad_reader.py:32`（accoreconsole 路径）、`:3576-3577`（os.name 守卫）
- APD 对账基线：`experiment/runs/apd_architecture_v3_complete/`（readcad_review_bundle.json 9,391 objects 元数据+delivery/evidence GPKG）

### newmodel 结构实测（裁剪依据）
- 垃圾：`.pytest-tmp-*` 34 目录/459 文件/24MB；`build/`=setuptools 产物；`ErrorReports/`=AutoCAD .cer.log
- 大头：`experiment/runs/` 308MB（11 bundles；留 2 删 9）
- 保留：`src/`(388K)、`tests/`(232K)、`experiment/py_scripts`(4M)、`docs/`、`env/`、`qgis_plugin/`、`webdemo/`（组员延申点）、`main_archive/`、APD DWG(1.7M)、`demo/`(102M)、`official/`(9.8M)、`paper/`(4.5M)

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| robustness 分支 | core | 基于ff41501/轻裁剪/.omc入库 | 用户工作主场；合并回 newmodel |
| newmodel 分支 | core | ff41501/canonical 生产线 | robustness 的基线与合并目标 |
| LibreDWG dev-reader | core | v3契约/extraction_backend=libredwg_dev/非canonical | 解锁 ingest 本机化；复用 main ctypes 桥 |
| AutoCAD canonical reader | external | accoreconsole/Windows/交付权威 | 组员侧；dev-reader 永不取代 |
| APD 重放基线 | supporting | BOITE=43/CABLE=6/CABLE_SEGMENT=139/PTECH=167/IMB=682 | dev-reader 对账目标 |
| 四工作包 A/B/C/D | core | 见 Goal | 鲁棒性提升范围 |
| 真实 GCP/新 DWG 样本 | external | 外部资源 | 不在本机范围 |
| 轻裁剪清单 | supporting | .pytest-tmp/build/ErrorReports/旧bundles | 防合并传播风险 |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 5 | 5 | - | - | N/A |
| 2 | 7 | 2 | 0 | 5 | 71% |
| 3 | 9 | 2 | 0 | 7 | 78% |
| 4 | 10 | 1 | 0 | 9 | 90% |
| 5 | 11 | 1 | 0 | 10 | 91% |
| 6 | 11 | 0 | 0 | 11 | 100% |

## Trace Findings
Trace 全文：`.omc/specs/deep-dive-trace-newmodel-robustness-local-no-win.md`。
- **实证结论**：本机可跑（51/51+281/282），硬边界=真实 DWG ingest 唯一
- **Lane 1 未知**（Linux 替代读取）→ R2 解决：LibreDWG dev-reader
- **Lane 2 未知**（GDAL/PROJ 版本差）→ 实证可复现性通过；conda 对齐降为可选核验
- **Lane 3 未知**（范围/优先级）→ R3 全选 A+B+C+D；R4-R6 定工作区形态

## Interview Transcript
<details>
<summary>Full Q&A (6 rounds + Round 0)</summary>

### Round 0（拓扑确认）
**Q:** 三组件（可行性确认/中间地带决策/范围与优先级）？
**A:** 划分正确。

### Round 1（可行性 / Goal）
**Q:** 接受"85-95% 定界+三类归 win 端"吗？环境严格性怎么定？
**A:** **不接受定界**（鲁棒性必须含真实 ingest 相关）。**Ambiguity:** 60%

### Round 2（中间地带 / Constraints）
**Q:** 真实 DWG 读取本机化机制：LibreDWG dev-reader / 提取记录入库 / ODA SDK / 组合？
**A:** **LibreDWG dev-reader**。**Ambiguity:** 39%

### Round 3（范围 / Criteria）
**Q:** 工作包多选（A dev-reader+APD重放 / B 转移资产+拓扑门控 / C GCP框架+验证矩阵 / D UX+测试扩充）？
**A:** **全选**，并新增：重构本机工作区文件结构（旧结构已封存 main 不再维护）；分工=组员延申结构写 webUI+后端，用户=多DWG验证+准确度改进。**Ambiguity:** 26%

### Round 4（工作区 / Contrarian）
**Q:** 重构形态：原地切分支 / 新目录新 clone / worktree 并存？
**A:** 都不是——**单开 robustness 分支**，拉入 main 的 .omc 知识库 + newmodel 中工作必要部分。**Ambiguity:** 20.5%

### Round 5（内容策略 / Criteria）
**Q:** 全量树+.omc入库 / 全量树+.omc不入git / 裁剪树？
**A:** .omc 要复制过去；但**首先深度了解 newmodel 文件结构**——.pytest-* 真的有必要迁移吗？→ 实测：.pytest-tmp-*=34目录/459文件/24MB 垃圾。**Ambiguity:** 16.5%

### Round 6（裁剪清单）
**Q:** 轻裁剪（垃圾+旧bundles，留 demo/official/paper）/ 重裁剪（全删）/ 自定义？
**A:** **轻裁剪（推荐）**。**Ambiguity:** 11.5% ✅
</details>
