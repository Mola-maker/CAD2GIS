# 交接 Spec：青色 CABLE 捕获 + ZPM 多边形生成 + QGIS 渲染修复

> 状态：待修复（2026-08-01 交接）。前一个 session 已完成根因排查，证据齐备，本 spec 直接给修复路径。
> 背景：四项目 auto-convert 产线（hutabohu / lamteh_main / lamteh_sf / kletek），用户反馈三个问题。标签族（annotation family）问题已搁置，勿再钻牛角尖。

## 一、问题与根因（均已验证）

### 问题 1：青色 CABLE 未被捕获（最高优先级）
- 现象：delivery.gpkg 的 CABLE 表**无一条青色线**（aci=4 全部缺失）。QGIS 中看不到青色光缆网络。
- 证据（lamteh_main）：
  - `source.gpkg` 中青色线（aci=4 / truecolor #00FFFF）共 394 条，**全部位于 SLING WIRE 层**（lamteh_sf 341 条 / hutabohu 26 条 / kletek 7 条）。
  - 这些线 terminal_state 全部 `accepted`，但**没有进任何 delivery 表**（CABLE/CABLE_SEGMENT/BOITE/PTECH/SITE/IMB 全查过，0 命中）。
  - delivery 的 CABLE 全部来自 FO xx CORE 层（颜色 3/30/61/90/192/210/220/232/256），与图例光缆色（绿/玫红/紫/橙黄/红/黄绿/深橙）对应。
- 根因：
  1. SLING WIRE 在 registry 中声明为 `layers.sling_wire` 角色（config/mapping_registry.json），管线只把它用于**拓扑 span 测量**（`topology.py:669`），从不产出 CABLE 特征。
  2. route regex（`positive_route_layer_regex`）只含 FO xx CORE 层。`onboarding.py` 的 route-regex 检查链（`_extend_route_regex`，疑似缆线层检测 CABLE/FEEDER/FO/GRT 前缀）**不会把 SLING 加入** → 青色线在 `classify_entities` 中落入 `unmatched_route_layer` → abstain。
- 用户澄清（图例 CABLE TYPE）：
  - 青色在 DWG 中有两个样式：**实线 = 光缆样式**（与绿色 24C、紫色 48C 一致）；**加粗虚线 = ZPM 多边形边界**（见问题 2）。
  - SLING WIRE 图例色就是青蓝色——青色实线即用户所说"青色 CABLE"。
  - 用户否定了"394 条全部并入 CABLE 会涨到 400"的说法：SLING WIRE 中大部分是**短吊线段**（20-100m 共 375 条，>100m 仅 16 条），真正代表光缆路由的长实线只是其中一部分。**修复时必须先验证青色长线（>100m 或连通性）是否就是光缆网络，再决定过滤阈值**，不可盲目全并。

### 问题 2：ZPM 图层"有名无实"（delivery ZPM=0）
- 现象：delivery.gpkg 中 ZPM/ZNRO 表存在（count=0），QGIS 加载后空白。
- 证据：
  - ZPM/ZNRO 在 `schema_config.py` 有完整表定义（Polygon 类型），`warehouse.py` 会建表。
  - **全代码库 grep 无任何 ZPM/ZNRO 特征生成逻辑**（semantics.py / onboarding.py / pipeline.py 均无产出）→ 永远空表。
  - ZPM 边界实体 = **FAT AREA 层**：暗青色 aci=134（RGB 0,127,127）+ PHANTOM2 虚线线型（图层定义 ltype_ref=42293，`dwgread JSON` 确认）。lamteh_main 533 条，其中 68 条闭合、300 个端点连接节点，构成多边形区域。
- 用户确认的 ZPM 边界图层：hutabohu=`FAT AREA FDT 1`；lamteh_main=`FAT AREA`；kletek=`FAT AREA FDT 1`；lamteh_sf=无（太小没有）。
- 修复方向：新增 FAT AREA 层 → ZPM 多边形的分类/生成逻辑（闭合线段组环 → Polygon，参照 ZPM schema 字段 CODE/REF_SRO 等），并在 `classify_entities` / pipeline 中产出。

### 问题 3：QGIS 渲染全虚线（光缆应为实线）
- 现象：CABLE.qml 所有线 `line_style="dash dot dot"`（虚线）。用户："此前的构建中这两个颜色（绿/紫光缆）都用的虚线"。
- 根因链：
  1. reader 不解析实体线型：`libredwg.py:1115` 硬编码 `entity_linetype = "ByLayer"`；`libredwg.py:887` 图层样式表硬编码 `"linetype": "Continuous"`。
  2. `styles.py:_qgis_pen_style("ByLayer")` 返回 None → 落入 `_style_coverage_records` 的 `unresolved_linetype` → fallback `_UNSUPPORTED_PEN_STYLE = "dash dot dot"`（styles.py:28,120）。
