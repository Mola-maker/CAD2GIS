# CAD2GIS verification report

日期：2026-07-19  
范围：architecture-v3 canonical CLI/API、APD compatibility pack、GCP/绝对精度边界、
QGIS 加载与多 CAD claim policy。

## 1. Executive conclusion

截至本文更新：

- canonical CLI parser 已提供 `doctor`、`inspect`、`bootstrap`、`validate`、
  `convert`、`gcp status/prepare/diagnose/export` 和 `verify`；本文逐项执行了 source
  checkout 的 `--help`，全部返回 0；
- `src/cad2gis.pipeline` 是 package、experiment wrapper 与 QGIS adapter 共享的
  conversion facade；旧 demo/official converters 有默认禁用 guard；
- onboarding 合同生成 source-bound draft，并在人工 review 前禁止 conversion；
- reader、curve、unit/CRS、unsupported/abstain、GCP 与 verification matrix 有对应
  实现和自动化测试文件；本次纯文档工作没有运行全套测试，因此本文不虚构 pass
  数字；
- APD Hutabohu 是仓库目前唯一可作为真实 DWG regression anchor 的输入；
- APD GCP profile 明确 `enabled=false`、`controls=[]`，所以绝对地面精度为
  `not_verified`；
- 尚无第二份 distinct、独立 reviewed 且完整评估的真实 CAD matrix row，不能声称
  cross-CAD success；
- 未在本次文档工作中完成真实 QGIS 渲染/截图验收，不能把结构测试等同于人工视觉
  验收。

## 2. Evidence levels

本报告使用以下状态，避免把“代码存在”写成“真实数据已通过”：

| 状态 | 含义 |
| --- | --- |
| `HELP_PASS` | 实际调用 parser 的 `--help`，返回 0 |
| `IMPLEMENTED_TEST_DEFINED` | 实现与自动化测试场景存在；本次文档子任务未重跑全套 |
| `SINGLE_REAL_INPUT_BASELINE` | 只由 APD 一份真实 DWG 及其 source-bound contract 支持 |
| `INVENTORY_ONLY` | 只登记文件/metadata，未获得独立 reviewed conversion row |
| `NOT_VERIFIED` | 缺少该结论所需证据，特别是绝对精度或 QGIS 实际验收 |
| `NOT_ESTABLISHED` | 所需样本数量/独立性不足，不能作跨 CAD 声明 |

历史目录名中的 `complete`、`gcp_ready` 或 `validation` 不自动改变状态。只有 manifest
中的 source/config/implementation/toolchain bindings 和 artifact hashes 与当前审查
对象一致，snapshot 才能作为该版本产物证据。

## 3. Canonical command surface

静态检查使用当前 checkout：

```powershell
$env:PYTHONPATH='src'
python -m cad2gis --help
python -m cad2gis doctor --help
python -m cad2gis inspect --help
python -m cad2gis bootstrap --help
python -m cad2gis validate --help
python -m cad2gis convert --help
python -m cad2gis gcp --help
python -m cad2gis gcp status --help
python -m cad2gis gcp prepare --help
python -m cad2gis gcp diagnose --help
python -m cad2gis gcp export --help
python -m cad2gis verify --help
```

结果：以上帮助命令均为 `HELP_PASS`。已核对的稳定语法：

| 命令 | 核对后的主要参数 |
| --- | --- |
| `doctor` | `[--json] [--deep] [--strict]` |
| `inspect` | `SOURCE [--project DIR] [--json]`；`--input` 为兼容 alias |
| `bootstrap` | `SOURCE --project DIR [--force] [--json]` |
| `validate` | `--project DIR [--json]` |
| `convert` | `SOURCE --run-dir DIR [--project DIR] [--source-profile P] [--mapping-registry R] [--gcp-profile G] [--json]` |
| `gcp status` | `--project DIR [--json]` |
| `gcp prepare` | project 模式，或显式 delivery/evidence/manifest/output/candidate-layer paths |
| `gcp diagnose` | project 模式，或显式 capture/report/robust-threshold paths/options |
| `gcp export` | project 模式，或显式 capture/template/output/diagnostic/model/reviewed gate options |
| `verify` | `MATRIX.json [--json]` |

用户文档只推荐 positional `SOURCE` 与 `--project`，不把兼容 alias 当作第二种
canonical 命令。全局 `--debug` 可位于 command 前后；默认错误不输出 traceback。
`validate`、`gcp status` 与 `verify` 是报告型命令：成功完成评估不代表报告为
ready/verified/PASS，CI 必须读取 JSON 状态与 `strongest_allowed_claim`。

