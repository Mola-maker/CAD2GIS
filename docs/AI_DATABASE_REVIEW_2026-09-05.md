# CAD2GIS 三源分析与架构代码审查

审查日期：2026-09-05。基线：[main提交3d35497](https://github.com/Mola-maker/CAD2GIS/commit/3d3549776a6aa275e4076f1f25d18b1d22cdb164)。

后续实施更新：本文保留实施前发现；对应修复、独立复审和真实仿真结果见 [升级实施结果](ARCHITECTURE_UPGRADE_RESULT_2026-09-05.md)。

## 结论

当前架构已具备不可变源事实、内容寻址证据、确定性Decision Pack执行及原子GeoPackage发布基础。AI的主介入仍是onboarding配置提议和转换后的证据修复；尚未形成独立的“源快照数据库→语义事务→增量编译”闭环。

下一步应优先提升语义解释边界：先把源快照独立出来，再让AI以少量、有界数据库查询定位证据，通过版本化ID级patch修改解释。源几何、原生长度及测量/配准权威继续由确定性数据链维护。

SQLite是本地持久化与检索基础；Redis仅在多worker/实测热点出现后承担辅助队列、缓存和进度传递。具体工作包、责任模块、提交协议与验收条件见[升级计划](AI_DATABASE_ARCHITECTURE_PLAN_2026-09-05.md)，诊断契约见[debug_mcp规范](DEBUG_MCP_SPEC_2026-09-05.md)。

## 三源方法与运行基线

- **GitHub**：connector读取远端main；实际Git fetch/pull得到同一SHA。新建`E:/branch_CAD2GIS/CAD2GIS-main`工作目录；原feature检出及未跟踪文件保留。
- **Exa**：三轮检索加一次批量fetch成功，覆盖SQLite RTree、GeoPackage、MCP；只用官方正文作技术依据。
- **Tavily**：两轮检索成功，聚焦SQL权限/预算和MCP协议，再回查官方固定版本。

以上是三个检索通道；分析另外交叉使用固定源码、定向运行/测试、上游规范。多个通道命中同一页面不算多份独立证据。由独立agent执行架构追踪、代码审查、上游核验；Redis作为后续用户要求纳入同一计划。

本机测试runtime为Python3.12.13，MCP SDK1.29.1。真实stdio协商2025-11-25；main有33工具，越界路径返回错误。已安装连接器返回capabilities v2，而main返回v1；不能据安装工具推断GitHub main已提供semantic compiler。

## 已核实问题与设计缺口

### R1：请求的graph与固定文件名sidecar可能不是同一张图

位置：[agent_mcp.py:784](../src/cad2gis/agent_mcp.py#L784)、[evidence_index.py:42](../src/cad2gis/cad2gis_v3/evidence_index.py#L42)。

`_evidence_index(graph_path)`取同目录固定名称`evidence_index.sqlite3`，校验的是index与manifest记录，没有把调用者请求的graph身份纳入验证。将图B和图A的合法索引放在同一reasoning目录，请求图B能返回图A的节点和graph hash。

独立复现输出：requested graph `e9ee9ccd…`，returned graph `f5351a7e…`，返回`entity:A`。这证明查询上下文错配；不等于已证明后续Decision Pack能绕过源绑定或改写错误DWG。当前优先级P1：在继续扩展AI数据库查询前，绑定请求graph的canonical identity、source与manifest artifact identity。

### R2：存在manifest但绑定字段缺失时被降级成可用索引

位置：[evidence_index.py:281](../src/cad2gis/cad2gis_v3/evidence_index.py#L281)、[agent_mcp.py:790](../src/cad2gis/agent_mcp.py#L790)。

`validate_index_manifest_binding`对缺失记录/部分解析失败返回False；调用方没有使用这个False阻止查询。复现中run_manifest为`{"artifacts":{}}`，validator返回False，MCP仍以`sqlite-index`返回1条记录。当前测试主要覆盖有效绑定和错误digest，未锁住“存在manifest但绑定不完整”的拒绝行为。

当前优先级P1：区分official与explicit standalone。前者缺字段或损坏必须拒绝；后者显式返回unbound并不得用于受信写决策。另须将manifest身份纳入验证缓存失效条件，避免只按index path/size/mtime缓存检查结果。

### R3：onboarding的AI权限比文档描述更宽

位置：[onboarding.py:530](../src/cad2gis/cad2gis_v3/onboarding.py#L530)、[:684](../src/cad2gis/cad2gis_v3/onboarding.py#L684)、[:798](../src/cad2gis/cad2gis_v3/onboarding.py#L798)、[:828](../src/cad2gis/cad2gis_v3/onboarding.py#L828)；文档[LLM_AGENT_ARCHITECTURE.md:25](LLM_AGENT_ARCHITECTURE.md#L25)。

schema和compiler接纳模型提供的标注正则与0.1–100的匹配距离，source_layer也并非该字段上的观测ID枚举。已有文本样本/结构校验不能使模型距离自动变为权威测量。它不直接改变源坐标，却可改变标签匹配及语义归属。

这是已确认的P2权限契约不一致与精度风险；不声称已在真实图纸复现错误标注。应纳入早期工作包处理：来源核验、严格整包错误、将距离/文本模式收束为经过版本化验证的candidate/policy ID；若保留模型规则生成，应明确实验模式、权限、验证与弃权条件。

### R4：分页能力与可执行能力没有覆盖整个AI闭环

位置：[agent_mcp.py:598](../src/cad2gis/agent_mcp.py#L598)、[:701](../src/cad2gis/agent_mcp.py#L701)、[:956](../src/cad2gis/agent_mcp.py#L956)、[:1041](../src/cad2gis/agent_mcp.py#L1041)；[decision_executor.py:40](../src/cad2gis/cad2gis_v3/decision_executor.py#L40)。

普通证据节点真实使用SQL分页，不能说所有分页都是内存切片。但标签候选全量读取source/feature，场景及修复路径仍有全图解析；`select_semantic_class`和`bind_existing_dimension`虽注册却未实现执行器。这是当前能力/性能缺口，不是已经量化的大图性能故障。

计划补keyset、批量取证、字段投影、局部候选和executable状态；先做label绑定切片，再扩展分类/尺寸绑定。性能数字在新基准上验证后才能声称达标。

### R5：审阅库的可选revision检查不应直接复用为AI写协议

位置：[review_server.py:204](../src/cad2gis/review_server.py#L204)。

当前review store在`expected_revision=None`时允许盲写；复现两客户端依次提交，revision为1→2→3，当前值为第二客户端内容。事件仍然保留，源GeoPackage也不受影响。该行为是现有API的可选前置条件，单凭此复现不能判定UI发生了并发丢改bug。

这是未来语义库设计必须规避的模式：AI commit强制expected revision及source hash，同一事务检查，冲突显式返回。Redis锁不改变此要求。

### R6：仓库内插件提示契约自身存在v2/v3不一致

位置：`plugins/cad2gis-agent/skills/convert-cad-to-gis/agents/openai.yaml:4`要求prompt v2；同技能`references/agent-prompt-contract.md:1`标题为v2；但`src/cad2gis/contracts.py:17`与该`SKILL.md:16`要求v3，且技能指示遇漂移停止。

这是独立于“安装包与checkout不同”的P2打包契约问题。应统一生成/验证提示契约，测试真实YAML和reference文件，而不仅断言Python常量等于capabilities返回值。debug身份核验必须覆盖这些宿主实际加载的文件。

## 现有基础应保留

`source_gpkg.py`已有源实体账本、完整性核验、临时文件发布和确定性测试；`warehouse.py`已有写失败不替换旧交付文件的测试。Decision Pack已有未知/数值字段拒绝、源hash冻结、几何/长度/拓扑独立验证及未实现操作隔离。

升级以这些边界为基础，不把已有SQLite索引重写成另一套事实数据库，不把review标注当正式语义状态，也不通过Redis缓存跳过现有`cacheable:false`的stage契约。

## 验证与产物

主测试集8文件 **69 passed / 14.12秒**；补充onboarding与review的2文件 **17 passed / 3.86秒**，总计 **86 passed**。真实stdio probe与doctor通过。复现脚本验证了上述R1/R2查询问题及R5的盲写行为。没有执行真实DWG转换、全量pytest、故障注入或Redis集成测试；没有把单元测试通过解释成跨CAD或绝对位置精度证明。

独立code review结论为REQUEST_CHANGES：2项P1绑定问题必须在扩大AI写入权限前修复；另记录3项P2（onboarding权限、提示契约、可选revision设计）。这是代码升级的准入结论，本次审查与计划任务已完成；并非声称本轮已修复这些问题。

计划经上游资料agent独立复核后，补齐了Redis消息丢失后的非终态job重投、候选run与权威接受指针的原子CAS、取消后的独立状态查询三个执行细节。

证据根目录：`E:/branch_CAD2GIS/validation/architecture-audit-20260905/`。

| 文件 | 内容 |
|---|---|
| `reproduction.md` | Git同步、命令与范围 |
| `pytest-output.txt`、`pytest-results.xml` | 69项测试原始结果 |
| `main-mcp-probe.json`、`mcp-stderr.log` | 真实协议与完整工具schema |
| `installed-mcp-capabilities.json` | 当前连接器能力，不代表main实现 |
| `doctor.json` | 依赖就绪及实际backend路径 |
| `code-review/reproduce_architecture_findings.py`、`reproduction-output.json` | 独立复现脚本及结果 |
| `code-review/architecture-audit-20260905-code-review.md` | 独立代码审查详细结论 |
| `architecture-evidence.md` | 源码/测试架构追踪及补充验证 |
| `official-sources.md` | SQLite、GeoPackage、MCP、Redis官方证据与三路检索记录 |

本轮只添加审查/计划/规范文档与独立诊断产物。未修复上列生产问题，未提交或推送GitHub，未修改原图或接受的转换run。
