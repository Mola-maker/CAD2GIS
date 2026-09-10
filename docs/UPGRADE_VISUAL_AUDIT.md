# Manifest 变换的独立逆变换审计

核验日期：2026-09-09。修改范围为 `src/cad2gis/visual_audit.py` 与独立 regression；没有修改输入 DWG、source snapshot、已生成 run、mapping 或验收 gates。

## 实图暴露的问题

旧审计器发现 `crs.osm_anchor` 非空便拒绝逆变换，但这个字段可能只是保留的 Nominatim 区域中心 hint，并非实际应用的 coordinate operation。Kletek 同时保留旧 hint 与新的 sealed OSM matching record；Lamteh SF 的 matcher 为 `unavailable`，实际仍使用 nominal GEODATA/CRS。

| 实图 | Manifest 明确记录的实际操作 | 本次审计处理 |
| --- | --- | --- |
| Kletek | `osm_relative_translation`；GEODATA → EPSG:32749 → EPSG:3857，再平移 `[-3.1110917070730775, 0.9136057806470526]` | 验证 record、source、nominal-operation 哈希以及 OSM matching 文件绑定；逆序撤销平移、CRS、GEODATA |
| Lamteh SF | `dwg_geodata_nominal`；GEODATA → EPSG:23846 → 明确记录的 ID74/WGS84 Helmert pipeline → EPSG:3857 | 不应用 OSM hint；独立逆解原始 forward XY operation |
| Tinggede | GEODATA → EPSG:23890 nominal noop → EPSG:3857，平移 `[-5.980362997845717, -15.372640974426046]` 后返回 EPSG:23890 | 分别反演 OSM wrapper 两侧实际 ID74/WGS84 forward XY operation |

Kletek hint 中约 `[12543751, -821455]` 的旧 translation 没有参与本次转换，不能拿来抵消实际 delivery 坐标。当前 canonical 实现是 `OSMMatchingTransformer`；审计代码不导入或调用这个生产 transformer。

## 数学与边界

记 `G` 为 manifest 中的固定 GEODATA/unit operation，`P` 为明确记录的 CRS pipeline，`M` 为 target CRS → EPSG:3857，`N` 为 EPSG:3857 → target CRS，`d` 为 sealed matching translation。`M` 和 `N` 是分别构造的二维 CRS 操作，涉及 datum 与高度时不能假定二者精确互逆。nominal OSM 的变换链是：

```text
delivery = N(M(P(G(native))) + d)
native   = G^-1(P_xy^-1(M_xy^-1(N_xy^-1(delivery) - d)))
```

local OSM 模式先解 `N` 的二维逆，再撤销已记录的 Mercator translation 和正比例 unit/latitude scale。两种情况都不会重新 fit、估计 rotation 或根据道路外观反推位置。

Lamteh SF 的 pipeline 包含 `push v_3`、3D cartesian/Helmert 和 `pop v_3`。保持原高度的正式 `INVERSE` 与已经交付的二维 XY map 的逆并不完全相同；在本图上直接使用 formal inverse，最大 roundtrip 为约 `2.9e-5 m`。本次先用该 inverse 生成初值，再针对**同一条 manifest forward pipeline**以 central-difference Jacobian 做有界 Newton XY 求解。没有修改 Helmert 参数，也没有提高 `1e-5 m` 的数值 roundtrip 门槛。反向求解不收敛或 derivative 奇异时明确阻塞。

Tinggede 的 nominal pipeline 是 noop；约 `1.95e-5 m` 的旧审计超差发生在 OSM wrapper 两侧 datum 操作。实际 GEODATA design point 上，两条近似逆各贡献约 `9.3e-6 m` 的误差。最终修复仅将审计器的两次 CRS 反向调用改为分别求解 `N_xy^-1` 与 `M_xy^-1`，复用上述独立、有界 solver；没有改动生产 forward、生产 inverse、匹配参数或验收 gates。

