# Deep Interview Spec: BOITE 标签数据源修正 + SITE FDT 偏移修复

## Metadata
- Interview ID: dd-20260719-boite-source-site
- Rounds: 1 (+ Round 0)
- Final Ambiguity: 12%
- Type: brownfield / Threshold: 0.2 / Source: default / Status: PASSED

## Topology
| Component | Status | Description |
|-----------|--------|-------------|
| BOITE 标签源修正 | active | ①移除错误的 span→BOITE 距离错配 ②从块 ATTRIB 读 FAT=16/FDT=48/72 |
| SITE 偏移修复 | active | FDT 聚合用 INSERT 插入点替代质心，消除 3.5m 偏移 |

## Goal
(BOITE) 撤销上一轮 B 组件把 span_annotations 跨距距离写入 BOITE 的错误改动；从 DWG 块定义的 ATTRIB 实体（entmode=0，通过 dwgread JSON 的 ATTRIB→ownerhandle→INSERT 链路）读取 FAT=16、FDT=48/72 写入 BOITE 对应字段；(SITE) 将 2 个 FDT SITE 的位置从碎片聚合质心改为 FDT STRUCTURE INSERT 块的插入点（零或极小聚合容差），消除对 CABLE 拓扑的 3.5m 偏移污染。

## Acceptance Criteria
### BOITE
- [ ] converter.py 中 B 组件加入的 _join_boite_span_distance 及 distance_label 字段写入逻辑**整体移除**
- [ ] 新加 ATTRIB 读取：从 dwgread JSON dump 中提取 tag∈{FAT,FDT} 的 ATTRIB，按 ownerhandle 匹配到 FAT DWG INSERT 实体
- [ ] BOITE 新增 fat_value（FAT ATTRIB 数值）和 fdt_value（FDT ATTRIB 数值）字段
- [ ] Hutabohu 实测：43 个 BOITE 中 ≥40 个 fat_value=16；≥2 个 fdt_value 分别为 48 和 72
- [ ] 不回归：BOITE CODE（DMPH 标签 43/43）不受影响；CABLE 168 不受影响；SUM 6942
### SITE
- [ ] FDT 碎片聚合：FDT STRUCTURE 层 INSERT 块用 2m（不是 50m）小容差或直接取插入点
- [ ] 端到端后：SITE PM0001 距离最近的 PTECH ≤ 2m；PM0002 同样 ≤ 2m；两处 dx/dy 不再对称偏离
- [ ] CABLE 拓扑不再被 SITE 偏移节点强制偏转——两处 CABLE 走向回归真实杆位

## Technical Context
- BOITE 根因：块 ATTRIB 在 entmode=0，现行 converter 只处理 entmode=2 → 全量丢失；后续 B 组件误用 span_annotations 数据填补
- SITE 根因：FDT STRUCTURE 碎片聚合 50m 容差产出的质心偏离真实 FDT INSERT 插入点约 3.5m
- ATTRIB 关联路径：dwgread JSON → entity=ATTRIB, tag=FAT/FDT, ownerhandle→INSERT handle
