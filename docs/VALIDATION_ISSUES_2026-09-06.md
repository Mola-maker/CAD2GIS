# 九图跨平台测试与历史问题台账

持续更新；状态以实际证据为准。软件回归通过、几何一致、语义验收和绝对定位分别记录。
不改写历史失败，不把 CONDITIONAL 升级为工程验收通过。

## 本轮已定位的软件问题

| ID | 问题及影响 | 处理 / 状态 | 证据 |
|---|---|---|---|
| B01 | 批次调用不存在的 `--source`，被参数缩写解析为 CRS；九图均未开始转换 | 已改为位置参数，增加真实 CLI parser 集成回归 | `tests/test_batch_delivery.py`；WSL `nine-20260906` 保留失败 |
| B02 | 尚未执行的图纸不显示，运行页阶段不明确 | 已预填全部 PENDING 项；HTML 展示阶段并在运行中刷新 | 同上；批次 `batch-report.json` |
| B03 | QGZ 项目与图层 CRS 元数据不足，移动目录后打开失败 | 已补齐 CRS、相对路径和地图范围 | `validation/qgis-portable-check.json`，11 项实际 QGIS 打开 |
| B04 | 覆盖多边形充当标签障碍，遮挡点标签；线标签缺候选位置 | 已关闭覆盖面的障碍并补线标签 placementFlags | Lamteh 实际 QGIS 截图 `qgis-labels-detail.png`；样式回归 |
| B05 | CABLE 长度数据存在但 QGIS 交付未显示 | 交付 QML 显示长度及 CAD/GEOM 来源；保持业务名称和原数据库不变 | CSV 与 QGZ 字节/字段回归、Lamteh 近景 |
| B06 | 部分 SQLite Python 构建没有 enable_load_extension，查询直接异常 | 已按实际 API 能力调用，仍强制 query_only | SQLite 无扩展 API 回归 |
| B07 | Web 把不同投影的交付坐标当 Web Mercator，产生错误位置 | 已按真实 SRS 导出完整精度的 EPSG:4326 地图数据 | 九图浏览器逐项数量检查；QGIS 实际 SRS |
| B08 | WSL 在 C 盘，首次启动发生内核 Machine Check | 已迁 D，前后 VHDX SHA256 相同，重试启动成功；硬件根因未定 | `validation/wsl-migration-20260906.json` |
| B09 | WSL Python 3.13 不支持当前程序且无 Linux DWG reader | 隔离安装 Python 3.12、编译 LibreDWG；doctor ready | `validation/linux-runtime/doctor.json` |
| B10 | Windows 有符号 RGB 与 Linux 无符号 RGB 造成无效颜色字符串和清单哈希不等 | 已按 24 位 RGB 修复读器；01/02/08 完整逐记录审查后生成新配置，已真实复跑完成 | `tools/review_reader_migration.py`；01 为 59,369 条、02 为 63,483 条，浮点最大差 8.89e-16；08 为 1,538 条，无数值差 |
| B11 | 历史现场脚本硬用 Windows CREATE_NO_WINDOW | 已改为按运行平台能力设置 | `tools/onsite_canonical.py` |
| B12 | AutoCAD 与 LibreDWG 在 03/07/09 的实体和文字集合不同 | 不能使用颜色迁移；必须重新准备 source-bound 候选并比较输出，旧清单拒绝是有效保护 | `validation/linux-runtime/inventory-diff.json` |
| B13 | 测试期间审定项目可能被修改，无法证明本轮具体配置 | 已复制独立 project 并比对全部文件 SHA；随批次记录快照 | `cad2gis.batch.run_batch` |
| B14 | 首次 AI 查询才触发冷索引，延迟原因不可见 | 新增 `index-source` CLI；批次显式 source-index 阶段及逐阶段耗时/日志 | CLI parser 与 batch 回归；Linux 热查询中位数 6.94 ms，5 实体上下文 9.01 ms；首次进程查询 2.57 s |

## 上轮能力核验和精度架构遗留

| ID | 问题 / 边界 | 当前处理与待验收内容 |
|---|---|---|
| H01 | 批量 HTML/ZIP 只存在现场脚本，安装包没有统一接口 | 已新增包内 batch prepare/run/package 和独立 visual_audit；九图及两分区已执行；保持 CONDITIONAL |
| H02 | Windows 绝对输入路径不能迁移 | 已建相对路径输入契约，原 DWG SHA 绑定、每图审定配置及独立输出目录；九图已完成 Linux 复跑 |
| H03 | AI 写入后的编译与正式交付需统一编排 | 现有 typed preview/hash/CAS/compile 写通道保留；待核查缺少的编排接口 |
| H04 | 冷索引耗时长，MCP 往返比 SQL 执行慢 | 已增加显式索引阶段，原有批量上下文接口保留；待 Linux 冷/热读取测量。SQLite 为持久状态，Redis 不替代事实或 revision |
| H05 | GCP 浏览器相似变换预览与正式平移模型不一致 | 已统一服务端正式平移模型、目标 CRS、权重及训练/检查点分离；实际浏览器验证，预览 hash 防过期；合成点不作工程精度证据 |
| H06 | 图形修复混在分类中，被误解为原图不变 | 保留已声明位移/损失和待审状态；待拆分独立候选，原事实不变 |
| H07 | 主图通过不代表分区通过 | 已打包主图及 Manado 两分区，共 11 份 QGZ/GPKG；分区过程与演示完整性继续核查 |
| H08 | Web 左窗只含交付要素，不能证明原图无遗漏 | 已明确标为 GIS 交付坐标，另提供独立源图叠加；完整原图与未交付实体去向仍需单独审查 |
| H09 | debug_mcp 与不同模型客户端标准流程 | 既有 stdio 标准工具通道继续保留；最终 installed-wheel 已实测 46 工具、协议 2025-11-25、debug_mcp 和 SQL 查询；不宣称所有模型客户端已实测 |
| H10 | Pages 未包含完整可搬移交付与九图过程 | 已生成全量派生包、CSV、QML、QGZ、过程、SHA 清单；待最终校验和发布，不发布 DWG |