审计只接受明确的 `nominal_direct`、`dwg_geodata_nominal`、`identity_residual`、`osm_relative_translation` lineage。未知或缺失 lineage、互相矛盾的 GEODATA/nominal 声明、未审查的 coordinate scale、缺失 OSM record binding、篡改 hash、错误 region 或缺少所需 CRS pipeline，均不会被当作 nominal 操作继续。Residual calibration 仍超出本审计器支持范围。

## 两份实图结果

| 指标 | Kletek | Lamteh SF |
| --- | ---: | ---: |
| Delivery features | 263 | 56 |
| Source geometry groups | 239 | 32 |
| 数值/曲线采样容差内 groups | 228 | 30 |
| 已记录的派生几何变化 groups | 9 | 2 |
| 无直接 source 的派生 groups | 2 | 0 |
| 未解释的几何变化 groups | 0 | 0 |
| 字段与记录来源不一致 | 0 | 0 |
| CABLE groups | 9 | 1 |
| CABLE 最大 source-relative Hausdorff difference | `2.124286977780723e-9 m` | `1.5618468403099675e-9 m` |
| CABLE 最大长度差绝对值 | `1.8581545191409532e-9 m` | `1.2089458323316649e-9 m` |
| Source roundtrip 点数 | 1787 | 1506 |
| Delivery roundtrip 点数 | 814 | 106 |
| 最大 source roundtrip | `2.2867884556182997e-9 m` | `2.958571586542623e-9 m` |
| 最大 target grid roundtrip | `1.9199706733309015e-9 m` | `1.9199706733309015e-9 m` |
| 原坐标顶点图比较 | equal | equal |
| Run/source 绑定文件 before/after | unchanged | unchanged |
| Canonical run status | CONDITIONAL | CONDITIONAL |

这是源图相对几何保真度和逆变换数值误差，不是纳米级工程测量能力；也没有证明 OSM 对现实地物的定位误差。两份 run 的 `absolute_accuracy_verified` 仍为 false。审计成功的 `status=EXECUTED` 仅表示程序执行完成。

最终审计目录：

- Kletek：`/tmp/kletek-anchor-audit-v1`
- Lamteh SF：`/tmp/lamteh-sf-anchor-audit-v2`

Lamteh SF 的初次 formal-inverse 尝试保留在 `v1`，其报告为 `SKIPPED_UNSUPPORTED_TRANSFORM`，记录触发 roundtrip guard 的实际数值。成功的 `v2` 不覆盖这个失败记录。

## Kletek 必须工程复核的 9 项

这些变化全部属于 BOITE 的 `collocate_with_support`。原始 lineage 没有 `requires_review` 或工程 acceptance 字段，因此审计报告保留 `declared_requires_review=null`、`declared_review_status=not_recorded`，并明确设置 `audit_requires_review=true`、`engineering_acceptance=not_established_by_lineage`。

| BOITE CODE | 原 handle | 目标 support handle | 实测源点到交付点位移 m |
| --- | --- | --- | ---: |
| KTK5.086.A01 | CD8 | 12D9 | 3.696544492277 |
| KTK5.086.A02 | 133E | 12CD | 3.696544492034 |
| KTK5.086.A03 | 1343 | 12C4 | 3.696544491425 |
| KTK5.086.A04 | 1348 | 12A8 | **3.696544493018** |
| KTK5.086.A05 | 134D | 12B4 | 3.696544492597 |
| KTK5.086.B01 | 1357 | 128D | 3.696544492008 |
| KTK5.086.B02 | 1352 | 127E | 3.680850737293 |
| KTK5.086.C01 | 135C | 129C | 3.696544491764 |
| KTK5.086.C02 | 1361 | 12A5 | 3.696544492503 |

逐项问题是：这个盒体在工程语义上是否应与该支撑点共址，还是原 CAD insert anchor 应当保留？可追溯的自动操作不等于工程上正确或已经接受。本次只生成 review 清单，不批准这些位移，也不修改原 run。