- 修复方向（两条路，推荐 A）：
  - **A（最小）**：`_qgis_pen_style` 把 `BYLAYER/BYBLOCK` 解析为 `solid`（图层线型 Continuous → 实线）。实线/虚线区分改由图层级线型驱动（图层样式表已可读到 Continuous/DASHED/PHANTOM2）。
  - B（彻底）：reader 解引用实体 ltype（`dwg_ent_get_ltype_name` 已在 C API 可用，见下方探针代码）→ 真实线型进入 style。

## 二、已完成的探针代码（可直接复用）

```python
# 实体线型探针（SWIG + C API），验证 DWG 真实线型
import ctypes
import cad2gis.reader.libredwg as m
m._require_libredwg()
lib = m._libdwg
lib.dwg_ent_get_ltype_name.restype = ctypes.c_char_p
lib.dwg_ent_get_ltype_name.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
# data = m.Dwg_Data(); data.object = m.new_Dwg_Object_Array(500000)
# m.dwg_read_file(path, data); 遍历 obj.supertype == m.DWG_SUPERTYPE_ENTITY
# entity_ptr = int(obj.tio.entity.this); lib.dwg_ent_get_ltype_name(entity_ptr, byref(err))
```

## 三、修复计划（建议顺序）

1. **青色 CABLE 捕获**（问题 1）：
   - 先验证：对 lamteh_main 的 SLING WIRE 青色线做长度/连通性分析，确认光缆网络子集（>100m 的 16 条？还是连通的主环？），并在 QGIS 中对照 DWG 目检。
   - 实现：方案 a) onboarding 的疑似缆线层检测加入 `SLING` 前缀并扩展 route regex；方案 b) `semantics.py:classify_entities` 把 `registry.layers.sling_wire` 层的实体同时产出 CABLE 特征（保留 sling 拓扑角色不冲突——topology 从 entities 读，classify 输出 features）。
   - 与用户确认过滤规则后再定（用户已提示不可全并 394 条）。
2. **ZPM 生成**（问题 2）：FAT AREA 层闭合线 → Polygon，产出 ZPM 特征（schema 字段见 `schema_config.py:1353` ZPM 定义，注意 D1 注释：REF_SRO 非 REF_PM）。
3. **QGIS 渲染实线**（问题 3）：styles.py `_qgis_pen_style` 支持 BYLAYER→solid（方案 A）。
4. 重跑四项目 `cad2gis auto-convert --provider deepseek --json --llm assist`（先 `unset CAD2GIS_LAYOUT`，API key 由用户在终端 export），验证：CABLE 出现青色线、ZPM 有内容、QML 实线。

## 四、验证基线

- 当前（修复前）delivery_counts：
  - hutabohu: CABLE=9, BOITE=46, PTECH=172, SITE=4, IMB=640, ZPM=0
  - lamteh_main: CABLE=16, BOITE=76, PTECH=215, SITE=4, IMB=419, ZPM=0
  - lamteh_sf: CABLE=16, BOITE=1, PTECH=7, SITE=0, IMB=0, ZPM=0（BOITE/PTECH 异常低——另有问题，不在本 spec 范围）
  - kletek: CABLE=7, BOITE=19, PTECH=33, SITE=2, IMB=164, ZPM=0
- 测试：`python -m pytest tests/ -q --tb=short --ignore=tests/test_review_server.py --ignore=tests/test_mcp_stdio.py -k "not test_capability_rejects_loadable_library_without_required_symbols and not test_native_initialization"`（当前 197 passed, 6 skipped）

## 五、关键文件索引

- `src/cad2gis/cad2gis_v3/semantics.py` — classify_entities（CABLE/route regex 匹配，line 429+；无 ZPM 产出）
- `src/cad2gis/cad2gis_v3/onboarding.py` — `_extend_route_regex`（line 671）、route_regex 检查链（line 1168-1260）
- `src/cad2gis/cad2gis_v3/topology.py:669` — sling_wire 角色（仅测量，不交付）
- `src/cad2gis/cad2gis_v3/styles.py` — `_qgis_pen_style`（line 58）、`_UNSUPPORTED_PEN_STYLE`（line 28）
- `src/cad2gis/reader/libredwg.py` — `_read_layer_styles`（line 869，硬编码 Continuous）、line 1114-1115（实体线型硬编码 ByLayer）
- `src/cad2gis/cad2gis_v3/warehouse.py` — write_delivery / LAYER_CONFIGS（含 ZPM 表，但无数据源）
- `src/cad2gis/cad2gis_v3/schema_config.py:1353` — ZPM 字段定义
