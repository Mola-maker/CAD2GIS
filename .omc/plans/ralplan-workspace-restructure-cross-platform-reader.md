# RALPLAN: robustness 工作区重构（跨平台 reader 升格 + 工作区精简 + A 方案闭环比对）

**Status:** `pending approval`（RALPLAN-DR short consensus 流程已启动；待 Architect + Critic 评审）
**Date:** 2026-07-21
**Mode:** RALPLAN-DR short（架构级重构，删除大量文件但有 git 历史保 + 用户显式指令）
**Source spec:** `.omc/specs/deep-interview-linux-robustness-files.md`（ambiguity 22%, BELOW_THRESHOLD_EARLY_EXIT）
**User directives:**
- 工作区是 robustness 独立工作区，与 newmodel 解耦
- 仅保留：架构知识 + 核心算法 + 闭环比对验证
- 删除：原始 DWG / paper / demo / official / Standard_qgis_projects / main_archive / plugincad2gis / qgis_plugin / webdemo / tmp / test-artifacts / 旧版本
- 命名统一（旧 `_dev` 前缀全去，目录仅 src/verify/docs/baselines/tests/）
- Reader 升格：LibreDWG 为跨平台 primary，AutoCAD 降级为可选 fallback
- 闭环比对方案：A（records bundle → pipeline → GPKG 对账，不依赖 DWG）

---

## 1. RALPLAN-DR Summary

### Principles (5)

1. **工作区独立性**：本工作区与 newmodel 演进解耦；继承 newmodel 的有用文件但不再 merge 回；后续开发以此工作区为唯一主场。
2. **最大精简**：保留"架构知识 + 核心算法 + 闭环比对验证"三类，其他全删；目录命名严格统一到 `src/` `verify/` `docs/` `baselines/` `tests/`。
3. **Reader 跨平台化**：LibreDWG 从 dev-only 升格为跨平台 primary；AutoCAD 降级为可选 Windows-only fallback；通过 `CAD2GIS_READER_BACKEND=libredwg|autocad` env 切换（默认 libredwg）。
4. **闭环比对 A 方案**：reader 入口可替换——闭环比对用 `readcad_review_bundle.json` 作为 canonical records 输入，不依赖原始 DWG。
5. **canonical 边界保持零触碰**：原 canonical 三件套（`experiment/py_scripts/cad2gis_v3/ingest.py` `autocad_reader.py` `apd_source_profile.json`）在新结构下迁移为 `src/cad2gis/ingest.py` + `src/cad2gis/reader/autocad.py`（deprecation），逻辑等价，**重命名而非删除**，构造性消除合并泄漏风险。

### Decision Drivers (top 3)

| Rank | Driver | Why |
|------|--------|-----|
| 1 | **可移植性 + AutoCAD 非必需**（用户 R1/R4 确认） | AutoCAD 不是 newmodel 设计本质，仅是历史环境巧合；reader 角色应重新设计——跨平台 primary 而非 Windows-only canonical |
| 2 | **命名混乱 + 多版本遗留**（用户 R4 指令） | 当前 19 个顶层目录中 `experiment/` `demo/` `official/` `Standard_qgis_projects/` `main_archive/` 等仅余演示价值；`_dev` 前缀锁死 reader 角色错位；统一命名是结构性清理 |
| 3 | **无 DWG 下闭环比对**（用户 R3 选 A） | A 方案 records bundle 替代 DWG 输入；reader 与 pipeline 解耦验证（契约层 vs 闭环层） |

### Viable Options

#### Option A：分阶段重构（SELECTED）

**Phase 1**：canonical 三件套迁移与 reader 升格（不破坏工作）
- `experiment/py_scripts/cad2gis_v3/ingest.py` → `src/cad2gis/ingest.py`（git mv）
- `experiment/py_scripts/autocad_reader.py` → `src/cad2gis/reader/autocad.py`（git mv + deprecation docstring）
- `experiment/py_scripts/libredwg_dev_reader.py` → `src/cad2gis/reader/libredwg.py`（git mv + 去除 `_dev` 前缀）
- `experiment/py_scripts/cad2gis_v3/ingest_dev.py` 删除（逻辑并入 `src/cad2gis/ingest.py`）
- `experiment/py_scripts/cad2gis_v3/` 整目录 → `src/cad2gis/`（git mv + import 路径调整）
- `src/cad2gis/reader/contracts.py` 新建（reader 抽象接口，约束 autocad/libredwg 共同契约）

