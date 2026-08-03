# 实体静默丢失记账与恢复 — 实现计划

对应设计:`docs/superpowers/specs/2026-08-03-entity-loss-accounting-recovery-design.md`

## 0. 已确认的代码事实(计划的依据)

- `build_plan_domain(entities, *, require_complete_fallback=True)`(`src/cad2gis/cad2gis_v3/plan_domain.py:318`)被两处调用:`pipeline.py:1190`(convert,无配置)和 `project_profile.py:362`(inspect_source,无配置)。块定义表 `definitions` 在函数内由 `_definition_name` 构建(`plan_domain.py:327-333`),root 展开只从选中 root INSERT 出发(`plan_domain.py:479-488`)——orphan 检测所需数据(definitions + 各 layout 的 INSERT)全部在函数内可得,无需新数据源。
- `_root_entities`(`plan_domain.py:270-315`)只认 `layout_role ∈ {model, plan}`;paper layout 实体(`layout_role="layout"`)永不入围,且目前完全无声。
- `scene_partition.detect_style_catalog_entities(roots)` 在 `plan_domain.py:505` 被调用,候选集合 = root 实体全集(`scene_partition.py:100-158`),签名 `{translated_shape_catalog, aligned_symbol_catalog}`。
- reader 记录是**平铺 dict**;`partition_plan_roles`/`partition_model_legend`(`reader/autocad.py:3077-3134`)直接改写 `record["cad_role"]`,调用点三处:`autocad.py:1994-2003`(bulk 路径)和 `:3529-3531`(COM 回退路径)。关键时序:`record["raw_properties"]` 在 partition **之前**已由 `_canonical_raw_properties` 构建(`autocad.py:3037`),且 `_canonical_raw_properties` 是**固定白名单 schema**——provenance 必须在 partition 函数内同时写平铺键和 `raw_properties`,否则不会进入 `SourceEntity`。
- `SourceEntity.from_record`(`model.py:277-339`)只映射固定平铺键 + `raw_properties`。**决策:provenance 载体为 `raw_properties["cad_role_original"]` 与 `raw_properties["role_reclassification"]`,不给 `SourceEntity` 加新字段**——旧 bundle 天然兼容(键缺失即无改判),review bundle(curation.py `build_review_bundle`,其 facts 透传 `raw_properties`)自动携带,evidence `raw_properties` JSON 列自动可查。
- evidence disposition:`evidence.py:288-299`,每实体恰好落入 mapped/annotation/legend/out_of_scope/graphic_only 一桶;conservation ledger 就是 disposition 的 Counter 落表(`evidence.py:283, 871-876`),并有 `conserved == len(entities)` 校验(`evidence.py:971-975`)。新增桶只需在判定链中插入新分支,表结构不变。
- run_status:`RunStatus` 枚举只有 VERIFIED/CONDITIONAL/UNSAFE/FAILED,**没有 WATCH 值**;代码库中 "WATCH" 是 diagnostics 级状态(plan_domain/coverage),映射到 run 级的机制是 `_derive_conversion_status`(`pipeline.py:944-1094`)累加 `warning_count` → `derive_run_status` 输出 CONDITIONAL(非阻断)。`_derive_conversion_status` 目前**不接收** plan_domain diagnostics。
- 配置体系是 JSON 而非 YAML:项目配置 = `config/source_profile.json`(`SourceProfile`,`config.py:456`,schema `cad2gis-project-profile-v1`)+ `config/mapping_registry.json`(`MappingRegistry`,`config.py:752`);两者加载器均做严格键集合校验,新增字段必须显式加入;reviewed 门控由文件级 `review` 记录承担。`positive_route_layer_regex` 已在 registry(`config.py:863-868` 校验可编译)。
- `validate_project`(`project_profile.py:632`)是 `cad2gis validate` 的实现,可读到 `review/source_inventory.json`(含 `layouts` 与 `block_names` 计数)——layout/块名存在性校验不需要读 DWG。
- `inspect_source` 路径也会跑 `build_plan_domain` 并把摘要写进 `inventory["plan_domain"]`;`inventory_sha256` 计算时 `plan_domain`/`inspection_status` 被剔除(`project_profile.py:286-295`),所以 plan_domain 诊断变化**不会**破坏 inventory 绑定。
- 现有测试:`tests/test_plan_domain.py`(308 行,`_entity` fixture 走 `SourceEntity.from_record`)、`tests/test_scene_partition.py`、`tests/test_run_status.py`、`tests/test_baseline_reconciliation.py`(APD baseline bundle + delivery 计数)。注意:committed review bundle 的 facts **无坐标**(shape_binding 指纹替代),`load_records` 重放不出几何——"-SF records bundle 全流水线 replay" 需要全量 facts 的 bundle 夹具(见风险 R6)。

