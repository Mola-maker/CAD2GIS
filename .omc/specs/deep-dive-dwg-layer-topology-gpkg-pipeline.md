# Deep Interview Spec: CAD2GIS 管线重铸 —— 召回修复 + 分级拓扑 + CRS 参数化 + 验证回路提示词

## Metadata
- Interview ID: dd-20260716-dwg-gpkg
- Rounds: 5 (+ Round 0 拓扑门)
- Final Ambiguity Score: 18%
- Type: brownfield
- Generated: 2026-07-16
- Threshold: 0.2
- Threshold Source: default
- Initial Context Summarized: yes（trace 报告注入，见 Trace Findings）
- Status: PASSED
- Trace Path: .omc/specs/deep-dive-trace-dwg-layer-topology-gpkg-pipeline.md

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.88 | 0.35 | 0.31 |
| Constraint Clarity | 0.78 | 0.25 | 0.20 |
| Success Criteria | 0.75 | 0.25 | 0.19 |
| Context Clarity | 0.85 | 0.15 | 0.13 |
| **Total Clarity** | | | **0.82** |
| **Ambiguity** | | | **0.18** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| A recall-audit-classification | active | 召回盘点与分类覆盖修复 | Round 1 盘点 + Round 5 映射表全盘采纳 |
| B strict-topology | active | 分级严格拓扑（吸附+隔离清单） | Round 4 验收基准锁定 |
| C georef-output-spec | active | 配准与输出规范（CRS 参数化） | Round 2 锁定参数化；DWG 原生 3857 米制已实锤 |
| D verification-loop-prompts | active | 基于 evaluation_standards 的英文验证回路提示词 | Round 3 锁定为新建独立文档 |

## Goal
将 experiment 管线从"线性转换器"升级为达标 FTTH 交付管线：(A) 按已确认的 CAD图层→FC 映射表修复分类召回（杆→PTECH、FDT→SITE、FAT→BOITE、Home Number→IMB、FAT AREA→ZPM、SPAN CABLE 尺寸标注→跨距属性）；(B) 实现分级拓扑（容差内吸附重合+回填 ORIGINE/EXTREMITE，超容差仅赋属性并进 QUARANTINE 复核清单）；(C) CRS 全链参数化（DWG 原生 EPSG:3857 米制 → 可配置目标 CRS），transform_record 如实记录；(D) 在 /experiment/guide 新建独立英文提示词文档，把 VERIFICATION_RULE.csv 总纲 + 7 个图层字段 CSV 重铸为验证回路 Agent 提示词。

## Constraints
- CRS 不写死：converter 目标 CRS、拓扑容差单位、验证规则 CRS 检查均参数化；验证遵循 VERIFICATION_RULE 规则 2 的"工程-图层一致性"原则
- 拓扑两级容差（吸附容差 / 隔离阈值）均为参数，默认值在实现时以米制标定后换算
- D 组件不修改现有 GeoFormer_FiberHome_Hutabohu_AgentPrompts.md、不修改任何代码；产物为 /experiment/guide 下新建英文 .md
- INSERT 分类必须按图层名路由（本图纸块名全部为匿名块 *U11–*U17，块名正则不可用）
- 目标数据集：APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg（原生 EPSG:3857 米制，dwgread 实测 X≈13.68M Y≈69K）
- 印尼边界过滤（converter.py:833-834）改为告警+计数，不静默丢弃（对齐 guide:304-306 "warning, NOT halt"）

## Non-Goals
- 不改动 demo/ 旧变体（Regime A/B、32648→3857 链条为历史方案，与本数据集无关）
- 不实现 guide Agent 7 瓦片合并、Kvisimine 四叉树分片
- 不做与参考真值的几何精度比对（evaluator 保持合规验证定位）
- D 不要求实现 PASS/FAIL/QUARANTINE→converter 的自动回灌闭环（提示词文档可描述，实现另立任务）
- 源图纸确无的要素不伪造（ZNRO 无 OLT 范围 → 空层+QUARANTINE 报告，不合成占位面）

