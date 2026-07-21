# RALPLAN: robustness 分支重构 + LibreDWG dev-reader + APD 重放（Phase I）

**Status:** `approved for autopilot execution`（consensus iteration 2 达成：Critic 阻塞 5 项+Architect 改进 5 项已全部并入，iter2 双方 APPROVED_WITH_IMPROVEMENTS，非阻塞改进 3 项已并入；用户经 deep-dive 菜单显式选择 Ralplan→Autopilot 管线）
**Date:** 2026-07-21
**Mode:** RALPLAN-DR short（新分支隔离作业+新代码模块，无生产路径变更；删除仅限轻裁剪清单）
**Source spec:** `.omc/specs/deep-dive-newmodel-robustness-local-no-win.md` (ambiguity 11.5%, PASSED)
**Trace:** `.omc/specs/deep-dive-trace-newmodel-robustness-local-no-win.md`（含实证探针：51/51+281/282 测试本机通过）

---

## 1. RALPLAN-DR Summary

### Principles (5)

1. **dev-reader 永不入交付路径** — `libredwg_dev_reader` 产物的 `extraction_backend="libredwg_dev"` 显式标记；canonical 交付权威仍属 AutoCAD 路径（`autocad_reader.py`）。dev-reader 仅用于本机开发/重放/测试。
2. **fail-closed 兼容，零静默丢失** — LibreDWG 读不出/读不准的字段一律产生 `supportedp=false` + `unsupported=[原因码]` 的完整记录，绝不跳过实体（满足 `ingest.py:20-27` 的 `skipped_rows==0` 且 `inventory_complete!=False` 门）。
3. **同分支自包含** — dev-reader 只依赖 robustness 分支树内代码（可从同分支 legacy `experiment/py_scripts/converter.py` 拷贝 ctypes 桥适配），不 import main 分支、不新增系统依赖（LibreDWG `.so` 本机已装，`/usr/local/lib/libredwg.so`）。
4. **分支手术可逆** — robustness 分支新建自 `ff41501`；所有删除仅限轻裁剪清单（垃圾+9 个旧 run bundles），newmodel 历史完整保留一切；不 push 除非用户显式确认。
5. **对账先行** — dev-reader 的合格判据不是"能跑"，而是 APD 重放与 `apd_architecture_v3_complete` 基线对账（BOITE=43/CABLE=6/CABLE_SEGMENT=139/PTECH=167/IMB=682），所有偏差有分类解释（reader 保真差 vs pipeline 行为差）。

### Decision Drivers (top 3)

| Rank | Driver | Why |
|------|--------|-----|
| 1 | `ingest.py:20-27` 的 fail-closed 门（skipped_rows/inventory_complete）与 LibreDWG 保真局限存在设计张力 | 决定 dev-reader 的记录策略（全量 records+supportedp=false）与是否需要 profile 级 dev 宽容标志 |
| 2 | `DOCUMENT_METADATA`（CGEOCS/INSUNITS 证据，`ingest.py:31-45`）LibreDWG 未必能读 | 决定 dev 模式下 CRS/单位证据检查的处理（合成 vs typed unsupported vs profile 放宽） |
| 3 | 分支重构不可逆性低但删除会传播 | 轻裁剪清单必须精确；合并回 newmodel 时只应传播垃圾删除 |

### Viable Options

#### Option A: 同分支新模块 `libredwg_dev_reader.py`（SELECTED）

在 `experiment/py_scripts/` 新建自包含模块，实现 `extract_dwg_records(source_path) -> DWGRecordInventory`（含 `.diagnostics`），ctypes 桥函数从同分支 legacy `converter.py`（92KB，含 `_init_libredwg`/`_lwpoline_points`/`_entity_utf8_text`/`_parse_dwg_color`）拷贝适配。

**Pros:** 契约对齐（`ingest.py:8` import 形态不变）；同分支自包含；不污染 `autocad_reader.py`（Windows canonical 一字不动）；可独立测试。**Cons:** ctypes 桥代码存在两份拷贝（legacy converter.py 与 dev-reader）——可接受，dev 模块独立演进。

