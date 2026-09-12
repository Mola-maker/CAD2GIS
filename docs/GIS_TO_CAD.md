# GIS → CAD 逆向转换

本地 Python API、CLI 和 MCP 将 GeoPackage / GeoJSON 转成新的 CAD 图纸。
输入只读，输出为新目录中的 `drawing.dxf`、`attributes.jsonl` 和 `manifest.json`。
读取使用 SQLite、Shapely 和 pyproj，写出使用现有依赖 ezdxf，不要求安装 GDAL、
QGIS 或 AutoCAD。可选的 DWG R2018 输出需要 Windows AutoCAD Core Console；
程序每次写出后都重新读取 DWG，将其内容与输入 GIS 比较，未通过则不发布。

这是 GIS 几何的 CAD 交付路径。它不等同于恢复源 DWG：原始块定义、动态块、
DIMENSION、圆弧/样条参数、纸空间布局、原实体句柄和 QGIS 渲染器不能仅凭 GIS
坐标重建。系统不会把 GIS 修改回写源 CAD。

## 使用

在包含本改动的 checkout 中安装：

```shell
python -m pip install -e .
cad2gis reverse-inspect "delivery.gpkg" --json
cad2gis reverse-convert "delivery.gpkg" --run-dir "cad-output" --json
cad2gis reverse-verify "cad-output" --json
```

输入是相同投影坐标系的 GeoPackage 图层时，默认沿用该 CRS。
GeoJSON 未声明旧式 `crs` 时按 RFC 7946 使用经度、纬度顺序的 `OGC:CRS84`，
必须显式选定投影目标。例如，项目确实位于 UTM 18N 时：

```shell
cad2gis reverse-convert "assets.geojson" --run-dir "cad-utm18" --target-crs EPSG:32618 --json
```

目标 CRS 应根据实际项目选择；程序不会猜测 UTM 分区或把经纬度当作米。
Web Mercator 的米单位也不等于地面测量长度，工程交付应选择合适的当地投影。
`--source-crs` 可明确覆盖全部选中图层的源 CRS；这只是解释输入坐标，不执行
配准或找回原始 CAD 坐标。覆盖值和原声明会同时保存在 manifest 中。

选择图层、按属性分层并生成标签：

```shell
cad2gis reverse-convert "delivery.gpkg" --run-dir "cad-cables" --layer CABLE --layer PTECH --layer-field source_layer --label-field display_label --label-height 2.5 --json
```

`--layer` 可重复；未指定时读取全部矢量图层。属性表和瓦片不作为几何读取。
GeoJSON 的图层名是顶层 `name`，缺省时使用文件名（去掉扩展名）。
`--label-field` 和 `--layer-field` 是显式选择：选定字段必须存在。
标签值为 null 或空串时不创建文字；标签高度使用目标 CRS 的线性单位。

Python API：

```python
from cad2gis.reverse import inspect_gis, reverse_convert

inventory = inspect_gis("delivery.gpkg")
result = reverse_convert(
    "delivery.gpkg",
    run_dir="cad-output",
    layers=["CABLE"],
    label_field="display_label",
)
```

## 原 CAD 坐标系与 DWG

可提供已有正向转换的规范 `run_manifest.json`，明确恢复该次转换记录的原 CAD
坐标系。程序校验交付 GIS 的哈希、分区、单位契约、实际 PROJ/GEODATA 操作，
以及存在时封存的 OSM 注册证据；逐顶点检查逆变换再正变换的闭合残差。
缺失、歧义或仅凭 CRS 推测的操作会拒绝。

```shell
cad2gis reverse-convert "delivery.gpkg" --run-dir "cad-native" --registration-manifest "forward/run_manifest.json" --json
cad2gis reverse-verify "cad-native" --json
```

多分区使用明确的 `--partition`。此模式不能与 `--source-crs` / `--target-crs`
混用。它恢复坐标框架，保留 GIS 中已经发生的编辑，不恢复原实体或撤销几何修复。
源 CAD 的声明哈希不等同于重新验证了原 DWG 字节；记录中明确区分这一点。
OSM 证据仍为相对依据，数值闭合不证明绝对定位。

```shell
cad2gis reverse-convert "delivery.gpkg" --run-dir "cad-dwg" --output-format DWG --json
cad2gis reverse-verify "cad-dwg" --json
```

