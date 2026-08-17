# road_match_p2 — 路网匹配自动定位 P2：多基线回归

把 P1（`../road_match_p1`，只读复用算法）的恢复协议推广到 **kletek / lamteh / lamteh_sf**
三个基线。数据源：`E:\branch_CAD2GIS\APD_test\runs\{kletek,lamteh,lamteh_sf}\delivery.gpkg`
（只读，sha256 已记入各 `baseline_<name>.json`）。

## 总结论（诚实版）

| 基线 | 路由 | 最强假设 exact coverage | Top-1/Top-2 分离度 | 判定 |
|---|---|---|---|---|
| hutabohu（P1 引用） | A 剥离-恢复 | Dice F1=0.114 | ≈2.2×（对最佳错误模式） | **PASS**：残差 7.28 m / 0.008° |
| kletek | B 粗锚扫描 | 0.990（θ=7.2°） | 1.01× | **INCONCLUSIVE**：指标饱和，道路通道无鉴别力 |
| lamteh | B 粗锚扫描 | 0.358（θ=62°） | 1.08×（旋转迥异模式 0.999×） | **INCONCLUSIVE**：多模式简并 |
| lamteh_sf | B 粗锚扫描 | 0.608（θ=324°） | 1.04×（162° 近对偶） | **INCONCLUSIVE**：弱峰 + 搜索域截断 |

三个 P2 基线全部**未通过**自动定位——这不是实现失败，而是数据与方法的边界证据，
详见各 `results/baseline_<name>.json` 与 `results/multibaseline_summary.json`。

## 协议自适应（第 1 步：路由判定）

`screen` 阶段（`results/identity_screen.json`）：

- 三基线 CABLE 坐标量级 ≤ 10.6 km。按其声明 CRS（图层与 manifest 均称 EPSG:3857）
  直读，名义 footprint 落在 **Null Island 附近几内亚湾大洋**（0.02–0.09°E, 0.07–0.09°S）。
- 实证：对该海域单次 Overpass `way[highway]` 查询（bbox ±0.11°）返回 **0 条 way**
  ⇒ 恒等假设 F1=0，三基线全部为**本地工程坐标**，无独立真值，只能走 Route B
  （粗锚 + 旋转×平移扫描），残余不可量化。
- ⚠️ 上游 manifest 自检 `coordinate_domain_status=PLAUSIBLE_DECLARED_CRS_DOMAIN
  (passed=true)` 与本证据矛盾——坐标域检查未识别该错误，建议主流程修正。

参考锚点（`data/osm_anchor_<name>.json`，来源 `origin/robustness @ 78f5d1a`，
`baselines/{kletek,lamteh_main,lamteh_sf}/config/osm_anchor.json`）：Nominatim
`coarse_bbox_centre` 精度，给出纯平移先验 `t_anchor`；搜索中心 = 缆线 bbox 中心 + t_anchor。

## Route B 协议（第 2–4 步）

模型：`world = R(θ)·(local − c_l) + c_w`（c_l=缆线本地中心；c_w=自由平移；scale=1）。

- **scale=1 假设**：kletek insunits=6(m) 与量级一致；lamteh/lamteh_sf insunits=4(mm)，
  但若按 mm 解释缆线总长仅数米（村级 FTTH 不可能），按米解释跨度数 km、与 Nominatim
  村级 bbox（约 4.5 km）同量级 ⇒ 数值按米处理。该假设只由扫描结构间接支持，未经真值证实。
- **双指标评分**（对 P1 的协议修正）：Dice F1 在道路密集区被分母稀释（P1 README 已预警）。
  P2 主指标改为 **coverage = |缆线缓冲 ∩ 道路缓冲| / |缆线缓冲|**（recall 型，与 P1 sanity
  的 87.7% 同款），Dice F1 作辅证。两者由同一次 FFT 互相关同时产出。
- 粗扫 θ 0–355° 步 5°（FFT 互相关全分辨率平移评估）→ Top-3 细扫 θ±5° 步 1° + 3×3
  抛物线亚像素 → Top-1 精确 coverage 爬山抛光（shapely，步长 5 m/0.5° 递减至 1 m/0.1°）。
- 模式去重半径 Δθ=5° / 350 m（缆线走廊数 km，同 θ 沿走廊数百米平移近似等价）。
  另报**旋转迥异分离度**：Top-1 vs Δθ≥10° 最佳模式的 exact coverage 比。
- selftest（与 P1 相同合成验证）在 coarse 第 0 块自动执行，两次均 PASS。

每基线扫描参数（`data/meta_<name>.json`）：

| 基线 | res | grid_radius | search_radius | FFT | 备注 |
|---|---|---|---|---|---|
| kletek | 5 m/px | 2.2 km | 1.5 km | 1024 | 缆线仅 876 m / 3 条 |
| lamteh | 8 m/px | 7.0 km | 2.5→**2.9 km** | 3072 | 粗扫峰距先验 2.4 km 近边界，外扩掩膜 |
| lamteh_sf | 8 m/px | 7.0 km | 2.5→**2.9 km** | 3072 | 同上（网格/区域与 lamteh 共用） |