#### Option B: 在 `autocad_reader.py` 内加 LibreDWG 分支（REJECTED）

**拒绝理由:** 污染 canonical 读取器（3,577 行的 Windows 权威模块混入 Linux dev 路径）；违反 Principle 1；组员维护该文件时冲突风险高。

#### Option C: ezdxf 读取路径（REJECTED）

**拒绝理由:** ezdxf 不读 DWG 二进制；本机无 DWG→DXF 转换器（该转换器本身就是 AutoCAD）。技术上不可行。

**Verdict:** Option A。B、C 已显式排除。

---

## 2. Requirements Summary

Phase I（本计划详述）：
1. **工作区重构**：新建 `robustness` 分支（@`ff41501`），轻裁剪（`.pytest-tmp-*`/`build/`/`ErrorReports*` + 9 个旧 run bundles），`.gitignore` 补防+放行 `.omc/`，`.omc` 精华（specs/plans/wiki/notepad.md，~468KB）提交入库。
2. **工作包 A.1**：`libredwg_dev_reader.py` 实现 `extract_dwg_records` 契约（records + `.diagnostics`），LibreDWG 局限转 typed unsupported。
3. **工作包 A.2**：契约测试 + APD DWG 本机重放，与 `apd_architecture_v3_complete` 基线对账（BOITE=43/CABLE=6/CABLE_SEGMENT=139/PTECH=167/IMB=682），偏差分类记录。

Phase II（框架，各自执行周期细化，本计划只定入口判据）：
- B. 转移资产落地+拓扑门控（入口：A 对账通过）
- C. GCP 框架+验证矩阵（入口：A 对账通过；真实 GCP/新样本属外部）
- D. UX+测试扩充（入口：B/C 启动后可并行）

## 3. Implementation Steps（Phase I）

### S0: robustness 分支创建（worktree 隔离）

```bash
cd /home/cat/projects/CAD2GIS
git worktree add /home/cat/projects/CAD2GIS-robustness -b robustness ff41501
```

- 新工作目录 `~/projects/CAD2GIS-robustness`（用户主工作区）；main 检出 `/home/cat/projects/CAD2GIS` 不动
- 既有 `/tmp/newmodel-trace` worktree 在执行开始时移除（避免同分支双 checkout 冲突：newmodel 与 robustness 是不同分支，无冲突，但保持整洁）

### S1: 轻裁剪（commit 1）

```bash
cd /home/cat/projects/CAD2GIS-robustness
git rm -r --quiet -- .pytest-tmp-* build/ ErrorReports/ experiment/ErrorReports/
git rm -r --quiet -- \
  experiment/runs/apd_accuracy_v4_validation \
  experiment/runs/apd_accuracy_v5_validation \
  experiment/runs/apd_architecture_v1 \
  experiment/runs/apd_architecture_v2 \
  experiment/runs/apd_architecture_v3 \
  experiment/runs/apd_architecture_v6_validation \
  experiment/runs/apd_architecture_v7_validation \
  experiment/runs/apd_gcp_architecture_probe \
  experiment/runs/close_probe.gpkg
# 保留: apd_architecture_v3_complete, apd_architecture_v3_gcp_ready
```

`.gitignore` 追加：
```
# robustness: test temp & build junk (cleaned at branch creation)
.pytest-tmp-*
build/
ErrorReports/
```
commit: `chore: 轻裁剪测试临时产物/构建垃圾/旧 run bundles（保留 v3_complete+gcp_ready 基线）`

### S2: .omc 入库（commit 2）

- `.gitignore` 修改：删除 `.omc/` 行（该文件注释块标注 "personal config, do not commit"——本分支决策反转为"知识库随分支版本化"，在 commit message 中说明）
- 拷贝 main 侧 `.omc/{specs,plans,wiki,notepad.md}` → 分支 `.omc/`（排除 state/sessions/handoffs/drafts 瞬态内容）
- commit: `docs: .omc 知识库入库（specs/plans/wiki/notepad，随分支版本化）`

