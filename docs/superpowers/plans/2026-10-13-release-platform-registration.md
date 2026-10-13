# C：发行、主流平台注册与部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 A/B 的可用代码变成可安装产物，部署网站并完成主流平台注册或取得具体待审核状态。
**Architecture:** 一份有哈希的发行清单驱动 GitHub Release、MCPB 与平台提交；分别记录仓库 marketplace、客户端配置和公共目录状态。
**Tech Stack:** Python build、GitHub Release/Actions、MCPB、官方 MCP Publisher、平台 API/浏览器。
**Spec:** [设计 §7/8](../specs/2026-10-13-cad-bundles-exposure-design.md)。

## Global Constraints

- 继承索引全部约束；仅消费 A/B 已验证的 0.8.0。
- 登记名 io.github.Mola-maker/cad2gis-agent；账号实际 namespace 不匹配则停止该登记并记录原因，不能借用他人 namespace。
- MCP Registry 优先 MCPB/GitHub Release；PyPI 当前没有 cad2gis，不以假定已发布的 PyPI 包作为依赖。
- 新 MCPB 只含插件/运行入口与新 wheel/必要文档，不含九图数据、源图和秘密。
- 平台状态仅使用 prepared、submitted、under_review、published、requires_user_action、failed；published 要求公开 URL 读回。
- OpenAI/Anthropic 官方审核或身份验证不能伪造；缺账号时完成提交包再请求具体登录协助。
- 不新增公开无认证 HTTP 文件服务来迎合平台注册；本地 stdio 与静态官网是不同服务。

## Review Focus

- MCPB 能解压但不能启动：Task 1 在干净安装环境实际握手。
- 新 wheel 被错包装进旧版本 ZIP：Task 1 manifest/METADATA 检查。
- 平台提交返回 202/审核中被误算 published：Task 2。
- 后续目录显示旧工具列表：Task 2 tools/list/实际公开描述比较。
- Pages 部署成功但官网没更新：Task 3 分开检测，不合并状态。

---

执行顺序：Task 1 打包 → Task 2 准备提交包 → Task 3 全分支验证/独立审查/远端源码与站点 → Task 2 实际注册和发布 → Task 3 最终读回与交付。注册的公开发行始终晚于审查。

### Task 1：客户端接入、MCPB 与可复现发行包

**Files:** Create packaging/mcpb/manifest.json、packaging/mcpb/server/entrypoint.py、tools/build_agent_delivery.py、tests/test_agent_delivery_integrity.py；Modify plugins/cad2gis-agent/clients/README.md；校验现有 Cursor、VS Code、Claude Code、Codex 模板。

**Interfaces:**
- build_agent_delivery(destination: Path, *, wheel: Path, sdist: Path, site: Path) -> dict[str, Any]，拒绝覆盖非本工具拥有的目录。
- MCPB manifest_version=0.3、name=cad2gis-agent、version=0.8.0、server.type=python、server.entry_point=server/entrypoint.py。
- 入口最终调用 cad2gis.agent_mcp.main(["--transport","stdio"])。
- mcp_config.command=uv，args 使用 run、--python、3.12、--with、包内 wheel 的 [agent] extra 与 entrypoint；首次启动依赖 uv/网络，文档明确说明。
- user_config.project_root 为必填 directory，传入 CAD2GIS_PROJECT_ROOT；不默认整个 HOME。
- 工具配置由实际 MCP_TOOL_NAMES 导出，核心来源仍是已构建 wheel。

- [ ] 写失败测试：test_delivery_versions_and_licenses_match、test_mcpb_contains_no_project_data_or_secrets、test_mcpb_launches_installed_wheel；检查 ZIP 内容与实际握手，不逐字比较 README。
- [ ] 运行新增测试确认构建器/产物尚未存在。
- [ ] 使用当前官方 MCPB schema 验证模板，构建器将新 wheel 拷入 server/，计算哈希；入口 stdout 只写 MCP，说明首次安装和 runtime doctor 步骤。
- [ ] 生成 cad2gis-agent-0.8.0-plugin.zip、cad2gis-agent-0.8.0.mcpb、cad2gis-0.8.0-agent-delivery.zip、cad2gis-0.8.0-pages.zip、release-manifest.json 与 SHA256SUMS。
- [ ] 独立测试环境启动 MCPB，实际 get_capabilities/readiness/tools/list；四种客户端模板分别 schema 检查，CLI 可用时用实际 validate 命令验证。
- [ ] 提交打包实现与 metadata；将最终生成产物放相邻 deliverables，不将构建缓存和大二进制提交源码库。

### Task 2：主流平台公开注册与可发现性

**Files:** Create server.json、glama.json、registries/listing-metadata.json、registries/publication-status.json、docs/PLATFORM_REGISTRATION.md；Modify plugin manifest 的官网、支持、图标与准确简介；如 portal 需要，Create 官网 support/privacy/terms 页面及独立公共提交 ZIP。

