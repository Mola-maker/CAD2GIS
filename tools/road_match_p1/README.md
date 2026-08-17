# road_match_p1 — 路网匹配自动定位 P1 原型

验证命题：FTTH 缆线沿道路敷设 ⇒ 可以用**缓冲带重叠 F1 + 网格扫描**，把转换产物
（CABLE 线网）与 OSM 路网做图案匹配，自动恢复相似变换定位参数，将"人工点 4 个 GCP"
变成"机器提候选、人确认"（对接 `gcp_workflow.py` 的候选框架）。

**结论（hutabohu 基线，种子 20240818）：通过。**
平移残差 **7.28 m**（标准 <50 m），旋转残差 **0.008°**（标准 <5°），分数峰明显
（真模式细扫描 F1=0.114，最佳错误模式 0.053，约 2.2 倍分离度）。详见
`results/result.json`。

## 数据与真值推断（协议第 1 步）

- 真值来源（只读引用，未复制未修改）：
  `E:\branch_CAD2GIS\CAD2GIS\.worktrees\robustness\baselines\apd_hutabohu\delivery\apd_delivery.gpkg`
  - delivery CRS = **EPSG:9481**（SRGI2013 / UTM zone 51N）；CABLE 6 条线、145 顶点、
    总长 5465 m；范围 X∈[488611, 491236], Y∈[67954, 69709]。
  - `gcp_capture.gpkg` 的 212 条 gcp_controls 全部 `review_status='candidate'`、
    `target_easting/northing=None`、`enabled=0` —— **没有任何人工批准的目标坐标**；
    `run_manifest.json` 亦声明 `absolute_accuracy_validation: not independently verified`。
  - 因此本实验把 delivery 的**名义位置**当作实验真值：用 pyproj 将 CABLE 从
    EPSG:9481 变换到 **EPSG:3857** 米制框架（真值中心 122.9094°E, 0.6227°N，
    Gorontalo 省 Limboto 以西）。
- 前提检验（`sanity` 阶段，`results/sanity_identity.json`）：真值位置缆线缓冲与 OSM
  道路缓冲的交集 = 缆线缓冲面积的 **87.7%**（135,201 / 154,153 m²，随机放置期望约
  6.3%）——"缆线沿路"前提在该基线上成立，且基线名义位置本身即与 OSM 路网对齐
  （Dice F1 只有 0.111 是因为 ±3km 域内道路缓冲总面积 2.28 km² 稀释分母，并非不对齐）。

## 实验协议（第 2–3 步）

1. `simulate`：固定种子 `SEED=20240818` 生成已知相似变换 **T_true**
   （θ_true=41.31°，平移 δ=493 m，scale=1.0），约定 `truth = R(θ)·local + t`，
   把真值缆线变换到模拟"本地坐标系"。
2. `scan`：
   - 评分 = 变换后缆线 15 m 缓冲 ∩ OSM 道路 15 m 缓冲 的 Dice F1。
   - **粗扫描**：θ = 0–355° 步 5°（72 个）；平移不逐点循环——对每个 θ 用
     **FFT 互相关**（slide 式栅格化模板匹配）一次性评估锚点 bbox（真值中心 ±1200 m）
     内全部 5 m 像素平移，优于任务书"步长≈50 m"的朴素网格。栅格 ±3 km @5 m/px，
     模板 771 px，FFT 2560²。
   - **细扫描**：粗扫描 Top-3，θ±5° 步 1°，相关峰 3×3 抛物线亚像素拟合。
   - **抛光**：对最优假设用 shapely 精确矢量 F1 爬山（步长 5 m/0.5° 递减到 1 m/0.1°），
     消除栅格量化误差。
3. 自检（`selftest`）：合成图案已知偏移，验证互相关索引→平移映射零误差，防止映射
   写反导致假阳性。

## 结果（第 4 步，`results/result.json`）

| 项 | T_true | T_recovered | 残差 |
|---|---|---|---|
| θ (°) | 41.3079 | 41.3000 | **0.008°** |
| t (EPSG:3857) | (13682720.83, 69268.43) | (13682727.30, 69271.76) | **7.28 m** |

- 粗扫描 Top-5：40°/0.0906 → 45°/0.0693 → 35°/0.0564 → 125°/0.0528 → 210°/0.0524
  （真模式对错误模式约 1.7×；细扫描后 41°/0.1144 对错误模式 2.2×）。