### S3: dev-reader 实现（commit 3）— 工作包 A.1

**前置阅读（执行第一步，不可跳过）**：`cad2gis_v3/model.py:277-341`（`SourceEntity.from_record` 实际消费的 ~30 键）、`autocad_reader.py:1589-1747`（`inventory_support_status` 与 `raw_properties["unsupported_reasons"]` 的真实用法）、`autocad_reader.py:69`（`DWGRecordInventory`）、`:3562`（`extract_dwg_records` 签名）。

新文件 `experiment/py_scripts/libredwg_dev_reader.py`（目标 ≤700 行）：

**契约实现**：
```python
def extract_dwg_records(source_path) -> DWGRecordInventory:
    # list-like DWGRecordInventory，携带 .diagnostics = {
    #   "extraction_backend": "libredwg_dev",
    #   "skipped_rows": 0,                 # 绝不跳过实体（Principle 2）
    #   "inventory_complete": True,         # LibreDWG 全量枚举
    #   "metadata_evidence": "reader" | "synthetic",
    #   "unsupported_reason_counts": {...}  # unsupported_reasons 分布台账
    # }
```

**records 字段映射**（对齐 `from_record` 实际消费的 ~30 键；缺失键会给默认值导致下游静默错位——必须全部生成）：

| 字段组 | 字段 | LibreDWG 来源 | 不可读时 |
|---|---|---|---|
| 身份 | entity_key/source_sha256/source_file/handle | sha256 文件+handle 合成 | — |
| 角色 | layout/layout_role/cad_role | model space→`Model`；paper→layout 名；角色按 v3 枚举推导 | 推导不出→`graphic_only`+原因码 |
| 分类 | layer/object_name/dwg_type_name | `_layer_name()`（newmodel legacy `converter.py:244`，UTF-8 修复） | — |
| 几何 | points/centroid/closed/native_length | `_lwpoline_points()`（`converter.py:251`）+弦长计算 | `inventory_support_status="inventory_only"` + `raw_properties["unsupported_reasons"]+=["geometry_unavailable"]` |
| 曲线 | curve_facts/curve_fingerprint | bulge/elevation/normal 经 ctypes 扩展读取 | 原因码 `curve_facts_unavailable` |
| 样式 | color/linetype/lineweight 等 style 子字段 | **从 main 分支移植** `_parse_dwg_color`/`_resolve_effective_color`（`git show main:experiment/py_scripts/converter.py`，约 291-333 行）+ ACI 链（`git show main:experiment/py_scripts/schema_config.py` 的 `_hsv_bytes`/`_generate_aci_table`/`aci_to_rgb`，约 2638-2680 行）——newmodel legacy converter.py **没有**这些函数 | ByLayer 默认+原因码 |
| 文本 | text/block_attributes/dimension_text_override | **从 main 移植** `_entity_utf8_text`（main converter.py:101-117）；newmodel legacy 只有内联提取（:759-779） | 乱码/截断→原因码 `text_encoding` |
| 块 | block_name/scale_x/y/z/owner_handle | ctypes 读 INSERT | 原因码 |
| 测量 | dimension_value/native_length | `_extract_dimension()`（main converter.py:529-547） | `dimension_value=None`（v3 允许 unmeasured） |
| 杂项 | raw_properties | 全量原始属性 dict | — |
| HATCH | 整记录 | LibreDWG 局限 | sentinel：`inventory_support_status="inventory_only"`，原因码 `hatch_reader` |

**unsupported 标记契约（Critic 阻塞 2 修复）**：自造 `supportedp` 字段**废弃**；一律使用 v3 真实契约——`inventory_support_status`（`full`/`inventory_only`）+ `raw_properties["unsupported_reasons"]`（原因码字符串列表，对齐 `autocad_reader.py:1589-1747` 的既有码集，新增码以 `libredwg_` 前缀）。

