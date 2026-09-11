# Ubuntu / Claude Code 九图验证记录（2026-09-11）

基线为用户指定的 `cf22407fa69ad39f3ce46cdbf862a76bbc7f95ba`，修复分支为 `fix/linux-claude-verification-20260911`。九张真实 DWG 均完成转换，共 3794 个交付要素；九份产物审计通过。所有运行仍为 **CONDITIONAL**，不是测量、语义或图例的最终验收。

本机证据根目录是仓库下的 `tmp/verification-20260911/`（以下路径均相对此目录）。原始图纸来自 `/home/jlu/下载/datadwg`；最终重新计算九份原件 SHA-256，全部与测试前一致。测试项目、别名副本和输出均独立保存。

## 运行环境与覆盖边界

Ubuntu 26.04.1 LTS x86_64；Python 3.12.14；Node 24.19.0；Claude Code 2.1.266；CAD2GIS 0.6.0；本机编译 LibreDWG 0.14；pyramids-gis 提供 GDAL 3.13.1。修复了原 `.venv` 指向已删除临时 Python 的启动问题，并安装 agent/test 依赖。strict/full doctor 和 `pip check` 通过。

七张已有配置图通过从 cf22407 构建的非 editable wheel 执行；两张新建配置图通过包含本轮 Python 修复的非 editable wheel 执行。最终工作台使用持久安装 `.venv/claude-agent` 中的 wheel，端口 40252，仅监听本机。最终 wheel 还包含窄屏按钮修复。没有把源码导入测试等同于安装版测试，也没有执行新 `.deb` 的系统安装验证。

## 九张图的实际结果

每份运行的 `delivery.gpkg`、源/证据数据库、样式、SVG 和 OSM 等 manifest 绑定产物均经本地 `audit_run` 校验；实际图层数量与 manifest 一致。

| 图纸 ID | 交付要素 | 本地产物审计 | OSM 状态 | 配置来源 |
|---|---:|---|---|---|
| lamteh_main | 1000 | PASS | abstained | 既有配置 |
| lamteh_sf | 56 | PASS | abstained | 既有配置 |
| tinggar | 715 | PASS | abstained | 既有配置 |
| kletek | 263 | PASS | abstained | 同字节历史文件名绑定 |
| semarang_sf | 39 | PASS | abstained | 同字节历史文件名绑定 |
| taipa | 673 | PASS | matched | 同字节历史文件名绑定 |
| tinggede | 515 | PASS | abstained | 同字节历史文件名绑定 |
| manado-tomohon_uplink | 415 | PASS | unavailable | Claude 新建 source-bound proposal |
| darat_sekip_sf | 118 | PASS | abstained | Claude 新建 source-bound proposal |

七张已有配置结果位于 `claude-configured/<id>/run/`；两张新配置结果位于 `claude-recovered/<id>/run/`。逐图字段计数和审计摘要见 `nine-drawing-results.json`。

直接使用当前下载文件名时，六个旧 baseline 的 inventory 绑定不通过。对其中四张，仅使用历史文件名的**同字节副本**即可恢复完整 inventory hash，证明差异来自文件名绑定。没有编辑 baseline hash、门禁或计数。Manado 和 Darat 的完整 inventory 无法以此恢复，因此保留旧配置的阻断，另外 bootstrap 新项目。证据见 `filename-binding-proof.json`、`source-names.json` 和 `original-source-integrity.json`。

## Claude 插件全流程

实际执行插件 validate、添加本地 marketplace、local scope 安装，并通过 stdio 连接实际工具。身份检查返回版本 0.6.0、46 个工具及 convert skill v3。工具调用不是模拟回复。

七张已有配置图完成 `validate_project → run_conversion → inspect_run → audit_run`，七次 Claude 调用的审计均 PASS。两张新图完成 `inspect_source → bootstrap_project → prepare_ai_onboarding → typed proposal → apply_ai_onboarding → validate_project → run_conversion`。第一次 proposal 被结构校验拒绝，未提交配置；恢复轮根据真实诊断省略 Manado 的 5 个、Darat 的 6 个不支持标注候选，保留未识别内容，随后两份 proposal 均 `auto_accepted`，转换均完成。此状态是程序自动准入，不是人工审核签字。

恢复轮在转换完成后收到外部模型服务 **HTTP 402 / Insufficient Balance**，因此未完成 Claude 端最后两次 inspect/audit。这两份产物由本地审计工具补完。不能报告“Claude 九图无人干预全链路全部通过”。CLI 报告的各次模型费用累计约 **$2.695767**（包括连通性 smoke），低于用户授权 $8；该数字是 CLI 计量，不是服务商账单核对。

