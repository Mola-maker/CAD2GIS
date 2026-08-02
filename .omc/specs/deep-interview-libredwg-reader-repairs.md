# Deep Interview Spec: LibreDWG Reader 修复

## Metadata
- Interview ID: 8f3b2c71-d4a1-4e9f-b530-c3a67e1f8d22
- Rounds: 6 (Round 0 + 4 + 1)
- Final Ambiguity Score: 11.4%
- Type: brownfield
- Generated: 2026-07-28
- Threshold: 0.2 (20%)
- Threshold Source: default
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.95 | 0.35 | 0.333 |
| Constraint Clarity | 0.85 | 0.25 | 0.213 |
| Success Criteria | 0.85 | 0.25 | 0.213 |
| Context Clarity | 0.85 | 0.15 | 0.128 |
| **Total Clarity** | | | **0.886** |
| **Ambiguity** | | | **11.4%** |

## Topology

| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| INSERT 块属性遍历 | active | 在 `_build_record` INSERT 分支遍历 `entity.tio.INSERT` 的子 ATTRIB 实体，提取属性名→值 | Round 1-2 确认：已有项目 delivery counts 可变化 |
| 匿名块名解析 ctypes 重构 | active | 用 LibreDWG C API 直接读取匿名块有效名，替代 `_read_anon_block_names_json` 的 dwgread JSON 侧通道 | Round 4 确认：绕过 dwgread，用 ctypes |
| BLOCK/ENDBLK/SEQEND 统计清理 | active | 在 `_build_record` 入口跳过控制类型，消除统计噪音 | 可选但建议修 |
| CIRCLE/ARC/ELLIPSE 几何提取 | deferred | 非线性几何暂不处理，FTTH 设计不涉及 | Round 3 用户确认推迟 |

## Goal

修复 LibreDWG reader 中导致 INSERT 块属性丢失和匿名块名无法解析的两类算法缺陷，用 LibreDWG C API 直接读取替代 JSON 侧通道，使 Kletek/Lamteh/Hutabohu 的 INSERT 实体能正确提取属性值、ATTRIB 实体能正确归属 block owner 名。

## Constraints

1. **不破坏已有项目**：Hutabohu/Lamteh 的 bootstrap→validate→convert 必须继续走通
2. **delivery_counts 变化可接受**：修复后属性值被正确填充，已有项目的 counts 可能变化
3. **只用 ctypes，不依赖 dwgread**：匿名块名解析走 LibreDWG C API，消除 JSON 侧通道脆弱性和外部进程依赖
4. **修复范围仅限 `src/cad2gis/reader/libredwg.py`**：不涉及 pipeline、semantics、验证层
5. **保持向后兼容**：`_BINDING_EXPORTS`、`_TYPE_NAME_SPECS` 中的已有条目不动

## Non-Goals

- 不修改 CIRCLE/ARC/ELLIPSE/SPLINE 的几何提取
- 不修改 pipeline、mapping_registry、semantic 层的任何代码
- 不修改 ATTRIB 实体的 text_value 提取（TEXT 分支已处理）
- 不涉及 AutoCAD reader

## Acceptance Criteria

