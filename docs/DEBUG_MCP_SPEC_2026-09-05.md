# CAD2GIS debug_mcp 规范

日期：2026-09-05；基于 main `3d3549776a6aa275e4076f1f25d18b1d22cdb164`。状态：下述规范保留设计要求；0.4.0 工作分支已实现只读 `debug_mcp` / `debug-mcp`、身份/schema/实际协议核验、结构化 trace、错误码和语义 job 取消恢复。实施和验收范围见 [升级实施结果](ARCHITECTURE_UPGRADE_RESULT_2026-09-05.md)。本文“本次main实测”及第7节原始记录为实施前基线。

目标：使每次MCP调用能确定“运行的是哪份代码、读的哪份证据、尝试改什么、耗时在哪里、失败是否留下了提交”，同时保留源事实及完整审计记录。

## 1. 身份先于功能

启动诊断至少返回：Python可执行路径、包文件路径、package/plugin/skill版本、源码commit或构建指纹、backend实际路径、MCP SDK版本、协商protocol version、transport、工具名称与完整input/output schema的digest、已配置project roots、reader/GDAL/PROJ版本与可用性。

`package_version`相等不足以证明相同实现；现有`mcp_tool_contract`只hash工具名，应增加独立schema digest，避免同名不同参数被忽略。工具能力需区分 `registered`、`executable`、`quarantined` 及所需validators。

宿主加载的skill、agents/openai.yaml、prompt reference也必须参与版本校验：当前仓库YAML要求v2，reference标题为v2但正文要求v3，runtime/skill同样要求v3。只比较Python常量和自身返回值会漏掉这种打包漂移。

本次main实测：Python3.12.13、SDK1.29.1、协议2025-11-25、33工具、capabilities v1。当前连接器却返回capabilities v2及semantic compiler信息。发现这种漂移时，诊断显示`VERSION_DRIFT`，先给出实际路径和能力差异，不自动切换到另一后端。

实现拟入口：`cad2gis debug-mcp --json`（诊断编排）及可选只读MCP工具`debug_mcp`（有限scope）。scope只允许`identity/runtime/artifacts/query`；不接受任意Python、shell或SQL字符串。现有`get_capabilities/get_runtime_status/inspect_run/audit_run`继续复用，不增加第二套转换实现。

## 2. 协议与输出通道

- stdio的stdout只输出JSON-RPC帧；Python、GDAL、reader、依赖库调试信息进入stderr或项目诊断文件。
- 日志逐条结构化JSONL；摘要与详细artifact分离，超预算使用分页/引用，不把整个图、SQL结果或模型响应塞回工具文本。
- 按**实际协商协议**测试。此次运行版本是2025-11-25；升级新MCP规范须单独核验SDK/宿主兼容性，不能因官方网站latest变化直接改运行协议。
- 参数/协议错误与业务执行错误分开；已知业务失败使用稳定code与工具错误结果，不能返回看似成功的空数组。
- 运行时安装、转换、语义commit属于显式有副作用调用，debug默认只读，不隐式安装依赖或触发转换。

## 3. 每次调用的观测事件

建议schema：`cad2gis.mcp_trace.v1`。每个工具至少产生started与一个terminal事件；terminal只能是succeeded/failed/cancelled之一，崩溃后的未终结调用由恢复审计标记interrupted。

```json
{
  "schema_version": "cad2gis.mcp_trace.v1",
  "trace_id": "opaque-id",
  "request_id": "rpc-id",
  "tool_name": "query_source_entities",
  "phase": "succeeded",
  "identity_digest": "sha256",
  "source_sha256": "sha256",
  "snapshot_id": "snapshot-id",
  "index_sha256": "sha256",
  "semantic_revision": 7,
  "query_template_id": "entities_by_layer_v1",
  "query_backend": "sqlite-index",
  "rows_returned": 50,
  "response_bytes": 14000,
  "duration_ms": 82,
  "db_wait_ms": 2,
  "committed": false,
  "error_code": null,
  "detail_artifact": "diagnostics/trace-id.json"
}
```

示例数值仅定义字段，不代表性能测量。时间用单调时钟算duration，UTC用于事件排序；数据库查询、hash验证、JSON编码、provider、reader、几何计算分别计时，不能把各阶段之和冒充完整wall time。

写调用额外记录：idempotency key hash、base/current revision、patch digest、before/after语义hash、源事实before/after hash、affected count、事务结果、job/run ID。trace不得存完整Authorization、API key、cookie、连接口令、全量env或未脱敏provider原始错误。

默认模型prompt/response只留hash与长度；需要保存详细内容时使用受控本地artifact及明确保留策略。源图文字、绝对路径也是潜在敏感业务内容，普通日志使用source ID/相对artifact路径；身份诊断的本地路径只面向当前授权用户。

## 4. 数据库与缓存诊断

