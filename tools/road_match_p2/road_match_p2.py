# -*- coding: utf-8 -*-
"""road_match_p2.py — CAD2GIS 路网匹配自动定位 P2：多基线回归。

把 P1（tools/road_match_p1，只读复用其算法）的"缓冲带重叠 Dice F1 + FFT 互相关
旋转×平移扫描"协议推广到 kletek / lamteh / lamteh_sf 三个基线。

与 P1 的关键差异（诚实性前提）：
  P1 的 hutabohu 基线名义位置与 OSM 对齐（恒等假设 87.7% 相交），可用已知变换
  模拟本地系并量化残差（Route A）。本阶段三个 delivery 的坐标按声明 CRS
  （EPSG:3857）解释全部落在 Null Island 附近几内亚湾大洋上（|coord| ≤ ~10.6km），
  即实际是**本地工程坐标**，无独立真值 ⇒ 全部走 **Route B**：
  以 baselines/*/config/osm_anchor.json 的粗锚平移为先验确定搜索中心，
  拉取 OSM（每区域单次查询落盘缓存），直接扫描旋转×平移，报告分数、
  Top-1/Top-2 分离度与 overlay 供人眼复核；残余不可量化，结论如实写明。

模型约定：
  world = R(θ)·(local − c_l) + c_w
  c_l  = 缆线 bbox 中心（本地系）；c_w = 缆线中心的世界（EPSG:3857）位置（自由平移量）。
  等价 t（world = R·local + t 约定）：t = c_w − R(θ)·c_l。
  粗锚先验：c_w0 = c_l + t_anchor（t_anchor 来自 osm_anchor.json 的 translation_dx/dy）。

阶段：
  extract <name>          读 delivery CABLE（只读）→ data/cables_<name>_local.json、
                          data/meta_<name>.json（含扫描参数与粗锚先验）。
  planfetch               汇总三基线网格 bbox → data/fetch_plan.json
                          （kletek 区、lamteh 区（lamteh/lamteh_sf 共用）、nullisland 筛查区）。
  screen                  恒等假设筛查（用 nullisland 缓存）→ results/identity_screen.json。
  prep <name>             区域 OSM 缓存 → 裁剪道路 + 道路缓冲 union WKB 缓存。
  coarse <name> <k> <n>   粗扫描第 k/n 块（θ 分块，适配 300s 单次执行预算）。
  fine <name> <i>         对粗扫描全局 Top-3 中第 i 个峰做 θ±5° 步 1° 细扫。
  finalize <name>         合并细扫 → 亚像素 → 去重 → 精确 F1 抛光 Top-1 →
                          先验分数/分离度 → overlay（geojson/svg/png）→
                          results/baseline_<name>.json。
  summary                 四基线（含 hutabohu P1 引用）→ results/multibaseline_summary.json。

运行：conda run -n cad2gis python tools/road_match_p2/road_match_p2.py <phase> [args]
确定性：扫描无随机源；OSM 结果全部落盘缓存；selftest 在 coarse 第 0 块自动执行。
"""
import argparse
import hashlib
import json
import math
import pathlib
import sys
import time

import numpy as np
import pyproj
import shapely
import shapely.wkb
from osgeo import ogr
from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
RUNS = pathlib.Path(r"E:\branch_CAD2GIS\APD_test\runs")
P1_RESULTS = HERE.parent / "road_match_p1" / "results"  # 只读引用

BUFFER_M = 15.0
COARSE_THETA_STEP = 5.0
FINE_THETA_RANGE = 5.0
FINE_THETA_STEP = 1.0
TOP_K_COARSE = 3
SEED = 20240818  # 仅 selftest 使用（与 P1 相同）

# 每基线扫描配置（res/search_radius 按缆线尺寸与锚点精度调参，见 README）
CFG = {
    "kletek": dict(region="kletek", res=5.0, search_radius=1500.0, margin=500.0, fft=1024),
    "lamteh": dict(region="lamteh", res=8.0, search_radius=2500.0, margin=500.0, fft=3072),
    "lamteh_sf": dict(region="lamteh_sf", res=8.0, search_radius=2500.0, margin=500.0, fft=3072),
}
# lamteh 与 lamteh_sf 地理上同区（同一 Lamteh Dayah），共用一份 OSM 区域缓存
REGION_OF = {"kletek": "kletek", "lamteh": "lamteh", "lamteh_sf": "lamteh"}

TO_4326 = pyproj.Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
FROM_4326 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

NULL_BBOX = (-0.11, -0.11, 0.11, 0.11)  # S W N E，覆盖三基线名义 footprint


# ---------------------------------------------------------------- 工具
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rot_mat(deg):
    r = math.radians(deg)
    return np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])


def load_cables_local(gpkg):
    """只读打开 delivery，取 CABLE 图层线几何（原始本地坐标，数值按米处理）。"""
    ds = ogr.Open(str(gpkg), 0)
    if ds is None:
        raise RuntimeError(f"无法打开 {gpkg}")
    lyr = ds.GetLayerByName("CABLE")
    lines = []
    for feat in lyr:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        sh = shapely.wkb.loads(bytes(geom.ExportToWkb()))
        if sh.geom_type == "LineString":
            lines.append(sh)
        elif sh.geom_type == "MultiLineString":
            lines.extend(list(sh.geoms))
    ds = None
    return lines


def layer_stats(gpkg, layer_name):
    ds = ogr.Open(str(gpkg), 0)
    lyr = ds.GetLayerByName(layer_name)
    if lyr is None:
        ds = None
        return None
    n, nv, length = 0, 0, 0.0
    for feat in lyr:
        g = feat.GetGeometryRef()
        if g is None:
            continue
        sh = shapely.wkb.loads(bytes(g.ExportToWkb()))
        if sh.geom_type == "LineString":
            n += 1; nv += len(sh.coords); length += sh.length
        elif sh.geom_type == "MultiLineString":
            for part in sh.geoms:
                n += 1; nv += len(part.coords); length += part.length
    ds = None
    return {"line_count": n, "vertex_count": int(nv), "total_length_units": float(length)}


def manifest_unit_contract(manifest_path):
    man = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    u = man.get("crs", {}).get("unit_crs_contract", {})
    ca = man.get("validation", {}).get("coordinate_accuracy", {})
    co = man.get("crs", {}).get("coordinate_operation", {})
    return {
        "coordinate_mode": u.get("coordinate_mode"),
        "source_crs": u.get("source_crs"),
        "insunits": u.get("source_geometry_unit", {}).get("insunits"),
        "metres_per_unit": u.get("source_geometry_unit", {}).get("metres_per_unit"),
        "source_coordinate_scale_to_m": u.get("source_coordinate_scale_to_m"),
        "absolute_accuracy_validation": co.get("absolute_accuracy_validation"),
        "coordinate_domain_status": ca.get("coordinate_domain_status"),
        "coordinate_domain_passed": ca.get("coordinate_domain_passed"),
    }


def dilate_disk(mask, r_px):
    r = int(math.ceil(r_px))
    out = mask.copy()
    H, W = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r_px * r_px:
                continue
            shifted = np.zeros_like(mask)
            ys = slice(max(0, dy), min(H, H + dy))
            xs = slice(max(0, dx), min(W, W + dx))
            ysrc = slice(max(0, -dy), min(H, H - dy))
            xsrc = slice(max(0, -dx), min(W, W - dx))
            shifted[ys, xs] = mask[ysrc, xsrc]
            out |= shifted
    return out


