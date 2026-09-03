# CAD2GIS 大图内存占用优化约束方案（MEMORY_OPTIMIZATION_SPEC）

- 日期: 2026-08-31
- 背景: `cad2gis-ftth:0.1.0` 陌生机实验——lamteh 级大图（~6 万实体）在受限内存容器（如 `--memory 4g/6g`）内 OOM；本地全量转换不崩但峰值内存极高
- 范围: reader → 实体 → 证据图 → 写盘 全链路内存峰值；不改变产物 schema、证据语义、确定性
- 状态: 约束方案（本机验证在实现后执行，见 §6）

## 1. 峰值来源分解（lamteh_main: 63,483 实体 / 67,105 source_entity 图节点 / 374MB evidence_graph.json）

| # | 阶段 | 持有结构 | 估算峰值 | 代码位置 |
|---|---|---|---|---|
| 1 | LibreDWG 读取 | `records: list[dict]`（每实体全字段 ~4KB） | 0.3-0.6 GB | `reader/libredwg.py:1773` append 全量 |
| 2 | 实体化 | `SourceEntity.from_record` 全量复制（records 仍存活） | +0.3-0.6 GB | `cad2gis_v3/ingest.py:57` |
| 3 | 图构建 | `nodes: list` + `node_by_logical: dict` 双结构，facts 第三次复制 | +1-2 GB | `evidence_graph.py:336-360` |
| 4 | 序列化 | `to_dict()` 全量副本 → `json.dumps` 整串 | +1.5-2.5 GB 峰值 | `pipeline.py:1975` → `_write_manifest` |
| Σ | — | — | **峰值 3-5 GB+** | — |

> 374MB 的 JSON 文件只是冰山一角：Python 对象开销（dict/list 膨胀 3-6 倍）+ 同一 facts 在 records/SourceEntity/node/to_dict 中 4 次复制，是 OOM 的根因。

## 2. 约束原则（不可违反）

1. **产物不变**：`source/evidence/delivery.gpkg`、`evidence_graph.json`（schema `cad2gis.evidence_graph.v1` 含 `facts` 全字段与 `graph_sha256`）、`run_manifest.json`、QML 的**字节级确定性**保持——任何优化不得改变既有 run 的 sha256 复现性
2. **语义不变**：reader 契约（`skipped_rows==0`、`inventory_complete`）、census、semantic classification 输出逐项一致
3. **架构不变**：不引入数据库/外部进程作为新依赖；优化只发生在 Python 进程内的数据结构与写盘路径
4. **渐进可验证**：每步优化独立可开关（环境变量），可对照基线做 A/B 验证
5. **不动 LibreDWG C 层**：SWIG `Dwg_Data` 全量加载是库设计，不在优化范围

## 3. 优化分层方案（按收益/风险排序）

### 3.1 [P0] 图节点 facts 去重复持有（消除第 3 次复制）

- **现状**：`add_node` 时 `facts={...}` 从实体重新拼字段，`EvidenceNode.create` 内部 `_canonical_json(facts)` 与 `facts` dict 并存；`to_dict()` 再展开
- **方案**：
  a. `EvidenceNode` 改为**单次序列化缓存**：构造时算一次 `facts_sha256`，`to_dict` 直接复用缓存的 canonical JSON 段（按字段拼接），不再整图二次 `json.dumps`
  b. `build_stage_evidence_graph` 的 `node_by_logical` 在**构建完成且校验通过后释放**（构建期查重需要它，图产出后删引用）；`sorted(entities)` 的临时排序表同理（改为按需 sort 后逐条消费并释放）
- **预期**：峰值 -1 份全量（约 -25%）

### 3.2 [P0] 写盘流式化（消除 `to_dict()` + 整串 dumps 峰值）

- **现状**：`pipeline.py:1975` `_write_manifest(graph_path, evidence_graph.to_dict())` → `_write_manifest` 内 `json.dumps` 一次构建整串
- **方案**：
  a. 为 EvidenceGraph 增加**流式序列化器**：`json.JSONEncoder` 子类或手写分块（`json.dumps(node, ...)` 逐节点 + `ensure_ascii` 一致性 + 相同 `separators`），外层 `[`/`,`/`]` 手工拼装，`nodes`/`edges` 逐条 flush
  b. 节点顺序固定（现有 `sorted` 顺序），保证字节级输出与现状一致（以基线文件 sha256 为准核验）
  c. `graph_sha256` 计算改为**流式哈希**（边写边算），不依赖整串
- **预期**：序列化阶段峰值从"图 + 副本 + 整串"降为"图 + 常驻缓冲"（约 -1.5-2.5 GB）

### 3.3 [P1] records/SourceEntity 生命周期裁剪（消除第 2 次复制滞留）

- **现状**：`ingest()` 产出 records 全量 list；`SourceEntity.from_record` 后 records 仍被 pipeline 引用至语义阶段之后
- **方案**：
  a. 审计 pipeline 中 records 的最后使用点；在语义编译入口之后 `del records`/置空并 `gc.collect()`（明确注释语义边界）
  b. `DWGRecordInventory` 已是协议化容器（`__iter__/__len__`），若 3.1/3.2 落地后仍有需求，再评估 reader 侧惰性产出（**默认不做**：LibreDWG 遍历本身已全量，Python 侧惰性只省 list 壳，收益有限、风险高）
- **预期**：语义阶段后峰值 -0.3-0.6 GB

