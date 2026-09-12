# Android 验证

`tools/build_android.py` 每次构建都会运行 `ServerAddressTest.java` 的 27 个地址边界断言，并调用 Android 官方工具验证 APK 签名、包信息和 zip 对齐。它不会把这些检查当成设备测试。

`emulator_ui.py` 只接受 `emulator-<端口>` 序列号，所有 adb 操作均绑定该模拟器，拒绝实体设备序列号。需要已启动的 Android 8.0+ 模拟器及 Chromium 83+ WebView。可以通过 Android Studio Device Manager 创建隔离 AVD，或使用 SDK 的 `avdmanager` 创建；首次安装前检查 `adb devices -l`，确认目标确为本次测试的模拟器。

以下命令以已经创建的 `CAD2GIS_API30` AVD 为例。后台启动时在 Windows 使用 `Start-Process -WindowStyle Hidden -PassThru` 保存返回的进程 ID，不另开用户可见窗口。

```text
emulator -avd CAD2GIS_API30 -port 5580 -no-window -noaudio -no-snapshot -gpu swiftshader_indirect
adb -s emulator-5580 install -r dist/android/CAD2GIS-0.7.0-android-dev.apk
adb -s emulator-5580 shell am start -n org.cad2gis.mobile/.MainActivity
python apps/android/tests/emulator_ui.py --adb /path/to/adb --serial emulator-5580 --evidence tmp/android-evidence --connect https://cad.example.com
adb -s emulator-5580 emu kill
```

实际服务应部署有效系统受信任证书，登录 `/_app/`，打开一个已转换 DWG 的 run，分别检查 CAD 源几何、GIS 与 OSM 叠加、SVG 对应报告及人工校正入口。真实转换、预览与二次匹配的计算验收由服务端测试负责；手机端需要独立确认界面和交互。

`https_fixture.py` 是仅绑定主机回环地址的独立测试服务，需要 Python 的 `cryptography`。它生成短期测试 CA/密钥、提供登录、带身份验证的下载及上传记录。它不是可部署的 CAD2GIS 服务；口令固定为 `fixture-password`。所有生成材料必须留在忽略的 `tmp/` 下。

```text
python apps/android/tests/https_fixture.py --directory tmp/android-fixture --port 8943
python apps/android/tests/emulator_ui.py --adb /path/to/adb --serial emulator-5580 --evidence tmp/android-evidence --connect https://10.0.2.2:8943 --fixture-login
```

默认情况下 APK 会拒绝 fixture 自签名证书，这正是预期的负面测试。要测试登录和文件传输，只能在专门的、可丢弃的 AOSP 测试系统镜像中安装该测试 CA；测试镜像可用 `-writable-system` 启动并使用 Android 官方 adb root/remount 流程。不要修改 APK 的系统证书策略，不要改宿主机或真实设备的证书信任。系统镜像版本可能要求先完成其验证启动设置，不能把重启失败当成 APK 问题。模拟器里的 `10.0.2.2` 指向主机回环地址，普通手机应使用真实服务器域名。

应独立记录：有效证书登录、下载内容 SHA-256、跨源导航拒绝、切换端口后重新登录、无效证书拒绝以及失败时旧页面已清空。不要仅凭控件出现就声明上传成功；需要服务器收到正确文件。测试产物中的 `emulator-test-report.json` 应绑定实际安装的 APK SHA-256，并明确实体设备及文件选择器未完成的覆盖范围。

恢复验收还需覆盖：不可达服务器在 30 秒加载截止时间后显示连接失败，进度条消失；再切换到系统受信任的 HTTPS 页面能够恢复。Home 后通过 launcher 返回应恢复原页面，不应假设地址对话框自动弹出；点击“服务器”后应出现已记住的服务地址。公共 HTTPS 页面只证明证书与导航能力，不能代替真实 CAD2GIS 登录、语义提取或文件传输验收。相同 versionCode 的 `adb install -r` 只能证明替换安装及状态保留，不能代替旧版本升级。