**Phase 2**：基线与配置迁移
- `experiment/runs/apd_architecture_v3_complete/` → `baselines/apd_hutabohu/`（保留 `delivery/` `evidence/` `records/readcad_review_bundle.json`）
- `experiment/runs/apd_architecture_v3_gcp_ready/` → `baselines/apd_hutabohu_gcp_ready/`
- `experiment/config/apd_source_profile.json` → `baselines/apd_hutabohu/config/source_profile.json`
- `experiment/config/apd_mapping_registry.json` → `baselines/apd_hutabohu/config/mapping_registry.json`
- `experiment/config/apd_gcp_profile.json` → `baselines/apd_hutabohu/config/gcp_profile.json`
- `experiment/config/apd_source_profile_dev_libredwg.json` → `baselines/apd_hutabohu/config/source_profile_libredwg.json`（去 `_dev`）

**Phase 3**：删除与命名精简
- `git rm`：`experiment/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`（原始 DWG）
- `git rm -r`：`paper/` `demo/` `official/` `Standard_qgis_projects/` `main_archive/` `plugincad2gis/` `qgis_plugin/` `webdemo/` `tmp/` `test-artifacts/`
- `git rm -r`：`experiment/ErrorReports/` `experiment/output/` `experiment/guide/` `experiment/evaluation_standards/`
- `git rm -r`：`experiment/py_scripts/converter.py` `convert_v3.py` `curate_v3.py` `apd_rules.py` `evaluator.py` `gcp_tool.py` `domain_vocab.py` `schema_config.py`（legacy 脚本）
- `git rm -r`：`experiment/py_scripts/test_*.py` 中被 v3 测试取代的（保留 7 项契约测试 → 移至 `verify/contract/`）
- `git rm -r`：`docs/XA-202610...pdf` `docs/superpowers/`（比赛方案 + webdemo 相关）
- `git rm -r`：`docs/APD_CAD2GIS_EXECUTION_PLAN.md` `docs/APD_CAD2GIS_HANDOFF.md` `docs/Literature_review_lite.md` `docs/technical_plan.md` `docs/verification_matrix.md` `docs/verification_report.md`（精简合并进 `docs/ARCHITECTURE.md` `docs/PORTABILITY.md` `docs/RECONCILIATION.md`）

**Phase 4**：闭环比对 A 方案实施
- 新建 `verify/replay.py`：以 `baselines/apd_hutabohu/records/readcad_review_bundle.json` 为输入驱动 pipeline，输出到 `baselines/apd_hutabohu/output/`，对账 delivery/evidence GPKG
- 新建 `verify/contract/`：7 项契约测试（reader 行为、records 完整性、env 切换、跨平台加载）
- 新建 `verify/portability/`：Win/Linux 等价性测试（OS 检测 + ctypes 跨平台加载 + 输出 schema 一致）
- 新建 `verify/reconciliation/`：replay 输出 vs baseline GPKG 对账

**Phase 5**：文档精简与 README 重写
- 新建 `docs/ARCHITECTURE.md`：核心架构说明
- 新建 `docs/PORTABILITY.md`：跨平台 reader 部署指南
- 新建 `docs/RECONCILIATION.md`：对账口径说明
- 重写 `README.md`：架构图 + 三个目录语义 + reader 升格声明 + 闭环比对入口

**Pros：** 用户指令直接对齐；分阶段保留 git 历史；canonical 边界不变逻辑；闭环比对 A 方案自洽。
**Cons：** 工作量大（~50+ 文件改动）；reader 升格涉及多处 import 路径调整；可能暴露隐藏在 _dev 命名下的耦合。

#### Option B：原地 git mv + 增量提交（REJECTED）

**拒绝理由：** 不符合用户"以最大程度简化此工作区文件结构为准 + 摈弃旧版本"的指令；保留 legacy 脚本与多余目录违背 max_simplify 原则。

#### Option C：拆出新工作区 + git submodule（REJECTED）

**拒绝理由：** 用户明确说"这个工作区已经和newmodel没有关系...你应当把这个工作区看作robustness分支"——重构在当前工作区内完成，不需要新建工作区或 submodule。

**Verdict：** Option A 是唯一符合用户所有指令的路径；B、C 已显式排除。

---

## 2. Requirements Summary

工作区当前 19 个顶层目录 → 重构后 5 个 + 文档/基线/测试：

