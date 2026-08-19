# Spec：LibreDWG INSERT 变换事实与块属性/块内文字物化（robustness 下一 session 重点）

> 术语：APD = **As Plan Drawing**（按计划图纸，as-planned 设计态）；
> SF = **Subfeeder**（副馈线）。见 [GLOSSARY.md](GLOSSARY.md)。
>
> 状态：待实施（`78f5d1a` 已提前合入 Model 空间 POLE ID 标签修复；本方案剩余范围收窄为 **block-only 文字/属性物化**）
> 分支：`robustness`
> 关联问题：DSH 工业化调研中发现的 issue 4 —— “LibreDWG reader 不输出 INSERT transform facts，导致 block definition 内的真实杆号/线缆文字无法进入语义层”。
> 优先级：P1（正确性）；本 spec 只做 reader/plan-domain/语义证据链，不重写 main 分支转换算法。

---

## 0. 与 `78f5d1a` 的进度对照（修订时间：2026-08-17）

| 事项 | 在 issue 4 方案中的位置 | 当前状态 |
|---|---|---|
| `_annotation_target_eligible()`：真实标签可覆盖 `PTECH-CAD-*` | Phase 3 前置 | ✅ 已实现（`78f5d1a`） |
| Model 空间 `POLE ID` 杆号 fallback（`MR.KLDYA.P017` / `MR.IJY.KLDYA.P003` 等） | 原问题陈述第 2 条的“全量退化”证据 | ✅ 已由 pole-shape fallback 修复；hutabohu 167/167、kletek 33/33、lamteh_main 207/208、lamteh_sf 26/26 |
| 去噪阶段保护 label / DIMENSION 证据实体 | 本方案的并行补充，非替代 | ✅ 已实现（`spatial_filter.evidence_exempt`） |
| DIMENSION 渲染文本提取（DIMENSION→block→MTEXT） | Phase 0 的 side-channel 先例，非同一目标 | ✅ 已实现（`_read_anon_block_names_json` 返回 `dimension_display_texts`） |
| LibreDWG INSERT transform facts（六项） | Phase 1 | ⬜ 未开始 |
| plan_domain 块定义物化 | Phase 2 | ⬜ 未开始 |
| owner/block-path 优先的语义归属 | Phase 3 | ⬜ 未开始；当前仍为 layer+shape fallback + 坐标最近邻 |
| block attribute field rule 扩展 | Phase 4 | ⬜ 未开始 |

结论：**没有实现重复**。`78f5d1a` 解决的是“Model 空间可解析杆号”和“DIMENSION 渲染值”，issue 4 主干的 INSERT facts / 块物化仍未实现。

---

## 1. 问题陈述

CAD2GIS 的 PTECH / BOITE / SITE 等设备在图中的几何是 INSERT（块引用）点；其中一部分真实标识文字不在 Model 空间的独立 TEXT 上，而在 INSERT 引用的块定义（BLOCK DEFINITION）里。`78f5d1a` 之后，Model 空间可解析杆号已由 pole-shape fallback 覆盖，**本 spec 只针对仍然缺失的 block-only 文字/属性**。例如：

- `lamteh_main / lamteh_sf`：`MR.XXX.P016.HC`（NEW POLE HC）、`MR.XXX.HC.P087`（SLING WIRE）、`FAT.B010`（FAT CODE）大量只以 `cad_role=block_definition` 存在。
- `hutabohu`：`EXT.MR.MF.LBB.Sxx.Pxx` 和 `MR.xxx.Pxx` 有 Model 空间文本，但块内文字仍未物化。
- `kletek`：`MR.KLKx.Pxx` 有 Model 空间文本，但块内文字仍未物化。

当前后果：

1. `plan_domain` 无法把 INSERT 的块定义成员变换到 Model/WCS，四个产线 `plan_domain.derived_entity_count = 0`、`expanded_insert_count = 0`。
2. Model 空间可解析的 PTECH 标签已修复；**block-only 文字**对应的目标仍退化为 `PTECH-CAD-<handle>`（provenance=`DWG_DERIVED:stable-handle-id`）。`78f5d1a` 后临时重跑结果：lamteh_main 207/208、lamteh_sf 26/26、kletek 33/33、hutabohu 167/167；剩余 block-only 样本待本 spec 物化后覆盖。
3. 即使 Model 空间有标签，标注归属仍退化为“文本插入点 ↔ 目标点”的最近邻欧氏距离，未利用块/owner 关系。

