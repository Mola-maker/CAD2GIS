# T 组件技术文档：拓扑修复三方对照分析（Hutabohu）

- 日期: 2026-07-17
- 作者: cad2gis-five-defects-fix / worker-1
- 依据: `.omc/specs/deep-dive-topology-color-label-fix-compare.md`（T 节）、
  `.omc/specs/deep-dive-trace-topology-color-label-fix-compare.md`（Lane 1）
- 实验工具: `experiment/py_scripts/t_experiment.py`（分析专用，不改产线代码）
- 结果数据: `/tmp/t_exp/t_experiment_results.json`（排除生效基线）、
  `/tmp/t_exp_pre/t_experiment_results.json`(排除前参照)

---

## 1. 问题陈述

当前交付 gpkg（缺陷 1/2 数据侧）表现为：

| 症状 | 产线实测 |
|---|---|
| gap-bridge 桥接 | 429 次，其中 117 次跨 DWG 缆种（Service Core × Expansion Core 等） |
| 连通分量 | 302 个，最大分量 203 成员，横跨 FDT-01+FDT-02+LINK |
| CABLE FDT_ID 空值 | 424/627 = 67.6% |

取证报告（Lane 1）定位根因为 `topology_builder.py` `chain_edges` 的桥接条件
（L875）只验距离（`d<=bridge_tol and d<nda and d<ndb`），无方向/缆种过滤。
用户已拍板目标形态为**拓扑保真优先（默认禁桥）**，并要求先做三方对照实验
论证后再实施。

## 2. 实验设计

### 2.1 基线

X 组件（#1）已生效：`experiment/config/legend_exclusions.json` 确认排除
LC-001（SPLICING 面板，1125 成员）与 LC-002（FDT LAYOUT 图例样本，67 成员）。
排除生效后 CABLE 源折线 990 → **31 条真实地图缆**（其余为示意图/图例内容）。

基线生成（转换全量跑，跳过拓扑阶段）：

```bash
cd experiment/py_scripts
python3 converter.py \
  --input "../APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" \
  --output /tmp/t_exp/base.gpkg --skip-topology
# base SHA256: 0319c05d5113f3491a3afe7876e5a154e80feb31f15c67496f4f24742834f915
# CABLE=31, PTECH=167, SITE=2, BOITE=43, conservation SUM=6942 ok
```

### 2.2 四臂对照

每臂从同一 base 拷贝起步，依次执行 [成链变体] → 三态吸附修复
（snap 5 m / isolation 30 m）→ FDT 域打标（layout facts:
FDT-01=DMPH-1.010, FDT-02=DMPH-2.011）：

| 臂 | 含义 | 成链配置 |
|---|---|---|
| A | 上版等效（--skip-chaining） | 无成链，碎段直接吸附 |
| B | **禁桥（目标形态）** | 节点截断 + 0.5 m 端点焊接，桥接关闭 |
| C | 约束桥 | B + 桥接（同 dwg_layer 且双端延续方向 cos≥cos30°，tol 5 m） |
| D | 自由桥（现产线行为） | B + 桥接（仅距离条件，tol 5 m） |

```bash
python3 t_experiment.py --base /tmp/t_exp/base.gpkg \
  --dwg "../APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg" \
  --workdir /tmp/t_exp --arms A,B,C,D
```

### 2.3 工具保真度验证

`t_experiment.py` 的插桩成链函数是产线 `chain_edges` 的等价拷贝
（仅加过滤与日志）。在排除前基线（/tmp/raw_frags.gpkg，990 碎段）上
自由桥模式与产线逐项 **IDENTICAL**：627 段 / 429 桥 / 172 节点截断 /
535 吸收——并复现 Lane 1 的 117 跨缆种数字。

```bash
python3 t_experiment.py --validate --base /tmp/raw_frags.gpkg --workdir /tmp/t_exp_val
```

### 2.4 指标口径

- **跨缆种错接**：桥接两端 dwg_layer 不同的桥数 + 融合链中含 >1 缆种的链数
- **FDT 覆盖率**：CABLE FDT_ID 空值率（按条数与按长度双口径）
- **分量形态**：连通分量数 / 最大分量成员与域构成。端点聚类双容差：
  0.5 m（焊接级=真实交付连通性）与 5 m（FDT 泛洪邻接口径）
- **LINK 保留**：跨域杆链段（FDT_ID=LINK）是否存活

## 3. 三方数据表

### 3.1 排除生效基线（本实验主表）

