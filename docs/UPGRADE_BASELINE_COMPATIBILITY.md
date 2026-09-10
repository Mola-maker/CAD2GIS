# 六份历史 baseline 的兼容性审查

日期：2026-09-09。结论分为两组：**Taipa、Tinggede、Darat 的 inventory 哈希差异已证明仅由文件名造成；Manado、Semarang、Tinggar 的数量差值精确对应历史被当作 Model 的一个 paper layout。** 本次未修改原 DWG、baseline、review、expected counts 或任何精度门禁。

完整原始 profile/registry/source 哈希、当前 census、paper handle 清单和候选方案见 [upgrade-baseline-migration-proposals.json](upgrade-baseline-migration-proposals.json)。全部条目为 `PROPOSAL_ONLY`、`accepted=false`；该文件不是可直接加载的生产 profile。

## 1. 历史角色契约未迁移：三个 named paper layout

| 项目 | 历史 expected Model 实体 / INSERT / DIMENSION | 当前物理 Model | 当前保留的 paper 实体 | 相加结果 |
|---|---:|---:|---|---:|
| Manado–Tomohon uplink | 2,970 / 127 / 43 | 2,788 / 125 / 43 | `APD`：182 / 2 / 0，owner `22F0` | 2,970 / 127 / 43 |
| Semarang SF | 571 / 56 / 18 | 118 / 28 / 18 | `APD`：453 / 28 / 0，owner `18958` | 571 / 56 / 18 |
| Tinggar | 2,845 / 156 / 0 | 2,342 / 108 / 0 | `FDT_2`：503 / 48 / 0，owner `5F298` | 2,845 / 156 / 0 |

这些 paper 实体实际保留在当前 reader records 中；Source export 的总 records 分别为 5,057、1,455、11,141。因此不能把 Model 数量变小直接判成此次 reader 漏读。反过来，数量相加吻合也不能证明历史和当前全部实体、文本、颜色及几何事实完全等价：原 baseline 包只有 config，没有与旧 inventory 哈希绑定的完整历史 records。

历史 Git 证据已经定位：用户提供的完整 Git snapshot 中，commit `e682de2ab63b939f7ab6f2fad4de63b8953fdb5e`（2026-09-02）修改 `reader/libredwg.py:_classify_layout_role`：

```diff
-    if name and name.casefold() != "paper":
-        return "model"
+    # A named paper tab is not model space. It may carry authoritative plan
+    # geometry, but admitting it requires a source-bound ``plan_layouts``
+    # declaration instead of a reader-wide rewrite.
     return "layout"
```

三份 baseline 的 review 时间都是 2026-08-20，config 在 `686d379`（2026-08-24）入库；检查 `686d379..e682de2`，上述三个 profile 没有同步迁移。它们没有 `plan_layouts` 声明。

这与当前的物理 layout 证据一致：Tinggar 的 `dwgread -O minJSON` 返回 `HEADER.BLOCK_RECORD_PSPACE=389784`，即十六进制 `5F298`；对应 `LAYOUT.layout_name=FDT_2`。CLI reader records 中该 layout 的 503 个普通实体使用同一 owner。其余 `FDT_1`、`FULL FDT`、`DIST3 (2)` 仍分别存在，不能为了恢复旧 Model 数字而把全部 paper tabs 纳入 GIS。

三条上传分支中的 `project_profile.py` 完全相同，均只统计 `cad_role == "model"`；其文件 SHA-256 见提案 JSON。现有 `libredwg.py` 在三分支也相同。这排除了本轮对 census 函数的改动造成该差异。主任务还用未修改的 `e401383` adapter 在同一 DWG/runtime 上复现了 Manado 的当前 inventory hash `77fed9d4…`；专项独立重读得到相同结果。

**为何仍保留 BLOCKED：** 历史 named paper 被自动当成 Model 的行为已移除，恢复它会重新把图框、图例或绘图页内容带进业务几何。当前 profile 的旧 inventory binding 与旧 census 也不满足新 reader 契约。不能仅凭总数吻合替用户编造新的 review，或下调 expected counts 后继续宣称历史验收仍有效。

可审阅的两种候选方案已经按图写入 JSON：

| 方案 | 预期影响 | 接受前需要的证据 |
|---|---|---|
| 默认 Model-only | paper 继续保留在 source evidence；不作为 plan-domain roots。 | 新项目绑定、Model 内业务要素与旧交付的逐项对应，确认遗漏的工程内容是否实际只画在 paper 中。 |
| 显式 `plan_layouts` | 只为 Manado/Semarang 声明 `APD`，或为 Tinggar 声明 `FDT_2`，生成派生 plan view。 | 复核该页的 paper/viewport 坐标、图例重复、目标工程范围、source→delivery lineage 与业务要素数量。不能把整个 paper 页直接套用 Model 的地理位置。 |