## 4. Architecture contract evidence

| 维度 | 实现/测试锚点 | 截至当前的结论 |
| --- | --- | --- |
| Entrypoint | `src/cad2gis/cli.py`、`tests/test_canonical_cli.py`、`tests/test_entrypoint_governance.py` | `IMPLEMENTED_TEST_DEFINED`；wrapper/QGIS adapter 指向 canonical facade |
| Backend deployment | `src/cad2gis/runtime.py`、`src/cad2gis/doctor.py` | wheel 不捆绑 experiment backend；三种显式部署，由 doctor 暴露 |
| Different-CAD onboarding | `cad2gis_v3/project_profile.py`、`test_project_onboarding_v3.py`、`test_crosscad_contracts.py` | bootstrap 永远 draft；review 前 conversion_allowed=false |
| Reader strictness | `autocad_reader.py`、`test_readcad_inventory_v3.py`、`test_crosscad_contracts.py` | malformed/unsupported protocol 有显式错误/证据；COM fallback 需 opt-in |
| Curve fidelity | `curve_geometry.py`、`test_curve_facts_v3.py`、`test_crosscad_contracts.py` | line/bulge arc、native length、source segment preservation 有合同场景 |
| Units/CRS | `units.py`、`test_units_crs_v3.py`、`test_crosscad_contracts.py` | mm/ft 需 reviewed scale；unknown/local CRS 不得猜 direct transform |
| Semantics/style | `semantics.py`、`styles.py`、`test_crosscad_contracts.py` | unknown block/layer/linetype 形成 coverage；fail 或 reviewed abstain，不静默 drop |
| Topology/length | `topology.py`、`pipeline.py`、span/topology tests | crossings/support/ports/segment closure 分别留证；不伪造连接或 measurement |
| GCP | `src/cad2gis/gcp_workflow.py`、`tests/test_gcp_workflow.py` | prepare/diagnose/export/status 分离；无 surveyed train/check 时 not_verified |
| Verification matrix | `tests/test_verification_matrix.py` 与 canonical `verify` 命令 | schema/version、distinct hash、inventory-only、absolute hard gate 有测试定义 |

`IMPLEMENTED_TEST_DEFINED` 不是本次运行的 pass verdict。发布前仍需由集成执行者记录
准确测试命令、环境和 observed results。

## 5. APD real-data boundary

APD authoritative experiment input：

- path：`experiment/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`；
- reviewed source SHA-256：
  `557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557`；
- declared source：`INSUNITS=6`、nominal `EPSG:3857`；
- delivery CRS：`EPSG:9481`；
- mapping/source profile：仅绑定上述内容 hash；
- GCP：disabled、0 controls；
- claim status：`SINGLE_REAL_INPUT_BASELINE`，absolute accuracy
  `NOT_VERIFIED`。

APD contract 中的 layer/count、139 CABLE segments（130 measured + 9
`unmeasured_no_dimension`）、2 optical components 与 unresolved snapshot 是这一份源
的回归预期，不是 generic CAD default。已有 run bundles 没有在本次文档任务中按当前
implementation digest 重新发布，因此本文不把任意一个历史目录标成当前 certified
release。

旧 `APD_HUTABOHU_verification.json` 和早期 execution/handoff 文档使用过不同的
验收状态、CRS 或八层假设；它们是历史材料，不覆盖 canonical v3 contract。

## 6. GCP and absolute accuracy audit

APD `config/apd_gcp_profile.json` 当前事实：

| 字段 | 当前值 | 影响 |
| --- | --- | --- |
| `enabled` | `false` | 不应用 residual calibration |
| `controls` | `[]` | 无 train，也无 independent check |
| robust outlier threshold | `null` | 未经项目审查 |
| transform limits | `null` | 未经项目审查 |
| independent-check thresholds | `null` | 未经项目审查 |
| spatial distribution review | `false` | 未授权 calibration |

因此以下证据都不能升级为 absolute accuracy：名义 EPSG 变换成功、PROJ/OSR
agreement、round-trip 数值小、QGIS 与底图目视接近、relative OSM 控制点、LLM
建议或 fitting residual 没有独立 check。

绝对精度从 `NOT_VERIFIED` 变为 verified 至少需要：真实 surveyed/approved
authoritative controls；独立 train/check roles；reviewed numeric/coverage/physical
limits；hash-bound enabled profile；重新 conversion；新 manifest 中 accepted model
和 passed independent metrics；最后 `cad2gis gcp status` 复核。`gcp export` 单独不能
满足这些条件。

