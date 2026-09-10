# OSM 配准与矢量证据层升级

核验日期：2026-09-09。适用模块：`cad2gis_v3/osm_matching.py`、`cad2gis_v3/osm_evidence.py`。

## 已修复的定位缺陷

| 原有行为 | 实际问题 | 新行为 |
| --- | --- | --- |
| 以 CAD segment 数量判断结构是否充分 | 一条直线加上冗余顶点即可满足条件；多条平行线仍无法确定沿线方向的位置 | 以按长度加权的法向信息矩阵检查两个平移自由度 |
| 相距不超过 12.5 m 的候选视为同一个解 | 相距 8 m 的重复路网被合并，产生虚假的 confidence gap | 仅合并收敛位置相距不超过 0.25 m 的数值重复解 |
| 对最近线段的偏移量逐坐标取 median | 长主方向可能压掉较短、但提供独立约束的转折方向 | 使用 Huber 权重和 point-to-line normal equations 迭代平移 |
| `STRtree.nearest` 只返回一个任意等距对象 | 交叉点附近可能因树遍历顺序选到方向不兼容的道路 | 取全部最近等距对象，按方向一致性、稳定 segment 顺序选择 |
| Overpass 解析去掉重复坐标，丢弃 node IDs | 同坐标不同 node、内部连接点、桥下穿越关系无法追溯 | 保持一一对应的 node/vertex 序列，保存 layer/bridge/tunnel/junction |
| 匹配报告缺少能回看的中间矢量 | 难以区分 source 绘图、地理位置和 OSM 本身的误差 | 保存原始 CAD 路线、base 路线、真实 OSM 拓扑、残差和来源哈希 |

增加了这些失败场景的 regression；保留原来的“候选不可信时仍提供独立区域中心粗定位”的工作流程。有效 nominal CRS 的匹配被拒绝时，不用被拒绝的候选覆盖原位置。

## 为什么直线和两条平行线仍然不够

当前自动 matcher 只估计平移量 `delta = (dx, dy)`。对一个目标线段，令单位法向为 `n`，则 point-to-line residual 随平移的变化为：

```text
r(delta) = n.T @ delta + constant
H = sum(w_i * n_i @ n_i.T) / sum(w_i)
```

`w_i` 是样本代表的路线长度。所有线段平行时，`H` 的 rank 为 1：垂直道路的方向受约束，沿道路方向仍可滑动。两条平行线的整体点云可以有二维空间分布，但它们的法向信息矩阵仍缺少一个约束。因此不能用顶点数量、点云面积或更多平行线代替 observability 检查。

新实现要求归一化 `H` 的最小 eigenvalue 至少为 `0.04`。检查分别作用于 source 结构和实际得到支持的 OSM 线段。该阈值是保守的算法质量门槛，**不是测量误差或统计 confidence interval**。

样本仍沿每条路线均匀按 arclength 生成。每个样本按所代表长度加权，减少最少两个样本的规则对极短路线造成的额外票数。逐路线 coverage 继续用于防止一条长路线掩盖其它路线的不一致。

迭代时用 Huber influence 抑制较大偏移的影响，再通过 `numpy.linalg.lstsq` 求解二维 normal equations。不引入 SciPy，不更新原始 CAD vertices，不做 snapping、rotation 或 elastic warp。

## 证据数据契约

`match_osm()` 在每个新 record 中增加 `evidence`；`refine_match()` 从原始 base 重建新 revision 的证据。原始 OSM vectors 仍保存在 `record.osm_snapshot.roads`，通过 `osm_snapshot_sha256` 绑定，避免复制一整份 OSM 数据。

| 数据 | Coordinate space | 含义 |
| --- | --- | --- |
| `evidence.cad_routes[].native_points` | CAD native coordinates / source units | matcher 实际使用的原始路线，不被配准修改 |
| `evidence.cad_routes[].base_points_3857` | EPSG:3857 | 固定 unit/CRS base 变换后的坐标，尚未加平移 |
| `osm_snapshot.roads[].points` | EPSG:3857 | OSM snapshot 的原始投影 vectors |
| `evidence.osm_topology.nodes` | EPSG:3857 + OSM node IDs | 根据 shared node IDs 建立的 adjacency |
| `evidence.cad_topology.nodes` | CAD native coordinates | 显式重合 CAD vertices 的几何连接；不声称已验证物理连接 |
| `evidence.residuals` | 两个 EPSG:3857 endpoints | 当前 revision 的 CAD sample → 最近 OSM segment 的残差线 |

证据绑定 source SHA-256、OSM snapshot SHA-256、离线输入文件 SHA-256（如果提供）、region、revision、实际平移和 placement status；整体还有 `evidence_sha256`。哈希用于完整性和可复现性，不是数字签名，也不证明 OSM 坐标正确。

