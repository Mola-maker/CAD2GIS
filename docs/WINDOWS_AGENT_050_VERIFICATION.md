# Windows 0.5 智能体与界面验收

日期：2026-09-09。当前用户安装位置：`C:\Users\DangHe\AppData\Local\Programs\CAD2GIS`。

## 交付

- `dist/windows/CAD2GIS-0.5.0-windows-x64-setup.exe`，141,142,561 字节。
- SHA-256：`a576f46fd365a3577a880e50b043cc079a383b8b95d3fcb4fda2e208452929ce`。
- Python wheel 和插件 ZIP 为 0.5.0，详见 `dist/windows-agent-0.5.0-manifest.json`。
- 安装器退出码 0，日志确认安装成功且无需重启。升级前关闭了已检查无活动转换子进程的旧服务。项目目录与用户配置位于安装目录外。
- Linux / Android 原有 0.4 安装包保留，本次未重新构建它们。Linux 新依赖哈希已补入锁文件，尚未执行 0.5 Linux 安装验收。

## 已验证行为

1. Windows DPAPI 保存和重新读取测试密钥；接口返回不含明文密钥，留空保留、明确清除生效。
2. 真实 Pydantic AI SDK 经 OpenAI 兼容 HTTP 调用：测试工具往返、流式回复、现有 MCP 子进程与 get_capabilities 调用。
3. 加载 46 个 CAD2GIS 工具，加上 5 个工作台工具。错误参数以 ModelRetry 返回模型，随后可继续创建 JSON 证据。
4. 任务取消后保留已写文件及包含请求、上下文和工具事件的中断记录；SQLite 会话分页和项目范围隔离。
5. SQLite / GeoPackage 只读查询、数据分页、禁止写入和扩展载入；105 行测试表第三页为 5 行。
6. 真实浏览器五页切换、帮助弹窗，未出现页面脚本异常。底栏和侧栏玻璃、按钮阻尼、焦点提示及减少动态效果样式已接入。
7. 独立打包运行时执行新生成 DWG：OSM matched、SVG completed；匹配后坐标为 (12600100, -799800)，等于预期值。
8. GCP 预览不改交付文件；二次匹配生成新结果，匹配后点为 (12600110, -799805)，原始交付哈希不变。
9. 实际安装目录再次完成 AI 工具测试和流式任务：4 次 HTTP 请求、1 次实际项目工具调用、数据库第三页 5 行、正常退出。

## 回归与审查

完整回归首轮为 867 passed、13 skipped、1 failed。唯一失败为 0.5 软件与 0.4 插件合同版本漂移；同步软件合同和两个插件清单后，该 MCP 集成测试及相关提供者测试共 8 项全部通过。未再次运行完整套件，不能将首轮报告改称一次性全绿。

另有 40 项应用/智能体/提供者针对性测试通过（1 项 Windows 符号链接条件跳过）；后续 15 项智能体与审查页面测试通过；最终 6 项智能体测试通过。跳过项主要依赖外部语料、指定 DWG 或系统符号链接权限。

独立只读审查提出中断历史和工具错误恢复两项问题，修复后复审无遗留重要问题。安装包测试另外发现完成状态早于 MCP 退出，已修正并验证正常退出。升级验收发现旧版 dist-info 残留造成身份误报，安装器现显式清理 cad2gis-0.4.0.dist-info，验收脚本新增软件/插件身份一致性断言。

## 证据与范围

- `tmp/smoke-agent-050-final/result.json`：独立运行时 AI 验证。
- `tmp/smoke-agent-050-installed-verified/result.json`：实际安装目录 AI 验证。
- `tmp/synthetic-050-final/conversion-smoke.json`：新生成图纸转换。
- `tmp/smoke-review-050-clean.log`：GCP 预览与二次匹配。
- `tmp/install-050-metadata-final.log`：安装成功日志。
- `tmp/pytest-050-full.log`：完整首轮回归原始记录。
- `docs/design/windows-050-preview.png`：开发版实际浏览器界面截图。
- [Figma 五页设计](https://www.figma.com/design/gjcYZ4DDk11piCtxr3XE3y)。

AI 测试使用本地协议模拟服务，不使用真实收费模型账号或用户密钥。因此已验证安装版集成链路，未声称用户的外部模型账号、额度或网络已经验证。安装包不附带模型密钥，首次使用应在 AI 配置保存自己的服务并执行工具连接测试。

Windows 安装包仍为未签名开发测试版本；安装包通过独立交付目录提供，不纳入 Git 源码提交。

最终升级复测：Installed CAD2GIS 0.5.0；软件/插件 identity.status = ok、differences = []；安装目录仅保留 cad2gis-0.5.0.dist-info。模型工具探测、流式实际工具调用及正常退出再次通过。安装后已重新启动桌面工作台。
