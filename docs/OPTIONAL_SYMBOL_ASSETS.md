# 标准 SVG 候选提取与可选符号绑定

SVG 提取是标准转换流程的一部分，默认使用 `candidate` 模式。`convert`、`auto-convert`、
Python `convert_project`、MCP `run_conversion` / `auto_onboard_and_convert` 和批量入口
都在 `<run>/svg-candidates/` 生成 `symbols.sqlite3`、`correspondence.json` 和
`correspondence.html`，用于并排复核原图符号及图例对应关系。
显式传入 `--svg-mode off` 或 API / 批量契约中的 `"svg_mode": "off"` 可以关闭提取；
旧批量契约省略该字段时也默认提取候选。
候选步骤为全部交付点记录状态，按布局分别检测图例，不混合纸空间和模型坐标。
图例候选与实例的归一化 SVG、颜色和镜像比例需要相符；附带属性的块不自动认领图例。
当前只实现 off/candidate，完整人工复核准入的 reviewed 模式尚未开放。
候选不会自动应用到交付 QML 或 GIS 业务数据；显式符号绑定仍是下文的独立操作。

`convert` 与 `auto-convert` 都接受可重复的 `--svg-font-dir /path/to/fonts`。
Python API 接收 `svg_font_dirs` 路径元组，两个 MCP 转换工具接收目录字符串数组。
批量 JSON 的 `svg_font_dirs` 是相对于输入契约的目录数组，只允许输入包内路径；
MCP 字体目录仍受项目根路径约束。`off` 模式不接受字体目录。字体替代情况记录在提取诊断中。

SVG 候选在规范转换的临时 run 内生成，完成后与 GIS 一起原子发布。
依赖、字体、提取或生成失败时，不发布不完整的新 run，已有 run 保留。
运行清单的 `modes.svg` 记录所用模式；`svg_candidates` 包含 `mode`、`status`、
`auto_applied: false`、`font_dirs`、`output_dir`、`report`、`html`、`store` 和文件 SHA-256。
关闭时该记录为 `{"mode":"off","status":"disabled","font_dirs":[]}`。
CLI 和两个 MCP 转换工具都返回候选报告位置；批量 HTML 链接到 run 内的对应报告。
旧 run 旁的 `<run-name>-svg-candidates/` 历史目录保留原状，新转换只写 run 内目录。

也可独立运行 `python -m cad2gis.symbol_assets`，或仓库中的 `tools/extract_svg_symbols.py`。
安装后也可以调用 `cad2gis-symbols`，不依赖源码目录。
标准安装包含 `ezdxf`；DWG 输入还需要已安装的 LibreDWG `dwg2dxf`。不需要 AutoCAD。
原有 `pip install ".[symbols]"` extra 继续保留兼容。

## 数据职责

| 文件 | 职责 |
| --- | --- |
| 原始 DWG、source/evidence 数据库 | 不可变的原图及转换证据，不由此脚本修改 |
| semantic store | 业务实体和受控修改，不保存此脚本的渲染候选 |
| `symbols.sqlite3` | 派生符号资产；保存 SVG、SHA-256、原图 SHA、实体 handle、定义依赖和诊断 |
| `symbols/*.svg` | 从数据库同时导出的可查看矢量文件 |
| `index.html` | 多符号候选预览，显示 handle、状态和限制 |
| 单独导出的 `.qml` | 显式选择一个候选后生成的 `SvgMarker` 样式；base64 内嵌 SVG |

SQLite 的扩展名不影响数据库格式；这里使用 `symbols.sqlite3` 来明确用途。`symbol_id` 为主键索引，
AI 可按 ID 查询 SVG 与来源，而不必每次扫描所有图纸。该库不是 GIS 几何或业务值的权威来源。

## 使用

先读取原图实体记录，按具体 handle 选择一个符号。配置必须绑定实际源文件 SHA-256：

```json
{
  "source_sha256": "<DWG 文件的完整 SHA-256>",
  "symbols": [
    {"symbol_id": "pole-source-candidate", "label": "原图杆型候选", "handles": ["<实际 handle>"]}
  ]
}
```

```sh
python -m cad2gis.symbol_assets extract --source drawing.dwg --selection selection.json --output symbol-review-v1
python -m cad2gis.symbol_assets qml --store symbol-review-v1/symbols.sqlite3 --symbol-id pole-source-candidate --output pole-candidate.qml --size-mm 6
```

缺少字体的 Linux 环境应提供 `--font-dir /path/to/fonts`（可重复）。无字体时拒绝提取含文字的符号。
字体目录只读取，不上传或复制字体；具体 CAD 字体缺失时，替代状态写入 `fonts` 诊断。

脚本拒绝错误源 SHA、找不到的 handle、重复 ID、覆盖已有输出和被修改的 SVG。提取在临时 DXF 上完成，
所有源坐标和 DXF 属性记录保留在库内；SVG 坐标只是渲染坐标。QML 导出以只读方式打开库。