DWG 使用现有 AutoCAD Core Console 配置与发现机制，不自动安装软件或退回其他
写入器。它以隐藏、限时进程处理暂存副本，保留运行版本、哈希及进程日志。
输出增加 `drawing.dwg` 和二进制 `dwg_readback.dxf`。使用二进制 DXF 回读以避免
ASCII DXFOUT 的额外数值舍入，不增加 `1e-8` 的坐标容差。独立核验还会重新提取当前 DWG，
不能只凭保留的 DXF 或重算后的文件哈希判定成功。核验需要同样可用的原生运行时。
失败项目及当前精度边界见验证记录，不将进程退出 0 当作内容正确。

## 并行批处理与 MCP

先生成只读预检及输入哈希绑定的计划，再执行：

```shell
cad2gis reverse-batch prepare "project-a/delivery.gpkg" "project-b/delivery.gpkg" --plan "reverse-plan.json" --label-field display_label --json
cad2gis reverse-batch run --plan "reverse-plan.json" --run-dir "cad-batch" --workers 4 --json
```

`--workers` 为 1–8。准备命令接受相同的图层、坐标、文字及输出格式选项。
执行前校验全部输入；每项再执行 inspect → convert → verify。计划、批次目录
及单项输出均不覆盖。`batch_report.json` 分别列出已核验、失败及保留产物，
部分失败返回非零退出码。DWG 并发会启动多个原生进程；本次实际验证了 2 个
工作线程的完整批次，更高原生并发数量尚未实测，应结合本机资源选取。

MCP 工具为 `reverse_inspect`、`reverse_convert`、`reverse_verify`，参数与 Python
实现一致。文件路径必须处于服务配置的允许根目录内；包括核验时从 manifest
读取的输入及注册路径。代理收到不确定的写入结果后先核验已有目录，不自动重复写入。

## 原生图元的源快照复现

当保留了规范源快照，可明确复现完整曲线参数，而不从已线性化的 GIS 坐标猜测。
该模式读取当前 `export-source` 产生并绑定哈希的原始记录和 `source.gpkg`，交叉
校验实体身份及曲线指纹；旧版缺少必要绑定的快照需要重新提取。

```shell
cad2gis export-source "original.dwg" --run-dir "source-snapshot" --json
cad2gis reverse-source-inspect "source-snapshot/source_manifest.json" --json
cad2gis reverse-source-convert "source-snapshot/source_manifest.json" --run-dir "source-primitives" --entity-type LINE --entity-type LWPOLYLINE --entity-type CIRCLE --entity-type ARC --entity-type ELLIPSE --json
cad2gis reverse-source-verify "source-primitives" --json
```

源快照导出和严格快照核验沿用现有 CAD reader/GDAL runtime 依赖；基础 GIS→DXF
不需要 GDAL，并不代表源快照模式也没有此依赖。

仅在具备完整事实时支持 LINE、LWPOLYLINE、CIRCLE、ARC、ELLIPSE；核验原生端点、
XYZ、bulge、圆心、半径、角度/参数区间、挤出方向与实体绑定。默认选择 Model；
若该布局内含不支持的记录，未指定 `--entity-type` 的转换会拒绝，不自动跳过。
显式类型选择会记录全部排除项与布局统计。`--layout` 可明确选择另一源布局，
其几何放入新图纸 Model 空间；`BLOCKDEF:` 块定义坐标系会被拒绝，因为实例位置和
层级尚未重建。此功能不重建原布局或块实例。该模式同样支持
`--output-format DWG` 和实际原生回读。

源记录未保留的线宽、厚度、自定义线型图案、字体、标注和填充不被声称为已恢复。
已知样式降级逐实体记录在 `style_omissions`，原值仍在属性文件中。
INSUNITS 是保留的插入单位元数据，不等同于对原坐标比例或绝对精度的推断。

此模式明确记录 `GIS_edits_applied=false`：它复现选中的源图元，不应用交付 GIS
中的分段、合并、移位或其他编辑。实际 Kletek 交付中存在多个 GIS 要素对应同一
源路线及支撑点移位，所以不能仅凭同一个实体 ID 把原曲线重新贴回 GIS 结果。

对应 MCP 工具为 `reverse_source_inspect`、`reverse_source_convert`、
`reverse_source_verify`。检查默认返回 50 条实体详情，`max_entities` 允许 1–200；
总数量和截断状态保留。所有快照引用先检查为固定目录内路径，再读取和核验。

## 几何与属性

