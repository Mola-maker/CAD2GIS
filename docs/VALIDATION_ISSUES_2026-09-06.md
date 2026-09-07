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

截至该轮的架构遗留为 H03、H06 与一次源提取复用；后续实施进展见下文。工程证据项 E01–E06 不自动关闭。

## 后续架构实施：源复用、事务交付、修复候选

| ID | 处理与证据 | 剩余边界 |
|---|---|---|
| B25 | 原生 source 快照通过代码/源/产物 SHA 校验后供 convert 重放；batch 自动使用。Kletek 263 要素全字段与几何相同，26.95 s → 16.49 s，禁用 reader 后仍完成 | 单图计时；旧快照需重导出；原始 DWG 仍需可读 |
| H03 | published semantic job 的 pinned revision 接入规范流水线；实际 preview/commit/compile/convert 标签仿真通过，几何不变 | 仅已有资产标签、兼容类别与既有尺寸确认；新增尺寸绑定、增删资产仍未接入 |
| H06 | 新增 candidate-only，暂停点位吸附、端点桥接、有损边界修复；run、失败审查目录及 ZIP 保留候选 | 默认 legacy 保持历史行为；九图新模式与逐候选接受执行仍待完成 |
| B26 | 实图候选模式产生 9 个未应用候选，独立审计点位移动从 9 降为 0，线路顶点图不变 | 派生覆盖面和独立 GCP 验收继续保留边界 |
| B27 | 视觉核验器不识别新 revision 标签来源，误列 1 个待核实标签；新增 snapshot、job、revision、目标、原文和 provenance 联合校验 | 修复后未验证标签 0、字段不一致 0；不等于标签业务含义已确认 |

最终 Windows：628 passed / 7 skipped。Linux installed wheel 115 项相关回归通过；真实安装包三种候选已转换、独立审计和打包。最后核验修复的 wheel SHA 为 `c4e57406099ad65dbe6e7edafe16cbfa5a5c3dc488c39f6dbaf3ff2a00be9358`，96 个 Python 文件相同。标准 MCP 握手为 2025-11-25，46 个工具，新增 run_conversion 参数可见。

过程失败也保留：首次 Linux 测试脚本误用位置参数，修正后新目录复跑；新增可选 API 参数要求更新旧签名测试；新建 wheel 的 ACL 需由创建该目录的正常沙箱上下文重置继承，再在 WSL 安装。没有放宽项目配置门槛。

产物与收据在 `E:\branch_CAD2GIS\validation\architecture-next-20260906`。架构说明见 [源快照与语义交付](SOURCE_REPLAY_SEMANTIC_DELIVERY_2026-09-06.md)。

## 发布后核验（提交 4d501c2）

- GitHub Actions `34031332905`：Linux Python 3.11/3.12 和 macOS 回归全部通过；上一轮 macOS 19 个 SQLite enable_load_extension 失败已修复。
- Pages `34031332941` 部署成功。实际线上 ERRATA、Lamteh ZIP、两个 Manado 分区 QGZ 的 SHA256 均与 publication.json 一致。
- Docker `34031332929` 构建与验证成功。
- 最终本机 Linux wheel SHA256：`c5892548560d346ed37323077c1cd7fe78ab8a54a7f7c6e92d3a3abe51e7075f`；94 个 Python 文件与安装内容一致；最终 MCP 实测及 20 项打包回归通过。
- B23：分区 Web 长度摘要曾复用错误的主图摘要，显示 0 段；现按分区真实 CABLE/length_source 重建，分别 6 和 11 段，数据库原值不变。
- B24：桌面浏览器截图/前台附着接口超时；页面 DOM 中的分区目录、图层数量与本机九图下载入口已验证，不能据此声明最终线上截图已完成。独立 PNG 审查及实际 QGIS 渲染证据不受此工具限制影响。

新 Linux 交付总览：`http://127.0.0.1:8805/`；持久目录：`E:\branch_CAD2GIS\validation\linux-delivery-20260906`。公开历史演示：`https://mola-maker.github.io/CAD2GIS/deliveries/`。每个 Linux ZIP 包含视觉审查和旧版差异，19 个未认定设备的源块另列 `unclassified-source-blocks.csv`，不凭空补名称。