## Acceptance Criteria
### A 召回修复
- [ ] 按映射表：NEW POLE 7-*/EXISTING POLE/POLE * 图层的 216 个匿名块 → PTECH 非空
- [ ] FDT STRUCTURE 系列碎片聚合为单点 → SITE (TYPE=PM) 非空，BOITE 中不再有 2268 个 FDT 碎片
- [ ] FAT 系列 + napf1-f15 → BOITE (TYPE=PBO)；Home Number 682 条文字 → IMB
- [ ] FAT AREA/BOUNDARY FAT/HP COVER → ZPM 面；ZNRO 空层进 QUARANTINE 报告
- [ ] SPAN CABLE 的 170 个 DIMENSION 测量值提取为跨距属性（新增 DIMENSION 实体提取）
- [ ] misc 占比从 48.3% 显著下降，且每个丢弃口（misc/块跳过/边界过滤/图框过滤）输出逐图层计数
### B 分级拓扑
- [ ] 容差内：缆端点吸附至节点，几何重合且 ORIGINE/EXTREMITE 回填对应节点 CODE
- [ ] 超容差：仅赋 ORIGINE/EXTREMITE（最近节点），要素进 QUARANTINE 复核清单
- [ ] VERIFICATION_RULE 5.4 双向孤立性检查在非空属性下通过（消除 evaluator 空洞通过）
- [ ] 输出 FLOATING_CABLE/ISOLATED_NODE 统计（guide Agent 4 定义）
### C 配准与输出
- [ ] `--source-crs`/目标 CRS 参数化端到端生效；对 Hutabohu 以 EPSG:3857 输入验证
- [ ] transform_record 表如实记录实际变换链（消除恒写 identity 的失真，converter.py:1169）
- [ ] 印尼边界/图框过滤改为告警+计数
### D 验证提示词文档
- [ ] /experiment/guide 下新建英文 .md，覆盖 VERIFICATION_RULE.csv 全部 7 个规则组（文件完整性/CRS 一致性/空层/字段+CODE 唯一/5.x 孤立性/6.x 几何/容量端口）
- [ ] 7 个图层 CSV（BOITE/CABLE/INFRASTRUCTURE/PTECH/SITE/ZNRO/ZPM）+ VERIFICATION_RULE 内嵌 IMB 字段表全部转写为英文字段校验提示词（含 10 字符截断名对照）
- [ ] CRS 检查表述为"工程-图层一致 + 可配置目标 CRS"，不硬编码 4326
- [ ] 不改动现有 AgentPrompts.md 与任何代码

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| 管线已有聚类/严格拓扑（用户原问预设） | trace 三分道取证 | 未实现；guide Agent 4 为纸面规范，B 组件补齐 |
| DWG 原生 WGS84 度制（Lane 3 初判） | resolution.txt 比对 + dwgread 实测 | 实为 EPSG:3857 米制；产出 gpkg 必经 --source-crs 3857 运行 |
| PTECH/SITE 空层可能是源缺失 | Round 1 盘点 | 分类未命中（杆类图层大量存在）；SITE 可由 FDT 映射；ZNRO 真缺失 |
| 块名正则可分类 INSERT | 块名直方图 | 全部为匿名块 *U##，必须按图层名路由 |
| "严格拓扑"=几何吸附 | Round 4 Contrarian | 分级：吸附+属性+QUARANTINE 三态 |
| 交付 CRS 应为 4326 或 3857 之一 | Round 2 三处矛盾证据 | 参数化，验证只查一致性 |

## Technical Context
- 管线现状（trace 实证）：LibreDWG 直读（ctypes+SWIG 双通道，converter.py:53-77, 609-659）→ 两级分类 `_assign_fc`（:493-508；Tier-1 图层名正则 schema_config.py:1872-1930）→ 三重空间过滤 → OGR 直写 8 层 GPKG + 3 元数据表（:992-1211）→ evaluator.py 7 规则组事后验证（:1209-1254）
- 已知缺陷清单：标注挂接判重死代码+质心失配（:568-596）；R11 类型码冲突产生 SEQEND_r11 伪要素；evaluator 5.4/6.6 空属性空洞通过（evaluator.py:936-937）；`_haversine_distance` 实为欧氏度距（:647-649）；transform_record 恒写 identity（:1169）
- 源图纸盘点（resolution.txt，2114 实体）：文字 1364/图块 222/圆 195/尺寸标注 170/圆弧 131/填充 23/多段线 10；图层直方图见 trace 报告
- 评估标准：experiment/evaluation_standards/{VERIFICATION_RULE.csv + 7 图层 CSV}，中文，含 10 字符截断字段名对照
- 遗留可参考实现：plugincad2gis/src/cad2gis/network.py（build_network snap_tol、_cluster_points）与 topology.py（被 gitignore，未参与 experiment 管线）

