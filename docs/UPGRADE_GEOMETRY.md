# CAD reader 与原图几何修复

本次基于 OSM archive `e401383` 检查 reader → immutable source facts → display geometry → cable delivery 链路。以下结论由合成 DXF 和现有 regression 验证；真实 9 份 DWG 的转换结果由整体验证报告记录，不能用这里的合成测试代替。

## 已修复的问题

| 缺陷 | 原行为 | 修复后的行为与验证 |
|---|---|---|
| OCS 被标记为 WCS | LWPOLYLINE / 2D POLYLINE 直接复制 DXF XY；镜像和倾斜 elevation 会移位 | 使用 ezdxf `vertices_in_wcs()` / `points_in_wcs()`；保留真实 Z、normal、extrusion 和原始 bulge。分别验证 `(0,0,-1)` 和倾斜平面 |
| 原始 Z 丢失 | LINE 用 XY 算 native length，facts 将 Z 填 0 | 保留 WCS XYZ 并计算 3D native length；`(1,2,3) → (4,6,15)` 长度为 13，2D delivery 不会将它误认成长度 5 |
| INSERT / TEXT anchor 未转换 | `insert` 的 OCS 数值直接用作平面位置 | anchor 转 WCS；INSERT 同时保留 `insertion_point_ocs`。带 base point、rotation、非等比 scale 的镜像 INSERT 与 ezdxf `matrix44()` 对照 |
| 镜像文字 baseline 错向 | 使用 OCS rotation 作为平面 rotation | baseline 投影为 WCS angle；镜像或倾斜 glyph 仍明确标记需要 review，当前 UI 不能完整重现 glyph reflection |
| legacy POLYLINE 缺少 flags | 合法普通 2D route 被完整性检查拒绝 | reader 发布原始 flags；fitted / mesh / 3D 标志仍参与 delivery 检查 |
| complete reader path 崩溃 | `skipped_zero_length_segments` 只在另一分支初始化 | 两条路径均初始化 audit state；添加完整 semicircle regression |
| 完整曲线的错误位置未检查 | 曲线与 source 长度相同即可通过，即使端点整体移位 | 校验首尾端点；带显式 source vertex index 的 segment 还须与对应原始顶点一致 |
| 原图曲线画成弦 | source.gpkg 直接以原始顶点作空间折线 | 新增 `source_display_points()`，用 bounded tessellation 得到显示几何；原始 `native_points`、`curve_facts`、native length 保持独立 |
| 闭合曲线最后一段丢失 | 两顶点的弧形闭合边界没有形成 polygon | 闭合时使用最后一个 bulge 连接首点；测试直线加半圆形成的两顶点边界 |
| open polyline 末顶点 bulge 阻断转换 | 没有后续 segment 的 bulge 被判为非法 | 按 DXF 语义忽略其几何作用，并在 `ignored_open_trailing_bulge` 保留数值 |
| 很浅的长弧丢失 | 将 dimensionless bulge 与 drawing-unit tolerance 比较，直接当直线 | 仅 bulge 等于 0 才是直线；`chord=1e9, bulge=1e-10` 的 0.05 sagitta 被保留 |
| ARC 整圈长度变成 0 | 对 `end-start` 直接 `% 360` | 使用 ezdxf `arc_angle_span_deg()`；0→360 按 closed circle 规范化，原始角度和 endpoints 仍保留在 parameters |
| 大坐标小边界消失 | shoelace 的大数乘积相减得到 0 | 平移到边界局部原点后用 `math.fsum`；验证 `1e9` 坐标域内边长 0.5 的闭合边界 |

## 坐标与长度的语义

对 planar DXF entity，OCS 坐标转换为：

\[
\mathbf p_{WCS}=x\,\mathbf u_x+y\,\mathbf u_y+e\,\mathbf n.
\]

normal 为 `(0,0,-1)` 时，一个典型结果是 `(x,y,e) → (-x,y,-e)`。因此不能只保留 elevation 作为额外注释，更不能直接把 OCS XY 写进 `vertices_wcs`。

reader 的 `bulges` 仍是原始 DXF 值。`primitive_parameters.bulge_coordinate_system="OCS"` 明确它们的参照平面。对水平负 Z normal，显示/交付计算临时反转 bulge 符号；这一步不会改写 source facts。没有明确 bulge coordinate system 的历史镜像记录仍会拒绝猜测方向。

