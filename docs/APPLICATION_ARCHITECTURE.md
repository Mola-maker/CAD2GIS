# 可安装应用架构决策

状态：已实现，安装与实际验收结果见 [安装验证记录](INSTALLATION_VERIFICATION.md)。

0.5 Windows 工作台升级：Pydantic AI Core 负责模型工具循环，通过隔离 MCP
子进程复用完整转换工具目录；SQLite 保存会话、计划和过程，当前用户配置为标准
转换与对话提供同一模型配置。Framework7 iOS 组件提供分页与弹窗，项目样式实现
蓝色液态玻璃、阻尼动效和视觉聚焦。详见随软件内置的
[工作台架构](../src/cad2gis/desktop/docs/architecture.md)、
[框架来源](../src/cad2gis/desktop/docs/sources.md) 和
[Figma / 图像 / 动效记录](design/ASSETS_AND_MOTION.md)。

用户范围：Windows、Linux 安装完整应用，Android 安装可连接服务器的客户端；
保留 Codex 等智能体使用的插件接口。Android 不要求离线独立转换 DWG。

## 选择：单一转换核心，薄入口，按平台封装

```text
Windows / Linux 桌面入口 ── 本地浏览器 ─┐
Android 应用 ── HTTPS + 登录 ──────────┴─ 应用服务 ─┬─ 原有 CLI ─┐
                                                 └─ run 审查 ─┤
Codex / Claude / Cursor ── MCP stdio ──────────────────────────┤
                                                             ▼
                            同一核心：DWG → 语义门禁 → OSM → GIS + SVG
                                                    → GCP 预览 / 二次匹配
```

转换核心仍是 `src/cad2gis`。应用服务只处理项目目录、用户登录、任务生命周期和
审查入口，通过已有 CLI 调用转换，不复制分类、配准、拓扑、SVG 或发布逻辑。
MCP 继续独立运行并采用原有项目根目录授权规则，不将应用服务的登录等同于
智能体的目录授权。

桌面启动器使用系统浏览器承载已有 HTML/JavaScript 工作台；Windows 启动器是
小型本机进程入口，Linux 使用可重定位的脚本入口。两端安装包都携带独立 Python、
平台 wheel 与 LibreDWG。应用不依赖开发仓库、系统 Python、Conda、AutoCAD 或 QGIS。
QGIS 专用导出/校验工具继续是可选集成，不打入整套 QGIS 桌面。

Android 是原生 Java Activity 和系统 WebView，不包含桌面 GIS 二进制。它连接
指定服务器的同一工作台，服务器负责读取项目和执行转换。连接模式由用户明确接受。
登录、审查和控制点交互共用浏览器实现，因此手机与桌面不会维护两套配准算法。
移动界面以系统 WebView 83 或更新版本为兼容下限，旧版本提示用户先更新。

## 与替代方案比较

| 方案 | 对本项目的影响 | 决策 |
| --- | --- | --- |
| 独立 Python + 系统浏览器 + Android WebView | 复用现有 FastAPI/HTML/CLI/MCP；原生 GIS 文件可以保留 wheel 原布局 | 采用 |
| Tauri 2 + Python sidecar | 能嵌入 Python 服务，但新增 Rust、桌面 WebView 构建链；Android 仍需另处理转换服务 | 可作为未来桌面窗口体验升级，不作为首次安装版必需依赖 |
| PyInstaller 打包整个引擎 | 提供独立可执行文件，但必须维护动态加载的 GDAL、PROJ、pyramids、数据目录和隐藏导入规则 | 当前选择保留标准 Python 包布局，降低原生库遗漏风险 |
| Qt/PySide 跨端 | 能生成 Android 应用，但 Android Python/native wheels 仍需适配，且会新增 GUI 运行库 | 当前界面已是 Web，收益不足以抵消迁移与体积成本 |

