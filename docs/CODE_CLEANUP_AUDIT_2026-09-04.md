# CAD2GIS 主入口、依赖边界与待清理候选

审计日期：2026-09-04。原始审计基于 `main` / `39b5625`，以下候选表、规模和行号记录清理前状态；A1/A2/A3 随后根据用户要求执行，结果见下节。B 类的后续实施与保留决定见 [B 类执行记录](CODE_CLEANUP_B_2026-09-04.md)。`src/cad2gis/cli.py` 已有的两处未提交注释修改保留。

## 已执行：第一批 A1 + A2 + A3

2026-09-04 完成以下清理：

- 删除失效的 `src/cad2gis/reader/dwg_extractor.py`，1,089 行。
- 从 7 个模块删除 A2 指定的 11 个私有函数及 AutoCAD 独占的 `uuid` 导入，实际 diff 删除 138 行（包括相邻空白）。
- 删除 Hutabohu、Lamteh Main、Lamteh SF 三份 `spatial_regions.json.bak.2345`，2,542 行。
- `.gitignore` 增加 `/baselines/*/config/*.bak.*`，避免同类备份再次被纳入版本控制；正式 `spatial_regions.json` 不受影响。
- 本批合计删除 **3,769 行**，其中 Python **1,227 行**；新增 1 行忽略规则。文档变更及用户原有 CLI 注释不计入这个清理规模。

上述 A 批次完成时，B/C 候选尚未执行。B 类随后按 [B 类执行记录](CODE_CLEANUP_B_2026-09-04.md) 分项处理；C 类失联策略守卫等问题仍待单独修复。下面 A 批次的验证和原始候选表保留历史口径。

本次清理后的验证：

| 检查 | 结果 |
|---|---|
| 指定 7 个文件的剩余 AST 与 `39b5625` 比较 | 完全一致，仅删除目标函数和独占 `uuid` 导入 |
| 所有仍存在的已跟踪 Python 文件 AST 解析 | 147 个通过 |
| Reader/facts/family/semantics/performance/stage 回归 | 75 passed |
| CLI/cross-CAD/source profile/pipeline/CRS 回归 | 69 passed，3 个既有依赖弃用/配置提示 |
| Ruff 0.12.12：src、tests、tools、插件 scripts | All checks passed |
| `git diff --check` | 通过 |
| wheel 构建与内容核查 | 成功；无旧 dwg_extractor；保留三个 reader、公共 CLI、MCP 和 v3 pipeline；console scripts 正确 |
| implementation provenance | 生成成功，50 个文件均存在；scope version 7；被删旧 reader 原本不在清单中，无需调整 scope |
| 独立只读代码审查 | Critical / Important / Minor 均为 0 |

测试使用 Python 3.12.13；缺失依赖安装在项目忽略的 `tmp/code-cleanup-validation-deps` 和 `tmp/code-cleanup-gis-deps`，没有替换系统 Python 或已安装 CAD2GIS。GDAL/OGR wheel 使用项目指定的 `pyramids-gis==0.58.1`。本次为 **144 项针对性回归**，没有执行完整 pytest 或真实 DWG 转换；不声称已完成真实产物等价性验证。

复现两个测试集合（准备好项目依赖，令 `PYTHONPATH` 包含 `src` 后）：

```text
python -m pytest -q tests/test_family_validation.py tests/test_reader_capabilities.py tests/test_reader_role_provenance.py tests/test_libredwg_insert_facts.py tests/test_portable_runtime.py tests/test_performance_contracts.py tests/test_semantics_annotation.py tests/test_stage_contracts.py
python -m pytest -q tests/test_canonical_cli.py tests/test_crosscad_contracts.py tests/test_source_inspection.py tests/test_source_profile_plan_domain.py tests/test_pipeline_drawing_space.py tests/test_georef_batch_equivalence.py
python -m ruff check src tests tools plugins/cad2gis-agent/scripts
python tools/build_reproducible_wheel.py --output tmp/code-cleanup-wheel
```

第二组测试在本次临时环境中先调用了 `cad2gis.native_runtime.ensure_osgeo_runtime()`，随后在同一进程调用 `pytest.main`，以暴露 wheel 内的 GDAL/OGR。wheel 只构建一次，本轮未声称重复构建的二进制可复现性。

## 1. 结论与审计口径

