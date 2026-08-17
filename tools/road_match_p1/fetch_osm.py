# -*- coding: utf-8 -*-
"""fetch_osm.py — 单次礼貌 Overpass 查询并缓存结果（复跑不重复请求）。

用法: conda run -n cad2gis python tools/road_match_p1/fetch_osm.py
输入: tools/road_match_p1/data/truth_meta.json （由 road_match_p1.py extract 生成）
输出: tools/road_match_p1/data/overpass_way_highway_<hash>.json
"""
import hashlib
import json
import pathlib
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

meta = json.loads((DATA / "truth_meta.json").read_text(encoding="utf-8"))
s, w, n, e = meta["overpass_bbox_4326"]  # south west north east
query = f'[out:json][timeout:120];\nway["highway"]({s},{w},{n},{e});\nout geom;\n'
key = hashlib.md5(query.encode("utf-8")).hexdigest()[:10]
cache = DATA / f"overpass_way_highway_{key}.json"

if cache.exists():
    print(f"CACHE HIT: {cache} ({cache.stat().st_size} bytes)")
    sys.exit(0)

print("Overpass query:")
print(query)
url = OVERPASS_URL + "?data=" + urllib.parse.quote(query)
req = urllib.request.Request(url, headers={"User-Agent": "cad2gis-road-match-p1-prototype/0.1"})
with urllib.request.urlopen(req, timeout=180) as resp:
    payload = resp.read()
obj = json.loads(payload.decode("utf-8"))
obj["_cache_meta"] = {"query": query, "bbox": [s, w, n, e]}
cache.write_text(json.dumps(obj), encoding="utf-8")
n_way = sum(1 for el in obj.get("elements", []) if el.get("type") == "way")
print(f"FETCHED -> {cache}  ways={n_way} bytes={len(payload)}")
