# Deep Interview Spec: Phase 2 — 空间过滤 LLM 集成

## Metadata
- Interview ID: di-20260730-phase2-legend-llm
- Rounds: 11 (+ Round 0 topology)
- Final Ambiguity Score: 15.8%
- Type: brownfield
- Generated: 2026-07-30
- Threshold: 0.2
- Threshold Source: default
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.88 | 0.35 | 0.307 |
| Constraint Clarity | 0.84 | 0.25 | 0.209 |
| Success Criteria | 0.81 | 0.25 | 0.201 |
| Context Clarity | 0.83 | 0.15 | 0.124 |
| **Total Clarity** | | | **0.842** |
| **Ambiguity** | | | **15.8%** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| scene_partition 迁移至聚合层 | active | 将 `detect_style_catalog_entities` 调用从 `plan_domain.build_plan_domain` 移至 `pipeline.py`，输出通过 `PlanDomainView.catalog_roots: frozenset[str]` 承载 | Round 8/10: PlanDomainView 新增 `catalog_roots` 字段，plan_domain 不再做过滤 |
| spatial_regions.json schema 正式化 | active | 集群快照格式：`{cluster_id, member_ids, disposition, confidence, justification, provenance}` | Round 3/7: 精确 entity_key 匹配 + provenance 审计字段 |
| LLM supervisor 运行时集成 | active | `--llm observe/assist` 时自动触发 LLM 对未确认集群做语义判断；LLM 失败时用 detector confidence > 0.7 确定性回退 | Round 1/5/6/11: 6 输入/3 输出接口，自动触发，方案B降级，可配置提示词模板 |
| evidence ledger disposition | active | source.gpkg 添加 `legend_flag` 字段（source 枚举），不改 `build_stage_evidence_graph` | Round 4/9: GPKG + semantic_diagnostics，不做 evidence_graph 改造 |

## Goal

将 scene_partition 和 legend_detector 两个独立空间探测器统一到聚合层，通过 LLM supervisor 的运行时语义判断和 per-project 可配置提示词模板，对 Model 空间的空间集群做 disposition 分类（legend/derived_noise/technical_diagram/annotation_frame/subject），标记结果通过 source.gpkg 的 `legend_flag` 字段和 semantic_diagnostics 可视化审计。LLM 失败时用探测器自有 confidence > 0.7 做确定性回退。绝不硬编码项目特定的空间结构知识——所有领域知识通过自然语言提示词模板承载。

## Constraints

- **绝不硬编码项目知识**：LLM 系统提示词从 per-project 可配置模板加载（`spatial_prompt.md` 或 `spatial_regions.json` 的 `prompt` 字段），不包含 Hutabohu/Lamteh/Kletek 等具体项目的空间结构描述
- **不修改 `detect_style_catalog_entities` 算法**：只改调用位置和返回值传递方式
- **不修改 `detect_legend_clusters` 算法**：Phase 1 已稳定的 gap 聚类逻辑不变
- **不修改 `build_stage_evidence_graph`**：evidence graph 保持纯属性快照，disposition 通过 GPKG 字段 + semantic_diagnostics 承载
- **LLM 自动触发**：`--llm observe/assist` 且存在未确认集群时自动调用，不需要 decision_pack 作为前提
- **LLM 降级路径**：LLM 调用失败（API 错误/超时/非法输出）→ 探测器 confidence > 0.7 的集群自动标记为探测器建议的 disposition，其他标记 UNCERTAIN
- **--llm off 降级**：仅应用 spatial_regions.json 中已确认的集群快照（精确 entity_key 匹配），无配置 = 全通过 + 诊断输出
- **不改变已验证基线**：Hutabohu / Lamteh SF 的转换结果必须可复现

## Non-Goals

- 不重写 `detect_style_catalog_entities` 的形状指纹算法
- 不修改 evidence_graph 的数据结构
- 不在 Python 代码中硬编码任何项目特定的空间结构知识
- 不为 spatial_regions.json 创建独立的 JSON Schema 校验器（格式校验由 LLM supervisor 输出 + 人工 review 保证）
- 不在 Phase 2 中处理 LLM 决策写回 spatial_regions.json 的自动化（人工 review 后手动或半自动写回是 Phase 3 的范畴）

## Acceptance Criteria