当前仓库已经有正式入口和统一转换实现。主要问题是：旧 reader/转换代码残留、部分拆分后的函数失去调用、可选框架与默认运行包的边界不清，以及两套 Web 产物共享代码不足。继续把 CLI 拆成更多小文件，对清理冗余的帮助有限；应先处理有证据的残留，再拆后端调度和 reader 中混入的语义逻辑。

本次以 Git 跟踪文件为清单，共 330 个文件；148 个 Python 文件、67,857 行，其中 `src/` 下 83 个 Python 文件、54,049 行。行数包含注释与空行。对全部 Python 文件进行 AST 解析和模块引用扫描，再人工核对关键主链、候选调用方、动态注册、测试、插件、打包、CI 和网页构建。前端另做资源引用、两份实现差异和 SHA-256 比对。

“全仓静态扫描”不等于完整运行覆盖。本文的“无调用”指当前仓库生产代码、测试、工具和配置中未找到消费者；不能排除仓库外的 Python 调用者。公开 API、命令入口、装饰器注册以及人工诊断脚本单独判断，不因缺少普通 import 就删除。

证据分级：

- **A：优先清理候选**。没有当前消费者，且有具体残留证据；仍应在实施时做相应回归。
- **B：条件清理候选**。有外部兼容可能、尚有测试/文档承诺，或需要先调整调用方。
- **C：先修复或澄清**。失去调用可能意味着功能遗漏，直接删除会掩盖问题。
- **保留**。已有消费者，或者属于必要的独立入口/回退/证据数据。

## 2. 从哪里进入代码

### 2.1 正式转换调用链

```text
pyproject.toml: cad2gis = cad2gis.cli:main
python -m cad2gis -> __main__.py -> cli.main
                                      |
                        _parser / args.handler
                                      |
                       _convert / _auto_convert
                                      |
                 cad2gis.pipeline.convert_project
                    配置发现、参数与路径校验
                                      |
                runtime.call_conversion_backend
                   延迟导入、原生运行时准备
                                      |
          cad2gis.cad2gis_v3.pipeline.convert(ConversionRequest)
                                      |
            SourceProfile / MappingRegistry / 快照与准入
                                      |
                 cad2gis_v3.ingest.ingest
                                      |
              reader.resolver.extract_records
                 /             |              \
         libredwg bindings  libredwg_cli     autocad
                                      |
         plan_domain -> 场景与空间过滤 -> 可选 OSM 粗锚点
                                      |
                semantics -> 可选道路锚点细化
                                      |
             曲线几何保真校验 -> topology
                                      |
       构建 evidence graph -> 可选 Decision Pack
                                      |
                    几何/拓扑策略校验
                                      |
                CRS / GEODATA / 可选 GCP
                                      |
       写出 evidence graph + source/evidence/delivery.gpkg
                                      |
             QML / manifest / 原子发布 / run_status
```

Reader 的具体选择规则在 resolver 中：`libredwg` 优先可用的 bindings，否则走 CLI；AutoCAD 由显式配置选择。不能把 LibreDWG bindings 和 CLI 视作重复实现。

建议阅读顺序与定位：

| 顺序 | 文件/行 | 要理解的边界 |
|---|---|---|
| 1 | [pyproject.toml](E:/github-CAD2GIS/CAD2GIS/pyproject.toml:39) | console scripts 与打包范围 |
| 2 | [cli.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cli.py:21)、[main](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cli.py:773) | 参数注册、handler 分派、输出和错误转换 |
| 3 | [公共 pipeline](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/pipeline.py:247) | 配置发现、source/project/run 的公共契约 |
| 4 | [runtime](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/runtime.py:199) | 后端加载与调用 |
| 5 | [v3 convert](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/pipeline.py:1454) | 实际转换顺序与副作用 |
| 6 | [v3 ingest](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/ingest.py:26)、[resolver](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/reader/resolver.py:86) | 源事实提取与完整性 |
| 7 | `plan_domain.py`、`semantics.py`、`topology.py` | 几何域、业务分类、网络关系 |
| 8 | `georef.py`、`warehouse.py`、`stage_contract.py`、`implementation.py` | 坐标与交付、阶段证据、实现指纹 |

### 2.2 其他真实入口

