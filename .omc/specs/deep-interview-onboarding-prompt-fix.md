# Deep Interview Spec: onboarding 提示词修复 — annotation_families 结构校验

## Metadata
- Interview ID: di-20260731-onboarding-prompt-fix
- Rounds: 6 (+ Round 0 topology)
- Final Ambiguity Score: 19.5%
- Type: brownfield
- Generated: 2026-07-31
- Threshold: 0.2
- Threshold Source: default
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.83 | 0.35 | 0.292 |
| Constraint Clarity | 0.77 | 0.25 | 0.193 |
| Success Criteria | 0.79 | 0.25 | 0.198 |
| Context Clarity | 0.82 | 0.15 | 0.123 |
| **Total Clarity** | | | **0.805** |
| **Ambiguity** | | | **19.5%** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| 提示词内容修正 | active | 删除误导性示例锚点（DMPH-X.XX.XXX, MR.DMPH.P###），改为 L1+L2 分层校验指令 | 引导模型生成跨层隔离 + 层内多家族 + 颜色维度的 families |
| 样本数据增强 | active | _text_samples 动态扩展 + aci_color + 双层负样本 | 样本按前缀家族动态扩展，每个家族 ≥8 个；负样本 = 其他层 + 本层其他家族 |
| admission 正则校验 | active | L1 跨层语义隔离 + L2 层内家族分组（token 覆盖 + 匈牙利分配）+ 修复循环 | 结构错误触发修复循环（≤2 次），2 次后放行 |

## Goal

修复 AI onboarding 的 annotation_families 生成质量：v4-flash 当前生成的 regex 全部不匹配实际 DWG 文本（annotation_family_match=0.00），根因是提示词含错误示例锚点 + 无结构校验。修复后：AI 生成的 families 必须满足 L1（跨层语义隔离）和 L2（层内家族正确分组），字段结构错误通过修复循环（≤2 次）自愈，字面变体（空格/点/大小写）容忍。

## Constraints

- **L1 跨层语义隔离（硬约束）**：每个 family regex 必须匹配本层样本，且不能匹配其他层的负样本
- **L2 层内家族分组**：同一 layer 多家族（如 BOITE 的 DMPH-1/2 家族 vs 16/48/72 家族）用 token 覆盖区分，匈牙利分配验证样本归属
- **结构完整性（大问题）**：regex 必须覆盖观察到的全部字段层级（如 `EXT.MR.MF.LBB.S02.P*` 的 6 个字段），不能漏字段、不能用 `.*` 吞掉中间字段
- **字面变体（不重要）**：分隔符（空格/点/斜杠）、大小写差异不校验、不阻塞
- **颜色维度（方案 B）**：样本带 aci_color；family schema 增加可选 `aci_color` 字段——运行时 regex 匹配后按颜色过滤。用于 PTECH cyan/purple 同文本不同色的场景
- **修复循环（方案 2）**：结构错误 → 失败信息（缺失字段、该层样本、regex）回传 LLM 重生成该 family；最多 2 次；2 次后放行（失败 family 保留在 diagnostics，不阻塞 onboarding）
- **运行时匈牙利分配独立**：现有 `_assign_family_annotations`（semantics.py:251，位置驱动的 annotation→feature 匹配）不修改，L1/L2 只作用于文本过滤层
- **不改变已验证基线**：噪声去除（spatial supervisor）表现优秀（1.00），不触碰

## Non-Goals

- 不重写 `_assign_family_annotations` 的运行时匈牙利分配
- 不修改 spatial_llm.py 的噪声去除提示词（v4-flash 已表现优秀）
- 不做 regex AST 解析（结构校验用字面 token 覆盖，非语义等价分析）
- 不改变 admission 的事务性（失败仍回滚 draft）

## Acceptance Criteria

- [ ] `_text_samples` 输出改为 `{layer: [{"text": ..., "aci_color": N}, ...]}`，按前缀家族动态扩展样本数（每家族 ≥8）
- [ ] annotation_families schema 增加可选 `aci_color` 字段
- [ ] 系统提示词删除 `DMPH-X.XX.XXX, MR.DMPH.P###` 示例锚点
- [ ] 提示词引导模型：按图层生成 family（L1 跨层隔离）；同层多家族时按 token 区分（L2）；同文本不同颜色时按 aci_color 分 family 或注释
- [ ] `compile_onboarding_proposal` 增加 L1 校验：family regex 匹配本层样本 ≥ 阈值且不匹配其他层负样本
- [ ] `compile_onboarding_proposal` 增加 L2 校验：同层多家族时用 token 覆盖 + 匈牙利分配验证样本归属
- [ ] 结构错误触发修复循环：失败信息回传 LLM 重生成，≤2 次
- [ ] 2 次修复后仍失败 → 放行，失败 family 从 proposal 删除并写入 diagnostics
- [ ] 字面变体（空格/点/大小写）不触发修复循环
- [ ] 运行时 `_assign_family_annotations` 按 aci_color 过滤（当 family 声明了 aci_color）
- [ ] 重新生成 4 个 baseline 后：annotation_family_match > 0（当前 0.00）
- [ ] Hutabohu 的 `EXT.MR.MF.LBB.S02.P*` 家族被正确提取（当前全丢失）

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| regex 必须匹配全部样本 | 空格/点差异会误杀有效 family | 字面变体容忍；校验改为字段结构覆盖率 |
| 校验失败应该阻塞 onboarding | 工程上字段变体不影响理解 | 字面变体不阻塞；结构错误修复循环 2 次后放行 |
| 文本模式是唯一的标签区分维度 | PTECH cyan/purple 文本相同颜色不同 | 方案 B：样本带 aci_color + family 支持 aci_color 过滤 |
| 固定 8 样本/层足够 | lamteh_main 前缀更长更复杂 | 动态按前缀家族扩展（每家族 ≥8） |
| regex AST 分析才能校验结构 | 工程量过大 | 字面 token 覆盖：regex 字符串中必须字面出现关键字段 token |

