# Deep Interview Spec: 空间图例过滤接入语义管线

## Metadata
- Interview ID: di-20260730-legend-filter-integration
- Rounds: 8 (+ Round 0 topology)
- Final Ambiguity Score: 17.3%
- Type: brownfield
- Generated: 2026-07-30
- Threshold: 0.2
- Threshold Source: default
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.85 | 0.35 | 0.298 |
| Constraint Clarity | 0.82 | 0.25 | 0.204 |
| Success Criteria | 0.80 | 0.25 | 0.200 |
| Context Clarity | 0.83 | 0.15 | 0.125 |
| **Total Clarity** | | | **0.827** |
| **Ambiguity** | | | **17.3%** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| 适配器 + 管线接入 | active | 从 libredwg.py 删除孤立 `_filter_legend_clusters`；在 `legend_detector.py` 新增 `filter_legend_entities()` 适配器；在 `pipeline.py` 的 `build_plan_domain` 之后、`classify_entities` 之前插入调用 | 适配器返回 `legend_flagged_keys: frozenset[str]` + diagnostics，调用方决定如何注入证据账本 |
| 首轮运行策略 | active | `--llm off` 时仅应用已确认配置，空配置 = 全通过 + 诊断输出；`--llm observe/assist` 时 LLM 参与对未确认集群的语义判断 | 三条降级路径已定义（见 Constraints） |
| 排除配置持久化 | active | 按项目独立存储于 `baselines/<project>/config/spatial_regions.json`；包含语义区域定义（bbox/成员 + disposition 枚举）；LLM 可在 observe/assist 模式下提案新的区域条目 | 最终输出是 LLM 监督者通过自然语义 prompt 调校的区域配置 |

## Goal

将两个独立的空间噪声探测器（`scene_partition` 的形状指纹检测 + `legend_detector` 的空间 gap 聚类）接入语义管线，通过统一的 `spatial_regions.json` 配置和 LLM 监督者的运行时语义判断，实现跨项目（Hutabohu / Lamteh MAIN+SF / Kletek）的 Model 空间区域语义识别——区分主体（转换）、图例（排除）、住宅剥离子图（排除）、技术图（排除）、注释框噪声（排除）。

## Constraints

- **不修改 `detect_legend_clusters` 算法逻辑**：保持 legend_detector.py 的 gap 聚类算法不变
- **不修改 `detect_style_catalog_entities` 算法逻辑**：保持 scene_partition.py 的形状指纹算法不变
- **不修改 `SourceEntity` frozen dataclass**：适配器输出独立 frozenset，不污染实体模型
- **两条探测器独立运行**：各有各的输出，最终由聚合层统一
- **`--llm off` 降级路径**：
  1. `spatial_regions.json` 有已确认条目 → 确定性应用
  2. `spatial_regions.json` 为空（新项目首跑） → 所有实体通过，探测器输出写入 diagnostics 和 evidence ledger
  3. `spatial_regions.json` 缺失 → 同空配置，不报错
- **`--llm observe` 模式**：LLM 对未确认集群提案 disposition，人工确认后写入 spatial_regions.json
- **`--llm assist` 模式**：LLM 自主判断未确认集群的 disposition，可选回写配置
- **不改变已验证基线**：Hutabohu / Lamteh SF 的转换结果必须可复现
- **按项目独立配置**：每个 baseline 有自己的 `spatial_regions.json`

## Non-Goals

- 不重写 legend_detector 的 gap 聚类算法
- 不合并 scene_partition 和 legend_detector 的检测逻辑
- 不在 reader 层做空间过滤（reader 保持无状态）
- 不改变现有 `project_profile.py` 的 inventory 构建流程
- 不修改 `plan_domain.py` 的核心展开逻辑（仅从其中删除 scene_partition 的调用者角色，改为在聚合层统一调用）

## Acceptance Criteria