| 检查 | 规范结果 |
|---|---|
| graph/index/manifest绑定 | 官方run缺字段、无法解析、source/graph/hash不匹配即`ARTIFACT_BINDING_INVALID`；不可退成standalone成功 |
| standalone索引 | 必须显式请求模式，返回`unbound`；不能用于有副作用的受信决策 |
| 校验缓存 | key纳入manifest与snapshot身份，失效可观测；索引未变但manifest变不能沿用已验证结果 |
| 只读查询 | 记录template、filters摘要、耗时、页深与rows；常规请求不运行完整integrity_check |
| 深度检查 | 只读执行integrity/foreign-key/hash核验，带deadline；明确其成本和校验范围 |
| 热点查询 | 在诊断请求中保存EXPLAIN QUERY PLAN与索引信息；生产正常调用不返回SQL内部细节 |
| 可变语义库 | 记录busy timeout、锁等待、事务耗时、revision conflict、WAL/checkpoint状态 |
| Redis（未来可选） | readiness、cache hit/miss、queue lag、pending/retry、ACK、lease generation；`not_configured`不是错误 |

Redis恢复后缓存必须可丢弃，任务结果须向持久化job账本核对。禁止在debug中执行FLUSHALL、清空源库、重置revision或解除其他worker锁。不得通过关闭校验、放宽容差或换reader来“解决”诊断失败。

## 5. 失败、超时、取消与幂等

稳定错误码最少覆盖：`VERSION_DRIFT`、`PATH_OUTSIDE_ROOT`、`ARTIFACT_BINDING_INVALID`、`SOURCE_MISMATCH`、`STALE_REVISION`、`UNKNOWN_ID`、`UNSUPPORTED_OPERATION`、`QUERY_BUDGET_EXCEEDED`、`DATABASE_BUSY`、`VALIDATION_FAILED`、`PROVIDER_UNAVAILABLE`、`CANCELLED`、`PUBLICATION_INCOMPLETE`。

错误返回包含retryable、committed、trace_id、恢复动作与详情引用。未知执行状态不能随意标记`committed=false`；须返回unknown并允许按幂等键查最终状态。

查询有时间/行数/字节上限；取消通过progress handler中断SQLite查询。长转换使用job而非在stdio请求里无限阻塞；job检查取消信号并有独立stage状态。客户端请求结束不等于后台worker已经停止。

语义提交发生在短事务，提交前取消则rollback；提交后取消保留已提交revision及审计历史。若原RPC已被取消，不要求继续向该RPC发送响应；客户端通过新的operation/job/idempotency状态查询获得最终提交结果。重试必须复用同一幂等键。Redis重复投递或lease过期的旧worker在数据库提交结果时重新核验job generation并CAS结果指针，不可覆盖较新发布结果。

## 6. 验收场景

1. 启动checkout及安装包，比较身份、schema与33工具基线；故意换旧包，应清楚报告差异。
2. stdio完成initialize/list_tools/read call；把reader/原生库日志注入stderr，确认stdout仍只含有效协议帧。
3. project root之外路径、未知ID、无效分页、损坏manifest、错源index、过期revision均返回明确错误且无修改。
4. 两个客户端读同revision并提交，恰好一个成功；同幂等键重复提交返回同一revision/event。
5. 10万实体有界查询不加载全图；超时/取消后下一次调用仍可用，connection和锁已释放。
6. provider失败、数据库busy、writer半途异常及发布前后崩溃分别注入；已有run文件hash不变，重启可恢复唯一终态。
7. 模型提交coordinates/native_length/CRS/raw SQL等非法写字段应整包拒绝；源geometry/curve/length hash保持不变。
8. 日志中放入假API key和DSN，自动扫描确认脱敏；详细内容仅在受控artifact中出现。
9. Redis未配置不影响本地模式；缓存淘汰、Stream重复/待处理消息、Redis重启、租约过期不丢失已提交语义或重复发布。特别覆盖outbox已发送后Redis清空：从非终态job账本重新投递；旧worker产物不得绕过generation CAS被接受。
10. 图文“看起来一致”只能作为辅助证据；最终精度报告仍须独立列source/geometry/topology/length/coordinate各维度。

## 7. 当前已执行与尚未执行

本轮已执行main stdio真实子进程probe：协议2025-11-25、工具33、越界路径拒绝；主定向集69项、onboarding/review补集17项通过，合计86项。probe记录initialize结果、工具完整schema与digest，文件位于审计证据目录的`main-mcp-probe.json`，stderr单独保存。doctor的ready为依赖可用性证据。

上述新增debug接口、完整trace、取消/崩溃注入、语义CAS和Redis场景均是后续验收要求，不能作为当前已实现或已通过的能力报告。

## 8. 0.4.0 实施更新

已实现身份完整schema digest、两份真实plugin manifest/prompt指纹及内部一致性校验、初始化session实际协议、四种只读scope、稳定业务错误、结构化started/terminal、源查询取消及语义job持久化恢复。成功写receipt的 `committed=true` 表示持久化操作状态已确认，不表示候选已被接受为正式交付；写RPC失败/取消结果不明时仍为unknown，并可按幂等键查询。宿主实际加载的skill不由服务器假定，使用客户端bundle digest比较。

实际stdio验收为46工具、SDK1.29.1、协议2025-11-25。MCP有界查询预算8–64KiB包括text/structured结果及协议余量；内部Python查询预算2–64KiB按紧凑UTF-8 JSON计算，二者不可混称。`debug_mcp(scope=query)`发现source index尚未构建时只报告状态，不隐式创建索引。

尚未实施的规范项：逐阶段db_wait/checkpoint/EXPLAIN诊断、legacy canonical conversion的持久化job改造、实例级semantic revision到正式delivery adapter，以及Redis adapter和相关故障演练。普通trace不记录完整CAD文本或provider原始异常；完整受控明细与原生stderr位于调用者选定的本地证据目录。