### 3.4 [P1] facts 规范化去冗余（结构性，需 schema 评估）

- **现状**：`source_entity` 节点 facts 携带完整实体字段（含 `points` 坐标数组、`curve_facts`、样式），与 source.gpkg 内容重叠
- **方案（谨慎，需产品确认）**：
  a. 坐标类大字段在 facts 中以 `curve_fingerprint`/`curve_sha256` 引用 + 几何汇总（长度/点数/bbox）替代全量点列——**会改变 schema v1 语义，仅作为 v2 提案**，本方案默认不实施，单列待决
  b. 若实施：`schema_version` 升 `cad2gis.evidence_graph.v2`，老版本 reader 兼容保留
- **预期**：单节点 4KB → 1-1.5KB，文件 374MB → ~100-140MB，内存同比例降

### 3.5 [P2] 容器/运行时护栏（不优化内存，但防 OOM 崩溃）

- 镜像 `run_all.sh` 增加可选 `CAD2GIS_EXPECTED_MAX_ENTITIES`（默认 200k）与峰值告警：转换前打印实体数 + 预估峰值（实体数 × ~60KB/实体经验系数），超过阈值时提示 `--memory` 建议
- Dockerfile 保留现状（不做镜像内 swap/overcommit 配置，交给 run 时 `--memory`/`--memory-swap` 由使用方控制）

## 4. 不做的事（明确排除）

1. **不改 evidence_graph schema v1**（3.4 为 v2 待决提案，不在本次落地）
2. **不引入 SQLite/duckdb/外存图**替代内存图（架构原则 3）
3. **不重构 LibreDWG SWIG 读取**（约束 5）
4. **不做跨 run 图复用/增量图**（run 自包含语义不变）
5. **不动 `_write_manifest` 的通用 JSON 语义**——只对 evidence_graph 大路径做专用流式写，run_manifest/其他小 json 维持原样

## 5. 实施顺序与开关

| 步 | 内容 | 默认 |
|---|---|---|
| S1 | 3.1a 单次序列化缓存 | 开 |
| S2 | 3.1b 构建期索引释放 | 开 |
| S3 | 3.2 流式写盘（字节级对照基线） | 开，`CAD2GIS_GRAPH_STREAMING=0` 可关 |
| S4 | 3.3 records 生命周期裁剪 | 开 |
| S5 | 3.5 护栏告警 | 开 |

每个 S 步骤独立提交，跑 §6 验证，sha256 对照不通过则回退该步。

## 6. 本机独立验证方案（实现后执行）

### 6.1 峰值测量基线（先测现状，后测优化，同机同容器）

```bash
# A. 进程级峰值（本机原生环境）
/usr/bin/time -v ./.conda/envs/cad2gis/bin/cad2gis convert \
  "raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg" \
  --project baselines/lamteh_main --run-dir /tmp/mem_run --llm off \
  2>&1 | grep -E "Maximum resident|Elapsed"

# B. 分阶段探针（reader 后 / 图构建后 / 写盘时各打一次 RSS）
python - <<'EOF'
import resource, cad2gis.reader.libredwg as r
recs = r.extract_dwg_records("raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg")
print("after reader RSS MB:", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
EOF

# C. 容器内存约束实验（镜像重建后）
docker run --rm --memory 4g -v /tmp/cad2gis_out:/out cad2gis-ftth:0.1.0   # 现行为：OOM
docker run --rm --memory 4g --memory-swap 4g -v /tmp/cad2gis_out:/out cad2gis-ftth:0.1.0  # 优化后预期：通过
# 分级：4g / 6g / 8g 三档，记录通过/失败与耗时
```

### 6.2 验收标准

| 项 | 基线（现状） | 目标 |
|---|---|---|
| lamteh_main 转换进程峰值 RSS | 实测（预期 3-5 GB） | ≤ 2.5 GB |
| `--memory 4g` 容器内转换 | OOM | 通过（10 站全量） |
| evidence_graph.json 字节 | 基线 sha256 | **逐字节一致**（S1-S4 不得改变产物） |
| delivery.gpkg / manifest | 基线 | 一致 |
| T1 回归（282） + T2 契约（7） | 通过 | 通过 |
| 小图（kletek）峰值 | 基线 | 不劣化（≤ +10%） |

### 6.3 验证资产

- 基线 run: `baselines/lamteh_main/run/`（现有 374MB evidence_graph.json 即基线，sha256 已记录于 manifest）
- 测量脚本与结果落盘 `docs/memory_validation/`（`peak_*.log`、`memory_matrix.md`）

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 流式写盘字节不一致（separators/ensure_ascii/顺序） | S3 强制 sha256 对照；失败即回退；测试新增"图序列化字节级 golden"用例 |
| 生命周期裁剪误删仍被引用对象 | S4 前审计所有 `records`/`entities` 引用点；裁剪点加断言 |
| 优化只对大图有效，小图退化 | 6.2 小图不劣化门禁 |
| v2 schema 决策悬置导致 3.4 不做 | 接受（v1 语义冻结优先），3.1-3.3 已覆盖主要峰值 |

## 8. 参考

- 大图产物实测：`baselines/lamteh_main/run/reasoning/evidence_graph.json`（374MB，67k 节点）、lamteh_sf（328MB）
- 相关讨论：evidence_graph 消费方全为程序（pipeline/verify/MCP 仅 sha256/review 不读），优化不触碰任何 agent 审查路径