primitive native length 使用真实 source 几何：LINE 用 XYZ 距离，2D polyline 在 OCS 平面用解析弧长。显示折线的 chord sum 不会覆盖这个长度。display policy 的单位始终是 CAD drawing unit，不能未经 unit / CRS 检查称为米。

## Tessellation 与数值稳定性

chord 长度为 \(c\)，bulge 为 \(b\) 时：

\[
\theta=4\arctan b,\qquad
r=\frac{c(1+b^2)}{4|b|},\qquad
L=r|\theta|.
\]

显示误差上限 \(s\) 约束每一步的角度：

\[
\Delta\theta_s=4\arcsin\sqrt{\frac{s}{2r}}.
\]

这与 \(2\arccos(1-s/r)\) 等价，但极浅弧的 \(1-s/r\) 不会先舍入成 1。再与 max chord 对应的角度取较小值。采样点在局部 chord frame 内计算，使用 \(1-\cos\phi=2\sin^2(\phi/2)\)，避免大圆心与大 radius 相减抹掉小的偏移。

默认弧线 policy：max sagitta 0.01 drawing unit、max chord 1 drawing unit、每 source segment 最多 100,000 个显示顶点。超限会给出明确失败，不会静默降低精度。native endpoints 保持输入的 full precision。

## 对接与限制

`source_display_points(SourceEntity)` 返回 XY display path，可供 source.gpkg 或原图 UI 使用。它不进行语义分类，也不把 display tessellation 当 source geometry。已知曲线缺少足够事实时会抛 `ValueError`；source.gpkg 将该实体保留到 `source_metadata`，写明 `source_curve_geometry_unavailable`。SPLINE 的 control polygon 不能冒充其实际曲线。历史 import port 已提供的显式 closed CIRCLE ring 继续保留。

source export manifest 的 `coordinate_reference.geometry_representation` 记录显示用途、原始字段和 tessellation policy。source replay 本身保持既有的 source SHA、artifact SHA、reader identity 校验。reader 代码已经改变，旧 snapshot 必须重新 export，不能使用旧 OCS→WCS 实现生成的 facts。

tilted / varying-Z 曲线在没有完整 reader materialization 或明确 XY projection contract 时仍不进入 2D display / cable delivery。当前工作没有实现通用 3D warehouse，也没有把保留 Z 等同于完整 3D deliverable。complete reader materialization 仍须自带 native length provenance、连续 geometry 和 source endpoint 锚定。

## 验证

以下命令在共享 Python 3.12 environment 通过 **89 tests**；另有 2 条既有 `sqlite3.version` DeprecationWarning。该结果为几何任务完成时的 targeted suite，完整 CI 的计数另行记录。

```bash
python -m pytest -q \
  tests/test_geometry_fidelity.py \
  tests/test_libredwg_text_preservation.py \
  tests/test_source_gpkg.py \
  tests/test_source_replay.py \
  tests/test_crosscad_contracts.py \
  tests/test_reader_capabilities.py
```

新增测试不需要联网，也不需要外部 DWG；source.gpkg / source export 测试需要现有 GDAL runtime。相关改动通过 Ruff 检查。

## 文档依据

2026-09-09 查询并对照 ezdxf stable 1.4.4 的 primary documentation：

- [LWPOLYLINE：OCS、elevation 与末顶点 bulge](https://ezdxf.readthedocs.io/en/stable/dxfentities/lwpolyline.html)
- [Polyline：2D OCS 与 3D WCS 的区别](https://ezdxf.readthedocs.io/en/stable/dxfentities/polyline.html)
- [OCS/UCS 转换与 extrusion](https://ezdxf.readthedocs.io/en/stable/tutorials/ocs_usage.html)
- [ARC 的 OCS 与方向](https://ezdxf.readthedocs.io/en/stable/dxfentities/arc.html)
- [Circle 的 WCS vertices 与 sagitta flattening](https://ezdxf.readthedocs.io/en/stable/dxfentities/circle.html)
- [Math core：bulge、angle span 与 sagitta](https://ezdxf.readthedocs.io/en/stable/math/core.html)
- [INSERT 与 block transform](https://ezdxf.readthedocs.io/en/stable/dxfentities/insert.html)
