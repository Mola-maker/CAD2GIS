# Agent 与 CI 升级记录

核查日期：2026-09-09。执行框架继续使用项目锁定的 Pydantic AI 2.42.0；CAD 读取、坐标计算、语义审核和交付仍由 canonical pipeline 执行。

## 已复现并修复的问题

| 问题 | 原先行为 | 现在的行为 |
| --- | --- | --- |
| MCP `isError=true` | 当作普通成功的 function return 返回 `{is_error: true}`，未消耗 SDK 的 tool retry budget | 转换为 `ModelRetry`，模型得到纠错反馈；每个工具最多允许两次修正，持续失败终止本轮 |
| 只有 `structuredContent` 的响应 | 只拼接 text block，得到空字符串 | 成功响应读取 structured payload，包含空对象 `{}`；错误响应保留错误 text 与 structured details |
| 不确定写入被原样重复 | 失败后模型可以直接重发相同 mutation | 在本轮保存规范化参数的 fingerprint；未确定是否提交的写入禁止原样重发，并提示检查已有产物或 operation status |
| 失败调用的执行预算 | SDK `tool_calls_limit` 只计成功调用 | 所有进入工具函数的本地与 MCP 调用共用 150 次 attempt budget，错误和被拒绝的尝试也计数 |
| 异常 / 取消后的 usage | 只在成功返回时保存 usage | 成功、失败、取消都保存 requests、tokens、成功 tool calls 与 tool attempts |
| MCP trace 误报成功 | handler 直接返回 `CallToolResult(isError=true)` 时仍记录 `succeeded` | terminal trace 记录 `failed` 与稳定错误码 |
| 缺文件的恢复提示 | `VALIDATION_FAILED` 加一条泛化的 mutation 提示 | `ARTIFACT_NOT_FOUND`，并按错误码提供不含原始 provider body 的恢复建议 |
| Docker 版本漂移 | build / smoke / inspect / export 均写死 `0.4.0` | 从 `pyproject.toml` 读取并验证 Docker tag，全链使用同一版本 |
| package 与 plugin 发布版本不一致 | package 升至 `0.6.0` 后，plugin contract 和两个 manifest 仍为 `0.5.0`，触发真实 `VERSION_DRIFT` | 三处发布版本同步为 `0.6.0`；skill / prompt schema 版本、权限和授权要求保持原样 |

`agent_tool_bridge.py` 集中处理 MCP 返回、错误、路径 admission 和 attempt budget。它不会自行重试 RPC；`ModelRetry` 只把失败交给模型纠正，SDK 负责 tool retry 与 request limit。失败响应不再计为工具成功，工程验证是否通过仍以 canonical artifact gate 为准。

## 错误与取消边界

- 错误码、trace ID、`committed` 和恢复说明进入工具反馈；原始 provider exception 不进入 server trace。Desktop 继续遮蔽当前 API key。
- `committed="unknown"` 表示服务可能已经产生写入。相同工具和规范化参数在本轮不能再次提交；明确返回 `committed=false` 时才可重试原参数。
- transport error 和 timeout 不自动变成重试；它们终止本轮并记录未确认结果。Cancellation 保持 `CancelledError` 的传播，已有产物继续保留。
- mutation guard 是本轮内的防重复措施，不是持久化 exactly-once 保证。跨轮恢复仍须读取 `inspect_run` / `inspect_semantic_store`、现有产物和 durable job receipts。
- 取消不能保证已经开始的 synchronous worker 尚未落盘。界面记录会明确要求检查结果，不把取消等同于回滚。
- 大结果保持 60,000 字符上限并增加明确的 `truncated` 标志。文件和工具结果始终处在数据通道，不被追加为更高优先级指令。

`MUTATING_TOOLS` 在 `mcp_diagnostics.py` 统一维护，包含会持久化 context 的 `prepare_iteration_context`。未来增加写工具时，应同步加入这一集合并覆盖不确定结果的 regression。

## CI gate

`.github/workflows/ci.yml` 现在对所有 branch push、PR 和手动 dispatch 生效。权限仍为 `contents: read`。

1. 独立的 `precision-agent` job 运行离线 synthetic OSM、geometry、GCP、runtime archive 与 Agent protocol regression。Agent 使用 FunctionModel、本地 HTTP fixture 和真实 stdio MCP，不需要模型账号或公开 OSM 服务。
2. 原完整平台矩阵继续保留：Ubuntu Python 3.11 / 3.12，Windows Python 3.12，macOS Python 3.12。安装 portable reader、运行 strict doctor、lint、compile、全部 regression，并构建 distribution / WebDemo。
3. 两类 job 均运行 `pip check`。JavaScript syntax gate 现在包含 desktop GCP/Agent 所在的 `desktop/app.js`。
4. JUnit 文件按平台 / Python / commit 分开保存；失败时仍尝试保留报告。

离线 gate 不使用私有 `1234` DWG 样本。真实 DWG 转换和精度验证属于交付证据，不能以 synthetic 测试通过替代。workflow 已写入源码；本记录不声称远端 GitHub Actions 或 Docker build 已执行。

## 本地验证

执行环境：Linux、Python 3.12、项目声明的 agent/test/portable dependencies。

```bash
python -m pytest -q \
  tests/test_agent_tool_bridge.py \
  tests/test_mcp_diagnostics.py \
  tests/test_desktop_agent.py \
  tests/test_mcp_stdio.py
```

结果：**32 passed**。覆盖真实 MCP 错误反馈、连续失败停止、structured-only result、错误状态优先、未知写入防重复、失败 attempt budget、取消传播、credential 遮蔽、截断提示和本地 OpenAI-compatible HTTP / SSE 往返。修改的 Python 文件通过 Ruff。

整合发布版本后，`tests/test_database_mcp.py`、`tests/test_plugin_contracts.py`、`tests/test_mcp_diagnostics.py` 再次执行，**22 passed**；真实 stdio 的 version identity、semantic CAS round trip 和 8 KiB 协议预算均通过。这两组 contract / database 文件也纳入独立 CI gate。

HTTP fixture 会临时移除 proxy 环境变量，保证 loopback 测试不依赖宿主机 proxy 或可选 SOCKS 支持；生产环境的 proxy 配置没有被禁用。

## 官方规范与代码借鉴

- [MCP Tools / Error Handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling)：区分 protocol error 与 `isError` tool execution error，将可操作的错误反馈交给模型。
- [Pydantic AI Tool Retries](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/#tool-retries) 与 [Usage Limits](https://pydantic.dev/docs/ai/core-concepts/agent/#usage-limits)：分别限制模型请求、工具纠错和成功调用；本项目补充失败 attempt 计数。
- [Pydantic AI 的 MCP 实现](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/mcp.py)：参考显式 tool error policy，并按本项目实际安装版本核对 API。
- [OpenAI Agents SDK MCP result 处理](https://github.com/openai/openai-agents-python/blob/main/src/agents/mcp/util.py) 与 [Agent loop](https://openai.github.io/openai-agents-python/running_agents/#the-agent-loop)：参考错误内容优先级、structured result 与 bounded model loop，没有增加第二套执行框架。
- [GitHub Actions workflow syntax](https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions)：branch trigger、job 权限与并发边界。

后续最有价值的工作是把所有写工具的 operation receipt 统一为可查询的持久化协议，扩展已有 SQLite CAS / compile job 机制，让跨轮恢复能够判断“已提交、未提交、仍在运行”，再决定是否重试。
