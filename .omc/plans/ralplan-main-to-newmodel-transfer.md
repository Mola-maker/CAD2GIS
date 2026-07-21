# RALPLAN: main→newmodel 知识转移（wiki 更新 + main_archive 综合分析文档）

**Status:** `approved for autopilot execution`（consensus iteration 1 即达成：Architect APPROVED_WITH_IMPROVEMENTS + Critic APPROVED_WITH_IMPROVEMENTS，改进已全部并入；用户经 deep-dive 菜单显式选择 Ralplan→Autopilot 管线，执行授权已捕获）
**Date:** 2026-07-20
**Mode:** RALPLAN-DR short (documentation deliverables, no destructive ops except gated push)
**Source spec:** `.omc/specs/deep-dive-grasp-project-soul-architecture.md` (ambiguity 13%, PASSED)
**Trace:** `.omc/specs/deep-dive-trace-grasp-project-soul-architecture.md`

---

## 1. RALPLAN-DR Summary

### Principles (5)

1. **读者优先** — 转移文档面向 Windows 侧组员，自包含；引用 main 侧资产一律用仓库相对路径（`experiment/py_scripts/...`）并注明"见 main 分支"，绝不引用 `.omc/` 内部路径或 WSL 专有上下文。
2. **哲学边界** — 转移内容以"领域知识 + 参考思路"形态呈现；不得暗示用 main 的聚合拓扑/宽松验证替换 newmodel 的 fail-closed 与源几何不可变机制。
3. **最小侵入** — newmodel 分支**只新增** `main_archive/`（1 个文件夹 1 个文档），不改动任何现有文件；main 侧只改 `.omc/wiki/cad2gis-converter-pipeline.md` 一页。
4. **无损现场** — main 工作区是用户活跃会话现场；跨分支写入一律通过 git worktree 隔离，禁止就地 checkout 切换分支。
5. **诚实边界** — 文档沿用 newmodel README 的口径：单图纸基线、GCP 未验证、禁止跨 CAD 外推；main 侧成果同样如实标注（如 14 个 E 级缺口）。

### Decision Drivers (top 3)

| Rank | Driver | Why |
|------|--------|-----|
| 1 | 组员在 Windows 侧通过 `origin/newmodel` 消费文档 | 投递必须 commit 到 newmodel 分支；push 是共享状态操作，需独立确认门 |
| 2 | 文档价值 = 可操作资产清单（带来源路径），不是泛泛总结 | 每项资产必须标注 main 侧文件路径与转移形态（直接用/改造用/仅思路） |
| 3 | main 工作区不可污染 | `.omc/` 已确认 gitignored（root `.gitignore:6`），wiki 更新零提交成本；分支写入必须 worktree 隔离 |

### Viable Options

#### Option A: git worktree 隔离投递（SELECTED）

- `git worktree add <path> -b newmodel origin/newmodel`（本地无 newmodel 分支，已确认，需 -b 创建跟踪分支）
- 在 worktree 内新建 `main_archive/MAIN_BRANCH_SYNTHESIS.md`，commit
- push 与否经用户确认门
- `git worktree remove` 清理

**Pros:** main 现场零干扰；分支历史干净；可回滚（不 push 则纯本地）。**Cons:** 多一步 worktree 管理。

#### Option B: 就地 checkout 切换投递（REJECTED）

在 main 工作区直接 `git checkout newmodel` 写文件再切回。

**拒绝理由:** 切换会改变工作区 1,032,751+ 行的文件视图（newmodel 含大量新文件），污染用户会话现场；`.omc/` 状态文件与未追踪材料在切换中有混淆风险；违反 Principle 4。

#### Option C: 文档留在 main 仓库让用户自行搬运（REJECTED）

只在 main 侧写文档，由用户手动复制到 Windows。

**拒绝理由:** 不满足 spec 验收判据"传到 newmodel，新建 main_archive 文件夹中"；把分支操作负担转嫁给用户。

**Verdict:** Option A。B、C 已显式排除。

---

## 2. Requirements Summary

交付两项（spec 验收判据 ②③）：