| 臂 | CABLE 段 | 桥接 | 跨缆种错接 | 混种链 | 分量@0.5m | 最大分量 | 最大分量域 | FDT 空值率(条/长度) | LINK |
|---|---|---|---|---|---|---|---|---|---|
| A 上版等效 | 31 | 0 | 0 | 0 | 21 | 7 | —(全空) | **100% / 100%** | **0（丢失）** |
| B 禁桥 | 203 | 0 | 0 | 0 | **1** | 203 | FDT-01+02+LINK | **0% / 0%** | 1（保留） |
| C 约束桥 | 203 | **0** | 0 | 0 | 1 | 203 | FDT-01+02+LINK | 0% / 0% | 1（保留） |
| D 自由桥(现产线) | 203 | **0** | 0 | 0 | 1 | 203 | FDT-01+02+LINK | 0% / 0% | 1（保留） |

**B ≡ C ≡ D**：成链指标逐项一致（31 碎段 → 172 节点截断 → 203 段；
junctions 204 = 158 node_cut + 46 degree_cut + 0 pass_through；焊接合并 0；
**桥接 0 次——自由桥模式在干净基线上一次也未触发**）。
吸附：390 snapped + 16 attr_only + 0 floating。
FDT 打标（B 臂，43 种子）：CABLE FDT-01=151 / FDT-02=51 / LINK=1 / 空=0；
BOITE 43/43、PTECH 167/167、SITE 2/2 全覆盖。全网总长 7412.4 m。
LINK 段 = CBL0005-S7（Cable Line A，28.992 m，青色跨域杆链）。

### 3.2 排除前参照（污染基线，佐证根因）

| 臂 | CABLE 段 | 桥接 | 跨缆种桥 | 混种链 | FDT 空值率(条) | 备注 |
|---|---|---|---|---|---|---|
| A | 990 | 0 | 0 | 0 | 100% | |
| B | 1019 | 0 | 0 | 0 | 80.1% | |
| C | 986 | 34 | **0** | 0 | 79.4% | 拒绝 501 次跨种 + 1123 次角度 |
| D | 627 | 429 | **117** | **49** | 67.6% | 精确复现产线记录 |

**全量桥接日志判定（关键证据）**：D 臂 429 桥中，**0 桥触及任何真实地图
缆层**（Cable Line A/B/C、SLING WIRE、MAIN/SUBFEEDER）；117 跨缆种桥与
49 条混种链全部发生在示意图面板层之间（Service Core / Expansion Core(s) /
moniter core / Line）。

## 4. 根因论证

1. **429 桥 / 117 错接 / 混种链全部是面板内产物**（3.2 全量日志），
   X 组件排除生效后该病灶整体离场；真实地图缆上桥接条件从未满足——
   真实缆端点终止于杆/FAT 附近，桥接守卫 `d<nda and d<ndb`（端点比最近
   节点更近才桥）在节点密集的真实路网中天然抑制桥接。
2. **对取证报告 H1 的修正**：产线"203 巨型分量"与干净基线的真实路网
   **完全同一**（203 段、7412.4 m、SLING WIRE+Cable Line A+C、
   FDT-01+02+LINK，逐项吻合）。它不是桥接熔合的怪物，而是本图**真实
   连通的单一网络**——FDT-01 与 FDT-02 域经由合法的青色跨域杆链
   （LINK 段 CBL0005-S7）物理相连。真正的病灶是共存于 CABLE 层的
   424 条面板碎段（永不可打标 → 推高空值率至 67.6%）与面板内部错接。
3. **成链的节点截断是 FDT 打标的前提**（A 臂）：31 条原始折线中途穿过
   杆/FAT 而端点不落节点，跳过成链则 BOITE 种子成为孤立顶点，Dijkstra
   无边可走 → FDT 覆盖率 0%、LINK 语义丢失。因此**只可禁桥，不可禁链**。
4. **焊接与截断无错接风险**（B 臂）：0.5 m 焊接在本图 0 次合并、
   截断只切不接，禁桥臂混种链 = 0，结构上不可能跨缆种错接。

## 5. 决策建议

**采纳"拓扑保真优先（默认禁桥）"，实验数据完全支持用户预定方向：**

- 在干净基线上禁桥**零代价**——桥接本就 0 触发，B ≡ D，三指标全满分；
- 禁桥提供**纵深防御**：若未来图纸的排除配置遗漏面板，自由桥会重演
  117 错接（3.2 D 臂），禁桥则结构性免疫（3.2 B 臂错接 0）；