## 1. 关键设计决策(先于阶段划分)

**D1 — 配置落点。**
- `plan_layouts: list[str]` → **source profile 顶层可选字段**(`config/source_profile.json`)。理由:它声明的是"这份源图纸哪些图纸空间 layout 是平面图",是 source 事实,与 spec 3.3 一致。
- `plan_domain.include_orphan_blocks: list[str] | "*"` → **source profile 的可选 `"plan_domain"` 对象**。本仓库中 source_profile.json 即 project profile(`PROJECT_PROFILE_SCHEMA_VERSION`),没有第三个"项目配置"文件;orphan 恢复是 source-bound 的 reviewed 决策,放 profile 与 reviewed 门控天然一致。加载方式沿用 `SourceProfile._load_project_profile` 的严格键集合 + 可选键模式(参照 `source_coordinate_scale_to_m` 先例)。bootstrap(`project_profile.py:_draft_profile`)与 onboarding 编译器(`onboarding.py:_compile_profile_draft`)默认**不产出**这两个字段——默认无任何恢复,满足 spec"bootstrap 默认值不含 orphan 恢复"。
- 豁免用 regex 不新增配置,复用 registry 的 `positive_route_layer_regex`;`"(?!)"`(永不命中,draft 默认)视为"未配置豁免",行为与现状完全一致。

**D2 — WATCH 机制接入。**
不给 `RunStatus` 加枚举值。在 `_derive_conversion_status` 中把两类新事实折算进既有 `warning_count` 参数:orphan 桶 >0 且未恢复 → +1;route 层被排除 >0 → +1。结果走既有 CONDITIONAL(即代码库中与 spec "WATCH" 对应的非阻断状态)。fail-closed 不变量(serious_failures → UNSAFE)一行不动。**副作用要写进文档/发布说明**:CONDITIONAL 不发布 `latest_verified.json` 别名(`run_status.py:publish_verified_alias`),这是"不阻断转换但可见"的既有语义,符合 spec 意图。

**D3 — orphan 定义与防双计。**
- orphan 定义 = `definitions` 中含实体记录、且在**选中 root 所属 layout 集合**内无任何 INSERT(`_block_name` 命中)引用的块定义。块内嵌套 INSERT 不算引用(其 layout_role 是 block_definition,不在选中 layout)。
- orphan **root** = 不被任何其他 orphan 定义引用的 orphan 定义。WATCH 按 orphan root 逐块产出(成员统计递归展开、按 entity_key 去重);仅被 orphan 引用的嵌套块不单独成桶,避免双计。
- 恢复展开只对 orphan root 生效;配置了一个"非 root"的 orphan 块名时,若它已被某个已恢复的 root 覆盖则记 issue 跳过(不重复展开),否则按 root 处理。

**D4 — provenance 载体。**
`raw_properties["cad_role_original"]: str`(仅发生改判时写入,首写不覆盖)与 `raw_properties["role_reclassification"]: {"rule": ..., "reason": ..., "from": ..., "to": ...}`。rule 枚举:`plan_roles_title_block_name`、`plan_roles_frame_span`、`plan_roles_legend_region`、`plan_roles_design_summary`、`plan_roles_title_region`、`model_legend_gap`(spec 只列三个,但 `partition_plan_roles` 有 5 个改判分支;为"改判从此可审计"必须全覆盖,命名如上,spec 的三个名保留)。block_definition 布局的 `plan→block_definition` 改写(`autocad.py:2002-2003`)是确定性布局归一化,**不记** reclassification。