| 入口 | 路由与性质 |
|---|---|
| Python 包 API | `cad2gis.__getattr__` 延迟导出 `pipeline` API；`convert = convert_project` 是兼容别名 |
| inspect/bootstrap/validate | 公共 `pipeline` → `runtime.call_project_backend` → `project_profile` |
| auto-convert | onboarding/provider → 源绑定配置 → 同一个 `convert_project` |
| MCP | `cad2gis-agent-mcp = cad2gis.agent_mcp:main`；工具由服务注册，不依赖普通函数调用形式 |
| Review | `cli._review` → `review_server`；HTTP route、静态资源与 revision store 是独立运行入口 |
| GCP | `gcp_workflow`；prepare/diagnose/export 可加载 `CAD2GIS_GCP_TOOL_PATH` 指定的外部实现，status 可独立运行 |
| 独立 v3 CLI | `python -m cad2gis.cad2gis_v3.cli` 可直接调用 v3 convert；属于旧入口候选，不是主入口 |
| 离线 curation CLI | `python -m cad2gis.cad2gis_v3.curation_cli`；proposal-only 独立流程，不应按主转换的不可达模块删除 |
| 本地 Web/CI 构建 | `tools/build_webdemo.py`；本地服务读取根 `webdemo` |
| Pages 发布 | `.github/workflows/pages.yml` → `scripts/build_pages.py` → `original-demo` 工作区模板及共享产品页面 |

CLI 已核验 14 个叶命令都有 handler：doctor、runtime status/install、inspect、bootstrap、validate、convert、auto-convert、gcp status/prepare/diagnose/export、review、verify。没有发现整块失联的正式 CLI 子命令。

## 3. A 级：优先清理候选

### A1. 孤立的旧 DWG 提取文件

- 原位置：`src/cad2gis/reader/dwg_extractor.py`，**1,089 行**，现已删除；原始内容可从 `39b5625` 查看。
- resolver 只分派 `autocad`、`libredwg_cli`、`libredwg`；源码、测试和工具中没有该模块的消费入口。
- 第 35 行导入不存在的 `cad2gis.cad_common`。独立导入实测抛出 `ModuleNotFoundError: cad2gis.cad_common`。
- 含固定 Linux Python 路径；与现行 `libredwg.py` 有 18 个同名函数/类，其中 12 个 AST 完全一致。
- 隐藏目录中的旧解耦 spec 曾计划这一重命名，但当前实现没有完成该路径。

**建议：将它列为最高优先级的整文件删除候选。** 保留当前三个 reader 实现；历史迁移意图留在 Git/spec 中。实施后检查 wheel 不再包含它，并运行 reader、CLI 和跨 CAD 契约测试。这里的“低风险”仅针对现有仓库支持的入口。

### A2. 无消费者的私有辅助函数

以下不是按“名称像旧代码”判断，而是顶层定义、全仓引用及替代调用路径交叉核对后的候选。

| 文件 | 函数与行范围 | 说明 |
|---|---|---|
| [ingest.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/ingest.py:10) | `_reader_backend`，10–13 | 公共 ingest 直接经 `_extract_records` 调 resolver；该包装未调用 |
| [v3/pipeline.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/pipeline.py:176) | `_implementation_digest`，176–178 | 实际流程使用完整 provenance/snapshot |
| [family_validation.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/family_validation.py:202) | `_separator_normalized`，202–206；`_field_tokens`，209–219 | 后者无调用，前者只服务后者；当前使用 `_cleaned_pattern` / `_literal_tokens` |
| [project_profile.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/project_profile.py:755) | `_patch_source_profile_local`，755–765 | 未找到调用；不要因此删除实际 profile 编译流程 |
| [semantics.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/semantics.py:85) | `_convex_hull`，85–104；`_polygon_area_signed`，107–112 | 两者在本模块及外部均无消费者；其他模块的同名 hull 函数需要分别保留 |
| [topology.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/topology.py:110) | `_nearest_unique`，110–112 | 无调用的兼容包装；索引类和 linear fallback 仍使用 |
| [reader/autocad.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/reader/autocad.py:2136) | `_select_model_collections`，2136–2183 | 无消费者；其独占 `uuid` 导入可联动检查 |
| [reader/autocad.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/reader/autocad.py:2571) | `_entity_text`，2571–2572；`_block_attributes`，2597–2598 | 活跃流程调用保留 provenance 的 facts 版本 |

共 **11 个函数，115 行函数定义/实体**，未计周围空白和可顺带去掉的导入。不包含 C1 的失联策略守卫。它们是适合单独提交的小批清理。裸函数名计数会漏掉同名函数，也会漏掉“几个函数互相调用、整体无人调用”的孤岛，因此已补充模块内调用核查。

