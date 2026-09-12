# 0.7.0 九张真实 DWG 回归

本次以用户确认的 **Git `baselines/` 文件夹**为标准，基准提交为 `1d4086213f8bb882d8ab6cddd64cb1cdf434a6e9`。9 张原始 DWG 的 SHA-256 全部符合原配置；源事实导出共 **152,621 条实体**。当前 **8 张完成转换，共 3,289 个交付要素**；Manado 的现读清单与原 baseline 不一致，继续保留阻断。8 张完成运行均为 `CONDITIONAL`，不是新增人工验收。

真实执行环境为 Windows、Python 3.12、CAD2GIS 0.7.0 当前工作区代码、LibreDWG CLI DXF 读取器、ezdxf 1.4.4。此项是实际图纸功能回归；安装版 wheel、Windows/Linux/Android 软件包验收另见 [交付报告](UPGRADE_DELIVERY_2026-09-11.md)。

## 配置和原件保护

- 未修改 baseline 的 source profile、mapping registry、人工 GCP、计数、审核状态或 inventory hash。
- 回归前后对 Git 跟踪的 **64 个 baseline 文件**逐项 SHA-256 比较，全部不变；与主任务提前保存的 `baseline-reference.json` 也逐项一致。
- 9 张原始 DWG 回归后重新计算 SHA-256，全部不变。所有项目副本、同字节历史名称副本及运行产物都在新的验证目录内。
- Source export 的 `reader_records == gpkg_entities` 在 9 张图上均成立。这表示读取事实没有在导出时丢弃，不表示全部 CAD 实体都应该被分类为 GIS 工程要素。

机器可核验总表及全量 hash 位于 `E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/corpus-upgrade-final.json`。原始失败记录保留于同根目录的 `corpus/`，后续验证不覆盖它。

## 逐图转换和 SVG 覆盖

SVG 的统计粒度是“数据库作用域与图层中的交付点”。`source_present` 表示已找到可绑定的源图元，`rendered` 表示生成了相应符号资产；这些数量不是语义正确率或图例准确率。

| 图纸 | 源实体 | 交付要素 | SVG 交付点 | 找到源图元 | 已渲染 | 待审阅 | 结果 |
|---|---:|---:|---:|---:|---:|---:|---|
| Lamteh main | 63,483 | 1,000 | 671 | 671 | 248 | 423 | CONVERTED / CONDITIONAL |
| Lamteh SF | 59,369 | 56 | 30 | 28 | 28 | 2 | CONVERTED / CONDITIONAL |
| Tinggar | 11,141 | 715 | 577 | 577 | 142 | 435 | CONVERTED / CONDITIONAL |
| Kletek | 1,538 | 263 | 210 | 210 | 43 | 167 | CONVERTED / CONDITIONAL |
| Semarang SF | 1,455 | 39 | 20 | 20 | 20 | 0 | CONVERTED / CONDITIONAL |
| Taipa | 3,276 | 673 | 508 | 508 | 139 | 369 | CONVERTED / CONDITIONAL |
| Tinggede | 2,601 | 515 | 411 | 411 | 74 | 337 | CONVERTED / CONDITIONAL |
| Darat Sekip SF | 4,701 | 28 | 15 | 15 | 15 | 0 | CONVERTED / CONDITIONAL；绑定历史源事实重放 |
| Manado–Tomohon uplink | 5,057 | — | — | — | — | — | BLOCKED_STALE_INVENTORY_BINDING |

Tinggar 的 BOITE 31/31、SITE 2/2、PTECH 109/109 已有渲染资产；435 个 IMB 仍为 `UNRESOLVED_NON_BLOCK`，保留审阅队列。5 个采用配置指定替代图元，33 个含展示文字叠加，均有明确标记。没有把替代图元声明为原实例完全忠实。

Lamteh 的真实 `INSERT → ATTRIB` owner 链崩溃已修复，两个完整图纸均通过此前失败的 SVG 阶段。SF 仍有 2 个 `UNRESOLVED_NESTED_HANDLE`，没有凭空补符号。其余大批未渲染点主要为非块 IMB，不能用块符号提取成功掩盖这部分差距。所有完成运行均保留 `auto_applied=false` 与 `complete_legend_correspondence_verified=false`，SVG 审阅队列可定位原 CAD 坐标。

8 份完成产物的 SQLite `integrity_check` 均为 `ok`，manifest 绑定文件核验通过。独立 source→delivery 几何审计均为 **0 个未解释几何变化、0 个意外字段失配**，数值往返检查通过；审计前后源及交付产物 hash 不变。显式派生位移与派生几何仍单独计入，不能把此结论表述为所有交付坐标完全不动或独立测量精度已验收。

