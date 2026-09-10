# CAD2GIS 0.6.0 实图验收复核与下一步

复核日期：2026-09-09。证据来自 `/tmp/cad2gis-verification-final` 的实际 DWG 作业与 run manifest；五份最终独立审计使用 `/tmp/cad2gis-final-audits/{id}/report.json`，Lamteh SF 使用 `/tmp/cad2gis-verification-lamteh-sf-final/lamteh_sf` 的 run/audit。机器摘要为交付证据包中的 `verification/acceptance-review.json`，记录各文件的 SHA-256。六份最终审计共用 verifier SHA-256 `ab3bb04115c31f20e77ff6c3f8040af8642a521d7e847e078663aa9b925bacc0`。本文只读审查，不新增审批，不修改原 DWG、baseline、profile、registry、review 记录或生产门禁。

**六份 DWG 已完成转换并执行最终独立审计，六份数值回环通过；run 均为 `CONDITIONAL`，没有一份建立独立 GCP 绝对精度验收。另外三份因历史 baseline 不兼容保持 `BLOCKED`。** 六份共有 106 条几何变更需工程复核，另有 7 个无直接完整源几何的派生区域。不能把转换完成、审计执行或测试数量写成工程验收通过。Tinggede 首次审计失败后已重跑成功；Lamteh SF 首次运行崩溃后已顺序重跑成功，原失败事实保留在下文和机器摘要中。

尤其要复核两类真实修改：Lamteh 的 `ZNRO / 18837E` 执行 `repair_boundary_polygon` 后，源 Polygon 与交付 Polygon 的最大差异为 **97.705 m**；Taipa 的 `BOITE / 4D210` 执行 `collocate_with_support`，位置相对源点变化 **12.184 m**。前者是区域边界比较值，不是设备移位或地面定位误差。两者都有来源记录，仍缺少工程意图确认。

## 逐图状态

| 图纸 | 交付要素 | run 状态 | 作业 / 独立审计 | 未解决记录 | reader 不支持 | semantic abstained | OSM 状态 |
|---|---:|---|---|---:|---:|---:|---|
| Lamteh main | 1,000 | CONDITIONAL | CONVERTED / EXECUTED | 800 | 665 | 2,626 | abstained |
| Kletek | 263 | CONDITIONAL | CONVERTED / EXECUTED | 57 | 47 | 468 | matched，相对匹配 |
| Taipa | 673 | CONDITIONAL | CONVERTED / EXECUTED | 145 | 49 | 604 | matched，相对匹配 |
| Tinggede | 515 | CONDITIONAL | 首次审计 FAILED；独立重跑 EXECUTED | 83 | 49 | 178 | matched，相对匹配 |
| Darat Sekip SF | 28 | CONDITIONAL | CONVERTED / EXECUTED | 52 | 228 | 1,516 | unavailable |
| Manado–Tomohon uplink | — | 未生成 | BLOCKED at validate_project | — | — | — | 未执行转换 |
| Semarang SF | — | 未生成 | BLOCKED at validate_project | — | — | — | 未执行转换 |
| Tinggar | — | 未生成 | BLOCKED at validate_project | — | — | — | 未执行转换 |
| Lamteh SF | 56 | CONDITIONAL | 顺序重跑 CONVERTED / EXECUTED | 2 | 517 | 428 | unavailable |

未解决记录、reader 不支持、semantic abstained 的对象和作用范围不同，不能相加成“丢失实体数”。六个 run 的 terminal accounting 均为 `errored=0`；semantic coverage 均为 `WATCH`。`pipeline._derive_conversion_status()` 会把未解决、不支持、semantic abstention，以及缺少独立坐标验收保留为条件状态。

Tinggede 的 source export、project validation、conversion 与 artifact integrity 均已通过；失败发生在后续独立审计，不是转换未产出。初次审计数值回环为：source 最大误差 `1.9508683631480923e-5 m`、target grid 最大误差 `1.8664709821322345e-5 m`，超过独立审计的 `1e-5 m` 阈值。修复审计逆解后，在相同 run、相同阈值下分别降为 `9.927793919652025e-9 m` 和 `8.614733815193176e-9 m`，已通过。原 worker report 仍记录首次失败，最终结果来自单独重跑的审计；没有覆盖旧记录或修改交付几何。

Lamteh SF 首次启用 trace、corpus jobs=2 的运行以 `-11 / SIGSEGV` 结束；随后关闭 trace、顺序运行成功，耗时约 547 秒，独立 source roundtrip 为 `2.958571586542623e-9 m`。运行方式同时改变，不能据此断言由 trace 或并发引起，崩溃根因仍未确定。