### 1.1 已核实的证据（截至 `78f5d1a` 的 plan_domain 状态）

| 站点 | model INSERT 数 | plan_domain 展开 INSERT | plan_domain 派生实体 | `missing_or_invalid_insert_transform` 警告数 |
|---|---:|---:|---:|---:|
| hutabohu | 226 | 0 | 0 | 226 |
| kletek | 56 | 0 | 0 | 56 |
| lamteh_main | 305 | 0 | 0 | 305 |
| lamteh_sf | 55 | 0 | 0 | 55 |

- 四个 `run_manifest.json` 的 `plan_domain.selection_mode` 都是 `cad-role-partition`，root 实体数量正常，但没有一个 INSERT 被展开。
- 每个 `plan_domain.issues` 都是同一条：
  `missing_block_transform_facts`，缺失字段：
  `["block_base_point", "extrusion", "insertion_point", "normal", "rotation", "scale"]`。

### 1.2 证据来源文件

- `baselines/<site>/run/run_manifest.json` → `plan_domain`
- `baselines/<site>/run/evidence.gpkg` → `cad_entities.cad_role` / `text` / `block_attributes`
- `src/cad2gis/reader/libredwg.py:1270-1358`：INSERT 分支（已读 `ins_pt.x/y`、`scale.x/y/z`、`rotation`、block name、owner ATTRIB）
- `src/cad2gis/reader/libredwg.py:1373-1437`：`raw_properties` 把 `insertion_point` / `block_base_point` / `normal` / `extrusion` 全部标 `not_applicable`，`transform_facts={}`
- `src/cad2gis/reader/libredwg.py:820-898`：`_read_anon_block_names_json()` side channel（`78f5d1a` 已扩展出 DIMENSION 显示文本映射，可作为 Phase 0 先例）
- `src/cad2gis/cad2gis_v3/ports.py:150-330`：`_transform_facts()` 的 fail-closed 契约
- `src/cad2gis/cad2gis_v3/ports.py:528-542`：`resolve_insert_affine()` 公共边界
- `src/cad2gis/cad2gis_v3/plan_domain.py:394-499`：INSERT 展开调用 `resolve_insert_affine()`，缺 facts 则 abstain（当前非 blocking warning，因此 run 继续但块内文字丢失）

---

## 2. 目标与验收（Definition of Done）

1. **Reader 输出完整 INSERT transform facts。**
   对四个 APD（As Plan Drawing）开发基线 DWG，所有 model-space INSERT（以及后续嵌套 INSERT）的 `raw_properties.transform_facts` 都包含六项且 status=`available`：
   `insertion_point`（含 z）、`block_base_point`、`scale`、`rotation`、`normal`、`extrusion`。
2. **plan_domain 成功物化块定义成员。**
   四个项目重跑后，`plan_domain.derived_entity_count > 0`、`expanded_insert_count == model INSERT 数量`（允许以诊断记录列出无法展开的异常 INSERT），`missing_or_invalid_insert_transform` 警告数降为 0 或只剩被显式 allowlist 的实体。
3. **块内文字进入语义层。**
   `cad_entities` 中能看到带 `plan_domain.materialization="nested-insert-affine"` 的派生 TEXT/MTEXT 记录，文本内容与 `dwgread -O json` 交叉核对一致。
4. **block-only 标签通过物化后进入语义层并优先于生成 handle。**
   `78f5d1a` 已保证 Model 空间可解析标签覆盖 `PTECH-CAD-*`；本 spec 的增量验收是：block-only 样本（如 `MR.XXX.HC.P087`、`FAT.B010`）物化后能进入 `classify_entities`，并按 owner/block-path 优先规则覆盖生成 handle；无可解析标签时仍保留 fail-closed 的生成 handle。
5. **确定性回归不破坏。**
   - 四站点 `delivery_counts` 与 `feature_counts` gate 保持与 reviewed `source_profile.json` 一致；
   - `tools/diagnostics/compare_runs.py` 对“无 GCP run vs GCP registered-run”应报 `IDENTICAL`（几何数量与 source_entity_key/source_handle 集合一致）；
   - `pytest tests/ -q --tb=short`（排除本仓库已存在的 `baselines/apd_hutabohu` 删除与 Linux `_ctypes.FreeLibrary` 环境差异）通过。

---

## 3. 契约与现状缺口

`ports._transform_facts()` 只接受两种输入：

