# CAD2GIS Android

Android 8.0（API 26）及更新版本的服务器客户端，同时需要系统 WebView 的 Chromium 版本至少为 83。客户端会检查版本；组件缺失或过旧时，会提示先更新 Android System WebView / Chrome。Android 版本符合要求并不代表预装 WebView 也符合要求。安装 APK，点“服务器”，输入例如 `https://cad.example.com` 的服务根地址。登录后使用服务器上的转换、OSM 配准、SVG 提取及 GCP 预览/二次匹配功能。Android 不包含 GIS 引擎；服务需已经部署并提供 `/_app/` 和登录页，计算与项目文件保留在服务器。上传使用系统文件选择器，下载时选择保存位置。

只接受有效证书的 HTTPS 服务。支持公网域名和可路由的内网 HTTPS 服务；自签名证书、明文 HTTP、网址中的账号密码及跨站导航均不支持。Android 客户端只信任系统证书。地图瓦片等 HTTPS 子资源仍按服务器页面请求。客户端记住服务器地址，登录会话由 WebView cookie 管理，不另外保存密码。更换服务会清除此前的 cookie、缓存和网页存储。

页面连接超过 30 秒仍未完成时，客户端会停止加载、隐藏失败页面并提示检查地址或网络。可点击“服务器”重试或切换服务；从桌面返回应用会保留当前页面，点击“服务器”才会打开地址对话框。

`*-dev.apk` 是开发证书签名的可安装验证包，不是应用商店正式发布版。侧载时需按 Android 提示允许所用文件管理器安装应用。生产发布应使用归属项目的长期签名密钥；签名变化时 Android 不允许覆盖升级此前安装的版本。

0.7.0 使用与桌面端一致的 molamaker 头像作为启动图标。连接 0.7.0 服务后，在图纸设置中选择「语义提取」，指定源 DWG 和新的结果目录，可生成来源绑定的源事实及分类、标签、尺寸候选。按钮反馈、执行阶段、取消状态和产物下载由同一服务界面提供；候选不会自动提交分类。旧服务器不会因为安装新版 APK 而获得这些服务端功能。

## 构建

无 Gradle、npm 或第三方 Android 运行库。使用官方 Android SDK 的 `aapt2 → javac → D8 → zipalign → apksigner` 编译链。APK 支持 Android 的所有 CPU 架构，没有平台本机库。

Windows，在项目根目录使用 Python 3.11+：

```powershell
python tools/build_android.py --bootstrap --development-key
```

依赖固定 SHA-256，下载、构建过程和开发密钥放在忽略的 `tmp/` 内。可先通过 Android SDK Manager 安装 `platforms;android-36` 和 `build-tools;36.0.0`，然后在 Windows 或 Linux 指定 JDK 17 和 SDK 路径，跳过下载：

```text
python tools/build_android.py --sdk /path/to/android-sdk --java-home /path/to/jdk-17 --development-key
```

正式签名使用 `--keystore /private/path/release.jks --key-alias cad2gis`，并通过环境变量 `CAD2GIS_ANDROID_KEY_PASSWORD` 提供口令。不要提交私钥。输出为 `dist/android/` 中的 APK、SHA256SUMS.txt 和构建验证报告。SDK/JDK 原始许可证保留在下载的构建工具中，这些工具不随 APK 分发。

## 验证

构建脚本必须通过 APK 签名验证、包结构检查和 zip 对齐检查。单独的 Java `ServerAddressTest` 验证 HTTPS、完整源边界及地址注入拒绝。构建报告中的静态验证不代表已在设备上安装；模拟器/实机安装和服务器登录、文件传输仍需独立记录。

重复设备验收的命令与隔离 HTTPS fixture 见 [测试说明](tests/README.md)。设备验收后的 `emulator-test-report.json` 单独记录模拟器版本、APK 指纹、实际服务连接与界面验证结果及覆盖限制；仅重新构建不会自动生成这份设备验收报告。

参考：[Android 官方命令行构建](https://developer.android.com/build/building-cmdline)、[WebView](https://developer.android.com/develop/ui/views/layout/webapps/webview)、[apksigner](https://developer.android.com/tools/apksigner)、[网络安全配置](https://developer.android.com/privacy-and-security/security-config)。
