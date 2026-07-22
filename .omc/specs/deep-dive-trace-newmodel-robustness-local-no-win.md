# Deep Dive Trace: newmodel-robustness-local-no-win

## Observed Result

问题：评估 newmodel 分支（生产线），若要在本机本环境（WSL2 Ubuntu 24.04，无 Windows/AutoCAD）完成"鲁棒性提升"，不安装 win 端附件时能否达到？

三条调查线路：(1) 读取层依赖；(2) 下游链路可移植性；(3) 鲁棒性目标分解。**本 trace 含实证探针结果**（在 worktree `/tmp/newmodel-trace` 上实际运行测试套件）。

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | 下游链路（ingest 之后全部）本机可跑：纯 Python+OSS，测试实证 51/51+281/282 通过 | **High（实证）** | Strong（实际运行） | 不是推测——测试套件已在 WSL2 venv 实际执行并通过 |
| 2 | 鲁棒性工作约 85-95% 可本机完成；硬边界仅"真实 DWG ingest"相关三类 | High | Strong | 三线路收敛 + 依赖判定表 + 唯一失败测试=Windows 守卫（设计行为） |
| 3 | 读取层理论可替换（CAD2GIS_BACKEND_PATH），但分支内无现成 Linux reader 也无含坐标的 SourceEntity 缓存 | High | Strong | ingest 接口纯对象边界 vs autocad_reader `os.name!="nt"` 硬守卫；review bundle `coordinate_payloads_visible=false` |

## Evidence Summary by Hypothesis

- **H1（下游可跑，实证）**：venv(--system-site-packages)+pip 装 ezdxf 1.4.4/pytest 9.1.1 后：`tests/` 顶层合同套件 **51/51 通过**（0.51s）；`experiment/py_scripts` 全套 **281 通过 / 1 失败**（6.22s）；`test_geopackage_reproducibility_v3` **2/2 通过**（GDAL 3.8.4 下字节可复现链验证）。下游模块（semantics/topology/georef/evidence/warehouse/styles/calibration/units）零 Windows 调用。本机已有：Python 3.12.3、GDAL 3.8.4、pyproj 3.6.1、shapely 2.1.2；缺 ezdxf/pytest（venv 已补）。11 个 run bundles（含 gcp_ready/accuracy_v4-v7/gcp_architecture_probe）提供开发素材。
- **H2（工作分解）**：鲁棒性工作项依赖判定——**(a) 纯本机可做**：GCP adapter/状态机、verify 矩阵 schema 与评估器、CadCurve facts 算法、几何血缘、拓扑门控规则（含 legend/title 排除）、样式验收（ByLayer 解算/QML）、LLM curate bundle/校验器、doctor/UX/错误边界、测试扩充、main 转移资产落地（图层正则/验证规则 CSV/图例检测思路/匈牙利标注/三轨样式/跨度注记）。**(b) 本机可做但需缓存/模拟输入**：APD 下游回归（用 run bundles 的 GPKG）。**(c) 本质需 Windows/AutoCAD**：新 DWG inspect/bootstrap/convert、跨 CAD 矩阵第二行（真实新样本 ingest）、真实 GCP 校准后端到端重跑。**(d) 需外部资源**：surveyed GCP、新 DWG 样本、LLM API key。
- **H3（读取层）**：`ingest.py:14` 接口=纯对象边界（source+SourceProfile→list[SourceEntity]+diagnostics）；但 `autocad_reader.py:32` 硬编码 `C:/Program Files/Autodesk/AutoCAD 2027/accoreconsole.exe`、`:3576-3577` `os.name!="nt"→RuntimeError("Direct AutoCAD DWG reading requires Windows")`、`pipeline.py:945` 无条件调 ingest、`project_profile.py:110-113` inventory 绑定真实 DWG 字节哈希。`readcad_review_bundle.json`（33MB，9,391 objects）经探针查验=**元数据缓存**（facts 含 type/layer/handle/native_length，但 `coordinate_payloads_visible=false` 无坐标）→ APD 从 ingest 阶段的完整重放**不可**本机进行。

## Evidence Against / Missing Evidence

