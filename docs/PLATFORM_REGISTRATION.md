# 主流平台注册与验收

本地 stdio MCP 与官网静态演示分开部署。提交统一使用 `registries/listing-metadata.json` 和 0.8.0 MCPB；真实状态见 [publication-status.json](../registries/publication-status.json)。提交成功或审核中不等于 published。

- 官方 MCP Registry：`server.json` 使用 `io.github.Mola-maker/cad2gis-agent`、GitHub Release MCPB 与真实 SHA-256。发行后通过 GitHub Actions OIDC 认证 namespace，无需新增长期 token。公开 API 要同时读回名称、版本与包哈希。
- Smithery：按 [官方发布说明](https://smithery.ai/docs/build/publish) 发布 local MCPB；需要账号 namespace 和登录。只提交 stdio 包，不创建付费服务。
- Glama：按 [官方 FAQ](https://glama.ai/mcp/faq) 的 Add MCP Server 提交 GitHub 仓库、名称和简介；`glama.json` 按公开 schema 声明维护者。公开收录与 claim 所有权分开核验。
- Codex / Claude Code：仓库 marketplace 已提供本地安装。Claude 插件可运行 `claude plugin validate plugins/cad2gis-agent` 验证包；官方 marketplace 收录另需提交与审核。
- OpenAI 公共目录：[官方规则](https://developers.openai.com/plugins/build/mcp-server) 要求 MCP 插件有稳定公开 HTTPS 服务，并完成发布身份验证。本插件涉及本地项目文件和运行时，目前仅 stdio 接入；本次保留完整功能，不把静态官网伪装成 MCP endpoint。公共目录提交需要另行设计带认证、租户隔离和授权上传的远端服务。
- Cursor / VS Code：标准客户端配置已提供，属于 client_supported；不是公共目录收录。

首次启动 MCPB 需要 uv 和网络。安装的 wheel 与 Release 哈希固定；项目目录必须显式选择。DWG reader、GIS 与可选 CAD runtime 按 doctor 状态安装，不能以 MCP 握手成功替代转换 readiness。

官网缺少管理通道时，交付新版静态 ZIP 与部署说明，状态保持 requires_user_action；旧官网 HTTP 200 不表示新版已部署。