**Driver 2 设计决策（CGEOCS/INSUNITS 门，Critic 阻塞 1 修复）**：dev profile **保留原值不置 null**。
- dev-reader 先尝试 LibreDWG header 变量读取 CGEOCS/INSUNITS；读到 → 真实 `DOCUMENT_METADATA` 记录，`diagnostics["metadata_evidence"]="reader"`
- 读不到 → 合成 `DOCUMENT_METADATA` 记录，文本含期望串 + 标记串 `SYNTHETIC_METADATA_EVIDENCE`，`diagnostics["metadata_evidence"]="synthetic"`

**dev-reader 加载机制（Architect iter2 残留 3 修复：不补丁 canonical 文件）**：
- 新文件 `experiment/py_scripts/cad2gis_v3/ingest_dev.py`（dev-only wrapper）：复制 `ingest()` 主流程（读 records→diagnostics 检查→from_record→census 校验），但 `from libredwg_dev_reader import extract_dwg_records`；并在 metadata 含 `SYNTHETIC_METADATA_EVIDENCE` 且 `os.environ.get("CAD2GIS_DEV_READER") != "1"` 时 `raise RuntimeError("synthetic metadata evidence requires CAD2GIS_DEV_READER=1")`
- **`ingest.py` 一字不改**——canonical 文件零触碰，合并泄漏风险构造性消除（无补丁可泄漏）；重放驱动（S5）与契约测试（S4）调 `ingest_dev.ingest()`，生产路径不受影响
- dev profile 副本 `config/apd_source_profile_dev_libredwg.json` 保留 `dwg_cgeocs/dwg_insunits` 原值并附 `_dev_note`

**关键实现约束**：
- `sys.path` 处理与 `cad2gis_v3` 包导入形态对齐（try/except 双模式）
- 不 import `autocad_reader`（避免 Windows-only import 链）；`DWGRecordInventory` 的兼容通过 duck-typing（list 子类+`.diagnostics`），必要时在 dev-reader 内定义同形类
- ctypes 桥来源如实标注注释（哪些函数来自 newmodel legacy、哪些移植自 main 分支 commit 引用）

### S4: 契约测试（commit 4）

新文件 `experiment/py_scripts/test_libredwg_dev_reader.py`：
1. `test_inventory_complete_and_no_skips`：APD DWG → diagnostics `skipped_rows==0`、`inventory_complete is True`、`extraction_backend=="libredwg_dev"`
2. `test_census_matches_apd_baseline`：model entities=6,940、INSERT=222、DIMENSION=170（容差 0；不符即 FAIL 并打印差异清单）
3. `test_unsupported_records_use_v3_contract`：所有 `inventory_support_status=="inventory_only"` 记录的 `raw_properties["unsupported_reasons"]` 非空，且原因码 ∈ 既有码集 ∪ `libredwg_` 前缀新码（对齐 `autocad_reader.py:1589-1747`）
4. `test_no_windows_imports`：模块内无 `win32com`/`pythoncom`/`accoreconsole` 引用（grep 式断言）
5. `test_ingest_gate_passes`：`CAD2GIS_DEV_READER=1` 环境下 `ingest(apd_dwg, dev_profile)` 不抛异常，entities 非空；**且** 无该环境变量时若 metadata 为 synthetic 则必须 raise（双向门验证）
6. `test_record_field_completeness_snapshot`（Critic 阻塞 5 修复）：对 APD 全部 records 断言每条键集 ⊇ 必需 ~30 键快照清单（entity_key/source_sha256/source_file/handle/layout/layout_role/cad_role/layer/object_name/dwg_type_name/points/centroid/closed/text/block_name/block_attributes/dimension_value/scale_x/scale_y/scale_z/owner_handle/dimension_text_override/native_length/raw_properties/curve_facts/curve_fingerprint + style 子字段），缺键即 FAIL 并列出缺失分布——防 from_record 默认值静默兜底
7. `test_ingest_dev_matches_canonical_post_reader`（Critic iter2 改进 1）：用同一组 mock records 分别驱动 `ingest_dev.ingest` 与 `ingest.ingest`，断言 reader 之后的行为一致（entities 数、census 计数、annotation carriers 统计）——防 wrapper 与 canonical 逻辑漂移