1. 权威容器：`raw_properties.transform_facts`（或 `insert_transform`），其中每个字段有 `<field>_status=available` 才算数；
2. 显式 legacy 兼容：`raw_properties.legacy_transform_facts=true` 或 `transform_facts_compatibility="legacy"`，并且 `scale_x/scale_y/scale_z` 与 `CadStyle.rotation` 非默认值。

我们走路线 1（权威容器），**禁止打开 legacy 开关冒充完整事实**。

| 事实 | 现在 reader 读到了什么 | 缺口 |
|---|---|---|
| `insertion_point` | `ent.ins_pt.x / ent.ins_pt.y`，只写进 `points`/`centroid` | 未写 z；未写入 `transform_facts` |
| `block_base_point` | 无 | 需从 BLOCK_HEADER（`base_pt`）读取，按 block header handle 建索引 |
| `scale` | `ent.scale.x/y/z` → `scale_x/y/z` 字段 | 未写入 `transform_facts.scale` |
| `rotation` | `ent.rotation` → `rotation` / `CadStyle.rotation` | 未写入 `transform_facts.rotation` |
| `normal` / `extrusion` | INSERT 分支未读 | 需探针确认 LibreDWG `INSERT.extrusion`/`INSERT.normal`（或 OCS 字段）的可用性 |
| `block_attributes` | `owner_attribs` 已按 owner handle 预索引（`libredwg.py:1544-1563`、`1295-1311`） | 需验证嵌套块、tag 提取和 value 字段；四个 DWG 中目前仍有 `libredwg_block_attributes_unread` 记录 |

### 3.1 `transform_facts` 目标形状

在 `_build_record()` 的 INSERT 分支成功后写入：

```python
raw_properties["transform_facts"] = {
    "schema_version": "cad2gis.reader-transform-facts.v1",
    "insertion_point": [x, y, z],
    "insertion_point_status": "available",
    "block_base_point": [bx, by, bz],
    "block_base_point_status": "available",
    "scale": [sx, sy, sz],
    "scale_status": "available",
    "rotation": radians,
    "rotation_status": "available",
    "normal": [nx, ny, nz],
    "normal_status": "available",
    "extrusion": [ex, ey, ez],
    "extrusion_status": "available",
}
raw_properties["transform_facts_provenance"] = {
    "insertion_point": "DWG_DIRECT:LibreDWG:INSERT.ins_pt",
    "block_base_point": "DWG_DIRECT:LibreDWG:BLOCK_HEADER.base_pt",
    "scale": "DWG_DIRECT:LibreDWG:INSERT.scale",
    "rotation": "DWG_DIRECT:LibreDWG:INSERT.rotation",
    "normal": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
    "extrusion": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
}
```

约束：

- 读取失败时对应 `*_status` 写 `unavailable`（或 `not_applicable`），绝不能伪造默认值；
- z / normal / extrusion 为零向量按 `ports` 契约处理：normal/extrusion 不得零向量，非竖直（oblique）INSERT 会 fail-closed；
- 默认值本身（scale=(1,1,1)、rotation=0、base=(0,0,0)）**如果是真实读到的，可以写入且 status=available**；只有“未读到”才不可写。

---

## 4. 分阶段实施方案

### Phase 0：LibreDWG 字段探针（0.5 session）

新增只读探针：`tools/diagnostics/libredwg_insert_probe.py`

对四个 DWG 逐一执行：

1. 遍历 `DWG_TYPE_INSERT` 实体，dump：
   - `entity.tio.INSERT.ins_pt.x/y/z`
   - `entity.tio.INSERT.scale.x/y/z`
   - `entity.tio.INSERT.rotation`
   - `entity.tio.INSERT.extrusion` / `normal` / `block_header`
   - owner handle、block header handle
2. 遍历 `DWG_TYPE_BLOCK_HEADER`，dump：
   - `name`
   - `base_pt.x/y/z`（确认 dynapi 字段名，可能是 `base_pt` 或 `base_point`）
3. 交叉核对 `dwgread -O json` side channel（已有 `_read_anon_block_names_json` 可参考），输出 Markdown 报告到 `/tmp` 或 `.omc`。
4. 交付物：确认六项事实在四个样本中的字段名、单位和可读率。探针不改生产代码。

### Phase 1：reader 输出权威 transform facts（1 session）

1. 把 `_read_block_header_names()` 升级为 `_read_block_header_metadata()`：
   - 仍返回 name map；
   - 同时返回 `handle -> base_point(x,y,z)` 与读取状态；
   - 失败时 `base_point_status="unavailable"`。
