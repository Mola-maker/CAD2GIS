# -*- coding: utf-8 -*-
"""fetch_osm_p2.py — 按 data/fetch_plan.json 执行 Overpass 单次查询并缓存（复跑跳过）。

用法: conda run -n cad2gis python tools/road_match_p2/fetch_osm_p2.py
每区域仅一次 way[highway] 查询（timeout 120s），结果落盘 data/overpass_way_highway_*.json。
"""
import json
import pathlib
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

plan = json.loads((DATA / "fetch_plan.json").read_text(encoding="utf-8"))
for p in plan:
    cache = DATA / p["cache_file"]
    if cache.exists():
        print(f"CACHE HIT [{p['region']}]: {cache.name} ({cache.stat().st_size} bytes)")
        continue
    print(f"FETCH [{p['region']}] bbox(SWNE)={p['bbox_4326_swne']}")
    url = OVERPASS_URL + "?data=" + urllib.parse.quote(p["query"])
    req = urllib.request.Request(
        url, headers={"User-Agent": "cad2gis-road-match-p2-multibaseline/0.1"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = resp.read()
    obj = json.loads(payload.decode("utf-8"))
    obj["_cache_meta"] = {"query": p["query"], "bbox_swne": p["bbox_4326_swne"],
                          "region": p["region"]}
    cache.write_text(json.dumps(obj), encoding="utf-8")
    n_way = sum(1 for el in obj.get("elements", []) if el.get("type") == "way")
    print(f"  -> {cache.name}  ways={n_way} bytes={len(payload)}", flush=True)
print("done")