### S5: APD 重放对账（commit 5）

```bash
cd /home/cat/projects/CAD2GIS-robustness
PYTHONPATH=src:experiment/py_scripts CAD2GIS_BACKEND_PATH=experiment/py_scripts CAD2GIS_DEV_READER=1 \
  /tmp/cad2gis-venv/bin/python -c "驱动 cad2gis_v3.pipeline 下游（dev profile + dev-reader）输出到 /tmp 重放 run dir"
```

对账脚本（`experiment/py_scripts/replay_apd_libredwg_dev.py`，新文件）输出对账报告 JSON，**基线分层**（Critic 阻塞 4 修复）：
- **delivery 层**（vs `experiment/runs/apd_architecture_v3_complete/apd_delivery.gpkg`）：BOITE=43/CABLE=6/PTECH=167/IMB=682/SITE=2/INFRASTRUCTURE=0/ZNRO=0/ZPM=0
- **evidence 层**（vs 同目录 `apd_evidence.gpkg`）：`cable_span_segments=139`（=CABLE_SEGMENT 口径）、`source_route_evidence=6`、`physical_span_evidence=170`
- 几何抽样对比：CABLE 6 条源线顶点数（145）与抽样顶点坐标差（容差明示）
- 偏差分类：`reader_fidelity`（LibreDWG 保真差）/ `pipeline_behavior`（下游行为差）/ `baseline_drift`（基线本身口径）
- **对账合格判据（硬门槛，Critic 独立核查 2 修复）**：交付+证据层计数全对；**或**每个偏差都带 typed 解释记录写入报告——"偏差无解释"本身构成 FAIL，不允许绕过

### S6: 回归 + 文档（commit 6）

- 全量测试：`pytest tests -q`（≥51 通过）+ `pytest experiment/py_scripts -q`（≥281 通过+新增 dev-reader 测试，0 新失败）
- `README.md`（robustness 分支）追加一节：dev-reader 角色/非 canonical 声明/本机运行方法/对账口径
- commit: `feat: LibreDWG dev-reader（v3 reader 契约，非 canonical）+ APD 重放对账`

### S7: push 确认门 + 合并隔离声明

- 全部完成后展示 commit 序列；**用户显式确认才** `git push -u origin robustness`
- **合并隔离声明**（写入 README 增补节与 S7 交接说明）：robustness 分支特有的变更——`.gitignore`（.omc 放行+垃圾补防）、`.omc/` 入库、`libredwg_dev_reader.py`、`cad2gis_v3/ingest_dev.py`、`config/apd_source_profile_dev_libredwg.json`、轻裁剪删除——合并回 newmodel 时由组员逐项评审取舍；`ingest.py`/`autocad_reader.py`/canonical profiles 零改动（可 `git diff ff41501..robustness -- experiment/py_scripts/cad2gis_v3/ingest.py experiment/py_scripts/autocad_reader.py experiment/config/apd_source_profile.json` 验证为空）
- 行号引用说明：本计划所有 newmodel 代码行号均以 **newmodel 分支 worktree**（/tmp/newmodel-trace）为准（如 `model.py:277`=from_record，全文件 389 行）；main 工作区的 `newmodel/` 子目录是同名片旧版（168 行），勿混淆

## 4. Phase II 入口判据（B/C/D 框架）