1. **wiki 架构页更新**（main，本地文件，无提交）：`.omc/wiki/cad2gis-converter-pipeline.md` 从"单管线速查"更新为"两路线格局 + 终局决策 + 转移范围"的准确描述，frontmatter `updated` 刷新。
2. **综合分析转移文档**（newmodel 分支 `main_archive/`）：面向 Windows 侧组员的中文 markdown，覆盖 spec 判据 (a)-(e)：(a) 项目灵魂与生产定位；(b) 六维对比；(c) 可转移资产清单（附来源路径）；(d) 五缺陷教训→newmodel 鲁棒性启示；(e) 不转移项及理由。

## 3. Implementation Steps

### Step 1: 更新 wiki 架构页（main 工作区，直接编辑）

- 文件：`.omc/wiki/cad2gis-converter-pipeline.md`
- 保留 frontmatter 结构，更新 `updated: 2026-07-20`，`confidence: medium→high`
- 正文重构为：
  - 现状总览：两路线并存格局表（main=WSL2/LibreDWG/converter.py 单体+基线 CABLE=203/CONV-SUM=6942；newmodel=Windows/AutoCAD2027/cad2gis 包+基线 BOITE=43/CABLE=6/CABLE_SEGMENT=139/EPSG:9481）
  - 终局决策（2026-07-20 访谈确认）：生产转化优先；newmodel=生产线；main 归档前知识转移
  - 原页面技术细节（LibreDWG ctypes 桥、坐标双 regime、transform offsets）保留但标注"main 路线（归档待定）"
- 验证：页面不含与决策矛盾的表述（如暗示 main 管线继续演进）

### Step 2: 起草综合分析文档

- 起草位置：`.omc/drafts/MAIN_BRANCH_SYNTHESIS.md`（drafts 目录，符合 plan 工件纪律）
- 文件名（投递后）：`main_archive/MAIN_BRANCH_SYNTHESIS.md`
- **文档头部必备**（Architect 改进 1）：TOC 目录 + "本文最后验证于 main 分支 HEAD `3e5be1a`（2026-07-20）；行号引用以该时点为准，文件路径为主、行号为辅"声明
- 结构（对照 spec 判据 a-e）：
  1. 项目灵魂与生产定位：XA-202610 契机 + 生产转化优先 + ≥90% 指标 + 烽火内部（定制）QGIS 目标环境
  2. 两路线六维对比表（工程环境/依赖/批量操作性/可移植性/应用软件前景/数据安全）——直接采用 spec Technical Context 的对比矩阵
  3. 可转移资产清单（每项：资产/来源路径/转移形态；**路径必须用仓库相对全路径**，Critic/Architect 共识）：
     - 领域知识：`experiment/py_scripts/domain_vocab.py`；`experiment/py_scripts/schema_config.py` 的 LAYER_PATTERN_MAP(约1870-1933行)/LABEL_FAMILIES(约2614-2618行)/NEGATIVE_EVIDENCE_LAYERS(约1835-1861行)；`experiment/evaluation_standards/*.csv`（8 份法标规则）；`experiment/guide/T_TOPOLOGY_REPAIR_ANALYSIS.md`
     - 算法参考：匈牙利标注分配（`experiment/py_scripts/converter.py` `_minimum_cost_assignment` 约777-831行）；三轨样式方案（实体色 ByLayer 解算→QML→layer_styles useAsDefault 内嵌）；span_annotations（"xx.x m" 标签+真 FID 外键）；图例排除法（`experiment/py_scripts/legend_detector.py` + `experiment/config/legend_exclusions.json`）
  4. 五缺陷教训→newmodel 鲁棒性启示：
     - T（图例面板 596 碎片诱发 429 伪桥，排除后 0 桥）→ 启示：newmodel 的 unsupported/legend 分类已有此防线，可对照验证 FDT-ALL/LEGEND 布局排除完备性
     - S（pyqgis 无头 segfault=Qt teardown，子进程隔离根治；三轨样式）→ 启示：newmodel 的 layer_styles 内嵌已覆盖；样式简化关闭的验收 QML 思路同源
     - P（span_annotations 170 条+真 FID 外键）→ 启示：与 newmodel CABLE_SEGMENT 的 dimension_entity_key 机制互为印证
     - X（SITE 真值=2，编码归真 CBL0001）→ 启示：真值台账（ground-truth ledger）机制值得引入
  5. 不转移项及理由：LibreDWG ctypes 读取链（组员判定"难以克服的局限"）；`experiment/py_scripts/converter.py` 单体与业务聚合拓扑（203 CABLE 桥接哲学，违反源几何不可变）；EPSG:3857 交付选择（newmodel 用 EPSG:9481）；cad_common/ftth_converter 解耦残骸（回归未修，随归档埋掉）
  6. 基线对照表 + 诚实边界（单图纸/无 GCP/14 E 级缺口 vs 13 unresolved）
