# CAD2GIS：CAD 能力旁路、Apache-2.0 与公开交付设计

日期：2026-10-13
状态：用户已批准具体设计；已纳入随后补充的曝光、九图复用与主流平台注册要求。
基线：Mola-maker/CAD2GIS main，提交 a75d15ebe0b854bd667390c6c05dbfe56cd10d91，版本 0.7.8。

## 1. 用户目标与已确认事项

用户要求拉取最新插件，通过三源搜索收敛社区 CAD 能力包，扩大插件能力；优化仓库与 README，提升曝光；降低许可门槛；连接官方演示网站 cad2gis.molamaker.cn；使用下载目录的 Pages、最终 Agent 和九图报告压缩包完成交付。

用户已批准 U-C4N/Autocad-MCP 独立旁路与 CAD2GIS 验收方案，选择 Apache-2.0，并确认历史贡献身份属于自己或持有相关版权。此确认解决本次原创代码改许可的权利前提，第三方许可仍分别保留。

成功标准：新增能力能从插件实际调用；默认转换仍可离线使用；源文件不被旁路改写；README 中展示的能力、版本和下载入口有验证证据；Pages 可以构建和部署；交付包中的源码、插件、wheel 与许可和版本保持一致。

## 2. 已核验的现状

- 最新源码是 0.7.8，原创代码当前采用 PolyForm-Noncommercial-1.0.0。
- 现有 wheel 的 METADATA 仍写 LicenseRef-Proprietary，旧安装包各平台版本不同。新许可产物必须重构建，旧产物保留原版本与原许可说明。
- GitHub 仓库 homepage 未设置、topics 为空，README 顶部 Linux 链接停留在 .1，而现有交付中有 .3。
- 官网与 GitHub Pages 当前 HTTPS 均返回 200；下载包中的旧备案阻塞记录是历史信息。
- Pages 使用 scripts/build_pages.py 和 GitHub Actions。九图公开交付已有独立哈希与来源核验，不将软件开源许可自动扩展到图纸数据。
- 九图包中九个 delivery-manifest 均为 CONDITIONAL，absolute_accuracy_verified=false。效果展示保留这些边界。
- 正确指定 PYTHONPATH=src 后，插件与 Pages 相关 39 项基线检查通过；这不代表全仓库测试通过。
- 工作副本位于 outputs/cad2gis-upgrade-2026-10-13/repo，不改写旧 CAD2GIS checkout 或其历史。

## 3. 三源收敛与候选决策

搜索渠道为 GitHub REST 仓库检索及官方源码、Exa、Tavily。搜索结果不直接等于可用性证据，最终以固定上游源码、安装元数据和实际 MCP 检查为准。原始搜索与输入包清单存放于相邻 audit 目录。

选择标准按任务适配、跨平台能力、明确许可、可控制的文件写入、维护状态、实际安装与工具调用依次核验；stars 仅作为辅助信号。此次选择是针对 CAD2GIS 的集成判断，不宣称覆盖全社区或完成性能排名。

| 候选 | 核验许可 | 判断 |
| --- | --- | --- |
| U-C4N/Autocad-MCP | MIT | 首选：跨平台 ezdxf 引擎、图纸理解、查询、图层与几何编辑；通过独立进程受控调用 |
| mozman/ezdxf | MIT | CAD2GIS 已有依赖；继续用于合成测试与 DXF 基础能力 |
| daobataotie/CAD-MCP | MIT | Windows COM 扩展候选，依赖已安装 CAD，作为文档中的可选替代 |
| dimitrovakulenko/dwg-mcp-server | GPL-3.0 | 单独 DWG 服务候选，本次不复制源码进 Apache 模块 |
| jdilla1277/agentcad | Apache-2.0 | 更适合 3D STEP/mesh，留作后续方向 |

首选固定到 U-C4N/Autocad-MCP 提交 cdb10638963898b3ea9b10cdd96a2c9bc495f184，源码版本 1.6.0。上游 README 声称 247 个注册工具，但本次只公开测试通过的受控子集。

