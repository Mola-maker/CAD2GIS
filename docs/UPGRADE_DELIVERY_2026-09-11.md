# CAD2GIS 0.7.0 升级与验收记录

本次工作从远端 `main` 的 `1d4086213f8bb882d8ab6cddd64cb1cdf434a6e9` 开始；已执行 `git pull --ff-only` 并核对 HEAD 与 upstream。实现位于 `codex/semantic-svg-upgrade-20260911` 工作树，原工作目录及其未提交文件保留。本文记录构建与验收；Git 提交和分支推送另以最终交付回执为准，不代表公开发布安装包。

用户提供的 Markdown 是历史问题和方案资料；其中的命令、过往完成声明和建议不作为当前验收证据。用户确认 Git 中 `baselines/` 就是人工校验的标准工程。本次按源 DWG SHA256 绑定这些配置，不改写 baseline、不以新结果替换标准。

## 并行设计小组落地的功能

| 方向 | 本次实现 | 验证入口 |
| --- | --- | --- |
| SVG 完整性 | 原生几何、明确 GROUP 组合、块属性及布局来源；分类型覆盖率与缺失审阅 CSV | [SVG 证据](SVG_UPGRADE_EVIDENCE_2026-09-11.md) |
| OSM 匹配 | 显式 ROAD 优先，保留 CABLE 物理偏移，逐路线残差与源选择证据 | [OSM 证据](OSM_UPGRADE_EVIDENCE_2026-09-11.md) |
| 统一语义提取 | 桌面、服务器界面和 Android 共用的源导出、候选准备、验证三阶段；MCP 插件同一后端 | [交互和语义提取](UI_SEMANTIC_UPGRADE_2026-09-11.md) |
| 头像交互 | 原软件头像衍生按下、处理、成功、失败、取消等状态；键盘、触摸和减弱动画偏好 | [图像来源](IMAGE_ASSETS_2026-09-11.md) |
| 数据库可靠性 | Redis 有界源查询摘要缓存、超时熔断、SQLite 回退、可观测诊断 | [Redis 配置及证据](REDIS_RUNTIME_2026-09-11.md) |
| 交付完整性 | 源码和插件归档包含本次未提交实现；逐文件 SHA256 与确定性构建校验 | [源码交付](SOURCE_DELIVERY_2026-09-11.md) |

“提取语义”生成来源绑定的事实与关系候选，完成后才能下载或交给 AI 审阅；按钮不自动发送 AI 请求，也不自动提交语义修订。插件的预览、提交、幂等和修订编译沿用同一来源校验。

Redis 实现的是既有数据库计划中的可重建缓存。源事实、语义事务和持久任务状态仍由 SQLite 与源清单负责；安装包包含 Redis 客户端，服务端通过附带 Compose 配置选用。Redis 未配置不阻止本地转换。

## 标准工程与精度边界

Git baseline 包含配置、冻结 OSM 数据和 QGIS 工程；Taipa、Tinggede 还包含 `matching-manual-gcp.json` 人工修正记录。记录自身的权威字段为 `relative_osm`，`absolute_accuracy_verified=false`，本次保留原值。Tinggar 的 `osm_anchor.json` 是粗定位搜索提示，不能单独作为最终精度证据；其 `source_profile.json` 另有原 DWG GEODATA 配准信息，两者不能混为一谈。

工程的人工验收地位按用户说明保留。数值报告区分标准工程对比、原始几何守恒、OSM 相对匹配和独立绝对精度证明。算法的合成回归中约 4.98 米偏差被消除，不能外推为每张真实图纸的测量精度。

Tinggar 真实 DWG 的专项 SVG 提取已完成：沿用 baseline 的明确符号配置，对已有交付点身份进行核对，FAT/BOITE 31、FDT/SITE 2、PTECH 109 均生成候选图形。435 个 IMB 身份没有自动补画轮廓。5 个替代几何映射与 33 个文字覆盖分别留有来源记录。P011 的圆圈在原图中缺失，baseline 借用了另一源符号的呈现；它仍标为人工替代，不标为恢复原图。

该专项使用的点身份数据库来自本地旧版 Tinggar baseline 交付，验证的是源身份与 SVG 呈现链路；新的九图全流程回归独立记录。专项证据目录为 `E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/tinggar-svg`。

九图回归已汇总：152,621 条源实体，8 张完成转换，共 3,289 个交付要素；完成运行的独立审计均无未解释几何变化和意外字段失配。所有 9 个原件及 64 个 Git baseline 文件哈希不变。仍有非块 IMB 和嵌套符号需要人工审阅，完成状态均为 `CONDITIONAL`。Manado 仍保留清单绑定阻断，Darat 使用精确绑定的封存源事实重放；详见 [真实图纸报告](CORPUS_UPGRADE_2026-09-11.md)。

Taipa/Tinggede 使用 Git 冻结路网重新匹配，与原人工位移向量分别相差 2.359 米、1.196 米。这是同源、同坐标基础上的软件回归差值；自动支撑来自电缆路线代理，不能当作独立测量误差。

