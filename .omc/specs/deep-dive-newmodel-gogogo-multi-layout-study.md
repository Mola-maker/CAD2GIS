# Deep Interview Spec: 吸收 newmodel 精华 —— 标签绑定重铸 + 布局采掘 + 证据留痕 + 主体解耦

## Metadata
- Interview ID: dd-20260717-newmodel-study
- Rounds: 6 (+ Round 0 拓扑门)
- Final Ambiguity Score: 14%
- Type: brownfield
- Generated: 2026-07-17
- Threshold: 0.2 / Threshold Source: default
- Status: PASSED
- Trace Path: .omc/specs/deep-dive-trace-newmodel-gogogo-multi-layout-study.md
- 研学对象: /newmodel（gogogo 提交 4b4b8fc 提取物，50 文件）；改造对象: experiment/py_scripts 现行管线

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal | 0.90 | 0.35 | 0.32 |
| Constraints | 0.80 | 0.25 | 0.20 |
| Success Criteria | 0.84 | 0.25 | 0.21 |
| Context | 0.90 | 0.15 | 0.14 |
| **Total Clarity** | | | **0.86** |
| **Ambiguity** | | | **0.14** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| A 标签绑定重铸 | active | 家族门+匈牙利全局指派，真标签 CODE | Round 2 锁定 CODE 策略 |
| C 布局属性/证据采掘 | active | FDT_ID 打标（吸收 E1）+ TOPOLOGY 布局证据 | Round 1/4 演进后定型 |
| D 证据留痕体系 | active | 守恒记账+provenance+候选边留痕 | 门禁强度按假设：warning 级 + --strict-provenance 开关 |
| F 主体解耦 | active | Model 空间图例/非主体元素检测与排除 | Round 5 锁定：自动检测+隔离复核 |
| B 要素检出校准 | **active（Round 6 激活）** | B1 BOITE 多重表征融合+样式组；B2 CABLE 成链（TOPOLOGY 证据引导）；B3 FDT 域解耦（EMR 已裁决：有缆） | Round 6 用户逐项拍板；EMR 由领域证词裁决 |
| E 每布局独立导出 | **dissolved** | Round 1 定为独立导出 → Round 3 拆解三去向 → Round 4 CONTRARIAN 消除视口变换 | FDT 图纸→C 的 FDT_ID 打标；TOPOLOGY→C 证据；SPLICING/LEGEND→F 排除 |

## Goal
把 newmodel（cad2gis_v3）经实证有效的三项设计移植到我方 WSL/LibreDWG 管线，并新增主体解耦能力：(A) 用"文本家族正则门 + 匈牙利全局一对一指派"重铸标签绑定，修复真标签 0 命中，CODE 改为真标签优先/合成回退并新增 display_label；(C) 经 LibreDWG 布局通道采掘 FDT-01/02 布局属性，以"布局↔连通分量唯一最优匹配（非唯一即弃权）"给 Model 要素打 FDT_ID 标（QGIS 属性过滤实现分册呈现），TOPOLOGY 布局内容入证据表用于拓扑二次确认与标签补充；(D) 建立处置守恒记账 + field_provenance + 绑定候选边留痕的证据体系；(F) 参数化自动检测 Model 空间内图例/说明框类非主体元素簇，首期进 quarantine_review 供人工复核，确认后从交付排除；(B) 要素检出校准——BOITE 多重表征融合至 43 物理真值并以表征字段驱动 QGIS 分样式呈现（统一 FAT 图层组），CABLE 碎段按"先切碎、再依 TOPOLOGY 证据整合为逻辑段"成链，FDT-01/FDT-02/域间连接线（青色、MR.DMPH 系列标签标注的架空段）从单一图层解耦为按 FDT_ID 区分的域归属。

## Constraints
- 全程 WSL/LibreDWG：禁止引入 Windows/AutoCAD COM 依赖（newmodel 读取层不移植，仅移植算法与数据模型）
- 不从纸空间取几何（视口重复 Model，autocad_reader.py:983-990 已证）；纸空间只取属性/注记/证据
- 标签家族正则配置化（挂 schema_config），不硬编码 APD 单图模式；间隙聚类/围栏参数化，禁止魔法比例
- CODE 保持 evaluator 规则 4.x 兼容（非空+层内唯一）：真标签优先，无标签回退合成序号并以 provenance 标注 synthetic
- D 门禁首期 warning 级（provenance 缺失记违规不中止），`--strict-provenance` 升级 fail-closed（v3 式）
- F 首次运行不直接删除：LEGEND_CANDIDATE 入 quarantine_review，经确认清单在后续运行排除；SPLICING/LEGEND 布局内容永不入 QGIS 交付
- 匈牙利指派用纯 Python 实现（参考 semantics.py:22-74，零新增依赖）；容差沿用 15m（CRS 感知换算已就位）
- 不破坏既有产线：8 FC 层 schema、三态拓扑、CRS 参数化（默认 3857）不回退