### A3. 3 份历史空间配置备份

| 位置 | 字节数 | 行数 |
|---|---:|---:|
| `baselines/hutabohu/config/spatial_regions.json.bak.2345`（已删除） | 55,869 | 739 |
| `baselines/lamteh_main/config/spatial_regions.json.bak.2345`（已删除） | 118,628 | 1,564 |
| `baselines/lamteh_sf/config/spatial_regions.json.bak.2345`（已删除） | 16,566 | 239 |

[spatial_filter.py:586](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/spatial_filter.py:586) 精确读取 `spatial_regions.json`，没有备份匹配逻辑。合计 **191,063 字节、2,542 行 JSON**。备份与当前配置内容不同，应称为可归档的历史文件，而不是完全重复文件。保留 Git 历史后可从工作树清理；不修改当前基线配置或 DWG。

## 4. B 级：先收敛接口或用途，再清理

### B1. AutoCAD reader 内的旧 GIS 分类/导出路径

入口是 [read_dwg_with_autocad](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/reader/autocad.py:3696)，调用 `_items_from_grouped` → `build_items_from_records`。全仓未发现外部消费者；现行入口是 [extract_dwg_records](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/reader/autocad.py:3754)。

候选闭包共有 9 个函数：`_point_wkt`、`_line_wkt`、`_polygon_wkt`、`_evidence_item`、`_feature_item`、`build_items_from_records`、`_bind_entity_keys`、`_items_from_grouped`、`read_dwg_with_autocad`。位于 3189–3404、3657–3751 两段，函数实体合计 **297 行**，含间隔的两段合计 311 行。

联动候选是 [apd_rules.py:1–141](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/apd_rules.py:1)：单图匿名块编号、DMPH 标签规则和旧标注绑定。`link_annotations` 本身已无调用；其余旧规则的消费者集中在上述 AutoCAD 旧路径。

**建议：作为一批“退出旧转换 API”的清理。** 保留全部现行 records 提取、Core Console/COM 回退、原生曲线与属性事实。`apd_rules.py` 不能整文件直接删除：第 144 行 `set_traditional_axis_order` 仍被 `georef.py:143–144` 使用，先迁到坐标/原生运行时公共模块，再清理旧规则。这一项对 reader 与业务语义解耦的收益最大。

### B2. 未接入的 semantic_anchor 框架

[semantic_anchor.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/semantic_anchor.py:1)，**1,580 行**。生产源码、测试、工具没有 import 消费者，也无独立 CLI；但有 22 项 `__all__` 公共 API。[ROAD_MATCH_LOCATOR.md:35](E:/github-CAD2GIS/CAD2GIS/docs/ROAD_MATCH_LOCATOR.md:35) 明确把它描述为尚无候选生成器输入的框架。

**选项：** 如果短期不做该路线，可移入实验区并从默认 wheel 移除；如果要做，保留并补真实调用入口与契约测试。不能把“尚未集成”写成“算法无价值”，也不能与已经在转换中调用的 `osm_anchor.py` 混淆。

### B3. 未接入且协议脱节的 records_adapter

[reader/records_adapter.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/reader/records_adapter.py:1)，**70 行**。`load_records`、`validate_bundle_facts` 无生产/测试消费者，resolver 未提供 records 模式；架构文档仍称其为不可变记录重放入口。

它调用 `curation.load_review_bundle` 校验，但当前 curation bundle 明确 `conversion_import_allowed=False`，facts 结构也不是完整 SourceEntity 原始记录；`load_records` 又直接把 facts 喂给 `SourceEntity.from_record`。因此其宣称的重放契约不能仅凭现有代码视为可用。

**选项：** 撤回该接口与对应文档承诺，或建立真正的 source-bound record bundle schema 后接入。不要为了消除未引用模块，把 curation 提案包直接接入权威转换。

### B4. 旧 v3 CLI 与轻量公开接口

- [v3/cli.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/cli.py:12) 是独立 `python -m` 入口，绕过公共 CLI 的配置发现和错误处理；可先兼容转发到正式 CLI，随后退役。它仍在 implementation 文件清单中，不能裸删。
- `profile.py` 是明确公开的重导出门面；顶层 `ingest.py` 是另一条包装入口。主转换没有经过它们，但保留成本低；只有明确收敛公共 API 时才考虑删除。
- `runtime.backend_location`、`model.StageBundle`、`cable_legend.cable_spec_color`、`reader.contracts.ReaderContract` 无仓内消费者，但属于非私有符号/类型。建议列入 API 退役候选，不与私有叶函数同批处理。