三份 `BLOCKED` 的原因是历史 profile/registry inventory binding 失效。旧 reader 曾把名为 APD/FDT_2 的 paper space 纳入 model census；当前读器默认将它们保留为证据并排除出转换域。实体保留和计数对应已核清，布局的工程用途仍需明确。[基线兼容性报告](UPGRADE_BASELINE_COMPATIBILITY.md) 与 [迁移提案](upgrade-baseline-migration-proposals.json) 记录 model-only 和显式 `plan_layouts` 两种未接受方案。本轮没有回写历史审批。

## 已成立的检查与不能推出的结论

| 证据域 | 本次成立的事实 | 不能据此推出 |
|---|---|---|
| Source 保全 | 六份作业的 source conservation、原文件哈希与产物完整性通过；六份最终审计的 immutable/source artifacts 均未被审计改变。 | 所有 source graphic 都是交付业务资产，或语义覆盖已经完整。 |
| Source geometry / topology policy | 六份 manifest 对源事实保全、禁止虚构拓扑连接等政策检查为 PASS；synthetic route vertices、support optical promotions、crossing connections 均为 0。 | 交付几何逐点完全等于源图，或设备层面的工程连通性已经验收。 |
| Segment delivery | 六份 schema、单位、索引、几何长度闭合与总长度闭合检查为 PASS。 | 每段都拥有实测 SPAN，或长度差异满足某个未声明的工程容差。 |
| CRS 与数值 | 六份生产转换 roundtrip 均在其门禁内，coordinate domain 均 plausibly inside declared CRS；六份最终独立审计数值回环通过。 | 数据在地面的位置正确，或 EPSG/PROJ 声明的 operation accuracy 是本项目实测误差。 |
| OSM | Kletek/Taipa/Tinggede 获得相对道路匹配；Lamteh main 因证据不足 abstain；Darat/Lamteh SF 未获得道路数据。 | OSM 道路与工程设施的对应关系必然正确，或取得独立厘米级定位证明。 |