## Non-Goals
- 视口逆变换（Round 4 CONTRARIAN 消除）；每布局独立 gpkg 导出（E 已溶解）
- newmodel 的 census 契约式 fail-closed 作为默认行为；布局匹配"分量数==布局数"硬断言（断连固化风险，topology.py:189 教训）
- 照搬 v2 桥接几何造假、v3 政策三连；**不采纳 newmodel 的"2 分量=2 服务域"交付形态**（EMR 段经用户领域证词裁决为有缆连接——青色矢量线+MR.DMPH 标签，我方 Model 整体连通是正确基线，必须保持）

## Acceptance Criteria
### A 标签绑定重铸
- [ ] schema_config 新增 label_families 配置（fat: `^DMPH-\d+\.\d+\.[A-Z]\d{2}$`→BOITE；pole: `^MR\.DMPH\.P\d+$`→PTECH；可扩展）
- [ ] converter 挂接逻辑（:648-671 贪心）替换为：家族门→候选边（≤15m，次优差≤阈值判 multiple_optima 弃权）→匈牙利全局一对一指派
- [ ] Hutabohu 实测：BOITE 真标签绑定 43/43；PTECH 真标签 118 + 合成回退 49（provenance=synthetic）；IMB 682 CODE=文字原文
- [ ] 新增 display_label 字段（真标签原文，无则空）；CODE 全层非空且唯一（evaluator 4.x 通过）
- [ ] 我方输出中匹配 DMPH-*/MR.DMPH.P* 的要素数从 0 → ≥161
### C 布局属性/证据采掘（含 FDT_ID 打标）
- [ ] LibreDWG 布局通道落地：枚举 8 布局（探针已证可行），经 \*Paper_Space 块读取布局实体与块 ATTRIB
- [ ] FDT-01/02 布局属性采掘（FDT 标识/FAT 序列），布局↔CABLE 连通分量唯一最优匹配，非唯一弃权留痕（移植 topology.py:140-201 语义，不移植分量数==布局数硬断言）
- [ ] Model 要素（CABLE/BOITE/PTECH/SITE）新增 FDT_ID 字段；QGIS 按 FDT_ID 过滤可获得 FDT-01/02 分册视图
- [ ] TOPOLOGY 布局内容写入 gpkg 证据表 topology_evidence（不入 8 FC 交付层），供拓扑二次确认与标签补充
### D 证据留痕体系
- [ ] conservation_ledger 表：全实体处置去向（mapped/legend/annotation/out_of_scope/…），SUM==实体总数校验
- [ ] annotation_assignment_candidates 表：每条候选边（text/target/distance/selected/status）
- [ ] field_provenance 表：非空业务字段的来源标注；warning 级门禁 + --strict-provenance 开关
- [ ] 现有 drop_accounting/quarantine_review 并入统一证据体系，不重复记账
### F 主体解耦
- [ ] 参数化检测器：X/Y 向间隙聚类 + 锚文本围栏（LEGEND/SYMBOL/DESIGN SUMMARY/SPLICING 等锚词配置化），无硬编码比例
- [ ] 检出的图例簇要素首次运行写 quarantine_review（reason=LEGEND_CANDIDATE，含簇范围与成员数），交付层暂不剔除
- [ ] 提供确认机制（config 排除清单），确认后运行时该簇要素 disposition=legend 且不入 8 FC 层
- [ ] Hutabohu 实测：Model 空间内图例样本簇（研学已证存在，newmodel 分出 10 个图例样本）被检出并入复核清单
### B 要素检出校准（Round 6 激活）
- [ ] B1 BOITE 融合：同一物理 FAT 的多重表征（FAT DWG 块 44 + napf1-f15 圆 43 + 文字杂项）按空间重合聚合 → BOITE ≈ 43 要素；新增 representation 字段记录源表征构成（block/circle/text 组合）
- [ ] B1 样式组：以 representation/TYPE 字段驱动 QGIS 分类样式，交付随附 QML 样式文件（参考 newmodel/experiment/runs/apd_architecture_v3/qgis/styles/ 的 BOITE.qml + style_manifest.json 形态），在 QGIS 中呈现为统一 FAT 图层组
- [ ] B2 CABLE 成链：保持现行"先切碎"提取，新增成链阶段——共享端点续接、遇节点（BOITE/SITE/PTECH）截断，并用 C 组件的 topology_evidence（TOPOLOGY 布局采掘）作为逻辑段划分的证据引导；Hutabohu 实测 990 碎段 → 与源图 6 条逻辑缆同数量级的逻辑段，LONGUEUR 按逻辑段重算
- [ ] B2 成链后拓扑复跑：三态吸附作用于逻辑段端点，5.4 引用完整性在逻辑段粒度评估，浮空端点数较 1452 显著下降
- [ ] B3 域解耦：FDT-01 域、FDT-02 域、域间连接段（青色 MR.DMPH 标注架空段）在输出中以 FDT_ID 区分（如 FDT-01/FDT-02/LINK），整网连通性保持不变（连通分量数不因解耦而增加）；颜色（青色）可作为连接段识别的辅助证据（LibreDWG 可读实体颜色）

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| newmodel 从多图纸取几何 | Lane 1 取证 | 几何全取 Model；布局是属性/证据源——C 组件按此设计 |
| 断连=两图纸配准错位 | Lane 3 取证 | 单一变换器；根因是衔接段无正版缆折线+政策拒绝推断；EMR 裁决探针存档（B defer） |
| 我方标签"部分丢失" | Lane 2 产物实测 | 实为真标签 0 命中+合成 CODE 假象；163 条真标签在 Model 空间（探针②），根因在挂接/分类阶段 |
| 多图纸利用需 Windows+AutoCAD | 探针①（dwgread） | LibreDWG 可枚举 8 布局、可读 ATTRIB(550)——WSL 侧可行 |
| "与主图共同呈现"需视口变换 | Round 4 CONTRARIAN | FDT_ID 打标+QGIS 过滤即达成，视口变换工程消除 |
| E=每布局独立导出 | Round 3 追问 | 拆解为三去向（打标/证据/排除），原导出物取消 |
| EMR 段性质（2 分量是否设计事实） | Round 6 用户领域证词 | **有缆**：FDT-01/02 间由青色矢量线连接、MR.DMPH 系列标签标注（沿杆架空段）；newmodel 2 分量交付被证伪；我方整体连通为正确基线，剩余工作是域解耦而非桥接 |