### B5. runtime 中的旧后端部署兼容

[runtime.py](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/runtime.py:31) 还保留 `profile_builder` 候选模块、editable fallback、external path 部署模式。

- 仓库没有 `profile_builder.py`；现行 `project_profile.py` 已有 inspect/bootstrap/validate 三个操作。
- 本次轻量检查中，真实 checkout 已通过 `installed_package` 找到内置后端。
- `_editable_backend_root()` 返回 `src/cad2gis`，再经 `_backend_package_path()` 追加 `cad2gis/cad2gis_v3`，得到不存在的双层 `cad2gis/cad2gis` 路径。这是 fallback 实现与路径契约不同步的证据。
- `CAD2GIS_BACKEND_PATH` 仍有打包说明与测试承诺，不能仅因本仓不用就删除。

**选项：** 决定是否继续支持外部后端。继续支持就统一 root 定义并补真实加载验证；只维护内置后端时，可退役 fallback 和 `profile_builder` 兼容分支。`runtime` 的延迟导入、错误归一化与 native runtime 准备仍有用途。

### B6. Web 共享资产和重复 JS

`webdemo/assets` 与 `webdemo/original-demo/assets` 有 **15 个 SHA-256 完全一致的文件，366,427 字节**：6 个字体、4 个 SVG、`hero-geometry.json`、4 份字体许可证。

[scripts/build_pages.py:190](E:/github-CAD2GIS/CAD2GIS/scripts/build_pages.py:190) 当前复制 original assets；[tools/build_webdemo.py:161](E:/github-CAD2GIS/CAD2GIS/tools/build_webdemo.py:161) 使用公共 assets。先改成同一个资源来源，再删除副本。字体许可证应随共享字体继续保留。

两份 `app.js` 只在 10 行上存在差异，主要是 `ZoomToExtent` 与初始地图中心；两份 `demo-fixture.js` 仅缓存版本 query string 不同。通过页面配置共享实现，可消除约 **963 行、41,597 字节**副本。它们现在都有消费者，不属于可直接删除的死文件。

特别保留两套数据清单的差异：本地 catalog 有 10 个案例，当前公开 Pages catalog 只有 Hutabohu；公开案例还有 publication gate，不能通过合并代码顺便扩大公开数据范围。

### B7. Pages 原稿中被裁掉的旧首页

[build_pages.py:114](E:/github-CAD2GIS/CAD2GIS/scripts/build_pages.py:114) 只抽取 original HTML 的 console-app 区段。原稿前部 270 多行 hero 首页不进入当前 Pages 页面，生成页面也不加载 original `hero-motion.js`（396 行）。

**选项：** 不再维护原稿独立访问时，改成专用 workspace 模板，再删除旧 hero HTML/JS 及专属样式资源。`test_webdemo_plugin_guide.py:87` 仍检查原稿文案，需迁移其有效断言。与 B6 的资产删减存在重叠，收益不可机械累加。

`hero-geometry.json` 当前主要是生成器元数据，可只从公开产物排除；它在开发/构建验证中仍有用途，优先级低。

### B8. 只在旧测试或公开导出中保留的算法分支

- [znro_shape.alpha_shape_union:192](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/znro_shape.py:192) 只由 `test_znro_shape.py` 调用；生产 `semantics.py:2437` 使用 `conservative_znro_polygons`。若不再保留算法对比参考，可连同 `_unique_points`、`_prim_mst_max_edge`、`_triangle_circumradius` 和该模块专属 `_convex_hull` 一起退役，合计约 **112 行定义体**。保留 conservative 分支和共享依赖，迁移/退出旧测试的意图要写清楚。
- [spatial_coverage.source_entity_drawing_points:50](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/spatial_coverage.py:50)，**62 行**，无生产或测试调用，但存在于 `__all__`。当前 GCP 空间覆盖在 pipeline 中使用当前分区业务要素；旧函数从经过 CAD 空间/角色过滤的源实体顶点取覆盖域，两者策略不同。可随公共 API 整理退役，不应简单替换现有覆盖策略。
- `decision_validation.validate_crs_candidate`、`geodata.crs_to_local_point`、`osm_anchor.apply_osm_anchor`、`iteration.learning_context_for_bundle` 有测试消费者、暂无生产调用。作为扩展候选记录，暂不建议删除：这些可能是独立决策、逆变换验证、权限或未来接入契约，先确认维护目的。

