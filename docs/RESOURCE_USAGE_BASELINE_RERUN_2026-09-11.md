# baselines 串行全量重跑 — 资源占用报告

- 生成时间：2026-09-11T22:16:26+08:00
- 主机：12 vCPU / 15707 MiB 内存 / Python 3.12.13 / LibreDWG 0.14
- GIS 运行时：pyramids-gis bundled GDAL runtime is active；QGIS 3.44.11-Solothurn 'Solothurn' (f8f5812197f)
- 执行方式：严格串行，一次只运行一个项目（`--jobs 1`），每个项目一个独立受监控窗口。

## 采集方法

- 权威总量：`/usr/bin/time -v` 包裹整个项目脚本（转换 → 审计 → 六文件交付包发布），包含全部已回收子孙进程。
- 时间序列：`monitor.py` 每 1 秒读取 `/proc`，跟踪根进程及其全部子孙进程的 CPU、RSS、块设备 IO，同时记录主机 CPU 利用率、可用内存与 load1。
- 已知偏差：轮询无法观测两个采样点之间启动并退出的短命进程 CPU，因此 CPU 总量以 `/usr/bin/time -v` 为准；采样序列用于观察形态、内存峰值与主机压力。

## 逐项目结果

| 项目 | 结果 | run_status | 墙钟(s) | CPU(s) | CPU(单核%) | 峰值树RSS | 峰值单进程RSS | 读(MiB) | 写(MiB) | 交付要素 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| kletek | CONVERTED | CONDITIONAL | 27.1 | 25.5 | 94 | 247.5 MiB | 299.8 MiB | 1.4 | 370.9 | 263 |
| semarang_sf | CONVERTED | CONDITIONAL | 18.7 | 17.5 | 93 | 251.1 MiB | 284.9 MiB | 1.6 | 325.6 | 39 |
| taipa | CONVERTED | CONDITIONAL | 64.3 | 59.5 | 92 | 953.4 MiB | 956.1 MiB | 2.8 | 925.1 | 673 |
| tinggede | CONVERTED | CONDITIONAL | 54.2 | 50.9 | 92 | 346.3 MiB | 418.1 MiB | 10.3 | 700.2 | 515 |
| manado-tomohon_uplink | CONVERTED | CONDITIONAL | 120.3 | 60.3 | 50 | 429.4 MiB | 556.0 MiB | 72.7 | 1194.9 | 375 |
| darat_sekip_sf | CONVERTED | CONDITIONAL | 62.3 | 38.4 | 61 | 417.2 MiB | 470.0 MiB | 2.1 | 865.6 | 28 |
| tinggar | CONVERTED | CONDITIONAL | 101.7 | 98.4 | 96 | 620.1 MiB | 827.3 MiB | 2.1 | 2141.8 | 715 |
| hutabohu | CONVERTED | CONDITIONAL | 101.1 | 97.8 | 96 | 1.4 GiB | 1.4 GiB | 8.0 | 2308.4 | 1150 |
| lamteh_sf | CONVERTED | CONDITIONAL | 317.2 | 313.6 | 98 | 2.1 GiB | 2.4 GiB | 4.3 | 7587.8 | 56 |
| lamteh_main | CONVERTED | CONDITIONAL | 411.4 | 409.3 | 99 | 3.1 GiB | 3.1 GiB | 63.8 | 8735.5 | 1000 |
| **合计/峰值** | 10/10 转换成功 | — | 1278.3 | 1171.3 | — | 3.1 GiB | — | — | — | 4814 |

## 主机负载与内存压力

| 项目 | 主机CPU均值% | 主机CPU峰值% | 最低可用内存 | load1峰值 | 进程数峰值 | 采样数 | 文件系统输出(块) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| kletek | 10.0 | 18.7 | 10.6 GiB | 1.51 | 5 | 29 | 305080 |
| semarang_sf | 10.3 | 18.1 | 9.9 GiB | 1.11 | 4 | 21 | 257760 |
| taipa | 14.9 | 50.0 | 9.7 GiB | 1.52 | 4 | 68 | 699704 |
| tinggede | 16.6 | 50.0 | 10.2 GiB | 1.66 | 4 | 58 | 518488 |
| manado-tomohon_uplink | 7.4 | 20.4 | 8.6 GiB | 1.02 | 5 | 142 | 878608 |
| darat_sekip_sf | 11.0 | 66.7 | 10.1 GiB | 1.85 | 5 | 66 | 919160 |
| tinggar | 16.7 | 32.6 | 7.6 GiB | 2.03 | 5 | 108 | 1538592 |
| hutabohu | 10.4 | 17.6 | 8.7 GiB | 1.41 | 5 | 109 | 1649600 |
| lamteh_sf | 18.3 | 34.2 | 7.9 GiB | 3.76 | 5 | 337 | 5214952 |
| lamteh_main | 17.1 | 35.9 | 7.0 GiB | 2.45 | 5 | 438 | 6057792 |

