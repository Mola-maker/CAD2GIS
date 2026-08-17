# -*- coding: utf-8 -*-
"""inspect_p2.py — P2 前置勘察（只读）。

对 kletek / lamteh / lamteh_sf 三个 delivery.gpkg：
  - 列出全部图层、各图层 CRS（WKT 里找 AUTHORITY）、要素数、坐标范围
  - 重点读 CABLE 类图层（名字含 CABLE 的线图层）的几何统计
  - 读 run_manifest.json 中与 CRS/单位/精度相关的键（探测，不假设结构）
结果写 data/inspect_<name>.json。不修改任何源文件。
"""
import json
import pathlib
import re
import sys

import numpy as np
import shapely
import shapely.wkb
from osgeo import ogr

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
RUNS = pathlib.Path(r"E:\branch_CAD2GIS\APD_test\runs")
BASELINES = ["kletek", "lamteh", "lamteh_sf"]

KEY_RE = re.compile(r"crs|epsg|unit|insunit|coord|accuracy|transform|anchor|srs", re.I)


def walk_manifest(obj, path="", hits=None, depth=0):
    """浅采样 manifest 里与坐标/单位相关的键值（最多 80 条，防 8MB 文件刷屏）。"""
    if hits is None:
        hits = []
    if len(hits) >= 80 or depth > 5:
        return hits
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if KEY_RE.search(k) and not isinstance(v, (dict, list)):
                hits.append({"key": p, "value": v})
            walk_manifest(v, p, hits, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            walk_manifest(v, f"{path}[{i}]", hits, depth + 1)
    return hits


def inspect(name):
    run_dir = RUNS / name
    gpkg = run_dir / "delivery.gpkg"
    manifest_path = run_dir / "run_manifest.json"
    out = {"baseline": name, "delivery_gpkg": str(gpkg), "layers": []}

    ds = ogr.Open(str(gpkg), 0)
    if ds is None:
        out["error"] = "cannot open gpkg"
        return out
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayerByIndex(i)
        lname = lyr.GetName()
        srs = lyr.GetSpatialRef()
        auth = None
        if srs is not None:
            auth = srs.GetAuthorityCode(None) or srs.GetAuthorityCode("PROJCS") \
                or srs.GetAuthorityCode("GEOGCS")
            wkt = srs.ExportToWkt()
            m = re.search(r'AUTHORITY\["EPSG","(\d+)"\]', wkt)
            epsg = m.group(1) if m else None
            linear_unit = srs.GetLinearUnitsName() if hasattr(srs, "GetLinearUnitsName") else None
        else:
            epsg, linear_unit = None, None
        # 范围用快速读取（不强制 GetExtent，逐要素算更稳）
        xs, ys, n = [], [], 0
        geom_types = {}
        for feat in lyr:
            g = feat.GetGeometryRef()
            if g is None:
                continue
            n += 1
            gt = g.GetGeometryName()
            geom_types[gt] = geom_types.get(gt, 0) + 1
            if gt in ("POINT",):
                xs.append(g.GetX()); ys.append(g.GetY())
            else:
                try:
                    sh = shapely.wkb.loads(bytes(g.ExportToWkb()))
                    if sh.is_empty:
                        continue
                    b = sh.bounds
                    xs.extend([b[0], b[2]]); ys.extend([b[1], b[3]])
                except Exception:
                    pass
            if n > 200000:  # 防爆
                break
        info = {
            "name": lname, "feature_count_read": n, "epsg": epsg,
            "auth": auth, "linear_unit": linear_unit,
            "geom_types": geom_types,
        }
        if xs:
            info["bbox"] = [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]
        out["layers"].append(info)
    ds = None

    # manifest 采样
    try:
        man = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
        out["manifest_hits"] = walk_manifest(man)
        out["manifest_top_keys"] = list(man.keys())[:40]
    except Exception as e:  # noqa: BLE001
        out["manifest_error"] = str(e)
    return out


def main():
    DATA.mkdir(exist_ok=True)
    for name in BASELINES:
        out = inspect(name)
        (DATA / f"inspect_{name}.json").write_text(
            json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"== {name} ==")
        for l in out["layers"]:
            print(f"  layer={l['name']:<24} epsg={l['epsg']} n={l['feature_count_read']} "
                  f"types={l['geom_types']} bbox={l.get('bbox')}")
        errs = out.get("manifest_error")
        if errs:
            print("  manifest_error:", errs)
        else:
            for h in out.get("manifest_hits", [])[:12]:
                print(f"  manifest {h['key']} = {h['value']}")
    print("done")


if __name__ == "__main__":
    main()
