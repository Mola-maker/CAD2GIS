# A：CAD 旁路能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给现有 Agent MCP 增加可调用的 CAD 查询、检查与副本编辑能力，并形成核心 GIS 验收闭环。
**Architecture:** 小型策略、工作副本和 stdio 传输模块分别负责允许清单、不可变修订和上游进程；agent_mcp 只负责授权根目录与工具注册。
**Tech Stack:** Python、ezdxf、mcp.client.stdio、ClientSession、隔离的 U-C4N/Autocad-MCP。
**Spec:** [设计 §3/4/8](../specs/2026-10-13-cad-bundles-exposure-design.md)。

## Global Constraints

- 继承索引全部约束；AUTOCAD_MCP_BACKEND=ezdxf、DISCOVERY_MODE=off、TOOL_PACKS=core、ENABLE_3D=false、DANGEROUS_COMMANDS_ENABLED=false。
- 四个入口名称固定为 get_cad_bundle_status、prepare_cad_bundle_workspace、list_cad_bundle_tools、call_cad_bundle_tool。
- 15 个上游工具取设计固定清单；drawing_diff 强制 markup=false；生命周期工具仅供内部使用。
- 上游调用超时 120 秒、源 DXF 上限 512 MiB、JSON 结果上限 4 MiB；并发修改同一 workspace 使用互斥与修订比较。
- workspace_id 是 canonical workspace 绝对路径；所有 MCP 接口必须用现有 _path 验证授权根目录。
- 输入根路径含逗号时明确拒绝该 workspace，并解释上游 ALLOWED_PATHS 限制；不设置为空或扩大到整个项目根。

## Review Focus

- 中文/空格/逗号、符号链接与路径越界：Task 1/2。
- 源 DXF 大小边界或导出失败：Task 1。
- 上游调用退出、报错、超时：Task 2。
- 一次图纸编辑失败后并发读请求：Task 1/2。
- 实际工具列表减少：Task 2/3，不注册不存在的能力。

---

### Task 1：工作副本、允许清单与修订

**Files:** Create src/cad2gis/cad_bundle_policy.py、src/cad2gis/cad_bundle_workspace.py；Test tests/test_cad_bundle_workspace.py。

**Interfaces:**
- Produces policy.ALLOWED_TOOLS: frozenset[str]、MUTATING_TOOLS: frozenset[str]、UPSTREAM_SHA: str。
- create_workspace(source: Path, workspace: Path) -> dict[str, Any]。
- load_workspace(workspace: Path) -> dict[str, Any]，返回 source_sha256/current_revision/current_sha256。
- commit_revision(workspace: Path, staged_dxf: Path, expected_revision: int, evidence: dict[str, Any]) -> dict[str, Any]。
- workspace 中 manifest.json、source.dxf 和 revisions/000000.dxf；source.dxf 与每个修订不会被覆盖。

- [ ] 写失败测试：test_workspace_keeps_source_hash、test_workspace_rejects_existing_or_symlink、test_revision_conflict_preserves_current、test_unicode_space_paths_and_comma_rejection；验证源哈希不变、路径拒绝与 current_revision CAS。
- [ ] 运行 PYTHONPATH=src python -m pytest tests/test_cad_bundle_workspace.py -q，确认因新接口缺失而失败。
- [ ] 实现上述接口；复制用文件流，哈希用 hashlib.file_digest，staging 同文件系统，清单原子替换；拒绝 .dwg 直接作为 DXF，提供先用 reader 转出 DXF 的错误。
- [ ] 运行同一文件全部测试，预期全通过；补充大于上限与损坏 DXF 的拒绝检查。
- [ ] 提交该任务的策略、workspace 模块与测试。

### Task 2：隔离上游安装、MCP 发现与受控调用

**Files:** Create src/cad2gis/cad_bundle_transport.py、src/cad2gis/cad_bundles.py；Modify src/cad2gis/cli.py；Test tests/test_cad_bundle_transport.py。

**Interfaces:**
- Consumes Task 1 的 workspace 与策略。
- install_bundle(prefix: Path) -> dict[str, Any]：创建独立 venv，安装固定 Git 提交，写 receipt.json。
- get_bundle_status() -> dict[str, Any]：检查 CAD2GIS_CAD_BUNDLE_COMMAND 指定的单一绝对可执行文件与安装 receipt，未设置返回 not_installed。
- async discover_bundle(workspace: Path) -> list[dict[str, Any]]。
- async invoke_bundle(workspace: Path, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]。
- CLI：cad2gis cad-bundle install --prefix PATH、cad2gis cad-bundle status --json；安装输出给出需配置的 executable 绝对路径。

- [ ] 写失败测试：test_not_installed_is_explicit、test_call_rejects_tools_and_path_arguments、test_child_env_omits_api_secrets、test_failed_or_timed_out_call_keeps_revision、test_discovery_intersects_runtime_tools。
- [ ] 运行 PYTHONPATH=src python -m pytest tests/test_cad_bundle_transport.py -q，确认失败是接口缺失。
- [ ] 实现独立安装与 receipt 核验；argv 使用列表，无 shell；传输使用 MCP SDK 的 stdio_client/ClientSession，保留 stderr，stdout 仅协议。
- [ ] 每次调用在 workspace 新建 staging 目录，只给上游该 staging 的 ALLOWED_PATHS；内部复制所需快照/修订并 drawing_open，参数只注入 staging 路径；编辑成功 drawing_save 到 staging 输出，再用 Task 1 CAS 发布。错误/超时不得发布。
- [ ] 实现超时和结果上限，拒绝 path/old_path/new_path 及其他文件路径字段；对 drawing_diff 固定历史与当前副本、markup=false。子进程 env 只继承必要 PATH、运行时和系统变量。
- [ ] 同一测试文件全通过；在本任务隔离环境安装上游并真实执行 handshake/tools/list，保存 source pin 和结果证据。
- [ ] 提交该任务源码、CLI 接口和测试。