## 5. C 级：先修复或确认，不能直接以死码处理

### C1. 解耦后失联的 reviewed policy 守卫

[pipeline._enforce_geometry_policy:484](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/pipeline.py:484) 无调用。主流程现在分别调用 `_validate_source_geometry:1734` 与 `_validate_topology_policy:1837`，但旧包装还调用了 [_validate_reviewed_policy:294](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/pipeline.py:294)。后者没有其他消费者。

这一守卫要求 6 个 policy 值等于既定合同，例如 `source_geometry_immutable=True`、`crossing_is_connection=False`。[MappingRegistry.load:1216](E:/github-CAD2GIS/CAD2GIS/src/cad2gis/cad2gis_v3/config.py:1216) 目前检查的是字段集合及布尔类型，没有实施同样的值约束。

**处理顺序：** 先确定是否应在 source admission 恢复该守卫，并用反例验证非法 policy 被拒绝；然后删除不再需要的包装。这里只确认调用链缺口，不声称已经动态验证所有错误配置都会造成错误交付。

### C2. implementation 文件清单不能作为活代码清单

`PRODUCTION_CONVERSION_FILES` 是指纹范围，不是模块可达性分析结果。当前它既含旧 v3 CLI，又遗漏一些实际可达模块，例如 `family_validation`（semantics 引入）、`geometry_repairs`（decision_executor 引入）、`osm_anchor`（convert 引入）、`spatial_llm`（空间过滤的可选分支引入）。

清理时不能据此删掉未列入的文件。对指纹范围内的文件删函数会改变实现 SHA；移除/迁移文件还需更新清单和 scope version，保证 provenance 仍可生成。涉及模型/网络的可选调用应单独明确快照与输入边界。

### C3. 描述与忽略规则残留

- `pyproject.toml:9` 的发布 readme 仍称 backend separately deployed，后面的打包配置已经包含 bundled backend。
- `runtime.py` 模块说明与实际嵌套包路径不一致。
- `.gitignore` 的 `scripts/` 会让常规检索漏掉已跟踪 Pages 脚本与插件脚本；应缩窄范围或明确允许维护脚本。
- `*.bak` 不匹配 `.bak.2345`；历史备份清理后可补准确模式。
- `.omc` 中旧解耦/迁移方案只能当历史上下文，不能作为现行调用关系的证明。本轮不修改这些历史记录。

## 6. 已排除的误删项

| 模块/资产 | 保留原因 |
|---|---|
| 两个 pipeline | 外层负责公共配置/调用，内层负责转换执行，不是算法副本 |
| `__init__.__getattr__`、`__main__`、命令 handler | 动态导出或命令入口；普通静态调用次数会低估 |
| MCP 工具、HTTP routes | 装饰器/路由注册，不能按函数未被直接调用判断 |
| `plugins/.../scripts/cad2gis_mcp.py` | 文件真实存在，兼容启动器，测试有 runpy 消费者；常规 rg 会被 scripts ignore 误导 |
| `libredwg.py` / `libredwg_cli.py` / `autocad.py` | bindings、CLI、Windows 的不同 reader 能力和回退 |
| `_nearest_unique_linear`、`_NearestFeatureIndex` | 仍有运行分支及等价性测试；只候选删除无调用包装 |
| `curation*`、`curation_providers` | 独立离线 CLI、proposal/audit 及 provider 测试；provider 又被 onboarding 复用 |
| `osm_anchor.py`、`spatial_llm.py` | 函数内/条件分支导入；属于可选活代码 |
| `schema_config.py` | 大量 schema 数据经业务逻辑消费；无函数不等于无用途 |
| `stage_contract.py` | 阶段证据在 convert 中有 8 个实际 run 调用；预留 cache 字段不表示可复用缓存 |
| `gcp_workflow` | 有正式命令与外部 operator backend 扩展点 |
| `landing.*`、`install.*`、`hero-tube.js`、`pointer.*`、`workspace-shell.css` | Pages 构建中的真实资源 |
| `demo-data-*.json` | catalog 字段驱动动态加载，不能只查硬编码文件名 |
| 两个 Web builder | CI 与 Pages 当前分别调用；未来可合并，当前不能删 |
| `tools/diagnostics/*` | 多个有独立 CLI、AutoCAD 应用入口或文档命令；未被 import 不构成删除证据 |
| delivery equivalence / compare_runs | 前者精确比较交付行与几何，后者检查运行元数据/身份，目标不同 |
| `experiment`、当前 baselines、raw/official DWG | 源绑定兼容/验证数据；experiment 配置仍有测试读取 |
| 字体许可证、构建依赖 | 共享资源和生成脚本仍在使用，不以“运行时不调用”删掉开发依赖 |

