# B：许可、曝光与 Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 Apache-2.0/0.8.0 的原创代码发行，通过 README、官网、客户端入口与现有九图展示增加可发现性。
**Architecture:** 许可和版本是单一发行身份；文档消费 A 已验证能力；站点保持现有页面与链接，只补入口、展示和搜索元数据。
**Tech Stack:** setuptools、静态 HTML/CSS/JS、GitHub REST、现有 build_pages。
**Spec:** [设计 §5/6/7](../specs/2026-10-13-cad-bundles-exposure-design.md)。

## Global Constraints

- 继承计划索引；原创代码 Apache-2.0，GPL/字体/vendor 各自保留。
- 0.8.0 的 Python/插件/协议身份一致；旧安装包保持实际版本。
- 九图既有 https://mola-maker.github.io/CAD2GIS/deliveries/ 可用，九张主效果展示复用现有 URL。
- 不增加九图 ZIP/数据上传任务，不更改 CONDITIONAL/absolute_accuracy_verified=false。
- 不代发邮件、私信、社区帖；公开曝光使用用户授权的 repo/site/registry。

## Review Focus

- wheel 仍含旧 LicenseRef-Proprietary：Task 1 实际包检查。
- README 安装命令引向不存在的 Release：Task 2。
- 中文与英文描述出现能力范围冲突：Task 2 人工审阅。
- GitHub Pages 子路径与官网根路径混用：Task 3 双 base URL 构建。
- 九图外链失效：Task 3 记录错误并保留入口，不自动上传替代数据。

---

### Task 1：原创许可与一致版本

**Files:** Modify LICENSE.md、NOTICE.md、pyproject.toml、src/cad2gis/__init__.py、src/cad2gis/contracts.py、plugins/cad2gis-agent/.codex-plugin/plugin.json、plugins/cad2gis-agent/.claude-plugin/plugin.json、packaging/THIRD_PARTY_NOTICES.md、docs/APPLICATION_ARCHITECTURE.md；检查两个 marketplace 文件有无版本字段。

**Interfaces:** 包、插件与 PLUGIN_CONTRACT_VERSION 均为 0.8.0；pyproject license=Apache-2.0，保留 license-files 中所有第三方例外。

- [ ] 建立当前实际包身份基线：打包临时 wheel 并检查 METADATA/许可证，保存旧许可与版本证据。
- [ ] 从 Apache 官方取完整 LICENSE 正文；调整 NOTICE 和原创源码声明，保留贡献版权、第三方和数据边界。
- [ ] 同步所有实际版本字段、插件展示元数据、package readme 与 project URLs；不全局替换历史记录中的 0.7.8。
- [ ] 运行 python -m build，并实际打开 wheel/sdist 验证 Version: 0.8.0、License-Expression: Apache-2.0、NOTICE/第三方许可证均存在。
- [ ] 跑现有 tests/test_plugin_contracts.py、tests/test_release_architecture.py；检查源码中当前生效的非商用表述已更新，历史发行记录保持原意。
- [ ] 提交该任务。

### Task 2：中英文 README 与曝光入口

**Files:** Modify README.md、plugins/cad2gis-agent/README.md、plugins/cad2gis-agent/clients/README.md、.agents/plugins/marketplace.json、.claude-plugin/marketplace.json；Create README.en.md、CONTRIBUTING.md、.github/ISSUE_TEMPLATE/bug_report.yml、.github/ISSUE_TEMPLATE/feature_request.yml、.github/PULL_REQUEST_TEMPLATE.md、docs/DISCOVERY_CHANNELS.md。

**Interfaces:** 曝光清单描述 GitHub 搜索、官网、Release、客户端 marketplace、公开 MCP/plugin 目录；每项状态有实际 URL 或明确尚未发布原因。

- [ ] 核验已有 Linux .3、macOS 0.7.0、Windows 0.7.8 的下载 URL 和版本，保存准确矩阵。
- [ ] 改写 README 首页：一句能力说明、官网/效果展示/安装/许可/英文入口、能力矩阵、最短安装示例。长安装和维修命令仍保留可查链接，现有契约要求的有效命令保留。
- [ ] 英文文档覆盖相同能力与边界；只有 A 实测工具在能力表写 supported，其余写 optional/not_verified。
- [ ] 增加贡献与问题提交指南，要求来源哈希、runtime 状态和合成复现，禁止在公开 issue 上传客户原图。
- [ ] 验证内部文件链接存在、外部下载返回正确响应；运行 tests/test_webdemo_plugin_guide.py、tests/test_plugin_contracts.py，修正真实失效命令而非删除有效保护。
- [ ] 提交该任务；GitHub 远端 metadata 的实际更新留到 C 的发布阶段，使官网和下载入口先有具体可用结果。

### Task 3：双站点入口、元数据与九图效果复用

**Files:** Modify src/cad2gis/webdemo/landing.html、src/cad2gis/webdemo/install.html、scripts/build_pages.py、.github/workflows/pages.yml；Create scripts/verify_public_site.py；Test tests/test_pages_metadata.py；构建产物含 robots.txt、sitemap.xml。

**Interfaces:**
- build(output: Path, *, release_root: Path | None=None, public_base_url: str="https://mola-maker.github.io/CAD2GIS/") -> None，保留原有调用兼容性。
- verify_public_site(base_url: str, *, gallery_url: str) -> dict[str, Any]，检查页面、九张效果图与跳转，报告 JSON。
- 官网构建 base 为 https://cad2gis.molamaker.cn/，Pages base 为 https://mola-maker.github.io/CAD2GIS/。

- [ ] 写失败测试：test_pages_and_custom_domain_metadata_resolve、test_gallery_reuses_existing_urls_without_report_copy；检查 canonical/sitemap 一致、nine-drawings-report.zip 不在新输出中、效果链接只有九张主图而非 11 个分区。
- [ ] 运行新增测试，确认旧构建缺少元数据/复用约定导致失败。
- [ ] 增加 build 的 public_base_url 参数，生成实际 description/OG/JSON-LD/robots/sitemap；落地首页“九图效果”与“Agent 安装/能力”入口，保持现有视觉风格与 URL。
- [ ] Gallery 链接现有 deliveries 展示；若需要首页缩略图，引用已有公开效果图片 URL，不重新发布压缩包或 QGZ/GPKG。
- [ ] 实现 HTTP/本地资源核验脚本；远端暂时不可访问报告 failed_check，不抓取上传完整九图作补救。
- [ ] 两个 base 分别构建并运行 tests/test_pages_publication_gate.py、tests/test_webdemo_shared_assets.py、tests/test_ci_contracts.py、tests/test_webdemo_plugin_guide.py；node --check 检查实际变更 JS；浏览器验证导航、安装复制和效果展示。
- [ ] 提交该任务并保存两个站点清单，等待 C 完成公开部署。

## 外部元数据发布目标

description：Evidence-first DWG/DXF to GIS conversion, QGIS delivery and CAD MCP tools for AI agents.  
homepage：https://cad2gis.molamaker.cn/  
topics：cad、gis、dwg、dxf、qgis、geopackage、mcp、geospatial、ai-agents、cad2gis。  
没有访问统计接口时，不报告访问量增长；只报告曝光入口实际完成数量。

