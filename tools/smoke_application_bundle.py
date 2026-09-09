"""Exercise the installed application and real GCP rematching on synthetic data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run(bundle: Path, output: Path, conversion: Path) -> dict:
    import httpx
    from pyproj import Transformer

    bundle, output, conversion = bundle.resolve(), output.resolve(), conversion.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = conversion / "run"
    original_delivery = original / "delivery.gpkg"
    original_hash = digest(original_delivery)
    suffix = ".exe" if os.name == "nt" else ""
    python = bundle / ("runtime/python.exe" if os.name == "nt" else "runtime/bin/python3")
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "CAD2GIS_BACKEND_PATH"):
        environment.pop(key, None)
    native = bundle / "libredwg" / ("" if os.name == "nt" else "bin")
    environment["CAD2GIS_LIBREDWG_CLI"] = str(native / ("dwg2dxf" + suffix))
    environment["PATH"] = str(bundle / "bin") + os.pathsep + environment.get("PATH", "")
    announcements = queue.Queue()
    with (output / "service.stderr.log").open("w", encoding="utf-8") as errors:
        process = subprocess.Popen([str(python), "-I", "-B", "-X", "utf8", "-m", "cad2gis.application",
            "--no-browser", "--project-root", str(conversion)], cwd=output, env=environment,
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))

        def read_announcements():
            for line in process.stdout:
                if line.startswith("CAD2GIS: "):
                    announcements.put(line.removeprefix("CAD2GIS: ").strip())

        threading.Thread(target=read_announcements, daemon=True).start()
        try:
            parsed = urlsplit(announcements.get(timeout=60))
            origin = f"{parsed.scheme}://{parsed.netloc}"
            token = parse_qs(parsed.fragment)["token"][0]
            with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=180, trust_env=False) as client:
                def request(method, path, **kwargs):
                    response = client.request(method, path, **kwargs)
                    assert response.status_code == 200, (path, response.status_code, response.text[:2000])
                    return response.json()

                assert client.get("/_app/api/status").status_code == 401
                request("POST", "/_app/api/login", json={"token": token})
                assert client.post("/_app/api/login", json={"token": token}).status_code == 401
                status = request("GET", "/_app/api/status")
                assert status["matching"] == "osm" and status["svg_mode"] == "candidate"
                uploaded = request("POST", "/_app/api/upload", params={"name": "安装验收 drawing.dwg"},
                    content=(conversion / "generated.dwg").read_bytes(),
                    headers={"Content-Type": "application/octet-stream"})
                assert digest(Path(uploaded["source"])) == digest(conversion / "generated.dwg")
                job = request("POST", "/_app/api/jobs", json={"action": "inspect", "source": uploaded["source"]})
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    current = next(row for row in request("GET", "/_app/api/jobs")["jobs"] if row["id"] == job["id"])
                    if current["status"] not in {"queued", "running"}:
                        break
                    time.sleep(.2)
                (output / "inspect-job.json").write_text(json.dumps(current, indent=2), encoding="utf-8")
                assert current["status"] == "succeeded", current
                selected = request("POST", "/_app/api/review", json={"run": str(original)})
                client.headers["X-CAD2GIS-Review"] = selected["review_id"]
                summary = request("GET", "/api/run")
                assert summary["osm_matching"]["status"] == "matched"
                assert client.get("/assets/vendor/ol.js").status_code == 200
                svg = summary["svg_candidates"]
                assert svg["status"] == "completed", svg
                assert client.get(svg["report_url"]).status_code == 200
                geographic = Transformer.from_crs(3857, 4326, always_xy=True)
                for index, (x, y, role) in enumerate([
                    (12600000, -800000, "train"), (12600140, -799960, "train"),
                    (12600100, -800080, "check")]):
                    request("POST", "/api/review/features", json={"expected_revision": 0, "feature": {
                        "type": "Feature", "id": f"gcp:installation-{index}",
                        "geometry": {"type": "Point", "coordinates": geographic.transform(x + 110, y + 195)},
                        "properties": {"_kind": "cad_map_gcp", "cad_x": x, "cad_y": y, "role": role}}})
                capture = request("GET", "/api/registration")
                assert capture["matching_ready"] is True, capture
                preview = request("GET", "/api/registration/preview/PTECH",
                    params={"expected_controls_sha256": capture["controls_sha256"]})
                assert preview["preview_only"] is True
                assert digest(original_delivery) == original_hash
                result = request("POST", "/api/matching/rematch", json={
                    "expected_controls_sha256": capture["controls_sha256"],
                    "expected_match_sha256": capture["matching"]["match_sha256"],
                    "expected_match_revision": capture["matching"]["revision"], "execute": True})
                (output / "rematch.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                assert result["status"] == "completed", result
                assert result["matching"]["revision"] == capture["matching"]["revision"] + 1
                assert Path(result["current_run_dir"]) != original
                assert digest(original_delivery) == original_hash
                from cad2gis.native_runtime import ensure_osgeo_runtime
                ensure_osgeo_runtime()
                from osgeo import ogr
                data = ogr.Open(str(Path(result["current_run_dir"]) / "delivery.gpkg"))
                row = data.GetLayerByName("PTECH").GetNextFeature()
                point = row.GetGeometryRef().GetPoint_2D(0)
                assert all(abs(a - b) < .2 for a, b in zip(point, (12600110, -799805))), point
                row = data = None
                request("POST", "/_app/api/shutdown", json={})
            process.wait(timeout=30)
            assert process.returncode == 0
            report = {"schema": "cad2gis.application_bundle_smoke.v1", "status": "passed", "bundle": str(bundle),
                "authentication": "passed", "single_use_link": "passed", "upload_hash_verified": True,
                "real_inspect_job": "passed", "local_map_library": "passed", "svg_report": "passed",
                "gcp_preview_only": True, "real_rematch": "passed", "original_delivery_unchanged": True,
                "matched_point": point, "expected_point": [12600110, -799805], "shutdown": "passed"}
            (output / "application-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            return report
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--conversion", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.bundle, args.output, args.conversion), indent=2))