**D5 — plan_domain 新签名(全部可选,缺省 = 现状逐字节)。**
```python
def build_plan_domain(
    raw_entities, *,
    require_complete_fallback: bool = True,
    route_layer_pattern: re.Pattern | None = None,      # 豁免
    plan_layouts: tuple[str, ...] = (),                 # paper layout 入围
    include_orphan_blocks: tuple[str, ...] | str | None = None,  # "*" = 全部
) -> PlanDomainView
```
`pipeline.py:1190` 传入三个新参数(regex 由 registry 编译,`"(?!)"`→None);`project_profile.py:362`(inspect)保持缺省——onboarding 不做任何恢复/豁免,只产出检测 WATCH。

## 2. 分阶段实施

### 阶段 1:reader 角色改判 provenance(小)

**改文件:`src/cad2gis/reader/autocad.py`**
- `partition_plan_roles`(`:3077-3111`):提取私有助手 `_reclassify(record, new_role, rule, reason)`,在五个改判分支处调用;仅当 `cad_role` 真被改写时记录;`cad_role_original` 首写不覆盖(同一 record 可能被两个函数先后改判,original 永远是最初值);同步写入 `record["raw_properties"]`(存在时)。
- `partition_model_legend`(`:3114-3134`):同上,rule=`model_legend_gap`。
- 三处调用点(`:1994-2003`、`:3529-3531`)不改。

**新增测试:`tests/test_reader_role_provenance.py`**
- 构造平铺 record 列表(模仿现有 reader 测试风格)直调两个 partition 函数:改判实体带 `cad_role_original`/`role_reclassification` 且平铺与 raw_properties 一致;未改判实体无此键;二次改判不覆盖 original;`partition_model_legend` 只改判 `cad_role=="model"` 的实体(现行逻辑)且 provenance 正确。

**兼容:** 只增不改;下游尚不消费,输出零变化。
**工作量:小。**

### 阶段 2:orphan 块检测 + WATCH + conservation 桶(中)

**改文件:`src/cad2gis/cad2gis_v3/plan_domain.py`**
- `build_plan_domain`:在 root 选择与 definitions 构建后新增 `_detect_orphan_definitions(definitions, inventory, root_layouts)`:收集选中 layout 内全部 INSERT 的 `_block_name`,差集得 orphan 定义,再剔除被其他 orphan 引用者得 orphan root;对每个 root 递归统计(成员实体数、图层分布 top 10、嵌套 INSERT 数、member_entity_keys 去重)。
- `diagnostics` 新增 `"orphan_blocks": [...]`(无 orphan 时为 `[]`);diagnostics["issues"] 追加 `code="orphan_block_definition", severity="warning", blocking=False` 每 root 一条(字段:block_name、member_count、layer_distribution、nested_insert_count)。**注意:这会让含 orphan 的图纸 plan_domain status 从 PASS 变 WATCH**——这是 spec 的本意(记账可见),但会反映到 manifest 与 inspect 的 `inspection_status`(见 R2)。
- orphan member 的 entity_key 集合放入 `diagnostics["orphan_member_entity_keys"]`(排序列表),供 evidence 与 gate 消费。

**改文件:`src/cad2gis/cad2gis_v3/evidence.py`**
- `_write_staged` / `write_evidence` 增加可选参数 `orphan_member_keys: frozenset[str] | None = None`;disposition 判定链在 `out_of_scope` 分支前插入:`entity.entity_key in orphan_member_keys → "orphan_block_member"`。None 时代码路径与现状逐字节一致。conservation ledger 自动多桶;`conserved == len(entities)` 不变式天然保持。

**改文件:`src/cad2gis/cad2gis_v3/pipeline.py`**
- `convert`:从 `plan_domain.diagnostics` 取 orphan member keys,`write_evidence` 调用点(`:1589`)传入。

**修改测试:`tests/test_plan_domain.py`** — 新增用例:
- 有 INSERT 引用的块不记 orphan;无任何引用的块记 orphan + WATCH;
- 仅被另一个 orphan 嵌套引用的块不双计(root 集合正确,成员去重);
- 空块(无实体成员)不记;
- 无 orphan 时 `orphan_blocks == []`、无新增 issue,其余 diagnostics 与修复前一致(回归)。