## Trace Findings
- 最可能解释：experiment 管线为线性"读取→分类→过滤→写出"转换器；聚类/严格拓扑是 guide Agent 4 规范，未实现；地理配准对本数据集经 --source-crs 3857 重投影（原判"恒等"被 dwgread 实测修正）
- Lane 1 unknown（misc 48.3% 归因）→ 已解：正则覆盖缺口 + 匿名块丢弃 + 文字主体；映射表修复
- Lane 2 unknown（CABLE_ALL Topology 产物）→ 部分解：仓库内无产物，实测 100% FLOATING_CABLE；B 组件以分级拓扑取代猜测
- Lane 3 unknown（四空层归因）→ 已解：PTECH/SITE=分类未命中，ZPM=可由 FAT AREA 映射，ZNRO=源真缺失
- 完整证据链：.omc/specs/deep-dive-trace-dwg-layer-topology-gpkg-pipeline.md

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| SITE | core domain (点FC) | CODE, TYPE=PM, REF_NRO, X, Y | FDT 图层映射而来；ZPM.CODE=SITE.CODE (5.1) |
| BOITE | core domain (点FC) | CODE, TYPE=PBO, REF_PM, CAPACITE | FAT 系列映射；REF_PM→SITE.CODE (5.2) |
| PTECH | core domain (点FC) | CODE, TYPE, NATURE, X, Y | 杆类图层匿名块映射 |
| IMB | core domain (点FC) | CODE, RACCORDEMENT, X, Y | Home Number 文字映射 |
| CABLE | core domain (线FC) | CODE, ORIGINE, EXTREMITE, LONGUEUR, 跨距 | ORIGINE/EXTREMITE→BOITE/SITE.CODE (5.4) |
| INFRASTRUCTURE | core domain (线FC) | CODE, ORIGINE, EXTREMITE | 管道线 |
| ZNRO/ZPM | core domain (面FC) | CODE, NB_PRISES | ZPM←FAT AREA；ZNRO 空层+报告 |
| CRS参数 | supporting | source-crs, target-crs | 全链贯穿 converter/topology/验证 |
| QUARANTINE清单 | supporting | 要素ID, 原因, 距离 | 超容差缆段+空层报告的载体 |
| 验证提示词文档 | deliverable | 7规则组×8图层英文提示词 | 源于 VERIFICATION_RULE.csv+7 CSV |
| LAYER_PATTERN_MAP | supporting (代码) | (正则, FC, 几何型) | A 组件的主要修改点 |
| converter/evaluator | external system (代码) | — | 承载 A/B/C 修改；D 只读参照 |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 9 | 9 | - | - | N/A |
| 2 | 10 | 1 (CRS参数) | 0 | 9 | 90% |
| 3 | 11 | 1 (提示词文档) | 0 | 10 | 91% |
| 4 | 12 | 1 (QUARANTINE清单) | 0 | 11 | 92% |
| 5 | 14 | 2 (映射表细分实体) | 0 | 12 | 100%（核心域连续两轮稳定） |

## Interview Transcript
<details>
<summary>Full Q&A (Round 0 + 5 rounds)</summary>

### Round 0（拓扑门）
**Q:** 4 个候选顶层组件哪些 active？
**A:** A/B/C 全选 + D 具体化为"基于 evaluation_standards 七图层字段标准 + VERIFICATION_RULE.csv 总纲，在 /guide 编写重铸验证回路的英文提示词"

### Round 1（A / Context）
**Q:** 图纸实际画了哪些设施？PTECH/SITE/ZNRO/ZPM 存在吗？
**A:** 指向 experiment/guide/APD...resolution.txt 比对 → 盘点实证：杆类大量存在（分类未命中）、块全匿名、Home Number 682 文字、无 OLT/SRO 图层；另实锤 DWG 原生 3857 米制
**Ambiguity:** 53%

### Round 2（C / Constraints）
**Q:** 交付 GPKG 以哪个 CRS 为准（三处矛盾证据）？
**A:** 参数化，不写死
**Ambiguity:** 45%

### Round 3（D / Goal）
**Q:** 重铸验证回路提示词的交付形态？
**A:** 新建独立英文提示词文档（不动 AgentPrompts.md、不动代码）
**Ambiguity:** 37%

### Round 4（B / Criteria，CONTRARIAN）
**Q:** "严格拓扑"若不等于几何吸附？绘图偏差如何处理？
**A:** 分级：容差内吸附+超容差隔离清单
**Ambiguity:** 28%

### Round 5（A / Goal+Criteria）
**Q:** 确认 CAD图层→FC 映射表（含 FDT→SITE、ZNRO 空层处置）
**A:** 映射表总体没有问题，采纳
**Ambiguity:** 18% ✓

</details>
