# CAD2GIS 0.8.0 实施计划索引

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现已批准的 CAD 旁路，完成 Apache-2.0、曝光优化、网站交付与主流平台注册。
**Architecture:** 保留 canonical GIS 流水线，通过独立 stdio MCP 接入固定上游；效果展示复用现有 Pages；发行和平台注册消费同一份经过验收的产物。
**Tech Stack:** Python 3.11/3.12、MCP SDK、ezdxf、隔离的 FastMCP 3.x、静态 HTML/CSS/JS、GitHub Actions、MCPB。
**Spec:** [已批准设计](../specs/2026-10-13-cad-bundles-exposure-design.md)。

## Global Constraints

- 新源码、wheel、插件与协议版本为 0.8.0；第三方保留自身许可。
- 上游固定 cdb10638963898b3ea9b10cdd96a2c9bc495f184，版本 1.6.0；不合并上游 FastMCP 依赖到主环境。
- 九图只展示效果；已有 Pages 展示可用，不上传新的九图完整报告/数据包。
- 原始源文件不被旁路改写；GIS 精度和 CONDITIONAL 状态不因宣传或注册改变。
- 所有平台发布以真实读回 URL 为验收；当前本地服务不改成无认证公网服务。
- 当前副本为专为此任务拉取的独立 checkout；复用该副本，执行时建立功能分支，避免从旧 checkout 合并历史。
- 按本索引审阅整组计划；目前仅编写文档，未开始产品实现。
- 执行方法等待用户选择。推荐 Native：三个子项目共享版本、工具契约和发行清单，当前会话顺序实施更易保持一致。

## Review Focus

- 中文、空格和逗号路径：旁路工作目录不能因 ALLOWED_PATHS 的逗号分隔而误扩大访问范围，计划 A Task 1/2 覆盖。
- 上游进程中途退出：当前修订不能被半成品替换，计划 A Task 2 覆盖。
- DWG 输入与 DXF 副本：导出失败必须明确拒绝，不能让 ezdxf 假称支持原始 DWG，计划 A Task 1/4 覆盖。
- 已有九图外链暂时不可用：保持已有入口，报告检测失败，不自动重复上传数据，计划 B Task 3 覆盖。
- 注册时无登录或审核未完成：记录 prepared/requires_user_action/under_review，不能标为 published，计划 C Task 2 覆盖。

---

## 子项目与依赖

1. [A：CAD 旁路能力](2026-10-13-cad-bundle-capabilities.md)：4 个任务，合成 DXF 到真实 MCP 调用及核心验收闭环。
2. [B：许可、曝光与效果展示](2026-10-13-license-exposure-pages.md)：3 个任务，可在 A 的固定接口基础上完成；全部宣传能力以 A 验收结果为准。
3. [C：发行、主流平台注册与部署](2026-10-13-release-platform-registration.md)：3 个任务，依赖 A/B 的已验证版本与产物。

每个任务由实施者按失败测试/验证、最小实现、通过验证、提交顺序完成。纯文档和元数据改动使用实际构建、链接与平台 schema 检查，不新增逐字比对 README 的测试。

## 工作位置与验证入口

Repo：/home/jlu/桌面/nanodot/outputs/cad2gis-upgrade-2026-10-13/repo  
审计：/home/jlu/桌面/nanodot/outputs/cad2gis-upgrade-2026-10-13/audit  
产物：/home/jlu/桌面/nanodot/outputs/cad2gis-upgrade-2026-10-13/deliverables

基线测试使用 PYTHONPATH=src，避免旧 editable 0.7.0 污染。实施期建立本任务测试环境，不改写旧 CAD2GIS 环境。

- [ ] 用户审阅整组计划并选择 Native 或 Subagent-driven。
- [ ] 读取选择对应的执行技能，按 A → B → C 推进。
- [ ] 每个子项目独立验收，再执行整组提交前验证与独立最终审查。
- [ ] 全部可完成任务完成后，报告代码、测试、公开部署与平台注册实际状态，并列出确需用户身份操作的剩余项。