**新增集成断言**(放在阶段 7 的集成测试文件,本阶段可先用小 fixture):evidence.gpkg `conservation_ledger` 出现 `orphan_block_member` 桶且计数正确,全表总和仍等于实体总数。
**工作量:中。**

### 阶段 3:`plan_domain.include_orphan_blocks` 恢复展开(中)

**改文件:`src/cad2gis/cad2gis_v3/config.py`**
- `SourceProfile` 增加字段 `include_orphan_blocks: tuple[str, ...] = ()`;`_load_project_profile` 的 `expected` 集合增加可选键 `"plan_domain"`:存在时必须是对象、只允许键 `include_orphan_blocks`,值为非空字符串数组或 `"*"`;缺省/空数组 = 不恢复。legacy schema 路径不动(legacy profile 含此键会被现有 unknown-key 校验拒绝,符合预期)。

**改文件:`src/cad2gis/cad2gis_v3/plan_domain.py`**
- `build_plan_domain` 新增 `include_orphan_blocks` 参数。实现:
  - 规范化配置(`"*"` → 全部 orphan root;大小写统一到定义名 upper);
  - 对每个待恢复 orphan root 构造**合成虚拟 root**:`replace` 一个确定性 `SourceEntity`(dwg_type="INSERT"、entity_key=`f"orphan-recovery-root:{name}"`、handle 同、layout 取第一个 root 的 layout、raw_properties 带完整单位变换 transform_facts:insertion=原点、scale=(1,1,1)、rotation=0、normal/extrusion=(0,0,1)),使 `resolve_insert_affine` 走既有路径;
  - 复用现有 `visit()` 展开(nested 递归、cyclic 检测、非均匀缩放拒绝全部继承);恢复实体经 `_materialize_leaf` 获得确定性 `plan:` 前缀 key;
  - 给恢复出的每个 derived 实体在 `raw_properties["plan_domain"]` 之外追加 `raw_properties["provenance"] = {"orphan_block_recovery": <块名>}`(在恢复专用的 materialize 包装里做,不污染正常路径);
  - fail-closed:块定义基点非原点(从该定义任一成员的 `raw_properties.transform_facts.block_base_point` 读取,见 R4)、配置了不存在的块名、或 `_materialize_leaf` 抛 `unsupported_block_geometry_transform` → 记 issue(severity warning,不阻断),跳过该块;
  - `include_orphan_blocks is None`(缺省)时整段不执行,输出与阶段 2 一致。
- diagnostics 增加 `"orphan_recovery": {"configured": [...], "recovered": [...], "skipped": [{block_name, reason}]}`。

**改文件:`src/cad2gis/cad2gis_v3/pipeline.py`** — `convert` 传 `include_orphan_blocks=("*" if profile 配置为 "*" else profile.include_orphan_blocks)`;未配置 → None。恢复实体 key 不在 raw inventory 中,自动进入 `evidence_entities`(`pipeline.py:1198-1205` 的既有差集逻辑),evidence 可查 provenance。

**修改测试:`tests/test_plan_domain.py`** — 恢复用例:单位变换展开坐标正确;嵌套 INSERT 递归;provenance 标记;cyclic/非均匀缩放拒绝路径仍记 issue;配置不存在块名 → issue 且不阻断;未配置 → 不展开。
**修改测试:`tests/test_canonical_cli.py` 或新增 config 测试** — profile 加载:`plan_domain` 键合法/非法值、`"*"`、缺省。
**工作量:中。**

### 阶段 4:route 层豁免(plan_domain root 选择 + scene_partition 候选)(中)