`plan_layouts` 发生在 immutable ingest 之后，因此两个方案的**物理 Model census 都保持当前值**。它不是把 source records 的 `cad_role` 改回 `model` 的开关。最终接受必须是新的、来源明确的 review，旧 baseline 仍保留。

## 2. 文件名导致的哈希差异：三份可保持原 baseline 复核

inventory hash 包含 `source.name`；这里的 path-independent 不代表 basename-independent。上传文件名增加了下载平台前缀，而 baseline 使用旧名称。

| 项目 | 当前 Model 实体 / INSERT / DIMENSION（与 baseline 相同） | 已证明的历史 basename |
|---|---:|---|
| Taipa | 1,297 / 151 / 118 | `APD - TAIPA RW 05 PALU.dwg` |
| Tinggede | 918 / 79 / 51 | `APD - PERUMAHAN TINGGEDE VIEW PALU.dwg` |
| Darat Sekip SF | 2,395 / 35 / 12 | `APD - DARAT SEKIP RW 12 PONTIANAK - SF.dwg` |

在内存中只替换当前 inventory 的 `source.name`，其他 canonical 字段均不改，然后调用正式 `inventory_sha256()`：

| 项目 | 当前 inventory SHA-256 | 恢复历史 basename 后的 SHA-256，完全等于 baseline |
|---|---|---|
| Taipa | `b8f38332184bc1066b724c4cbb1d748f0763ebdd7ffb1a2acaca47c82195d1a2` | `ce35d5023f96dfead9e7b46e9581ed1004dc889fbeff94d86e7f1a319755f9ae` |
| Tinggede | `3bedaf6b8d5bf3b1ac13c9501ec76fc4b464d80acacb616f341b8b69299e1942` | `42b9ac1812f3423ed71a1a4d1e8c59c990a43da234aef9bad6684debc8cf2eb8` |
| Darat Sekip SF | `72cf2128bbb012be7ed0a7e3025329893418bc22eb209c27a1f054a8d7c0dfac` | `488e5126ea596513dedcd0c65f472fc589fb8ca0eb96664c6205616d72a8dd04` |

此外，主任务保留的 `e401383` 同源重读 inventory 与当前 inventory，除正式 hash 函数本来就排除的 `plan_domain / onboarding / inspection_status / cad_scene_graph` 外逐键完全相等。当前 reader protocol 为 `libredwg_cli_dxf`。这里只恢复 basename 已精确命中整个旧 inventory hash，故无需猜测 backend 差异或改写协议字段。

可执行且不改变原批准配置的处理方式：

1. 在新的 verification project 内复制同 SHA-256 的 DWG，使用表中的历史 basename；保留原上传文件。
2. 重新 export/inspect，由 reader 实际生成 inventory；再次核对 source bytes、inventory hash 与 baseline binding。
3. 使用原 profile/registry、原 expected counts 和全部门禁执行 validate→convert→audit。文件名恢复只解决 inventory 兼容，不保证后续 CRS/GEODATA、业务数量或坐标往返验收通过。

这三份不需要接受“降低计数”的迁移，也不需要重写 historical reviewer。提案中的候选动作仅为 source copy alias 和原门禁重跑，执行结果由主任务实图验证报告记录。

## 3. 验证范围与剩余限制

- 已读三分支的 reader / inventory 实现、历史 Git diff、源 profile、当前逐实体 records，以及历史验证文档。Manado 在当前 runtime 上独立重读一次；其余使用已绑定 source exports，未重跑全套测试。
- `docs/SOFTWARE_VERIFICATION_2026-09-09.md` 原本就写明曾创建独立 `current-reader-project`，而且未覆盖仓库 baseline；它在另一环境的成功结果不能证明当前 config-only archive 可以原样复跑。
- `tools/review_reader_migration.py` 仅允许完整 records 对比后的 RGB/微小浮点迁移；它明确拒绝不同实体集合/reader 语义差异。不得扩展其容忍范围来吞掉上述 paper-domain 改动。
- 六份源 DWG 的 SHA-256 均匹配原 baseline source binding。这里没有 surveyed checkpoints，也没有新增绝对位置精度结论。
- 长期可考虑新的 inventory schema 将展示名称与几何事实身份分离，但须定义跨版本迁移，不能直接修改现有 v1 hash 算法让旧校验失效。

技术定义已查阅 [ezdxf Layout Types](https://ezdxf.readthedocs.io/en/stable/layouts/layouts.html) 与 [Get Layouts and Blocks](https://ezdxf.readthedocs.io/en/stable/tasks/get_layouts.html)：Modelspace、Paperspace、BlockLayout 是不同容器，paper 内容也可包含指向 Model 的 viewport。LibreDWG 的 [官方 CLI 说明](https://github.com/LibreDWG/libredwg/blob/master/README) 提供 DWG→DXF/JSON 接口；本报告没有把第三方 reader 的支持范围当作逐图零损失保证。