- **H1**：唯一失败测试 `test_reader_protocol_strict_v3.py::test_flat_inventory_preserves_compatibility_diagnostics` 触发 Windows 守卫——是设计行为非回归。GDAL 3.8.4 vs env.yml 3.10、pyproj 3.6.1 vs 3.7.2、PROJ 9.4 vs 9.8.1 的版本差在可复现性测试已过，但 verify matrix 的名义 CRS 检查可能有 sub-mm 敏感性（未实证）。
- **H2**：占比估计（85-95%）基于工作项计数而非工作量加权；GCP 真实校准、跨 CAD 第二样本恰是"鲁棒性"中含金量最高的部分，它们被划到 (c)/(d)。
- **H3**：仓库外的 CI 产物/外部存储是否存有含坐标的完整 SourceEntity dump 未知；Linux 替代 reader（LibreDWG/ODA/预提取 TSV 提交入库）是否被项目哲学接受未知（LibreDWG 已被判"埋掉"，但那是针对 canonical 生产路径）。

## Per-Lane Critical Unknowns

- **Lane 1（读取层）**：是否允许为 Linux 开发引入替代读取后端（dev-only reader 或把含坐标的提取记录提交入库）？这决定 APD 端到端重放能否本机化。
- **Lane 2（下游）**：GDAL/PROJ 版本差（本机 3.8.4/9.4 vs env.yml 3.10/9.8.1）在 verify matrix 名义 CRS 检查上是否可接受，还是必须 conda 严格对齐 env.yml？
- **Lane 3（目标）**："鲁棒性提升"的范围与优先级——本机做哪些工作项、以什么为完成判据？win 端工作（真实 ingest 三类）与组员的界面如何划分？

## Lane 3 Misplacement / SoT Ownership Scope

| Source | Candidate destination | ownership_scope | Boundary relationship | Default? | Warning |
|--------|-----------------------|-----------------|-----------------------|----------|---------|
| N/A — 本 trace 为可行性评估，无 MOVE 候选 | — | — | — | — | — |

## Rebuttal Round

- **Best rebuttal to leader**："测试通过 ≠ 鲁棒性提升可完成"——测试套件通过只证明现有代码可运行，鲁棒性提升的核心（GCP 真实精度、跨 CAD 泛化）恰恰都在 (c)/(d) 类，本机做不了的部分才是'鲁棒性'的含金量。
- **Why leader held**：反驳部分成立但被吸收为限定：history.md 的建议架构 7 阶段中 (1)CadCurve(2)血缘(3)验收QML(4)源线/派生网络分离(5)校准门控框架 全部可本机实现与测试；只有 (6) 的"真实控制点采集"与跨 CAD 第二样本的 ingest 需要外部。且 README 已知缺口中 UX/doctor/错误处理/测试扩充整块可本机完成。结论修正为"**框架与验证体系 ~90% 本机可成；证据完备性（真实GCP/第二样本）本质外部**"。

## Convergence / Separation Notes

三线路无冲突，互为基础：L1 定硬边界（ingest=唯一 win 依赖点，接口形态清晰）；L2 实证边界内全部可跑（281/282）；L3 在边界内分解出具体工作清单。合流为单一结论。

## Most Likely Explanation

**能达到，且有明确定界。** newmodel 的鲁棒性提升在本机 WSL2（不装任何 win 端附件）可完成约 **85-95%**——验证器/门控/样式/血缘/GCP 框架/curate/UX/测试扩充及 main 转移资产落地全部可做，实证测试 51/51+281/282 通过（唯一失败=Windows 守卫设计行为）。**硬边界仅一个：真实 DWG ingest**（`autocad_reader.py` 的 accoreconsole/COM + `os.name!="nt"` 守卫），它阻塞三类工作：新 DWG onboarding、跨 CAD 矩阵第二行、真实 GCP 校准后的端到端重跑——这三类本质属于 Windows 侧组员。APD 单图纸从 ingest 的完整重放也不可本机（无含坐标的 SourceEntity 缓存），但 11 个 run bundles 的 GPKG 产物支撑全部下游回归。

## Critical Unknown

**Linux 开发侧是否允许引入替代读取路径**（dev-only reader 后端 / 提交含坐标的提取记录入库 / 接受 LibreDWG 作为非 canonical dev reader）——它决定"APD 端到端重放本机化"这一中间地带（从 85-95% 到 ~99%）能否解锁。这是项目哲学决策，非技术问题。

## Recommended Discriminating Probe

已在 trace 中执行两个实证探针（测试套件、review bundle 查验）。剩余探针=向用户提出 Lane 1 关键未知（是否允许 Linux 替代读取路径），以及用 conda 严格对齐 env.yml 后重跑 verify matrix 样本（验证 GDAL/PROJ 版本敏感性）——后者可在执行阶段进行。