Tauri 官方明确支持将 Python 服务作为外部二进制，并要求每个目标架构提供自己的
sidecar；这不会自动使桌面 GIS 运行库变成 Android 库。
来源：[Tauri 外部二进制](https://v2.tauri.app/develop/sidecar/)。
PyInstaller 的构建结果与构建操作系统相关，不能把 Windows 构建直接当作 Linux
应用。来源：[PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)。
PySide 的 Android 工具需要 Android wheels/SDK/NDK，当前要求 Unix 构建宿主。
来源：[Qt for Python Android 部署](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-android-deploy.html)。
独立 Python 采用 uv 同源的 python-build-standalone 分发，工具包按版本与 SHA-256
固定。来源：[uv Python 分发说明](https://docs.astral.sh/uv/concepts/python-versions/)。

## 进程与数据边界

- 应用默认只监听回环地址。桌面浏览器通过启动时的随机凭证进入自己的服务会话。
- 对外连接必须显式启用 TLS，并设置服务密码；HTTP 请求和 WebSocket 都受会话保护。
- 浏览、转换和打开 run 只限显式授权项目根目录，解析实际路径后拒绝越界与符号链接逃逸。
- 长任务通过独立 CLI 子进程执行；保留真实输出与退出状态，已有 run 不被失败任务覆盖。
- 已完成 run 的审查继续使用现有 `/api/*`、`/ws`、OSM/GCP/SVG 契约。
- 每个审查页面绑定打开时的结果标识；另一个标签切换项目后，旧页面停止写入并提示重开。
- 安装目录只保存程序；项目、控制点、日志和输出放在用户选择的数据目录。
- 插件随包附带，但不会自动扩展 MCP 项目目录权限，也不会修改用户已有智能体配置。

## 打包与发布边界

Windows 使用 Inno Setup 生成用户级安装程序，支持卸载和可选 PATH/桌面快捷方式。
Linux 提供 Debian 包与便携包，按实际构建的 x86-64 平台标明支持范围。
Android 提供经签名的测试 APK；生产发布密钥与商店发布是独立的发布输入。

依赖 wheel 安装报告、版本/哈希锁、组件许可清单与文件哈希随桌面包提供。
OpenLayers 10.6.1 渲染资源本地化，因此工作台加载不依赖 CDN；OSM 道路查询、地图
瓦片和可选 AI 接入仍需要网络，离线 OSM 快照仍走现有参数。
为 WebView 83 做了两处等价语法/API 改写，由固定上游哈希的 vendor 脚本重复生成，
修改说明随原 OpenLayers 许可证保留。工作台导入仅接受现有引擎支持的 DWG；
0.8.0 的 CLI/API/MCP 增加原生 DXF 接入，复用现有 DXF 实体解析；桌面文件选择入口仍按其支持范围使用。

原创源码采用 Apache-2.0，允许商用与二次开发；具体范围见
根目录 LICENSE.md 和 NOTICE.md。pyramids-gis 和 LibreDWG 包含 GPL 组件，
继续保留组件原始许可和上游源码，公开再分发前需明确组合许可方案。
不能将当前依赖组合描述为“全部宽松开源”或“已完成商业分发合规”。

## 验收要求

1. Windows 实际安装到独立目录，从该目录执行 doctor、MCP、转换/审查，再验证卸载。
2. Linux 在真实 Linux 用户空间构建并安装/解包运行；检查原生动态库与桌面入口。
3. Android APK 验证签名和 manifest，并在可用模拟器中安装、启动和连接 HTTPS 服务。
4. 安装版启动不使用仓库 `src`、开发虚拟环境或临时配置。
5. 既有 OSM、人工预览/二次匹配与 SVG 功能保留，失败状态可见且不伪造交付成功。
6. 未授权路径、跨源写入、未登录请求及未登录 WebSocket 被拒绝。

最终交付文档必须逐项说明实际通过的验证和剩余限制，不以安装包文件存在代替运行验证。
