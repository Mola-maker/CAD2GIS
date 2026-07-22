# Deep Interview Spec: BOITE 距离标签 + EXT.MR 颜色约束匹配

## Metadata
- Interview ID: dd-20260719-boite-ext-matching
- Rounds: 1 (+ Round 0)
- Final Ambiguity: 15.5%
- Type: brownfield / Generated: 2026-07-19
- Threshold: 0.2 / Source: default / Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal | 0.90 | 0.35 | 0.32 |
| Constraints | 0.82 | 0.25 | 0.21 |
| Success Criteria | 0.75 | 0.25 | 0.19 |
| Context | 0.92 | 0.15 | 0.14 |
| **Total** | | | **0.845** |
| **Ambiguity** | | | **0.155** |

## Topology
| Component | Status | Description |
|-----------|--------|-------------|
| B BOITE 距离标签+镂空样式 | active | BOITE 追加 span 距离值 display_label + QML 镂空红框 |
| M 颜色约束匹配 | active | pole_ext 25m+#FF0000 / pole 15m+非 #FF0000 分家族匈牙利 |

## Goal
(B) 将 span_annotations 的距离值（SPAN_M, 已格式化为 "49.1 m"）通过空间 JOIN 写入对应的 BOITE 要素 `distance_label` 字段，BOITE QML 样式改为镂空红色方形框（无填充，红色边框）+ 显示 CODE（DMPH 标签）与 distance_label 双行标注；(M) LABEL_FAMILIES 的 pole 和 pole_ext 家族各自运行独立匈牙利指派：pole_ext 容差提升至 25m 且仅匹配 color_rgb="#FF0000" 的 PTECH 要素；pole 保持 15m 容差且仅匹配 color_rgb!="#FF0000" 的 PTECH 要素——消灭 48 条 EXT.MR 标签丢失与 P069 跨色错配。

## Constraints
- M: 不破坏现有 fat 家族的匹配逻辑（43/43 保持）
- B: distance_label 数据源为 span_annotations（DIMENSION 定义的跨距线层）；无匹配 span 的 BOITE 留空不崩溃
- 两个 pole 家族各自独立运行匈牙利，节点池按 color_rgb 预过滤
- pole_ext 的 25m 容差仅用于此家族，不影响全局 --annotation-link-tol 默认值
- 不回归既有成果：真标签 43/118/682、CABLE 168、三轨样式、排除闭环

## Non-Goals
- 不调整 pole_ext 的模式正则（`^EXT\.MR\.\w+\.\w+\.\w+\.P\d+$` 已验证正确命中 49 条）
- 不做全局容差统一提升（其他家族保持 15m）
- 不处理 1 条 EXT.MR.XXX.XXX.PXXX 占位标签

## Acceptance Criteria
### B BOITE 距离标签
- [ ] 每个 BOITE 通过空间 JOIN（最近 span_annotations 线的 SPAN_M 值）写入 `distance_label` 字段
- [ ] BOITE.qml 改为镂空红框：`<symbol type="marker">` fill="#00000000"（或 transparent）outline="#FF0000"
- [ ] QML 标注双行：CODE（顶部）+ distance_label（底部，如 "49.1 m"）
- [ ] 距最近 span 超 100m 的 BOITE display_distance 置空或标 "—"（安全阀）
### M 颜色约束匹配
- [ ] LABEL_FAMILIES 新增 per-family 可选 `node_color_filter` 字段（如 `"#FF0000"` / `"!#FF0000"`）
- [ ] 匈牙利指派前按 color_rgb 预过滤 PTECH 节点池：pole_ext 仅匹配 #FF0000；pole 仅匹配非 #FF0000
- [ ] pole_ext 家族 min_distance 放宽至 25m（配置项，不硬编码）
- [ ] 端到端后：PTECH #FF0000 label_provenance=annotation-assigned ≥49（EXT.MR 全部命中）；PTECH #00FFFF 标签全 71 条（3 条 MR.DMPH 回收）；无跨色错配（pole_ext assigned 全部落在 #FF0000 节点）
- [ ] annotation_assignment_candidates 表 pole_ext 家族 49 条 selected（不再只有 1 条）

## Assumptions Exposed
| Assumption | Resolution |
|------------|------------|
| EXT.MR 该映射到 CABLE | 用户澄清：EXT.MR = PTECH 杆标签（与 MR.DMPH 平级），正确映射 |
| 15m 容差足够 | 探针实证：EXT.MR 标签系统性偏移 21.6m（median），需 25m |
| BOITE 数字标签缺失 | 探针实证：数字 = `►16`/`►72`/`►48` 距离值，来自 span_annotations |

## Technical Context
- 断点位：LABEL_FAMILIES → candidate-filter distance ≤ 15m → 48/49 EXT.MR 排除
- 距离分布：MR.DMPH median 12.9m（100% ≤15m），EXT.MR median 21.6m（2% ≤15m）
- M 改点位：converter.py Hungarian 指派入池前按 color_rgb 过滤；LABEL_FAMILIES 每 family 的 tolerance 可从 label_families 配置读取
- B 改点位：converter.py main() 中写 BOITE 层后做 span_annotations→BOITE 空间 JOIN；QML 修改 style_builder.py BOITE 生成段
