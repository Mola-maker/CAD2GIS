# 源快照、语义 revision 与几何修复候选

本次改造处理重复 DWG 读取、语义编译与交付断开、自动修复难以单独审查三个边界。SQLite 继续持有事实索引、事务 revision 和任务状态；Redis 不替代这些权威数据。

## 一次提取与复用

```sh
cad2gis export-source /data/drawing.dwg --run-dir /data/source-001 --json
cad2gis index-source /data/source-001 --json
cad2gis convert /data/drawing.dwg --project /data/project \
  --source-run /data/source-001 --run-dir /data/run-001 --json
```

batch 自动传入刚导出的 source。未指定 source-run 的单图调用继续直接读取。快照复用仍执行审定配置、清单、来源依赖、单位、几何和交付门槛；必须保留源 DWG 用于 SHA 绑定。

仅接受完整 native reader 快照。检查源 SHA、五份事实产物摘要、读取器适配代码与实体模型的归一化 SHA、记录身份和完整性，拒绝注入记录、错图、过期代码和篡改。发布前再次校验。旧快照没有 replay 契约，需要重新 export-source，不能手工补字段冒充已验证快照。读取器实际诊断另保留；代码摘要不代表不同读取器输出必然一致。

原始 JSONL 双精度值直接进入同一 ingest；不从展示 GeoJSON 反推事实。run manifest 的 source_replay 和 stage_contracts 记录复用依据。

## 已提交 revision 的交付入口

已有 prepare / candidates / preview / commit / compile 流程不变。compile 成功后使用其 job_id：

```sh
cad2gis convert /data/drawing.dwg --project /data/project \
  --source-run /data/source-001 \
  --semantic-store /data/semantic.sqlite3 --semantic-job PUBLISHED_JOB_ID \
  --run-dir /data/semantic-delivery-001 --json
```

MCP run_conversion 提供同名参数。入口检查 SQLite 中 published 任务、固定历史 revision、来源快照、编译产物 SHA、决策历史与编译 ledger/feature 的一致性；发布前复查收据。后续 head 更新不会切换本次 revision，不能同时指定另一份 decision_pack。不会自动设置 accepted_run_id。

当前支持已有且唯一对应的规范设备选择源文本，并确认兼容类别与已经采用的尺寸实体。文本原样进入 display_label，来源实体、候选 ID、revision 留在 provenance/lineage；语义投影不改变几何。

**新增或替换尺寸绑定尚未接入**，需要分段级审定规则。新增/删除设备、终态排除、冲突类别、非唯一规范对象、缺失文字明确拒绝，整个 revision 先校验再应用。不能将本入口说成任意 ontology 到规范图层的完整编译器。

公开 ZIP 含派生成果和修改收据；后续 AI 修改仍需私有 source 快照、prepare、semantic.sqlite3、编译候选和审定 project。

## 修复只生成候选

```sh
cad2gis convert /data/drawing.dwg --project /data/project \
  --source-run /data/source-001 --geometry-repairs candidate-only \
  --run-dir /data/review-001 --json
```

批量每图可设置 `"geometry_repairs":"candidate-only"`。默认 legacy 保留旧审定行为，本次未将九图历史成果全部切换。candidate-only 暂停 BOITE 点位吸附、CABLE 端点桥接、无效边界 buffer(0) 外环修复，保存源/候选坐标、SHA、候选 ID、位移和损失，applied=false。

无效源边界不作为已修复边界交付。其他派生覆盖面规则仍存在，不能理解为所有成果均是原始 CAD 实体。旧数量/精度门槛若拒绝新模式，不自动放宽配置。

每次尝试在 run 同级唯一 `run-name.repair-review-*/geometry_repair_candidates.json` 留阶段报告，失败也保留。成功 run 另包含 SHA 绑定的候选 JSON；ZIP 自动携带，batch HTML 提供链接。逐候选接受并重执行的通道仍待实现。

## 证据与下一步

D 盘 WSL，Linux / LibreDWG，无 AutoCAD，第八张 Kletek 实图：

- 直接转换 26.95 s，导出 8.25 s，快照转换 16.49 s。263 个要素全部几何和字段相等；转换中禁止调用读取函数，证明未重读。这是单次单图测量。
- preview / commit / compile 后，源标签 MR.KLK5.P018 进入独立候选，全部几何与直接转换一致。属于流程仿真，标签工程含义未验收。
- candidate-only 生成 9 个未应用候选；仍为 CONDITIONAL。
- Windows 最终全套 628 passed / 7 skipped；Linux installed wheel 首轮 115 项通过。实图复跑与 MCP 验证见 release-verification.json：96 个 Python 文件与 wheel 一致，46 个工具，协议 2025-11-25。最后发现并修复了视觉核验器未识别 semantic revision 标签来源链的问题；最终审计未验证标签数与字段不一致数均为 0，见 audit-release-verification.json。

新产物：`E:\branch_CAD2GIS\validation\architecture-next-20260906`。最终 wheel SHA256 为 `c4e57406099ad65dbe6e7edafe16cbfa5a5c3dc488c39f6dbaf3ff2a00be9358`；最后一次审计修改不改变原转换成果，96 个 Python 文件已重新核对安装内容。原报告中的 14 个未直接进入 canonical ledger 的展开实例，均有原定义与父实例留存，详见 source-dispositions.csv，不能误称为 14 个丢失要素。

下一步仍需新增尺寸绑定的分段契约、新建/删除设备的依赖重建、逐候选接受执行、九图 candidate-only 对比。独立实测 GCP、数字注记语义和历史修复损失的工程验收保持开放。