## 六份初始清单阻断如何处理

### 四份确认为历史文件名绑定

从原 `config/osm_anchor.json` 的 `source_path` 和历史文档查到原始 basename。仅在验证目录内复制相同 DWG 字节，再用验证通过的 native records 重新调用 `inspect_source`。以下四份重建得到的**完整** inventory hash 与未修改的 baseline 精确相等，随后执行原 `validate_project → convert_project → artifact integrity → visual_audit`：

| 图纸 | 已核验原绑定 basename | inventory SHA-256 |
|---|---|---|
| Kletek | `APD - KLETEK RW 05 SIDOARJO.dwg` | `bf44ebd977cf650b5e9a9b475eb1481547b6dcaa651dffccdf0a9fe0cd15744a` |
| Semarang SF | `APD - BULU LOR RW 05 SEMARANG - SF.dwg` | `2c6bb2a7d26841111fa2297b1657aec498925034df5a3701a182053ccd905f94` |
| Taipa | `APD - TAIPA RW 05 PALU.dwg` | `ce35d5023f96dfead9e7b46e9581ed1004dc889fbeff94d86e7f1a319755f9ae` |
| Tinggede | `APD - PERUMAHAN TINGGEDE VIEW PALU.dwg` | `42b9ac1812f3423ed71a1a4d1e8c59c990a43da234aef9bad6684debc8cf2eb8` |

现有 `docs/NINE_DWG_SOURCE_NAMES.json` 的 Semarang 下载名称并非当前 baseline 绑定名称，本次使用独立验证映射文件，不修改该文档或 baseline 来消除阻断。具体证明：`corpus-binding-research/filename-binding-proof.json` 与 `source-names.json`。

### Darat：找回完整绑定的历史源快照

当前读取与 2026-09-10 封存读取具有相同实体集、Model census、样式与 reader protocol，但 2 条 MTEXT 的文本分别从历史 748/746 字变为当前 385/385 字。只改名称不能恢复完整 hash。

在 `E:/branch_CAD2GIS/validation/multipage-20260910/sources-baseline/drawing-07/source` 找到原件 SHA 相同、manifest 绑定完整的 native snapshot。正式 `load_native_snapshot` 校验后，以历史名称 `APD - DARAT SEKIP RW 12 PONTIANAK - SF.dwg` 重新 `inspect_source` 得到：

`488e5126ea596513dedcd0c65f472fc589fb8ca0eb96664c6205616d72a8dd04`

此值精确等于 Git profile 和 registry 原绑定，因此使用原 baseline 在新目录完成转换。结果为 **28 个要素**；它不是历史 Claude 新建 proposal 的 118 个要素方案。该成功证明封存事实可重放；**不能宣称当前新读 MTEXT 差异已经修复，也不能以旧文本较长判断其更完整**。并行原生 reader 调查观察到 `dwg2dxf` 输出尾部混入其他对象文字，同一文件多次读取也出现尾部变化；因此文本长度差不应直接归因为源图纸漏字，须结合独立 `dwgread` 原始字段核验。该调查证据位于工作区 `tmp/reader-investigation/`。

### Manado：保留现读阻断和具体差异证据

当前清单为 `981b205d73e2a208dc9de2249ddd6f2e9f817c9d5b7feff6c39227f27833dfcc`；当前 Git baseline 期待 `c19e4259ed8c6348c0ba4755814248d307332a5cd8c4573a2b69f12b1bc74630`。Model 实体 2,788、INSERT 125、DIMENSION 43 已符合当前 baseline，所以本次不能使用旧 paper layout 角色迁移说明代替实际诊断。

找到的 2026-09-10 同源封存 snapshot 经正式重建为 `90f0ae4d6922a3459d1cdc54d669edd99a23976fa8aadc5949cbffca1073d993`。Git `065c159` 曾使用此绑定；`38a1b38` 的 profile 注册更新引入现在的 `c19e4259…`。现读与可用旧 snapshot 的清单差异仅涉及 3 条 MTEXT 文本（315→252、303→162、313→252 字），实体数量与样式事实一致。现有完整旧 snapshot 仍不匹配最新批准绑定，不能把它当作可直接继续运行的授权。

本次没有拼接不同读取轮次的文本凑 hash，没有自动重绑 profile，没有 bootstrap 新 proposal，也没有下调计数。详细 entity key、文本长度与 SHA、清单差异、封存位置在 `corpus-binding-research/reader-fact-differences.json` 与 `historical-inventory-locations.json`。原始文本仅保留在本地验证数据，不放入源码交付。

## 人工标准与冻结 OSM 比较