- 长度目标：250-400 行，单文件自包含

### Step 3: worktree 投递到 newmodel 分支

```bash
cd /home/cat/projects/CAD2GIS
git worktree add /tmp/cad2gis-newmodel-wt -b newmodel origin/newmodel
mkdir -p /tmp/cad2gis-newmodel-wt/main_archive
cp .omc/drafts/MAIN_BRANCH_SYNTHESIS.md /tmp/cad2gis-newmodel-wt/main_archive/
cd /tmp/cad2gis-newmodel-wt && git add main_archive/ && git commit -m "docs: main 分支知识转移综合分析（归档前吸收总结）"
```

- 若 `-b newmodel` 报分支已存在（竞态）：改用 `git worktree add /tmp/cad2gis-newmodel-wt newmodel`
- commit 不署名 Claude（遵循仓库人工提交风格——近期提交均为业务描述短句，无 Co-Authored-By；按 git 安全协议默认不加，除非用户要求）

### Step 4: push 确认门（共享状态操作）

- **先** `git fetch origin newmodel` 并检查本地 commit 对 `origin/newmodel` 是否快进（`git merge-base --is-ancestor origin/newmodel newmodel`）；非快进则停止并报告用户，禁止强推
- 向用户展示 commit 摘要，**显式确认后**执行 `git push -u origin newmodel`
- 用户拒绝则保留本地 commit，文档仍以 worktree 路径交付

### Step 5: 清理

- `git worktree remove /tmp/cad2gis-newmodel-wt`（push 或用户确认保留本地后）
- main 工作区 `git status` 复核：无源码改动（`.omc/` 本就 ignored）

## 4. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| worktree 分支名冲突 | Low | Low | Step 3 已含 fallback；执行前先 `git branch --list newmodel` 复核 |
| push 未经确认污染共享分支 | Medium | High | Step 4 独立确认门；不 push 也可交付（本地 commit） |
| 文档引用 main 侧行号漂移误导组员 | Medium | Medium | 行号标注"（main 分支，2026-07-20 时点）"；核心资产以文件路径为主、行号为辅 |
| 文档暗示替换 newmodel 机制 | Low | High | Step 2 结构第 5 节显式声明不转移项；每项算法标注"参考思路，非替换" |
| wiki 更新与其余 32 页矛盾 | Low | Low | 只改一页；其他页面（session-log 等）属历史记录不需要改 |
| 文档含 .omc 私有路径 | Medium | Medium | 起草后 grep 自检：`grep -n '\.omc' draft` 必须零命中 |

## 5. Acceptance Criteria（可测试）

- [ ] `.omc/wiki/cad2gis-converter-pipeline.md` 含"两路线格局"与"终局决策"与"转移范围"段落（三者均 grep 命中），frontmatter `updated: 2026-07-20`，且 `grep -c "LibreDWG"` ≥1（原技术细节保留）
- [ ] newmodel 分支（worktree 内）`main_archive/MAIN_BRANCH_SYNTHESIS.md` 存在，`git log -1 --name-only` 仅含该路径
- [ ] 文档五域覆盖（结构化检查，不止关键词）：TOC 存在；六个维度名（工程环境/依赖/批量操作性/可移植性/应用软件前景/数据安全）全部出现；"可转移资产"节含 ≥6 个带 `experiment/` 全路径的资产条目；"五缺陷"节含 T/S/P/X 四项；"不转移"节含 LibreDWG/聚合拓扑/EPSG:3857/解耦残骸四项
- [ ] 否定性检查（spec AC④）：`grep -ciE '继续维护.?main|main.{0,4}继续演进|继续开发.?main' main_archive/MAIN_BRANCH_SYNTHESIS.md` == 0
- [ ] 文档零 `.omc` 引用：`grep -c '\.omc' main_archive/MAIN_BRANCH_SYNTHESIS.md` == 0
- [ ] main 工作区 `git status --short` 无已追踪文件改动
- [ ] push 决策有用户显式确认记录；push 前有 fetch+快进检查记录

