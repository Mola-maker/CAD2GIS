# road_match_p2 — 路网匹配自动定位 · P2 阶段

P2 在 P1（`../road_match_p1`，只读复用其算法与缓存）基础上完成三件事：
**拓扑种子匹配**、**多基线回归**、**弃权门禁**。各子模块文档：

| 子模块 | 文档 | 结论 |
|---|---|---|
| 拓扑种子（Li & Briggs 路口模式匹配） | [README_topo_seed.md](README_topo_seed.md) | hutabohu 恢复残差 7.24 m / 0.088°，F1 评估量仅为 P1 暴力扫描的 0.0096%（~1 万倍加速），耗时 13.4 s |
| 多基线回归（kletek / lamteh / lamteh_sf） | [README_multibaseline.md](README_multibaseline.md) | hutabohu **PASS**；其余三基线 **INCONCLUSIVE**（诚实弃权，证据与原因见内） |
| 弃权门禁（ACCEPT / AMBIGUOUS / NO_MATCH） | 见 `gates.py` 模块 docstring 与 `tests_ambiguity.py` | 四用例全过：合成网格城市正确判 AMBIGUOUS（false-pass=0），hutabohu 正常案例不误弃权 |

## 跨模块要点

- **指标修正**：密集路网测区 Dice F1 被分母稀释（kletek 理论上限 0.0067），P2 起主指标改为
  coverage（cable-recall），Dice 仅作参考。
- **弃权是一等产出**：lamteh 与 lamteh_sf（同村两变体）恢复位置相距 6391 m、旋转迥异，
  交叉一致性检查判定至少其一为伪匹配——系统拒绝硬选，交人工。
- **遗留到 P3**：lamteh_sf 峰被搜索域截断（需更大 bbox 重拉 OSM）；scale 维未搜索；
  信号充分性前置门禁（kletek 证明需先评估可鉴别性）；第二通道（IMB 建筑点）；
  候选接入 `gcp_workflow.py` 人工确认闭环。

## 运行环境

与 P1 相同：`conda run -n cad2gis python <script>`（shapely / pyproj / GDAL / numpy / Pillow）。
Overpass 每区域单次查询落盘缓存于 `data/`，复跑不重复请求。全部实验固定随机种子、可复现。
