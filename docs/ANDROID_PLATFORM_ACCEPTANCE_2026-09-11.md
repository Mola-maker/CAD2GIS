# Android 0.7.0 package and isolated installation evidence

Worktree: `E:/branch_CAD2GIS/CAD2GIS-semantic-upgrade`. Android is a server client with no embedded Python/GIS runtime. Local build, installation, HTTPS policy and recovery acceptance are complete for the development APK below. Authenticated CAD2GIS service acceptance remains blocked by the absence of an existing system-trusted HTTPS endpoint; this is not full end-to-end Android product acceptance. This continuation supersedes the partial status in `ANDROID_UPGRADE_EVIDENCE_2026-09-11.md`, retained as history.

## Final package

- `dist/android/CAD2GIS-0.7.0-android-dev.apk`
- SHA-256: `1b9f550e9f25a06d176218f7ff414a4aab6709fb610f94b62640edff6d2b77b9`
- Size: **1,725,056 bytes**.
- Application: `org.cad2gis.mobile`, version `0.7.0`, versionCode `700`, min API26, target35, compile36.
- Development certificate SHA-256: `1dfa859e4ebf840a8d0b0bfb99344c3463b7fd7406b49b66684b9f449735cfb6`.
- Build report: `dist/android/build-report.json`; device report: `dist/android/emulator-test-report.json`; checksums: `dist/android/SHA256SUMS.txt`.

The final APK supersedes initial APK SHA-256 `206e92df156bb71c417b89b94e87d667b902bf0ce8e0483b8bd02f6be06714ed`. Its missing bounded network-failure feedback was reproduced and fixed; do not use its old hash for delivery. The APK does not contain either the old or frozen desktop wheel. New semantic functionality must come from a server built from the release input selected by `tmp/RELEASE_INPUT_READY.json`; no Android authenticated-service result is inferred from desktop smoke results.

## Source and build changes

The manifest uses the desktop molamaker avatar as `@drawable/ic_molamaker`; icon SHA-256 `9bf09d975cdad5885a36bd7a1f261f372cf583c20037c76a7367bac5777e3c55` matches the retained desktop asset. The continuation changed only `MainActivity.java` and Android documentation, besides ignored test artifacts. No backend source, baseline, original checkout, GIS algorithm or version number was changed.

Real emulator evidence showed `https://10.0.2.2:8944` remained connecting after **104.4 seconds**. `MainActivity` now applies a **30-second navigation deadline**, stops the pending request, hides the failed WebView and progress indicator, and displays the existing connection-failure message. Success/error, server replacement and Activity destruction cancel the pending deadline. Cold start, Home and explicit server navigation use this handling; stale WebView progress callbacks cannot restore a failed progress indicator.

Command: `E:/branch_CAD2GIS/.venv-multipage-final/Scripts/python.exe tools/build_android.py --development-key`. Reused existing checksum-pinned JDK17, SDK36 and build-tools36.0.0; did not download or rebuild the toolchain. All **27** Java address-boundary assertions, APK v2/v3 signature verification, metadata inspection and zip alignment passed. Existing deprecated-API warnings are retained in build output. Ignored development signing material is not part of the delivery.

## Isolated installation

Retained dedicated AVD `CAD2GIS070`, serial **emulator-5580**, Android16/API36.1, WebView `134.0.6998.135`; dedicated ADB port **5038**. The existing Google Play system image and task-owned writable data directory were reused. No emulator, system image or ADB server was duplicated. Before continuation, process identities were verified against the retained emulator/fixture commands. A subsequent optional elevated process-evidence export stalled and was interrupted; it was not treated as completed evidence.

Final APK `adb -P 5038 -s emulator-5580 install -r` returned **Success**. Explicit cold start returned `Status: ok`, `LaunchState: COLD`, `TotalTime: 6303ms`. This was replacement installation at the same versionCode700, with the remembered HTTPS origin retained; it is **not** a previous-version upgrade test. The installed `base.apk` was pulled from the emulator and its SHA-256 matched the final delivery APK exactly.