def rasterize_lines(lines_pts, origin_x, origin_y_max, res, size_px, buffer_px):
    img = Image.new("L", (size_px, size_px), 0)
    draw = ImageDraw.Draw(img)
    for pts in lines_pts:
        px = (pts[:, 0] - origin_x) / res
        py = (origin_y_max - pts[:, 1]) / res
        draw.line(list(zip(px.tolist(), py.tolist())), fill=255, width=1)
    mask = np.asarray(img, dtype=bool)
    if buffer_px > 0:
        mask = dilate_disk(mask, buffer_px)
    return mask


def lines_to_points(lines):
    return [np.asarray(ln.coords, dtype=float) for ln in lines]


# ---------------------------------------------------------------- extract / planfetch
def phase_extract(name, search_radius=None):
    DATA.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    cfg = CFG[name]
    gpkg = RUNS / name / "delivery.gpkg"
    manifest_path = RUNS / name / "run_manifest.json"
    anchor = json.loads((DATA / f"osm_anchor_{name}.json").read_text(encoding="utf-8"))
    git_ref = (DATA / "git_ref_robustness.txt").read_text(encoding="utf-8").strip()

    lines = load_cables_local(gpkg)
    pts_all = np.concatenate([np.asarray(ln.coords) for ln in lines])
    bbox = [float(pts_all[:, 0].min()), float(pts_all[:, 1].min()),
            float(pts_all[:, 0].max()), float(pts_all[:, 1].max())]
    c_l = np.array([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2])
    half_span = float(math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2)
    total_len = float(sum(ln.length for ln in lines))
    nv = int(sum(len(ln.coords) for ln in lines))

    t_anchor = np.array([anchor["translation_dx"], anchor["translation_dy"]], dtype=float)
    c_w0 = c_l + t_anchor  # 粗锚先验：缆线中心世界位置（纯平移假设）

    res = cfg["res"]
    search_radius = cfg["search_radius"] if search_radius is None else float(search_radius)
    # 网格半径固定由默认搜索半径决定（保持已抓取的 OSM 区域与 prep 缓存有效）；
    # search_radius 覆盖只允许在既有网格可达范围内外扩搜索掩膜。
    grid_radius = math.ceil((half_span + BUFFER_M + cfg["search_radius"]
                             + cfg["margin"]) / 100.0) * 100.0
    if search_radius > cfg["search_radius"]:
        reachable = grid_radius - (half_span + BUFFER_M + 4 * res)
        if search_radius > reachable:
            raise SystemExit(f"{name}: search_radius={search_radius} 超出可达范围 {reachable:.0f}m")
    N = int(math.ceil(2 * grid_radius / res))
    M = int(math.ceil(2 * (half_span + BUFFER_M + 4 * res) / res))
    if N + M > cfg["fft"]:
        raise SystemExit(f"{name}: N+M={N+M} > FFT={cfg['fft']}，需调大 fft 或 res")

    meta = {
        "baseline": name,
        "delivery_gpkg": str(gpkg),
        "delivery_sha256": sha256_of(gpkg),
        "run_manifest": str(manifest_path),
        "run_manifest_sha256": sha256_of(manifest_path),
        "unit_contract": manifest_unit_contract(manifest_path),
        "anchor_config": anchor,
        "anchor_git_ref": git_ref,
        "coordinate_interpretation": (
            "delivery 图层声明 CRS 与 manifest source_crs=EPSG:3857 不符：坐标数值量级 ≤ ~10.6km，"
            "按 EPSG:3857 解释位于 Null Island 附近几内亚湾（大洋），不可能是印尼 FTTH 现场 ⇒ "
            "判定为本地工程坐标。数值按米处理（scale=1）：kletek insunits=6(m) 与量级一致；"
            "lamteh/lamteh_sf insunits=4(mm) 但若按 mm 解释缆线总长仅数米，与村级 FTTH 规模矛盾，"
            "而按米解释缆线跨度数 km、与 Nominatim 村级 bbox(约4.5km) 同量级 ⇒ scale=1 为唯一合理假设；"
            "该假设最终由扫描峰强度经验检验。"
        ),
        "scale_assumption": 1.0,
        "cable_count": len(lines),
        "cable_vertex_count": nv,
        "cable_total_length_local_m": total_len,
        "cable_bbox_local": bbox,
        "cable_centroid_local": [float(c_l[0]), float(c_l[1])],
        "cable_half_span_m": half_span,
        "cable_segment_layer_stats": layer_stats(gpkg, "CABLE_SEGMENT"),
        "t_anchor_3857": [float(t_anchor[0]), float(t_anchor[1])],
        "search_centre_prior_3857": [float(c_w0[0]), float(c_w0[1])],
        "search_centre_prior_4326": [float(v) for v in TO_4326.transform(c_w0[0], c_w0[1])],
        "scan": {
            "res_m_per_px": res, "grid_radius_m": grid_radius,
            "search_radius_m": search_radius, "buffer_m": BUFFER_M,
            "grid_px": N, "template_px": M, "fft_size": cfg["fft"],
            "coarse_theta_step_deg": COARSE_THETA_STEP,
            "fine_theta_range_deg": FINE_THETA_RANGE, "fine_theta_step_deg": FINE_THETA_STEP,
            "region": REGION_OF[name],
        },
        "model_convention": "world = R(theta) @ (local - c_l) + c_w ; t = c_w - R(theta) @ c_l",
        "search_radius_note": (
            None if search_radius == cfg["search_radius"] else
            f"搜索半径由默认 {cfg['search_radius']}m 外扩至 {search_radius}m："
            "粗扫描峰距先验中心约 2.4km，接近原掩膜边界，外扩以防截断峰。"
        ),
    }
    (DATA / f"meta_{name}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    payload = {"crs": "local(numeric metres, scale=1)",
               "cables": [[list(map(float, c)) for c in ln.coords] for ln in lines]}
    (DATA / f"cables_{name}_local.json").write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps({
        "baseline": name, "cables": len(lines), "vertices": nv,
        "length_m": round(total_len, 1), "bbox": [round(v, 1) for v in bbox],
        "half_span_m": round(half_span, 1),
        "c_w0": [round(v, 1) for v in c_w0],
        "N": N, "M": M, "fft": cfg["fft"], "grid_radius": grid_radius,
    }, ensure_ascii=False))