不将上游 FastMCP 3.x 安装到 CAD2GIS 主环境：旁路独立虚拟环境，使用 stdio MCP；CAD2GIS 继续使用自身 MCP SDK。上游未安装、工具缺失、启动失败时，返回明确状态，现有转换服务继续工作。

## 4. 插件能力与数据流

增加独立 cad_bundles 模块，通过现有 cad2gis-agent-mcp 注册四个稳定入口：

| CAD2GIS 入口 | 行为 |
| --- | --- |
| get_cad_bundle_status | 返回固定上游身份、安装状态、引擎和能力边界；无自动下载 |
| prepare_cad_bundle_workspace | 在授权项目根目录内创建新工作副本与哈希清单，返回 workspace_id |
| list_cad_bundle_tools | 实际发现上游工具，返回允许清单与参数 schema 的交集 |
| call_cad_bundle_tool | 用 workspace_id 调用允许工具，保存副本修订及调用证据 |

工作流：

1. 授权根目录内选择源 DXF。DWG 先使用 CAD2GIS 现有 reader 导出 DXF，再交给旁路；无界面 ezdxf 本身不读取 DWG。
2. 建立全新旁路 workspace；保存只读源快照、当前副本、source_sha256 与清单。拒绝覆盖已存在 workspace，拒绝符号链接逃逸和越界路径。
3. 每次调用打开指定副本，使用白名单工具，成功后保存独立修订；失败不替换当前副本。避免跨 workspace 共享上游当前图纸状态。
4. 返回结果、前后哈希、修订路径、上游提交和工具名。结构化结果可以进入 Agent 推理，但不直接将旁路判断写成 CAD2GIS VERIFIED。
5. 需要交付 GIS 时，将新副本作为新来源，通过 project/onboarding/convert/audit 流程生成新 run。现有核心来源检查主要只接受 DWG，实施中需补齐原生 DXF 接入口：复用当前 LibreDWG→DXF reader 的实体/块/属性解析逻辑，记录 native_dxf 来源和真实副本哈希，不走 injected_records 模拟入口，也不把 DXF 改后缀冒充 DWG。旁路结果属于新的输入，原图的几何保真声明不能自动继承。

首期白名单为官方固定源码中存在的：
drawing_understand、drawing_topology_check、drawing_diff、
analysis_select_by_layer、analysis_select_by_type、
entity_get、layer_list、layer_create、
entity_create_line、entity_create_circle、entity_create_arc、
entity_create_polyline、entity_create_rectangle、entity_create_text、entity_delete。

drawing_open、drawing_save 由适配器内部管理，不让调用者指定任意打开/保存路径。drawing_diff 的比较路径由 workspace 修订记录注入。对外参数禁止提供 path、old_path、new_path 或输出路径；只允许适配器注入经过校验的 workspace 文件。拒绝未列工具及上游通用 call_tool、命令透传和 LISP 执行。

启动环境固定 AUTOCAD_MCP_BACKEND=ezdxf、DISCOVERY_MODE=off、TOOL_PACKS=core、ENABLE_3D=false、DANGEROUS_COMMANDS_ENABLED=false；ALLOWED_PATHS 仅含该工作副本目录。设定请求超时、文件大小和返回结果上限。启动 argv 不通过 shell 拼接；不转发主服务的 AI/API 密钥。

旁路安装使用固定 Git 提交的独立环境，安装步骤显式记录版本并验证 MCP 握手。不在默认 get_capabilities 或正常转换过程中自动安装。Windows COM 接入仅作为后续可选方向，本次不声称 Linux 验证了 Windows CAD 桌面控制。

## 5. 许可与版本

新源码发行版本定为 0.8.0，包版本、两个插件 manifest、marketplace 中存在的版本字段及 PLUGIN_CONTRACT_VERSION 同步。新增入口更新精确 MCP_TOOL_NAMES 和能力描述。

原创源码 LICENSE.md 使用完整 Apache License 2.0 正文；NOTICE.md 保留版权与贡献署名，明确用户已确认的原创代码权利、第三方例外、图纸/数据/商标边界。同步 pyproject.toml 的 SPDX、license-files、插件 README、应用架构说明、第三方声明和发行说明。

