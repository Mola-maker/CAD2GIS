# Android 0.7.0 build and partial emulator verification

Historical handoff snapshot. The final APK hash, timeout fix and continued acceptance are recorded in [Android platform acceptance](ANDROID_PLATFORM_ACCEPTANCE_2026-09-11.md); use that record for current delivery status.

Worktree: `E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade`. This record describes a development-signed Android server client, not an APK containing a GIS runtime. Platform work was handed to a separate task before the remaining recovery and authenticated-service scenarios were completed.

## Source changes

- `apps/android/AndroidManifest.xml` uses `@drawable/ic_molamaker` and version `0.7.0` / code `700`.
- `apps/android/res/drawable-nodpi/ic_molamaker.png` is a byte-for-byte copy of `src/cad2gis/desktop/assets/molamaker-icon.png`; both SHA-256 values are `9bf09d975cdad5885a36bd7a1f261f372cf583c20037c76a7367bac5777e3c55`.
- Android documentation explains that semantic extraction and its interactions come from the connected 0.7.0 server; updating the APK alone cannot upgrade an old server. The test command now references the 0.7.0 APK.
- No Java source, APK certificate policy, existing AVD configuration or physical device was changed in this Android task.

## Build

Command: `E:/branch_CAD2GIS/.venv-multipage-final/Scripts/python.exe tools/build_android.py --development-key`.

Artifact: `dist/android/CAD2GIS-0.7.0-android-dev.apk`, **1,725,056 bytes**, SHA-256 **`206e92df156bb71c417b89b94e87d667b902bf0ce8e0483b8bd02f6be06714ed`**. The build ran all 27 Java server-address boundary assertions, APK signature verification (v2 and v3), metadata inspection and zip alignment successfully. JDK 17 and SDK platform 36 / build-tools 36.0.0 came from the existing checksum-pinned toolchain. Target API is 35; minimum API is 26. The compiler reports use of existing deprecated Android APIs; this did not fail compilation.

The package metadata resolves its application icon to `res/drawable-nodpi-v4/ic_molamaker.png`. Full compiler/package evidence is `dist/android/build-report.json`; this build report intentionally does not claim device tests. The development certificate SHA-256 is `1dfa859e4ebf840a8d0b0bfb99344c3463b7fd7406b49b66684b9f449735cfb6`. No release signing key was supplied.

## Isolated emulator

An independent AVD named `CAD2GIS070` was created under `tmp/android-emulator-070/avds/`. The user's Medium Phone configuration was read only as a hardware template; its disk data, SD card and snapshots were not copied or selected. The test uses the already installed read-only Android 36.1 Google Play x86_64 system image, with new writable files in the task directory. The user's configuration and AVD descriptor hashes match before and after; reports are in the evidence directory.

Launch parameters include `-port 5580 -read-only -no-snapshot -no-snapshot-save -no-window -noaudio -no-boot-anim -skip-adb-auth -gpu swiftshader` and the isolated `-datadir`. `-skip-adb-auth` applies only to this disposable emulator's ADB pairing; it does not bypass APK HTTPS validation. A dedicated ADB server uses port **5038**. The initial emulator was stopped when sandbox/host ADB identities differed; the user's default ADB service was not restarted.

The installed package is `org.cad2gis.mobile`, version name `0.7.0`, version code `700`. `adb install -r` returned Success. `am start -W -n org.cad2gis.mobile/.MainActivity` returned `Status: ok`, `LaunchState: COLD`, `TotalTime: 9395 ms`. The emulator reports Android 16 and WebView `com.google.android.webview 134.0.6998.135`.

## Observed results

Evidence directory: `tmp/android-emulator-070/evidence/`.

| Scenario | Result | Evidence |
| --- | --- | --- |
| Install and first launch | Passed on the isolated emulator | `installed-package.txt`, `01-first-launch.png` |
| Avatar application icon | Visually confirmed on the launcher | `06-launcher-avatar.png`; the bottom-right icon shows the molamaker avatar |
| Enter `http://10.0.2.2:8943` | Rejected; dialog stays open and shows the HTTPS requirement | `02-http-rejected.png`, `02-http-rejected.xml` |
| Enter fixture `https://10.0.2.2:8943` with its untrusted certificate | Rejected with `证书验证失败`; fixture login was not exposed | `03-untrusted-certificate.png`, `03-untrusted-certificate.xml` |
| Change from the certificate error to unreachable `https://10.0.2.2:8944` | **Not passed**: expected connection-failure text did not appear within the 25-second helper limit; visible state remained `正在连接 https://10.0.2.2:8944` | Recorded assertion in task; requires distinguishing network delay from WebView/server-switch recovery |
| Return from launcher to app | App is visible, but expected connection dialog did not reappear within the 20-second check | Final `result.xml` shows `CAD2GIS`, `连接 CAD2GIS 服务器`, `服务器`; not sufficient to claim recovery correctness |

All device operations used `apps/android/tests/emulator_ui.py` or its `Emulator` class with explicit serial `emulator-5580` and environment `ADB_SERVER_SOCKET=tcp:localhost:5038`. One initial HTTP assertion expected the native error text in the UI Automator XML; that assertion was invalid because Android renders the error bubble separately. The screenshot explicitly shows the expected HTTPS validation message. The later network/reopen assertions remain unresolved and have not been relabeled as passing.

## Remaining boundary

There is no verified system-trusted HTTPS CAD2GIS service for this emulator. Authenticated login, semantic extraction execution, shared animated buttons, file upload/download, GIS/OSM/SVG rendering and source/GCP correctness were **not** exercised in Android. The local HTTPS fixture is only a certificate rejection test and is not a CAD2GIS server. No test CA was installed in a system or user trust store. No physical device, old-version upgrade compatibility or app-store release signing was verified. The current Android acceptance is partial, with outstanding server-switch/network/reopen behavior to reproduce.

Operational handoff, current process identifiers and continuation commands: `tmp/ANDROID_HANDOFF_20260911.md`. Test services and the isolated emulator were intentionally retained for the successor task; no user application was uninstalled.
