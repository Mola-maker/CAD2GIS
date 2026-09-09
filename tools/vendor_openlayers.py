"""Vendor the exact OpenLayers build used by the review UI, including its license."""
from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = "https://registry.npmjs.org/ol/-/ol-10.6.1.tgz"
INTEGRITY = "xp174YOwPeLj7c7/8TCIEHQ4d41tgTDDhdv6SqNdySsql5/MaFJEJkjlsYcvOPt7xA6vrum/QG4UdJ0iCGT1cg=="
COMPATIBILITY_PATCHES = {
    b"e[3]??=1": b"e[3]??(e[3]=1)",
    b"Object.hasOwn(r,e)": b"Object.prototype.hasOwnProperty.call(r,e)",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "tmp/packaging-web/ol-10.6.1.tgz")
    args = parser.parse_args()
    archive = args.archive
    if not archive.is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL, archive)
    if base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode() != INTEGRITY:
        raise ValueError("OpenLayers package integrity mismatch")
    destination = ROOT / "src/cad2gis/webdemo/assets/vendor"
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as package:
        for source, name in (("dist/ol.js", "ol.js"), ("ol.css", "ol.css"), ("LICENSE.md", "OPENLAYERS-LICENSE.md")):
            content = package.extractfile("package/" + source)
            if content is None:
                raise ValueError(f"Missing package member: {source}")
            data = content.read()
            if name == "ol.js":
                # Two equivalent rewrites keep the fixed upstream build usable
                # in Android System WebView 83. Fail closed on upstream drift.
                for original, replacement in COMPATIBILITY_PATCHES.items():
                    if data.count(original) != 1:
                        raise ValueError(f"Unexpected OpenLayers compatibility patch count: {original!r}")
                    data = data.replace(original, replacement)
            if name == "OPENLAYERS-LICENSE.md":
                data += ("\n\n## CAD2GIS distribution changes\n\n"
                         "OpenLayers 10.6.1 is taken from the integrity-pinned npm archive. "
                         "For Android System WebView 83, tools/vendor_openlayers.py deterministically "
                         "rewrites one nullish assignment into nullish coalescing with assignment, "
                         "and one Object.hasOwn call into Object.prototype.hasOwnProperty.call. "
                         "The original license above remains unchanged.\n").encode("utf-8")
            (destination / name).write_bytes(data)
    print(destination)


if __name__ == "__main__":
    main()
