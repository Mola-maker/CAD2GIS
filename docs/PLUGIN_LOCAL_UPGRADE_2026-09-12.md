# 本机 CAD2GIS 插件升级验证

个人 `cad2gis-agent` 插件已通过 Codex 正式重装命令升级为 `0.7.0+codex.20260912070859`。仓库中的 Codex/Claude 插件合同版本均保持 `0.7.0`；时间后缀只用于个人安装的缓存刷新。

## 实际安装与保留内容

- 个人插件源：`C:/Users/22494/plugins/cad2gis-agent`。
- 正式安装缓存：`C:/Users/22494/.codex/plugins/cache/personal/cad2gis-agent/0.7.0+codex.20260912070859`。
- 独立 Python：`E:/branch_CAD2GIS/.venv-plugin070/Scripts/python.exe`，使用 `-I -m cad2gis.agent_mcp --transport stdio` 启动。
- 补丁读取器：`E:/branch_CAD2GIS/runtime/libredwg-0.14-cquote-966125d62564/dwg2dxf.exe`。
- 旧插件完整备份：工作树 `tmp/plugin070-backup/cad2gis-agent`。原有工程访问范围、缓存路径、其他环境配置及附加文件保留；旧 Python 和原生运行时未覆盖。

未手动修改插件缓存或 marketplace 配置。已对本次 18 个插件文件在暂存、个人源与正式缓存之间逐字节核对。`codex plugin list` 的 marketplace 快照仍显示旧版本元数据；正式 `plugin add` 返回的安装目录及实际清单为上述新版本，二者在证据中分别记录。正在运行的会话保留已有工具，**新建会话后加载新插件和 MCP**。

## 安装后验证

安装了 Windows 锁文件中的 73 项依赖和最终 CAD2GIS wheel；74 个发行包通过依赖一致性检查。wheel SHA256 为 `e5d99d655a3e1824857d7dc583c0a8dd667e0d0b76abb9e6837c8db4c79128fb`，179 个已安装源码文件与冻结清单一致。

独立安装环境的 `doctor --deep --strict --profile full --json` 退出码为 0，状态 `ready`。GDAL 运行时加载、resvg 实际 PNG 渲染和 MCP stdio 握手通过；50 个工具可用，语义准备、预览、提交和编译工具存在，越过工程范围的读取被拒绝。这里不把“工具可列出”当作所有真实图纸的转换验收；真实流程由图纸报告和平台安装报告分别证明。

切换读取器前，完全相同的新插件环境对旧 EXE/DLL 组合报告 `limited`，strict doctor 退出码 2；切换到补丁后变为 `ready`。补丁的可信证据来自构建记录以及 Darat/Manado 各五次独立进程读取对照，而非仅凭不同二进制哈希。两个工程全部 49/330 条 MTEXT 与 dwgread JSON 对照通过，源文件未变。读取器通用诊断对未登记构建仍标为 `not_evaluated`，不冒充普遍兼容性认证。

## 本地证据

- `tmp/plugin070-installation-receipt.json`：源与缓存文件哈希、配置保留、实际安装路径。
- `tmp/plugin070-verification/result.json`：旧读取器组合的缺陷诊断。
- `tmp/plugin070-verification-patched/result.json`、`doctor.json`：补丁读取器的安装后验证。
- 独立原生目录内的 `build-receipt.json`、`acceptance.json`：原生编译、补丁、二进制哈希及真实文本验收。

本机绝对路径仅描述这次安装。可分发插件 ZIP 使用仓库通用命令和环境变量模板，需要在目标机器安装对应运行时。