- [ ] `PlanDomainView` 新增 `catalog_roots: frozenset[str]` 字段，承载 `detect_style_catalog_entities` 的检测结果
- [ ] `plan_domain.build_plan_domain` 不再过滤 output——catalog_roots 中的实体保留在 entities 中，标记信息通过 catalog_roots 字段传递
- [ ] `pipeline.py` 聚合层统一处理两个探测器的输出：`plan_domain.catalog_roots` + `filter_legend_entities.legend_flagged_keys`
- [ ] `spatial_regions.json` 的集群快照格式包含：`cluster_id`, `member_ids`, `disposition`, `confidence`, `justification`, `provenance`（detector, llm_model, llm_decision, llm_confidence, confirmed_by, confirmed_at）
- [ ] LLM supervisor 输入侧：集群 bbox、采样文本（集群内实体 text 字段去重后的前 N 条）、锚文本命中、layer 分布、centroid 坐标、相对 body_bbox 位置偏移
- [ ] LLM supervisor 输出侧：JSON 格式 `{disposition, confidence, justification}`，disposition 枚举 = `legend | derived_noise | technical_diagram | annotation_frame | subject`
- [ ] LLM 系统提示词从 per-project 可配置模板加载（优先 `spatial_prompt.md`，回退到 `spatial_regions.json` 的 `prompt` 字段，无配置时使用通用 FTTH 提示词）
- [ ] `--llm observe/assist` 且存在未确认集群时自动触发 LLM 调用（不需要 decision_pack）
- [ ] LLM 调用失败时：探测器 confidence > 0.7 → 自动标记为探测器类型对应的 disposition；其他 → UNCERTAIN
- [ ] `source.gpkg` 写入时添加 `legend_flag` 字段，source 枚举值：`""`（未标记）/ `"scene_partition"` / `"legend_detector"` / `"llm_legend"` / `"llm_derived_noise"` / `"llm_technical_diagram"` / `"llm_annotation_frame"`
- [ ] `run_manifest.json` 的 `semantic_diagnostics.legend_spatial` 包含 flagged_entity_keys、clusters、LLM 决策日志
- [ ] Hutabohu 用现有 mapping_registry（无 spatial_regions.json、无 LLM）转换结果与基线一致

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| evidence graph 需要 disposition 节点 | Contrarian: 真的需要吗？ | 不需要——GPKG legend_flag 字段 + semantic_diagnostics 足够 |
| plan_domain 内部过滤就行 | 迁移到聚合层才能统一两个探测器 | PlanDomainView 新增 catalog_roots 字段，过滤移至 pipeline |
| LLM 调用需要 decision_pack 触发 | 空间过滤是独立的决策类型 | 自动触发——存在未确认集群 + --llm observe/assist |
| LLM 失败应该阻塞转换 | 图纸转换不应依赖 LLM 可用性 | 方案 B：探测器 confidence > 0.7 确定性回退 |
| 项目知识可以硬编码在提示词中 | 鲁棒性来自泛用性 | 绝不硬编码——per-project 可配置模板 + 自然语言推理 |
| legend_flag 只需要布尔值 | reviewer 需要区分检测来源 | 来源枚举：scene_partition / legend_detector / llm_* |

## Technical Context

### 关键代码点
- `plan_domain.py:505-519` — `detect_style_catalog_entities` 调用 + output 过滤（需改为只检测不过滤）
- `plan_domain.py:46-49` — `PlanDomainView` dataclass（新增 `catalog_roots` 字段）
- `pipeline.py:1188-1215` — Phase 1 已插入的 legend 过滤块（扩展为聚合层）
- `pipeline.py:1314-1339` — LLM observe/assist 路径（当前仅对接 decision_pack，需扩展空间过滤分支）
- `evidence_graph.py:336-343` — `build_stage_evidence_graph`（不修改）
- `source_gpkg.py` — `write_source_gpkg`（添加 legend_flag 字段写入）
- `curation_providers/openai_compatible.py:67` — `OpenAICompatibleProvider.review()`（可复用为 LLM supervisor 的调用接口）

### 聚合层数据流
```
plan_domain.build_plan_domain()
  → PlanDomainView(entities, diagnostics, catalog_roots)    ← 新字段

filter_legend_entities(semantic_entities)
  → {entities, legend_flagged_keys, diagnostics}

聚合层:
  all_flagged = catalog_roots | legend_flagged_keys
  未确认 = all_flagged - spatial_regions.json 中的 confirmed member_ids
  已确认 = spatial_regions.json 中的 confirmed member_ids → 按 disposition 处理

  if --llm observe/assist and 未确认:
    LLM supervisor(未确认集群) → dispositions
  elif LLM 失败:
    探测器 confidence > 0.7 → auto-disposition

write_source_gpkg(entities, legend_flag_map)  ← 新参数
```