- [ ] `libredwg.py` 中的 `_filter_legend_clusters` 函数已移除，`extract_dwg_records` 无调用痕迹
- [ ] `legend_detector.py` 新增 `filter_legend_entities(entities: Iterable[SourceEntity], *, min_confidence=0.25) -> dict` 适配函数，返回 `{"entities": list, "legend_flagged_keys": frozenset[str], "diagnostics": {...}}`
- [ ] `pipeline.py:convert_project` 在 `build_plan_domain` 后、`classify_entities` 前调用 `filter_legend_entities`
- [ ] `pipeline.py` 中 `scene_partition` 的调用从 `plan_domain.build_plan_domain` 移至聚合层，与 legend 结果统一处理
- [ ] `spatial_regions.json` schema 支持 per-region 的 `disposition` 枚举（`legend`, `derived_noise`, `technical_diagram`, `annotation_frame`, `empty`, `subject`）
- [ ] `--llm off` 且 `spatial_regions.json` 为空时：所有实体通过，探测器输出写入 evidence ledger 作为 `UNCERTAIN_REGION`
- [ ] `--llm observe/assist` 时：LLM 对未确认集群做语义判断，输出 disposition 提案
- [ ] 被标记的实体在 evidence ledger 中有 `disposition` 字段和 `source`（`scene_partition` 或 `legend_detector` 或 `spatial_regions`）
- [ ] legend diagnostics（`body_bbox`, `clusters`, `excluded_count`）写入 `run_manifest.json`
- [ ] Hutabohu 用现有 mapping_registry（无 spatial_regions.json）转换结果与基线一致

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| 图例可以用 body_bbox 自动过滤 | Contrarian: 算法能多准？Hutabohu 的 FDT STRUCTURE 有 25% 实体，不会被标记为集群 | body_bbox 只做空间裁剪提示，不做自动过滤。语义判断交给 LLM |
| legend_exclusions.json 就够了 | Ontologist: 六种区域类型需要语义标签 | 扩展为 `spatial_regions.json`，支持 disposition 枚举 |
| 两个探测器可以合并 | Simplifier: 各有长处 | 保持独立，聚合层统一输出 |
| LLM 只在 onboarding 时参与 | LLM 在转换运行时对未确认集群做语义判断 | 选择 Mode B：运行时推理，`--llm observe/assist` 控制行为 |
| `--llm off` 必须拒绝运行如果没有配置 | 空配置 = 全通过 + 诊断，不阻塞转换 | 方案 1：降级运行，宁可多不可丢 |

## Technical Context

### 代码库现状
- `libredwg.py:1416` — `_filter_legend_clusters()` 孤立定义，无调用者
- `legend_detector.py:222` — `detect_legend_clusters(features)` 纯函数，接收 `{centroid, text, layer, id}` dicts
- `scene_partition.py:90` — `detect_style_catalog_entities(entities: Iterable[SourceEntity])` 在 `plan_domain.py:505` 被调用
- `pipeline.py:1190-1206` — `build_plan_domain` 和 `classify_entities` 之间无空间过滤
- `SourceEntity` (model.py:205) — frozen dataclass，有 `entity_key`, `layer`, `centroid`, `text`
- `pipeline.py:1197-1205` — `evidence_entities` 构建点，适配器输出在此注入
- `ConversionRequest` 已有 `llm` 参数（off/observe/assist），`decision_pack` 字段已预留

### 四个项目的 Model 空间结构
- **Hutabohu**: 矩形主体框 + 右上角技术图 + 框外远处图例
- **Lamteh MAIN**: 横向排列 — 图例区 → 文字框 → 框1(主体+FDT注释噪声) → 框2-4 → 框5(住宅剥离子图)
- **Lamteh SF**: 类似 MAIN 但框2-4为空
- **Kletek**: 无图例区，FDT/FAT/BOITE 注释框在左上边界