2. 修改 `_build_record()` INSERT 分支：
   - 读取 insertion z、extrusion/normal（按 Phase 0 探针结论）；
   - 从 block header metadata 取 block base；
   - 组装第 3.1 节 `transform_facts`；
   - 保留现有 `scale_x/y/z`、`rotation` 顶层字段（兼容旧消费者），但 provenance 指向 transform_facts。
3. 同步更新 `raw_properties` 中旧的 `insertion_point/block_base_point/normal/extrusion = None` 占位，改为实际值或明确的 unavailable 状态，避免两个真源互相矛盾。
4. 单元测试（`tests/test_reader_capabilities.py` 或新 `tests/test_libredwg_insert_facts.py`）：
   - mock/fixture 构造 INSERT record，验证 `_transform_facts()` 可解析；
   - 验证 `resolve_insert_affine()` 产生正确 `Affine2D`（平移 + 旋转 + 缩放 + block base）；
   - 验证 missing/zero/oblique 仍 fail-closed；
   - 不依赖真实 DWG 的测试放在 CI，真实 DWG 冒烟测试可标记 `@pytest.mark.skipif` 无文件时跳过。

### Phase 2：plan-domain 物化打通（1 session）

1. 用四个真实 DWG 各跑一次 `cad2gis convert ... --llm off`（或仅到 plan_domain 的诊断入口），断言：
   - `plan_domain.expanded_insert_count > 0`
   - `plan_domain.derived_entity_count > 0`
   - 新派生实体 `cad_role=model`、`raw_properties.plan_domain.materialization="nested-insert-affine"`
2. 重点核对派生 TEXT/MTEXT 的文本、层名、颜色、`owner_handle`：
   - `owner_handle` 应等于根 INSERT handle；
   - 为后续语义归属提供强关联键。
3. 检查 `plan_domain.issues` 中剩余 abstain，逐条归类：
   - 匿名块名未解析；
   - 嵌套 INSERT；
   - oblique/non-planar；
   - 曲线 footprint（CIRCLE/SPLINE）——维持 fail-closed。
4. 对剩余无法展开的 INSERT，确认仍是 warning 而非 blocking；在 run manifest 里可审计数量。

### Phase 3：语义归属不再只靠欧氏距离（1 session，可与 Phase 2 并行）

目标：真实标签归属优先级如下（从强到弱）：

1. **owner/block 关系**：派生文本的 `owner_handle == 目标 INSERT handle`；
2. **block definition 路径**：`raw_properties.plan_domain.instance_path` 指向同一根 INSERT；
3. **layer + text pattern + ACI**（现有 reviewed family 契约）；
4. **坐标最近邻**：仅作为最后 fallback，保留现有 0.01 m 多最优弃权与 max_distance 门禁。

实现位置：

- `src/cad2gis/cad2gis_v3/semantics.py`
  - `_assign_family_annotations()` 增加 `relation_priority` 输入或候选排序键；
  - 候选记录增加 `link_kind`（`owner` / `block_path` / `family_contract` / `distance`）；
  - 同距离内 owner 匹配者优先，且 owner 匹配不受纯几何 tie 弃权影响（有明确 DWG 归属关系就不是歧义）。
- 已在 `78f5d1a` 合入的前置修复保留并作为基线：
  - `_annotation_target_eligible()` 基于 `label_provenance` + `CODE` provenance，允许真实标签覆盖 `PTECH-CAD-*` 生成 handle；
  - `unclaimed_pole_annotations` + `is_pole_identifier_shape()` 的 Model 空间 POLE 标签 fallback。

### Phase 4：块属性进入字段（视需要）

当前 `owner_attribs` 已能把顶层 ATTRIB 塞进 `block_attributes`，但：

- `config.py` 的 field rule 只支持 `block-attribute-integer`（`semantics.py:396-398`）；
- 若用户要求 CODE/TYPE 等直接来自 ATTRIB tag，需要新增 `block-attribute-string` / `block-attribute-float` rule 并过 `SourceProfile`/`MappingRegistry` 校验；
- 本 spec 建议 Phase 4 单独开小 spec，先不混入 transform facts 工作。

---

## 5. 回归与风险控制

### 5.1 不得改变的行为

- Source geometry immutable、evidence-first、fail-closed 原则；
- `source_profile.json` / `mapping_registry.json` 的 reviewed 契约与 SHA-256 绑定；
- 无 GCP 与 GCP 两条路径在 feature inventory 上一致；
- reader 的 crash isolation：任何单实体读取失败不得中断整图；
- 不引入“默认单位/默认 base/默认 normal”这类推断——没读到就是 unavailable。