## 7. 解耦方向与可选择的执行批次

建议的边界是：CLI/MCP/HTTP 适配层 → 项目用例 → 转换调度 → 纯阶段/reader/发布。优先保证 reader 只产出事实、语义阶段统一分类，避免重新形成两套转换算法。

1. **批次一：最小清理。** A1 + A2 + A3。候选规模为 1,089 行旧 reader、115 行私有函数实体、3 份历史 JSON；Python 合计约占当前生产 Python 物理行数的 2.23%。这些数字表示候选范围，不是承诺每一行都能无条件删除。
2. **批次二：reader/业务解耦。** B1；先迁移 axis-order helper，退役旧 AutoCAD GIS API，再移除 APD 单图规则。保留现行事实采集与平台回退。
3. **批次三：主流程契约整理。** 先处理 C1，再决定 B4/B5。将 v3 convert 中的 source admission、空间分区/过滤、classification、topology、registration、artifact publication 分清输入和副作用。现有 stage receipts 和交付等价检查可作为约束。
4. **批次四：可选能力归属。** 对 B2/B3/B8 明确“接入并测试”或“移出默认产品”。这属于产品/接口范围选择，不能只由删除行数决定。
5. **批次五：Web 去重。** B6 后 B7。共享程序与二进制资产，保留受控的公开 catalog 和发布 gate。

不建议把这五批混成一个大删除提交；reader 的退役与 Web 构建合并有不同回归证据，也应可以分别回滚。

## 8. 验证事实与实施后的门槛

本次实际完成：

- 148 个已跟踪 Python 文件 AST 解析通过；统计源码模块、导入边和低引用符号。
- 正式 CLI `--help` 退出码 0，14 个叶命令 handler 均可解析；帮助路径没有加载 osgeo、pyproj、shapely、mcp、fastapi。
- 轻量 backend discovery 成功定位当前内置 v3；检查到 editable fallback 的路径拼接问题。
- 独立旧 `dwg_extractor` 导入证实缺失 `cad_common`。
- 原始前端副本 SHA-256、JS 差异和构建消费者核对。

本机本次可用的 Python 缺少 pytest、pyproj、shapely、GDAL/OGR、MCP、FastAPI；**没有运行完整 pytest、真实 DWG 转换或浏览器交互回归**，也未安装/替换用户运行时。候选清单是静态审计加有限入口验证，不是删除后的等价性证明。

正式实施建议沿用仓库现有验证，不为简单删除编写镜像测试：

- A1/A2/B1：reader capabilities、跨 CAD 契约、native facts、CLI、portable runtime 相关测试。
- C1：补一个非法 policy 值的针对性反例测试，再跑 source geometry/topology gate。
- B4/B5：验证 console scripts、`python -m`、lazy import、外部路径部署和 wheel 安装入口。
- B6/B7：Web build、publication gate、多 demo、plugin guide、terminal replay，以及两个构建产物的实际页面。
- 触及转换行为/作用域时，用代表性 DWG 生成新 run，与原交付通过 `tools/verify_delivery_equivalence.py` 比较 schema、行、二进制几何、标签、长度和 lineage；实现指纹变化本身不能被误判为交付差异。

附录模块清单用于后续逐模块认领。AST 根不可达只能作为筛查结果，不能代替本报告的 A/B/C 分级。

## 附录：83 个生产 Python 模块覆盖索引

“可达”包含函数内、条件分支及已识别模块名字符串路径，表示存在静态联系，不表示本次实际执行。模块可达也不表示文件内每一个函数都可达。空包文件计入统计。