首次测试禁用了 Read，Claude 无法读取大工具结果的自动落盘文件；后续恢复轮启用 Read 后正常读取。首次 smoke 错把 doctor 当作工具名而导致正确的 `get_runtime_status` 不在测试白名单，这属于测试配置问题。仓库的 doctor 是 CLI 功能。

### 本机持久启动方式

已安装的独立运行时位于 `.venv/claude-agent`；个人插件配置位于忽略提交的 `.claude/settings.local.json`。当前宿主 PATH 没有 MCP 入口，单独设置 Claude settings 的 PATH 在 `claude mcp list` 中仍出现 ENOENT。因此创建了明确设置运行时 PATH、缓存和两个授权项目根目录的本机启动入口：

```sh
cd /home/jlu/桌面/claude-project/CAD2GIS
.venv/claude-agent/launch-claude
```

同一入口执行 `mcp list` 已显示 `plugin:cad2gis-agent:cad2gis … Connected`，证据为 `claude-persistent-health.txt`。此启动入口依赖本机项目位置和 `tmp/runtime-cache`，移动项目或清理缓存后需要调整/重新安装；它不是便携发行包。模型余额问题仍需在当前服务商侧解决，没有更换模型、购买额度或改动凭据。

参考：[Claude 插件规范](https://code.claude.com/docs/en/plugins-reference)、[Claude MCP 配置](https://code.claude.com/docs/en/mcp)。

## 视觉证据、SVG 与路网

实际浏览器操作覆盖选图、环境检查、源图检查、独立目录转换、打开结果、CAD/GIS 地图、SVG 对应审阅、SQLite 查询及属性表翻页。Kletek 的真实查询返回 CABLE 33 段、源 CAD 长度合计 1078.4 米；IMB 从第 1 页成功翻至第 2 页。修复后独立安装工作台再次打开 Taipa，显示源实体 3276、交付 673 和相对 OSM 已应用状态，保留在侧边栏。窄屏仍使用纵向地图及滚动布局，没有把截图中显示的交付几何当作完整 DWG。

对九份最终运行分别执行独立 `visual_audit`，从独立源快照逆变换核对交付几何，未复用生产变换器：无未解释的几何变化、无意外字段值失配；源和交付审计前后 hash 不变；数值往返检查通过。已有显式位移和派生面仍保留其操作来源，不能据此声称所有要素坐标完全未移动。两张新图中，Manado 有 18 个显式位移和 1 个派生要素，Darat 有 3 个显式位移；未直接交付的源实体仍留在源证据中，需要按用途审查分类覆盖。

| 图纸 | SVG 对应点数 | 图例候选 | 仅源实例 | 非块未识别 | 嵌套 handle 未识别 |
|---|---:|---:|---:|---:|---:|
| lamteh_main | 671 | 112 | 136 | 423 | 0 |
| lamteh_sf | 30 | 26 | 2 | 0 | 2 |
| tinggar | 577 | 81 | 25 | 471 | 0 |
| kletek | 210 | 0 | 43 | 167 | 0 |
| semarang_sf | 20 | 19 | 1 | 0 | 0 |
| taipa | 508 | 0 | 139 | 369 | 0 |
| tinggede | 411 | 0 | 74 | 337 | 0 |
| manado-tomohon_uplink | 323 | 0 | 78 | 245 | 0 |
| darat_sekip_sf | 17 | 0 | 15 | 0 | 2 |

九份 SVG 均 `auto_applied=false`，`complete_legend_correspondence_verified=false`。提取文件生成及来源绑定已验证；没有人工核准完整符号/图例对应，也未提供专用 SHX 字体目录进行完整字形验收。

OSM 总计：1 matched、7 abstained、1 unavailable。Manado 的 unavailable 原因为 `osm_roads_unavailable`。Taipa 的匹配仅提供相对 OSM 参考；所有结果 `absolute_accuracy_verified=false`。保留搜索不完整、支撑不足和未知匹配状态，没有放宽阈值强行对齐。在线瓦片默认关闭，已有 OSM 匹配证据与矢量层可独立审阅。

人工 GCP 流程另以真实 Kletek 几何和明确标记的模拟目标（EPSG:3857 向东 2 米、向南 3 米）验证：两训练点、一独立检查点；预览、生成新运行、过期 revision/controls 拒绝、原交付 hash 不变、重启后恢复控制点及结果全部通过。近零残差仅说明模拟流程正确，不是现场精度。证据为 `real-review-check.json` 和 `simulation-rematch-audit.json`。

## 本轮代码修复

1. **SQLite 连接生命周期与事件分页**：`SQLiteReviewStore._connect` 在成功、异常、冲突后明确关闭连接；保留事务提交/回滚。增加 session_id 索引以利用隐含 rowid 顺序分页，避免 ORDER BY 临时 B-tree。50000 条交错 session 事件、100 次读取、暂停 GC 的本机测量：文件描述符增长 201 → 0，耗时中位数 8.532 ms → 0.413 ms；返回事件完全一致。约 20.7 倍是这个读场景的改善，不是整个转换器速度提升。
2. **智能体标注错误可恢复性**：结构失败返回受校验的 candidate ID 和 `committed=false`，仅适用于已证明尚未暂存配置的 `apply_ai_onboarding` 分支。嵌套自动建项工具仍返回提交状态 unknown；不向客户端泄露原始图纸文本。结构门禁未放宽。
3. **源 GeoPackage 字节确定性**：完整测试发现两份逻辑相同数据库仅 SQLite 文件头 change counter/version-valid-for 不同。驱动时间变化可能增加提交次数，事后统一时间戳加 VACUUM 无法消除文件头差异。现在在 GDAL 写入生命周期内使用线程局部 `OGR_CURRENT_DATE` 固定时钟，再保留现有 metadata normalization；退出后恢复调用者设置，失败仍不替换原文件。新增测试覆盖驱动原始时间戳、设置恢复和失败原子性。[GDAL GeoPackage 时间配置说明](https://gdal.org/en/stable/drivers/vector/gpkg.html)。
4. **窄屏视觉问题**：通用移动端样式把工作区按钮字号设为 0，导致没有图标的“展开/收起工作区”成为空白按钮。增加针对性样式，实际 640 像素侧边栏复核为 12px 可见文字，按钮宽约 82px。

## Android、服务器与 Redis

使用官方 Android SDK 平台 36、build-tools 36.0.0、Adoptium JDK 17 构建开发版 APK。下载归档经过官方 metadata 中 SHA-1/SHA-256 校验。Java 服务器地址边界测试 27 项通过，aapt2、javac、D8、zipalign 和 APK v2/v3 签名验证成功。

产物 `android/CAD2GIS-0.6.0-android-dev.apk`，25132 字节，SHA-256 `6602388028fdb44f0d965cbf005bc23d545216152f78289889de2df682099977`。min API 26、target 35、compile 36。这是开发签名，未运行真实 Android 设备或模拟器；不能报告端到端真机、断网重连或触摸交互已验收。

Android 是服务器工作台的 WebView 客户端，当前没有 Redis 实现。服务器持久化依赖 SQLite；jobs/outbox、幂等、租约与 revision 控制的现有测试纳入完整套件，本轮优化的是实际使用的审阅 SQLite。Redis 为可选未来组件，诊断为 not_configured，没有做 Redis 性能或故障恢复验收，也没有为凑覆盖引入空服务。

## 测试与证据索引

最终 Python 全套：**1173 passed / 14 skipped / 3 warnings，67.52 秒**，见 `pytest-final-host.xml` 与 `logs/pytest-final-host.log`。跳过项涉及缺失外部 APD/Hutabohu corpus、未指定的 AutoCAD attribute corpus 和 Windows 专用权限测试。Ruff、compileall、Node 语法及 git diff whitespace 检查通过。

早期错误记录保留：错误 Node 环境导致 8 个测试失败；修正 Node 后基线 1166/14；本轮修改后捕获一次 source.gpkg 字节确定性失败，修复后得到上述最终结果。受限沙箱中本机通信测试启动停顿，停止该轮并在正常宿主环境重跑。没有删除失败日志或把中断轮次算作通过。

关键本机证据：`nine-drawing-results.json`、`original-source-integrity.json`、`claude-tool-receipts.json`、各图 `local-artifact-audit.json` / `visual-audit/report.json`、`review-benchmark-before.json` / `review-benchmark-after.json`、`android/build-report.json`。原始 Claude 日志可能包含图纸文字，工作台私有日志包含本机登录信息，均不提交 Git，也不放入交付摘要包。

软件 wheel 位于 `dist-release/cad2gis-0.6.0-py3-none-any.whl`；它需要上述原生依赖，不是自带运行时的 Linux 安装器。
