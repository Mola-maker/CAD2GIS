"""Record and verify the exact wheel, installed package and checkout binding."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import cad2gis

    installed = Path(cad2gis.__file__).resolve().parent
    assert "site-packages" in installed.parts
    assert installed.is_relative_to(Path(sys.prefix).resolve())
    records = []
    with zipfile.ZipFile(args.wheel) as archive:
        for name in sorted(archive.namelist()):
            if not name.startswith("cad2gis/") or name.endswith("/"):
                continue
            wheel_hash = hashlib.sha256(archive.read(name)).hexdigest()
            rel = Path(name).relative_to("cad2gis")
            source = args.checkout / "src" / name
            actual = installed / rel
            records.append({
                "path": name, "wheel_sha256": wheel_hash,
                "installed_sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
                "checkout_sha256": hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else None,
            })
    installed_match = all(x["wheel_sha256"] == x["installed_sha256"] for x in records)
    checkout_match = all(x["wheel_sha256"] == x["checkout_sha256"] for x in records)
    result = {
        "schema_version": "cad2gis.onsite_runtime_binding.v1",
        "status": "PASS" if installed_match and checkout_match else "DRIFT",
        "python": sys.executable, "package_path": str(installed),
        "wheel": str(args.wheel.resolve()),
        "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
        "installed_matches_wheel": installed_match,
        "checkout_matches_wheel": checkout_match, "files": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "files"}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