### 关键技术决策
- 适配器纯函数：`frozenset[str]` 输出，不修改实体
- 配置位置：`baselines/<project>/config/spatial_regions.json`
- 首轮默认：标记 + 证据账本（LEGEND_CANDIDATE / UNCERTAIN_REGION）
- LLM 离线时：仅服从已确认配置，空配置 = 全通过

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| SpatialCluster | core domain | cluster_id, bbox, member_entity_keys, confidence, anchor_hits, detection_source | detected by Detector, resolved by SpatialRegion |
| Detector | supporting | source (scene_partition / legend_detector), detection_method, confidence | produces SpatialCluster |
| SpatialRegion | core domain | region_id, bbox or member_keys, disposition, confirmed_by, confirmed_at | defined in spatial_regions.json, matches SpatialCluster |
| Disposition | value object | value (subject / legend / derived_noise / technical_diagram / annotation_frame / empty / uncertain) | assigned to SpatialRegion, applied to entities in that region |
| LegendFlaggedEntity | supporting | entity_key, cluster_id, disposition, source | references SourceEntity, written to evidence ledger |
| SpatialRegionsConfig | configuration | project_id, schema_version, regions[], review_status | stored per-project in baselines/<project>/config/spatial_regions.json |
| LLMSupervisor | external system | mode (off/observe/assist), prompt_template, decision_log | reads SpatialCluster, proposes/writes SpatialRegion dispositions |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 4 (LegendCluster, ExclusionConfig, EvidenceLedger, SourceEntity) | 4 | - | - | N/A |
| 3 | 5 (adapter output contract added) | 1 | 0 | 4 | 80% |
| 5 | 6 (SemanticRegion + Disposition added, ExclusionConfig→SpatialRegionsConfig) | 1 | 1 | 4 | 83% |
| 7 | 7 (LLMSupervisor added as external system) | 1 | 0 | 6 | 86% |
| 8 | 7 (all entities stable) | 0 | 0 | 7 | 100% |

## Interview Transcript
<details>
<summary>Full Q&A (8 rounds + Round 0)</summary>

### Round 0 (Topology)
**Q:** 3个顶层组件的拓扑确认（适配器+管线接入、首轮运行策略、排除配置持久化）
**A:** 对，拓扑正确
**Ambiguity:** not scored

### Round 1
**Q:** 图例检测到的实体应该怎么处理？（仅标记 / 标记+账本 / 直接裁剪 / 等待配置）
**A:** 标记 + 证据账本记录：检测到的图例候选实体继续进入 classify_entities，在 evidence ledger 中有独立条目（disposition=legend），可在 QGIS 中可视化为单独图层
**Ambiguity:** 40.2%

### Round 2
**Q:** 排除配置应全局共享还是按项目独立？
**A:** 按项目独立，存到各自的 config/ 下
**Ambiguity:** 34.5%

### Round 3
**Q:** 适配器输出形式——独立 frozenset 让调用方注入，还是适配器自己写 evidence？
**A:** 返回独立的 legend_flagged_keys: frozenset[str] 让调用方决定如何注入
**Ambiguity:** 27.5%

### Round 4 (Contrarian)
**Q:** legend_exclusions.json 是否真的需要？提高 min_confidence 自动过滤不行吗？
**A:** 配置文件是人工对算法输出的确认凭证——黑箱需要白箱声明
**Ambiguity:** ~27%

### Round 5
**Q:** 六种空间区域类型和 disposition 枚举的方向？
**A:** 方向正确
**Ambiguity:** 26.9%

### Round 6 (Simplifier)
**Q:** scene_partition 和 legend_detector 应该合并还是独立？
**A:** 两条路独立，各有长处，最终聚合由配置要求决定，通过自然语义方式调教 LLM 监督者
**Ambiguity:** 23.4%

### Round 7
**Q:** LLM 监督者——离线配置生成（Mode A）还是运行时推理（Mode B）？
**A:** 模式 B — 运行时推理
**Ambiguity:** 19.4%

### Round 8 (Ontologist)
**Q:** `--llm off` 时空间过滤的降级策略？
**A:** 方案 1 — 仅应用已确认配置，空配置 = 全通过 + 诊断输出
**Ambiguity:** 17.3%

</details>
