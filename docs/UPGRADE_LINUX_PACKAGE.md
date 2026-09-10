# Linux 0.6.0 最终发布包验证

2026-09-09；在 Linux x86_64、glibc 2.39 环境完成修复后重新构建。最终 `.deb` 和便携归档各 **15,500 个 manifest 文件全部通过 SHA256 校验**；两种提取环境的严格 doctor 均为 `ready`。

## 最终文件绑定

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| `dist/release-final/cad2gis-0.6.0-py3-none-any.whl` | 9376162 | `9478ad61d0d975a656c2aeb784af0ea7c6d4f0475db787300d0ab901dff4cef8` |
| `dist/linux-final/cad2gis_0.6.0_amd64.deb` | 388327026 | `571dde5bf930792befccbe88182a283a5e0e62a2930cb478ca1f7bed8fce6fdf` |
| `dist/linux-final/cad2gis-0.6.0-linux-x86_64.tar.gz` | 388325167 | `941ad3b42d3faa25a0be8fce278a4a4a50772d9a3c71982789a84e43160fdcb0` |

wheel 在两个独立临时源码目录构建，`SOURCE_DATE_EPOCH=1788972299`；两次 SHA256 相同。两份 Linux 归档 hash 同时符合 `dist/linux-final/SHA256SUMS` 和 `linux-build.json`。本次可复现结论适用于 wheel。

两包共同的 payload manifest SHA256 为 `e20cb4fdfccfa48975fd73b330a19a68e854d0760fcf52c4a1e1a1cab7e2f4e1`。包内 **171 个应用文件**与 wheel 和冻结源码逐字节一致；其中 `visual_audit.py` 修复 SHA256 为 `ab3bb04115c31f20e77ff6c3f8040af8642a521d7e847e078663aa9b925bacc0`。manifest 保留构建时修订 `9df68cb04a21fba03c2f064458f1f43b3c561dd0`、`source_tree_dirty=true`；应用源码绑定以逐文件 SHA256 记录为准。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| Python、LibreDWG、依赖锁 | 沿用已验证输入；6 个 native 文件 hash 不变；73 项已安装分发包名称和版本与前轮一致；锁文件字节一致，`pip check` 通过 |
| 包内实际 native smoke | 合成 DXF 编码成 DWG 后，经 native reader 导出源 GeoPackage；GDAL、PROJ、GEOS、SVG 均通过 |
| MCP stdio | 初始化与 46 个工具发现通过；能力查询成功；超出明确项目根目录的访问被拒绝 |
| 每份归档的完整性 | 15,500 个文件逐一计算 SHA256；0 mismatch，0 非预期文件（便携包另含安装说明） |
| Launcher / native 权限 | 两包各 9 个 launcher 均 `0755`；包内 Python、`dwg2dxf`、`dwgread` 可执行 |
| `.deb` 结构 | 版本 `0.6.0`、架构 `amd64`；`/opt/cad2gis` payload、9 个 `/usr/bin` 链接、desktop entry、SVG icon、安装说明齐全 |
| 便携归档结构 | 单一 `cad2gis-0.6.0` 顶层目录，16,812 个条目；未发现绝对成员路径、`..` 路径、device/FIFO 条目 |
| 两份提取包的命令 | `cad2gis --version` 均为 `cad2gis 0.6.0`；`doctor --deep --strict --profile full --json` 均为 `ready`，全部 profile 为 `true`；desktop/MCP `--help` 通过 |
| 两份提取包的隔离导入 | 从 `/tmp` 运行包内 Python，清除外部 Python/backend 覆盖；171 个应用文件各自与 wheel/checkout 匹配 |

目标环境为 **Ubuntu 24.04 x86_64、glibc 2.39 或更新版本**。`.deb` 声明依赖 `libc6 >= 2.39`、`libstdc++6`、`libgomp1`、`ca-certificates`、`fonts-dejavu-core`，推荐 `xdg-utils`。

## 证据与历史

最终原始日志与 JSON 记录位于 `/workspace/scratch/7489590ca040/verification/linux-release-verified-final/`，包括 `release-validation-final.json`、双构建日志、输入与安装绑定、native smoke、双归档全部文件校验、提取命令 stdout/stderr；最终安装包位于 `dist/linux-final/`。

| 前轮记录 | 保留位置 |
| --- | --- |
| 最早安装包独立检查，旧 `.deb` hash 前缀 `f3266481` | `/workspace/scratch/7489590ca040/verification/linux-package-audit-f3266481/`，含原报告 `UPGRADE_LINUX_PACKAGE_PRE_FINAL.md` |
| 视觉审计数值修复前的候选包，旧 wheel hash 前缀 `a466f340` | `/workspace/scratch/7489590ca040/verification/linux-release-provisional-before-audit-fix/`；候选归档为 `dist/linux-provisional-before-audit-fix/` |

本轮执行归档提取和包内运行，未进行系统安装、卸载或推送；远端 CI 和其他操作系统安装不在这些记录的验证范围内。归档提取命令的语义见 [Debian 官方 dpkg-deb 文档](https://manpages.debian.org/trixie/dpkg/dpkg-deb.1.en.html)。