**改文件:`src/cad2gis/cad2gis_v3/plan_domain.py`**
- 新增助手 `_effective_cad_role(entity, route_pattern)`:`route_pattern` 非 None 且命中 `entity.layer` 且 `raw_properties` 含 `cad_role_original` → 返回 original;否则返回 `entity.cad_role`。
- `_root_entities` 增加可选 `route_pattern` 参数,三处 role 比较(`preferred_model`/`preferred_plan` 的过滤、以及入围后 `:359` 的 role 规范化判定)改用 `_effective_cad_role`。被豁免救回、原 role 非 model/plan 的 root 走既有 `layout-root-role-normalization` 分支(`plan_domain.py:362-371`),并把豁免事实补进该 raw_properties 记录。
- `detect_style_catalog_entities(roots)` 调用点(`:505`)传入豁免谓词。
- diagnostics 增加 `"route_layer_exemption": {"exempted_count": N, "route_layer_excluded_count": M}`。M = 命中 route regex 但最终未进入 output 的 root 候选实体数(被 role 排除且未被豁免、或被 scene_partition 排除)——即阶段 6 gate 的数据源。`route_pattern is None` 时两个计数均为 0。

**改文件:`src/cad2gis/cad2gis_v3/scene_partition.py`**
- `detect_style_catalog_entities(entities, *, exempt=None)`:`exempt` 谓词非 None 时,候选收集(shapes 分组与 INSERT 桶)跳过命中实体;diagnostics 增加 `"exempted_entity_count"`。缺省 None → 逐字节现状。

**改文件:`src/cad2gis/cad2gis_v3/pipeline.py`** — `convert` 中 `re.compile(registry.positive_route_layer_regex)`(`"(?!)"` → None)传入 `build_plan_domain`。

**修改测试:`tests/test_plan_domain.py`** — route 层实体被改判 style_legend 后恢复原 role 入围;非 route 层改判保持生效;无 `cad_role_original` 的 record(模拟旧 bundle)按现行为处理。
**修改测试:`tests/test_scene_partition.py`** — 命中豁免的实体不参与目录签名统计、不被整组排除;缺省参数回归(现有用例不动即通过)。
**工作量:中。**

### 阶段 5:`plan_layouts` paper layout 入围(中)

**改文件:`src/cad2gis/cad2gis_v3/config.py`**
- `SourceProfile` 增加 `plan_layouts: tuple[str, ...] = ()`;`_load_project_profile` `expected` 增加可选 `"plan_layouts"`(字符串数组、去重、不允许空串)。

**改文件:`src/cad2gis/cad2gis_v3/plan_domain.py`**
- `build_plan_domain` 新增 `plan_layouts` 参数(大小写不敏感匹配 `entity.layout`)。实现放在 root 选择前的视图层:声明命中的实体以 `replace(entity, layout_role="plan")` 进入选择流程,`raw_properties["plan_domain"]` 记录 `{"materialization": "declared-plan-layout", "declared_layout": <名>}`(不修改原 inventory,符合模块 docstring 的不可变契约);`_root_entities` 既有 plan 分支(`:296-314`)接管。
- 未声明的 `layout_role=="layout"` 实体:按 layout 名聚合计数,产出**一条** warning issue `code="undeclared_layout_entities"`(fields:layout 分布 dict、总数),替代完全无声。`plan_layouts` 为空时此聚合照常产出(spec 3.3)——见 R2,这会让所有含 paper layout 的图纸 status 变 WATCH。
- diagnostics 增加 `"plan_layouts": {"declared": [...], "admitted": [...], "undeclared": {layout: count}}`。

**改文件:`src/cad2gis/cad2gis_v3/project_profile.py`**
- `validate_project`:非 legacy 路径下,若 `profile.plan_layouts` 非空,对照 `inventory["layouts"]` 的键做存在性校验,缺失 → 追加到 `failures`(fail-closed 报错,与现有 binding 校验同路径);legacy 路径不检查(legacy loader 根本不会读到该字段)。

**改文件:`src/cad2gis/cad2gis_v3/pipeline.py`** — `convert` 传 `plan_layouts=profile.plan_layouts`。

**修改测试:`tests/test_plan_domain.py`** — 声明 layout 入围(plan 分支);paper layout 内 INSERT 能作为 root 展开;未声明 layout 出聚合 WATCH 且计数正确。
**新增/修改校验测试**(可放 `tests/test_source_inspection.py` 或新文件)— 声明不存在的 layout 时 `validate_project` 报错;声明存在的通过。
**工作量:中。**