### Task 3：现有 Agent 注册四个入口

**Files:** Modify src/cad2gis/agent_mcp.py、src/cad2gis/contracts.py、plugins/cad2gis-agent/.mcp.json、plugins/cad2gis-agent/clients/codex.config.toml；Test tests/test_cad_bundle_mcp.py；运行现有 tests/test_plugin_contracts.py、tests/test_mcp_stdio.py。

**Interfaces:**
- Consumes Task 2。
- get_cad_bundle_status() -> dict[str, Any]。
- prepare_cad_bundle_workspace(source: str, workspace: str) -> dict[str, Any]。
- async list_cad_bundle_tools(workspace_id: str) -> dict[str, Any]。
- async call_cad_bundle_tool(workspace_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]。
- get_capabilities 增加 optional_cad_bundle 段，精确列出四个入口与 installed/limited 状态。

- [ ] 写失败测试：test_four_tools_are_registered、test_missing_bundle_keeps_core_mcp_alive、test_bundle_workspace_obeys_project_roots、test_advertised_tools_have_upstream_schema。
- [ ] 运行 PYTHONPATH=src python -m pytest tests/test_cad_bundle_mcp.py -q，确认失败。
- [ ] 注册四个 handler、MCP_TOOL_NAMES 的排序清单、参数 schema 与 env_vars；把源/输出检查放在 handler 授权边界，不给上游整个原始项目根。
- [ ] 运行新增测试及上述两个现有测试文件，预期全部通过；实际 stdio tools/list 的集合等于 MCP_TOOL_NAMES。
- [ ] 提交接口、协议、配置与测试。

### Task 4：原生 DXF 接口与真实编辑后 GIS 验收

**Files:** Create src/cad2gis/reader/dxf.py、tests/test_native_dxf_reader.py、tests/test_cad_bundle_integration.py、tools/verify_cad_bundle.py；Modify src/cad2gis/reader/libredwg_cli.py、src/cad2gis/reader/resolver.py、src/cad2gis/cad2gis_v3/project_profile.py、src/cad2gis/cad2gis_v3/source_export.py、src/cad2gis/cad2gis_v3/source_replay.py；Modify plugins/cad2gis-agent/skills/convert-cad-to-gis/SKILL.md、plugins/cad2gis-agent/skills/iterate-cad-to-gis/SKILL.md；Create docs/CAD_BUNDLES.md。

**Interfaces:**
- reader.dxf.extract_dxf_records(source_path: str | Path, *, layout_filter: str | None=None) -> DWGRecordInventory。
- 从 libredwg_cli 提取 records_from_dxf_document(document: Any, source: Path, source_sha256: str, *, layout_filter: str | None=None, geodata_registration: dict[str, Any] | None=None) -> DWGRecordInventory，复用实体、块、属性与元数据提取，不复制一套解析器。
- resolver.extract_records 对 .dxf 使用原生 reader，DWG 后端选择保持现有行为；core source 校验接受 .dwg/.dxf。
- source replay/export 保存实际 extraction_backend=native_dxf 与副本哈希，原生结果不标为 injected_records。
- verify_bundle(workdir: Path, output: Path) -> dict[str, Any]，输出 cad2gis.cad_bundle_verification.v1。

- [ ] 写原生 reader 失败测试：test_native_dxf_keeps_handles_blocks_attributes_and_hash、test_native_dxf_needs_no_libredwg、test_dxf_source_export_is_native_and_replayable；合成 DXF 中覆盖 INSERT/ATTRIB、曲线与单位。运行 tests/test_native_dxf_reader.py 确认旧 DWG-only 边界失败。
- [ ] 最小提取共享 DXF 文档解析，新增 native_dxf 派发并更新 project/source-export/replay 的来源识别；保持完整库存门禁，不伪造诊断。运行新增 reader 测试与现有 reader/source-export/replay 测试。
- [ ] 写可选真实集成失败测试：test_real_sidecar_edit_preserves_source_and_converts；合成 DXF 含线、闭合多段线和文字，编辑后要求来源原哈希不变、新副本实体改变、GIS run 来源绑定新副本而非原图。
- [ ] 在已配置隔离上游的环境运行该测试，先确认当前实现尚未完成该验收闭环。
- [ ] 写验证工具串起 source copy、discovery、layer_list、entity_create_line、drawing_diff、drawing_topology_check 和 canonical convert/audit；保存每步实际输出与日志，不通过伪造 run_status 满足断言。
- [ ] 更新技能与文档：什么时候用旁路、怎样安装、DWG 导出边界、如何将新副本重新验收；仅引用真正注册的四个 CAD2GIS 入口。
- [ ] 跑真实集成与插件契约，再跑 tests/test_geometry_fidelity.py、tests/test_source_replay.py；缺少完整 DWG/GDAL runtime 时报告缺失并完成可运行检查。
- [ ] 验证记录写到相邻 audit，提交该任务代码与文档。