| 包 | 入口判据 | 范围锚点（执行期细化） |
|---|---|---|
| B 转移资产+拓扑门控 | A 对账通过 | main_archive 文档 §3 资产清单；evaluation_standards CSV 接入 verify；图例排除完备性对照 |
| C GCP 框架+验证矩阵 | A 对账通过 | src/cad2gis/gcp_workflow.py 完善+tests/test_gcp_workflow.py 扩充；verify/matrix.py claim ladder |
| D UX+测试扩充 | B/C 启动 | doctor Linux 能力报告准确化；README/安装引导；回归套件并入 CI 脚本 |

## 5. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| fail-closed 门（skipped_rows/inventory_complete）与 LibreDWG 保真局限冲突 | High | High | Principle 2 记录策略（全量 records + `inventory_support_status="inventory_only"` + typed 原因码）；S4 test_ingest_gate_passes 双向验证 |
| CGEOCS/INSUNITS metadata 读不到导致 ingest 拒跑，或置 null 打开"先放行后遗忘"口子 | Medium | High | dev profile 保留原值；synthetic metadata + `CAD2GIS_DEV_READER=1` 显式开关；开关判定封装在 dev-only `ingest_dev.py`，`ingest.py` 零补丁 |
| dev 机制泄漏进 canonical（合并回 newmodel 污染生产路径） | Medium | High | canonical 文件零触碰（构造性消除）；S7 合并隔离声明+diff 为空验证；dev 文件命名统一 `_dev` 前缀可 grep 审计 |
| LibreDWG 中文文本乱码重現 | Medium | Medium | 从 main 移植 `_entity_utf8_text` ctypes 修复；残余→typed unsupported；对账报告单列 text 类偏差 |
| dev-reader 几何偏差污染对账结论 | Medium | High | 对账报告偏差三分法（reader/pipeline/baseline）；CABLE 顶点抽样容差明示；"偏差无解释"=FAIL 硬门槛 |
| 裁剪误删 | Low | Medium | 仅清单内路径；newmodel 历史完整；S1 单独 commit 可 revert；git rm 加 `--` 分隔 |
| `.gitignore` 放行 .omc 与 newmodel 合并时冲突 | Medium | Low | 合并界面约束：.gitignore 与 .omc 变更为 robustness 分支特色，合并回 newmodel 时由组员评审取舍（记录在 S7 交接说明） |
| record 字段缺失被 from_record 默认值静默兜底（~30 键契约） | Medium | High | S3 前置阅读 model.py:277-341 强制；S4 字段完整性快照测试逐键断言 |

## 6. Acceptance Criteria（可测试）

- [ ] `git -C ~/projects/CAD2GIS-robustness log --oneline ff41501..robustness` ≥6 commits；`git ls-tree -r robustness --name-only | grep -c "pytest-tmp\|^build/\|ErrorReports"` == 0
- [ ] `git ls-tree robustness experiment/runs/ --name-only` 仅含 `apd_architecture_v3_complete` 与 `apd_architecture_v3_gcp_ready`
- [ ] `git show robustness:.gitignore | grep -c "pytest-tmp"` ≥1 且不含独立 `.omc/` 行；`git ls-tree robustness .omc/ --name-only` 含 specs/plans/wiki/notepad.md
- [ ] `libredwg_dev_reader.py` 存在；`grep -cE "win32com|pythoncom|accoreconsole" libredwg_dev_reader.py` == 0
- [ ] S4 六项契约测试全过（含字段完整性快照、ingest 双向门）
- [ ] 测试不回退（spec 对齐）：`pytest tests -q` ≥51 通过；`pytest experiment/py_scripts -q` ≥281 通过且唯一失败=`test_reader_protocol_strict_v3.py::test_flat_inventory_preserves_compatibility_diagnostics`（Windows 守卫），0 新增失败
- [ ] 对账报告 JSON 存在：delivery 层计数（BOITE=43/CABLE=6/PTECH=167/IMB=682/SITE=2）与 evidence 层（`cable_span_segments=139`）全部匹配；**或**每个偏差都带 typed 解释（reader_fidelity/pipeline_behavior/baseline_drift），"偏差无解释"条目数 == 0（硬门槛）
- [ ] README 增补节含"非 canonical"声明与 `CAD2GIS_DEV_READER=1` 使用说明
- [ ] canonical 零触碰：`git diff ff41501..robustness -- experiment/py_scripts/cad2gis_v3/ingest.py experiment/py_scripts/autocad_reader.py experiment/config/apd_source_profile.json` 输出为空
- [ ] main 工作区 `git status --short` 无已追踪改动；push 有用户显式确认记录