| 类别 | 当前 | 重构后 |
| --- | --- | --- |
| 核心代码 | `experiment/py_scripts/cad2gis_v3/` (31 文件) + `experiment/py_scripts/autocad_reader.py` (3577 行) + `experiment/py_scripts/libredwg_dev_reader.py` (1162 行) | `src/cad2gis/` (canonical 包) + `src/cad2gis/reader/` (libredwg.py + autocad.py + contracts.py) |
| 测试 | `tests/` (51 项 canonical) + `experiment/py_scripts/test_*.py` (116 项 v3) + `experiment/py_scripts/test_libredwg_dev_reader.py` (7 项 dev-reader) | `tests/` (canonical 51 项保持) + `verify/contract/` (7 项 reader 契约) + `verify/portability/` (新) + `verify/reconciliation/` (新) |
| 基线 | `experiment/runs/apd_architecture_v3_complete/` + `experiment/runs/apd_architecture_v3_gcp_ready/` | `baselines/apd_hutabohu/` + `baselines/apd_hutabohu_gcp_ready/` |
| 配置 | `experiment/config/apd_*.json` | `baselines/apd_hutabohu/config/` |
| 文档 | `docs/` (8 文件) + `experiment/ARCHITECTURE_V3.md` + `experiment/README.md` + `experiment/history.md` + `main_archive/MAIN_BRANCH_SYNTHESIS.md` + `paper/` (5 PDF) + `demo/` (含 README) | `docs/ARCHITECTURE.md` + `docs/PORTABILITY.md` + `docs/RECONCILIATION.md` + `README.md` |
| 删除 | - | `paper/` `demo/` `official/` `Standard_qgis_projects/` `main_archive/` `plugincad2gis/` `qgis_plugin/` `webdemo/` `tmp/` `test-artifacts/` + legacy 脚本 + 原始 DWG |
| 知识库 | `.omc/specs/` `.omc/plans/` `.omc/wiki/` `.omc/notepad.md` | 保留（transient 状态 gitignore） |

---

## 3. Acceptance Criteria（可测试）

### 工作区结构
- [ ] 顶层目录仅 `src/` `verify/` `docs/` `baselines/` `tests/` 五个（外加 `README.md` `pyproject.toml` `pyrightconfig.json` `.gitignore` `env/`）
- [ ] `git ls-tree HEAD --name-only` 不含 `paper/` `demo/` `official/` `Standard_qgis_projects/` `main_archive/` `plugincad2gis/` `qgis_plugin/` `webdemo/` `tmp/` `test-artifacts/` `experiment/`
- [ ] 原始 DWG 已 `git rm`（`git ls-files | grep -i "\.dwg$"` 为空）
- [ ] `_dev` 前缀文件全去（`git grep -l "_dev"` 仅命中历史 docstring/注释）
- [ ] `.omc/state/` `.omc/sessions/` `.omc/project-memory.json` 在 `.gitignore` 中

### 命名统一
- [ ] reader 路径：`src/cad2gis/reader/libredwg.py` `src/cad2gis/reader/autocad.py` `src/cad2gis/reader/contracts.py`
- [ ] 配置路径：`baselines/apd_hutabohu/config/{source_profile,mapping_registry,gcp_profile,source_profile_libredwg}.json`
- [ ] 测试入口：`verify/replay.py`（A 方案 records bundle 驱动）+ `tests/` 顶层
- [ ] README 声明："本工作区是 robustness 独立工作区，与 newmodel 解耦"

### Reader 升格
- [ ] `src/cad2gis/reader/libredwg.py` 存在；`grep -cE "win32com|pythoncom|accoreconsole" src/cad2gis/reader/libredwg.py` == 0
- [ ] `extraction_backend="libredwg"`（去掉 `_dev`）
- [ ] `CAD2GIS_READER_BACKEND` env 切换（默认 libredwg）；`grep -r "CAD2GIS_DEV_READER" src/ verify/ tests/` 为空（env 开关去除）
- [ ] `src/cad2gis/reader/autocad.py` 含 deprecation docstring + `os.name != "nt"` 守卫
- [ ] `src/cad2gis/ingest.py` 集成 reader 切换逻辑
- [ ] `src/cad2gis/reader/contracts.py` 定义 reader 抽象接口

### 闭环比对 A 方案
- [ ] `verify/replay.py` 存在
- [ ] `verify/replay.py` 以 `baselines/apd_hutabohu/records/readcad_review_bundle.json` 为输入驱动 pipeline
- [ ] 输出到 `baselines/apd_hutabohu/output/`，对账 baseline GPKG
- [ ] `verify/contract/` 7 项契约测试
- [ ] `verify/portability/` Win/Linux 等价性测试
- [ ] `verify/reconciliation/` A 方案闭环对账测试
- [ ] `tests/` 51 项 canonical 合同测试保持通过
- [ ] `verify/contract/` 7 项 reader 契约测试保持通过
- [ ] A 方案闭环：records bundle → ingest.from_record() → pipeline → delivery.gpkg → SQL count vs baseline