- 约束桥（同缆种 + cos30°）作为 `--enable-gap-bridge` 开关保留：
  污染基线上它把 429 桥压到 34 桥、跨缆种 0（拒绝 501 跨种 + 1123 角度），
  适用于确有数字化断缝的图纸，但本图无此需求，默认关。

**对 spec 验收标准的一处必要修订**（如实反馈）：
"最大分量成员不再横跨 FDT-01+FDT-02+LINK"按字面**不可达也不应达**——
真实路网就是经 LINK 合法连通的单一分量。应改判为：
（a）跨缆种错接 = 0；（b）域跨越仅经由合法 LINK 段发生；
（c）面板碎段退出 CABLE 层（X 组件已达成）。

## 6. 实施设计（给 #3 的改动清单）

### 6.1 topology_builder.py

- `chain_edges` / `chain_edges_gpkg` 增加参数：
  `gap_bridge=False`（默认禁桥）、`bridge_tol=None`（启用时默认取
  node_capture_tol）、`bridge_min_cos=cos(30°)`（参数化）、
  桥接候选过滤：同 `dwg_layer` + 双端延续方向内积 ≥ bridge_min_cos
  （方向 = 端点处末段单位向量，实现同 t_experiment.py `end_direction`）。
- 旧的无约束自由桥路径删除（不保留）。
- CLI 增加 `--enable-gap-bridge`（可选 `--bridge-min-cos-deg`）。

### 6.2 converter.py（仅拓扑参数段）

- 增加 `--enable-gap-bridge` 透传；默认调用改为
  `chain_edges_gpkg(..., gap_bridge=False)`（不再传 `gap_tol=snap_tol`）。
- 打印行与 qc_summary 注记体现 bridge 开关状态；
  `cable_chain_gap_bridges` 行照写（默认应为 0）。

### 6.3 端到端验证步骤

默认参数全量跑（exclusions 生效 + 禁桥），对照 §7 基准；再以
`--enable-gap-bridge` 跑一次确认开关联通且本图仍 0 桥。

## 7. 验收基准（实验标定）

默认（禁桥）全量跑后必须满足：

| 指标 | 基准值 |
|---|---|
| CABLE 逻辑段 | 203（31 源折线，172 节点截断） |
| gap_bridges | 0 |
| 跨缆种错接（桥/混种链） | 0 / 0 |
| CABLE FDT_ID | FDT-01=151，FDT-02=51，LINK=1，空值=0（空值率 0%，对照旧版 67.6%） |
| 节点层 FDT 覆盖 | BOITE 43/43，PTECH 167/167，SITE 2/2 |
| 连通分量@0.5m | 1（真实路网单一网络，总长 7412.4 m） |
| LINK 段保留 | CBL0005-S7（Cable Line A，28.992 m）存活，FDT_ID=LINK |
| 端点吸附 | 390 snapped / 16 attr_only / 0 floating |
| conservation SUM | 6942 守恒 |
| `--enable-gap-bridge`（本图） | 仍 0 桥（约束桥无候选） |

注：SITE 排除后为 2（此前 8；LC 面板内 6 个 SITE 样本被确认排除），
与 spec X 节"SITE≈6"的预估不符——以实测为准，已上报待 #6 集成时复核。

## 8. 实施验证记录（#3，2026-07-17）

§6 设计已实施：`topology_builder.py` `chain_edges/chain_edges_gpkg` 改为
`gap_bridge=False` 默认禁桥 + 约束桥参数（`bridge_tol`/`bridge_min_cos`，
无约束自由桥路径已删除）；CLI 与 `converter.py` 增加
`--enable-gap-bridge`（另 topology_builder CLI 有 `--bridge-min-cos-deg`）。

保真度：产线约束桥与本实验 C 臂在污染基线上逐项 IDENTICAL
（986 段 / 34 桥 / 拒绝 501 跨种 + 1123 角度）。

端到端全量跑（默认禁桥，`--skip-styles` 规避 S-2 在制段错误）逐项对照
§7 基准：**全部命中**——CABLE 203（31 源折线/172 截断）、gap_bridges=0、
FDT 151/51/1/空 0、BOITE 43/43、PTECH 167/167、SITE 2/2、
edge components=1、LINK CBL0005-S7（Cable Line A，28.992 m）、
吸附 390/16/0、SUM=6942 守恒、真标签 fat 43/43 + pole 118/118。
`--enable-gap-bridge` 复跑：本图仍 0 桥（qc note
`gap_bridge=constrained`），开关联通验证通过。
gpkg SHA256（禁桥默认跑）: d0449f05e8c6515648c750b690689861f745ed902ffbe872d4a6b4df1fd41835
