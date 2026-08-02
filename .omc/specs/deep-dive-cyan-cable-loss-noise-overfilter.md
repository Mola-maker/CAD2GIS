# Deep Dive Spec: 四项目线缆丢失 / 坐标锚定 / 噪声去噪修复

## Metadata
- Interview ID: dd-20260731-cyan-cable-noise-overfilter
- Rounds: 9 (+ Round 0 topology)
- Final Ambiguity Score: 18.8%
- Type: brownfield
- Generated: 2026-08-01
- Threshold: 0.2
- Status: PASSED

## Trace Findings
1. **线缆非全丢**：实际 2+1 条丢失——hutabohu 2 条 GRT feeder 线（layer 不在 route regex）+ lamteh_sf 1 条 FO 24 CORE LINE C（1290.5m，LLM assist 误判 legend 排除，**用户已确认是真实缆线**）
2. **(0,0) 坐标根因**：`assess_coordinate_domain` 对 EPSG:3857（全局 CRS）任何坐标通过；`validate_project` 的 magnitude 检查（max_abs<100k→LOCAL）是死代码——convert 管线直接调 assess 绕过
3. **SF 过去噪 = 评估误判**：CABLE 15/PTECH 7 成功交付；30 条 unmatched 中 DESIGN SUMMARY 19（示意图线，handle 连续、长度一致 255.8m）+ TITLE BLOCK 10 + DROP DUCT 1；示意图线正确 abstain 但 legend_flag=''（从未被检测器标记）
4. **BOITE 误分类**：`insert_layer_families` fallthrough（semantics.py:440-448）把匿名块 *U 系列/Title Block 在 FAT/FAT CODE/CLOSURE 层归为 BOITE
5. **图例交错**：centroid gap 聚类无法分离与主体空间交错的图例（Hutabohu 右上角、lamteh_main Y 轴重叠）

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.85 | 0.35 | 0.298 |
| Constraint Clarity | 0.80 | 0.25 | 0.200 |
| Success Criteria | 0.75 | 0.25 | 0.188 |
| Context Clarity | 0.85 | 0.15 | 0.128 |
| **Ambiguity** | | | **18.8%** |

## Goal
修复四项目（Hutabohu/Lamteh MAIN/Lamteh SF/Kletek）auto-convert 产出的三个问题：1) 线缆丢失（GRT regex 缺口 + LLM 去噪误杀真缆线）；2) 三项目 OSM (0,0) 坐标（OSM 地名自动锚定 + 人工 GCP 精配路径）；3) 噪声去除不彻底/过分（route 实体豁免 + 边界带探测器 + LLM 层语义判定）。

## Constraints
- **route 实体豁免（方案 A）**：`spatial_filter.py` auto-exclude 阶段，通过 `positive_route_layer_regex` 匹配的实体永不排除——LLM 只能排除非 route 实体
- **注释框去噪（方案 C）**：不改 INSERT 分类（semantics.py:443 保持），注释框在去噪层识别排除
- **边界带探测器**：新增第三探测器——body_bbox 确定后定义"边界带"（距边界 1-3% 跨度宽环带），带内实体以 TEXT/LEADER/框线为主 → annotation_frame 候选；图例位置特征=大框角落（两边界交点带）或独立并列位置（主体外围）
- **LLM 层语义判定（方案 B 为主）**：把每层层名+实体统计喂 LLM，判定"该层是主体层还是非主体层"（DESIGN SUMMARY→非主体），一次性调用写入 spatial_regions.json 缓存
- **OSM 地名自动锚定**：文件名提取地名 → Nominatim 免费 API 查询真实坐标 → **bbox 中心对齐**（本地 bbox 中心 → OSM 地名 bbox 中心）→ 平移量写入项目 source_profile.json 配置；粗锚定先行 + 可选人工 GCP 精配（粗锚后 GCP 距离更近）
- **regex 检查链（方案 C）**：auto-convert 后自动检查 `unmatched_route_layer` 覆盖记录，存在疑似线缆层（层名含 CABLE/FEEDER/FO/GRT）→ 自动扩写 positive_route_layer_regex 并重跑 admission
- **SF 示意图线正确 abstain**：DESIGN SUMMARY/TITLE BLOCK 层不扩写进 route regex——它们不是缆线
- 不改变已验证的语义分类逻辑（除 route 豁免外）

