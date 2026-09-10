"""Build the dependency-free Android server client with official SDK tools.

Requires Python 3.11+, JDK 17, Android SDK platform 36/build-tools 36.0.0.
No Gradle daemon, JavaScript bridge, or native GIS library is shipped in the APK.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "android"
TOOLCHAIN = ROOT / "tmp" / "android-toolchain"
BOOTSTRAP = (
    (
        "jdk17.zip",
        "https://github.com/adoptium/temurin17-binaries/releases/download/"
        "jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip",
        "e53a79c3c3d86865bd7e787903884331068e71321714ffd44f145785affc7cb0",
        "jdk",
    ),
    (
        "build-tools.zip",
        "https://dl.google.com/android/repository/build-tools_r36_windows.zip",
        "aa1095cb14d83e483818a748a2c06faaeb8e601561b06a356a119a1b2ca280d3",
        "sdk/build-tools",
    ),
    (
        "platform36.zip",
        "https://dl.google.com/android/repository/platform-36_r02.zip",
        "37607369a28c5b640b3a7998868d45898ebcb777565a0e85f9acf36f29631d2e",
        "sdk/platforms",
    ),
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bootstrap() -> None:
    if os.name != "nt":
        raise SystemExit("Bootstrap downloads target Windows; supply --sdk and --java-home on Linux.")
    TOOLCHAIN.mkdir(parents=True, exist_ok=True)
    for name, url, digest, destination in BOOTSTRAP:
        archive = TOOLCHAIN / name
        if not archive.exists():
            partial = archive.with_suffix(".part")
            print(f"Downloading {name} from its official publisher...", flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            partial.replace(archive)
        if sha256(archive) != digest:
            raise SystemExit(f"Checksum mismatch: {archive}; remove the invalid download and retry.")
        target = TOOLCHAIN / destination
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                resolved = (target / member.filename).resolve()
                if not resolved.is_relative_to(target.resolve()):
                    raise SystemExit("Unsafe path in SDK archive")
            bundle.extractall(target)
    extracted = TOOLCHAIN / "sdk/build-tools/android-16"
    tools_dir = TOOLCHAIN / "sdk/build-tools/36.0.0"
    if extracted.exists() and not tools_dir.exists():
        extracted.rename(tools_dir)


def run(command: list[str | Path], *, env: dict[str, str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        [str(part) for part in command], env=env, cwd=cwd, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)
    result.check_returncode()
    return result.stdout


def build(args: argparse.Namespace) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    major, minor, patch = (int(part) for part in version.split("."))
    version_code = major * 10000 + minor * 100 + patch
    java_home = args.java_home
    if java_home is None:
        homes = list((TOOLCHAIN / "jdk").glob("jdk-*"))
        java_home = homes[0] if homes else Path(os.environ.get("JAVA_HOME", ""))
    java_home = java_home.resolve()
    suffix = ".exe" if os.name == "nt" else ""
    java = java_home / "bin" / f"java{suffix}"
    sdk = args.sdk.resolve()
    build_tools = sdk / "build-tools" / "36.0.0"
    android = sdk / "platforms" / "android-36" / "android.jar"
    for required in (java, android, build_tools / f"aapt2{suffix}"):
        if not required.is_file():
            raise SystemExit(f"Missing build prerequisite: {required}")
    env = dict(os.environ, JAVA_HOME=str(java_home))
    env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temp_parent = ROOT / "tmp" / "android-builds"
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=temp_parent) as temporary:
        work = Path(temporary)
        manifest = ET.parse(APP / "AndroidManifest.xml")
        android_ns = "http://schemas.android.com/apk/res/android"
        ET.register_namespace("android", android_ns)
        manifest.getroot().set(f"{{{android_ns}}}versionName", version)
        manifest.getroot().set(f"{{{android_ns}}}versionCode", str(version_code))
        build_manifest = work / "AndroidManifest.xml"
        manifest.write(build_manifest, encoding="utf-8", xml_declaration=True)
        test_classes = work / "policy-tests"
        test_classes.mkdir()
        run([
            java_home / "bin" / f"javac{suffix}", "-J-Duser.language=en", "-encoding", "UTF-8", "--release", "8",
            "-d", test_classes, APP / "src/org/cad2gis/mobile/ServerAddress.java",
            APP / "tests/ServerAddressTest.java",
        ], env=env)
        policy_test = run([java, "-cp", test_classes, "ServerAddressTest"], env=env)
        compiled = work / "resources.zip"
        run([build_tools / f"aapt2{suffix}", "compile", "--dir", APP / "res", "-o", compiled], env=env)
        unsigned = work / "unsigned.apk"
        generated = work / "generated"
        generated.mkdir()
        run([
            build_tools / f"aapt2{suffix}", "link", "-o", unsigned,
            "--manifest", build_manifest, "-I", android,
            "--java", generated, "--auto-add-overlay", compiled,
        ], env=env)
        classes = work / "classes"
        classes.mkdir()
        sources = sorted((APP / "src").rglob("*.java")) + sorted(generated.rglob("*.java"))
        run([
            java_home / "bin" / f"javac{suffix}", "-J-Duser.language=en", "-encoding", "UTF-8",
            "--release", "8", "-classpath", android,
            "-d", classes, *sources,
        ], env=env)
        jar = work / "classes.jar"
        run([java_home / "bin" / f"jar{suffix}", "cf", jar, "-C", classes, "."], env=env)
        dex = work / "dex"
        dex.mkdir()
        run([
            java, "-cp", build_tools / "lib/d8.jar", "com.android.tools.r8.D8",
            "--release", "--min-api", "26", "--lib", android, "--output", dex, jar,
        ], env=env)
        with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(dex.glob("*.dex")):
                bundle.write(path, path.name)
        aligned = work / "aligned.apk"
        run([build_tools / f"zipalign{suffix}", "-f", "-p", "4", unsigned, aligned], env=env)

        keystore = args.keystore
        if args.development_key:
            if keystore is not None:
                raise SystemExit("Do not combine --development-key and --keystore.")
            keystore = TOOLCHAIN / "signing" / "development.jks"
            keystore.parent.mkdir(parents=True, exist_ok=True)
            env["CAD2GIS_ANDROID_KEY_PASSWORD"] = "android"
            if not keystore.exists():
                run([
                    java_home / "bin" / f"keytool{suffix}", "-J-Duser.language=en", "-genkeypair", "-noprompt",
                    "-keystore", keystore, "-storepass:env", "CAD2GIS_ANDROID_KEY_PASSWORD",
                    "-keypass:env", "CAD2GIS_ANDROID_KEY_PASSWORD", "-alias", args.key_alias,
                    "-keyalg", "RSA", "-keysize", "3072", "-validity", "3650",
                    "-dname", "CN=CAD2GIS Development,OU=Testing,O=CAD2GIS,C=CN",
                ], env=env)
        if keystore is None or not keystore.is_file():
            raise SystemExit("Supply --keystore and CAD2GIS_ANDROID_KEY_PASSWORD, or --development-key for a test APK.")
        if not env.get("CAD2GIS_ANDROID_KEY_PASSWORD"):
            raise SystemExit("Set CAD2GIS_ANDROID_KEY_PASSWORD without putting the signing password on the command line.")
        name = f"CAD2GIS-{version}-android" + ("-dev" if args.development_key else "") + ".apk"
        artifact = output / name
        apksigner = [java, "-jar", build_tools / "lib/apksigner.jar"]
        run([
            *apksigner, "sign", "--ks", keystore.resolve(), "--ks-key-alias", args.key_alias,
            "--ks-pass", "env:CAD2GIS_ANDROID_KEY_PASSWORD", "--key-pass", "env:CAD2GIS_ANDROID_KEY_PASSWORD",
            "--out", artifact, aligned,
        ], env=env)
        signature = run([*apksigner, "verify", "--verbose", "--print-certs", artifact], env=env)
        badging = run([build_tools / f"aapt{suffix}", "dump", "badging", artifact], env=env)
        alignment = run([build_tools / f"zipalign{suffix}", "-c", "-p", "4", artifact], env=env)
        report = {
            "artifact": artifact.name, "sha256": sha256(artifact), "bytes": artifact.stat().st_size,
            "application_id": "org.cad2gis.mobile", "version": version, "version_code": version_code, "min_android_api": 26,
            "target_android_api": 35, "build_tools": "36.0.0", "compile_android_api": 36,
            "development_signature": bool(args.development_key), "signature_verification": signature,
            "server_address_tests": policy_test,
            "package_metadata": badging, "alignment_verification": alignment,
            "device_test": "Not asserted by build; install and exercise the APK separately.",
        }
        (output / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / "SHA256SUMS.txt").write_text(f"{report['sha256']}  {artifact.name}\n", encoding="utf-8")
        (output / "README.md").write_text(
            (APP / "README.md").read_text(encoding="utf-8").replace("(tests/README.md)", "(TESTING.md)"),
            encoding="utf-8",
        )
        shutil.copy2(APP / "tests/README.md", output / "TESTING.md")
        print(f"Built {artifact} ({report['bytes']:,} bytes)", flush=True)
        return artifact


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="Download checksum-pinned official Windows build dependencies into tmp/")
    parser.add_argument("--sdk", type=Path, default=TOOLCHAIN / "sdk")
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "android")
    parser.add_argument("--development-key", action="store_true")
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--key-alias", default="cad2gis")
    args = parser.parse_args()
    if args.bootstrap:
        bootstrap()
    build(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