峰值主机 CPU 利用率 66.7%，load1 峰值 3.76，可用内存最低 7.0 GiB（主机总内存 15707 MiB）。

## 阶段分解

| 项目 | 阶段 | 开始时间 | 墙钟(s) | CPU(s) |
| --- | --- | --- | ---: | ---: |
| kletek | verify_upgrade_corpus | 2026-09-11T19:36:49+08:00 | 21.222 | 22.418 |
| kletek | publish_match | 2026-09-11T19:37:10+08:00 | 5.833 | 3.113 |
| semarang_sf | verify_upgrade_corpus | 2026-09-11T19:44:32+08:00 | 13.214 | 14.875 |
| semarang_sf | publish_match | 2026-09-11T19:44:46+08:00 | 5.462 | 2.648 |
| taipa | verify_upgrade_corpus | 2026-09-11T19:38:09+08:00 | 56.967 | 56.015 |
| taipa | publish_match | 2026-09-11T19:39:06+08:00 | 7.249 | 3.524 |
| tinggede | verify_upgrade_corpus | 2026-09-11T19:39:13+08:00 | 48.472 | 47.927 |
| tinggede | publish_match | 2026-09-11T19:40:01+08:00 | 6.839 | 3.01 |
| manado-tomohon_uplink | verify_upgrade_corpus | 2026-09-11T21:10:30+08:00 | 55.645 | 56.371 |
| manado-tomohon_uplink | publish_match | 2026-09-11T21:11:26+08:00 | 77.701 | 6.519 |
| darat_sekip_sf | verify_upgrade_corpus | 2026-09-11T19:40:23+08:00 | 34.974 | 35.504 |
| darat_sekip_sf | publish_match | 2026-09-11T19:40:58+08:00 | 27.246 | 2.933 |
| tinggar | verify_upgrade_corpus | 2026-09-11T22:13:35+08:00 | 93.041 | 93.514 |
| tinggar | publish_match | 2026-09-11T22:15:08+08:00 | 8.636 | 4.875 |
| hutabohu | verify_upgrade_corpus | 2026-09-11T20:00:23+08:00 | 93.279 | 93.687 |
| hutabohu | publish_match | 2026-09-11T20:01:56+08:00 | 7.858 | 4.081 |
| lamteh_sf | verify_upgrade_corpus | 2026-09-11T19:47:27+08:00 | 311.032 | 310.614 |
| lamteh_sf | publish_match | 2026-09-11T19:52:38+08:00 | 6.201 | 2.954 |
| lamteh_main | verify_upgrade_corpus | 2026-09-11T19:52:45+08:00 | 401.196 | 402.921 |
| lamteh_main | publish_match | 2026-09-11T19:59:27+08:00 | 10.175 | 6.424 |
## 环境备注

- 本次重跑使用 venv 内置 pyramids-gis GDAL 完成坐标与范围计算；系统 GDAL 3.8 对空 `layer_styles` 表返回非零退化范围，会污染 `match_delivery._gpkg_bounds` 的包络，故 QGIS 子进程通过包装器单独注入 PyQGIS，父进程保持内置 GDAL。
- QGIS 侧使用系统 PyQGIS 3.44.11（`QGIS_PREFIX_PATH=/usr`），交付包内 `.qgz` 的工程与图层坐标系在 `validate_corpus.py` 中逐项比对 config 的 `target_crs`。
- 交付包走 `cad2gis publish-match` 路径（与既有 baselines `delivery/` 布局一致：主体六文件 + 分区子目录），review 底图由 Overpass 在发布时抓取（与既有基线生成方式相同），匹配步骤仍使用各项目 `config/.osm-cache` 的冻结快照。


## 重跑过程备注

