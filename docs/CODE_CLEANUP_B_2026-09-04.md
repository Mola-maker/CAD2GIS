# CAD2GIS B 类解耦与清理执行记录

日期：2026-09-04。基于 A 批次已推送的 `3f3717bb80783a5047209ad9b0e1f809d86a31d5`，按用户“继续进行 B 的改动，这部分涉及到真正有用的源码，务必谨慎”的要求实施。本记录覆盖 B 类改动及提交前验证；用户随后授权提交与推送。用户原有 `src/cad2gis/cli.py` 的两处注释修改保留在工作区，不纳入本批提交。

## 1. 分项结果

| 候选 | 本轮处理 | 保留的能力与边界 |
|---|---|---|
| B1：AutoCAD 旧 GIS 分类/导出 | 将 9 个函数迁入 `legacy/autocad_conversion.py`，将 APD 单图规则迁入 `legacy/apd_rules.py`；坐标轴顺序辅助函数迁入 `coordinate_runtime.py` | `reader.autocad` 的两个公开入口保留原签名并延迟转发；`cad2gis.apd_rules` 保留兼容导出；当前 records 提取、Core Console/COM 回退、原生曲线和属性事实不改算法 |
| B2：semantic_anchor | 保留模块及公共 API，只明确其为尚未接入当前 CLI/MCP/生产流水线的实验框架 | 不删除有潜在用途的算法，也不把它接入权威转换 |
| B3：records_adapter | 保留真实 bundle 完整性和来源校验；`load_records` 明确抛出 `NotImplementedError`，撤回“可从 review bundle 重放完整原始记录”的错误承诺 | 不再将提案 facts 伪装为完整 `SourceEntity`；校验返回值明确 `conversion_import_allowed=False` |
| B4：旧 v3 CLI 与兼容门面 | `python -m cad2gis.cad2gis_v3.cli` 转发正式 `cad2gis convert --json`，消除另一份转换调度 | 旧完整参数名和成功 JSON 字段保留；`ingest.py`、`profile.py`、公共 pipeline API 保留 |
| B5：后端部署边界 | 修复 editable fallback 把 `src/cad2gis` 当成 import root、继而重复拼出 `cad2gis/cad2gis` 的问题；统一路径拼接说明 | 保留 `CAD2GIS_BACKEND_PATH`、`backend_location`、旧 `profile_builder` fallback；本轮不重写外部后端加载优先级和模块选择策略 |
| B6：Web 重复代码与静态资源 | 两套页面共用 `app.js`、`demo-fixture.js`；页面差异移入各自内嵌 JSON 配置；删除 15 个逐字节相同的资产副本 | 本地 10 案例、Pages 1 公开案例保持独立；地图中心、缩放控件、缓存版本和原始 hero 的差异保留；字体许可证随两套构建产物继续交付 |
| B7：original hero | 保留 | 独立页面仍有消费者，交互与页面结构存在真实差异，不能按“重复”删除 |
| B8：alpha shape 与 coverage 公共辅助能力 | 保留实现，只纠正过时注释/说明 | 生产路径使用的保守 coverage 构造不变；实验/reference alpha 路径和可选源顶点辅助函数继续可用 |

本轮主要收益是 reader 与旧业务分类解耦、入口调度收敛、Web 实现共享。搬移的 Python 函数仍随 wheel 发布，不能把它们计作已删除的无用代码。

## 2. 需要明确的行为变化

### Review bundle 不再被误认为可重放的源记录

当前 review bundle 是 proposal-only 协议，明确禁止权威转换导入，也不携带完整坐标载荷。其 `facts` 不是 `SourceEntity.from_record` 所需的完整记录；原适配器可能由此生成空 points、缺失身份或默认 centroid。

现在 `load_records(bundle_path)` 保留可导入的函数和签名，但调用时明确报“尚未支持重放”。`validate_bundle_facts` 继续使用现有 curation 校验链检查 schema、hash、policy、引用及 source/profile 绑定。真实 DWG 转换继续通过正式 reader ingest。新增测试使用合法、非空的现行 v2 bundle，并覆盖篡改哈希及来源不匹配。

### 旧 CLI 使用正式转换准入与错误输出

旧入口的 `--input`、`--run-dir`、`--source-profile`、`--mapping-registry`、`--gcp-profile` 仍被接受。成功结果保留 evidence、delivery、styles、manifest、counts、topology，仍排除 `connection_port_candidates`。

旧入口现在经过正式公共 pipeline 的路径/配置校验，失败使用统一的机器可读错误和退出码；例如缺失 DWG 返回 `SOURCE_NOT_FOUND`，而非绕过公共入口直接进入 v3。帮助路径继续延迟导入，不要求 GIS 原生运行时先可用。

### 实现指纹

生产转换 scope version 从 7 升到 8，显式清单中的 `apd_rules.py` 替换为实际使用的 `coordinate_runtime.py`，清单仍为 50 个文件。旧业务规则迁入的 `legacy/` 不属于正式转换主链。由本轮文件内容和作用域变化引起的指纹变化是预期结果，不能单独解释为几何交付变化。

## 3. 等价性和回归证据