LibreDWG 及其补丁保留 GPLv3；pyramids-gis 保留 GPLv3；字体、Web vendor 和所有其他依赖保留自身许可与归属。安装包中的 GPL 组件仍需相应源码和构建材料；不把整个含第三方运行时的安装包统称为 Apache-2.0。

新 wheel、sdist、插件 ZIP 的许可和版本必须从实际包内容检查。现有 Windows/macOS/Linux 二进制只作为旧版下载，不能仅改文件名充当 0.8.0。当前 Linux 主机可验证 Python/插件及可运行的 Linux 构建；Windows/macOS 新安装包需对应平台 CI 验证，未完成时在交付清单标为未构建，不宣称已完成跨平台发行。

## 6. README、仓库与曝光

README.md 保留中文为默认，新增 README.en.md。开头直接解释 DWG/DXF 到 GIS、Agent/MCP 接入、可视审查与 QGZ 交付；提供官网、安装、演示、Release、英文版及 Apache-2.0 入口。

能力矩阵区分核心已支持、需额外 runtime、可选 CAD 旁路、静态演示四类。演示截图和九图案例使用已核验素材，标注 CONDITIONAL 与绝对精度边界。提供复制即可运行的最短安装和合成 DXF 示例，复杂安装细节放到文档。

修正 Windows 0.7.8、Linux 0.7.0-3、macOS 0.7.0 历史下载矩阵；新 0.8.0 仅链接实际构建/发布产物，不能使用不存在的 Release URL。

增加 CONTRIBUTING、问题模板、PR 模板和明确的复现实例要求；项目描述突出 DWG/DXF、GeoPackage、QGIS、MCP，设置 homepage 为 https://cad2gis.molamaker.cn/，topics 使用 cad、gis、dwg、dxf、qgis、geopackage、mcp、geospatial 等真实关键词。

曝光路径包括 GitHub 搜索与 topics、官网搜索收录、中英文文档、可复制安装入口、公开 Release，以及主流 Agent 插件/MCP 目录。记录每条路径的实际落地链接和状态，不将配置文件生成视为平台公开注册。

网站补齐 description、Open Graph、canonical、SoftwareApplication JSON-LD、robots 和 sitemap，展示新增能力及安装入口。GitHub Pages 与官网使用各自正确的 base URL；不以未经验证的工具数量、测量精度或平台能力宣传曝光。

## 7. Pages、官网与交付包

输入压缩包保持原始文件不变。已记录：
07_Pages网页.zip：1009 个文件；
01_安装包与Agent-MCP入口.zip：58 个文件；
nine-svg-portable-20260906.zip：1248 个文件、9 张图。

Pages 输入包含生产站点、备份、历史部署材料等多个目录，不将整个压缩包直接作为站点根目录。现有仓库构建为可维护基线；下载包中的生产素材经文件级比较后按需合入。保留公开已有的 docs、报告与下载 URL，必要时通过重定向兼容。

用户补充：九图只需要展示效果；Pages 已有则不上传。已现场核验 https://mola-maker.github.io/CAD2GIS/deliveries/ 返回 200，包含九张主图；assets/catalog.json 含九个主图和两个分区共 11 条记录。下载的 Pages 包也含主图效果资源。优先链接/复用这些已有展示，保留 CONDITIONAL 和 false 精度标识；不新建或上传九图完整报告 ZIP、QGZ/GPKG 副本。既有报告和下载 URL 保持可达。原始 DWG/DXF 不放入新增公开演示包。

最终输出：
- cad2gis-0.8.0-pages.zip：可部署站点根目录、构建清单、SHA256SUMS；
- cad2gis-0.8.0-agent-delivery.zip：新源码、wheel/sdist、插件 ZIP、安装与旁路配置、验证记录、第三方声明；
- release-manifest.json：每项产物的版本、源码提交、大小、SHA-256 和实际验证范围。

