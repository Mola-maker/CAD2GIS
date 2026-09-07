# 批量转换与多图纸交付契约

2026-09-06。`cad2gis batch` 随 Python 包和 Docker 安装，Windows / Linux 使用相同入口。批次目录自动化不等于自动批准图纸语义或测量精度。

## 目录与执行

```sh
cad2gis batch prepare --inputs /data/inputs --output /data/inputs/batch.json
# 为每个 id 准备并审查 projects/<id>，保留 source_profile、mapping_registry 和来源绑定。
cad2gis batch run --contract /data/inputs/batch.json --output /data/results/attempt-001
cad2gis batch package --run-dir /data/results/attempt-001/drawing-id/run --output /data/delivery-new
```

`prepare` 递归发现 DWG/DXF，ID 按相对路径生成，源内容另以 SHA256 绑定。它不会伪造已审查项目。输入清单必须置于输入根目录，项目路径和源路径均为根内相对路径；拒绝重复 ID、路径越界和不合法哈希。输出必须是新目录，重试写新的 attempt，保留失败历史。

```json
{
  "schema_version": "cad2gis.batch.v1",
  "drawings": [{
    "id": "drawing-01",
    "source": "sources/example.dwg",
    "source_sha256": "64 lowercase hexadecimal characters",
    "project": "projects/drawing-01"
  }]
}
```

每图顺序执行源事实提取、审查配置重放、独立视觉比较和交付打包。`batch-report.json` 持续记录当前图和阶段；各阶段有独立日志。单图错误不会阻止后续图纸尝试。子进程超时按阶段限制，默认 1800 秒。转换失败保留事实快照与日志，不以旧成果替代成功。

```text
attempt-001/
  batch-contract.json
  batch-report.json
  index.html
  drawing-01/
    source/                 # 原始读者事实、索引输入；供 AI 查询
    run/                    # source.gpkg / evidence.gpkg / delivery.gpkg / run_manifest.json
    visual/                 # PNG、几何逐项 CSV、字段来源 CSV、report.json
    delivery/               # 可搬移 QGIS 项目、数据库、样式、字段与校验清单
    delivery.zip
    source-export.log
    conversion.log
    visual-audit.log
    result.json
```

独立视觉核验器安装于 `cad2gis.visual_audit`，无需仓库 tools 目录。它比较源坐标下的几何、字段及来源关系，支持范围有限的 CRS/GEODATA 逆变换；不支持的变换明确写 `SKIPPED_UNSUPPORTED_TRANSFORM`。`EXECUTED` 仅表示核验程序执行，报告中的差异仍需判断，不能理解成工程验收通过。

## 交付文件

完整解压 ZIP 后打开 `delivery.qgz`。QGZ 使用同目录 `delivery.gpkg` 相对路径，保留全部图层；独立分区各有自己的项目。QGIS 项目不嵌入数据库，不能只复制单个 QGZ。GeoPackage 本身是 SQLite，无须改成 `.db`。

每包包含：原字节 GPKG、QGZ、每层 QML、全部非几何属性 CSV、字段类型和缺失标签计数、源 DWG 哈希、原运行清单哈希、完整文件校验清单及 README。已准备的九图历史包另含逐项核验、叠图和转换阶段证据。打包先核对原运行清单中的 GPKG SHA256，再执行 SQLite quick_check。

CABLE 的显示表达式以 `length_value_m` 为值，显示 `m [CAD]` 或 `m [GEOM]`；前者对应 `dwg_dimension`，后者对应 `dwg_curve_geometry`，未知来源显示 `?`。`length_source`、`LONGUEUR`、`delivery_grid_length_m` 与 `geodesic_length_m` 均保留，不能把它们当作相同测量。点标签来自 `display_label`；空值保留并统计，不生成猜测标签。字体描边与长度表达式只改变 QML/QGZ 显示，不修改原数据库。

## 六项能力边界

源快照复用已接入 batch：export-source 后的转换直接使用同一 source 快照。每图可增加 `"geometry_repairs":"candidate-only"`，把三类历史自动几何修复保留为未应用候选；失败仍有独立候选报告，成功 ZIP 包含其 SHA 绑定文件。默认 legacy 保持历史审定行为。语义 revision 的规范交付入口、具体支持范围和实图证据见 [源快照与语义交付](SOURCE_REPLAY_SEMANTIC_DELIVERY_2026-09-06.md)。

| 能力 | 契约与边界 |
| --- | --- |
| 固定成果格式 | canonical run 格式保留，新增 batch 报告、相对路径 HTML、QGZ/CSV/ZIP；分区独立列出 |
| AI 高效修改 | 使用原始 source/evidence 与审查项目，先查询索引、取有界上下文，再通过已有 typed semantic proposal/apply 生成新候选；不可把公开交付 ZIP 当完整可重放项目 |
| 数据库读取 | SQLite FTS5/RTree 和来源索引继续使用；只读连接不要求 Python 必须编译扩展加载接口。冷建索引与热查询分别衡量，HTML 大小不能代表 SQL 性能 |
| 多图 HTML | 所有输入均出现在总览，包括失败；执行成功后连接交付清单和视觉报告，不隐藏空字段或部分失败 |
| 非特定模型 | CLI 与既有 MCP 契约不限定模型厂商。批次入口当前是 CLI/API，未宣称新增远程 MCP 批次工具；任何能调用受控 shell/SSH 的客户端均可执行同一命令 |
| 服务器运行 | `CAD2GIS_READER_BACKEND=libredwg` 强制无 AutoCAD。MCP 仍仅监听回环地址，SSH 在服务器执行同一 CLI 或转发本机端点。公开 Pages 是静态历史演示，不接收生产写入 |