## 7. Verification Steps

1. 分支树检查（上述 AC 前两条命令）
2. `cd ~/projects/CAD2GIS-robustness && PYTHONPATH=src:experiment/py_scripts /tmp/cad2gis-venv/bin/python -m pytest tests experiment/py_scripts -q` → 计数比对基线
3. `git -C ~/projects/CAD2GIS-robustness diff ff41501..robustness --stat | tail -5` → 变更面审阅
4. 对账报告人工抽读偏差分类合理性
5. `git -C /home/cat/projects/CAD2GIS status --short` → main 现场无污染

---

## 8. Pre-mortem（3 失败场景，Critic iter2 改进 2）

### 场景 1：ctypes 桥在 LibreDWG 调用中 segfault
**如何发生**：`_lwpoline_points()`/`_entity_utf8_text()` 经 ctypes 直接调 C API，遇到畸形实体（如损坏的 LWPOLYLINE 点数组）时段错误使整个 Python 进程崩溃，而非抛异常——重放中途无声死亡。
**检测**：重放进程退出码 139（SIGSEGV），对账报告缺失。
**缓解**：dev-reader 按图层/按 handle 分段处理并记录进度游标（每 N 实体 flush 一次 diagnostics 到临时 JSON）；崩溃后可从游标定位凶手实体，将其标记为 `inventory_only`+原因码 `reader_crash` 后跳过——宁可一个 typed unsupported，不可整体崩溃。

### 场景 2：APD DWG 文件损坏或被意外替换
**如何发生**：`apd_source_profile_dev_libredwg.json` 绑定源 SHA-256；若 DWG 被覆盖/损坏，`profile.validate_source()` 直接拒绝——但错误信息若被误读为"dev-reader 坏了"会浪费排查时间。
**检测**：validate_source 抛 hash mismatch；`sha256sum` 对比 `557e0141...815557`（experiment/README.md 记录值）。
**缓解**：S5 重放驱动第一步打印源 SHA-256 与期望值比对结果；不匹配时明确报错"源文件身份不符，非 reader 问题"。

### 场景 3：synthetic 标记意外命中真实 metadata
**如何发生**：未来某 DWG 的真实 DOCUMENT_METADATA 文本中恰好包含 `SYNTHETIC_METADATA_EVIDENCE` 字符串（构造碰撞或复制粘贴），导致 ingest_dev 误判真实证据为合成证据。
**检测**：`diagnostics["metadata_evidence"]=="reader"` 但 metadata 文本含标记串——矛盾态。
**缓解**：标记串设计为低碰撞形态（`__CAD2GIS_SYNTHETIC_METADATA_EVIDENCE_7f3a9c__` 带随机后缀）；ingest_dev 检查到"reader 证据含标记串"的矛盾态时 raise（宁可误拒不可误放）。

---

## 9. ADR (Architecture Decision Record)

**Decision:** 采用 Option A：robustness 分支（@ff41501）内新建 `experiment/py_scripts/libredwg_dev_reader.py`（自包含 ctypes 桥，实现 `extract_dwg_records` v3 契约）+ `cad2gis_v3/ingest_dev.py`（dev-only wrapper，canonical `ingest.py` 零触碰）；LibreDWG 局限经 `inventory_support_status="inventory_only"` + `raw_properties["unsupported_reasons"]` typed 记录；synthetic metadata 由 `CAD2GIS_DEV_READER=1` 显式开关门控；APD 重放对账以 delivery GPKG（BOITE=43/CABLE=6/PTECH=167/IMB=682/SITE=2）+ evidence GPKG（cable_span_segments=139）双层基线判定。

