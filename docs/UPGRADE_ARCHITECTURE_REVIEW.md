# CAD2GIS 0.6.0 架构复查

审查日期：2026-09-09。范围：共享升级工作树（开始复查时 HEAD 为 `9df68cb04a21fba03c2f064458f1f43b3c561dd0`）的跨阶段契约、隐私默认、版本与打包链路。本文是专项 Code Review，不把静态检查写成平台安装验收，也不把 OSM 叠图写成实测绝对精度。

本轮发现并交给对应实现者的关键问题是：已有路径绕过产物 SHA-256 校验、新匹配模块漏入 implementation fingerprint、Windows 升级遗留旧 metadata，以及真实图审查默认请求在线底图。前三项已完成代码修复并复查；在线底图默认关闭已落入代码。实际浏览器交互与网络请求尚未验证。

## 覆盖范围与实际边界

| 阶段 | 本次读取的主要实现 | 已有契约及限制 |
|---|---|---|
| Source | `reader/libredwg_cli.py`、`source_export.py`、`source_gpkg.py`、`source_dependencies.py` | 源 DWG 哈希、source snapshot、reader accounting 与外部引用门禁；完整外部 XREF 尚不能由当前单文件 reader 自动恢复。 |
| Semantic | `semantic_stage.py`、`semantic_store.py`、`semantic_delivery.py` | SQL candidate index、绑定源快照的 ID patch、revision CAS、编译 job generation、发布产物哈希；现有 delivery bridge 支持已有要素的标签和兼容分类确认。新增/删除要素、替换量测值仍明确拒绝，不能描述为任意 AI 改图。 |
| Conversion | `pipeline.py`、`stage_contract.py`、`implementation.py`、`runtime_provenance.py`、`run_status.py` | stage receipt 记录完整输入/输出指纹；`cacheable=False`，没有启用 stage-result cache。源事实、交付样式、运行环境分别留有证据。 |
| Registration | `osm_matching.py`、`osm_evidence.py`、`georef.py` 的接口及调用处 | OSM 相对匹配与 GCP 独立验收属于不同证据来源；中间层保存矢量/拓扑/残差证据，比从渲染瓦片倒推道路更易追溯。具体数值算法和 DWG 复现由专项报告覆盖。 |
| Delivery → Review | `delivery.py`、`qgis_package.py`、`qgis_review.py`、`review_server.py`、`application.py` | 打包校验 GeoPackage 哈希与 SQLite integrity；review workspace 独立于 source/evidence/delivery。发现并修复首次打开主 run 时的哈希绕过。 |
| Release / Privacy | Python/npm/plugin/Android metadata、Linux/Windows/Android builder、Docker workflow、`desktop_settings.py`、Provider adapter、WebReview 初始化 | 平台版本正在统一为 0.6.0；Python conversion 的 LLM 默认 `off`，配置 AI 后的调用与在线 OSM 仍可能联网，不能声称整个软件永不出网。 |

## Findings 与处理状态

严重性含义：P1 会破坏来源可信度、误用旧产物或导致升级身份错误；P2 会造成发布不一致、默认行为与界面预期不符。没有在本次只读审查中发现并确认 P0。

| ID | 严重性 | 已复核的问题及影响 | 处理状态 / 验证边界 |
|---|---|---|---|
| ARCH-01 | P1 | `review_server._artifact_path()` 只验证 relocated 路径；原 manifest 路径只要存在就直接返回。首次打开主 run 与 native evidence 查询可读取损坏或旧产物，却继续展示原 run 的状态。 | 已修：所有候选都要求有效 SHA-256，优先当前 run 的 canonical 文件；当前文件存在但哈希错时阻止，不回退旧路径。独立最小复现从“错误哈希仍返回”变为抛出 `ReviewServerError`。端到端回归由 GCP owner 执行。 |
| ARCH-02 | P1 | `osm_matching.py` 使用新增 `osm_evidence.py`，但 production allow-list 初始漏掉该模块。改变道路 segment/index 算法可能不改变 implementation identity。 | 已修：登记 `cad2gis_v3/osm_evidence.py`，scope 由 11 升为 12。直接调用 `production_conversion_provenance()` 确认新文件在 fingerprint 内。 |
| ARCH-03 | P1 | Windows 安装脚本只删除 `cad2gis-0.4.0.dist-info`；0.5 → 0.6 可残留 0.5 metadata，导致运行身份与新代码不一致。 | 已修：删除范围严格限制为安装目录内 `cad2gis-*.dist-info`，不扩展到用户项目；AppVersion 改为 builder 必填。Windows 实际升级安装仍需平台验收。 |
| ARCH-04 | P2 | Android APK 文件名/receipt、Linux 默认版本、Docker tag、partial-source fallback 存在 0.4.0 硬编码。会产出名为旧版的新程序，或使总清单找不到文件。 | 已修：Android/Linux/Docker 从 `pyproject.toml` 取版本；Android 同步 versionName/versionCode；partial source 无法判断时返回 `0+unknown`。历史版本文档不应批量改写。各平台成品内容仍需 build 验证。 |
| ARCH-05 | P2 | 真实 WebReview 无条件实例化 `ol.source.OSM()`；打开地图即向第三方请求当前视野瓦片，与第一次使用者对本地审查的预期不符。 | 默认初始化已改成无 source、不可见的 tile layer；在线底图通过明确开关加载，旁边说明视野请求。公开 demo 可保留在线显示。已复查实现；实际浏览器按需启用、关闭和请求行为尚未验收。 |
| ARCH-06 | P2 | 应用响应统一 `Referrer-Policy: no-referrer`，显式启用在线 OSM 后也会影响该服务要求的浏览器 Referer。 | 已修并复查代码：`setOnlineBasemap` 的按需 OSM source 仅为 tile image 设置 `referrerPolicy="strict-origin"`；未修改应用全局策略，避免带出 run 路径/query。实际请求中的 Referer 尚未捕获验证。 |