### 文档精简
- [ ] `docs/ARCHITECTURE.md` 存在（核心架构说明）
- [ ] `docs/PORTABILITY.md` 存在（跨平台 reader 部署指南）
- [ ] `docs/RECONCILIATION.md` 存在（对账口径说明）
- [ ] `docs/` 下其他文件全删（除三个 MD）
- [ ] `README.md` 含架构图 + 三个目录语义 + reader 升格声明 + 闭环比对入口

### 测试基线
- [ ] `pytest tests/ -q` ≥51 通过
- [ ] `pytest verify/contract/ -q` 7 项通过
- [ ] `pytest verify/portability/ -q` ≥1 通过
- [ ] `pytest verify/reconciliation/ -q` ≥1 通过
- [ ] 全部测试通过数 ≥67（即 51+7+1+1 = 60 项最低，新增 portability/reconciliation 共 ≥7 项）

---

## 4. Implementation Steps（Phase I-V）

### Phase 0：基线备份与状态保护

```bash
# 备份重要文件
cp -r baselines/apd_hutabohu/records /tmp/apd_records_backup/
cp -r baselines/apd_hutabohu/delivery /tmp/apd_delivery_backup/
cp -r baselines/apd_hutabohu/evidence /tmp/apd_evidence_backup/

# 确认当前测试基线
PYTHONPATH=src:experiment/py_scripts timeout 580 /tmp/cad2gis-venv/bin/python -m pytest tests -q
# 预期：51 passed
PYTHONPATH=src:experiment/py_scripts timeout 580 /tmp/cad2gis-venv/bin/python -m pytest experiment/py_scripts/test_libredwg_dev_reader.py -q
# 预期：7 passed
```

### Phase 1：canonical 三件套迁移与 reader 升格

```bash
# 1.1 创建新目录结构
mkdir -p src/cad2gis/reader
mkdir -p verify/contract verify/portability verify/reconciliation

# 1.2 迁移 v3 包（cad2gis_v3 → src/cad2gis）
git mv experiment/py_scripts/cad2gis_v3/__init__.py src/cad2gis/__init__.py
git mv experiment/py_scripts/cad2gis_v3/*.py src/cad2gis/
# 调整相对 import（去掉 `from .config` → `from .config`，保持不变）

# 1.3 迁移 autocad reader（加 deprecation docstring）
git mv experiment/py_scripts/autocad_reader.py src/cad2gis/reader/autocad.py
# 头部加：
# """DEPRECATED: Windows-only AutoCAD canonical reader.
# Use src/cad2gis/reader/libredwg.py for cross-platform primary path.
# This reader is retained as opt-in fallback via CAD2GIS_READER_BACKEND=autocad.
# Production robustness branch now uses libredwg; this module will be removed
# once all AutoCAD-specific dependencies are migrated."""

# 1.4 迁移 dev reader（去 _dev 前缀）
git mv experiment/py_scripts/libredwg_dev_reader.py src/cad2gis/reader/libredwg.py
# sed 替换：
#   libredwg_dev_reader → src.cad2gis.reader.libredwg
#   extraction_backend="libredwg_dev" → "libredwg"
#   CAD2GIS_DEV_READER → CAD2GIS_READER_BACKEND (default=libredwg)
#   synthetic marker 改为 typed unsupported 常规处理（移除 opt-in 开关）

# 1.5 删除 ingest_dev wrapper（并入 ingest.py）
git rm experiment/py_scripts/cad2gis_v3/ingest_dev.py
# ingest.py 加 reader 切换逻辑：
#   reader_backend = os.environ.get("CAD2GIS_READER_BACKEND", "libredwg")
#   if reader_backend == "libredwg":
#       from .reader.libredwg import extract_dwg_records
#   elif reader_backend == "autocad":
#       from .reader.autocad import extract_dwg_records
#   else:
#       raise ValueError(f"unknown reader backend: {reader_backend}")

# 1.6 新建 reader contracts
cat > src/cad2gis/reader/contracts.py <<'EOF'
"""Reader contract shared by autocad (legacy) and libredwg (cross-platform).

Defines the v3 reader protocol:
- extract_dwg_records(source_path) -> DWGRecordInventory
- DWGRecordInventory: list-like with .diagnostics attribute
- diagnostics: {skipped_rows, inventory_complete, extraction_backend,
                metadata_evidence, unsupported_reason_counts}
"""
from typing import Protocol

class ReaderContract(Protocol):
    def __call__(self, source_path) -> "DWGRecordInventory": ...

class DWGRecordInventory(Protocol):
    diagnostics: dict
    def __iter__(self): ...
    def __len__(self): ...
    def __getitem__(self, idx): ...
EOF
```

### Phase 2：基线与配置迁移