| 模块（相对 src/cad2gis） | 物理行数 | 入口/审计状态 |
|---|---:|---|
| `__init__.py` | 49 | 公共 API 懒导出 |
| `__main__.py` | 10 | python -m 入口 |
| `agent_mcp.py` | 1,308 | 主入口可达；具体候选见正文 |
| `apd_rules.py` | 149 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/__init__.py` | 24 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/accounting.py` | 98 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/artifact_io.py` | 73 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/cable_legend.py` | 141 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/cad_scene_graph.py` | 621 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/calibration.py` | 1,925 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/cli.py` | 45 | 独立旧 CLI（B4） |
| `cad2gis_v3/config.py` | 1,314 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/coordinate_domain.py` | 126 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/curation.py` | 1,309 | 独立 curation/测试链，保留 |
| `cad2gis_v3/curation_cli.py` | 138 | 独立离线 CLI，保留 |
| `cad2gis_v3/curation_provenance.py` | 34 | 独立 curation/测试链，保留 |
| `cad2gis_v3/curation_providers/__init__.py` | 22 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/curation_providers/base.py` | 54 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/curation_providers/config.py` | 163 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/curation_providers/openai_compatible.py` | 142 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/curation_service.py` | 103 | 独立 curation/测试链，保留 |
| `cad2gis_v3/curve_geometry.py` | 762 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/decision_executor.py` | 425 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/decision_validation.py` | 543 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/evidence.py` | 1,058 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/evidence_graph.py` | 465 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/evidence_index.py` | 325 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/family_validation.py` | 458 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/geodata.py` | 115 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/geometry_repairs.py` | 704 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/georef.py` | 906 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/gpkg_metadata.py` | 104 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/implementation.py` | 458 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/ingest.py` | 154 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/iteration.py` | 1,091 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/label_candidates.py` | 205 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/legend_detector.py` | 668 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/model.py` | 389 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/onboarding.py` | 1,703 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/osm_anchor.py` | 509 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/pipeline.py` | 2,506 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/plan_domain.py` | 886 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/ports.py` | 747 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/project_profile.py` | 1,027 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/repair_decisions.py` | 422 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/run_status.py` | 239 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/runtime_provenance.py` | 297 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/scene_partition.py` | 546 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/schema_config.py` | 2,662 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/semantic_anchor.py` | 1,580 | 未接入公开框架（B2） |
| `cad2gis_v3/semantics.py` | 2,562 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/source_dependencies.py` | 83 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/source_gpkg.py` | 664 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/spatial_coverage.py` | 685 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/spatial_filter.py` | 1,078 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/spatial_llm.py` | 496 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/stage_contract.py` | 128 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/styles.py` | 618 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/topology.py` | 1,472 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/units.py` | 414 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/visual_evidence.py` | 425 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/warehouse.py` | 914 | 主入口可达；具体候选见正文 |
| `cad2gis_v3/znro_shape.py` | 248 | 主入口可达；具体候选见正文 |
| `cli.py` | 808 | 主入口可达；具体候选见正文 |
| `contracts.py` | 87 | 主入口可达；具体候选见正文 |
| `doctor.py` | 410 | 主入口可达；具体候选见正文 |
| `gcp_workflow.py` | 1,266 | 主入口可达；具体候选见正文 |
| `ingest.py` | 26 | 公开包装入口（B4；局部 A2） |
| `native_runtime.py` | 405 | 主入口可达；具体候选见正文 |
| `pipeline.py` | 378 | 主入口可达；具体候选见正文 |
| `profile.py` | 11 | 公开兼容门面（B4） |
| `reader/autocad.py` | 3,836 | 主入口可达；具体候选见正文 |
| `reader/contracts.py` | 52 | 主入口可达；具体候选见正文 |
| `reader/dwg_extractor.py` | 1,089 | 孤立/失效（A1） |
| `reader/libredwg.py` | 1,898 | 主入口可达；具体候选见正文 |
| `reader/libredwg_cli.py` | 832 | 主入口可达；具体候选见正文 |
| `reader/records_adapter.py` | 70 | 未接入/协议待定（B3） |
| `reader/resolver.py` | 131 | 主入口可达；具体候选见正文 |
| `review_server.py` | 1,658 | 主入口可达；具体候选见正文 |
| `runtime.py` | 299 | 主入口可达；具体候选见正文 |
| `verify/__init__.py` | 50 | 主入口可达；具体候选见正文 |
| `verify/claims.py` | 164 | 主入口可达；具体候选见正文 |
| `verify/matrix.py` | 990 | 主入口可达；具体候选见正文 |