### 阶段 6:run_status / manifest / validate 接入(小)

**改文件:`src/cad2gis/cad2gis_v3/pipeline.py`**
- `_derive_conversion_status` 增加可选参数 `plan_domain_diagnostics: Mapping | None = None`;`convert` 调用点(`:1633`)传入 `plan_domain.diagnostics`。规则:
  - `orphan_blocks` 成员总数 >0 且 `orphan_recovery.recovered` 为空 → `warning_count += 1`;
  - `route_layer_exemption.route_layer_excluded_count` >0 → `warning_count += 1`。
  - 效果:经既有 `derive_run_status(warning_count=...)` → CONDITIONAL,不阻断,不动 `run_status.py` 一行。
- manifest:`plan_domain.diagnostics` 已在 manifest(`pipeline.py:1712`),两类 WATCH 随之可见;无需新顶层字段。

**改文件:`src/cad2gis/cad2gis_v3/project_profile.py`**
- `validate_project` 返回 dict 在非 legacy 分支增加 `"warnings": [...]`(对齐 legacy 分支已有字段):从 `inventory["plan_domain"]["issue_codes"]` 提取 `orphan_block_definition` / `undeclared_layout_entities` 计数生成人类可读条目。validate 不跑流水线,数据来自 inspect 阶段已写入的摘要(阶段 2/5 后 inspect 自动产出)——这使得两条 WATCH "一眼可见"于 `cad2gis validate` 输出。

**修改测试:`tests/test_run_status.py`** — `_derive_conversion_status` 经 pipeline 层较重,改为直接对 `derive_run_status` 的现有用例不动,新增针对 `_derive_conversion_status` 的轻量用例(构造最小 diagnostics dict 断言 CONDITIONAL/VERIFIED 分野)。
**工作量:小。**

### 阶段 7:集成测试与回归(中——依赖夹具,见 R6)

**新增测试:`tests/test_entity_loss_recovery.py`**
- 驱动方式对齐 spec:"records bundle + 项目配置重放"。需要**全量 facts** 的 -SF records 夹具(committed review bundle 无坐标,不可用;见 R6)。若夹具可用:
  1. 配置 `include_orphan_blocks` + route 豁免 + `plan_layouts` 后走 `build_plan_domain → classify_entities`(或直接 `pipeline.convert`,若夹具绑定允许),断言交付 CABLE 数从 1 恢复为预期值、恢复实体带 `provenance.orphan_block_recovery`、conservation ledger orphan 桶与 WATCH 内容正确;
  2. **默认行为回归**:不配置恢复时,delivery 计数/内容与 `tmp/diag_sf_run/` 修复前基线一致(比对 delivery.gpkg 各层计数与关键几何;evidence/manifest 因新增诊断字段必然变化,见 R1);
  3. KLETEK 回归:Model 3 条缆线交付不变(对 `tmp/diag_kletek_run/` 基线)。
- 若夹具不可用,用合成最小 DWG 等价物(`_entity` fixture 构造含 orphan 块的 inventory)覆盖 1/2 的逻辑断言,真实图纸验证留作手工验收步骤写进测试 docstring。
- 最后全量 `python -m pytest -q` 通过(spec 验收 3)。

**工作量:中(逻辑);若需制作/搬运真实夹具则大。**

## 3. 风险点与实现时必须验证的假设