## 可选符号入口及两位小数显示

| ID | 暴露问题 | 处理与边界 |
| --- | --- | --- |
| B28 | 用户发现 Manado P005 到 EMR29619 的末端跳线遗漏 | 原图 `79BE`、`ONT-MDU`、21.14648990531311 原生长度已确认；v9 和 Linux 分区缺少该 handle。新项目副本恢复 v7/v8 的 patchcord 映射并精确增加 CABLE/INFRASTRUCTURE 各 1，保留原数量门槛；EMR29619 已交付 1 条，独立几何差异及长度差均 0。不能把 20 m SLACK 或引线 `79ED` 当长度；历史包不覆盖 |
| B29 | 浮点属性出现过多小数，长度显示应忠于 CAD | QML Range formatter 和 CSV 展示两位；GeoPackage 和几何保持原值。真实 QGIS 4.0.3 显示 21.15 / 20.00；长度标签明确 CAD DIM / CAD CURVE，CSV 不作为无损回导格式 |
| B30 | SVG 被要求拆出默认转换流程 | 独立 `symbol_assets` 模块、脚本与 `symbols.sqlite3`；仅显式提取和单候选 QML 导出，不调用语义写入或默认转换。详见 OPTIONAL_SYMBOL_ASSETS.md |
| B31 | Linux 无字体，符号文字可能丢失 | 无字体时拒绝含文字提取；可选 `--font-dir` 只读已有字体。真实 Manado 的 dgn003.shx 缺失，保留替代提示，不能声称原字体一致 |
| B32 | SVG 默认不透明背景和百万级渲染坐标导致 QGIS 显示异常 | 显式透明背景、渲染坐标缩至 10000 并加边距；实际 QGIS 与 Qt PNG 确认 P005 绿色环及 NP7 / 4' 字样显示；CAD 原坐标不变 |
| B33 | PYTHONUTF8 模式让两个 Windows ACL 测试错解 icacls 输出 | 显式使用 Windows OEM 编码；未放宽权限断言，两个回归通过 |

本轮代码全量 640 passed / 7 skipped；随后加入“显式选择了隐藏实体”的拒绝导出回归，Windows/Linux 专项均 13 passed。
无 AutoCAD 的真实 DWG 最终提取输出 `validation/optional-symbols-manado-20260906-v4/`：数据库、HTML、2 个候选 QML
及 `qgis-verification.json`；另 1 个显式选择隐藏文字的组合为 incomplete，不能导出 QML。前三次视觉样本保留。
动态块显示、字体替代和手选组合仍为候选；不自动发布到九图历史交付。

新跳线交付 `validation/manado-patchcord-delivery-20260906/` 的主图及两分区共 3 个 QGZ 均通过真实 QGIS 开启与渲染，
EMR29619 属性表实际显示 21.15。使用 candidate-only，数据仍是 CONDITIONAL；未知数字标签与独立 GCP 等不随该跳线关闭。
首次复跑触发旧精确数量门槛的失败日志保留。合并查看入口 `http://127.0.0.1:8808/`，持久目录
`validation/svg-patchcord-review-20260906/`，含可选符号资产、默认交付、独立审计、PNG 与下载包。

## EMR 图层不可用与 SVG 交付反馈

- 用户实际打开 `validation/qgis-portable-20260906/drawing-03/EMR29619/delivery.qgz`。原文件在独立 QGIS
  中 9 个图层均有效，EMR 为 1 个要素；用户当前会话的不可用状态未能复现，不能认定原库缺少 EMR。
  该旧版本没有 SvgMarker，且仍是 11 条缆线的历史版本。
- 另查明 Downloads 中的 `delivery.qgz` 与新 EMR29619 分区字节一致，但同目录的旧 `delivery.gpkg`
  没有 EMR 表。网页裸 QGZ 下载确实会造成数据库错配。这是独立发现，不等同于用户所给路径的根因。