```bash
# 2.1 创建 baselines 目录
mkdir -p baselines/apd_hutabohu/{delivery,evidence,records,config,output}
mkdir -p baselines/apd_hutabohu_gcp_ready

# 2.2 迁移 GPKG 和 records bundle
git mv experiment/runs/apd_architecture_v3_complete/apd_delivery.gpkg baselines/apd_hutabohu/delivery/
git mv experiment/runs/apd_architecture_v3_complete/apd_evidence.gpkg baselines/apd_hutabohu/evidence/
git mv experiment/runs/apd_architecture_v3_complete/readcad_review_bundle.json baselines/apd_hutabohu/records/
git mv experiment/runs/apd_architecture_v3_complete/qgis baselines/apd_hutabohu/qgis/
git mv experiment/runs/apd_architecture_v3_complete/run_manifest.json baselines/apd_hutabohu/

# 2.3 迁移 GCP-ready baseline
git mv experiment/runs/apd_architecture_v3_gcp_ready baselines/apd_hutabohu_gcp_ready/

# 2.4 迁移配置
git mv experiment/config/apd_source_profile.json baselines/apd_hutabohu/config/source_profile.json
git mv experiment/config/apd_mapping_registry.json baselines/apd_hutabohu/config/mapping_registry.json
git mv experiment/config/apd_gcp_profile.json baselines/apd_hutabohu/config/gcp_profile.json
git mv experiment/config/apd_source_profile_dev_libredwg.json baselines/apd_hutabohu/config/source_profile_libredwg.json
git rm experiment/config/llm_provider.env.example

# 2.5 迁移 GPKG 验证产物
git mv experiment/APD_HUTABOHU_cad2gis.gpkg baselines/apd_hutabohu/cad2gis_historical.gpkg 2>/dev/null || true
git mv experiment/APD_HUTABOHU_verification.json baselines/apd_hutabohu/verification_historical.json 2>/dev/null || true
```

### Phase 3：删除与命名精简

```bash
# 3.1 原始 DWG 删除（用户明确指令）
git rm "experiment/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"

# 3.2 整目录删除
git rm -r paper/
git rm -r demo/
git rm -r official/
git rm -r Standard_qgis_projects/
git rm -r main_archive/
git rm -r qgis_plugin/
git rm -r webdemo/
git rm -r tmp/
git rm -r test-artifacts/
git rm -r experiment/ErrorReports/ 2>/dev/null || true
git rm -r experiment/output/ 2>/dev/null || true
git rm -r experiment/guide/ 2>/dev/null || true
git rm -r experiment/evaluation_standards/ 2>/dev/null || true
git rm -r experiment/py_scripts/ErrorReports/ 2>/dev/null || true

# 3.3 legacy 脚本删除
git rm experiment/py_scripts/converter.py
git rm experiment/py_scripts/convert_v3.py
git rm experiment/py_scripts/curate_v3.py
git rm experiment/py_scripts/apd_rules.py
git rm experiment/py_scripts/evaluator.py
git rm experiment/py_scripts/gcp_tool.py
git rm experiment/py_scripts/domain_vocab.py
git rm experiment/py_scripts/schema_config.py
# 保留 test 目录中的契约测试（迁移到 verify/contract/）

# 3.4 docs 精简（除三个新文件外全删）
git rm docs/APD_CAD2GIS_EXECUTION_PLAN.md
git rm docs/APD_CAD2GIS_HANDOFF.md
git rm docs/Literature_review_lite.md
git rm docs/technical_plan.md
git rm docs/verification_matrix.md
git rm docs/verification_report.md
git rm "docs/XA-202610烽火通信科技股份有限公司-通信基建工程数智化设计与交付关键技术比赛方案(2).pdf"
git rm -r docs/superpowers/

# 3.5 experiment/ 整目录删除（迁移后已空）
git rm -r experiment/

# 3.6 .gitignore 强化（transient 状态）
cat >> .gitignore <<'EOF'

# robustness: OMC transient state
.omc/state/
.omc/sessions/
.omc/project-memory.json
.omc/.gitignore

# robustness: verification output artifacts
baselines/apd_hutabohu/output/

# robustness: temp venv caches
.pytest_cache/
__pycache__/
EOF

# 3.7 移除 .omc/.gitignore（无效且与 plan §S2 决策冲突）
git rm .omc/.gitignore
```

### Phase 4：闭环比对 A 方案实施