读取时检查 evidence 哈希及其与 record 的绑定。新手动 revision 会保留原始 native/base vectors，重新计算 corridor residuals。旧 profile 仍可读取，但其缺失的证据不会被补造。

OSM topology 只认显式 shared IDs，不从几何相交推断连接。同坐标不同 IDs 保持不同节点；同一个 ID 出现冲突坐标则拒绝 snapshot。没有 IDs 的旧 cache 或 geometry-only snapshot 保留为 vectors，`geometry_only_way_count` 明确计数，节点层保持空缺。桥梁、隧道和 layer tags 随矢量一起保留。

CAD cable 路线的分叉与 OSM road junction 不一定一一对应，因此本次不把 CAD vertex degree 当作道路 degree 的硬性匹配约束。这些节点是人工核验依据，不会强迫 cable 几何变成道路中心线。

## GeoJSON 接口

```python
import json
from pathlib import Path

from cad2gis.cad2gis_v3.osm_evidence import evidence_geojson

record = json.loads(Path("osm_matching.json").read_text(encoding="utf-8"))
layers = evidence_geojson(record)
for name, collection in layers.items():
    Path(f"{name}.geojson").write_text(
        json.dumps(collection, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
```

返回的五个 `FeatureCollection` 是 `cad_routes`、`osm_roads`、`cad_nodes`、`osm_nodes`、`residuals`。**GeoJSON coordinates 均为 EPSG:4326 longitude/latitude**，不能当作 metres 使用。无可用 geographic placement 的本地 CAD 不输出假位置。

每个 feature 带有 `authority="relative_osm"` 和 `absolute_accuracy_verified=false`。自动 residual 的 `residual_kind="osm_corridor"`，其中 `distance_m_approx` 使用参考纬度的 Mercator cosine 近似。手动 control residual 的 `residual_kind="manual_control"`，保留 `point_id`、`role`、`error_m`、`grid_error_m`，地面误差按 WGS84 ellipsoidal geodesic 计算；`check` 点不参与拟合。

手动 controls 可以携带 server 管理的 `editor_state`，用于新 review workspace 恢复初始选点和 undo history。它被纳入 control / match 哈希，限制为有限 JSON、最多 32 KiB、128 个历史位置；nudge 只接受 `0.01 / 0.1 / 1 m`。它不改变训练点拟合规则。

## 验证范围与剩余边界

本模块的 regression 使用 deterministic、offline synthetic 几何，覆盖直线加密、平行和近似平行线、8 m 重复路网、主次方向平移、真实连接/桥梁交叉、node 对齐、等距最近线段、手动 revision、原始矢量不变和 evidence corruption。`test_osm_pipeline.py` 覆盖转换集成。真实九份 DWG 的 source extraction / conversion 验证由整合报告单独记录；本文件不将 synthetic 测试结果等同于那些 DWG 的测量精度。

候选搜索最多取 16 条 source proposers，每条使用排名前 6 的 OSM ways，属于有界搜索。报告写明 `search_complete=false`；confidence gap 仅比较搜索到的候选，不保证发现所有全局最优解。对密集重复街区、CAD 与道路没有相同结构、旋转/比例不对或 OSM 缺失的场景，需要人工 controls 或有独立精度依据的 reference data。

自动匹配的 25 m corridor 用于粗配准支持度，不是交付精度承诺。Mercator cosine 距离是局部近似，不能替代跨较大范围的适当 local projected CRS 计算。OSM-relative overlay 即使看起来贴合，也不能证明 centimetre survey accuracy；独立检查点和外部测量参考仍然承担绝对精度验证。

## 本次查阅的 primary documentation

- [Shapely STRtree](https://shapely.readthedocs.io/en/stable/strtree.html)：`nearest` 的等距结果可能依赖遍历顺序；`query_nearest(all_matches=True)` 可取全部等距结果。
- [SciPy least_squares](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html)：Jacobian rank deficiency 与 robust least-squares 的算法边界；本实现使用已有 NumPy 求解二维问题。
- [PROJ coordinate operation selection](https://proj.org/en/stable/operations/operations_computation.html)：operation selection、area of use 与 declared accuracy；投影转换能力不等于 reference data 的实际精度。
- [OpenStreetMap Way](https://wiki.openstreetmap.org/wiki/Way)：way 由有序 nodes 构成。
- [OpenStreetMap layer](https://wiki.openstreetmap.org/wiki/Key%3Alayer)：不同高度的 crossing 不应通过 shared node 被错误连接。
- [OpenStreetMap Accuracy](https://wiki.openstreetmap.org/wiki/Accuracy)：OSM 数据误差及来源差异。