- **R1(已裁决)—"逐字节一致"的口径。** 用户 2026-08-03 确认:**交付面(delivery.gpkg 各层内容与计数、source.gpkg、语义结果)未配置恢复时与现状逐字节一致;记账面(evidence disposition 细分、conservation ledger orphan 桶、manifest、validate 输出)无条件增强**。阶段 6 gate 的数据源由此保证。
- **R2 — status 翻转面。** 阶段 2/5 的 WATCH 使含 orphan 或未声明 paper layout 的图纸 plan_domain status PASS→WATCH,`inspect_source` 的 `inspection_status` 同步变化。需跑一遍现有测试确认没有断言旧 status 的用例(重点:`test_source_inspection.py`、baseline 相关)。
- **R3 — 旧 bundle 兼容。** APD baseline(9391 条)无 `cad_role_original`:阶段 4 豁免路径对其完全短路(键缺失 → 现行为);`tests/test_baseline_reconciliation.py` 必须原样通过。
- **R4 — orphan 块基点的权威来源。** "块基点非原点则跳过"依赖 `raw_properties.transform_facts.block_base_point` 在块定义成员/INSERT 上的填充情况;匿名块(`*U55`、`sfsfsfs`)该字段是否有值需在 -SF bundle 上实测;若普遍缺失,需在阶段 3 定义"UNAVAILABLE 视为不可恢复(fail-closed 跳过)"而非默认原点。
- **R5 — partition 函数的 raw_properties 时序。** `_canonical_raw_properties` 在 partition 之前构建且为白名单 schema;provenance 必须双写(平铺 + raw_properties),且 review bundle 导出(`curation.py:787 _safe_model_context`)需实测不剥离新嵌套键。
- **R6(已裁决)— -SF 全量 records 夹具策略。** 用户 2026-08-03 确认:**合成夹具(可提交)覆盖逻辑断言;真实 -SF 验证做成环境变量门控的测试**(沿用 `CAD2GIS_FULL_DWG_TESTS` 惯例,直接读 `ROOT.parent/APD_test`,参照 `tests/test_apd_test_compatibility.py` 的 `_dataset_root()` 模式),不把大 records 文件提交进仓库。committed review bundle 无坐标的问题由此绕开。阶段 7 按此执行:合成 fixture 走 `build_plan_domain → classify_entities` 逻辑断言;环境门控测试在装有 AutoCAD 的机器上跑真实 -SF 全流水线验收。
- **R7 — `layout_role=="layout"` 实体的 `cad_role` 实际值。** plan_layouts 入围后走 `_root_entities` 的 plan 分支,其 `preferred_plan` 过滤依赖 `cad_role=="plan"`;paper layout 分组不跑 partition(`autocad.py:1993` 只处理 model/plan),cad_role 初值需实测(很可能等于 layout_role,即 "layout")——若如此,入围路径会落到 `plan-layout-fallback` + `layout-root-role-normalization`,行为正确但触发 strict fallback(`require_complete_fallback`),需在阶段 5 用例中覆盖。
- **R8 — scene_partition 豁免的回归面。** 提供 route_pattern 后,目录签名统计的候选集合变小,理论上可能改变现有图纸的 catalog 检测(-SF/KLETEK 之外)。仅 APD baseline 走 convert 时传 registry regex,需跑 baseline 对账确认无变化。
- **R9 — CONDITIONAL 的别名副作用。** -SF 类图纸修复前若曾是 VERIFIED,接入 WATCH 后变 CONDITIONAL,`latest_verified.json` 不再更新——这是既有"可见性优先"语义,需在变更说明中显式告知。

## 4. 阶段总览

| 阶段 | 内容 | 主要文件 | 工作量 | 独立可提交 |
|---|---|---|---|---|
| 1 | reader 改判 provenance | reader/autocad.py + 新测试 | 小 | 是(零行为变化) |
| 2 | orphan 检测 + WATCH + conservation 桶 | plan_domain.py、evidence.py、pipeline.py、test_plan_domain.py | 中 | 是 |
| 3 | include_orphan_blocks 恢复 | config.py、plan_domain.py、pipeline.py + 测试 | 中 | 是 |
| 4 | route 层豁免 | plan_domain.py、scene_partition.py、pipeline.py + 测试 | 中 | 是(依赖 1 的 provenance 才有实效,代码可独立) |
| 5 | plan_layouts 入围 + validate 校验 | config.py、plan_domain.py、project_profile.py、pipeline.py + 测试 | 中 | 是 |
| 6 | run_status/manifest/validate 接入 | pipeline.py、project_profile.py、test_run_status.py | 小 | 是 |
| 7 | 集成测试与基线回归 | tests/test_entity_loss_recovery.py(新) | 中(有夹具)/大(无夹具) | 是 |

建议提交顺序即上表顺序;2→3→4→5 均只通过新的可选参数接线,任一阶段合入后未配置路径行为不变(R1 口径下)。
