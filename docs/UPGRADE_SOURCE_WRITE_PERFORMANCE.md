# Source GeoPackage 字段写入优化

2026-09-09。修改范围仅为 `source_gpkg.py` 的字段绑定与写入循环，未改变 source schema、reader identity、geometry 或产物验证。

Kletek 同一份真实 native reader snapshot 的 **1,538 个实体**重复写入，三组交错测量的中位耗时从 **3.792 s 降为 1.050 s**，source writer 阶段为 **3.61×**。这是 source writer 的结果，不代表整次 conversion 的加速倍率。

## 原因与实现

当前 GDAL Python binding 对 `SetField(name, value)` 中的字符串和整数值调用 `_getfieldindex`，在 Python 循环中逐个读取字段定义。本次 source writer profile 有 63,058 次这样的扫描，累计 5.103 s。

每个 layer 的 schema 建立完成后，新增 `_bind_field_indices` 从真实 `FeatureDefn` 取得字段 index，并核对精确名称和类型；缺字段或 schema 不符立即失败。写入时使用 `SetField(index, value)`。

```python
feature = ogr.Feature(layer_definitions[routed.layer_name])
indices = field_indices[routed.layer_name]
for field_name, value in _entity_values(routed).items():
    if value is not None:
        feature.SetField(indices[field_name], value)
```

对于 N 个实体、F 个字段，逐值查名增加约 O(NF²) 的字段扫描；缓存将查名移到少数 layer 的 schema 绑定阶段，逐实体只做 O(NF) 的字典索引和赋值。OGR 的 `CreateField`、`CreateFeature`、transaction return checks、rollback / close 和原有 validation 均保留；borrowed layer definitions 在关闭 dataset 前释放。API 行为见 [GDAL Python Vector API](https://gdal.org/en/stable/api/python/vector_api.html)。

## 真实数据等价与耗时

测试通过 `load_native_snapshot` 验证并加载原 reader records，没有重新解释 DWG 或绕过 reader identity。

| 项目 | 结果 |
| --- | --- |
| DWG SHA256 | `756f6e5bb4feb61fed2d86046478e0fb714434628bd0f17db4d78fef0b221016` |
| reader records SHA256 | `5ddfca631c084f7d299ebae823c6d4ac1c67aafb72b43420c0b42d71477ba80e` |
| 原实现三次 wall time | 4.114 / 3.574 / 3.792 s |
| 候选三次 wall time | 0.687 / 1.852 / 1.050 s |
| 全量 SQL 比较 | 36 tables，6,183 rows，92,245 values；schema、类型和值全部一致 |
| Geometry 比较 | 1,490 个完整 GeoPackage geometry BLOB 字节一致 |
| NULL 比较 | 2,423 个 NULL 保持一致 |
| Logical SHA256 | 六次输出一致：`1812e45826f05cefbc13245fe8eaf09e6efc45fa546c3943da5fe0ce392e20e1` |
| GeoPackage byte SHA256 | 六次输出一致：`f253be586ac120f6acc6dd2addea43f21c335fe04beac6ad33f317f2334d4277` |
| `SetField` 次数 | 原实现与候选均为 69,871 |
| Python `_getfieldindex` 次数 | 63,058 → 0 |

测量来自同一 Linux 环境、三组交错运行，存在宿主负载波动；完整原始时间保留，没有以最快单次代替中位数。

## 回归与证据

执行 `tests/test_source_gpkg_field_indices.py`、`tests/test_source_gpkg.py`、`tests/test_source_replay.py`、`tests/test_source_snapshot_queries.py`：**50 passed，1 skipped**。跳过的是 Windows DACL 专用测试。修改的 Python 文件通过 Ruff。

新增六项测试覆盖实际字段顺序、避免逐 feature 查名、scalar / NULL / 空字符串、缺字段、大小写与类型不符，以及失败时保留原产物并清理 staging。没有用绝对耗时阈值做 CI gate；新增文件已纳入 offline precision job。

原始证据位于 `/workspace/scratch/7489590ca040/verification/source-field-index-kletek/`：`report.json`、两个 profile、baseline / candidate 源码、benchmark script 和一对 byte-identical GeoPackage。生产源码与实测 candidate 的 SHA256 均为 `a026d665a982f11540f6043f290cfe174a81ab507077b98dd03a314cd2c90621`。
