# 实体静默丢失的记账与恢复 — 设计文档

日期:2026-08-03
状态:已获用户批准(设计层面)
诊断对象:`E:\branch_CAD2GIS\APD_test\APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg`(以下简称 -SF 图)

## 1. 背景与诊断结论

用户报告:转换 APD_test 图纸后,主体缆线要素的点"被噪声算法剔除"。经六路并行架构
review + 两轮真实流水线诊断(tmp/ 下脚本与产物),结论如下:

**不存在顶点级丢失。** 三级对账(reader → evidence `cad_entities` → source.gpkg →
delivery.gpkg)逐 handle 平账:mapped 缆线顶点数全程不变(-SF 26→26→26,KLETEK
17/6/7→17/6/7),`CABLE_SEGMENT` 弦长与源段长逐一相等。流水线中没有 Douglas-Peucker
简化、尖刺剔除或顶点级离群过滤;幸存的 CABLE 受 fail-closed 逐点不变量保护
(`cad2gis_v3/pipeline.py:217`、`cad2gis_v3/curve_geometry.py:274-283`)。

**实际问题是整条实体在进入语义域之前被静默丢弃。** -SF 图 97 条已声明 route 层实体
中仅 1 条幸存到交付,丢失归因:

| 丢失桶 | 数量 | 根因位置 |
|---|---|---|
| 孤儿匿名块成员,从未被展开 | 38(+16 条块内 TEXT) | `cad2gis_v3/plan_domain.py:479-488` 只从 root INSERT 出发展开;`sfsfsfs` 等 4 个匿名块(含 ~2989 条缆线层成员)全图无 INSERT 引用,**零记账零警告** |
| reader 启发式改判 style_legend | 28 | `reader/autocad.py:3077-3111` `partition_plan_roles`、`:3114-3134` `partition_model_legend`,按图面位置魔法阈值改写 `cad_role`,无 per-entity provenance |
| Model 内被改判 title_block | 7 | 同上 |
| 图纸空间 layout 永不入围 | 7 | `reader/autocad.py:757` `_PLAN_LAYOUT` 正则不匹配 "APD - SF";`plan_domain.py:270-315` `_root_entities` 只认 model/plan |
| 幸存 | 1 | 原 role=style_legend,被 layout-role-fallback 救回 |

辅助事实:-SF 图 3049 个 INSERT 中 2857 个嵌套在孤儿块内部(FAT Info ×422、*U55
×1368 等);KLETEK 图同构(16 条 route 实体 → 交付 3 条,丢失为 FRAME 块内 9 条
legend 改判 + Layout2 图纸空间 4 条 out_of_scope)。两图均可平账,无"凭空消失"。

## 2. 目标与范围

让 -SF 图(及同构图纸)的缆线实体**要么进入交付,要么以显式、可审计的方式被排除**,
消灭"零警告整条消失"。

**范围内(四根柱子):**

1. orphan 块定义检测 + 记账 + reviewed 恢复机制(主修复);
2. reader 启发式角色改判的 provenance 记录 + route 层豁免;
3. paper layout 可声明为 plan 域根;
4. 配套 census gate / WATCH,使残留排除可见。

**范围外(明确不做):**

- coordinate-domain gate 对新图的硬阻断(诊断时改 profile 绕过)——独立问题,另立事项;
- libredwg 后端的 bulge 恒零(`reader/libredwg.py:1058-1066`)与类型白名单
  (`:1167-1169`)——代码层面成立但不打在缆线上,且本机无 libredwg 运行时;
- 弧离散化阈值(`curve_geometry.py:27-28`)与 reader 外图层预过滤死代码清理。

## 3. 设计

### 3.1 Orphan 块:检测、记账、恢复

**检测。** `plan_domain.py` 完成块定义表构建与 root INSERT 展开后,扫描块定义表,
找出"含实体记录但在任何选中 layout 中均无 INSERT 引用"的块定义。判据仅基于
reader 输出的 records/block_instances,不引入新数据源。

**记账。** 每个 orphan 块产出一条 `orphan_block_definition` WATCH,字段:块名、
成员实体数、图层分布(top N)、嵌套 INSERT 数。conservation ledger 增加独立
`orphan_block_member` 桶,与 `legend`/`out_of_scope` 并列,替代现在笼统的
out_of_scope 归类;evidence 中这些实体的 disposition 相应细分。

**恢复。** 项目级 reviewed 配置(写入 project config,经 validate 校验):

```yaml
plan_domain:
  include_orphan_blocks: ["sfsfsfs", "zcczczc"]   # 或 "*" 表示全部
```

配置后,指定 orphan 块按**单位变换(块基点为原点)**作为虚拟 root 纳入展开,
其成员与嵌套 INSERT 走现有展开路径(含 nested block 递归、cyclic 检测、非均匀
缩放拒绝等既有保护)。恢复进来的实体逐条带
`provenance.orphan_block_recovery: <块名>`,evidence 可查。