**Drivers:** ①fail-closed 门（ingest.py:20-27）与 LibreDWG 保真局限的张力 → 全量 records+typed unsupported；②CGEOCS/INSUNITS 证据门（:31-45）→ synthetic 标记+env 开关封装于 dev wrapper；③dev 机制不得泄漏进 canonical → canonical 文件零触碰+合并隔离声明。

**Alternatives considered:** Option B（autocad_reader.py 内加 LibreDWG 分支——拒绝：污染 canonical）；Option C（ezdxf——拒绝：不读 DWG 二进制）；ingest.py 最小补丁方案（拒绝于 iter2：合并泄漏风险，wrapper 构造性消除）；dev profile 置 null 方案（拒绝于 Critic 阻塞 1："先放行后遗忘"口子）。

**Why chosen:** Option A 是唯一同时满足契约对齐、canonical 零污染、fail-closed 兼容、本机可测试的路径；两路 iter 的 10+3 项评审意见已全部并入。

**Consequences:** robustness 分支新增 ~4 个 dev 文件（reader/wrapper/dev profile/replay 驱动）+ 7 项契约测试；ctypes 桥代码在分支内存在两份（legacy converter.py 与 dev-reader）——可接受的 dev 冗余；合并回 newmodel 时 dev 文件与 .gitignore/.omc 变更需组员逐项评审。

**Follow-ups:** ①Phase II（B/C/D 工作包）各自启动前重审入口判据；②dev-reader 若在多 DWG 验证中表现稳定，可由组员评估是否晋升为非 canonical 但入库的 Linux 开发后端（长期）；③conda env.yml 严格对齐（GDAL 3.10/PROJ 9.8）作为可选核验项排期；④wrapper 与 canonical 的漂移监控依赖 S4-7 同步测试，若 ingest.py 在 newmodel 侧演进需人工同步 wrapper。

---

## 10. Consensus Changelog (iteration 1 → 2 → final)

**iteration 1（Critic REJECTED 5 阻塞 + Architect 5 改进）→ iteration 2：**
- [阻塞1/改进3] CGEOCS/INSUNITS：置 null 方案废弃 → dev profile 保留原值 + synthetic metadata + `CAD2GIS_DEV_READER=1` 开关
- [阻塞2/改进2] 自造 `supportedp` 废弃 → `inventory_support_status` + `raw_properties["unsupported_reasons"]`（v3 真实契约）
- [阻塞3/改进4] helper 来源修正：_layer_name(:244)/_lwpoline_points(:251) 来自 newmodel legacy；_entity_utf8_text/_parse_dwg_color/_extract_dimension/ACI 链从 main 移植（含 schema_config.py ACI 链出处修正）
- [阻塞4/改进5] CABLE_SEGMENT=139 基线 → `apd_evidence.gpkg:cable_span_segments`（delivery/evidence 双层对账）
- [阻塞5] S4 新增字段完整性快照测试（~30 键逐键断言）
- [改进1] S3 字段映射表补全 ~30 键；S3 前置阅读强制化

**iteration 2（Architect 3 残留）→ final：**
- [残留a+c] ingest.py 补丁方案废弃 → `cad2gis_v3/ingest_dev.py` dev-only wrapper（canonical 零触碰，构造性消除合并泄漏）；S7 合并隔离声明 + AC canonical diff 为空验证
- [残留b] 行号误报澄清：以 newmodel 分支 worktree 为准（model.py=389 行/:277），main 工作区 newmodel/ 子目录=168 行旧版，附防混淆说明
- [Critic iter2 改进1] S4 新增 wrapper-canonical 同步测试（mock records）
- [Critic iter2 改进2] 新增 Pre-mortem 三场景（segfault 游标恢复/源文件身份校验/synthetic 标记低碰撞设计+矛盾态 raise）
- [Critic iter2 改进3] aci_to_rgb 出处修正为 schema_config.py（~2638-2680 行）