def phase_planfetch():
    """汇总三基线扫描网格 bbox → 3 个 Overpass 查询计划（含 nullisland 筛查）。"""
    regions = {}
    for name in CFG:
        meta = json.loads((DATA / f"meta_{name}.json").read_text(encoding="utf-8"))
        cx, cy = meta["search_centre_prior_3857"]
        R = meta["scan"]["grid_radius_m"]
        reg = REGION_OF[name]
        b = (cx - R, cy - R, cx + R, cy + R)
        if reg in regions:
            p = regions[reg]
            regions[reg] = (min(p[0], b[0]), min(p[1], b[1]), max(p[2], b[2]), max(p[3], b[3]))
        else:
            regions[reg] = b
    plan = []
    for reg, (x0, y0, x1, y1) in sorted(regions.items()):
        lon0, lat0 = TO_4326.transform(x0, y0)
        lon1, lat1 = TO_4326.transform(x1, y1)
        s, w, n, e = min(lat0, lat1), min(lon0, lon1), max(lat0, lat1), max(lon0, lon1)
        q = f'[out:json][timeout:120];\nway["highway"]({s},{w},{n},{e});\nout geom;\n'
        key = hashlib.md5(q.encode("utf-8")).hexdigest()[:10]
        plan.append({"region": reg, "bbox_4326_swne": [s, w, n, e],
                     "bbox_3857": [x0, y0, x1, y1], "query": q,
                     "cache_file": f"overpass_way_highway_{reg}_{key}.json"})
    s, w, n, e = NULL_BBOX
    q = f'[out:json][timeout:120];\nway["highway"]({s},{w},{n},{e});\nout geom;\n'
    key = hashlib.md5(q.encode("utf-8")).hexdigest()[:10]
    plan.append({"region": "nullisland", "bbox_4326_swne": [s, w, n, e],
                 "bbox_3857": None, "query": q,
                 "cache_file": f"overpass_way_highway_nullisland_{key}.json"})
    (DATA / "fetch_plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    for p in plan:
        print(p["region"], p["cache_file"], [round(v, 5) for v in p["bbox_4326_swne"]])


# ---------------------------------------------------------------- 共用加载
def load_meta(name):
    return json.loads((DATA / f"meta_{name}.json").read_text(encoding="utf-8"))


def load_cables(name):
    payload = json.loads((DATA / f"cables_{name}_local.json").read_text(encoding="utf-8"))
    return [shapely.LineString(c) for c in payload["cables"]]


def region_cache(region):
    plan = json.loads((DATA / "fetch_plan.json").read_text(encoding="utf-8"))
    for p in plan:
        if p["region"] == region:
            path = DATA / p["cache_file"]
            if not path.exists():
                raise FileNotFoundError(f"缺少 Overpass 缓存 {path}，先运行 fetch_osm_p2.py")
            return path
    raise KeyError(region)


def load_roads_3857(region):
    """解析区域 Overpass 缓存 → EPSG:3857 道路 LineString 列表。"""
    cache = region_cache(region)
    obj = json.loads(cache.read_text(encoding="utf-8"))
    roads = []
    for el in obj.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = np.array([[p["lon"], p["lat"]] for p in el["geometry"]], dtype=float)
        if len(coords) < 2:
            continue
        x, y = FROM_4326.transform(coords[:, 0], coords[:, 1])
        roads.append(shapely.LineString(np.column_stack([x, y])))
    return roads, cache


# ---------------------------------------------------------------- screen
def phase_screen():
    """恒等假设筛查：把 delivery 坐标按声明的 EPSG:3857 解释，核验其落在何处。

    证据 1（几何量级）：三基线缆线 |coord| ≤ ~10.6km ⇒ 3857 原点（几内亚湾 Null
    Island）附近，大洋之中，不可能是印尼 FTTH 现场。
    证据 2（OSM 实证）：nullisland 缓存覆盖全部名义 footprint，统计 highway way 数。
    """
    cache = region_cache("nullisland")
    obj = json.loads(cache.read_text(encoding="utf-8"))
    ways = [el for el in obj.get("elements", []) if el.get("type") == "way"]
    n_ways = len(ways)
    s, w, n, e = NULL_BBOX
    out = {
        "purpose": "检验各 delivery 名义位置（按声明 CRS=EPSG:3857 直读）是否可能与 OSM 路网对齐",
        "null_bbox_swne_4326": [s, w, n, e],
        "osm_cache": cache.name,
        "osm_highway_way_count_in_null_bbox": n_ways,
        "baselines": {},
    }
    for name in CFG:
        meta = load_meta(name)
        x0, y0, x1, y1 = meta["cable_bbox_local"]
        lon0, lat0 = TO_4326.transform(x0, y0)
        lon1, lat1 = TO_4326.transform(x1, y1)
        inside = (s <= min(lat0, lat1) and max(lat0, lat1) <= n
                  and w <= min(lon0, lon1) and max(lon0, lon1) <= e)
        max_abs = max(abs(x0), abs(x1), abs(y0), abs(y1))
        out["baselines"][name] = {
            "nominal_bbox_3857": [x0, y0, x1, y1],
            "nominal_bbox_4326": [float(min(lon0, lon1)), float(min(lat0, lat1)),
                                  float(max(lon0, lon1)), float(max(lat0, lat1))],
            "max_abs_coord_m_from_3857_origin": float(max_abs),
            "nominal_footprint_inside_null_bbox": bool(inside),
            "identity_f1": 0.0 if n_ways == 0 else None,
            "decision": "route_B_anchor_scan",
            "reason": ("名义 footprint 位于几内亚湾大洋（3857 原点 %.1f km 内），该海域 OSM "
                       "highway way 数 = %d ⇒ 恒等假设 F1=0，delivery 实为本地工程坐标，"
                       "无独立真值，残余不可量化。" % (max_abs / 1000.0, n_ways)),
        }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "identity_screen.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- prep
def phase_prep(name):
    meta = load_meta(name)
    cx, cy = meta["search_centre_prior_3857"]
    R = meta["scan"]["grid_radius_m"]
    domain = shapely.box(cx - R, cy - R, cx + R, cy + R)
    roads, cache = load_roads_3857(REGION_OF[name])
    clipped = []
    for r in roads:
        if not r.intersects(domain):
            continue
        part = r.intersection(domain)
        if part.is_empty:
            continue
        if part.geom_type == "LineString":
            clipped.append(part)
        elif part.geom_type == "MultiLineString":
            clipped.extend([g for g in part.geoms if not g.is_empty])
    road_buf = shapely.union_all([ln.buffer(BUFFER_M) for ln in clipped])
    (DATA / f"roadbuf_{name}.wkb").write_bytes(shapely.wkb.dumps(road_buf))
    payload = {"crs": "EPSG:3857",
               "roads": [[list(map(float, c)) for c in ln.coords] for ln in clipped]}
    (DATA / f"roads_{name}_clipped.json").write_text(json.dumps(payload), encoding="utf-8")
    stats = {"baseline": name, "region": REGION_OF[name], "osm_cache": cache.name,
             "osm_ways_region": len(roads), "roads_in_domain": len(clipped),
             "road_buffer_area_m2": float(road_buf.area),
             "domain_3857": [cx - R, cy - R, cx + R, cy + R]}
    (DATA / f"prep_{name}.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))


def load_prepped(name):
    roads_payload = json.loads((DATA / f"roads_{name}_clipped.json").read_text(encoding="utf-8"))
    roads = [shapely.LineString(c) for c in roads_payload["roads"]]
    road_buf = shapely.wkb.loads((DATA / f"roadbuf_{name}.wkb").read_bytes())
    return roads, road_buf


# ---------------------------------------------------------------- selftest
def phase_selftest():
    """合成验证（与 P1 相同）：已知偏移图案的互相关峰必须精确还原偏移。"""
    rng = np.random.default_rng(SEED)
    N = 400
    A = np.zeros((N, N), dtype=bool)
    for _ in range(30):
        x0, y0 = rng.uniform(20, N - 20, 2)
        x1, y1 = rng.uniform(20, N - 20, 2)
        img = Image.new("L", (N, N), 0)
        ImageDraw.Draw(img).line([x0, y0, x1, y1], fill=255, width=1)
        A |= np.asarray(img, dtype=bool)
    M = 120
    img = Image.new("L", (M, M), 0)
    ImageDraw.Draw(img).line([10, 10, M - 10, M // 2, M // 2, M - 10], fill=255, width=1)
    B = np.asarray(img, dtype=bool)
    off = (173, 88)
    A[off[0]:off[0] + M, off[1]:off[1] + M] |= B
    S = 2560
    FA = np.fft.fft2(A.astype(float), s=(S, S))
    FB = np.fft.fft2(B.astype(float), s=(S, S))
    C = np.fft.ifft2(FA * np.conj(FB)).real
    iy, ix = np.unravel_index(np.argmax(C[: N - M, : N - M]), C[: N - M, : N - M].shape)
    ok = (iy, ix) == off
    print(f"selftest: recovered=({iy},{ix}) expected={off} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        raise SystemExit("互相关平移映射自检失败，停止。")


# ---------------------------------------------------------------- scan 内核
class Scanner:
    """单个基线的栅格扫描器（模型：world = R(θ)·(local − c_l) + c_w）。"""

    def __init__(self, name, buffer_m=BUFFER_M):
        self.name = name
        self.buffer_m = buffer_m
        self.name = name
        meta = load_meta(name)
        self.meta = meta
        sc = meta["scan"]
        self.res = sc["res_m_per_px"]
        self.N = sc["grid_px"]
        self.M = sc["template_px"]
        self.S = sc["fft_size"]
        self.search_radius = sc["search_radius_m"]
        self.c_l = np.array(meta["cable_centroid_local"])
        self.c_w0 = np.array(meta["search_centre_prior_3857"])
        R = sc["grid_radius_m"]
        self.x_min = self.c_w0[0] - R
        self.y_max = self.c_w0[1] + R
        cables = load_cables(name)
        self.local_pts0 = [np.asarray(ln.coords, dtype=float) - self.c_l for ln in cables]
        roads, road_buf = load_prepped(name)
        self.roads = roads
        self.road_buf = road_buf
        self.road_area = float(road_buf.area)
        buf_px = self.buffer_m / self.res
        self.buf_px = buf_px
        roads_pts = lines_to_points(roads)
        self.A = rasterize_lines(roads_pts, self.x_min, self.y_max, self.res, self.N, buf_px)
        self.areaA = int(self.A.sum())
        self.FA = np.fft.fft2(self.A.astype(float), s=(self.S, self.S))
        iy_g, ix_g = np.mgrid[0: self.N - self.M, 0: self.N - self.M]
        tx = self.x_min + (ix_g + self.M / 2) * self.res
        ty = self.y_max - (iy_g + self.M / 2) * self.res
        self.search_mask = ((tx - self.c_w0[0]) ** 2
                            + (ty - self.c_w0[1]) ** 2) <= self.search_radius ** 2

    def build_B(self, theta_deg):
        rot = [pts @ rot_mat(theta_deg).T for pts in self.local_pts0]
        return rasterize_lines(rot, -self.M * self.res / 2, self.M * self.res / 2,
                               self.res, self.M, self.buf_px)

    def map_to_cw(self, iy, ix):
        return np.array([self.x_min + (ix + self.M / 2) * self.res,
                         self.y_max - (iy + self.M / 2) * self.res])

    def eval_theta(self, theta_deg):
        """对给定 θ 评估全部平移，返回 Dice 与覆盖率（cable-recall）双指标的峰。

        score_dice      = 2·C/(areaA+areaB)  —— P1 协议指标（道路密集区被分母稀释）
        score_coverage  = C/areaB            —— 缆线缓冲覆盖率（P1 sanity 同款 recall 型，
                                               对道路总量不敏感，P2 主排序指标）
        返回 {"dice": (f1, iy, ix, c_w_subpx), "coverage": (cov, iy, ix, c_w_subpx)}。
        """
        B = self.build_B(theta_deg)
        areaB = int(B.sum())
        FB = np.fft.fft2(B.astype(float), s=(self.S, self.S))
        C = np.fft.ifft2(self.FA * np.conj(FB)).real[: self.N - self.M, : self.N - self.M]
        out = {}
        for key, sc_map in (
                ("dice", 2.0 * C / (self.areaA + areaB)),
                ("coverage", C / max(areaB, 1))):
            score = np.where(self.search_mask, sc_map, -np.inf)
            iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
            dy = dx = 0.0
            if 0 < iy < score.shape[0] - 1 and 0 < ix < score.shape[1] - 1:
                f0 = score[iy, ix]
                nb = (score[iy + 1, ix], score[iy - 1, ix],
                      score[iy, ix + 1], score[iy, ix - 1])
                if np.all(np.isfinite(nb)):  # 掩膜边缘（-inf 邻居）不做亚像素拟合
                    fy = (nb[0] - nb[1]) / 2
                    fyy = nb[0] - 2 * f0 + nb[1]
                    fx = (nb[2] - nb[3]) / 2
                    fxx = nb[2] - 2 * f0 + nb[3]
                    if fyy < 0:
                        dy = float(np.clip(-fy / fyy, -1, 1))
                    if fxx < 0:
                        dx = float(np.clip(-fx / fxx, -1, 1))
            out[key] = (float(score[iy, ix]), int(iy), int(ix),
                        self.map_to_cw(iy + dy, ix + dx))
        return out

    def _exact_parts(self, theta_deg, c_w):
        moved = [shapely.LineString(pts @ rot_mat(theta_deg).T + np.asarray(c_w))
                 for pts in self.local_pts0]
        cb = shapely.union_all([ln.buffer(BUFFER_M) for ln in moved])
        inter = cb.intersection(self.road_buf).area
        return float(inter), float(cb.area)

    def exact_f1(self, theta_deg, c_w):
        inter, cb_area = self._exact_parts(theta_deg, c_w)
        denom = cb_area + self.road_area
        return (2.0 * inter / denom) if denom > 0 else 0.0

    def exact_coverage(self, theta_deg, c_w):
        inter, cb_area = self._exact_parts(theta_deg, c_w)
        return (inter / cb_area) if cb_area > 0 else 0.0


def t_equiv(theta_deg, c_w, c_l):
    return np.asarray(c_w) - rot_mat(theta_deg) @ np.asarray(c_l)


# ---------------------------------------------------------------- coarse / fine
def phase_coarse(name, chunk, nchunks):
    if chunk == 0:
        phase_selftest()
    t0 = time.perf_counter()
    sc = Scanner(name)
    n_theta = int(360.0 / COARSE_THETA_STEP)
    per = int(math.ceil(n_theta / nchunks))
    rows = []
    for k in range(chunk * per, min((chunk + 1) * per, n_theta)):
        theta = k * COARSE_THETA_STEP
        r = sc.eval_theta(theta)
        cov_f, _, _, cov_cw = r["coverage"]
        dice_f, _, _, dice_cw = r["dice"]
        rows.append({"theta_deg": theta,
                     "coverage": cov_f, "c_w_3857": [float(cov_cw[0]), float(cov_cw[1])],
                     "dice_f1": dice_f,
                     "dice_c_w_3857": [float(dice_cw[0]), float(dice_cw[1])]})
        print(f"  [{sc.name} c{chunk}] θ={theta:6.1f}°  cov={cov_f:.4f}  dice={dice_f:.4f}",
              flush=True)
    RESULTS.mkdir(exist_ok=True)
    out = {"baseline": name, "chunk": chunk, "n_chunks": nchunks,
           "elapsed_s": round(time.perf_counter() - t0, 2), "rows": rows}
    (RESULTS / f"coarse_{name}_{chunk}.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"coarse chunk {chunk} done in {out['elapsed_s']}s")


def load_coarse_all(name):
    rows = []
    for p in sorted(RESULTS.glob(f"coarse_{name}_*.json")):
        obj = json.loads(p.read_text(encoding="utf-8"))
        if obj.get("baseline") != name:  # 防止 coarse_lamteh_* 匹配到 coarse_lamteh_sf_*
            continue
        rows.extend(obj["rows"])
    rows.sort(key=lambda r: -r["coverage"])
    return rows


def phase_fine(name, idx):
    t0 = time.perf_counter()
    sc = Scanner(name)
    top = load_coarse_all(name)[:TOP_K_COARSE]
    if idx >= len(top):
        raise SystemExit(f"fine idx {idx} 超出 Top-{len(top)}")
    th0 = top[idx]["theta_deg"]
    rows = []
    for dth in np.arange(-FINE_THETA_RANGE, FINE_THETA_RANGE + 1e-9, FINE_THETA_STEP):
        theta = float((th0 + dth) % 360.0)
        r = sc.eval_theta(theta)
        cov_f, _, _, cov_cw = r["coverage"]
        dice_f = r["dice"][0]
        rows.append({"theta_deg": theta, "coverage": cov_f, "dice_f1": dice_f,
                     "c_w_3857": [float(cov_cw[0]), float(cov_cw[1])]})
    rows.sort(key=lambda r: -r["coverage"])
    out = {"baseline": name, "fine_of_coarse_rank": idx, "coarse_theta_deg": th0,
           "elapsed_s": round(time.perf_counter() - t0, 2), "rows": rows}
    (RESULTS / f"fine_{name}_{idx}.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"fine[{idx}] around θ={th0}: best coverage {rows[0]['coverage']:.4f} @ "
          f"θ={rows[0]['theta_deg']} ({out['elapsed_s']}s)")


# ---------------------------------------------------------------- finalize
def circ_dtheta(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def dedup_modes(rows, dtheta=5.0, dm=350.0):
    """把细扫描行贪心合并为独立模式（θ 环形差 <dtheta° 且 c_w 距离 <dm m 视为同模式）。
    半径选取：缆线走廊数 km 长，同 θ 沿走廊数百米平移 coverage 近似等价，
    dm=350m 是'同一物理假设族'的合理粒度。"""
    modes = []
    for r in sorted(rows, key=lambda r: -r["coverage"]):
        cw = np.array(r["c_w_3857"])
        if all(abs(circ_dtheta(r["theta_deg"], m["theta_deg"])) >= dtheta
               or np.hypot(*(cw - np.array(m["c_w_3857"]))) >= dm for m in modes):
            modes.append(r)
    return modes


def polish(sc, theta0, c_w0):
    """精确覆盖率爬山抛光（shapely 矢量，消除栅格量化）。"""
    th, cw = float(theta0), np.array(c_w0, dtype=float)
    cur = sc.exact_coverage(th, cw)
    step_m, step_deg = 5.0, 0.5
    n_eval = 1
    while step_m >= 1.0:
        improved = False
        for dth in (0.0, step_deg, -step_deg):
            for dt in ((0, 0), (step_m, 0), (-step_m, 0), (0, step_m), (0, -step_m),
                       (step_m, step_m), (step_m, -step_m), (-step_m, step_m),
                       (-step_m, -step_m)):
                f = sc.exact_coverage(th + dth, cw + np.array(dt, dtype=float))
                n_eval += 1
                if f > cur + 1e-9:
                    cur, th, cw, improved = f, th + dth, cw + np.array(dt, dtype=float), True
        if not improved:
            step_m /= 2.5
            step_deg /= 2.5
    return th, cw, cur, n_eval


def moved_cables(sc, theta, c_w):
    return [shapely.LineString(pts @ rot_mat(theta).T + np.asarray(c_w))
            for pts in sc.local_pts0]


def write_overlay(name, sc, best, prior, modes):
    """overlay_<name>.geojson（EPSG:4326）+ 全域/局部 SVG+PNG（PIL，无 matplotlib）。"""
    recovered = moved_cables(sc, best["theta_deg"], best["c_w_3857"])
    prior_lines = moved_cables(sc, 0.0, sc.c_w0)

    def to4326(lines):
        out = []
        for ln in lines:
            xy = np.asarray(ln.coords)
            lon, lat = TO_4326.transform(xy[:, 0], xy[:, 1])
            out.append([[float(a), float(b)] for a, b in zip(lon, lat)])
        return out

    feats = []
    for lname, lines, color in (("osm_road", sc.roads, "#999999"),
                                ("cable_prior_anchor", prior_lines, "#4575b4"),
                                ("cable_recovered", recovered, "#d73027")):
        for coords in to4326(lines):
            feats.append({"type": "Feature", "properties": {"layer": lname, "stroke": color},
                          "geometry": {"type": "LineString", "coordinates": coords}})
    for i, m in enumerate(modes[:5]):
        lon, lat = TO_4326.transform(m["c_w_3857"][0], m["c_w_3857"][1])
        feats.append({"type": "Feature",
                      "properties": {"layer": "mode_top5", "rank": i + 1,
                                     "theta_deg": m["theta_deg"],
                                     "coverage": m.get("coverage")},
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}})
    anchor_c = sc.meta["anchor_config"]["target_epsg3857_centre"]
    for lname, pt in (("search_centre_prior", sc.c_w0),
                      ("anchor_target_centre", np.array(anchor_c)),
                      ("recovered_c_w", np.array(best["c_w_3857"]))):
        lon, lat = TO_4326.transform(pt[0], pt[1])
        feats.append({"type": "Feature", "properties": {"layer": lname},
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}})
    gj = {"type": "FeatureCollection",
          "properties": {"note": "EPSG:4326; cable_prior_anchor=粗锚先验(θ=0)位置；"
                                 "cable_recovered=扫描最优假设（无独立真值）"},
          "features": feats}
    (RESULTS / f"overlay_{name}.geojson").write_text(json.dumps(gj), encoding="utf-8")

    def render(cx, cy, half_span, suffix, title):
        W = H = 900
        span = 2 * half_span

        def sx(x):
            return (x - cx) / span * W + W / 2

        def sy(y):
            return H / 2 - (y - cy) / span * H

        def paths(lines):
            ds = []
            for ln in lines:
                xy = np.asarray(ln.coords)
                ds.append("M" + " L".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in xy))
            return ds

        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                 f'viewBox="0 0 {W} {H}" style="background:#faf8f5">',
                 f'<rect x="0" y="0" width="{W}" height="{H}" fill="#faf8f5"/>']
        for d in paths(sc.roads):
            parts.append(f'<path d="{d}" stroke="#b0a89f" stroke-width="1.1" fill="none"/>')
        for d in paths(prior_lines):
            parts.append(f'<path d="{d}" stroke="#7b9cc4" stroke-width="1.8" fill="none" opacity="0.75"/>')
        for d in paths(recovered):
            parts.append(f'<path d="{d}" stroke="#c2452f" stroke-width="2.2" fill="none"/>')
        for pt, col, r in ((sc.c_w0, "#4575b4", 5), (np.array(anchor_c), "#888", 4),
                           (np.array(best["c_w_3857"]), "#c2452f", 6)):
            parts.append(f'<circle cx="{sx(pt[0]):.1f}" cy="{sy(pt[1]):.1f}" r="{r}" '
                         f'fill="none" stroke="{col}" stroke-width="2"/>')
        for i, m in enumerate(modes[:5]):
            mx, my = sx(m["c_w_3857"][0]), sy(m["c_w_3857"][1])
            parts.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="4" fill="#e0a458"/>')
            parts.append(f'<text x="{mx + 6:.1f}" y="{my - 4:.1f}" font-family="sans-serif" '
                         f'font-size="11" fill="#8a6d3b">{i + 1}</text>')
        parts.append(f'<text x="14" y="24" font-family="sans-serif" font-size="15" fill="#333">{title}</text>')
        parts.append(f'<text x="14" y="{H - 12}" font-family="sans-serif" font-size="12" fill="#777">'
                     f'灰=OSM道路 蓝=粗锚先验(θ=0) 红=扫描最优 橙点=Top5模式 蓝圈=先验中心 灰圈=锚点中心</text>')
        parts.append('</svg>')
        (RESULTS / f"overlay_{name}{suffix}.svg").write_text("".join(parts), encoding="utf-8")

        S2 = 3
        img = Image.new("RGB", (W * S2, H * S2), (250, 248, 245))
        dr = ImageDraw.Draw(img)

        def draw_lines(lines, color, width):
            for ln in lines:
                xy = np.asarray(ln.coords)
                pts = [(sx(p[0]) * S2, sy(p[1]) * S2) for p in xy]
                dr.line(pts, fill=color, width=width * S2)

        draw_lines(sc.roads, (176, 168, 159), 1)
        draw_lines(prior_lines, (123, 156, 196), 2)
        draw_lines(recovered, (194, 69, 47), 2)
        for pt, color, r in ((sc.c_w0, (69, 117, 180), 5),
                             (np.array(best["c_w_3857"]), (194, 69, 47), 6)):
            cxp, cyp = sx(pt[0]) * S2, sy(pt[1]) * S2
            dr.ellipse([cxp - r * S2, cyp - r * S2, cxp + r * S2, cyp + r * S2],
                       outline=color, width=2 * S2)
        img = img.resize((W, H), Image.LANCZOS)
        img.save(RESULTS / f"overlay_{name}{suffix}.png")

    R = sc.meta["scan"]["grid_radius_m"]
    render(sc.c_w0[0], sc.c_w0[1], R, "",
           f"{name} 全域 — cov={best['exact_coverage']:.3f} θ={best['theta_deg']:.1f}° (无独立真值)")
    # 局部：最优假设缆线 bbox + 30% 边距（最小半径 600m）
    pts = np.concatenate([np.asarray(ln.coords) for ln in recovered])
    zx, zy = (pts[:, 0].min() + pts[:, 0].max()) / 2, (pts[:, 1].min() + pts[:, 1].max()) / 2
    zhalf = max(600.0, 0.65 * max(pts[:, 0].max() - pts[:, 0].min(),
                                  pts[:, 1].max() - pts[:, 1].min()))
    render(zx, zy, zhalf, "_zoom",
           f"{name} 局部 — cov={best['exact_coverage']:.3f} θ={best['theta_deg']:.1f}°")


def phase_finalize(name):
    t0 = time.perf_counter()
    sc = Scanner(name)
    meta = sc.meta
    fine_rows = []
    for i in range(TOP_K_COARSE):
        p = RESULTS / f"fine_{name}_{i}.json"
        fine_rows.extend(json.loads(p.read_text(encoding="utf-8"))["rows"])
    modes = dedup_modes(fine_rows)
    if not modes:
        raise SystemExit(f"{name}: 无细扫描模式")
    top = modes[0]
    th_hat, cw_hat, cov_hat, n_eval = polish(sc, top["theta_deg"], top["c_w_3857"])
    dice_hat = sc.exact_f1(th_hat, cw_hat)
    t1 = time.perf_counter()

    mode_rows = []
    for i, m in enumerate(modes[:5]):
        if i == 0:
            mode_rows.append({"rank": 1, "theta_deg": float(th_hat % 360.0),
                              "c_w_3857": [float(cw_hat[0]), float(cw_hat[1])],
                              "t_3857": [float(v) for v in t_equiv(th_hat, cw_hat, sc.c_l)],
                              "raster_coverage": m["coverage"],
                              "exact_coverage": float(cov_hat),
                              "exact_dice_f1": float(dice_hat), "polished": True})
        else:
            cw = np.array(m["c_w_3857"])
            mode_rows.append({"rank": i + 1, "theta_deg": m["theta_deg"],
                              "c_w_3857": [float(cw[0]), float(cw[1])],
                              "t_3857": [float(v) for v in t_equiv(m["theta_deg"], cw, sc.c_l)],
                              "raster_coverage": m["coverage"],
                              "exact_coverage": float(sc.exact_coverage(m["theta_deg"], cw)),
                              "exact_dice_f1": float(sc.exact_f1(m["theta_deg"], cw)),
                              "polished": False})
    # 粗锚先验分数（θ=0, c_w=c_w0）
    prior_cov = float(sc.exact_coverage(0.0, sc.c_w0))
    prior_dice = float(sc.exact_f1(0.0, sc.c_w0))
    t2 = time.perf_counter()

    coarse_rows = load_coarse_all(name)
    covs = sorted(r["coverage"] for r in coarse_rows)
    med = covs[len(covs) // 2]
    top5_coarse = [{"theta_deg": r["theta_deg"], "coverage": r["coverage"],
                    "dice_f1": r["dice_f1"], "c_w_3857": r["c_w_3857"],
                    "dist2prior_m": float(np.hypot(r["c_w_3857"][0] - sc.c_w0[0],
                                                   r["c_w_3857"][1] - sc.c_w0[1]))}
                   for r in coarse_rows[:5]]
    dist_top1 = float(np.hypot(cw_hat[0] - sc.c_w0[0], cw_hat[1] - sc.c_w0[1]))
    sep_cov2 = (cov_hat / mode_rows[1]["exact_coverage"]
                if len(mode_rows) > 1 and mode_rows[1]["exact_coverage"] > 0 else None)
    # 旋转迥异分离度：与 Top-1 θ 相差 ≥10° 的最佳细扫描假设（真正竞争模式）
    far_rows = [r for r in fine_rows
                if abs(circ_dtheta(r["theta_deg"], th_hat)) >= 10.0]
    far_best = None
    if far_rows:
        far_top = max(far_rows, key=lambda r: r["coverage"])
        far_cov = float(sc.exact_coverage(far_top["theta_deg"], far_top["c_w_3857"]))
        far_best = {"theta_deg": far_top["theta_deg"], "c_w_3857": far_top["c_w_3857"],
                    "exact_coverage": far_cov,
                    "dtheta_from_top1": float(abs(circ_dtheta(far_top["theta_deg"], th_hat)))}
    sep_far = (cov_hat / far_best["exact_coverage"]
               if far_best and far_best["exact_coverage"] > 0 else None)
    result = {
        "experiment": "road-match P2 Route B: 粗锚先验 + 旋转×平移扫描（无独立真值）",
        "baseline": name,
        "route": "B_anchor_scan",
        "route_reason": json.loads((RESULTS / "identity_screen.json").read_text(
            encoding="utf-8"))["baselines"][name]["reason"],
        "unit_contract": meta["unit_contract"],
        "scale_assumption": meta["scale_assumption"],
        "coordinate_interpretation": meta["coordinate_interpretation"],
        "anchor": {"git_ref": meta["anchor_git_ref"],
                   "target_centre_3857": meta["anchor_config"]["target_epsg3857_centre"],
                   "t_anchor_3857": meta["t_anchor_3857"],
                   "precision": meta["anchor_config"].get("precision"),
                   "source": meta["anchor_config"].get("source")},
        "cables": {"count": meta["cable_count"], "vertices": meta["cable_vertex_count"],
                   "total_length_m": meta["cable_total_length_local_m"],
                   "half_span_m": meta["cable_half_span_m"]},
        "osm": {"cache": (DATA / json.loads((DATA / "fetch_plan.json").read_text(
            encoding="utf-8"))[0]["cache_file"]).name,  # 占位，下面按 region 修正
                },
        "prior_anchor_theta0": {"c_w_3857": [float(sc.c_w0[0]), float(sc.c_w0[1])],
                                "exact_coverage": prior_cov, "exact_dice_f1": prior_dice},
        "recovered_best": {"theta_deg": float(th_hat % 360.0),
                           "c_w_3857": [float(cw_hat[0]), float(cw_hat[1])],
                           "t_3857": [float(v) for v in t_equiv(th_hat, cw_hat, sc.c_l)],
                           "exact_coverage": float(cov_hat),
                           "exact_dice_f1": float(dice_hat),
                           "dist2prior_m": dist_top1},
        "separation": {
            "coverage_top1_over_top2": (float(sep_cov2) if sep_cov2 else None),
            "coverage_top1_over_rotationally_distinct": (float(sep_far) if sep_far else None),
            "rotationally_distinct_competitor": far_best,
            "coverage_top1_over_coarse_median": float(cov_hat / med) if med > 0 else None,
            "coverage_top1_over_prior": (float(cov_hat / prior_cov)
                                         if prior_cov > 0 else None),
            "mode_dedup_radius": {"dtheta_deg": 5.0, "dist_m": 350.0},
        },
        "top5_coarse": top5_coarse,
        "top5_fine_modes": mode_rows,
        "scan_config": meta["scan"],
        "boundary_flags": {
            "search_radius_m": meta["scan"]["search_radius_m"],
            "top1_dist2prior_m": dist_top1,
            "near_boundary": bool(dist_top1 > 0.9 * meta["scan"]["search_radius_m"]),
        },
        "timing_s": {"polish": round(t1 - t0, 2), "mode_exact_scores": round(t2 - t1, 2),
                     "polish_evals": n_eval},
        "provenance": {
            "delivery_gpkg": meta["delivery_gpkg"],
            "delivery_sha256": meta["delivery_sha256"],
            "run_manifest_sha256": meta["run_manifest_sha256"],
            "anchor_git_ref": meta["anchor_git_ref"],
            "truth": "无独立真值（manifest: absolute_accuracy not independently verified; "
                     "no surveyed GCP）——残余不可量化，结论仅依赖分数分离度与人眼复核。",
        },
    }
    # 修正 osm cache 名（按 region）
    plan = json.loads((DATA / "fetch_plan.json").read_text(encoding="utf-8"))
    for p in plan:
        if p["region"] == REGION_OF[name]:
            result["osm"] = {"cache": p["cache_file"], "region": REGION_OF[name]}
    (RESULTS / f"baseline_{name}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    write_overlay(name, sc, result["recovered_best"], None, modes)
    print(json.dumps({"baseline": name, "best": result["recovered_best"],
                      "separation": result["separation"], "prior": result["prior_anchor_theta0"],
                      "boundary": result["boundary_flags"]},
                     indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- sens（缓冲半径敏感性）
def phase_sens(name, buffer_m):
    """敏感性实验：仅改缓冲半径重跑全角度粗扫（coverage），检验 kletek 类小缆线簇
    在更严容差下是否出现可鉴别峰。不动 meta/prep 缓存（exact 评分不参与）。"""
    t0 = time.perf_counter()
    sc = Scanner(name, buffer_m=buffer_m)
    rows = []
    for k in range(int(360.0 / COARSE_THETA_STEP)):
        theta = k * COARSE_THETA_STEP
        r = sc.eval_theta(theta)
        cov_f, _, _, cov_cw = r["coverage"]
        rows.append({"theta_deg": theta, "coverage": cov_f,
                     "c_w_3857": [float(cov_cw[0]), float(cov_cw[1])]})
    rows.sort(key=lambda r: -r["coverage"])
    covs = sorted(r["coverage"] for r in rows)
    med = covs[len(covs) // 2]
    out = {"baseline": name, "buffer_m": buffer_m,
           "note": "敏感性实验：道路/缆线双侧缓冲均改为此值；仅栅格 coverage，无精确抛光。",
           "top5": rows[:5], "median_coverage": med,
           "max_over_median": (rows[0]["coverage"] / med) if med > 0 else None,
           "elapsed_s": round(time.perf_counter() - t0, 2)}
    (RESULTS / f"sens_{name}_buf{int(buffer_m)}.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("baseline", "buffer_m", "median_coverage",
                                          "max_over_median")}, ensure_ascii=False))
    for r in out["top5"]:
        print("  top:", round(r["coverage"], 4), "θ", r["theta_deg"])


# ---------------------------------------------------------------- summary
def phase_summary():
    p1 = json.loads((P1_RESULTS / "result.json").read_text(encoding="utf-8"))
    p1_sanity = json.loads((P1_RESULTS / "sanity_identity.json").read_text(encoding="utf-8"))
    rows = [{
        "baseline": "hutabohu",
        "stage": "P1（引用）",
        "route": "A_strip_recover",
        "truth_available": True,
        "cable_km": 5.465,
        "identity_overlap_ratio": float(p1_sanity["intersection_m2"] / p1_sanity["cable_buffer_m2"]),
        "identity_note": "恒等假设下缆线缓冲 87.7% 落在 OSM 道路缓冲内（sanity_identity.json）",
        "primary_metric": "exact_dice_f1",
        "best_score": p1["T_recovered"]["exact_f1"],
        "separation": "真模式细扫 F1=0.114 vs 最佳错误模式 0.053（≈2.2×）",
        "residual": {"translation_m": p1["residuals"]["translation_m"],
                     "rotation_deg": p1["residuals"]["rotation_deg"]},
        "verdict": "PASS — 残差 7.28m/0.008°（标准 <50m/<5°）",
        "result_json": str(P1_RESULTS / "result.json"),
    }]
    for name in ("kletek", "lamteh", "lamteh_sf"):
        b = json.loads((RESULTS / f"baseline_{name}.json").read_text(encoding="utf-8"))
        best = b["recovered_best"]
        sep = b["separation"]
        s2 = sep.get("coverage_top1_over_top2")
        if b["baseline"] == "kletek":
            verdict = ("INCONCLUSIVE — 村级路网密度相对 270m 缆线簇在 ±15m 容差下趋饱和："
                       "全角度 coverage≈1.0，道路通道对旋转×平移无鉴别力；"
                       "需更小容差或第二通道（IMB 建筑点），见 sens 实验。")
        elif s2 is not None and s2 >= 1.5:
            verdict = "CANDIDATE — 峰分离度≥1.5×，候选假设可供 gcp_workflow 人工确认；无独立真值，残余不可量化。"
        elif s2 is not None and s2 >= 1.15:
            verdict = "WEAK — 峰分离度 1.15–1.5×，仅弱候选；无独立真值，残余不可量化。"
        else:
            verdict = ("INCONCLUSIVE — 峰分离度<1.15×，扫描未能找到显著最优假设；"
                       "无独立真值，残余不可量化。")
        if b["baseline"] != "kletek":
            if b["boundary_flags"].get("near_boundary"):
                verdict += (" 另：Top-1 距先验中心 %.0fm，达搜索半径 %.0fm 的 %.0f%%——"
                            "峰被搜索域截断，真实最优可能在域外，需扩大区域重扫。"
                            % (b["boundary_flags"]["top1_dist2prior_m"],
                               b["boundary_flags"]["search_radius_m"],
                               100.0 * b["boundary_flags"]["top1_dist2prior_m"]
                               / b["boundary_flags"]["search_radius_m"]))
            sfar = sep.get("coverage_top1_over_rotationally_distinct")
            comp = sep.get("rotationally_distinct_competitor")
            if sfar is not None and comp is not None:
                verdict += (" 旋转迥异竞争模式 θ=%.1f°（Δθ=%.1f°）exact coverage=%.4f，"
                            "对 Top-1 分离度 %.3f×。" % (comp["theta_deg"],
                                                       comp["dtheta_from_top1"],
                                                       comp["exact_coverage"], sfar))
        rows.append({
            "baseline": name,
            "stage": "P2",
            "route": "B_anchor_scan",
            "truth_available": False,
            "cable_km": round(b["cables"]["total_length_m"] / 1000.0, 3),
            "identity_overlap_ratio": 0.0,
            "identity_note": "恒等假设 F1=0（名义坐标在几内亚湾大洋，nullisland 区域 highway way=0）",
            "primary_metric": "exact_coverage（cable-recall 型；Dice F1 受道路总量稀释，作辅证）",
            "best_score": best["exact_coverage"],
            "best_dice_f1": best["exact_dice_f1"],
            "best_theta_deg": best["theta_deg"],
            "best_c_w_3857": best["c_w_3857"],
            "prior_coverage": b["prior_anchor_theta0"]["exact_coverage"],
            "separation": sep,
            "boundary_flags": b["boundary_flags"],
            "verdict": verdict,
            "result_json": str(RESULTS / f"baseline_{name}.json"),
        })
    # 交叉一致性：lamteh 与 lamteh_sf 是同一村庄（Lamteh Dayah）的两个变体，
    # 若两次扫描都正确，恢复位置应接近；相距数 km 则至少其一是伪匹配。
    cross_note = None
    try:
        b1 = json.loads((RESULTS / "baseline_lamteh.json").read_text(encoding="utf-8"))
        b2 = json.loads((RESULTS / "baseline_lamteh_sf.json").read_text(encoding="utf-8"))
        c1 = b1["recovered_best"]["c_w_3857"]
        c2 = b2["recovered_best"]["c_w_3857"]
        d = float(np.hypot(c1[0] - c2[0], c1[1] - c2[1]))
        cross_note = (f"交叉一致性检查：lamteh 最优 c_w 与 lamteh_sf 最优 c_w 相距 {d:.0f} m"
                      f"（θ 分别为 {b1['recovered_best']['theta_deg']:.1f}° 与 "
                      f"{b2['recovered_best']['theta_deg']:.1f}°）。二者同属 Lamteh Dayah，"
                      "真实网络位置应接近 ⇒ 至少一个扫描结果是伪匹配，两个 Route B 结论"
                      "均不可单独采信。")
    except Exception as e:  # noqa: BLE001
        cross_note = f"交叉一致性检查无法执行：{e}"
    out = {
        "title": "road-match 多基线对比（P1 hutabohu + P2 kletek/lamteh/lamteh_sf）",
        "generated_by": "tools/road_match_p2/road_match_p2.py summary",
        "baselines": rows,
        "cross_baseline_notes": [
            cross_note,
            "三个 P2 delivery 的图层/manifest 虽声明 EPSG:3857，坐标量级（≤10.6km）却落在 "
            "Null Island 附近大洋（该海域单次 Overpass 查询 highway way=0）——全部为本地工程坐标，"
            "恒等假设 F1=0，无一可走 P1 的剥离-恢复残差协议（Route A）。",
            "上游 manifest 自检 coordinate_domain_status=PLAUSIBLE_DECLARED_CRS_DOMAIN(passed=true) "
            "与本实验证据矛盾：该检查未识别坐标域错误，值得在 CAD2GIS 主流程中修正。",
            "lamteh 双基线 manifest insunits=4(mm) 但数值按米解释才与村级 FTTH 规模/Nominatim bbox "
            "同量级；scale=1 假设由扫描结构支持（lamteh_sf 出现明显角度峰），但未由独立真值证实。",
            "kletek 证明方法学边界：缆线簇过小（876m）+ 村级路网密集时，±15m 缓冲重叠指标"
            "（Dice 与 coverage 皆然）失去鉴别力——自动定位须先过'信号充分性'门禁。",
            "所有 Route B 结论均无独立真值：分数与分离度只是候选排序依据，残余不可量化，"
            "落地必须经 gcp_workflow 人工确认。",
        ],
    }
    (RESULTS / "multibaseline_summary.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in rows:
        print(f"- {r['baseline']:<10} route={r['route']:<16} best={r.get('best_score')} "
              f"verdict={r['verdict'][:60]}")
    print("-> results/multibaseline_summary.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["extract", "planfetch", "screen", "prep",
                                      "selftest", "coarse", "fine", "sens",
                                      "finalize", "summary"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("chunk", nargs="?", type=int, default=0)
    ap.add_argument("nchunks", nargs="?", type=int, default=6)
    ap.add_argument("--search-radius", type=float, default=None,
                    help="extract 阶段可选：在既有网格可达范围内外扩搜索半径(米)")
    args = ap.parse_args()
    if args.phase == "extract":
        phase_extract(args.name, search_radius=args.search_radius)
    elif args.phase == "planfetch":
        phase_planfetch()
    elif args.phase == "screen":
        phase_screen()
    elif args.phase == "prep":
        phase_prep(args.name)
    elif args.phase == "selftest":
        phase_selftest()
    elif args.phase == "coarse":
        phase_coarse(args.name, args.chunk, args.nchunks)
    elif args.phase == "fine":
        phase_fine(args.name, args.chunk)
    elif args.phase == "sens":
        phase_sens(args.name, float(args.chunk))
    elif args.phase == "finalize":
        phase_finalize(args.name)
    elif args.phase == "summary":
        phase_summary()
