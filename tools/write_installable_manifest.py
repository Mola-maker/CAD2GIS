"""Write the cross-platform delivery index after platform builds finish."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    files = (
        (f"windows/CAD2GIS-{version}-windows-x64-setup.exe", "Windows 10/11 x64"),
        (f"linux/cad2gis_{version}_amd64.deb", "Ubuntu 24.04 x86_64"),
        (f"linux/cad2gis-{version}-linux-x86_64.tar.gz", "Ubuntu 24.04 x86_64 portable"),
        (f"android/CAD2GIS-{version}-android-dev.apk", "Android 8+ with WebView 83+; HTTPS client"),
        (f"plugins/cad2gis-agent-{version}.zip", "Codex-compatible plugin and MCP templates"),
        (f"python/cad2gis-{version}-py3-none-any.whl", "Python package; native dependencies separate"),
    )
    artifacts = []
    for relative, platform in files:
        path = ROOT / "dist" / relative
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        artifacts.append({"path": relative, "platform": platform, "bytes": path.stat().st_size,
                          "sha256": digest})
    result = {"schema": "cad2gis.installable_delivery.v1", "version": version,
              "channel": "local-development-test", "android_execution": "HTTPS server client",
              "private_project_data_included": False, "derived_demo_included": True, "artifacts": artifacts}
    (ROOT / "dist/installable-manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (ROOT / "dist/SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in artifacts), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