**Interfaces:** publication-status.json 每项含 platform、status、url、checked_at、version、evidence、required_user_action；不存 session token/密钥。

- [ ] 按提交当天官方文档再次确认 schema/账号 namespace，查询已存在条目，避免重复创建。
- [ ] 写 registry schema/package 哈希验证和状态转换测试：只有公开读回可将 submitted 升为 published；缺登录返回 requires_user_action。
- [ ] 确认 A/B 验证和最终审查已通过后，建立 0.8.0 GitHub Release，上传新版源码/wheel/插件/MCPB 与哈希。保留旧 Release，不复传九图报告。
- [ ] 官方 MCP Registry：server.json 使用 registryType=mcpb、GitHub Release 真实 artifact URL 与 fileSha256；使用 mcp-publisher validate、GitHub namespace 登录后 publish，再读回 v0.1/servers 的该名称/版本。
- [ ] Smithery：复用授权账号 namespace，通过当前 stdio MCPB 发布流程提交同一产物；读回发布状态和公开 listing。没有登录时完成文案、文件、URL 与具体待登录操作，不开新付费托管。
- [ ] Glama：GitHub 源码 URL、名称和简介提交，glama.json 使用当前公开 schema；保存公开 listing 和收录检查结果。Claim 与收录分开记录，不自动授权额外 GitHub App 权限。
- [ ] Codex/Claude Code：验证本仓库 marketplace 在公开更新后可安装；更新准确安装文档。用 OpenAI 官方文档核验公共 Plugins Directory 的 local stdio 适用性，支持时提交完整实际能力包；不支持时记录限制并保留已可用 repo marketplace。Anthropic 官方 marketplace 使用官方提交入口，记录 submitted/under_review，等待审核。
- [ ] Cursor/VS Code：确认配置和项目根设置可启动同一服务；记录 client_supported，不将其伪装成不存在的独立公共 marketplace listing。
- [ ] 将所有平台公开链接与明确状态回填 README/官网/发现渠道文档，重新检查无虚假“官方已上架”表述。

### Task 3：最终审查、远端源码与双站点部署

**Files:** Modify .github/workflows/pages.yml、docs/DISCOVERY_CHANNELS.md；最终证据写相邻 audit/deployment-status.json 与 registries/publication-status.json。

**Interfaces:** 交付清单包含 git_sha、source_version、artifact_sha256、Pages_url、official_site_url、per_platform_status；用户能独立检查每个结果。

- [ ] 执行 A/B/C 全部受影响测试、pip check、python -m build、MCPB handshake、Pages 双 base 构建和产物哈希核验；无新增变更时不重复整套检查。
- [ ] 按所选执行技能完成一次全分支独立审查，解决实质问题并只重跑受影响验证。该步骤是 Task 2 公共发行的前置门禁；Task 2 的提交包准备可先完成。
- [ ] 用功能分支提交远端源码并创建可审查 PR；创建后调用 attach_artifact。按用户部署要求完成已通过验证的合入/发布流程，保留 Git 历史；不强推或合并旧 checkout。
- [ ] GitHub REST 更新 description/homepage/topics，再读回确认；若具备 Social preview 上传界面，使用现有视觉资源生成准确预览并验证。
- [ ] GitHub Pages 使用现有 workflow 构建部署；保留既有九图展示，读取 workflow 结果与公开首页、安装、效果入口。
- [ ] 官网：先确认实际托管目录和可用管理通道，更新可回滚的静态文件；缺管理通道时保留已构建官网包，仅该站点标 requires_user_action。已有 HTTPS 200 不等于新版内容已上线。
- [ ] 浏览器验证公开导航、安装复制、响应式展示与九图效果；抽查旧报告/download URL 保持可达。
- [ ] 交付最终真实状态、代码/产物链接与检测范围。任何平台身份验证或审核未结束时给出明确下一步，不宣称所有平台已发布。

## 本次核验的官方参考

- [OpenAI 插件打包](https://developers.openai.com/plugins/build/plugins)：仓库 marketplace 与公共目录不同。
- [OpenAI 公共提交](https://developers.openai.com/plugins/deploy/submission)：需要发布身份与审核。
- [Claude Code marketplace](https://code.claude.com/docs/en/plugin-marketplaces)：公开 Git 仓库分发与实际安装验证。
- [官方 Registry MCPB](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/package-types.mdx)：GitHub Release artifact 与 fileSha256。
- [MCPB manifest](https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md)：manifest_version=0.3、runtime 配置和 directory 输入。
- [Smithery 发布](https://smithery.ai/docs/build/publish)：按当前 stdio 发布入口核验。
- [Glama 提交](https://glama.ai/mcp/faq)：GitHub 源码收录与所有权 Claim 分开。