GitHub Pages 通过现有 Actions 部署。官网在现有托管路径和凭据确实可用时部署相同内容并验证；若当前环境没有官网管理通道，交付可部署包和准确的待部署状态，继续完成 GitHub Pages，不把打包当作官网上线。

网站公开访问 smoke 检查至少覆盖首页、安装、旁路能力、现有九图目录及九张效果图；抽查现有报告/下载兼容性，不因此生成新上传；页面展示还需浏览器交互验证。发布到远端以本次明确部署要求为授权范围，不发送社区邮件或代发宣传消息。

### 主流平台注册与可发现性

用户已明确“主流平台”。执行目标为 Codex 与 Claude Code 的仓库 marketplace、Cursor/VS Code 的 MCP 接入，以及官方 MCP Registry、Smithery、Glama 的公开收录。另核验 OpenAI 公共 Plugins Directory 和 Anthropic 官方 marketplace 的提交条件；它们与自行托管 marketplace 是不同状态。

官方 MCP Registry 当前 search=cad2gis 为空，PyPI cad2gis 返回 404。优先将新版 MCPB 放入 GitHub Release，登记 io.github.Mola-maker/cad2gis-agent 并提供真实 fileSha256。Smithery 的 stdio MCPB 发布与 Glama GitHub 源码收录按当前官方流程执行。

身份、命名空间和审核要求由平台控制。复用已有授权登录；缺少登录或遇到需要用户身份验证时，先完成可校验的提交包、文案、图标和安装验证，只暂停对应平台，记录具体待办。不得把本地 stdio 服务伪装为公开 HTTPS 服务，不向公网开放当前无认证的文件操作服务。公开 MCP 目录的 server listing、GitHub marketplace 可安装性与审核中的公共插件目录 listing 分别记录。

产物增加 cad2gis-agent-0.8.0.mcpb 与 registries/publication-status.json。状态枚举为 prepared、submitted、under_review、published、requires_user_action、failed；只有保存实际公开 URL 并通过读回检索才能标为 published。

## 8. 验收与风险控制

新增行为先用合成 DXF 写失败测试，再实现：
- 未安装上游时状态明确，核心工具能正常注册；
- 路径越界、符号链接、重复 workspace、未列工具被拒绝；
- 操作副本后原始源哈希不变；
- 编辑失败不发布修订；超时退出后主服务继续可用；
- 实际 MCP 握手、工具发现、查询、创建实体、保存、差异与拓扑检查；
- 对编辑后的 DXF 运行 CAD2GIS 合成来源转换/验收，证明旁路与核心形成实际链路；
- MCP 精确工具契约、插件 manifest、打包版本与许可一致；
- Pages 构建、链接和资源核验，官网/base URL 元数据正确；
- 九图哈希、来源和 CONDITIONAL 状态不被发布流程篡改。

运行相关现有插件、几何保真、来源重放、运行时、Pages 与打包测试；只有新变更或失败才扩大/重复检查。完整测试未运行时明确记录范围。

主要风险：上游可能有 API/依赖变化，以固定提交和隔离环境控制；首次安装需要网络，离线未安装状态不能伪装为 ready；大型图纸调用可能超时，要提供结构化失败并保留旧修订；跨平台安装包和官网托管凭据是独立交付条件；曝光优化增加可发现性入口，不能承诺 stars 或用户数量增长。

## 9. 审阅后续

用户已批准本文，现进入 writing-plans。按能力、许可/曝光/网站、发行/平台注册三个可独立验收的子项目编写计划，用户审阅整组计划并选择执行方式后开始实施。当前会话不主动委派子代理；除非用户选择并授权委派，默认建议在当前会话执行。

主要原始来源：
- https://github.com/U-C4N/Autocad-MCP
- https://github.com/mozman/ezdxf
- https://github.com/daobataotie/CAD-MCP
- https://github.com/dimitrovakulenko/dwg-mcp-server
- https://github.com/jdilla1277/agentcad
- https://www.apache.org/licenses/LICENSE-2.0
- https://www.apache.org/licenses/GPL-compatibility.html
- https://www.gnu.org/licenses/gpl-faq.html