- 新增可选 `tools/package_qgis_standalone.py`：按 QGIS 附件机制封装完整 GPKG；SVG 通过原 DWG SHA、
  交付数据库 SHA 和单个 source_handle 精确绑定，默认不启用。保留标准与 SVG 两个单文件版本。
- EMR29619 可选版本实际绑定 12 个 PTECH 和 1 个 BOITE，逐要素 `symbolForFeature` 均返回 SvgMarker，
  SVG 为 base64 内嵌；EMR 仍使用原样式，数据库原字节不变，79BE 长度显示 21.15。
- 验证覆盖：单文件移至空目录；旁边故意放错误的 delivery.gpkg；真实 HTTP 下载后独立打开；EMR=1、
  全图层有效；13 个 SVG 全图和末端近景实际渲染。错误实例、重复目标和缺失图层 3 项负面测试均拒绝，
  不生成输出。常规相关回归 33 passed，ruff 通过。
- 测试中 QGIS 自动附加样式库导致初版附件哈希验证误报，改为只比较 GPKG 附件哈希，未放宽数据库检查。
  WSL 再次内核崩溃，改用已有 Windows LibreDWG 完成这轮提取；没有把本轮宣称为 Linux 复跑通过。
  可选提取入口补入已有的数值线程限制，避免 OpenBLAS 默认多线程内存失败。

最终产物与验证记录：`validation/qgis-standalone-fix-20260906/`；网页
`http://127.0.0.1:8808/standalone/EMR29619-with-SVG.qgz` 已取代页面中的裸分区 QGZ 下载。
仍保留字体替代、工程 GCP、EMR 文字锚点和历史数字标签等工程语义边界。

## 更正：QGZ 白屏、临时路径泄漏与九图 SVG 复核

上一节对旧单文件交付的便携性结论撤回。真实下载 QGZ 内部仍引用 Windows 临时目录，生成进程未退出时
同进程复制检查误用了原临时文件；独立渲染代码重设范围，又掩盖了空默认范围。用户截图比例尺 1:1 的白屏
与这些缺陷一致，右侧 mask 提示不作为根因。

修复：Qt 生成的附件路径保持正斜杠，避免 QgsPathResolver 的字面前缀匹配失败；QGS 保存为 `attachment:`
数据源。DefaultViewExtent 的 spatialrefsys 直接位于范围节点下，不再套错误 crs 层。
`tools/verify_qgis_standalone.py` 必须在生成进程结束后执行；检查 ZIP 资源闭包、真实图层、SVG 渲染器选择、
保存的初始范围、非空实际画布、错误同名侧库干扰以及再次保存。旧 v3 已被最终英文后缀版本替代。

补齐旧基线的显示精度：表格 REAL 字段使用两位格式，CSV 数值列两位，QML/QGZ 同步；所有 GPKG 字节不变。
原图尺寸标注无后缀；未绑定尺寸时用原 CAD 曲线长度，用户最终指定英文 `[CAD curve]`。

产物根目录 `validation/nine-svg-portable-20260906/`：9 张主图与 2 个 Manado 分区，751 个源实例 SVG，
238 个具有匹配图例候选。11 工程独立进程加载、保存、画布和完整目录搬移/QML 加载通过。
Manado 使用已恢复 79BE 的基线；其他图不重新分类或移动几何。每图有独立 SQLite、对应 HTML、CSV、QML、
可编辑目录和自包含查看 QGZ。完整图例对应仍未验收，不能称为 final fidelity passed。

本轮 WSL 已恢复启动。`validation/linux-svg-rerun-20260906/report.json` 记录 9/9 原生 Linux LibreDWG/ezdxf
复跑，源绑定与 Windows 一致。字体通过显式本地目录提供，未用 AutoCAD；原 SHX 缺失仍有替代警告。
Linux 未安装 PyQGIS，本轮 QGIS 渲染证据来自 Windows，验证边界不能混淆。

代码回归：642 passed / 7 skipped；跳过项仍为独立真实 DWG 属性边界或外部基线输入门槛，不能计入通过。