| 验证 | 结果 |
|---|---|
| B1 迁移 AST 与 A 批次提交比较 | 9 个转换函数在还原 `_reader.` 限定引用后与原实现相同；5 个 APD 业务函数及坐标轴辅助逻辑不变；reader 的 79 个共享函数/类不变 |
| B1 旧版源码/新版执行结果比较 | 10 个场景返回值完全相同：空记录、混合几何/标签/证据、标注冲突与并列、退化几何、类型过滤、model/plan 去重、Core Console 和 COM stub 路径 |
| B1 公开签名 | 两个旧转换入口及 APD/坐标辅助函数共 8 个签名保持一致；共享 transport 的 reader monkeypatch 仍生效 |
| B2/B8 算法保留 | 去除 docstring 后，semantic_anchor、znro_shape、spatial_coverage、semantics 的 AST 与基准相同 |
| 完整 pytest，包含 MCP stdio 与 review server | **402 passed，10 skipped，4 warnings**；最终运行 14.40 秒 |
| Pages 发布检查所用测试集合 | 16 passed；包含新增共享 JS 行为/资源依赖测试 |
| Ruff 0.12.12 | `src tests tools scripts/build_pages.py plugins/cad2gis-agent/scripts` 全部通过 |
| JS 语法 | 共享 app、fixture 及 original 兼容入口通过 `node --check` |
| Web 构建前后比较 | Pages 35 个文件、本地 WebDemo 33 个文件，路径集合不变；各自只有 app.js、demo-fixture.js、index.html 内容变化，其余文件逐字节相同 |
| 15 个删除资产副本 | 每个副本与保留资产的 Git blob/文件哈希完全相同；合计 366,427 字节；包含 6 字体、4 SVG、1 元数据、4 许可证 |
| 浏览器冒烟验证 | Pages 加载 1 案例和 8 图层、控制台切换成功；本地加载 10 案例，Hutabohu → Lamteh Main 切换后目录、源文件及图层更新正确；两页浏览器脚本 error 日志为空 |
| 独立代码审查 | 后端与 Web 分别审查；最终无遗留 Critical / Important / Minor |

Pages 独立发布检查原本只检查旧 `original-demo/assets/app.js`。该文件现在是导入入口壳，语法检查不会自动检查依赖。已根据审查意见把共享 app/fixture 的语法检查及新增共享资产测试加入 `.github/workflows/pages.yml`，并将该测试纳入发布触发路径。

新增测试文件：

- `tests/test_legacy_conversion_compatibility.py`：旧公开入口、延迟加载、共享 transport 与旧业务结果。
- `tests/test_legacy_cli.py`：旧参数和成功结果、正式错误契约、无 GIS 帮助入口、真实 editable root。
- `tests/test_records_adapter_contract.py`：合法 bundle 的校验与拒绝误重放、哈希/来源不匹配。
- `tests/test_webdemo_shared_assets.py`：执行共享 JS 初始化和请求，核对地图参数、10/1 案例边界、回退与项目状态隔离、资源引用和许可证。

## 4. 安装包验证

最终包：`tmp/b-cleanup-wheel/cad2gis-0.3.0-py3-none-any.whl`。

SHA-256：`db3a6d20b3f08da8db851fbe40109cab7b8a229dbeb29d77a7c7c5e891daf9ca`。

- 对比 A 批次 wheel，没有丢失已打包路径，新增 `coordinate_runtime.py` 及三个 `legacy/` 文件。
- 从 wheel 解压目录独立启动正式 CLI 和旧 v3 CLI 的 `--help`，退出码均为 0；导入路径确实位于该包内。
- 旧 API 可导入，普通 reader 导入不会提前加载 legacy 分类模块，也不会导入 osgeo、pyproj、shapely、FastAPI 或 MCP。
- 共享配置、脚本、6 个字体和 4 份许可证均在包内，核对过的源码与工作区内容一致。
- `original-demo` 在 A/B 两个 wheel 中均不打包；它仍作为仓库 Pages 构建模板使用，产物另行验证。
- 包内 production provenance 成功生成，scope version 8、50 文件；implementation SHA-256 为 `e040fe89440c7a345e13dac3e72d1f735b80067faa8aba8b1ab676168bebf963`。

本次只构建一次最终 wheel，未声称两次构建的二进制可复现性。构建使用当前工作区，包括用户先前已存在的 CLI 注释。

## 5. 验证环境与保留限制

验证使用 Python 3.12.13、项目固定的 `pyramids-gis==0.58.1` GDAL/OGR，以及临时目录中的 pytest/Ruff/FastAPI/MCP 依赖。依赖仅位于忽略的 `tmp/code-cleanup-validation-deps`、`tmp/code-cleanup-gis-deps`、`tmp/b-cleanup-validation-extras`，没有替换系统 Python 或已有 CAD2GIS 安装。

最终完整测试显式设 `CAD2GIS_FULL_DWG_TESTS=0`，先激活 `ensure_osgeo_runtime()`，然后在同一进程调用 pytest。临时 `--target` 安装的 pywin32 需要把其 `win32`、`win32/lib`、`pywin32_system32` 加入搜索路径；补齐后 MCP stdio 测试通过。4 条 warning 来自 FastAPI/Starlette/AnyIO 及既有 sqlite3.version 弃用提示。

剩余 10 项跳过均因外部数据缺失：6 项 APD_test 外部样本、4 项可选 Hutabohu evidence baseline。没有执行真实 AutoCAD/Core Console/LibreDWG DWG 提取或新旧 DWG 交付文件等价性比较；B1 的 transport 场景使用 stub。现有测试和静态等价性不能替代这些外部样本验证。

外部后端部署机制只修正明确的 editable 路径错误，未对独立部署的旧后端做端到端运行验证。旧公开函数调用和共享 transport monkeypatch 保留；重新绑定旧模块的私有 helper 或 APD 名字不会自动重绑定已迁移函数的全局命名空间，不作为本轮兼容承诺。

C 类失联策略守卫等问题不属于本轮 B 类改动，仍需单独修复与验证。

本机补充证据保存在忽略目录中：`tmp/code-cleanup-b1-compatibility.json`、`tmp/b6-artifact-comparison.json`、`tmp/b-cleanup-wheel-validation.json`。长期维护以本记录及仓库测试为准。