- 耗时：粗扫 143 s + 细扫 66 s + 抛光 2.4 s ≈ **3.5 min**（i5 级单核，可复跑）。

产物：

- `results/result.json` — T_true/T_recovered、残差、Top-5、计时、溯源哈希
- `results/overlay.geojson` — EPSG:4326：OSM 道路、真值/恢复/本地系缆线、Top-5 假设点
- `results/overlay.svg` / `results/overlay.png` — 叠加可视化（环境无 matplotlib，用 PIL 渲染）
- `results/sanity_identity.json` — 前提检验证据
- `data/` — 真值缆线（3857）、元数据、Overpass 缓存（216 条 way，复跑不再请求）

## 复跑命令

```bash
cd E:\branch_CAD2GIS\CAD2GIS\.worktrees\road-match
CONDA="/c/Users/22494/miniconda3/Scripts/conda.exe run -n cad2gis python"
$CONDA tools/road_match_p1/road_match_p1.py extract   # 读 delivery → 真值(3857) + bbox
$CONDA tools/road_match_p1/fetch_osm.py               # 单次 Overpass 查询（有缓存则跳过）
$CONDA tools/road_match_p1/road_match_p1.py sanity    # 恒等假设前提检验
$CONDA tools/road_match_p1/road_match_p1.py scan      # 自检 + 模拟 + 网格扫描（≈3.5 min）
```

（Git Bash 路径写法；cmd/PowerShell 等价地把 `$CONDA` 换成完整命令前缀。）

## 关键设计决策

1. **FFT 互相关代替朴素平移循环**：θ 离散化后，平移评分 = 两幅二值掩膜的互相关，
   一次 FFT 得整张平移得分图（任务书 50 m 步长 × 72 θ ≈ 46 万假设的朴素循环需数小时；
   本实现 5 m 全分辨率 × 105 个 θ 仅 3.5 min），并附带完整热区图，天然支持 Top-N。
2. **栅格近似 + 精确抛光两段式**：栅格 F1 只负责找峰（对峰位置不敏感的量化误差
   可接受），最终参数用 shapely 精确缓冲爬山抛光到亚像素，残差达到 7 m/0.008°。
3. **确定性**：唯二随机源是 `SEED=20240818` 的 `numpy.default_rng`（T_true 生成与
   selftest）；Overpass 结果落盘缓存；两次完整运行输出逐字节一致（已验证）。
4. **诚实性保护**：`sanity` 阶段先量化"缆线是否真沿 OSM 路网"；若基线未配准，
   结果文件会如实显示（本基线前提成立）。selftest 失败会中止扫描。

## 已知限制 / 未解决项（交 P2）

- **scale 固定 1.0**：扫描未搜索比例维；T_true 的 scale 也取 1.0。若未来数据含
  CAD 单位误差，需要加比例维或在抛光阶段解算。
- **搜索域约束**：模板对角线 ~2.2 km ⇒ 栅格 ±3 km 内可及平移只有 ±780 m；
  若真实偏移超过此范围（本实验 δ=493 m，安全），需加大 GRID_RADIUS 或分块扫描。
- **道路密集区分母稀释**：Dice F1 的绝对值受域内道路总量影响，判峰应看相对分离度
  （本实验 2.2×）。可考虑改用"缆线缓冲覆盖率"（recall 型）或距离变换加权评分。
- **OSM 完备性依赖**：缆线经过无 OSM 道路覆盖的路段会降低峰值；P2 可引入 IMB
  建筑点作为第二匹配通道，或对重叠率设下限门禁后再进 `gcp_workflow` 人工确认。
- 本原型仅产出候选变换，**未写入** `gcp_capture.gpkg`；与 `semantic_anchor.py` /
  `gcp_workflow.py` 的候选接入属后续集成任务。

## 文件清单

- `road_match_p1.py` — 主程序（extract/selftest/sanity/scan，单文件含全部算法）
- `fetch_osm.py` — Overpass 单次礼貌查询 + 缓存
- `inspect_data.py` — 前置勘察（只读，解析 gpkg 图层/CRS/范围/GCP 状态）
- `env_check.py` — 环境探测（shapely/pyproj/GDAL 版本）
- `data/`, `results/` — 见上节