| GIS 内容 | DXF 输出 |
| --- | --- |
| Point / MultiPoint | POINT |
| XY LineString / MultiLineString | LWPOLYLINE |
| 带 Z 的线 | 3D POLYLINE，保留各顶点 Z |
| Polygon / MultiPolygon | 外环及每个内环分别输出闭合多段线 |
| GeometryCollection | 递归输出其成员 |
| 显式标签字段 | 转义后的 MTEXT，保留字面文本 |
| 图层名 / 显式分层字段 | 合法化并处理大小写及名称冲突后的 CAD 图层 |
| `style_aci` / `style_truecolor` / `style_lineweight` | 合法值写入实体样式 |
| 其他属性 | 完整保留在 `attributes.jsonl` |

多边形导出边界，不生成填充面；孔洞不会被删除。Z 原数值保留，不换算高程单位，
不变换垂直基准。M 坐标、空/无效/非有限几何及不支持的 WKB 类型会明确拒绝，
不静默删点、补环、降维或跳过要素。GeoPackage 必须具有规范的几何头、
一致的 SRS/维度声明及整数主键；GeoJSON 重复要素 ID 会拒绝。

CAD 中新增的实体句柄通过 `attributes.jsonl` 映射到 GIS 图层与要素 ID。
该文件保留源属性、源/输出几何的 SHA-256 和实体角色；二进制属性使用
`{"encoding":"base64","data":"..."}` 保存。未支持的样式仍留在属性中，
不会猜测线型图案、块或文字排版规则。

## 输出及失败行为

- `drawing.dxf`：写出后重新打开，逐项比较源导出的顶点、Z、孔洞、属性及文字，
  校验句柄全覆盖、图层/样式、单位及 audit；需要修复的文件不会发布。
- `attributes.jsonl`：每个输入要素一行，包含属性和输出实体句柄。
- `manifest.json`：输入 SHA-256、源/目标 CRS、单位、变换信息、实体数量、
  输出哈希、图层名映射和能力限制。

输出目录必须不存在。系统先在同一父目录暂存，验证完成才发布整个目录；
已存在的结果不被覆盖，普通失败会清理本次暂存目录。原生运行失败时保留未发布
暂存目录及日志；若进程终止尚未确认，也保留其工作文件，并通过结构化错误中的
`artifact_status` 指向该目录。这些暂存文件不属于已核验交付。
同名运行用排他锁避免并行写入。
进程异常退出后可能留下 `.<输出目录名>.reverse.lock`；确认没有对应运行后才可移除。
如果文件已发布而清理锁失败，返回 `REVERSE_CLEANUP_FAILED` 与非零退出码，
并在 `artifact_status` 指向已保留的完整输出；无需重复转换该目录。
输入 GeoPackage 存在非空 WAL / journal 时要求先关闭写入者并完成 checkpoint。
输入在读取前后及发布前的哈希变化会阻止转换。

成功退出码为 0，普通错误为 2；`--json` 错误写入 stderr，包含
`error_code`、`stage`、`artifact_status` 与 `recovery_command`。
常见错误为 `REVERSE_CRS_REQUIRED`、`REVERSE_CRS_INVALID`、
`REVERSE_INPUT_INVALID`、`REVERSE_OUTPUT_EXISTS`、`REVERSE_SOURCE_BUSY`。

CRS 转换不代表绝对定位已验证。所有结果明确记录
`absolute_accuracy_verified=false` 与 `original_cad_restored=false`。
普通投影模式按 GIS 坐标导出；恢复原坐标系必须明确提供上述注册证据。

`reverse-verify` 默认坐标绝对容差为 `1e-8` CAD 单位，报告实测最大误差。
`--source` 和 `--registration-manifest` 可指向搬迁后的同内容文件，但哈希必须一致。
核验固定的目录内产物，不跟随 manifest 中任意输出路径；输出符号链接会被拒绝。
修改几何、Z、厚度、孔洞闭合、曲线拟合、图层或标签后，即使保持实体数并重算哈希，
仍必须通过源内容比较。没有保存 `export_options` 的旧结果需要重新导出才能完整核验。

## 验证与标准依据

核心回归覆盖 GeoPackage 编码、几何/维度拒绝、DXF 回读、投影与 Z、中文/特殊文字、
图层冲突、属性追溯、输出保护和失败清理。运行：

```shell
python -m pytest tests -k "reverse or canonical_cli"
```

Windows 上可将 `--basetemp` 指向已有、可写父目录下的新测试目录。
本次实测结果与边界见 [逆向转换验证记录](GIS_TO_CAD_VERIFICATION.md)。

实现参考 [OGC GeoPackage 几何编码规范](https://www.geopackage.org/spec130/)、
[GeoJSON RFC 7946](https://www.rfc-editor.org/rfc/rfc7946) 和
[ezdxf POLYLINE 文档](https://ezdxf.readthedocs.io/en/stable/dxfentities/polyline.html)。