**防误恢复与 fail-closed。** 未配置时行为与现状一致(仅多出 WATCH),默认输出不变。
orphan 块基点非原点、或含不支持的块几何时,记 `plan_domain.issues` 并跳过该块,
不静默改几何。配置项必须存在于 reviewed 项目配置中才生效,bootstrap 默认值不含
任何 orphan 恢复。

### 3.2 角色改判:provenance + route 层豁免

**provenance。** `partition_plan_roles` / `partition_model_legend` 改写 `cad_role`
时,在 record 上记录 `cad_role_original` 与
`role_reclassification: {rule, reason}`(rule ∈
{plan_roles_legend_region, plan_roles_frame_span, model_legend_gap})。改判动作从此
可审计,不再只能从 cad_role 反推。

**豁免(下游执行)。** reader 拿不到项目 registry,豁免不在 reader 内做。豁免
具体落在两处:(a) `plan_domain._root_entities` 的 root 选择——凡图层命中
reviewed registry 的 `positive_route_layer_regex` 的实体,legend/title_block
改判失效,按 `cad_role_original` 判定是否入围;(b) `scene_partition` 候选集合
(见下节)。semantics 阶段不再重复判定。无 `cad_role_original` 的 record
(旧 bundle)按现行为处理,兼容既有 baselines。

**scene_partition 同步豁免。** `translated_shape_catalog` /
`aligned_symbol_catalog`(`scene_partition.py:105-158`)的候选集合排除命中
route 层 regex 的实体,避免真实缆线因"同形平移"被整组误杀。已被豁免救回的实体
不再参与目录签名统计。

### 3.3 Paper layout 声明为 plan

source profile 增加可选字段:

```yaml
plan_layouts: ["APD - SF"]   # 默认缺省 = 仅 Model
```

声明的图纸空间 layout 在 `_root_entities` 中与 model/plan 同等入围,
`layout_role` 视为 plan。未声明时维持现状。`layout_role="layout"` 且未被声明的
实体输出一条聚合 WATCH(按 layout 名分组计数),替代现在的完全无声。

### 3.4 Gate 与 run_status

- `orphan_block_member` 桶计数 > 0 且未配置恢复 → run_status WATCH(不阻断);
- route 层实体被 legend/out_of_scope 排除计数 > 0 → WATCH(不阻断);
- 两条 WATCH 进入 run_manifest 与 `cad2gis validate` 输出,一眼可见;
- 全部不阻断转换;阻断行为维持现有 fail-closed 不变量不动。

## 4. 数据流(修复后)

```
reader(autocad)
  → records(新增 cad_role_original / role_reclassification provenance)
  → plan_domain
      ├─ orphan 块检测 → WATCH + conservation 桶
      ├─ include_orphan_blocks 配置 → 虚拟 root 展开(带 provenance)
      ├─ route 层豁免 → 恢复 cad_role_original
      └─ plan_layouts 声明 → paper layout 入围
  → scene_partition(route 层实体不参与目录检测)
  → semantics / materialize / topology / georef(不变)
  → source.gpkg + evidence.gpkg + delivery.gpkg
  → run_manifest(新增两类 WATCH)
```

## 5. 错误处理

| 场景 | 行为 |
|---|---|
| 未配置 include_orphan_blocks | 与现状一致 + orphan WATCH,默认输出不变 |
| 配置了但块基点非原点/变换不支持 | 记 issue,跳过该块,不阻断 |
| 旧 records bundle 无 cad_role_original | 按现行为处理,不报错 |
| plan_layouts 声明了不存在的 layout | validate 报错(fail-closed) |
| route 层豁免后实体仍无几何/点数<2 | 走现有 coverage 记录,不变 |

## 6. 测试

**单元测试:**

- orphan 检测:有/无 INSERT 引用、仅嵌套引用(被另一个 orphan 引用的块不双重计为
  orphan root)、空块;
- 豁免规则:route 层实体被改判后恢复原 role;非 route 层改判保持生效;
- plan_layouts:声明的 layout 入围、未声明的报聚合 WATCH、声明不存在 layout 时
  validate 报错。

**集成测试:**

- 用 -SF 的 records bundle 走既有 APD replay driver:配置恢复后交付 CABLE 数从 1
  恢复为预期值;conservation ledger orphan 桶计数与 WATCH 内容正确;未配置恢复时
  输出与修复前逐字节一致(默认行为回归);
- KLETEK 对账回归:Model 3 条缆线交付不变。

**验收标准:**

1. -SF 图配置恢复后,孤儿块内 route 层实体进入交付并带 provenance;
2. 未配置时默认输出与现状一致,仅多出 WATCH;
3. 全套现有测试(`python -m pytest -q`)通过。

## 7. 诊断产物索引(实现时复用)

- `tmp/diag_reader_apd_test_report.{txt,json}`、`tmp/diag_reader_apd_test_followup.json`:
  读取层真值清单;
- `tmp/diag_sf/`、`tmp/diag_kletek/`:项目配置、onboarding 脚本、对账脚本
  (`reconcile_sf.py` 改路径即可复用于其他图);
- `tmp/diag_sf_run/`、`tmp/diag_kletek_run/`:修复前基线 run(evidence/manifest 可
  用于回归对比)。