### LLM 接口契约
```json
// 输入 (context dict)
{
  "clusters": [{
    "cluster_id": "LC-001",
    "detector": "legend_detector",
    "bbox": [x1, y1, x2, y2],
    "member_count": 496,
    "sampled_text": ["FDT", "LEGEND", "DESIGN SUMMARY", ...],
    "anchor_hits": ["FDT", "LEGEND"],
    "layer_distribution": {"0": 120, "FDT": 45, ...},
    "centroid": [x, y],
    "body_bbox_offset": {"x_offset": 2.3, "y_offset": 0.1}
  }],
  "body_bbox": [...],
  "total_entities": 3472
}

// 输出 (LLM response JSON)
{
  "decisions": [{
    "cluster_id": "LC-001",
    "disposition": "legend",
    "confidence": 0.95,
    "justification": "位于主体左侧，偏移 2.3x 主体跨度，包含 LEGEND/FDT 锚文本，图层分布以 0/TEXT_INFO 为主——典型图例区"
  }]
}
```

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| SpatialCluster | core domain | cluster_id, detector, bbox, member_ids, member_count, sampled_text, anchor_hits, layer_distribution, centroid, body_bbox_offset | detected by Detector, judged by LLMSupervisor |
| Detector | supporting | source (scene_partition / legend_detector), flagged_keys | produces SpatialCluster |
| LLMSupervisor | external system | mode (off/observe/assist), prompt_template, model, decision_log | reads SpatialCluster, writes ClusterDecision |
| ClusterDecision | core domain | cluster_id, disposition, confidence, justification | produced by LLMSupervisor or Detector fallback |
| ClusterSnapshot | configuration | cluster_id, member_ids, disposition, confidence, justification, provenance | stored in spatial_regions.json |
| Provenance | supporting | detector, llm_model, llm_decision, llm_confidence, confirmed_by, confirmed_at | records decision lineage |
| LegendFlag | supporting | entity_key, source (scene_partition / legend_detector / llm_*) | written to source.gpkg |
| PromptTemplate | configuration | project_id, system_prompt, spatial_context_description | stored per-project, loaded by LLMSupervisor |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 5 | 5 | - | - | N/A |
| 3 | 6 (LLMInterface added) | 1 | 0 | 5 | 83% |
| 5 | 7 (DecisionFallback added) | 1 | 0 | 6 | 86% |
| 7 | 8 (Provenance added) | 1 | 0 | 7 | 88% |
| 9 | 9 (LegendFlag added, EvidenceNode removed) | 1 | 1 | 7 | 89% |
| 11 | 9 (PromptTemplate added, LLMInterface merged into LLMSupervisor) | 0 | 1 | 8 | 100% |

## Interview Transcript
<details>
<summary>Full Q&A (11 rounds + Round 0)</summary>

### Round 0 (Topology)
**Q:** 4个顶层组件：scene_partition迁移、spatial_regions.json schema、LLM supervisor集成、evidence ledger disposition
**A:** 对

### Round 1
**Q:** LLM supervisor 的输入输出接口？
**A:** 输入：集群bbox、采样文本、锚文本命中、layer分布、centroid坐标、相对body_bbox偏移。输出：disposition + confidence + justification

### Round 2
**Q:** 两个探测器各以什么形式产出结果？
**A:** 各自返回 flagged_keys: frozenset[str] + diagnostics

### Round 3
**Q:** spatial_regions.json 存区域定义还是集群快照？
**A:** 集群快照：{cluster_id, member_ids, disposition, confidence, justification}，精确 entity_key 匹配

### Round 4 (Contrarian)
**Q:** evidence graph 真的需要 disposition 节点？
**A:** 不需要——GPKG legend_flag 字段 + semantic_diagnostics 可视化

### Round 5
**Q:** LLM 调用自动触发还是需要 decision_pack？
**A:** 自动触发——存在未确认集群 + --llm observe/assist

### Round 6 (Simplifier)
**Q:** LLM 调用失败时的降级策略？
**A:** 方案B——探测器 confidence > 0.7 确定性回退，其他 UNCERTAIN

### Round 7
**Q:** 集群快照需要 provenance 字段吗？
**A:** 需要——记录谁发现的、谁决策的、什么时候

### Round 8 (Ontologist)
**Q:** scene_partition 结果通过 diagnostics 还是新字段传递？
**A:** PlanDomainView 新增字段承载

### Round 9
**Q:** legend_flag 字段的值是布尔、来源枚举、还是多字段？
**A:** 方案B——来源枚举："" / "scene_partition" / "legend_detector" / "llm_*"

### Round 10
**Q:** PlanDomainView 新字段叫什么？
**A:** catalog_roots

### Round 11
**Q:** LLM 提示词硬编码还是可配置？
**A:** 方案B——可配置模板，绝不硬编码项目知识，鲁棒性来自自然语言推理

</details>