### 5.2 风险

| 风险 | 缓解 |
|---|---|
| LibreDWG dynapi 在 R2018 APD 文件上字段名/结构不稳定 | Phase 0 探针先取证；所有读取 try/except 并写 unavailable；对比 `dwgread -O json` |
| INSERT 块定义展开后，图例里的标本块也会被物化，可能增加候选实体 | 物化不改变既有 `spatial_filter`/`legend_detector` 判定；先只增实体，再由现有去噪层排除 |
| 展开后 `classify_entities` 的 coverage 记录数量暴涨，可能触发 reviewed coverage policy | 只对 model-root INSERT 展开；block_definition 实体本身不直接进语义层；必要时把派生实体计入同一 coverage 域并观察 |
| 真实标签覆盖生成 handle 后，`expectations.feature_counts` / `delivery_counts` 变化 | 验收标准：几何 feature 数量不变；字段/标签变化是预期，需同步重审 profile expectations |
| oblique INSERT 无法在 2D 交付中精确表达 | 维持 `ports` 的 fail-closed，记录 unresolved，不投影 |
| 匿名块 `*U54/*U41` 等 base point/名称读取失败 | 沿用 anon_block_names side channel；失败实体列入诊断，不允许静默跳过 |

### 5.3 建议验证命令

```bash
# 单点 reader 探针
python tools/diagnostics/libredwg_insert_probe.py "raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg"

# 单元/契约测试
.venv/bin/python -m pytest tests/test_reader_capabilities.py tests/test_semantics_annotation.py -q

# 四项目重跑（先 llm off，避免 LLM 非确定性混入）
cad2gis convert "<dwg>" --project baselines/<site> --run-dir /tmp/<site>-reader-check --llm off

# 有无 GCP 的确定性对比
.venv/bin/python tools/diagnostics/compare_runs.py \
  baselines/<site>/run \
  baselines/<site>/run.review/registered-run
```

---

## 6. 交接给下一个 session 的决策记录

- DSH-008：LibreDWG reader 只输出“读到的”INSERT facts；任何缺失都显式 unavailable，禁止 legacy 推断打开 plan-domain 展开。
- DSH-009：块定义文字与块属性的物化必须在 plan_domain 完成，语义层不直接读取 block_definition 原始实体。
- DSH-010：标注归属优先级固定为 owner/block path > reviewed family contract > 坐标最近邻；坐标最近邻仅作 fallback。
- DSH-011：真实 DWG 标签允许覆盖 `PTECH-CAD-*` 生成 handle；生成 handle 只是 fail-closed 占位，不算语义标签。（已于 `78f5d1a` 实现）
- DSH-012：feature inventory 的回归以 `compare_runs.py` 的 source_entity_key/source_handle 集合为判定，不以网页预览目视为准。

---

## 7. 参考文件索引

- Reader：`src/cad2gis/reader/libredwg.py`
  - INSERT 分支：1270-1358
  - raw_properties：1373-1437
  - owner ATTRIB 预索引：1544-1563
  - block header name：790-818
  - dwgread JSON side channel（含 `78f5d1a` 的 DIMENSION 显示文本扩展）：820-898
- Transform 契约：`src/cad2gis/cad2gis_v3/ports.py`
  - `_transform_facts`：150-330
  - `_affine_from_facts`：360-375
  - `resolve_insert_affine`：528-542
- plan-domain：`src/cad2gis/cad2gis_v3/plan_domain.py`
  - `_materialize_leaf`：227-268
  - INSERT 展开：394-499
- 语义标注：`src/cad2gis/cad2gis_v3/semantics.py`
  - 标注目标资格（`78f5d1a` 已实现）：`_annotation_target_eligible`：239
  - `_assign_family_annotations`：311-401
  - unclaimed POLE 标签 fallback：611 / 694 / 784
- 去噪证据保护（`78f5d1a` 已实现）：`src/cad2gis/cad2gis_v3/spatial_filter.py`
  - `is_pole_identifier_shape`：137
  - label/DIMENSION `evidence_exempt` 逻辑：约 300-520
- 诊断工具：`tools/diagnostics/compare_runs.py`（`78f5d1a` 新增）
- 批量重生成脚本：`scripts/regenerate_runs.py`（`78f5d1a` 新增）
- 证据产物：`baselines/<site>/run/run_manifest.json`、`baselines/<site>/run/evidence.gpkg`