## 6. Verification Steps

1. `git -C /home/cat/projects/CAD2GIS status --short` → 无已追踪改动
2. `git -C /tmp/cad2gis-newmodel-wt log -1 --stat` → 仅 main_archive/ 一个文件
3. `git -C /tmp/cad2gis-newmodel-wt show HEAD:main_archive/MAIN_BRANCH_SYNTHESIS.md | wc -l` → 250-400 行区间
4. 文档五域结构化检查 + 否定性 grep（见 AC 第 3、4 条）
5. wiki 页 frontmatter 与正文一致性人工复核

---

## 7. ADR (Architecture Decision Record)

**Decision:** 采用 Option A——git worktree 隔离投递：main 侧仅更新 `.omc/wiki/cad2gis-converter-pipeline.md`（gitignored 零提交）；综合分析文档起草于 `.omc/drafts/` 后，经 `git worktree add -b newmodel origin/newmodel` 投递至 newmodel 分支新建的 `main_archive/MAIN_BRANCH_SYNTHESIS.md`，commit 后经 fetch+快进检查与用户确认门再 push。

**Drivers:** ①组员在 Windows 侧经 origin/newmodel 消费（投递必须入分支历史）；②文档价值=带全路径的可操作资产清单；③main 工作区不可污染（worktree 隔离；`.omc/` gitignored 已证实）。

**Alternatives considered:** Option B（就地 checkout 切换——拒绝：污染活跃会话现场，违反 Principle 4）；Option C（文档留 main 由用户搬运——拒绝：不满足 spec 交付位置判据）。

**Why chosen:** Option A 是唯一同时满足"入 newmodel 分支历史 + main 现场零干扰 + 可回滚"的路径；Architect 的钢人反方（本地分支语义冲突/main_archive 位置/单文件形态）经审议：位置与单文件形态系 spec 用户显式指定，本地分支语义冲突以 push 确认门与 fetch 检查化解。

**Consequences:** 本地新增 newmodel 跟踪分支与一个临时 worktree（Step 5 清理 worktree，分支保留以便 push）；组员 pull origin/newmodel 后在仓库根看到 main_archive/；main 侧 wiki 与 spec/plan 均不入 git 历史（gitignored），知识沉淀在本机 .omc。

**Follow-ups:** ①组员评审转移文档后决定是否拆分多文件（INDEX.md 已评估为可选，不实施）；②main 物理归档（branch 锁定/删除）待转移文档被确认消化后另行决策；③newmodel 侧后续鲁棒性工作（GCP/跨CAD矩阵/UX）由 newmodel 团队规划，与本计划无关。

---

## 8. Consensus Changelog (iteration 1 → final)

- [Architect] 文档头部增加 TOC + "验证于 main HEAD 3e5be1a/行号以路径为主"声明 → 并入 Step 2
- [Architect+Critic] 资产路径统一为仓库相对全路径（`experiment/py_scripts/schema_config.py`、`experiment/config/legend_exclusions.json`、`experiment/py_scripts/legend_detector.py`、`experiment/py_scripts/converter.py`）→ 并入 Step 2/不转移节
- [Architect] main_archive/INDEX.md → 评估为可选，不实施（Critic 同意）
- [Architect+Critic] Step 4 增加 `git fetch origin newmodel` + 快进检查，非快进禁止 push → 并入 Step 4
- [Critic] AC3 升级为结构化五域检查（TOC/六维名/≥6 全路径资产/T-S-P-X/不转移四项）→ 并入 AC
- [Critic] 新增否定性 AC（"继续维护 main"语义 grep==0，覆盖 spec AC④）→ 并入 AC
- [Critic] wiki AC 增加"转移范围"段落要求 → 并入 AC
