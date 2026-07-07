"""Real-time CAD2GIS demo server (FastAPI).

Two things this serves that the static demo can't:
  1. an INTERACTIVE MAP of the converted GeoPackage layers (Leaflet, clickable features with
     provenance), loaded as GeoJSON;
  2. a LIVE CONVERSION endpoint — upload a DXF, watch the pipeline stages stream in real time
     (SSE) and see the result accuracy + layers appear.

Run:  python -m demo.server.app   (or)  uvicorn demo.server.app:app --port 8000
Open: http://localhost:8000
"""
from __future__ import annotations

import json
import os
import queue
import threading
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GPKG = os.path.join(ROOT, "build", "DS04_comms_full.gpkg")
REPORT = os.path.join(ROOT, "build", "accuracy_DS04_v2.json")
BENCH = os.path.join(ROOT, "src", "cad2gis", "verify", "benchmark", "ds04_surveyed.json")
DIAGNOSTICS = os.path.join(ROOT, "build", "diagnostics.json")
PROPOSALS = os.path.join(ROOT, "build", "doctor_proposals.json")
CORRECTIONS_DIR = os.path.join(ROOT, "build", "corrections")
VERIFICATION = os.path.join(ROOT, "build", "verification_after_corrections.json")

app = FastAPI(title="CAD2GIS live demo")
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "demo")), name="static")


def _gpkg_layers_as_geojson(path: str = GPKG, max_per_layer: int = 4000) -> dict:
    """Read every layer from the GeoPackage as GeoJSON FeatureCollections (simplified for the map)."""
    import geopandas as gpd
    import fiona

    if not os.path.exists(path):
        return {"error": "GeoPackage not found — run the pipeline first", "path": path}
    out: dict = {}
    for layer in fiona.listlayers(path):
        if layer.startswith("cad2gis_"):  # metadata table, not spatial
            continue
        gdf = gpd.read_file(path, layer=layer)
        if len(gdf) > max_per_layer:  # cap for browser performance
            gdf = gdf.sample(max_per_layer, random_state=1)
        # serialize NaN/None cleanly + keep key attrs for popups
        for col in gdf.columns:
            if col == "geometry":
                continue
            gdf[col] = gdf[col].astype(object).where(gdf[col].notna(), None)
        out[layer] = json.loads(gdf.to_json())
    return out


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    with open(os.path.join(ROOT, "demo", "server", "live.html"), "r", encoding="utf-8") as fh:
        return fh.read()


@app.get("/api/layers")
def layers():
    return JSONResponse(_gpkg_layers_as_geojson())


@app.get("/api/report")
def report():
    if not os.path.exists(REPORT):
        raise HTTPException(404, "run the pipeline first — build/accuracy_DS04_v2.json missing")
    with open(REPORT, "r", encoding="utf-8") as fh:
        return JSONResponse(json.load(fh))


def _json_file_or_empty(path: str, empty: dict):
    if not os.path.exists(path):
        return JSONResponse(empty)
    with open(path, "r", encoding="utf-8") as fh:
        return JSONResponse(json.load(fh))


@app.get("/api/diagnostics")
def diagnostics():
    return _json_file_or_empty(DIAGNOSTICS, {"issues": []})


@app.get("/api/proposals")
def proposals():
    return _json_file_or_empty(PROPOSALS, {"proposals": []})


@app.get("/api/corrections")
def corrections():
    rows = []
    if os.path.isdir(CORRECTIONS_DIR):
        for name in sorted(os.listdir(CORRECTIONS_DIR)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(CORRECTIONS_DIR, name)
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        item = json.loads(line)
                        item["_ledger_file"] = name
                        rows.append(item)
    return JSONResponse({"records": rows})


@app.get("/api/verification")
def verification():
    return _json_file_or_empty(VERIFICATION, {"status": "not_run"})


@app.post("/api/convert")
async def convert(file: UploadFile = File(...)):
    """Live-convert an uploaded DXF: stream pipeline stage events as SSE, end with the result.

    The pipeline runs in a worker thread; the on_stage callback pushes events onto a queue that a
    generator drains as SSE `event: stage` frames. The final frame carries the accuracy + counts.
    """
    if not file.filename.lower().endswith((".dxf", ".dwg")):
        raise HTTPException(400, "upload a .dxf or .dwg")

    tmp = os.path.join(ROOT, "build", "_upload_" + os.path.basename(file.filename))
    with open(tmp, "wb") as fh:
        fh.write(await file.read())

    # DWG -> DXF if needed (LibreDWG)
    src = tmp
    if src.lower().endswith(".dwg"):
        from cad2gis.ingest import normalize_to_dxf

        try:
            src = normalize_to_dxf(tmp).dxf_path
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(422, f"DWG→DXF normalization failed: {ex}")

    import sys
    sys.path.insert(0, os.path.join(ROOT, "src"))

    ev_q: "queue.Queue[tuple]" = queue.Queue()
    result: dict = {}

    def _run():
        from cad2gis.pipeline import run

        def on_stage(name, detail):
            ev_q.put(("stage", {"stage": name, **(detail or {})}))

        try:
            coll, rep = run(src, benchmark=BENCH, on_stage=on_stage)
            result["accuracy"] = rep.accuracy
            result["counts"] = rep.counts_final
            result["network"] = rep.network
            result["georef"] = rep.georef
            ev_q.put(("done", {"overall": rep.accuracy["overall"] if rep.accuracy else None}))
        except Exception as ex:  # noqa: BLE001
            ev_q.put(("error", {"message": str(ex)}))

    def _stream():
        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        while True:
            kind, payload = ev_q.get()
            if kind == "stage":
                yield f"event: stage\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            elif kind == "done":
                yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            elif kind == "error":
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
