# Notepad
<!-- Auto-managed by OMC. Manual edits preserved in MANUAL section. -->

## Priority Context
<!-- ALWAYS loaded. Keep under 500 chars. Critical discoveries only. -->
2026-08-15铁律:禁自杀式重启宿主harness进程;需重启先起新实例验证再停旧,否则交给用户。
2026-07-20 deep-dive完成: 定位=生产转化优先(竞赛为契机); 终局=newmodel=生产线与开发主场, main归档前知识转移; 转移范围=领域知识+可移植算法(LibreDWG链埋掉); 交付已push origin/newmodel: main_archive/MAIN_BRANCH_SYNTHESIS.md(8659a43+ff41501), wiki架构页已更新; BOITE真值=43带标签(值16)+2不带标签(值48/72位于两SITE,程序局限需人工干预)=物理45; main基线CABLE=203/CONV-SUM=6942(43129f3); ftth_converter回归(3e5be1a)不再修复。后续开发移步newmodel分支。

## Working Memory
<!-- Session notes. Auto-pruned after 7 days. -->

## MANUAL
<!-- User content. Never auto-pruned. -->

### 2026-08-15 事故教训：禁止自杀式重启宿主服务

- **规则**：在本会话及后续会话中，禁止终止、重启或停止承载当前会话运行的任何进程、进程组或服务实例（包括 agent 自己所在的 dsh/claude 等 harness 服务进程）。
- **事故背景**：执行“启用 Tavily 插件 → 重启 dsh web”时，先 `kill -TERM -<PGID>` 杀掉了承载当前会话的宿主实例，导致回合中断、任务挂起，配置变更只完成一半。
- **正确做法**：
  1. 先确认插件/配置已写入持久层（package.json、settings.yaml、credentials 等），再判断是否需要重启。多数插件变更在服务下次启动时自动生效，不主动重启。
  2. 若确认必须重启：先启动新实例（独立进程组，如 `setsid nohup`），验证新端口可用后，再停旧实例。禁止在自身所在实例中“先杀后启”。
  3. 执行任何 `kill` 前，先 `ps -o pid,ppid,pgid,sid,cmd` 核对自己当前会话的进程归属；若目标包含自身 PGID，立即中止。
  4. 不确定时，只完成配置变更并写出重启方法，把重启操作留给用户手动执行。

