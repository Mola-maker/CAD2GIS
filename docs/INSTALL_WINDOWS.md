# Windows 安装与使用

这是 Windows x64 的本地开发测试安装版，安装器位于 `dist/windows/`。
支持 Windows 10/11 x64，使用系统浏览器作为工作台。安装包自带 Python 3.12、
GIS 库和 LibreDWG，无需先安装 Python、uv、AutoCAD、Conda 或 QGIS。

## 安装

1. 退出正在运行的 CAD2GIS 本地服务，再运行 `CAD2GIS-0.7.1-windows-x64-setup.exe`。
2. 默认安装到当前用户的 `%LOCALAPPDATA%\Programs\CAD2GIS`，不需要管理员权限。
3. 可选择添加桌面快捷方式；“添加命令到用户 PATH”用于现有智能体插件。
4. 从开始菜单打开 CAD2GIS。程序启动本地服务，并用系统浏览器打开专用登录链接。

开发安装包尚无 Windows Authenticode 生产签名，不能将其描述为受信任发行商签名版。

## 使用工作台

默认项目目录是用户主目录下的 `CAD2GIS Projects`。可以从工作台导入电脑或手机
上的 DWG，也可以浏览已授权的服务器项目目录。DXF 请先在 CAD 软件中另存为 DWG；
安装版沿用现有 DWG 读取引擎。

底部「AI 配置」填写服务地址、模型和密钥，保存后测试模型与工具调用。
Windows 使用当前用户 DPAPI 加密保存密钥，重启后继续使用；转换任务与内置
智能体共享配置。软件不包含共享模型密钥。远程客户端由服务器本机管理 AI 配置。

底部五页为 AI 配置、转换对话框、OSM 工作台、SVG 审阅台和 SQLite 可视化中心。
左侧提供使用说明和集成架构文档。文件、会话、历史消息、任务和数据库结果支持分页。
转换对话接入既有 MCP 工具和工作台工具，支持计划、过程、停止与历史恢复。
“语义提取”执行源事实导出、候选准备、语义库初始化三个阶段，允许下载四类产物；
候选仍需审阅，不会自动提交为确定的语义分类。
已有通过审核的项目也可选择配置目录后直接转换；软件不会把空白草稿当作已审核配置。

新界面使用 molamaker 图标、蓝色背景、底栏与侧栏液态玻璃、阻尼动效与操作焦点。
系统开启减少动态效果时关闭非必要动画。

默认转换执行 OSM 相对匹配与 SVG 候选提取。第三步打开结果后，先核对已应用的
地图匹配；位置符合要求时，点击“匹配没问题，继续 SVG 审阅”，即可查看符号报告
和交付数据。人工 GCP 是可选项，可以随时展开或收起；已有控制点不会强制进入
人工校正预览。确认二次匹配才生成新的 GIS 结果，旧结果保留。OSM 配准不代表已获得
测量级绝对精度。地图瓦片、在线 OSM 查询和可选 AI 需要网络。

0.7.1 的地图页面使用专用审阅入口，不再先加载旧演示首页。地图和 SVG 页面可以
向下滚动；重复打开结果会重新加载并显示进度，SVG 缩略图与原始 SVG 均可查看。
二次匹配后会更新报告绑定，避免旧地图、旧报告与新交付数据混用。

任务进度区域显示真实输出与错误。“关闭程序”用于停止本次桌面服务；单纯关闭
浏览器标签页不会自动中断正在进行的转换任务。

## 保留智能体插件

安装目录内 `plugins/cad2gis-agent/` 包含原有技能、MCP 清单与客户端配置模板。
安装器提供稳定的 `bin/cad2gis-agent-mcp.exe`；选中 PATH 任务后，请重启智能体
客户端，使其读取更新后的用户 PATH。也可以在客户端命令字段填写该入口的完整路径。

`CAD2GIS_PROJECT_ROOTS` 应明确设为允许访问的项目目录，Windows 下多个目录以
分号分隔。安装器不会修改现有智能体配置或扩大目录授权范围。原有 CLI 入口与
Python API 仍可使用；QGIS 专用命令需要单独的 QGIS 运行环境。

## 向 Android 提供服务

远程连接必须由管理员提供有效 HTTPS 证书和服务密码。示例（替换路径和域名）：

```powershell
$env:CAD2GIS_APP_PASSWORD = '<至少12字符的服务密码>'
cad2gis-desktop --no-browser --host 0.0.0.0 --port 8765 `
  --public-host cad.example.com `
  --project-root 'D:\CAD Projects' `
  --ssl-certfile 'D:\Certificates\fullchain.pem' `
  --ssl-keyfile 'D:\Certificates\privkey.pem'
```

在 Android 应用填写相同 HTTPS 地址并登录。证书需要被设备信任，域名必须匹配；
客户端不会跳过 TLS 错误。应用不会自动开放系统防火墙或把本地服务暴露到公网。
MCP stdio 仍是本地独立入口，这个远程服务入口服务于工作台。

## 卸载与数据

从 Windows“已安装的应用”卸载 CAD2GIS，或运行安装目录的卸载器。卸载移除程序
文件、由安装器创建的快捷方式和由它添加的 PATH 项；项目和转换结果位于程序
目录外，继续保留。不要把业务图纸放进安装目录。

桌面启动日志保存在 `%LOCALAPPDATA%\CAD2GIS\logs`。运行库检查可执行：

```powershell
cad2gis doctor --deep --strict --profile full --json
```

## 从源码重建

1. 使用 Python 3.12 安装 `build`、`setuptools` 及测试工具。
2. 运行 `python tools/build_reproducible_wheel.py --output-dir dist/python --verify`。
3. 按 `packaging/patches/README.md` 准备并验收 LibreDWG 0.14 MTEXT 终止修补版。
4. 运行 `python tools/build_windows_package.py --wheel dist/python/cad2gis-0.7.1-py3-none-any.whl --libredwg <已验收原生工具目录>`。

已有完整 payload 时，先用 `tools/refresh_runtime_bundle.py` 更新指定 wheel，再运行
`python tools/build_windows_package.py --wheel <冻结wheel> --payload <临时payload> --reuse-payload`。
此模式复用 Python 与依赖，只压缩安装器，并核验 wheel、原生工具与 manifest 的哈希绑定。
原生工具替换后必须重新生成 manifest；构建器会拒绝未经重复图纸验收的旧 LibreDWG，
也会拒绝覆盖已有同名安装器。平台交付以 `tmp/RELEASE_INPUT_READY.json` 的路径及哈希为准。

构建工具下载到 `tmp/packaging-windows`，按脚本中固定 URL 和 SHA-256 校验。
可用 `--wheelhouse <目录>` 从已验证的本地 wheel 集合离线组装。
第三方许可、精确依赖版本与文件哈希随包附带；组件对应源码由
`tools/collect_distribution_sources.py` 收集到 `dist/third-party-sources`。