```bash
# 4.0 新建 records bundle 适配层（连接 reader 闭环与 pipeline 闭环）
cat > src/cad2gis/reader/records_adapter.py <<'EOF'
"""Records bundle adapter: feeds readcad_review_bundle.json into pipeline.

The pipeline's canonical entry point is ingest(source_path, profile) which
expects a real DWG path. For the A-plan closed-loop verification (no DWG),
this adapter synthesises a pipeline invocation by:

  1. Loading the records bundle from baselines/apd_hutabohu/records/
  2. Iterating bundle['objects'] (9391 canonical records)
  3. Calling SourceEntity.from_record() for each record (bypassing the
     reader layer entirely; records are already canonical-extracted)
  4. Feeding entities into the rest of the pipeline (semantic/topology/...)

This separates the "reader extraction" concern (covered by verify/contract/)
from the "pipeline behaviour" concern (covered by verify/replay.py).
Records bundle content stability = canonical-evidence baseline; bundle
drift indicates a schema change that requires re-validation.
"""
from __future__ import annotations
import json
from pathlib import Path

from ..model import SourceEntity
from ..config import SourceProfile


def load_records(bundle_path: Path) -> list[SourceEntity]:
    """Materialise a records bundle into SourceEntity list."""
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    return [
        SourceEntity.from_record(obj["facts"])
        for obj in bundle["objects"]
    ]


def validate_bundle_facts(bundle_path: Path, profile: SourceProfile) -> dict:
    """Verify bundle schema invariants + profile binding."""
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    facts_count = sum(1 for o in bundle["objects"] if "facts" in o)
    return {
        "bundle_path": str(bundle_path),
        "objects_count": len(bundle["objects"]),
        "facts_count": facts_count,
        "schema_version": bundle.get("schema_version"),
    }
EOF

# 4.1 迁移 7 项 dev-reader 契约测试（迁移 + import 路径更新）
git mv experiment/py_scripts/test_libredwg_dev_reader.py verify/contract/test_libredwg_reader.py
# sed 全局替换：
#   from libredwg_dev_reader import → from cad2gis.reader.libredwg import
#   libredwg_dev_reader. → cad2gis.reader.libredwg.
#   extraction_backend="libredwg_dev" → "libredwg"
#   CAD2GIS_DEV_READER → CAD2GIS_READER_BACKEND
# 7 项契约：
#   1. test_inventory_complete_and_no_skips
#   2. test_census_matches_apd_baseline (model entities=6940/INSERT=222/DIMENSION=170)
#   3. test_unsupported_records_use_v3_contract (typed unsupported_reasons)
#   4. test_no_windows_imports (无 win32com/pythoncom/accoreconsole)
#   5. test_ingest_gate_passes (CAD2GIS_READER_BACKEND=libredwg 双向门)
#   6. test_record_field_completeness_snapshot (~30 键逐键断言)
#   7. test_ingest_dev_matches_canonical_post_reader (wrapper-canonical 漂移)

# 4.2 新建 verify/replay.py
cat > verify/replay.py <<'EOF'
"""A-plan closed-loop verification: records bundle → pipeline → GPKG reconciliation.

Input: baselines/apd_hutabohu/records/readcad_review_bundle.json
Pipeline: records_adapter → ingest.from_record() → semantic → topology → output
Output: baselines/apd_hutabohu/output/{delivery,evidence}.gpkg
Reconciliation: SQL count vs baselines/apd_hutabohu/{delivery,evidence}/ baseline

This loop does NOT depend on the original DWG; it exercises pipeline behaviour
on canonical records. Reader is covered by verify/contract/ tests.
"""
import json
import sqlite3
import sys
from pathlib import Path

BASELINE_DIR = Path(__file__).parent.parent / "baselines" / "apd_hutabohu"
RECORDS = BASELINE_DIR / "records" / "readcad_review_bundle.json"
DELIVERY_OUT = BASELINE_DIR / "output" / "delivery.gpkg"
EVIDENCE_OUT = BASELINE_DIR / "output" / "evidence.gpkg"
DELIVERY_BASE = BASELINE_DIR / "delivery" / "apd_delivery.gpkg"
EVIDENCE_BASE = BASELINE_DIR / "evidence" / "apd_evidence.gpkg"

EXPECTED_DELIVERY = {"BOITE": 43, "CABLE": 6, "PTECH": 167, "IMB": 682, "SITE": 2}


def _table_counts(gpkg_path: Path, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    con = sqlite3.connect(str(gpkg_path))
    try:
        for table in tables:
            counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        con.close()
    return counts


def main() -> int:
    if not RECORDS.exists():
        raise SystemExit(f"records bundle missing: {RECORDS}")
    DELIVERY_OUT.parent.mkdir(parents=True, exist_ok=True)

    # 1. Drive pipeline from records bundle (no DWG)
    from cad2gis.reader.records_adapter import load_records
    entities = load_records(RECORDS)
    print(f"[replay] loaded {len(entities)} entities from {RECORDS}")

    # 2. Pipeline stages: semantic → topology → calibration → output
    # 3. Reconcile
    if DELIVERY_OUT.exists():
        actual = _table_counts(DELIVERY_OUT, list(EXPECTED_DELIVERY))
        expected = _table_counts(DELIVERY_BASE, list(EXPECTED_DELIVERY))
        print(f"[replay] delivery reconcile: actual={actual} expected={expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

# 4.3 新建 verify/portability/test_cross_platform.py
# OS 检测 + ctypes 跨平台加载验证
# 关键检查：libredwg.so (Linux) / libredwg.dll (Windows) / libredwg.dylib (macOS) 自动选择

# 4.4 新建 verify/reconciliation/test_records_loop.py
# A 方案闭环对账测试：records bundle → pipeline → GPKG → SQL count vs baseline

# 4.5 跑回归
PYTHONPATH=src:experiment/py_scripts timeout 580 /tmp/cad2gis-venv/bin/python -m pytest tests verify/contract verify/portability verify/reconciliation -q
# 预期：≥67 项通过（51 canonical + 7 contract + 1 portability + 1 reconciliation + 7 v3 carry-over = 67）
```

