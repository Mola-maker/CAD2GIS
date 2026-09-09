# CAD2GIS 0.4.0 安装验证记录

记录日期：2026-09-09（北京时间）。本次交付是本地开发测试安装版，包含 Windows、
Ubuntu Linux 和 Android 安装包，以及原有智能体插件接口。

Windows 和 Linux 使用同一最终 Python wheel，SHA-256：
`97be91d54295c165a970460e080ab00ed9d88459a0141003f3ea0ed5c7bbf8cf`。
该 wheel 独立构建两次，文件哈希一致。安装包清单、大小和哈希见
`dist/installable-manifest.json` 与 `dist/SHA256SUMS`。

## 实际安装及运行

| 平台 | 实际环境 | 已验证结果 |
| --- | --- | --- |
| Windows | Windows 11 x64，含中文和空格的独立安装目录 | 安装、原生 CAD/GIS、MCP、完整转换、工作台、GCP 二次匹配、卸载均通过；项目和用户 PATH 保留 |
| 当前 Windows 用户 | `%LOCALAPPDATA%\Programs\CAD2GIS` | 最终安装包已安装并保留；实际桌面 EXE 启动、Edge 登录和页面关闭程序均通过，退出码为 0，无页面 JavaScript 错误 |
| Linux | WSL 中 Ubuntu 24.04.4 LTS x86_64 | 真正通过 dpkg 安装到 `/opt/cad2gis`，普通用户在 `/tmp` 使用安装包 Python 完成全部检查；9 个系统入口有效；卸载移除程序与入口，项目继续保留 |
| Android | Android 11 / API 30 x86_64 模拟器，WebView 83.0.4103.120 | 最终 APK 实际安装，设备中取回的 APK 哈希一致；连接真实 Windows 服务，HTTPS 登录、目录浏览、结果审查和实时同步通过 |

Linux 便携包与 Debian 包包含同一运行时。最终版完整验收以 Debian 实际安装为准。
较早的 `09b3…` 候选便携包另通过了中文和空格路径下的原生库/MCP 检查；该补充检查
不作为最终便携包独立完整验收的证明。当前 Linux 测试没有图形桌面，未观察默认
浏览器窗口自动打开。

## 核心业务验收

Windows 和 Linux 均使用自造 DWG 与固定 OSM 参考数据，通过安装版完整转换链路：

1. LibreDWG 实际读取、写出及来源导出成功，GDAL GeoPackage、PROJ/GEOS 与 SVG 提取正常。
2. OSM 匹配状态为 `matched`，SVG 提取为 `completed`；已知点得到预期坐标
   `(12600100, -799800)`，结果保留 OSM 相对配准来源。
3. 真实工作台完成认证、一次性启动凭证、DWG 上传校验、图纸检查任务、地图资源及 SVG 报告读取。
4. 人工 GCP 先预览，明确二次匹配后生成新的 GIS；已知点得到约
   `(12600110, -799805)`，与预期一致，原始 `delivery.gpkg` 的哈希保持不变。
5. 安装版 MCP 发现 46 个工具，并验证能力查询调用；越界的图纸检查调用被拒绝。

这些测试证明安装版引擎和数据流可用，不代表任意真实图纸均可自动获得正确语义或
测量级定位。新项目仍使用原有语义审核门禁；在线 OSM 查询和可选 AI 仍需相应网络及配置。

## Android 验收边界

Android 按用户确认的“连接服务器即可”范围交付，计算由服务端执行。

- 最终 APK 对最终 `97be…` 服务验证了 HTTPS 密码登录、服务器目录浏览、直接打开
  审查页、WebSocket 同步、OSM 已应用及相对参考状态；新日志中无未捕获 JavaScript 错误。
- CAD/GIS 地图、实际 HTTPS OSM 底图、GCP 按钮及真实 SVG 图形曾在同一 APK 对
  `09b3…` 候选服务的检查中显示正常。完整记录明确区分候选和最终服务版本。
- 无效证书被拒绝，旧页面清除后可重新连接；跨站导航被拒绝；同主机切换端口要求
  重新登录；带认证的原生文件下载校验通过。
- **手机上传未完成端到端验证**：系统文件选择器能打开并显示 DWG，但自动操作没有
  完成选择返回，服务端未收到上传。需要使用手机文件导入时，仍应在实际设备验收。
  当前可靠使用方式是将图纸放在授权服务器目录后从手机浏览。
- 未使用实体手机或 Android 8/API 26 设备；实际安装测试使用 API 30。GCP 数值预览
  和二次匹配由 Windows/Linux 服务验收，Android 本次仅检查相关界面。

## 回归与独立审查

最终代码回归：**862 passed、13 skipped、4 warnings**。跳过项分别为：6 个外部 APD
图纸语料测试、1 个需要指定 AutoCAD 属性回归图纸的测试、4 个缺少可选证据基线的
测试，以及 2 个当前 Windows 账户不能创建文件符号链接的测试。Windows 目录联接
的实际越界回归另已通过。4 个警告为依赖/标准库弃用警告。

新增应用、路径授权、会话隔离、原生 Unicode 路径与打包工具的相关检查通过；插件
清单验证、JavaScript 语法检查及 Ruff 检查通过。独立审查发现的问题已修复，包括
审查标签页写入目标绑定、路径逃逸、中文安装目录的 PROJ 数据定位，以及 WebView 83
兼容性。安装验收在独立运行时中执行，不依赖仓库开发虚拟环境。

## 交付文件与限制

`dist/verification/` 收录最终 Windows/Linux 安装、转换、MCP、工作台及卸载 JSON，
以及最终回归和 wheel 构建记录。Android 原始报告与截图位于 `dist/android/`。
报告中的绝对测试路径是当时运行位置，交付副本用于保留证据，不要求这些测试目录存在。

Windows 包面向 Windows 10/11 x64，实际验收系统是 Windows 11。Linux 当前明确支持
Ubuntu 24.04 x86_64 / glibc ≥ 2.39，其他发行版与 ARM 未验证。Android 声明 Android
8+、WebView 83+；APK 使用开发签名，Windows 安装器尚无 Authenticode 生产签名。

第三方许可和对应源码随交付保留。本项目当前专有许可声明与所用 GPL 组件的组合
公开分发方案尚待明确，因此本次不视为已完成生产签名、应用商店上架或公开分发许可审定。
详见 `packaging/THIRD_PARTY_NOTICES.md`（交付副本为 `dist/docs/THIRD_PARTY_NOTICES.md`）。

测试使用自造图纸；私人 `official/`、`raw/` 原始项目未加入安装包。安装版包含已有
派生演示数据。验收临时服务和 Android 模拟器已关闭；当前用户的 Windows 软件保留安装。
