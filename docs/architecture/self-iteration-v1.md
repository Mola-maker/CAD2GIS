# CAD2GIS 受控自迭代架构 v1

## 结论

现有架构具备自迭代所需的大部分数据面能力：不可变 run、source/evidence hash、
CAD Scene Graph、视觉 region/hit-map、Evidence Graph、受约束 Decision Pack、
source-bound onboarding 和独立 validation gate。此前缺少的是跨多次候选运行的
状态机、用户证据摄取、候选对比、显式接受以及可复用的学习记录。

本版本新增的是控制面闭环，而不是让模型重写插件或源几何：

```text
不满意的 immutable run
  -> iteration session（来源、预算、当前 active run）
  -> 语言证据 + 视觉证据（region hash / 用户图片 hash）
  -> 分类路由到现有受约束工具
  -> 最小配置/决策变更
  -> 新 immutable candidate run
  -> 既有 gate 回归比较 + 用户视觉复核
  -> accept / reject / revise
  -> source-bound suggestions-only learning registry
```

## 借鉴点

DSH Extension Hub 的可取部分是宿主服务与客户端分层、跨 Claude/Codex 发现、
持久状态、临时文件加 rename 的原子写、安装/更新后的自检，以及对只读内置层和
用户可变层的区分。参考实现：
[Relistencode/dsh-extension-hub](https://github.com/Relistencode/dsh-extension-hub)。

Claude Code 插件的可取部分是把能力拆为 Skill、Agent、Hook、MCP，并将安装版本
隔离在缓存目录。当前实现采用 Skill + Agent + MCP：Skill 定义有限循环，Agent
隔离评审职责，MCP 保存真实状态并执行硬校验。没有默认启用 Stop Hook，因为跨
客户端的一致性和防无限循环比强制自动续跑更重要。参考：
[Claude Code Plugins](https://code.claude.com/docs/en/plugins) 与
[Hooks](https://code.claude.com/docs/en/hooks)。

## 状态与权限

`cad2gis.iteration_session.v1` 保存：

- base/active run manifest SHA-256 与 source SHA-256；
- 最大迭代次数和已使用次数；
- 用户语言证据、run visual region 引用、内容寻址的用户图片；
- 候选 run、变更工件 hash、回归与改进；
- accept/reject/revise 决策和显式用户确认；
- accepted learning artifact。

模型可以改进 scene role、语义映射、候选网络关系、标签/样式配置和 reviewed GCP
profile，但只能通过已有受约束接口。模型不能写 source geometry、坐标、WKT、
长度、任意图 ID，也不能自动提升候选。

## 回归判定

候选必须与 active run 的 source SHA-256 相同，使用新的 run 目录，且不能降低
`run_status`、增加 unresolved 数量、改变 source entity count，或让原来通过的
validation gate 失败。通过这些检查只代表“可以交给用户接受”，并不代表自动接受。

## 学习边界

接受后生成 `cad2gis.iteration_learning.v1`，可合并到
`cad2gis.iteration_learning_registry.v1` 并传给 `prepare_ai_onboarding`。注册表默认：

- source-bound；
- suggestions-only；
- 不跨图纸泛化；
- 不自动写入 proposal；
- 不修改插件代码。

需要把重复经验提升成跨项目规则时，应另开开发/审查任务，提供多个图纸上的正反
证据和回归测试，再修改算法或插件版本。