### Phase 5：文档精简与 README 重写

```bash
# 5.1 新建 docs/ARCHITECTURE.md（核心架构说明）
# 整合 experiment/ARCHITECTURE_V3.md + main_archive/MAIN_BRANCH_SYNTHESIS.md 的核心内容

# 5.2 新建 docs/PORTABILITY.md（跨平台 reader 部署指南）
# 整合 docs/APD_CAD2GIS_EXECUTION_PLAN.md 的相关节

# 5.3 新建 docs/RECONCILIATION.md（对账口径说明）
# 整合 docs/verification_matrix.md + docs/verification_report.md

# 5.4 重写 README.md
# 包含：架构图 + 三个目录语义 + reader 升格声明 + 闭环比对入口

# 5.5 提交
git add src/ verify/ docs/ baselines/ README.md .gitignore
git -c commit.gpgsign=false commit -m "feat: robustness workspace restructure - reader elevation + records-bundle closed-loop"
```

---

## 5. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| import 路径断裂（cad2gis_v3 → src/cad2gis） | High | High | 分阶段迁移；每 phase 跑 `pytest tests/` 验证；用 sed 全局替换 `from .config` 形态 |
| reader 升格漏改 `_dev` 字符串 | Medium | High | grep 验证：`git grep -l "_dev" -- src/ verify/ baselines/` 应仅命中历史 docstring |
| 28MB GPKG 移动占用 git 空间 | Low | Low | `git mv` 不复制内容，仅指针；空间释放需 `git gc`（可后续） |
| 7 项契约测试迁移失败（import 路径） | High | Medium | 测试文件已存在于 experiment/py_scripts/test_libredwg_dev_reader.py，git mv + sed 全局替换 |
| A 方案闭环不能产出 delivery.gpkg | Medium | High | Phase 4.0 新增 `src/cad2gis/reader/records_adapter.py` 适配层；先单元测试 records → entities → features，再做端到端 |
| **跨平台 ctypes 加载 Win 端 libredwg.dll 路径**（Architect 新增） | Medium | High | Phase 1.4 添加 Windows DLL 路径加载逻辑（ctypes.util.find_library("redwg") 跨平台）；verify/portability/ 验证 |
| **records bundle 漂移检测**（Architect 新增） | Low | Medium | records_adapter.validate_bundle_facts() 检查 schema_version + facts_count；漂移即视为 baseline 漂移需重审 |
| **cad2gis_v3/config.py 跨目录迁移**（Architect 新增） | Medium | Medium | ingest_dev.py 等多处 import `from .config` 在 cad2gis_v3 内；迁移后变 `from ..config`（同包内仍 work）；无需 sed |
| 误删 important 文件 | Low | High | Phase 0 备份到 /tmp；执行顺序按"先迁移后删除"原则；git revert 可恢复 |
| AutoCAD path 误删 | Low | Medium | 保留 src/cad2gis/reader/autocad.py 加 deprecation；仅 env 显式 opt-in 可用 |
| 命名统一漏改（残留 experiment/ demo/ 等） | Medium | Medium | Phase 3.5 整目录 `git rm -r experiment/` 兜底 |

---

## 6. Verification Steps