## 不可用软件补造的工程证据

| ID | 项目 | 验收要求 |
|---|---|---|
| E01 | 九图无独立实测 GCP，部分仅 OSM 粗定位 | 提供权威控制点及独立检查点后验收绝对位置；当前 CONDITIONAL |
| E02 | 部分 BOITE 原图无标签；Tinggar 31 个邻近数字语义未确认 | 保留空值、数字源实体/距离及待审关系，不猜造名称 |
| E03 | Lamteh 盒点位移约 12.30 m、边界尖刺损失约 97.7 m；Taipa 点位移约 12.18 m | 逐对象候选与原图对照，工程人员接受后才变更验收状态 |
| E04 | Manado EMR28560 线路桥接约 7.91 m，长度增加约 7.36 m | 主图与分区独立审查，不能以有 lineage 代替修复接受 |
| E05 | Z/3D、AutoCAD 字体/HATCH/WIPEOUT/proxy 像素一致性未完整覆盖 | 明确审计覆盖范围；XY 审计不声明上述能力已验证 |

## 证据保留约定

- 每轮使用新目录，逐图保存日志、错误类型、结果与批次报告；失败图继续进入汇总。
- 每个修复关联原始错误、改动、回归测试和真实图纸验证；未验证不得标“已解决”。
- 参考：`docs/ONSITE_PRECISION_UPGRADE_2026-09-05.md`、`docs/BATCH_DELIVERY_CONTRACT.md`、现场 `visual-qa/FINAL_QA.md`、`validation/SIX_CAPABILITIES_2026-09-06.md`。
- RGB 修复依据：[Autodesk DXF 420 的 24 位颜色定义](https://help.autodesk.com/cloudhelp/2024/ENU/AutoCAD-DXF/files/GUID-3F0380A5-1C15-464D-BC66-2C5F094BCFB9.htm)。迁移只允许同源 SHA、完整记录对应、颜色位一致及最多 1e-9 原始单位浮点差；新数值保持原样，所有差异写收据，其他变化一律拒绝。


## 本轮补充发现与验证收据

| ID | 问题 | 处理 / 证据 |
|---|---|---|
| B15 | wheel 目录 ACL 导致安装失败，随后误用旧包的 v3 尝试失败 | 仅重置新建 wheel 目录继承 ACL，安装成功后逐个校验包内 94 个 Python 文件；失败尝试保留 |
| B16 | PYTHONPATH checkout 被误报 installed | 修正运行身份；单元回归及最终 installed-wheel 路径/字节验证 |
| B17 | QGIS 核验器相对路径调用 as_uri 失败 | 入口 resolve；11 历史项目及 11 Linux 项目真实 QGIS 打开、渲染通过 |
| B18 | Web GCP 旧文案与测试锚点未随正式预览更新 | 更新共享界面文案及测试；实际 Lamteh 浏览器显示正式 EPSG:23846 平移预览 |
| B19 | AutoCAD 无属性 INSERT 串入下一块属性 | 修复 DXF66 与 ATTRIB 序列边界；真实 DWG 回归 1 passed；Semarang 51、Manado 89 个历史块受影响，历史发布附 ERRATA.md |
| B20 | batch 只审查主图，不审查 delivery_partitions | 自动遍历清单中全部分区；分区失败影响批次状态；Manado 两分区已 Linux 实跑 |
| B21 | OpenBLAS 多线程内存分配失败使 MCP 握手中断，WSL 测试发行版另出现停止/E_UNEXPECTED | CLI/MCP 在 GIS 导入前默认限制数值线程，保留操作者显式配置；8 项失败相关回归通过，全套 604 passed。WSL 恢复成功，硬件 Machine Check 根因仍未确认 |
| B22 | 独立测试从其他 cwd 执行时找不到仓库 tools 模块 | Linux 测试只把仓库根加入 PYTHONPATH，cad2gis 继续使用 site-packages；保留首次收集失败日志 |
| E06 | Linux 03/07/09 的部分数字注记变成 SITE/BOITE 标签 | 保留 label-differences.json 和字段来源；数字业务含义未确认，不作为语义已验收结果 |

Linux 选择结果：主图数量 56、1000、375、715、673、515、28、263、39；分区 14、26。与旧版减少的 19 个 BOITE 全部由真实 AutoCAD 只读提取确认存在历史属性串读。无属性块是否应交付仍需明确分类规则，不能挪用相邻块属性。

证据：`validation/linux-delivery-20260906/` 的总览、baseline-comparison.json、label-differences.json、逐图视觉报告及 ZIP；`validation/autocad-attribute-fix-20260906/`；`validation/qgis-linux-delivery-final.json`；`validation/linux-runtime/linux-mcp-sqlite-release-verification.json`；`validation/final-fixes3-tests.log`。

尚未关闭的架构项：H03（semantic compile 与正式候选交付的统一编排）、H06（历史自动修复拆分为独立待审候选）、一次源提取复用（当前 export-source 与 convert 会重复读取）。这些不能用测试通过替代实现，列为下一步优先改造。工程证据项 E01–E06 不自动关闭。
