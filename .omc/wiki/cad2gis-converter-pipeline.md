---
title: "CAD2GIS Converter Pipeline"
tags: ["converter", "pipeline", "DWG", "GeoPackage", "QGIS", "LibreDWG", "cad2gis", "newmodel", "两路线"]
created: 2026-07-05T15:38:01.720Z
updated: 2026-07-20T00:00:00.000Z
sources: []
links: []
category: architecture
confidence: high
schemaVersion: 1
---

# CAD2GIS Converter Pipeline

> **2026-07-20 重大更新**：项目已确认为**两路线格局**，并已决策终局——newmodel 为生产线，main 归档前完成知识转移。本页保留 main 管线原始技术细节（见下文"main 路线"节），但整体定位以本节为准。

## 现状总览：两路线格局

| | **main 路线**（归档待定） | **newmodel 路线**（生产线） |
|---|---|---|
| 工程环境 | WSL2 Ubuntu 24.04，系统 Python 3.12 | Windows，conda Python 3.12（env/environment.yml） |
| DWG 引擎 | LibreDWG 源码自编译（SWIG+ctypes 桥） | AutoCAD 2027 Core Console（entget/ssget） |
| 形态 | experiment/py_scripts/ 脚本集群（~12,000 行，converter.py 单体 3,510 行） | 可安装包 `src/cad2gis`（canonical CLI：inspect/bootstrap/validate/convert/gcp/verify/doctor）+ `cad2gis_v3` 后端 |
| 交付哲学 | 业务聚合（拓扑桥接、样式还原、≥90% 业务可读） | 源保真 + fail-closed（源几何不可变、abstain、证据分离） |
| 已验证基线 | Hutabohu：CABLE=203（0 桥）、BOITE=45、FDT 151/51/1、CONV-SUM=6942、EPSG:3857 | 同一 DWG：BOITE=43、CABLE=6（145 顶点 0.0m 差）、CABLE_SEGMENT=139（130 measured+9 unmeasured）、PTECH=167、IMB=682、EPSG:9481、105+116 测试、13 unresolved |
| 验证状态 | 五缺陷修复完成（2026-07-18, commit 43129f3）；evaluator 余 14 E 级（法标业务字段 DWG 无源，backlog） | GCP disabled → 绝对精度 not_verified；单图纸基线，禁止跨 CAD 外推 |

## 终局决策（2026-07-20 deep-dive 访谈确认）

1. **生产转化优先** — XA-202610 竞赛是契机，烽火内部（定制）QGIS 生产系统集成是目标。
2. **newmodel = 生产线与后续开发主场** — main 的优点（本机快速迭代）不能弥补其与 Windows 端/Web 的隔绝。
3. **main 归档，归档前完成知识转移** — 载体：综合分析文档，投递至 newmodel 分支 `main_archive/`。
4. **转移范围 = 领域知识 + 可移植算法**（以领域数据/参考思路形态，非替换 newmodel 实现） — domain_vocab/图层正则/法标评估规则/匈牙利标注/三轨样式/span 注记/图例排除法；**LibreDWG 读取链与业务聚合拓扑哲学埋掉**（难以克服的局限 + 违反源几何不可变）。

## newmodel 路线（生产线）速查

- 分支：`origin/newmodel`（本地无跟踪分支时需 `git worktree add <path> -b newmodel origin/newmodel`）
- 设计全记录：`experiment/history.md`（4,699 行，组员思路：DWG 唯一蓝图/证据优先 fail-closed/精度三域拆分/终局=唯一 canonical 核心包）
- 入口：根 `README.md`（canonical 工作流）+ `experiment/README.md`（APD project-pack）
- 架构：`experiment/ARCHITECTURE_V3.md`；包：`src/cad2gis/`（cli 540 行/doctor/gcp_workflow 1264 行/verify 711 行）+ `tests/`（5 个合同测试文件）
- 新方向：`docs/superpowers/specs/2026-07-19-webdemo-delivery-system-design.md` 与 `docs/superpowers/plans/2026-07-19-webdemo-delivery-system-plan.md`（webdemo 交付系统）

## main 路线（归档待定）速查

> 以下为本页 2026-07-05 原始内容，仍准确描述 main 管线，但其演进已冻结。

### Quickstart
```bash
cd <仓库根目录>
python3 demo/converter.py
# Output: demo/output/DS-02*.gpkg, DS-04*.gpkg (EPSG:32648)
# Then reproject:
ogr2ogr -t_srs EPSG:3857 output_3857.gpkg output.gpkg
```

### Architecture
```
.dwg → LibreDWG (SWIG + ctypes) → QgsGeometry → GeoPackage (via ogr2ogr)
         No intermediate formats, no GDAL DWG driver
```

### Key Technical Decisions
1. **LibreDWG ctypes bridge**: SWIG mangles Chinese UTF-8 strings and wraps point arrays as single elements. Use `dwg_ent_get_layer_name()` and `dwg_ent_lwpline_get_points()` via ctypes for correct data.
2. **Coordinate regime separation**: DWGs contain two coordinate systems — Regime A (Y>100K, UTM northing preserved) and Regime B (Y<100K, local engineering grid). Apply per-entity transforms.
3. **Mixed layers**: JMD, GCD, 0 split into _A/_B suffixes.

### Transform Offsets (→ EPSG:32648)
- Regime A: DX=+292,539 DY=-405
- Regime B: DX=+589,239 DY=+3,203,295
- Reference point: EPSG:32648=(661,539, 3,183,995)

### Known Gaps
- ~20 Chinese layer names: surrogate encoding in GeoJSON intermediate
- HATCH/DIMENSION entities skipped (non-geometric)
- POLYLINE_2D/3D skipped (needs VERTEX traversal)
- Residual: ~200m from Tianditu reference

### 后续补充（2026-07-18/19）
- 五缺陷修复：T=图例面板 596 碎片诱发 429 伪桥（排除后 0 桥）；S=三轨样式+pyqgis 无头 segfault 子进程隔离；P=span_annotations 170 条+真 FID 外键；X=SITE 真值=2 编码闭环。交付 gpkg SHA 10a89d6e + .qgz。
- 解耦重构（commit 3e5be1a）：cad_common/ftth_converter/convert_all/topology_repair/style_exporter 已建，但 ftth_converter 独立运行回归（from-import 绑定快照 vs 跨模块可变全局），实际生产路径=converter.py 单体+cad_common 库并存（方案 C）。**因 main 归档决策，回归不再修复。**