## 7. Multi-CAD verification matrix

canonical evaluator 接受 versioned `cad2gis-verification-matrix-v1` JSON。每个完整
evaluated row 应独立声明 source hash/vendor/version/units/CRS、layouts、blocks、
curves、reviewed profile、independent gold，以及 geometry/topology/semantics/style/
length/nominal CRS/GCP 状态。最低审计矩阵如下：

| Matrix row | distinct real input | independent reviewed pack | fidelity dimensions | surveyed GCP/checks | 当前资格 |
| --- | --- | --- | --- | --- | --- |
| APD Hutabohu | 是 | 有 APD source-bound compatibility profiles | 单输入 regression contract | 无 | single-input nominal only；absolute fail/not_verified |
| 同一 APD 的其他路径/副本 | 否，hash 相同 | 同一 pack | 不是新样本 | 无 | 不增加 cross-CAD 资格 |
| 其他仓库 CAD | 文件 inventory 可建立 | 尚未逐一确认 | 尚未形成完整 evaluated row | 未核实 | `INVENTORY_ONLY`/unevaluated |
| 合成 fixtures | 否 | 测试内合同 | 只覆盖代码分支 | 不构成实测 | 不计入真实 CAD 数量 |

Cross-CAD fidelity 至少需要两份 distinct hashes、各自 reviewed pack、各自独立 gold
和适用维度通过。即使达到该门槛，absolute accuracy 仍逐样本单独判断；没有 surveyed
GCP 的行仍失败。

## 8. QGIS verification status

实现提供：

- delivery/evidence GeoPackage 分仓；
- embedded `layer_styles` 与 QML sidecars；
- `qgis_plugin/cad2gis_plugin/adapter.py` 对 canonical pipeline 的薄委托；
- adapter 的无 QGIS 单元测试，可检查 OGR URI 与 layer discovery。

本次文档任务没有启动真实 QGIS，因此下列项目状态仍为 `NOT_VERIFIED`：当前 digest
bundle 是否能在目标 QGIS 版本完整加载、默认样式是否自动应用、线标签沿线、颜色/
线型/旋转是否视觉一致，以及 screenshot/manual checklist。建议验收时：

1. 打开 delivery GPKG，确认图层数、CRS、feature count 和 default styles；
2. 抽查短线/弧线、24C/48C、点符号旋转、`CABLE_SEGMENT.length_label`；
3. 打开 evidence GPKG，抽查 source IDs、unsupported/abstain、topology 与 GCP lineage；
4. 保存 QGIS 版本、操作步骤与截图，并与 manifest hash 绑定。

QGIS 视觉验收不能替代 source/topology/absolute gates，自动 XML/SQLite 检查也不能
替代真实渲染。

## 9. Allowed and forbidden claims

当前允许：

- “CAD2GIS 有 source-bound、fail-closed 的 canonical CLI/API 架构。”
- “APD 是唯一真实 DWG regression baseline，执行名义 `EPSG:3857 -> EPSG:9481`
  contract。”
- “缺少 surveyed GCP 时系统明确输出 `not_verified`。”
- “unsupported 与 abstain 被保留为证据，而不是静默猜测。”

当前禁止：

- “已在多种/任意 CAD 上通过。”
- “APD 已有 surveyed GCP”或“已达到 X 米绝对精度”。
- “PROJ round-trip、OSM 目视叠加或 QGIS 显示证明绝对准确。”
- “LLM 修复了坐标、几何、长度或 GCP。”
- “历史 `validation` 目录名等于当前代码 release 通过。”

## 10. Reproduction checklist

发布/验收负责人应记录以下实际输出，而不是只复制命令：

```powershell
conda activate cad2gis
pip install -e .
cad2gis doctor --deep --strict
python -m pytest tests -q
python -m pytest experiment/py_scripts -q
cad2gis convert "<SOURCE.dwg>" --run-dir "<NEW_RUN_DIR>" --project "<PROJECT_DIR>" --json
cad2gis gcp status --project "<NEW_RUN_DIR>" --json
cad2gis verify "<MATRIX.json>" --json
```

必须同时归档 environment/tool versions、source/config hashes、test result、
run manifest、artifact hashes、QGIS checklist 和 matrix report。对于当前 APD，无
surveyed controls 时，正确结果仍应是 absolute accuracy `not_verified`，而不是为了
得到绿色状态降低门槛。