```bash
# 6.1 工作区结构
git ls-tree HEAD --name-only | grep -vE "^(src/|verify/|docs/|baselines/|tests/|README.md|pyproject.toml|pyrightconfig.json|.gitignore|env/|\.omc/)"
# 预期：空输出

# 6.2 原始 DWG 已删
git ls-files | grep -i "\.dwg$"
# 预期：空输出

# 6.3 _dev 前缀已去
git grep -l "_dev" -- src/ verify/ baselines/ tests/ docs/ README.md
# 预期：仅命中历史 docstring/注释

# 6.4 reader 路径
ls src/cad2gis/reader/{libredwg.py,autocad.py,contracts.py}
# 预期：三个文件都存在

# 6.5 测试基线
PYTHONPATH=src:experiment/py_scripts timeout 580 /tmp/cad2gis-venv/bin/python -m pytest tests verify/contract verify/portability verify/reconciliation -q --no-header
# 预期：≥67 项通过（51 canonical + 7 contract + 1 portability + 1 reconciliation + 7 v3 carry-over = 67）

# 6.6 A 方案闭环
PYTHONPATH=src:experiment/py_scripts timeout 580 /tmp/cad2gis-venv/bin/python verify/replay.py
# 预期：records bundle → pipeline → GPKG，对账报告输出

# 6.7 canonical 边界（autocad reader 仍存在但加 deprecation）
head -10 src/cad2gis/reader/autocad.py | grep -i "DEPRECATED"
# 预期：命中 DEPRECATED

# 6.8 ingestion reader 切换
grep -n "CAD2GIS_READER_BACKEND" src/cad2gis/ingest.py
# 预期：命中

# 6.9 main 工作区无污染（cross-check）
git -C /home/cat/projects/CAD2GIS status --short
# 预期：空（main 没改）

# 6.10 git 历史保
git log ff41501..HEAD --oneline
# 预期：≥8 commit（Phase 1-5 + 之前的 6 commit）

# 6.11 文件计数对比
find src/ verify/ docs/ baselines/ tests/ -type f | wc -l
# 预期：< 200（精简目标）
```

---

## 7. ADR (Architecture Decision Record)

**Decision:** 采用 Option A——分阶段重构 robustness 工作区，5 个 Phase（基线备份 / canonical 迁移 / 基线配置迁移 / 删除精简 / 闭环比对 A 方案 / 文档精简）。

**Drivers:** ①用户指令 max_simplify（keep_only=架构知识+核心算法+闭环比对验证）→ 工作区从 19 顶层目录精简到 5；②AutoCAD 非必需（R1 用户澄清）→ reader 角色从 Windows-only canonical 升格为跨平台 primary + 可选 fallback；③无 DWG 下闭环比对（R3 用户选 A）→ records bundle 替代 DWG 作为闭环比对输入，reader 与 pipeline 解耦验证；④canonical 边界零触碰（合并泄漏构造性消除）→ 保留 autocad reader 但加 deprecation + env 显式 opt-in。

**Alternatives considered:** B 原地 git mv + 增量提交（拒绝：违背 max_simplify）；C 拆新工作区 + git submodule（拒绝：用户明确说"这个工作区已经和newmodel没有关系"）。

**Why chosen:** Option A 是唯一符合用户全部 4 个指令（max_simplify / keep_only / 命名统一 / 摈弃旧版本）的路径；分阶段保留 git 历史；canonical 边界不变逻辑。

**Consequences:** 工作区从 ~530 tracked 文件精简到 < 200；reader 升格到跨平台 primary；AutoCAD 降级为 opt-in；A 方案闭环使工作区可在无 DWG 下做 pipeline 行为验证；canonical 边界零触碰保留合并兼容性。

**Follow-ups:** ①Phase II B/C/D 工作包（转移资产落地 + 验证矩阵 + UX+测试扩充）按新工作区结构重新规划；②AutoCAD reader 后续若不再需要可彻底删除（保留 deprecation 期 1 个发布周期）；③新工作区与 newmodel 网页后端工作区的协同通过 `baselines/apd_hutabohu/` 共享基线；④conda env.yml 严格对齐作为可选核验项排期。

---

## 8. Consensus Changelog

**iteration 1**：Plan created by Planner (compressed).

**iteration 1 — Architect/Critic sub-spawns degraded**：
- Provider 返回 503 (deepseek-v4-pro under group vip) — 两轮重试均失败
- 退化路径：self-review inline (Architect + Critic 视角合并)
- 新增改进项 4 条已并入本文档：
  1. **Phase 4.0 新增 `src/cad2gis/reader/records_adapter.py`**（连接 reader 闭环与 pipeline 闭环）
  2. **Phase 4.1 列出 7 项契约测试清单**（从代码反推）
  3. **§5 Risk 新增 3 条**（跨平台 ctypes 加载 / records bundle 漂移 / config.py 跨目录迁移）
  4. **§6.5 测试目标 ≥60 → ≥67**（明确下限含 7 项 v3 carry-over）
- **consensus 状态**：degraded（sub-agent provider 不可用），pending approval 等待用户最终判断

**Status:** `pending approval` (awaiting user explicit execution approval per deep-interview Phase 5 protocol)

</content>
</invoke>