## Technical Context
- 我方管线（改造对象）：experiment/py_scripts/{converter.py, schema_config.py, evaluator.py, topology_builder.py}；缺陷位点 converter.py:194（15m 容差）、:648-671（贪心跨家族挂接）
- 移植源（只读参考）：newmodel/experiment/py_scripts/cad2gis_v3/semantics.py:22-154（匈牙利+家族门）、topology.py:140-201（布局匹配+弃权）、evidence.py:158-497（留痕三表+守恒）、autocad_reader.py:588-608（图例簇聚类，需去过拟合）；配置样例 newmodel/experiment/config/apd_mapping_registry.json
- 探针实证：dwgread 可见 8 LAYOUT（Model/FDT-ALL/FDT-01/FDT-02/FDT-01·02 TOPOLOGY/FDT LAYOUT/SPLICING FDT）；entmode 2:6942/1:161/0:3416（布局内容在 \*Paper_Space 块）；ATTRIB/ATTDEF 550 可读；DMPH-* 真标签 163 条在 Model 空间
- 真值基准（v3 交付实测）：FAT/BOITE=43、杆=167（118 有标签）、SITE=2、CABLE=6 整线 2 分量、IMB=682

## Trace Findings
- 最可能解释：newmodel 与我方读同一 DWG；其价值在布局角色化利用、全局指派标签绑定、证据留痕体系；其致命依赖（Windows+AutoCAD）与断连政策（2 分量钉进验收契约）不可照搬
- Lane 1 unknown（LibreDWG 能力）→ 探针①已解：可行
- Lane 2 unknown（0 命中根因）→ 探针②已解：标签在提取域内，根因在挂接/分类阶段（A 组件修复）
- Lane 3 unknown（EMR 敷缆事实）→ Round 6 用户领域证词裁决：有缆（青色矢量线+MR.DMPH 标签架空段），断连确为 newmodel 转换错误；探针方案（对照 v2 声明缆长）留作旁证可选
- 完整证据链：.omc/specs/deep-dive-trace-newmodel-gogogo-multi-layout-study.md

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| 标签家族 | core domain | 正则、目标FC | 门控候选边生成 |
| 匈牙利指派 | core algorithm | 候选边、cost=距离、unmatched_penalty | 产出 labels 关系与 display_label |
| CODE/display_label | core domain | 真标签/合成回退、provenance | evaluator 4.x 约束 |
| FDT_ID | core domain | 布局↔分量匹配产出 | 打在 CABLE/BOITE/PTECH/SITE 上 |
| 布局（8个） | supporting | 角色：model/plan/topology/legend | plan→打标；topology→证据；legend→排除 |
| topology_evidence 表 | deliverable | 布局内容证据 | 拓扑二次确认+标签补充 |
| conservation_ledger/field_provenance/candidates 表 | deliverable | 处置/来源/候选边 | D 组件三表 |
| LEGEND_CANDIDATE 簇 | supporting | 簇范围、成员、锚词 | 入 quarantine_review→确认后排除 |
| BOITE 表征融合体 | core domain (B1) | representation(block/circle/text)、43 真值 | 驱动 QGIS 分类样式（QML 样式组） |
| 逻辑缆段 | core domain (B2) | 成链后 LONGUEUR、ORIGINE/EXTREMITE | 由碎段续接+topology_evidence 引导 |
| 域间连接段 | core domain (B3) | 青色、MR.DMPH 标签、FDT_ID=LINK | 连接 FDT-01/FDT-02 域，保持整网连通 |
| quarantine_review | supporting (既有) | 追加 LEGEND_CANDIDATE 类型 | F 的复核载体 |
| LibreDWG 纸空间通道 | infrastructure | LAYOUT 枚举+\*Paper_Space 块+ATTRIB | C 的前置能力（已证） |
| 15m 容差（CRS 感知） | supporting (既有) | _meters_to_units | A 沿用 |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 8 | 8 | - | - | N/A |
| 2 | 9 | 1 | 0 | 8 | 89% |
| 3 | 11 | 3 | 0 | 8 | 73%（E 拆解引入新实体） |
| 4 | 11 | 0 | 1（E1→FDT_ID 打标） | 10 | 100% |
| 5 | 11 | 0 | 0 | 11 | 100%（连续两轮稳定，收敛） |