所有已生成 run 都记录 `coordinate_accuracy.passed=null`、`absolute_accuracy_verified=false`、`calibration_status=not_provided`。Lamteh 最佳候选支持 route fraction 约 0.660，未达到 0.75 门槛，保留 DWG GEODATA nominal 结果；Darat 的 `osm_roads_unavailable` 表示数据不可用，不表示已证明道路不匹配。PROJ 的候选 operation selection 与 accuracy 元数据描述转换本身，不能替代 surveyed checkpoints；GCP 拟合残差也需要与独立检查点分开报告。[PROJ operation computation](https://proj.org/en/stable/operations/operations_computation.html)、[QGIS Georeferencer](https://docs.qgis.org/latest/en/docs/user_manual/managing_data_source/georeferencer.html)

## 量测和拓扑诊断

| 图纸 | 有 SPAN 的段 | 无 SPAN 的段 | 最大 SPAN 对比差异 m | source route native length 最大变化 m | route group / segment graph components |
|---|---:|---:|---:|---:|---|
| Lamteh main | 209 | 28 | 1.367535 | 0.049552 | 40 / 21 |
| Kletek | 0 | 33 | 无量测比较证据；字段为 0 | 约 2.84e-14 | 6 / 4 |
| Taipa | 118 | 0 | 0.498348 | 0.607343 | 12 / 7 |
| Tinggede | 51 | 14 | 0.510280 | 0.655769 | 15 / 13 |
| Darat Sekip SF | 12 | 0 | 0.476039 | 0 | 1 / 1 |
| Lamteh SF | 25 | 0 | 0.481881 | 约 2.27e-13 | 1 / 1 |

`src/cad2gis/cad2gis_v3/pipeline.py` 的 validation summary 将 `measurements.passed` 写为 True，同时给出上述诊断量。此字段没有实施工程误差阈值。另有 `segment_delivery` 的 schema/closure 检查和 `project_contract.segments` 的声明门禁；本次六份 profile 的 `expectations.segment_gates` 和 `source_geometry_gates` 为空。实际阈值应由已审查的项目要求确定，不能为了消除差异临时生成阈值。

此限制没有被认定为新的生产算法缺陷：`src/cad2gis/verify/matrix.py` 已将已知 unmeasured segments 降为 length `WATCH`；检索到的界面实现未直接宣称这些量测误差达标。报告应继续区分“诊断完成、闭合成立”和“量测覆盖/容差验收”。Kletek 的零差异尤其不能写为完美量测。

route group 和 coordinate vertex graph 使用不同定义，component 数量不同是现有 `mismatch` 诊断，不等于强行丢失或增加连接。最终独立审计中，Kletek/Darat/Lamteh SF 的 coordinate edge multiset 相同；Lamteh main/Taipa/Tinggede 不同，但 source/delivery 的节点数、边数、component 数及度数分布相同。这个检查使用 native units 下 `1e-6` 量化坐标且不插入 crossing nodes，只验证其定义下的图，不能代替设备关联复核。

## 几何变更和来源覆盖仍需逐项处理

| 图纸 | geometry review required | 最大 source-relative 差异 m | 无直接完整源几何的派生对象 | 重点检查 |
|---|---:|---:|---:|---|
| Lamteh main | 41 | 97.705069 | 0 | ZNRO 边界修复；BOITE 最大移位另为 12.297585 m；cable endpoint bridge |
| Kletek | 9 | 3.696544 | 2 | BOITE/support 对位；2 个 `conservative_znro_component` |
| Taipa | 27 | 12.184453 | 4 | BOITE/support 对位、cable endpoint bridge；4 个 `conservative_znro_component` |
| Tinggede | 26 | 6.523634 | 1 | BOITE `4D347`/support 对位；cable bridge；派生 ZNRO |
| Darat Sekip SF | 1 | 0.117728 | 0 | BOITE `2988D` 与 support `29540` 对位 |
| Lamteh SF | 2 | 3.112457 | 0 | 源 rectangular frame 转 centroid 并与 support `1C9D5D` 对位；比较基础为 LineString → Point |

六份最终审计的 `unexplained_geometry_changes=0`，表示观察到的变化可追溯；这不取消上述 review required。Kletek 的 2 个、Taipa 的 4 个、Tinggede 的 1 个派生 ZNRO 没有直接完整源 Polygon，需要单列，不能混入“全部几何已保真”。Lamteh SF 的 3.112457 m 是源框线到交付点的几何距离，不是两个源/交付锚点间的平移量。独立审计使用的 `0.01 m` 是区分数值/曲线采样误差和实质几何变化的比较阈值，不是有意改图的工程验收标准。

六份审计的 unexpected field mismatches、present fields without provenance、delivery null geometries 均为 0。源计划实体中缺少同一 canonical key 的 Lamteh main 8、Kletek 14、Taipa 1,464、Tinggede 882、Darat 2、Lamteh SF 8 条，均归入 `expanded_instance_with_definition_and_parents_in_ledger`；应按实例、定义与父对象链查证，不能直接描述为丢失。按相同图纸顺序，另有 263、21、29、25、138、59 个 source plan 对象没有可绘制的完整几何；未交付 graphic/annotation 对象也在 source disposition 中保留。这些事实没有完成每个实体的业务分类确认。

## 优先实现选择

1. **先完成逐操作的几何决定记录。** 在 review workspace 中为边界修复、设备/support 对位、cable bridge 和无直接源几何的派生区域分别显示 source/delivery overlay、距离/面积变化、来源 handle 和理由。将接受、拒绝与撤销绑定 run/source/operation 哈希，且分别生成 review receipt；接受某项几何变化不能自动变成 GCP 精度通过。首批验收样本使用 `18837E`、`1B6BA1`、`4D210`、`4D347`、`2988D`、Lamteh SF 的 rectangular frames 及 7 个派生 ZNRO。边界修复提示应询问区域语义和保留部位，不能沿用“是否把设备移到支撑点”的通用问句。
2. **补齐定位的独立证据。** Tinggede 的独立逆解已按原阈值通过，保留这次失败/修复为固定回归样本。为各测区建立 source-bound GCP/checkpoint benchmark：训练点、独立检查点、CRS/单位、路段对应、覆盖与 abstention 分别记录。先用明确的局部 metric CRS 和误差预算验证 OSM residual，再判断是否需要分区变换；任何新变换都必须在留出的检查点上证明改进。
3. **以显式范围完成 corpus 和发布验收。** 为 Manado/Semarang/Tinggar 分别生成可审查的 model-only 与 named-paper plan-domain 差异，保持提案状态直到布局意图确定；并按来源和类别处理未知块、未量测段及 orphan definitions。随后补浏览器首次打开、在线 tile 开关网络记录、GCP 微调/撤销、portable run 搬移，以及 Windows/Linux/Android 实际安装矩阵。各项输出独立结果，保持未测平台和未验收图纸的状态可见。

## 验证边界

本文没有运行完整 CI、平台安装或 QGIS 实际渲染。浏览器 HTTP 首次导航挂起，file URL 被浏览器安全机制阻止，未完成实际 UI 截图与在线/离线请求验收；已有 Node 检查、8 项 build/reconnect 测试及后端 GCP 测试仅证明各自范围。最终发布说明须引用这些范围，不使用“九份图纸全部精度通过”“全平台已安装验证”或“浏览器交互已验收”。