OSM 缓存（每区域单次 `way[highway]` 查询，落盘于 `data/`，复跑不请求）：
`overpass_way_highway_kletek_*.json`（2896 way）、`overpass_way_highway_lamteh_*.json`
（2714 way，lamteh/lamteh_sf 共用）、`overpass_way_highway_nullisland_*.json`（0 way，筛查用）。

## 各基线发现

### kletek — INCONCLUSIVE（指标饱和）
村级路网相对 270×170 m 缆线簇在 ±15 m 容差下趋饱和：全角度 coverage≈1.0
（中位数 1.0），Dice 上限 0.0067 被顶满。敏感性实验（`results/sens_kletek_buf{8,4}.json`）：
buf 8 m 时 max/median=1.15；buf 4 m 时 1.45 且 θ≈0–5° 略占优（与粗锚纯平移先验相容），
但 4 m 已低于 OSM 道路定位噪声，不足为凭。⇒ 小缆线簇 + 密集路网下道路通道失去鉴别力，
需第二通道（如 IMB 建筑点 167 个）或人工 GCP。

### lamteh — INCONCLUSIVE（旋转简并）
缆线 6.6 km / 12 条，7.7 km 狭长走廊。最强 θ=62°（cov 0.358），但旋转迥异竞争模式
θ=90° cov 0.358（分离度 0.999×）——多个不同旋转假设沿各自路段获得相同覆盖率。
粗锚先验（θ=0）exact coverage=**0.0**：config 的纯平移锚定对缆线完全不成立。
峰对背景（粗扫中位数）2.23×，说明"沿路"结构存在但不可定位。

### lamteh_sf — INCONCLUSIVE（弱峰 + 截断）
最强 θ=324° cov 0.608（对背景 1.6×），但 162° 近对偶 θ=126° cov 0.587（1.04×），
且 Top-1 距先验中心 **2838 m = 搜索半径 98%**（`near_boundary=true`）——峰被掩膜截断，
真实最优可能在域外。先验 cov 仅 0.024。

### 交叉一致性（lamteh vs lamteh_sf）
同一村庄（Lamteh Dayah）两个变体的恢复位置相距 **6391 m**、旋转角 62° vs 324°——
真实网络位置应接近 ⇒ 至少其一是伪匹配，两个结论均不可单独采信。

## 复跑命令

```bash
cd E:\branch_CAD2GIS\CAD2GIS\.worktrees\road-match
C="/c/Users/22494/miniconda3/Scripts/conda.exe run -n cad2gis python"
P2=tools/road_match_p2/road_match_p2.py
$C $P2 extract kletek                       # 另两基线：extract lamteh/lamteh_sf --search-radius 2900
$C $P2 planfetch                            # 生成 data/fetch_plan.json
$C tools/road_match_p2/fetch_osm_p2.py      # 有缓存则全部跳过
$C $P2 screen                               # 恒等假设筛查
$C $P2 prep kletek                          # 道路裁剪 + 缓冲 WKB 缓存（每基线一次）
for k in 0 1 2 3 4 5; do $C $P2 coarse lamteh $k 6; done   # θ 分块（300s 预算）
for i in 0 1 2; do $C $P2 fine lamteh $i; done
$C $P2 finalize lamteh                      # 抛光 + overlay + baseline_lamteh.json
$C $P2 sens kletek 8                        # 缓冲敏感性（可选）
$C $P2 summary                              # multibaseline_summary.json
```

确定性：扫描无随机源（selftest 用固定种子 20240818）；除 `elapsed_s` 计时字段外，
重复运行逐字节一致（已验证 kletek 粗扫）。

## 已知限制 / 未解决项（交后续）

1. **lamteh_sf 搜索域截断**：定论需以更大 bbox 重新拉取 OSM（本阶段遵守"每区域单次
   查询"未重拉）并将网格半径扩到 ~9 km（FFT 4096）。
2. **scale 维未搜索**：若未来证据表明坐标非米（如真 mm），需加比例维。
3. **kletek 信号充分性门禁**：自动定位前应先评估"缆线长度 × 路网密度 × 容差"的
   可鉴别性（本基线 876 m 缆线在 40% 道路覆盖率的村内不可鉴别）。
4. **第二通道**：lamteh 有 IMB 建筑点 846 个、kletek 167 个，可做建筑点匹配通道
   交叉验证（本阶段未实现）。
5. Route B 全部结果仅候选排序依据，**未写入** `gcp_capture.gpkg`；接入
   `gcp_workflow.py` 人工确认属后续集成。

## 文件清单

- `inspect_p2.py` — 前置勘察（只读；图层/CRS/范围/manifest 单位合同）→ `data/inspect_*.json`
- `road_match_p2.py` — 主程序（extract/planfetch/screen/prep/coarse/fine/sens/finalize/summary）
- `fetch_osm_p2.py` — Overpass 单次查询 + 缓存
- `data/` — 缆线本地坐标、meta（扫描参数+先验）、OSM 缓存×3、锚点配置存证、git ref、
  道路裁剪与缓冲 WKB
- `results/` — `identity_screen.json`、`baseline_{kletek,lamteh,lamteh_sf}.json`、
  `multibaseline_summary.json`、`coarse_/fine_*.json`（分块原始记录）、
  `sens_kletek_buf{4,8}.json`、`overlay_<name>{,_zoom}.{geojson,svg,png}`