Lamteh SF 的两项 BOITE 为 `1CB380/15AA06`、`1CB380/15AA07`，操作包含 `rectangular_frame_centroid` 与 `collocate_with_support`，对应 support `1C9D5D`。约 `3.112457005 m` 与 `3.107443319 m` 是 **source LineString 到 delivery Point 的 Hausdorff difference**，包含几何表示变化，不能直接解读为两个 anchor 之间的纯平移距离。这两项同样在 review CSV 中待核。

每份报告新增 `geometry-review-required.csv`，保留 CODE、source/support handles、operation、距离定义、原始 review 声明和审计建议；完整 JSON 保留相同列表。

## Tinggede 最终实图复核

原始失败记录保留在 `/tmp/cad2gis-verification-final/tinggede/report.json`；成功审计另写入 `/tmp/tinggede-anchor-audit-v2`。输入为同一份 final run 与已绑定 source snapshot，未重做转换或修改 delivery。

| 指标 | 结果 |
| --- | ---: |
| Source / delivery roundtrip 点数 | 3566 / 1390 |
| 旧审计最大 source / target roundtrip | `1.9508683631480923e-5 / 1.8664709821322345e-5 m` |
| 修复后最大 source / target roundtrip | `9.927793919652025e-9 / 8.614733815193176e-9 m` |
| 原数值门槛 | `1e-5 m`，保持不变 |
| Delivery features / geometry groups | 515 / 466 |
| 数值和曲线采样容差内 groups | 439 |
| 已记录派生变化 / 无直接 source 的派生 groups | 26 / 1 |
| 未解释几何变化 / 字段来源不一致 | 0 / 0 |
| CABLE 最大 Hausdorff difference / 长度差绝对值 | `0.7571910683809934 / 0.6557686498508417 m` |
| Source / run SHA before-after | unchanged / unchanged |
| 审计执行 / canonical run | EXECUTED / CONDITIONAL |

26 项工程 review 包含 22 项 BOITE `collocate_with_support`（最大源点位移 `6.523633561033758 m`），以及 2 项 CABLE 和相应 2 项 INFRASTRUCTURE `bridge_cable_endpoint_to_pole`（最大源线到交付线 Hausdorff difference `0.7571910683809934 m`）。它们在原 lineage 中均没有显式 `requires_review` 或 acceptance 声明，审计全部要求人工复核。坐标顶点图显示 `different`，即使顶点数、边数和 component count 相同；不能把有来源记录的端点改动描述为保持原始几何完全不变。绝对测量精度仍未验证。

## 复现与验证

```bash
python -m pytest tests/test_visual_audit_transforms.py tests/test_semantic_visual_audit.py -q
python -m cad2gis.visual_audit --source-run SOURCE_SNAPSHOT --run CANONICAL_RUN --output NEW_AUDIT_DIRECTORY
```

测试结果为 28 passed，Ruff 与 `git diff --check` 通过。Regression 对照 canonical OSM/GEODATA forward，覆盖非 Mercator target、毫米 native units、local base、实际 ID74 pipeline、Tinggede 双 datum 操作、旧 hint、lineage/region/hash/scale 拒绝以及派生变化的 review 标记。实图另行执行全量审计，未把 synthetic 测试当作实图验收。

本次查阅的 primary documentation：

- [pyproj Transformer](https://pyproj4.github.io/pyproj/stable/api/transformer.html)：pipeline、`INVERSE`、`always_xy` 与 `errcheck`。
- [PROJ pipeline](https://proj.org/en/stable/operations/pipeline.html)：显式顺序操作及逆向步骤。
- [PROJ push/pop](https://proj.org/en/stable/operations/conversions/push.html)：保留垂直分量、只变换水平分量的 Helmert 用例。
- [PROJ Helmert](https://proj.org/en/stable/operations/transformations/helmert.html)：Cartesian transformation 与 rotation convention。
