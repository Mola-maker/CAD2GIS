# CAD2GIS Linux 安装版

此安装包面向 **Ubuntu 24.04 x86_64**。它包含独立 Python 3.12、CAD2GIS、GDAL/PROJ/GEOS、LibreDWG 以及智能体插件。无需预装 Python、Conda 或 AutoCAD。其他发行版尚未验证；本地原生依赖按 glibc 2.39 或更新版本声明，不能将它当作任意 Linux 发行版通用包。

## 安装与打开

下载 `.deb` 和同目录的 `SHA256SUMS` 后，在下载目录运行：

```sh
sha256sum --check SHA256SUMS --ignore-missing
sudo apt install ./cad2gis_0.4.0_amd64.deb
```

在应用菜单打开 **CAD2GIS**，默认浏览器会显示本地工作台。也可以运行 `cad2gis-desktop`。工作台与智能体调用共用同一转换引擎，支持既有 OSM 匹配、人工 GCP 预览与二次匹配，以及 SVG 候选提取。需要联网的 OSM 搜索、地图底图或 AI 服务仍需网络和相应配置。

安装目录是 `/opt/cad2gis`，可执行入口安装到 `/usr/bin`。源图纸、项目目录和运行结果由用户选择，安装程序不会把它们复制进安装包。升级或卸载前先在工作台结束服务并关闭使用此运行时的智能体进程。卸载软件使用 `sudo apt remove cad2gis`；卸载程序不删除项目或用户设置。

## 无管理员权限的运行方式

将 `cad2gis-0.4.0-linux-x86_64.tar.gz` 解压到自己拥有的目录，运行解压目录里的 `bin/cad2gis-desktop`。该运行时可以移动目录，不依赖构建机的 Python 路径。便携包要求系统已提供 `libstdc++6`、`libgomp1`、CA 证书与可用字体；桌面打开功能需要默认浏览器。可用 `bin/cad2gis doctor --deep --strict --profile full --json` 检查运行时。

## 智能体插件接口

插件文件位于 `/opt/cad2gis/plugins/cad2gis-agent`，MCP 入口为 `cad2gis-agent-mcp --transport stdio`。便携运行时应使用其 `bin/cad2gis-agent-mcp` 的绝对路径。为智能体设置明确的项目目录，例如：

```sh
export CAD2GIS_PROJECT_ROOTS="$HOME/CAD-projects"
cad2gis-agent-mcp --transport stdio
```

多个 Linux 项目根目录用冒号分隔。安装目录和插件缓存不应作为图纸工作目录。安装软件不会自动修改已安装智能体的配置或 API 密钥。

Android 安装版连接 HTTPS 服务器上的工作台；转换由服务器或桌面计算机执行。不要将原有未认证的本地审查或 MCP 服务直接公开到网络。

## 从代码构建

在具备 C 编译器和 binutils 的 Ubuntu 24.04 x86_64 上运行。准备步骤使用官方校验值固定的 Python 独立发行包和 LibreDWG 0.14 源码；本机构建目录限定在仓库 `tmp` 下。若缺 `make`，准备脚本会从 Ubuntu 签名软件索引下载固定版本并仅解包到临时目录，不安装系统软件。Python 的可选 `share/terminfo` 终端仿真数据库因 WSL 大小写别名冲突而排除；CAD2GIS 无需该数据库。

```sh
python3 packaging/linux/prepare_runtime.py
python3 tools/build_runtime_bundle.py \
  --runtime-source tmp/packaging-linux/standalone/python \
  --wheel dist/python/cad2gis-0.4.0-py3-none-any.whl \
  --libredwg tmp/packaging-linux/libredwg-runtime \
  --output tmp/packaging-linux/payload
python3 tools/build_linux_package.py \
  --payload tmp/packaging-linux/payload --output dist/linux
```

运行时保留包内第三方许可证、LibreDWG 对应版本源码和构建收据。`dependency-install.json`、`requirements.lock`、`THIRD_PARTY_COMPONENTS.json` 和 `bundle-manifest.json` 记录依赖版本、下载哈希和文件哈希。当前项目声明专有许可证，`pyramids-gis` 与 LibreDWG 包含 GPLv3 组件；本地产物用于安装验证，公开分发前须完成 `THIRD_PARTY_NOTICES.md` 说明的许可证决策。

构建程序生成 `.deb`、便携 `.tar.gz`、`SHA256SUMS` 和 `linux-build.json`。本地验证应从新目录解包，清除 `PYTHONPATH`/`PYTHONHOME` 后运行实际入口、GDAL/LibreDWG 检查、MCP 握手和工作台健康检查。

安装器验收须真正运行 `dpkg --install`，不能只用归档解包代替：Debian 数据归档使用 GNU tar 并显式列出父目录，以满足实际安装器规则。安装后可用安装目录自己的 Python 运行完整验证：

```sh
/opt/cad2gis/runtime/bin/python3 -I -B packaging/linux/verify_install.py \
  --bundle /opt/cad2gis --output tmp/linux-installed-verification
```

该验证只生成自造图纸，覆盖原生 CAD/GIS、MCP、完整 OSM+SVG 转换、本地工作台入口、上传、SVG 审查、GCP 预览和实际二次匹配；原交付文件的哈希必须保持不变。无图形桌面的 WSL 仅验证服务与页面响应，默认浏览器窗口的自动打开需在实际 Linux 桌面中检查。