原读者为 AutoCAD 的图纸在 Linux LibreDWG 下可能有实体集合、MTEXT 或 inventory hash 差异；应保留失败和新事实，重新审查绑定。禁止直接删除库存哈希门禁或把 Windows 成果冒充 Linux 复跑。

## 两位小数与可选符号资产

GeoPackage 的 REAL 和几何坐标继续保留全精度。交付 QML/QGZ 的数值字段显示两位，CSV 浮点值也显示两位，
因此 CSV 是查看和汇总产物，不适合作为无损回导来源。CABLE 标签使用原 CAD 尺寸或原 CAD 曲线长度，明确标出来源。

SVG 提取、SQLite 符号资产库和单符号 QML 导出是[独立可选入口](OPTIONAL_SYMBOL_ASSETS.md)，不由 batch 自动调用，
不静默替换任何历史符号。默认交付不依赖 `symbols.sqlite3`。

## 九图历史 Pages

`pages-delivery/nine-drawings` 现在是被 Git 忽略的下载缓存。用户授权的派生成果与过程保存在独立 GitHub Release，`docs/derived-release.json` 固定 URL、大小和 SHA256；`scripts/fetch_derived_release.py` 先下载校验再发布完整缓存。`publication.json` 对每个文件绑定哈希，Pages 构建器再次验证后合并九图与旧 Hutabohu 演示。不包含原 DWG。

公开图始终保留 CONDITIONAL 与“绝对 GCP 精度未验收”。左视图是交付坐标几何；右视图通过原目标 CRS 真正重投影为 EPSG:4326，再由地图渲染器显示。不能将非 3857 坐标直接当作 Web Mercator。原图与成果的差异通过独立叠图和 CSV 查看。

## 已执行验证

Windows 本地回归：590 passed / 6 skipped；跳过包含未启用的复杂 DWG 测试，不能替代九图复跑。实际 QGIS 4.0.3 已打开搬移后的 11 份项目，核对图层数量、CRS、标签表达式并渲染。浏览器实际切换九图，主成果数量依次为 56、1000、375、715、673、515、28、263、40。Linux 现场结果另记录，未完成时不得据此宣称 Linux 通过。

QGIS 序列化参考：[项目读取源码](https://github.com/qgis/QGIS/blob/master/src/core/project/qgsproject.cpp)、[CRS 读取源码](https://github.com/qgis/QGIS/blob/master/src/core/proj/qgscoordinatereferencesystem.cpp)。项目必须包含 SpatialRefSys/ProjectionsEnabled，图层必须显式写 SRS；仅有 WKT 片段不足以保证打开时启用坐标系。

## 2026-09-06 复跑结论与未关闭边界

- WSL 已迁至 `D:\WSL\podman-machine-default`，VHDX 迁移前后 SHA256 相同。Linux 使用 Python 3.12 / LibreDWG 0.14，无 AutoCAD。
- 九张主图和 Manado 两分区已生成独立审查和交付；所有 11 份 Linux QGZ 均被实际 QGIS 打开、渲染。批次 ZIP 自动包含 visual 子目录（分区也包括在内），可查看源实体去向和逐字段审查。
- Windows 全套 604 passed / 7 skipped，独立真实 AutoCAD 属性边界回归 1 passed；Linux 安装包聚焦回归 40 passed。最终交付包补入 visual 后再执行相应打包回归。
- 已安装包的 MCP 46 工具与协议握手、debug_mcp、SQLite 查询实测通过。热查询中位数 6.94 ms，5 实体上下文约 9.01 ms；新进程首次查询约 2.57 s，不能把热读耗时当作冷启动耗时。
- CLI/MCP 默认将 OpenBLAS/OMP/MKL 数值线程限制为 1，显式环境设置优先；本机高内存压力曾导致 OpenBLAS 分配失败和 MCP 断连。WSL 历史 Machine Check 的硬件根因仍未确认。
- 历史包保留，并加入 `ERRATA.md`。Manado 两分区 20/38 → 14/26、Semarang 40 → 39 的 19 个 BOITE，均追溯到旧 AutoCAD 无属性块串入后续属性的问题。不能填造属性恢复旧数量。
- 下一步架构仍需实现：复用一次源提取快照；将 typed semantic compile 与正式交付编排连接；将历史几何吸附/桥接拆为可拒绝的独立修复候选。这些不属于已经关闭的修复。
- 详细状态见 `docs/VALIDATION_ISSUES_2026-09-06.md`。工程 GCP、数字标签含义、无属性块设备身份及既有修复接受仍待真实证据。