The user's original `Medium_Phone.avd/config.ini` and `Medium_Phone_API_36.1.ini` hashes were rechecked after acceptance and are unchanged, matching `44ec6ed05ff67ca28a341b38674c0aadb8131dd24781b51fd99d1aee91a0ed66` and `4995dcef6fc5104072df11809570d676d85ed4da1025d59095abb1173b844925`. All device operations were serial-bound. Default ADB5037, user AVD data, host trust stores and physical devices were untouched. No test CA was installed anywhere.

## Device acceptance

Final evidence directory: `tmp/android-emulator-070/evidence-final/`. Earlier failure evidence is preserved in `evidence-continued/`; initial install/icon evidence remains in `evidence/`.

| Scenario | Result and evidence |
| --- | --- |
| Final replacement install and cold start | Passed; `installed-package.txt`, matching pulled `installed-base.apk`, `final-results.json` |
| Molamaker launcher icon | Initial icon visually confirmed in `evidence/06-launcher-avatar.png`; final APK metadata still resolves the same icon resource |
| Cleartext HTTP | Rejected; address dialog retained with HTTPS error bubble, `09-http-rejected.png/.xml` |
| Untrusted HTTPS fixture | Rejected, login hidden, `03-untrusted-certificate.png/.xml` |
| Unreachable server after certificate error | Failure text observed at the **33.3-second UI sample** after connect returned; 30-second app deadline plus UI polling overhead, `04-bounded-unreachable-server.png/.xml` and timing in `final-results.json` |
| Recovery after timeout | Switched to `https://example.com`, valid system-trusted HTTPS page rendered; `05-recover-system-trusted-https.png/.xml` |
| Cross-origin link | Tapping public-page Learn more keeps Example Domain in CAD2GIS; independent foreground-Activity recheck and refusal toast recorded in `10-origin-block-toast.png`, `origin-recheck.json`, `origin-recheck-activity.txt` |
| System Home then launcher | Current page preserved, `06-before-home` and `07-launcher-resume` screenshots/XML |
| Server settings after launcher return | Dialog opens and remembered origin is present, `08-server-dialog-after-resume.png/.xml` |
| Same-version replacement state | HTTPS origin preserved in `01-after-reinstall.png/.xml`; no old-version compatibility claim |
| Installed artifact identity | Pulled installed APK matches final package SHA-256, `final-results.json` |
| Original user AVD configuration | Both hashes unchanged, `original-avd-config-after.json` |

Two earlier QA assumptions were corrected transparently. A launcher resume does not automatically reopen the server dialog; the app resumes the existing page and settings explicitly opens the dialog. One final origin-boundary helper initially looked for the old `mResumedActivity` dump label, while API36 reports `topResumedActivity`; the original false assertion is retained in `final-results.json`, and an independent targeted recheck provides the authoritative result in `origin-recheck.json`. The HTTP error bubble is a native overlay omitted by UIAutomator XML, so its screenshot was inspected.

## Coverage limits

The user explicitly confirmed that no HTTPS CAD2GIS service is deployed. No existing system-trusted HTTPS CAD2GIS endpoint was supplied or found in the handoff. The local fixture has a deliberately untrusted certificate, and the only installed image is Google Play API36.1. Its trust was not modified to force a positive result. Public `example.com` proves valid HTTPS/WebView rendering and navigation rejection, **not** login, CAD2GIS semantic extraction, motion/button behavior, CAD/GIS/OSM/SVG rendering, authenticated uploads, downloads, cookie clearing across authenticated origins, or output-file hashes. Those Android end-to-end cases remain **blocked**.

No genuine previous-version APK was available in the supplied local artifacts and relevant package directories; previous-version upgrade remains unverified. No physical device, minimum-API device, app-store publication or production signature was verified. Android has no local `doctor` or semantic engine; server-side doctor and semantic smoke evidence belongs to the Windows/Linux packages and is reported separately.

Operational status: `tmp/PLATFORM_ANDROID_STATUS.json`. Reproduction scripts: `tmp/android-acceptance-continue.py`, `tmp/android-acceptance-final.py`, `tmp/android-origin-recheck.py`. After acceptance, the dedicated CAD2GIS070 emulator was identity-verified and closed to release host memory. Its evidence and private AVD files are retained; the dedicated ADB5038 and HTTPS fixture were retained. The default ADB and original user AVD were untouched. Cleanup evidence: `tmp/android-emulator-070/evidence/platform-resource-cleanup.txt`.