QML 是单符号样式，用于独立测试图层的视觉复核。把它载入整个 PTECH 图层会让整层使用同一个符号，
不能把这个动作当作按 TYPE 分类的自动映射。此入口不自动覆盖交付 QML、GeoPackage 或 QGZ；
按语义类型推断绑定多个符号仍未启用。下面的可选工具只接受精确原实体 handle 绑定。

## 单文件 QGZ 与显式 SVG 绑定

单独下载旧的 `delivery.qgz` 不会同时下载它引用的 `delivery.gpkg`，同名数据库还可能被错误复用。
用 QGIS Python 运行 `tools/package_qgis_standalone.py` 可以将数据库作为附件封装在 QGZ 内。
实现已随核心 Python 包发布：`python -m cad2gis.qgis_package`、
`python -m cad2gis.qgis_verify`、`python -m cad2gis.svg_delivery`；原 tools 脚本仅作兼容入口。
三个安装命令分别为 `cad2gis-qgis-package`、`cad2gis-qgis-verify`、`cad2gis-svg-delivery`。
QGIS 运行时是单独依赖，基础转换 Docker 镜像不含 PyQGIS。

```sh
python tools/package_qgis_standalone.py --project delivery/EMR29619/delivery.qgz --output EMR29619-standard.qgz
python tools/package_qgis_standalone.py --project delivery/EMR29619/delivery.qgz --output EMR29619-with-SVG.qgz --store source-symbols/symbols.sqlite3 --bindings bindings.json --delivery-manifest delivery/delivery-manifest.json
```

这里的 `python` 必须是有 PyQGIS 的环境，例如 Windows 的 `python-qgis.bat`。绑定文件示例：

```json
[{"layer": "PTECH", "source_handle": "7943", "symbol_id": "ptech-7943"}]
```

已有候选目录可通过 `cad2gis-svg-delivery --baseline <完整交付目录> --assets <候选目录> --output <新目录>` 接入完整交付。该入口按候选记录中的数据库 SHA-256 对应主工程和分区，不要求转换 run 或交付目录使用固定名称；相同数据库副本的重复绑定会合并，哈希不匹配时拒绝继续。输出的 `delivery.qgz` 使用相邻数据库，`view-with-source-SVG.qgz` 内嵌数据库和 SVG。生成进程退出后，仍须独立运行 `cad2gis-qgis-verify` 验证单文件工程。

工具要求源 DWG SHA 相同、交付 GPKG 哈希匹配、SVG 提取选择恰好为该 handle，且目标恰好为一个点要素。
未绑定对象沿用原渲染器，标准转换只提取 SVG 候选，不自动应用符号。必须退出生成进程后运行
`python tools/verify_qgis_standalone.py --project output.qgz --output verification`。
此前同进程检查漏检 Windows 临时路径，不能单独作为发布门槛。
无界面 QGIS 若没有字体，复验会明确失败，不再把方块标签的非空画布当作通过。
可以添加 `--font /path/to/font.ttf`，加载失败也会拒绝复验；字体只用于当前验证进程。
独立检查覆盖默认视图、搬移、错误同名数据库干扰、附件哈希、图层数量和逐要素实际 SvgMarker
选择检查；数据库字节不变。内嵌数据库适合便携查看，业务编辑仍应使用完整交付包或规范数据库，而非把查看副本当权威库。
附件接口依据 [QGIS 官方 API](https://api.qgis.org/api/3.44/classQgsProject.html)。

现场 EMR29619 可选版本绑定了 12 个 PTECH 和 1 个 BOITE。EMR 仍使用原样式；它的语义点来自文字锚点，
不能为了视觉连线而擅自移动或编造 SVG。字体替代限制继续保留。

## 保真边界

- `INSERT` 的动态显示状态必须核对。Manado 的 `*U16` 定义同时存在 NP7、NP9、FM、不同尺寸和重复圆形，
  按整个定义直接绘制会重叠。实体选择结果始终标记 `candidate`，不能宣称恢复了动态状态。
- 渲染失败跳过的实体写入诊断，状态为 `incomplete`，不能导出 QML；CAD 本来隐藏的实体单列记录。
  缺失字体可能替换；文字转为路径后，
  QGIS 加载不再依赖外部字体，但提取阶段仍需比较原 CAD 字形。
- SVG 不承诺测量精度，不参与长度或坐标计算。绘图中的 `4'` 不擅自改成 `4"`，不从语义猜颜色或内部文字。
- 独立数据库保存选中实体及块定义依赖的属性，不替代完整 CAD 证据库。未人工选择的图例不会被自动认领。

实现采用 [ezdxf 官方 SVG 导出接口](https://ezdxf.readthedocs.io/en/stable/addons/drawing.html)，
将导出的 CSS 类转成 SVG 属性以便移植；ezdxf 的 MTEXT、字体及动态显示差异仍需视觉复核。
QGIS 的 SVG 和内嵌入口见 [官方符号选择器文档](https://docs.qgis.org/3.44/en/docs/user_manual/style_library/symbol_selector.html)。