- 输入复用边界：每个项目只复用 `baselines/<项目>/config`（SVG 样式注入、图层结构、元素分类与计数规则、CRS/GEODATA 配准、冻结 OSM 缓存）；`run/`、`delivery/`、`run.review/`、`reviews/` 全部由本次运行重建。
- 源文件绑定按 SHA-256 匹配，`baselines/*/config` 的 `source_binding.inventory_sha256` 依赖 DWG 的**文件名**。本次先按 `docs/NINE_DWG_SOURCE_NAMES.json` 传供应商原名，semarang_sf 与 manado-tomohon_uplink 因此 fail-closed（BLOCKED）。改用 `raw/` 下的本地文件名后，用同一批原生记录重建的 inventory 哈希与 config 声明值完全一致（semarang_sf `e1e4f567→2c6bb2a7`、manado `77fed9d4→c19e4259`），两项目随后正常转换。证据见 `evidence/source-name-binding.json`。
- hutabohu 与 manado 各有一次因 stage 目录被中断尝试的残留进程占用而失败的记录（`report_runs` 目录冲突）；stage 路径改为「每次尝试唯一」后重跑通过。表中数值取自各自最终成功的那一次。
- 按操作者要求已取消 3.1 GB 级旧产物备份：旧 `run/` 树直接删除，只保留一份 11 KB 的旧 run 清单审计摘要（`evidence/previous-runs.json`）；`delivery/` 中的 GeoPackage 仍可由 git 恢复。
- 两类**无法由重跑再生**的已提交证据已从 git 原样恢复：`delivery/r2/`（tinggede、darat_sekip_sf 的人工复核 revision 交付包）与 `reviews/20260910-native-layouts/`（manado、semarang_sf、tinggar 的 onboarding 评审凭据，config 的 `review.provenance` 指向它们）。它们与本次标准重跑产物互不影响。

## 交付与校验

- `delivery/` 布局与既有基线一致：主体六文件（delivery.gpkg / delivery.qgz / delivery.review.json / osm-basemap.gpkg / view-with-source-SVG.qgz / view-with-source-SVG.review.json），多分区项目按分区建子目录（Manado 含 EMR28560、EMR29619）。
- `validate_corpus.py` 对 10 个项目逐一核对：manifest 产物哈希、GeoPackage integrity_check、delivery.gpkg 与 run/delivery.gpkg 字节一致、六文件契约、交付计数与旧基线一致、review 工程画布 CRS 固定 EPSG:3857 且每个 CAD 图层 CRS 等于 config `target_crs`、OSM 匹配记录目标 CRS。结果 **10/10 全部通过**（`validation/validation.json`）。


## 峰值归因（lamteh_main，分阶段实测）

为回答「3.1 GiB 是否属于转换管线本身」，把 lamteh_main 的每个阶段拆成独立受监控进程重测（新目录，不发布产物）：

| 阶段 | 墙钟(s) | CPU(s) | 峰值树RSS | 说明 |
| --- | ---: | ---: | ---: | --- |
| `export_source`（读取器→source.gpkg） | 177.5 | 176.5 | 1781 MiB | 读 DWG、物化原生事实快照 |
| `convert_project`（SQLite 转换管线） | 169.5 | 166.9 | 2077 MiB | 10 个 stage：ingest/OSM/evidence graph/source·evidence·delivery GPKG/SVG 候选 |
| `visual_audit`（独立进程） | 8.1 | 9.3 | 2337 MiB | 源/交付渲染比对 |
| `report_runs`（复核报表栅格化） | 5.0 | 3.8 | 48 MiB | SVG→PNG 预览 |

全量运行中 3194 MiB 的峰值是 **worker 进程内、退出前约 2 秒的瞬时尖峰**：视觉审计与刚结束的转换在同一个进程里串行执行，转换残留约 2.3 GiB，审计自身的分配把树峰值推到 3.2 GiB（该瞬间进程数仍为 4，没有新子进程）。因此：**转换管线自身的峰值是 2077 MiB，读取器阶段 1781 MiB**；`cad2gis convert` 单独运行不会有 3.2 GiB 的读数。读取器结束后内存确实回落（全量运行中 2.1 GiB → worker 退出时 61 MiB），符合本次 pull 的「早释放/流式序列化」改动。

代码同一性：本次运行的 `implementation.sha256` 与当前源码重算值完全一致（`bf32cc7e…`，scope_version 13，63 个文件，含 `warehouse.py`、`evidence_index.py`、`source_query.py`、`source_gpkg.py`、`semantic_store.py`）。证据：`evidence/phase-memory-lamteh_main.json`。

