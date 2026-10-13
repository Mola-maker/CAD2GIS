# CAD2GIS Wiki · 图纸到可审查的 GIS 交付

[English](Home-EN) · [官网研究报告](https://cad2gis.molamaker.cn/) · [完整视觉版 / 双语切换](https://mola-maker.github.io/CAD2GIS/wiki/index.html) · [安装与发布](https://github.com/Mola-maker/CAD2GIS/releases/tag/v0.8.0)

CAD2GIS 把 DWG/DXF 图纸转成有图层、有坐标声明、有检查结果和来源证据的 GIS 交付。MCP 让智能体调用转换、检查和审查工具；插件把任务流程、技能说明与工具配置送到智能体客户端。两者共同帮助用户完成“理解图纸 → 修订副本 → 转换 → 检查 → QGIS 交付”，并留下可以复查的记录。

当前 Agent 发行版本为 **0.8.0**，原创代码采用 **Apache-2.0**。官网保留本地压缩包中的研究报告、字体和 CSS；报告与十一章手册属于历史快照，里面的 0.7.x 桌面安装包和实验数字不代表新版 Agent 的重新验证。本 Wiki 说明当前 MCP/插件能力。

## 项目价值：把转换结果变成可以交接的成果

| 用户遇到的问题 | CAD2GIS 提供什么 | 实际价值 |
| --- | --- | --- |
| 图纸能打开，GIS 属性、图层和坐标却难以交接 | DWG/DXF 输入、GIS 图层与 QGIS 交付流程 | 把视觉图纸整理成可继续分析和审查的地理数据 |
| 智能体生成了一份文件，但说不清依据 | 输入 SHA-256、运行结果、检查报告与修订记录 | 审查者能核对来源和处理过程 |
| 修图时误改原始文件，难以回退 | 原图保留；CAD 编辑在独立副本与版本快照中进行 | 原始证据可保留，修改可以追踪和比较 |
| 不同智能体客户端需要分别配置工具 | 本地 stdio MCP、Codex/Claude Code 插件入口及 Cursor/VS Code 配置 | 同一工具链可以被多个客户端调用 |
| “看起来对齐”被当作测绘验收 | 明确区分检查状态、坐标声明和独立绝对精度验收 | 防止把演示结果误当作已经实测的成果 |

## MCP 与插件分别解决什么

MCP 是智能体与本地 CAD2GIS 运行时之间的工具接口。用户指定授权的 `project_root`，智能体在该项目范围内调用工具。文件读写受路径校验限制；转换结果和证据保存在本地项目。首次安装仍需下载运行时与依赖，某些 OSM 流程需要联网获取底图；“本地运行”并不意味着所有功能都完全离线。

插件提供 MCP 配置、任务技能和工作流程。它让智能体知道什么时候调用转换、如何读检查结果、怎样迭代审查，再生成交付成果。插件的价值在于让工具按流程配合；输出的质量仍取决于输入图纸、坐标与控制点资料，以及操作者对检查结果的判断。

当前代码注册 **63 个 MCP 接口**，包含四个 CAD 包装接口；15 个旁路工具是包装器中的可选能力，两个数字不能相加作为独立 MCP 接口数量。已有核心流程覆盖图纸理解、DWG/DXF 转换、工程检查、审查与 QGIS 交付。可选 CAD 能力包补充图层/实体查询、二维编辑、拓扑检查与差异比较。公开注册状态以仓库的 `registries/publication-status.json` 为准；客户端可接入不等于已进入每个平台的官方目录。

## 可选 CAD 能力：15 个已经实际调用的工具

新增旁路采用 MIT 许可的 U-C4N/Autocad-MCP v1.6.0，固定源码修订，独立安装。主运行时与旁路分开，减少不同 MCP SDK 和依赖之间的冲突。当前适配器仅开放下列 15 个工具；上游的全部工具、AutoCAD COM 和 3D 能力没有被宣称已经接入。

| 类别 | 当前工具 | 用途 |
| --- | --- | --- |
| 理解和检查 | `drawing_understand`, `drawing_topology_check`, `drawing_diff` | 理解结构、检查连接、比较修订 |
| 筛选和查询 | `analysis_select_by_layer`, `analysis_select_by_type`, `entity_get`, `layer_list` | 按图层或实体类型定位对象并读取属性 |
| 图层管理 | `layer_create` | 建立修订用图层 |
| 二维创建 | `entity_create_line`, `entity_create_circle`, `entity_create_arc`, `entity_create_polyline`, `entity_create_rectangle`, `entity_create_text` | 创建基本二维图元和标注 |
| 副本清理 | `entity_delete` | 在修订副本中删除指定实体 |

CAD2GIS 暴露四个稳定包装工具：`get_cad_bundle_status`、`prepare_cad_bundle_workspace`、`call_cad_bundle_tool`、`list_cad_bundle_tools`。准备工作区后得到 workspace_id，通过工具清单读取实际允许的名称与参数 schema，再执行查询或编辑。调用结果中的 `edited_source` 指向修订 DXF；检查它并 bootstrap 新项目后，再进入 CAD2GIS 转换与检查。通过包装器不能任意执行上游工具，也不能越过项目根目录修改任意文件。

## 从一次修改到一次交付

1. 指定项目根目录，运行环境检查并读取能力清单。
2. 读取图纸、确定图层与需要修订的对象；保留原始输入。
3. 用 CAD 包装器打开副本，查询结构并执行二维修订。
4. 导出修订 DXF，将它送回 CAD2GIS 的 GIS 转换与检查流程。
5. 审查 CRS、图层、连接关系、警告和运行状态，处理仍需人工判断的部分。
6. 交付 GIS/QGIS 成果与来源证据；需要绝对精度保证时，补充权威控制点和独立检查点验收。

示例请求：“在这个项目内检查光纤线与端点的连接；保留原图，把修订 DXF 转为 GeoPackage，并列出检查状态、输入哈希和需要人工处理的问题。”

## 安装：当前 Agent 与历史桌面包分开

建议使用 Python 3.12 和 uv，通过当前发行版安装 Agent：

```sh
uv tool install --python 3.12 "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/releases/download/v0.8.0/cad2gis-0.8.0-py3-none-any.whl"
cad2gis-agent-mcp --help
cad2gis cad-bundle status
```

Codex 与 Claude Code 使用仓库自带 marketplace；Cursor、VS Code 使用仓库提供的 MCP 配置。项目路径是必须由用户选择的配置，不能把示例路径直接当作自己的项目。完整安装命令和客户端配置见 [README](https://github.com/Mola-maker/CAD2GIS#readme) 与 [插件说明](https://github.com/Mola-maker/CAD2GIS/tree/main/plugins/cad2gis-agent)。

可选 CAD 运行时需要单独安装：

```sh
cad2gis cad-bundle install --prefix /absolute/path/to/cad-runtime
```

根据安装结果配置 `CAD2GIS_CAD_BUNDLE_COMMAND` 为实际可执行文件路径，再启动 MCP 客户端。不同平台的可执行文件位置以安装输出为准。没有安装旁路时，核心 CAD→GIS 功能仍可使用。详细配置、固定修订和权限边界见 [CAD_BUNDLES.md](https://github.com/Mola-maker/CAD2GIS/blob/main/docs/CAD_BUNDLES.md)。历史 Windows/macOS/Linux 桌面包保留其原版本，未改名为 0.8.0。

## 验证证据与九图演示

0.8.0 核心与 Agent 改动在 Linux / Python 3.12 完整测试中得到 **1661 passed、19 skipped**。独立旁路的 15 个工具均在合成 DXF 上实际调用成功，验证了原图不变、修订导出和进入 GIS 转换流程。该测试范围不等同于对所有客户 DWG、AutoCAD COM 或 3D 对象的保证。

官网包中已有九张主图及多分区效果、首开截图、SVG 提取和地图演示，本次直接复用这些网页资源，不额外上传九图报告压缩包。查看 [九图和配准效果](https://mola-maker.github.io/CAD2GIS/docs/osm-gcp.html)、[SVG 画廊](https://mola-maker.github.io/CAD2GIS/docs/svg.html) 与 [地图预览](https://mola-maker.github.io/CAD2GIS/docs/map.html)。这些历史实例仍为 **CONDITIONAL**，`absolute_accuracy_verified=false`。OSM 对齐和可视化微调不能替代实测控制点或独立测绘验收。

## 许可、适用场景与参与方式

Apache-2.0 允许原创代码的商业使用、修改和再分发，并包含明确的专利许可条款。分发时遵守 LICENSE 与 NOTICE。第三方组件、字体和示例资料继续遵守各自许可；不能把原创代码许可误套给所有依赖和工程数据。MIT CAD 旁路保持独立，LibreDWG 等组件的许可说明仍保留。

适合把基础设施 CAD 图纸整理为 GIS 图层、为 QGIS 准备可审查交付、让智能体协助图层与二维图元处理的用户。需要法定测绘成果或独立精度保证的项目，应在本流程上补充相应的控制资料和人工验收。

通过 [Issues](https://github.com/Mola-maker/CAD2GIS/issues) 提供脱敏复现步骤、输入格式、环境版本、检查状态和预期行为；贡献流程见 [CONTRIBUTING](https://github.com/Mola-maker/CAD2GIS/blob/main/CONTRIBUTING.md)。欢迎分享公开演示与真实使用反馈，也欢迎对文档翻译、CAD 适配工具和检查规则提出改进。
