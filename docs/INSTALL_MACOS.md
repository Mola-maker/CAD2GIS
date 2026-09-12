# CAD2GIS macOS 安装版

从 GitHub Releases 下载与本机处理器对应的安装包：

- Apple Silicon（M 系列）：`CAD2GIS-0.7.0-macos-arm64.pkg`。
- Intel：`CAD2GIS-0.7.0-macos-x86_64.pkg`。

安装包自带 Python、GDAL/PROJ/GEOS、LibreDWG 和智能体插件，无需先装
Python、Homebrew 或 AutoCAD。两个架构均在 macOS 15 原生 runner 上完成
构建、实际安装和转换检查；更早的系统版本未在本次发布中验证。

安装后从“应用程序”打开 CAD2GIS，默认浏览器会显示本地工作台。
应用固定安装在 `/Applications/CAD2GIS.app`，命令安装在 `/usr/local/bin`。
可以在终端运行 `/usr/local/bin/cad2gis --version` 或
`/usr/local/bin/cad2gis doctor --deep --strict --profile full --json` 检查安装。
命令链接使用固定应用路径，请保留应用在该位置。

0.7.0 是测试发行版，PKG 未使用 Developer ID 签名，也未公证。
macOS 可能要求在“系统设置 → 隐私与安全性”中允许打开。
下载后可用 `shasum -a 256 文件名.pkg` 对照 Release 的 `SHA256SUMS`。

OSM 在线查询、底图和可选 AI 服务仍需要网络及相应配置。桌面安装包
不包含用户的原始 DWG、密钥或项目目录。Android APK 是服务端工作台客户端。

## 发布构建

仓库的 `Build macOS release installers` 工作流需要一个已有 Git 标签、
一个未发布的 Release 草稿及其数字 ID。草稿需先附上同版本的冻结 wheel。
触发工作流时传入 `release_tag` 和 `release_id`；草稿附件按 ID 访问，
因为按标签查询的 API 可能无法找到尚未发布的草稿。

工作流在原生 ARM64 和 x86_64 runner 上分别构建 LibreDWG、组装独立
运行时和 PKG，实际安装并测试命令链接、MCP、原生读取和合成转换后
上传草稿。两个作业通过且所有平台附件和校验表齐备后，再发布 Release。
工作流不会自动将草稿公开。

包内保留第三方许可证、原生组件源码和补丁、依赖锁和构建清单；
Release 另外提供对应第三方源码归档和完整的 CAD2GIS 源码快照。