## Technical Context

### 当前代码（onboarding.py）
- `_text_samples`（line 232）：`{layer: [text...]}`, limit_per_layer=8, max_layers=80
- 系统提示词（line 871-885）：含 `e.g. DMPH-X.XX.XXX, MR.DMPH.P###` 错误锚点
- `_compile_annotation_families`（line 617）：AI families → 完整 registry 格式
- `compile_onboarding_proposal`（line 741）：admission dry-run，无 regex 校验
- `validate_onboarding_proposal`（line 500 附近）：schema 校验

### 运行时分配（semantics.py，不修改）
- `_minimum_cost_assignment`（line 196）：矩形匈牙利
- `_assign_family_annotations`（line 251）：位置驱动 annotation→feature 分配
- multiple_optima（两 target 距离 ≤ 0.01m）→ 失败（红点青点坐标太近场景）

### 标签类型学（领域知识，从实际 DWG 验证）
| 类 | 文本模式 | 颜色 | 备注 |
|----|---------|------|------|
| PTECH | `EXT.MR.MF.LBB.S02.P*` | 红 | 曾因校验失败全丢失 |
| PTECH | `MR.DMPH.P*` | 青/紫 | 文本相同，颜色区分 |
| BOITE | `DMPH-1/2.010.A/B/C*` | 橙 | 与 PTECH 位置重合；另有 16/48/72 并列家族 |
| CABLE | 名义长度数值 | - | 欧氏距离有 3 位小数偏差 |
| IMB | 住宅信息 | - | 独立 |

### 修复循环数据流
```
compile_onboarding_proposal
  → L1 校验 (跨层隔离)
  → L2 校验 (token 覆盖 + 匈牙利分配)
  → 结构错误? → 回传 LLM (缺失字段 + 样本) → 重生成该 family → 重验
     ↑ 最多 2 次
  → 2 次后放行: 失败 family 删除, 写入 diagnostics
```

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| AnnotationFamily | core domain | family_id, text_pattern, target_class, max_distance_native_m, aci_color | matches AnnotationSample, assigned to Feature |
| AnnotationSample | supporting | text, aci_color, layer | provided to LLM in bundle |
| NegativeSample | supporting | text, layer | cross-layer + same-layer-other-family |
| TokenCoverage | value object | tokens, coverage_ratio | used in L2 validation |
| L1Validation | value object | matched_samples, rejected_negatives, status | cross-layer isolation check |
| L2Validation | value object | family_assignments, hungarian_cost, status | intra-layer grouping check |
| RepairLoop | supporting | attempts (max 2), failure_feedback, regenerated_family | structural-error recovery |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 3 (L1Validation, L2Validation, RepairLoop) | 3 | - | - | N/A |
| 2 | 5 (AnnotationSample + aci_color, NegativeSample) | 2 | 0 | 3 | 60% |
| 4 | 6 (TokenCoverage added) | 1 | 0 | 5 | 83% |
| 6 | 6 (all stable) | 0 | 0 | 6 | 100% |

## Interview Transcript
<details>
<summary>Full Q&A (6 rounds + Round 0)</summary>

### Round 0 (Topology)
**Q:** 3 个组件：提示词内容修正 / 样本数据增强 / admission 正则校验
**A:** 对，三个都要

### Round 1
**Q:** "修好"的验收标准？A 样本全匹配 / B 覆盖率 / C 负样本
**A:** A 与 C 混合模式

### Round 2
**Q:** 样本增强参数？（动态按家族 / 简单提高 / 跳过）
**A:** 动态样本 + 颜色 + 双层负样本（推荐）

### Round 3
**Q:** 校验失败处理？（拒绝整个 / 丢弃 family / 反馈修复循环）
**A:** 用户纠正：字段结构错误是大问题（EXT.MR.MF.LBB.S02.P* 漏字段），字面变体不重要（FAT 空格/点）

### Round 4 (Contrarian)
**Q:** 程序真的能自动校验 regex 结构覆盖吗？（AST 解析 vs 字面 token）
**A:** 跨图层语义严格划分（L1）；层内分组用 token 覆盖 + 匈牙利分配（L2）

### Round 5
**Q:** 颜色维度如何进入？（A 样本带颜色 / B 样本带颜色 + family aci_color 过滤）
**A:** B — 样本带 aci_color + family schema 增加 aci_color 字段

### Round 6 (Simplifier)
**Q:** 结构错误的处理？（拒绝 family / 修复循环 / 拒绝整个）
**A:** 方案 2 — 修复循环，≤2 次，2 次后放行

</details>
