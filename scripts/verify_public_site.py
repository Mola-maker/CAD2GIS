"""Read-only checks for independently deployed public sites and existing effects."""

from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


def _check(url: str, expected: str | None = None) -> dict:
    try:
        if urlsplit(url).scheme == "file":
            raw = Path(urlsplit(url).path).read_bytes()
            final = url
            status = 200
        else:
            with urlopen(
                Request(url, headers={"User-Agent": "CAD2GIS-site-verifier/0.8.0"}),
                timeout=25,
            ) as response:
                status = response.status
                final = response.url
                raw = response.read(2_000_000 if expected else 512)
        passed = status == 200 and (expected is None or expected.encode() in raw)
        return {
            "url": url,
            "final_url": final,
            "http_status": status,
            "status": "passed" if passed else "failed_check",
            "expected_content": expected,
        }
    except Exception as exc:
        return {"url": url, "status": "failed_check", "error": str(exc)}


def verify_public_site(base_url: str, *, gallery_url: str) -> dict:
    base_url = base_url.rstrip("/") + "/"
    targets = [
        (base_url, "从图纸到证据：CAD2GIS"),
        (urljoin(base_url, "wiki/index.html"), "cad_bundle_execute"),
        (urljoin(base_url, "wiki/en.html"), "reviewable GIS"),
        (urljoin(base_url, "robots.txt"), "sitemap.xml"),
        (urljoin(base_url, "sitemap.xml"), "https://cad2gis.molamaker.cn/"),
        (gallery_url, "CAD2GIS"),
    ]
    targets.extend(
        (urljoin(base_url, f"docs/assets/img/first-open/drawing-{n:02}.jpg"), None)
        for n in range(1, 10)
    )
    with ThreadPoolExecutor(max_workers=5) as executor:
        checks = list(executor.map(lambda item: _check(*item), targets))
    return {
        "base_url": base_url,
        "gallery_url": gallery_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed"
        if all(c["status"] == "passed" for c in checks)
        else "failed_check",
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument(
        "--gallery-url",
        default="https://mola-maker.github.io/CAD2GIS/docs/osm-gcp.html",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_public_site(args.base_url, gallery_url=args.gallery_url)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload)
    print(payload)
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
