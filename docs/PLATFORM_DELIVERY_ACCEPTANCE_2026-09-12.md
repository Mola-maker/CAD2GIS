# CAD2GIS 0.7.0 平台交付与本机安装验收

Windows 安装包、Linux DEB / 便携包和 Android 开发 APK 均已构建。Windows 与 Linux 完整安装业务验收通过；Android 的 10 项本机安装与恢复检查通过。用户明确确认尚未部署 HTTPS CAD2GIS 服务，Android 登录、上传、语义提取及下载完整链路未验证，不列为通过。

## 最终文件

仅以下文件属于本次平台交付。旧 wheel 与旧安装器不在交付清单中。

| 文件 | 字节 | SHA256 |
| --- | ---: | --- |
| [Windows x64 安装器（未签名）](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/windows/CAD2GIS-0.7.0-windows-x64-setup.exe>) | 168,607,203 | `19f71755128305c86dd61f993b6989ffce6206d86abfde2ea7403924f0658679` |
| [Ubuntu 24.04 x64 DEB](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/semantic-upgrade/linux/cad2gis_0.7.0_amd64.deb>) | 344,884,110 | `736ee60dec5127d23ddb06de2781b917f97d701bbc26582d1690766000df9758` |
| [Linux x64 便携包](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/semantic-upgrade/linux/cad2gis-0.7.0-linux-x86_64.tar.gz>) | 344,888,579 | `38c6adc0aed8172c49bad72b973f5ef384377e679651eb79e1ea1a94a82e31c6` |
| [Android 开发签名 APK](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/android/CAD2GIS-0.7.0-android-dev.apk>) | 1,725,056 | `1b9f550e9f25a06d176218f7ff414a4aab6709fb610f94b62640edff6d2b77b9` |

冻结 wheel：[cad2gis-0.7.0-py3-none-any.whl](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/semantic-motion-final/cad2gis-0.7.0-py3-none-any.whl>)，SHA256 `e5d99d655a3e1824857d7dc583c0a8dd667e0d0b76abb9e6837c8db4c79128fb`。Windows/Linux 清单和已安装的应用文件均绑定此 wheel；Python 与既有依赖复用。Android APK 是独立的服务客户端，不包含后端 wheel。[机器可读交付清单](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/PLATFORM_DELIVERY_0.7.0.json>)、[校验文件](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/PLATFORM_SHA256SUMS_0.7.0>)。

## 安装与业务证据

- Windows：真实静默安装到含中文和空格的隔离目录，核验 14,767 个文件；deep / strict / full doctor、原生 DWG 往返、附属 ATTRIB 文本及 SVG、50 个 MCP 工具与目录边界、真实转换、语义三阶段和四类下载哈希全部通过。实际 EXE 启动与一次性登录令牌验证通过。最终卸载成功，用户工程保留，用户 PATH 精确恢复，卸载注册移除。[安装报告](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/windows-installed-final/installation-report.json>)、[doctor](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/windows-installed-final/User projects/native/doctor.json>)、[语义业务报告](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/windows-installed-final/User projects/application/application-report.json>)。
- Linux：在独立 Ubuntu 24.04 容器中真实安装 DEB；该容器原先没有系统 Python、编译器或 CAD2GIS。使用安装版自身 Python 完成同等 doctor、DWG / ATTRIB / SVG、MCP、转换与语义业务验收。将 TAR 迁移到另一目录后再次通过。两次各核验 12,775 个清单文件、179 个冻结 wheel 应用文件及 Redis 配置。[DEB 安装](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/linux-installed-verification/verification.json>)、[DEB 文件完整性](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/linux-installed-verification/integrity.json>)、[便携包验收](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/linux-portable-verification/verification.json>)、[便携包完整性](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/linux-portable-verification/integrity.json>)。
- Android：专用 API 36.1 模拟器安装、同版本覆盖保留服务地址、冷启动、HTTP / 跨域 / 不可信 TLS 阻止、网络超时可见反馈及恢复等 10 项检查通过；安装的 base.apk 与交付 APK 哈希一致，v2 / v3 签名和 zipalign 通过。使用开发签名；未测试旧版本跨版本升级、实体设备或 API 26。它是服务客户端，没有本地 GIS doctor 或离线语义引擎。专用模拟器完成取证后已关闭以释放内存，默认 ADB 和用户原有 AVD 配置未修改。[Android 证据](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/dist/android/emulator-test-report.json>)、[Android 验收说明](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/docs/ANDROID_PLATFORM_ACCEPTANCE_2026-09-11.md>)。

## 动效与安装后的真实界面

已安装 Windows 程序的界面资源与冻结 wheel 字节一致。真实浏览器验证了 1280px 桌面和 375px 手机宽度；动态任务出现后 82 / 82 个按钮均接入人物 IP 动效，可见按钮均不少于 44px，没有横向溢出。帮助 Esc 关闭、工具转项目面板、项目关闭均完成；背景 atelier-drift / grid / orbit 与页面切换样式生效。实际 doctor 任务经历 working / disabled，再进入 success / enabled，界面显示 ready，无浏览器控制台错误。

源码负责人针对同一最终 JS / CSS 另外完成 12 项弹窗生命周期回归，包含正常动效、减少动态效果和设置切换；本机安装浏览器未再次切换系统减少动态效果设置。系统浏览器自动打开未验证；真实界面使用本地服务与受控浏览器检查。[安装界面记录](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/windows-installed-final/installed-ui-review.json>)、[实际服务资源哈希](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/windows-installed-final/User projects/desktop-launcher/launcher-report.json>)、[源码最终界面证据](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/output/playwright/motion-final/result.json>)、[弹窗回归证据](<E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/popup-motion-regression-unrestricted.jsonl>)。

## 原生文本修补

LibreDWG 固定为 0.14，只修补 cquote 实际输出游标的 NUL 终止。原始源码、补丁、许可、编译回执和不含图纸文本的验收回执随原生运行时提供。旧二进制保留在临时历史目录，没有覆盖 baseline。

Windows / Linux 对 Darat、Manado 各执行至少 5 轮独立进程，交替顺序；每轮全部 49 + 330 条 MTEXT 与固定 dwgread JSON 文本一致，完整 JSON 对象及 DXF 实体记录各自稳定，原图哈希不变。两平台完整 JSON 对象相同；原始 DXF 实体记录仅颜色整数有有符号 / 无符号表示差异，其 32 位值相同，其余实体记录标签相同。[Windows 重复验证](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/native-patched-validation-windows/native-mtext-report.json>)、[Linux 重复验证](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/native-patched-validation-linux/native-mtext-report.json>)、[跨平台比较](<E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade/tmp/native-patched-cross-platform-explained.json>)。

doctor 对新可执行文件的内置哈希名单判断仍为 not_evaluated，这是源码中保守诊断的预期行为；该精确构建的修补证明来自独立编译与重复读取回执，不能把 doctor ready 单独解释为文本保真认证。本文不声称绝对地理精度，也不替代原会话负责的真实图纸业务回归与数据审阅。

## 范围与协调

Windows 安装器未签名，Android 为开发签名；Linux 验证范围为 Ubuntu 24.04 x86_64，glibc 2.39 或更新版本。没有远程部署、公开发布或 Git 推送。源码与插件归档由原会话负责。平台代码与说明已冻结，详细状态通过 tmp/PLATFORM_DELIVERY_STATUS.json 和 tmp/PACKAGING_FEEDBACK.md 交接。