ARCH-01 的复现只读取现有文件，未修改任何用户 CAD 或交付产物：

```python
from pathlib import Path
from cad2gis.review_server import _artifact_path, ReviewServerError

existing = Path("pyproject.toml").resolve()
manifest = {"artifacts": {"delivery": {
    "path": str(existing), "sha256": "0" * 64,
}}}
# 修复前正常返回 existing；修复后必须抛 ReviewServerError。
_artifact_path(manifest, "delivery", run_dir=Path.cwd())
```

## 隐私与精度的可陈述范围

- 确定性 conversion API 的 LLM 默认关闭；Provider 请求有大小上限，错误信息不插入 API key。Windows 密钥使用当前用户 DPAPI；Linux 保存为当前用户目录下的 `0600` 配置文件，不能把文件权限描述为加密。
- `application.py` 对宿主、Origin、登录会话与选中的 review ID 有校验；单独的 `run_review_server()` 只允许 loopback。它不是独立远程认证服务；已有 Desktop 包装层提供相应会话控制。
- QGIS review 的在线 XYZ 层原本就默认隐藏；本轮让真实 WebReview 的初始底图策略与它一致。OSM matching、地名检索和远程 AI 是另外的出网入口，开启功能时必须仍以实际 provider / 数据来源为准。
- Portable delivery 排除原始 DWG 与完整 source/evidence 数据库，但仍包含派生几何、属性和预览；“不包含 DWG”不等于“没有项目数据”。
- `run_status` 已将尚无独立 coordinate-accuracy 验收的情况保留为 `CONDITIONAL`；delivery/QGIS receipt 也明确 `absolute_accuracy_verified=False`。本轮不能把训练点拟合残差、OSM 视觉重合或更小的微调步长升级为测量精度证明。

## 尚未由本报告验证

1. 九份 DWG 的完整 reader → conversion → QGIS 渲染结果，以及每张图的残差/丢失实体数量。该验证由主任务 runtime 与 DWG 作业完成，结果应保留逐图状态，不用总测试通过数代替。
2. Windows 0.5 → 0.6 实机升级、Linux `.deb` 安装、Android 签名安装、实际远程 CI 和发布。这里只复查实现与配置，没有运行这些平台。
3. 不同测区、重复道路格网、桥梁/高架、局部非刚性变形的匹配表现；没有独立 surveyed checkpoints 时不能评估绝对位置误差。
4. 全库所有文件的逐行审查。本专项使用跨模块检索加关键契约读取；geometry、GCP、UI、Agent/CI 的实现和回归由对应子任务记录，本报告不冒称已经执行它们的全部测试。
5. 实际 UI 浏览器交互、视觉截图和网络请求。HTTP 首次导航挂起，file URL 又被浏览器安全机制阻止，未完成此项验收。Node 检查、8 项 build/reconnect 测试及后端 GCP 测试通过，仅覆盖相应代码路径。

## 后续三项可执行工作

1. **把来源校验做成一个共享 artifact resolver。** 统一 review、semantic、打包和 audit 的相对路径解析、哈希检查与 run 身份；新增 moved-run / stale-original / tampered-main / wrong-partition 四类契约样本。验收标准是任一入口都不能把哈希不一致的文件加载为已记录产物，而不是仅某个 UI 报错。
2. **建立来源绑定的 registration benchmark。** 为九张 DWG 保存 CAD/OSM 向量拓扑、人工确认同名点、独立 checkpoints 和旧/新算法 receipt；分别报告覆盖率、check RMSE/p95、abstention、曲线最大弦高误差及 reader accounting。先确认错误来自 OCS/WCS、单位/CRS、拓扑对应还是 source 提取，再决定是否引入局部分区模型；不把大残差用任意 rubber-sheet 隐藏。
3. **建立跨平台 release matrix 与迁移门禁。** 单一版本输入驱动 wheel/plugin/EXE/deb/APK/Docker manifest；每个平台校验安装后 identity、唯一 dist-info、runtime 可执行权限和离线开图。CI 输出实际构建平台与每个文件 SHA-256，未构建平台不放入“已验证安装包”清单。

## 核验用 Primary Sources

- [Python Core Metadata specification](https://packaging.python.org/specifications/core-metadata/)：发行包的版本与元数据契约；实现细节仍以本仓库 builder 为准。
- [QGIS Python plugin structure](https://docs.qgis.org/latest/en/docs/pyqgis_developer_cookbook/plugins/plugins.html)：plugin metadata 与兼容声明；不能仅改 version 就声称新平台兼容。
- [Inno Setup InstallDelete reference](https://jrsoftware.org/ishelp/topic_installdeletesection.htm)：安装期间删除条目的定义；本轮清理仅限定 CAD2GIS 自身旧 metadata。
- [OpenStreetMap Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/)：浏览器 Referer、attribution、缓存及在线使用要求；这里不建议从公共 tile 服务批量抓取离线地图。
- [IOGP / EPSG Guidance Notes](https://iogp.georepository.com/guidance-notes.html)：坐标转换模型与参数约定。模型成立和实际测量精度是两个需分别验证的问题。
