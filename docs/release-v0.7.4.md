# CAD2GIS 0.7.4 — Windows 后台进程回收

启动器退出后仍可能留下 Python 后台进程，持续占用安装目录中的 `runtime/DLLs/libcrypto-3-x64.dll`，导致关闭窗口后覆盖安装仍被阻止。本版本修复桌面启动器的进程归属与退出清理。

Windows 桌面启动器现在在创建 Python 之前加入独立的匿名 Job Object，启用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。句柄仅由启动器持有，到启动器进程退出时由操作系统关闭，使其 Python 后台及后代进程一同退出。正常关闭继续返回 Python 的退出码，普通 CLI 启动方式保持原有行为。[Microsoft Job Object 文档](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)

安装器继续在替换文件前检查占用，不覆盖仍在使用的运行环境。旧版本已经产生的孤立后台进程需要先结束，再安装本版本。

## 本地验证

- 已复现并定位旧启动器退出、Python 孤立存活、DLL 被占用的状态；清理确认无任务的残留进程后，0.7.3 覆盖安装成功。
- 新启动器正常启动、单次登录、前端资源校验、正常关闭及零退出码通过。
- 仅强制结束新启动器时，真实 Python 服务退出，`libcrypto` 可重新独占打开。
- 启动阶段子进程与孙进程回收重复验证 3 次，运行中的无关 Python 进程保留。
- 在普通外层 Job 中运行时，嵌套进程归属验证通过。

延续 0.7.3 的聊天布局、真实路径绑定和转换结果入口修复。本版本使用本地 Windows 构建，不运行 GitHub Actions。