清单差异调查进一步确认 LibreDWG 0.14 DXF 写出器的 MTEXT 结束符缺陷：同一二进制、同一 DWG 会在完整正文后追加不同堆残留。最小 C 复现和修复后的八个边界用例已通过；Windows/Linux 完整原生重建及 Darat/Manado 各五次原文对照均通过。此发现解释文本长度为何不稳定，不能用更长文本表示更完整，也不能自动改写原批准清单哈希。详见 [原生文本诊断](NATIVE_MTEXT_DIAGNOSIS_2026-09-11.md)。补丁后新读取的 Darat/Manado Model 计数符合 baseline，完整清单仍与旧批准不同，均保留绑定阻断；见 [新清单复核](CORPUS_NATIVE_RECHECK_2026-09-12.md)。

## 安装与验收方法

Windows 安装包内置 Python、LibreDWG 和锁定依赖；在带空格及中文路径的隔离目录安装，运行 doctor、原生 DWG/SVG、MCP、转换、语义提取和 GCP 重匹配验收后卸载，检查用户工程及 PATH 保留。

Linux 在本机 WSL/Podman 的 Ubuntu 24.04 环境构建，并在独立干净容器安装 deb 后验证；另提供可迁移的 tar.gz。这个结果不能替代其他 Linux 发行版逐项兼容性测试。

Android 提供开发签名 APK，在隔离模拟器安装验收。Android 是连接 CAD2GIS 服务的客户端；语义提取及 GIS 运算由 Windows/Linux 后端执行。开发签名不等同于应用商店发布签名。

完整源码以当前工作树文件构建归档，记录基础提交和实际文件哈希。安装包中的 Python wheel 另做两次隔离构建一致性校验。最终包路径、哈希、运行结果及环境限制在交付清单中记录，不能用单元测试通过代替安装验收。

## 核心验收结果

- Lamteh INSERT/ATTRIB 修复、全按钮动效及原生读取器诊断合并后，全套 Python 测试 **1316 通过、12 跳过、0 失败、0 错误**。报告：`E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/full-pytest-native-motion.xml`。
- 跳过项涵盖显式启用的复杂 DWG/AutoCAD 属性样本、当前账户的 Windows 符号链接权限、可选 Hutabohu 外部证据、POSIX 文件模式及未指定临时 Redis 服务。Redis 真实服务测试在独立实验中执行，见其专项证据文档。三条警告来自依赖的弃用提示。
- 最终核心只读复审无剩余 P1/P2；审查范围和证据限制见 [审查报告](FINAL_CORE_REVIEW_2026-09-11.md)。缺失 SHX 字体仍需按符号诊断进行视觉复核。
- 包含最终动效和诊断的 wheel 已双次构建一致，179 个包内源码文件逐字节匹配工作树。文件为 `dist/semantic-motion-final/cad2gis-0.7.0-py3-none-any.whl`，13,256,461 字节，SHA256 `e5d99d655a3e1824857d7dc583c0a8dd667e0d0b76abb9e6837c8db4c79128fb`。Windows/Linux 已采用通过真实图纸复测的补丁构建，并完成独立安装验收。
- 三类弹窗在普通、减弱动效和过渡中切换偏好的 12 项浏览器生命周期检查均通过；1440 与 375 像素界面的 78 个按钮全部绑定动效，无水平溢出，错误反馈和键盘关闭通过。这些使用本地接口夹具，真实转换仍由独立安装验收负责。见 [动效证据](MOTION_UPGRADE_2026-09-11.md)。
- Codex 插件和客户端模板已显式传递 Redis 三个可选环境变量名；未在配置中保存服务凭据。插件合同测试 **10 通过**。个人插件已正式重装至 `0.7.0+codex.20260912070859`，18 文件源/缓存一致；独立已安装环境的 full strict doctor 为 `ready`，50 个 MCP 工具握手与工程范围拒绝检查通过。新会话加载更新后的插件，详见 [本机插件升级](PLUGIN_LOCAL_UPGRADE_2026-09-12.md)。

用户随后要求平台软件包升级独立会话执行，已创建并启动 **“验收 CAD2GIS 全平台升级包”**。原会话继续负责源码、图纸和算法回归；三端构建及安装验收由独立会话接管。完整交接文件在工作树 `tmp/PLATFORM_HANDOFF_20260911.md`，平台状态汇总在 `tmp/PLATFORM_DELIVERY_STATUS.json`。

## 最终平台验收

Windows 安装、14,767 个清单文件核验、真实业务、实际界面动效与卸载保留检查通过；动态任务产生后的 82 个按钮均绑定人物动效，1280/375 像素界面没有横向溢出。Linux DEB 安装和迁移后的便携包各通过业务验收及 12,775 个清单文件核验。Android 开发 APK 的 10 项本机安装、边界和恢复检查通过；用户确认未部署 HTTPS 服务，故不声明 Android 服务端完整业务链路通过。三端包、哈希和详细边界见 [平台交付验收](PLATFORM_DELIVERY_ACCEPTANCE_2026-09-12.md)。