## Interview Transcript
<details>
<summary>Full Q&A (Round 0 + 5 rounds)</summary>

### Round 0（拓扑门）
**Q:** 4 候选组件哪些 active？ **A:** A/C/D + 用户补充 E"源dwg提取不同图纸"；B defer
### Round 1（E / Goal）
**Q:** E 的真实意图三选一？ **A:** 每布局独立导出 **Ambiguity:** 38%
（本轮前探针：①LibreDWG 8 布局+ATTRIB 可读；②163 条 DMPH 真标签在 Model 空间，0 命中根因锁定挂接阶段）
### Round 2（A / Criteria）
**Q:** CODE 策略？ **A:** 真标签优先+合成回退（+display_label） **Ambiguity:** 30%
### Round 3（E / Constraints）
**Q:** 布局导出物坐标处理？ **A:** （重定义 E）FDT-01/02/ALL 与主图共同呈现；TOPOLOGY 供拓扑确认+标签补充；SPLICING/LEGEND 不入 QGIS，且 Model 内同类元素需解耦（→F） **Ambiguity:** 28%
### Round 4（E1 / Constraints，CONTRARIAN）
**Q:** "共同呈现"是否真需视口变换？ **A:** FDT_ID 打标替代视口变换 **Ambiguity:** 21%
### Round 5（F / Criteria）
**Q:** 图例检测与处置方式？ **A:** 自动检测+隔离复核 **Ambiguity:** 16.5%
### Round 6（B / 用户主动澄清后激活）
**Q:**（用户询问匈牙利概念与 B 组件目的，解释后）B 三项如何处置？ **A:** ①BOITE 43 校准要做，且按源图元表征设多种样式、以统一 FAT 图层组呈现；②赞成成链，"先切碎再依 TOPOLOGY 整合为逻辑段"符合转化工程直觉；③EMR 段有缆（青色矢量线+MR.DMPH 系列标签），我方整体拓扑已正确，剩余问题是 FDT-01/FDT-02/连接线同图层未解耦 **Ambiguity:** 14% ✓
</details>