- [ ] INSERT 实体的 `block_attributes` 字典不再为空，包含至少 `{属性TAG: 属性值}` 的键值对
- [ ] `libredwg_block_attributes_unread` 丢行数从三项目各自的 INSERT 实体数降为 0
- [ ] 匿名块名不再依赖 `dwgread` 外部进程，`_read_anon_block_names_json` 函数被废弃或删除
- [ ] `libredwg_block_name_unreadable` 丢行数降为 0（ATTRIB 的 owner block name 全部可解析）
- [ ] BLOCK/ENDBLK/SEQEND 不再出现在 unsupported_inventory 的 entity_keys 中
- [ ] `pytest tests/test_reader_capabilities.py -q` 全部通过
- [ ] Hutabohu 项目重新 `bootstrap → validate → convert` 成功
- [ ] Lamteh SF 项目重新 `bootstrap → validate → convert` 成功
- [ ] Lamteh Main 项目重新 `bootstrap → validate → convert` 成功
- [ ] Kletek 项目的 `libredwg_block_attributes_unread` 和 `libredwg_block_name_unreadable` 降为 0

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| 修复后已有项目不受影响 | Round 1: delivery_counts 会变 | 用户接受变化，不对已有 counts 做兼容 |
| CIRCLE/ARC/ELLIPSE 需要修复 | Round 3: FTTH 设计不涉及非线性 | 推迟，不做 |
| dwgread JSON 是唯一方案 | Round 4: ctypes 直接读可行？ | 绕过 dwgread，用 LibreDWG C API |

## Technical Context

### 修复文件

单文件修改：`src/cad2gis/reader/libredwg.py`

### 修复 1：INSERT 块属性遍历

**位置**：`_build_record` L1103-1132，INSERT 处理分支

**当前代码**（L1128-1130）：
```python
# Attributes are not traversed in this dev reader.
block_attributes = {}
reasons.append("libredwg_block_attributes_unread")
```

**修复方向**：
- INSERT 实体的 `entity.tio.INSERT` 结构在 LibreDWG SWIG 中暴露了子实体访问
- 需要遍历 INSERT 的所有者块定义中的 ATTRIB 定义，然后匹配 INSERT 实例中的 ATTRIB 值
- LibreDWG ctypes 提供 `dwg_dynapi_entity_utf8text` 可读取属性文本值
- 属性 TAG 名称可从 ATTDEF 定义中获取，属性 VALUE 从 ATTRIB 实例中获取

**实现方案（已验证）**：

在 `extract_dwg_records` 中，遍历 `data.object` 一次，按 ownerhandle 建立 ATTRIB 索引：
```python
owner_attribs = defaultdict(list)
for i in range(data.num_objects):
    obj = Dwg_Object_Array_getitem(data.object, i)
    if obj.supertype != DWG_SUPERTYPE_ENTITY: continue
    if obj.type != 2: continue  # DWG_TYPE_ATTRIB
    oh = obj.tio.entity.ownerhandle
    if oh is not None:
        owner_attribs[oh.absolute_ref].append(obj)
```

然后将 `owner_attribs` 传入 `_build_record`。在 INSERT 分支中：
```python
attrs = owner_attribs.get(handle, [])
for aobj in attrs:
    attr = aobj.tio.entity.tio.ATTRIB
    block_attributes[attr.tag] = attr.text_value
```

**验证数据**（Kletek DWG）：
- 67 个 ATTRIB 实体，ownerhandle 分布在 24 个 INSERT 上
- `attr.tag` 返回 'F', 'D', 'L' 等属性名
- `attr.text_value` 返回 'K', 'X', '-' 等属性值

### 修复 2：匿名块名解析

**位置**：`_read_anon_block_names_json` L791-860

**当前流程**：
1. `dwgread -O json` 外部进程导出 DWG 为 JSON
2. 匹配 bare BLOCK_HEADER（`*U`、`*D`）和 numbered companion（`*U48`、`*D1026`）
3. 按 handle 值排序后 gap ≤ 5 配对

**探明结果**：

1. SWIG `bh_swig.name` 返回 `'*'`（比 dynapi 更差）
2. dynapi `_entity_utf8_text(ptr, "BLOCK_HEADER", "name")` 返回 `'*U'`（丢失数字后缀）
3. SWIG 结构体中无独立数字字段
4. **dwgread JSON 是唯一可用的数字后缀来源**，且已工作正常（15 个映射全部正确）
5. 匿名块名的数字后缀并非单独的 C 结构体字段，dwgread 通过对 DWG 二进制特殊解析得出