## Non-Goals
- 不恢复 positive_route_color_aci（ACI=4 实体是 SLING WIRE/FDT STRUCTURE 非光纤）
- 不改 INSERT→BOITE 分类 fallthrough（走去噪层识别）
- 不把 DESIGN SUMMARY 线加入 route regex（正确 abstain）
- 人工 GCP 精配保留为可选路径（用户操作），OSM 锚定是自动先行

## Acceptance Criteria
- [ ] `spatial_filter.py`：auto-exclude 前过滤 route 匹配实体（豁免集），SF 1290.5m FO 线不再被排除
- [ ] 新探测器 `annotation_frame_detector`（或并入 spatial_filter）：边界带内 TEXT/LEADER/框线实体 → `annotation_frame_candidates: frozenset[str]` 加入聚合层
- [ ] LLM supervisor 输入增加层语义判定：每层层名+实体统计 → 非主体层标记（DESIGN SUMMARY/TITLE BLOCK 等），缓存到 spatial_regions.json
- [ ] hutabohu GRT feeder 层（GRT.100.0X01 MAINFEEDER/SUBFEEDER CABLE）加入 route regex → CABLE 7→9
- [ ] OSM 锚定：文件名→地名→Nominatim 查询→bbox 中心平移量写入 source_profile.json crs 段（`osm_anchor` 策略）
- [ ] convert 遇本地坐标（magnitude<100k）时：若配置有 osm_anchor → 应用平移继续产出；否则标记 `LOCAL_OR_MISREGISTERED` 提示需锚定/GCP
- [ ] 人工匹配启动链可用：`cad2gis review <run_dir>` → web 选 GCP → 导出 → convert --gcp-profile
- [ ] regex 检查链：unmatched_route_layer 中出现疑似线缆层 → 自动扩写 regex + 重跑 admission
- [ ] 重新生成四项目后：CABLE hutabohu=9, lamteh_main=16, lamteh_sf=16, kletek=7；SF 图例线被标记；lamteh_main/kletek 注释框不再进 BOITE

## Technical Context
- `spatial_filter.py:120-135`：auto-exclude 逻辑（需加 route 豁免）
- `semantics.py:429-474`：route 分类（route_pattern 匹配）
- `semantics.py:440-448`：INSERT→block_families→insert_layer_families fallthrough
- `coordinate_domain.py:56-75`：assess_coordinate_domain（EPSG:3857 恒过）
- `project_profile.py:822-841`：validate_project magnitude 启发式（死代码，需移入 assess 或 convert 路径）
- `georef.py:74-168` DirectTransformer / `georef.py:259-374` DeliveryTransformer
- `review_server.py:1282` review 入口 / `:1078` GCP 导出 API
- SF unmatched: DESIGN SUMMARY 19（示意图）+ TITLE BLOCK 10 + DROP DUCT 1

## Ontology
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| RouteExemptEntity | core | entity_key, layer, route_regex_match | protected from auto-exclude |
| AnnotationFrameCandidate | core | entity_key, boundary_band, entity_type | produced by boundary detector |
| LayerSemanticVerdict | core | layer, verdict(subject/non_subject), confidence | cached in spatial_regions.json |
| OsmAnchor | core | place_name, bbox_center_delta, source_crs | stored in source_profile crs |
| GcpProfile | supporting | cad_points, target_points, accuracy | used by DeliveryTransformer |

## Interview Transcript
<details>
<summary>Full Q&A (9 rounds)</summary>

### R1: SF 被排除 1290.5m 线是真缆线吗？→ 是（用户确认）→ 去噪误杀坐实
### R2: 修复机制？→ 方案 A：route 实体豁免
### R3: 注释框→BOITE 修复？→ 方案 C：去噪层识别，不改分类
### R4: 边界带探测器值得做吗？→ 值得（注释框沿主体矩形边界规律分布）
### R5: (0,0) 修复？→ OSM 地名自动锚定（文件名→Nominatim→重心参考→继续 convert）
### R6: 锚定细节？→ bbox 中心对齐 + 写入项目配置 + 粗锚先行可选 GCP + 免费 API
### R7: regex 缺口修复？→ 方案 C：检查链自动扩写 regex
### R8: SF 30 条 unmatched 分布？→ DESIGN SUMMARY 19(示意图线)+TITLE BLOCK 10+DROP DUCT 1
### R9: 图例未标记修复？→ 方案 B 为主（LLM 层语义判定）+ C 兜底（边界带探测器）；图例位置=大框角落或独立并列位置

</details>