Taipa/Tinggede 的第一次 `matching=osm` 运行收到 `osm_roads_unavailable`，translation 为零且没有 p95，不能用这次零位移判断算法精度。随后从 Git 中原有 `.osm-cache` 直接取道路快照，保留原 OSM way/node IDs、标签和拓扑事实，构造离线 Overpass 输入；没有联网补点、虚构节点或改 baseline。EPSG:3857→4326→3857 坐标往返最大差分别为 `1.8629e-9`、`1.8632e-9` 米。

冻结输入的逐项来源与 hash：`corpus-frozen-osm/provenance.json`；独立新运行目录：`corpus-recheck-frozen-osm/`；人工/自动完整比较：`corpus-osm-manual-comparison.json`。原人工记录方法为 `manual_translation_from_original_base`，metadata 为 `authority=relative_osm`、`absolute_accuracy_verified=false`；尊重其作为用户选定参考的地位，同时原样保留该文件自身的权威性标签。两者差异只用于软件回归比较，不等同独立测量误差。

冻结快照运行的 Taipa、Tinggede 均为 `matched`，两份重新转换与独立产物审计通过，交付要素数量不变。已核对原件 SHA、source/target CRS、nominal operation SHA 和整个 `base` 相等，因而以下位移差可在共同的 EPSG:3857 基础上比较。

| 项目 | 人工 translation_3857 | 新算法 translation_3857 | 两个位移向量之差 | 自动相对路网 p95 | 自动覆盖 / 支撑路线比例 |
|---|---|---|---:|---:|---|
| Taipa | `[21.083213, -13.120013]` | `[18.771466, -12.652312]` | 2.358584 m | 11.822495 m | 100% / 100% |
| Tinggede | `[-4.669295, -16.722200]` | `[-5.831823, -16.441781]` | 1.195871 m | 10.194102 m | 96.9089% / 87.5% |

自动方法为 `observable_multi_route_translation_consensus`，分别选择 17/16 条 CABLE 路线代理（没有已分类 ROAD），即 `cable_proxy_only`，不能将电缆线与道路中心线的差解释为纯坐标误差。两份自动运行均采用方向兼容的邻近线段约束；confidence gap 分别为 0.391702/0.109639。搜索仍明确 `search_complete=false`，此差距只是在受限候选集合内计算，没有宣称全局最优。

人工记录自身的独立保留检查点数为 4/3，check RMSE 为 4.885911/7.481146 m；这些原记录指标与自动电缆→路网 p95 的目标和统计口径不同，不直接比较高低，也不升级为现场测量精度声明。自动运行始终保留 `authority=relative_osm`、`absolute_accuracy_verified=false`。

原人工文件 SHA-256：Taipa `fa9a3f71cffcb138b330f439c1bb87d91147af852423af35cd642c325134aea1`；Tinggede `8bd9b77307438255d680e4380327ca1642d2d299aefdc1931402b8b30609e024`。Git 冻结参考分别包含 137/170 条道路；归一化格式只为通过现有离线入口读取，没有增加参考覆盖范围。

## 可复跑命令与证据目录

所有目录位于 `E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/`。完整机器输出不作为源码附件打包。

```powershell
$env:PYTHONPATH='E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/src'
& 'E:/branch_CAD2GIS/.venv-multipage-final/Scripts/python.exe' tools/verify_upgrade_corpus.py `
  --input E:/source_dwg --baselines baselines --output <new-output-dir> `
  --reuse-exports E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/corpus `
  --source-names E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/corpus-binding-research/source-names.json `
  --drawing-id kletek --drawing-id semarang_sf --drawing-id taipa --drawing-id tinggede `
  --matching osm --jobs 2
```

Darat 用 `--source-snapshots .../corpus-binding-research/historical-source-snapshots.json`、`--source-names .../corpus-binding-research/source-names-with-historical-snapshot.json` 及 `--drawing-id darat_sekip_sf`。冻结 OSM 的 Taipa/Tinggede 验证额外传 `--osm-data-dir .../corpus-frozen-osm`。输出必须使用尚不存在的目录，旧失败及成功产物均保留。

- `corpus/`：首次 9 图原始事实、原失败、Tinggar 完成结果。
- `corpus-recheck-lamteh/`：Lamteh main/SF 修复后的完整重放。
- `corpus-recheck-bound-names/`：四图同字节历史名绑定重跑。
- `corpus-recheck-historical-snapshots/`：Darat 原绑定快照重放。
- 各完成目录的 `report.json`、`run/run_manifest.json`、`audit/report.json`、`run/svg-candidates/correspondence.json`、`run/svg-candidates/svg-review-queue.csv`：真实产物、覆盖与剩余审阅入口。