**结论**：无法用 ctypes 完全绕过 dwgread。保留 `_read_anon_block_names_json`，但修复其脆弱配对算法——改用精确的 handle 差匹配替代 gap ≤ 5 启发式。

**修复方向**：
观察 dwgread JSON 输出模式：每个 anonymous BLOCK_HEADER（`object=BLOCK_HEADER, name=*U`，handle=h）后紧跟一个 companion 条目（`name=*U##`，handle=h+3）。将配对算法改为：对每个 bare BLOCK_HEADER（name in `{'*U','*D'}`），在 numbered 条目中查找 handle 差最小且 ≤ 5 的匹配，同时增加 debug 日志记录未匹配的条目。

### 修复 3：BLOCK/ENDBLK/SEQEND 统计清理

**位置**：`_build_record` L1167-1168 或入口处

**方向**：在 `_build_record` 开头检查，如果 `dwg_type_name in _CONTROL_TYPE_NAMES`，直接返回 `None`。调用方 `extract_dwg_records` 的 L1385 已有 `if record is None: continue` 处理。

### 验证流程

```bash
# 1. 单元测试
pytest tests/test_reader_capabilities.py -q

# 2. Hutabohu 全流程
cad2gis bootstrap "raw/APD - HUTABOHU.dwg" --project baselines/hutabohu_repair --json
cad2gis validate --project baselines/hutabohu_repair --json
cad2gis convert "raw/APD - HUTABOHU.dwg" --project baselines/hutabohu_repair \
    --run-dir baselines/hutabohu_repair/run --json

# 3. Lamteh SF 全流程
cad2gis bootstrap "raw/APD - LAMTEH DAYAH ACEH - SF.dwg" --project baselines/lamteh_sf_repair --json
cad2gis validate --project baselines/lamteh_sf_repair --json
cad2gis convert "raw/APD - LAMTEH DAYAH ACEH - SF.dwg" --project baselines/lamteh_sf_repair \
    --run-dir baselines/lamteh_sf_repair/run --json

# 4. 比对 delivery_counts
python -c "
import json
for proj in ['hutabohu_repair','lamteh_sf_repair','lamteh_main_repair','kletek']:
    m = json.load(open(f'baselines/{proj}/run/run_manifest.json'))
    print(f'{proj}: {m[\"delivery_counts\"]}')
"
```

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| INSERT entity | core domain | ins_pt, scale, rotation, block_header_ref | contains ATTRIB children |
| ATTRIB entity | core domain | tag, text_value, ownerhandle | owned by INSERT or BLOCK |
| BLOCK_HEADER | core domain | name, handle, anonymous_flag | referenced by INSERT.block_header |
| block_attributes | output | dict[str,str] | populated from ATTRIB traversal |
| dwg_dynapi_entity_utf8text | external C API | entity_ptr, object_name, field_name | used for text extraction |
| _read_anon_block_names_json | to-be-removed | source_path, source_sha256 | replaced by ctypes |

## Interview Transcript

<details>
<summary>Full Q&A (5 rounds)</summary>

### Round 0 (Topology)
**Q:** 3 修复组件 + 1 可选清理。拓扑确认？
**A:** 对，修复的前提是不对已有的Hutabohu，Lamteh等图的转换添乱

### Round 1
**Q:** 修好属性提取后已有项目 delivery_counts 会变，接受还是要求透明？
**A:** 接受已有项目的 delivery counts 变化

### Round 2
**Q:** 修复后用什么验证？
**A:** 对 Hutabohu/Lamteh 重新执行 bootstrap → validate → convert 并比对 delivery_counts

### Round 3
**Q:** CIRCLE/ARC/ELLIPSE 用什么精度近似？
**A:** 暂时不做，非线性部分不会用在光缆铺设的设计中

### Round 4
**Q:** 匿名块名解析——修补 dwgread JSON 配对还是绕过改用 ctypes？
**A:** 绕过 dwgread，改用 ctypes

</details>
