# -*- coding: utf-8 -*-
"""road_match_p1.py — CAD2GIS 路网匹配自动定位 P1 原型。

验证命题：FTTH 缆线（CABLE）沿道路敷设 => 可用"缓冲带重叠 F1 + 网格扫描"
从 OSM 路网中恢复转换产物的相似变换定位。

协议（对应任务书）：
  extract   读 delivery CABLE（EPSG:9481，只读）→ 变换到 EPSG:3857 真值框架；
            输出 data/cables_truth_3857.json 与 data/truth_meta.json（含 Overpass bbox）。
  selftest  验证 FFT 互相关平移映射的正确性（合成图案，已知偏移）。
  sanity    恒等假设检验：真值位置缆线 vs OSM 道路的精确 F1（shapely），
            回答"该基线名义位置是否真的沿 OSM 路网"（证据，不粉饰）。
  simulate  生成模拟本地坐标系：local = R(-θ_true)·(truth - t_true)，
            T_true 由固定种子产生（平移几百米 + 旋转几十度 + 比例=1.0）。
  scan      网格扫描恢复：θ 0–355° 步 5°；平移由 FFT 互相关在锚点 bbox
            （真值中心 ±1200m）内全分辨率评估（优于 50m 步长，见 README）；
            Top-3 细扫描（θ±5° 步 1°）+ 亚像素抛物线拟合 + 精确 F1 爬山抛光。
  all       extract → (检查 OSM 缓存) → sanity → simulate → scan。

运行：conda run -n cad2gis python tools/road_match_p1/road_match_p1.py <phase>
确定性：所有随机量来自 SEED；Overpass 结果由 fetch_osm.py 缓存复用。
"""
import argparse
import hashlib
import json
import math
import pathlib
import time

import numpy as np
import pyproj
import shapely
import shapely.wkb
from osgeo import ogr
from PIL import Image, ImageDraw

# ---------------------------------------------------------------- 常量与路径
HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
DELIVERY_GPKG = r"E:\branch_CAD2GIS\CAD2GIS\.worktrees\robustness\baselines\apd_hutabohu\delivery\apd_delivery.gpkg"
RUN_MANIFEST = r"E:\branch_CAD2GIS\CAD2GIS\.worktrees\robustness\baselines\apd_hutabohu\run_manifest.json"

SEED = 20240818                 # 固定随机种子（写死，保证可复现）
RES = 5.0                       # 栅格分辨率 m/px
GRID_RADIUS = 3000.0            # 栅格半径（真值中心 ±3km）
SEARCH_RADIUS = 1200.0          # 平移搜索半径（锚点 bbox，真值中心 ±1.2km）
BUFFER_M = 15.0                 # 双侧缓冲带宽度 m
COARSE_THETA_STEP = 5.0         # 粗扫描角度步长 °
FINE_THETA_RANGE = 5.0          # 细扫描角度范围 ±°
FINE_THETA_STEP = 1.0           # 细扫描角度步长 °
TOP_K_COARSE = 3                # 进入细扫描的粗扫描假设数
FFT_SIZE = 2560                 # 相关运算零-padding 尺寸（≥ N + M ≈ 1200+900）

TO_3857 = pyproj.Transformer.from_crs("EPSG:9481", "EPSG:3857", always_xy=True)
TO_4326 = pyproj.Transformer.from_crs("EPSG:9481", "EPSG:4326", always_xy=True)
FROM_4326 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
FROM_3857 = pyproj.Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


# ---------------------------------------------------------------- 工具函数
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rot_mat(deg):
    r = math.radians(deg)
    return np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])


def apply_sim(pts, theta_deg, t):
    """相似变换（scale=1）：world = R(θ)·pts + t。pts: (n,2) ndarray。"""
    return pts @ rot_mat(theta_deg).T + np.asarray(t, dtype=float)


def load_cables_9481():
    """用 GDAL/OGR 只读打开 delivery，取 CABLE 图层线几何（EPSG:9481 坐标）。"""
    ds = ogr.Open(DELIVERY_GPKG, 0)  # 0 = read-only
    if ds is None:
        raise RuntimeError(f"无法打开 {DELIVERY_GPKG}")
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


def transform_lines(lines, transformer):
    out = []
    for ln in lines:
        xy = np.asarray(ln.coords, dtype=float)
        x, y = transformer.transform(xy[:, 0], xy[:, 1])
        out.append(shapely.LineString(np.column_stack([x, y])))
    return out


def lines_to_points(lines):
    return [np.asarray(ln.coords, dtype=float) for ln in lines]


# ---------------------------------------------------------------- 栅格化
def dilate_disk(mask, r_px):
    """numpy 圆盘膨胀（半径 r_px 像素）。scipy 不可用，用移位 OR 实现。"""
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
    """把线要素（世界/局部坐标）画进 size×size 布尔掩膜并膨胀 buffer_px。
    y 轴向下：world_y = origin_y_max - (iy+0.5)*res。
    """
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


# ---------------------------------------------------------------- 阶段实现
def phase_extract():
    DATA.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    lines = load_cables_9481()
    assert len(lines) == 6, f"预期 6 条 CABLE，实际 {len(lines)}"
    n_vertices = sum(len(ln.coords) for ln in lines)
    length_9481 = sum(ln.length for ln in lines)

    truth = transform_lines(lines, TO_3857)  # 真值框架：EPSG:3857 米制
    xs = np.concatenate([np.asarray(ln.coords)[:, 0] for ln in truth])
    ys = np.concatenate([np.asarray(ln.coords)[:, 1] for ln in truth])
    bbox = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
    center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]

    # Overpass 查询 bbox：真值中心外扩约 2km（按纬度换算到 4326）
    lon_c, lat_c = FROM_3857.transform(center[0], center[1])
    d_lat = 2000.0 / 111320.0
    d_lon = 2000.0 / (111320.0 * math.cos(math.radians(lat_c)))
    over_bbox = [lat_c - d_lat, lon_c - d_lon, lat_c + d_lat, lon_c + d_lon]  # S W N E

    # 缆线端点 4326 样本（人工核对用）
    lon0, lat0 = FROM_3857.transform(xs[0], ys[0])

    meta = {
        "source_delivery_gpkg": DELIVERY_GPKG,
        "source_delivery_sha256": sha256_of(DELIVERY_GPKG),
        "run_manifest": RUN_MANIFEST,
        "run_manifest_sha256": sha256_of(RUN_MANIFEST),
        "delivery_crs": "EPSG:9481 (SRGI2013 / UTM zone 51N)",
        "cable_count": len(lines),
        "cable_vertex_count": int(n_vertices),
        "cable_total_length_epsg9481_m": float(length_9481),
        "truth_frame": "EPSG:3857",
        "truth_bbox_3857": bbox,
        "truth_center_3857": center,
        "truth_center_4326": [float(lon_c), float(lat_c)],
        "cable_first_vertex_4326": [float(lon0), float(lat0)],
        "overpass_bbox_4326": [float(v) for v in over_bbox],
        "note": "manifest 声明 absolute_accuracy 未经独立验证；本原型把 delivery 名义位置当作实验真值，"
                "sanity 阶段用 OSM 重叠独立检验该前提。",
    }
    (DATA / "truth_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    payload = {
        "crs": "EPSG:3857",
        "cables": [[list(map(float, c)) for c in ln.coords] for ln in truth],
    }
    (DATA / "cables_truth_3857.json").write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in (
        "cable_count", "cable_vertex_count", "cable_total_length_epsg9481_m",
        "truth_bbox_3857", "truth_center_4326", "overpass_bbox_4326")}, indent=2, ensure_ascii=False))


def load_truth_cables():
    payload = json.loads((DATA / "cables_truth_3857.json").read_text(encoding="utf-8"))
    return [shapely.LineString(c) for c in payload["cables"]]


def find_osm_cache():
    caches = sorted(DATA.glob("overpass_way_highway_*.json"))
    if not caches:
        raise FileNotFoundError("缺少 Overpass 缓存，先运行 fetch_osm.py")
    return caches[0]


def load_roads_3857():
    """解析 Overpass JSON → EPSG:3857 道路 LineString 列表。"""
    cache = find_osm_cache()
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


def exact_f1(cable_lines_world, road_union_buf):
    """精确矢量 F1（Dice）：2·|缆线缓冲 ∩ 道路缓冲| / (|缆线缓冲| + |道路缓冲|)。
    road_union_buf 为已缓冲并 union 的道路几何（PreparedGeometry 之外再算面积）。
    """
    cb = shapely.union_all([ln.buffer(BUFFER_M) for ln in cable_lines_world])
    inter = cb.intersection(road_union_buf).area
    denom = cb.area + road_union_buf.area
    return (2.0 * inter / denom) if denom > 0 else 0.0, float(inter), float(cb.area)


def phase_sanity():
    """恒等假设检验：真值位置缆线与 OSM 道路的精确 F1。"""
    meta = json.loads((DATA / "truth_meta.json").read_text(encoding="utf-8"))
    truth = load_truth_cables()
    roads, cache = load_roads_3857()
    cx, cy = meta["truth_center_3857"]
    domain = shapely.box(cx - GRID_RADIUS, cy - GRID_RADIUS, cx + GRID_RADIUS, cy + GRID_RADIUS)
    roads_clip = [r for r in roads if r.intersects(domain)]
    road_buf = shapely.union_all([r.intersection(domain).buffer(BUFFER_M) for r in roads_clip])
    f1, inter, carea = exact_f1(truth, road_buf)
    out = {
        "osm_cache": str(cache),
        "osm_way_count": len(roads),
        "osm_way_in_domain": len(roads_clip),
        "domain": f"truth_center ± {GRID_RADIUS} m (EPSG:3857)",
        "buffer_m": BUFFER_M,
        "identity_f1": f1,
        "intersection_m2": inter,
        "cable_buffer_m2": carea,
        "road_buffer_m2": float(road_buf.area),
        "interpretation": "F1≈0 说明基线名义位置未与 OSM 路网配准（或缆线不沿路）；"
                          "F1 越高，‘缆线沿路’前提越强。",
    }
    (RESULTS / "sanity_identity.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


def phase_selftest():
    """合成验证：把已知图案按已知偏移放进 A，检查互相关峰 = 偏移（±1px）。"""
    rng = np.random.default_rng(SEED)
    N = 400
    A = np.zeros((N, N), dtype=bool)
    for _ in range(30):  # 随机折线当“道路”
        x0, y0 = rng.uniform(20, N - 20, 2)
        x1, y1 = rng.uniform(20, N - 20, 2)
        img = Image.new("L", (N, N), 0)
        ImageDraw.Draw(img).line([x0, y0, x1, y1], fill=255, width=1)
        A |= np.asarray(img, dtype=bool)
    M = 120
    img = Image.new("L", (M, M), 0)
    ImageDraw.Draw(img).line([10, 10, M - 10, M // 2, M // 2, M - 10], fill=255, width=1)
    B = np.asarray(img, dtype=bool)
    off = (173, 88)  # 已知放置偏移 (iy, ix)
    A[off[0]:off[0] + M, off[1]:off[1] + M] |= B

    S = FFT_SIZE
    FA = np.fft.fft2(A.astype(float), s=(S, S))
    FB = np.fft.fft2(B.astype(float), s=(S, S))
    C = np.fft.ifft2(FA * np.conj(FB)).real
    iy, ix = np.unravel_index(np.argmax(C[: N - M, : N - M]), C[: N - M, : N - M].shape)
    ok = (iy, ix) == off
    print(f"selftest: recovered offset=({iy},{ix}) expected={off} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        raise SystemExit("互相关平移映射自检失败，停止。")


def simulate_local_frame(truth):
    """生成模拟本地坐标系与 T_true（固定种子）。

    约定：T_true: local → truth，p_truth = R(θ_true)·p_local + t_true，scale=1.0。
    即 local = R(-θ_true)·(truth - t_true)。
    """
    rng = np.random.default_rng(SEED)
    meta = json.loads((DATA / "truth_meta.json").read_text(encoding="utf-8"))
    anchor = np.array(meta["truth_center_3857"], dtype=float)  # 锚点 = 真值缆线 bbox 中心
    theta_true = float(rng.uniform(20.0, 60.0))                 # 旋转几十度
    delta = float(rng.uniform(300.0, 600.0))                    # 平移几百米
    ang = float(rng.uniform(0.0, 2.0 * math.pi))
    t_true = anchor + delta * np.array([math.cos(ang), math.sin(ang)])
    scale_true = 1.0                                            # P1 固定比例 1.0（扫描不搜比例）

    local = []
    for ln in truth:
        pts = np.asarray(ln.coords, dtype=float)
        local.append(shapely.LineString((pts - t_true) @ rot_mat(theta_true)))  # R(-θ)·(p - t)
    return local, {
        "seed": SEED,
        "theta_true_deg": theta_true,
        "t_true_3857": [float(t_true[0]), float(t_true[1])],
        "scale_true": scale_true,
        "anchor_3857": [float(anchor[0]), float(anchor[1])],
        "convention": "truth = R(theta) @ local + t ; local = R(-theta) @ (truth - t)",
    }


def correlate_score_map(A_fft, B, S, valid_region):
    """返回互相关图 C（valid_region 外置 -inf）。"""
    FB = np.fft.fft2(B.astype(float), s=(S, S))
    C = np.fft.ifft2(A_fft * np.conj(FB)).real
    C = C[: valid_region[0], : valid_region[1]]
    return C


def phase_scan():
    t0 = time.perf_counter()
    meta = json.loads((DATA / "truth_meta.json").read_text(encoding="utf-8"))
    truth = load_truth_cables()
    roads, cache = load_roads_3857()

    # --- 模拟本地坐标系 ------------------------------------------------
    local, t_meta = simulate_local_frame(truth)
    anchor = np.array(t_meta["anchor_3857"])
    t_true = np.array(t_meta["t_true_3857"])
    theta_true = t_meta["theta_true_deg"]
    local_pts = lines_to_points(local)

    # --- 栅格 A：OSM 道路缓冲 -----------------------------------------
    N = int(2 * GRID_RADIUS / RES)
    x_min, y_max = anchor[0] - GRID_RADIUS, anchor[1] + GRID_RADIUS
    roads_pts = lines_to_points(roads)
    buf_px = BUFFER_M / RES
    A = rasterize_lines(roads_pts, x_min, y_max, RES, N, buf_px)
    areaA = int(A.sum())
    S = FFT_SIZE
    assert N < S, "栅格超过 FFT padding"
    FA = np.fft.fft2(A.astype(float), s=(S, S))

    # --- 缆线 B 掩膜尺寸：覆盖局部系线网对角线 ------------------------
    all_local = np.concatenate(local_pts)
    half_span = float(np.abs(all_local).max()) + BUFFER_M + 4 * RES
    M = int(2 * half_span / RES)
    assert N + M <= S, f"N+M={N+M} 超过 FFT_SIZE={S}"

    def build_B(theta_deg):
        rot = [pts @ rot_mat(theta_deg).T for pts in local_pts]
        return rasterize_lines(rot, -M * RES / 2, M * RES / 2, RES, M, buf_px)

    def map_to_translation(iy, ix):
        """B 左上角像素 (iy,ix) → 局部原点（=假设 t）的世界坐标。"""
        tx = x_min + (ix + M / 2) * RES
        ty = y_max - (iy + M / 2) * RES
        return np.array([tx, ty])

    # 搜索域掩膜：|t - anchor| ≤ SEARCH_RADIUS（且 B 完整落在 A 内）
    iy_grid, ix_grid = np.mgrid[0: N - M, 0: N - M]
    tx_grid = x_min + (ix_grid + M / 2) * RES
    ty_grid = y_max - (iy_grid + M / 2) * RES
    search_mask = ((tx_grid - anchor[0]) ** 2 + (ty_grid - anchor[1]) ** 2) <= SEARCH_RADIUS ** 2

    hypotheses = []  # (score, theta, iy, ix)
    t_coarse0 = time.perf_counter()
    for k in range(int(360.0 / COARSE_THETA_STEP)):
        theta = k * COARSE_THETA_STEP
        B = build_B(theta)
        areaB = int(B.sum())
        C = correlate_score_map(FA, B, S, (N - M, N - M))
        score_map = np.where(search_mask, 2.0 * C / (areaA + areaB), -np.inf)
        iy, ix = np.unravel_index(int(np.argmax(score_map)), score_map.shape)
        hypotheses.append((float(score_map[iy, ix]), float(theta), int(iy), int(ix)))
        if k % 12 == 0:
            print(f"  coarse θ={theta:6.1f}°  best F1={score_map[iy, ix]:.4f} @ t={map_to_translation(iy, ix)}")
    t_coarse1 = time.perf_counter()
    hypotheses.sort(key=lambda h: -h[0])
    top5_coarse = hypotheses[:5]
    print(f"coarse done in {t_coarse1 - t_coarse0:.1f}s; top5={[(round(s,4), th) for s, th, _, _ in top5_coarse]}")

    # --- 细扫描：Top-3 假设 θ±5° 步 1° ---------------------------------
    t_fine0 = time.perf_counter()
    refined = []
    for s0, th0, _, _ in hypotheses[:TOP_K_COARSE]:
        for dth in np.arange(-FINE_THETA_RANGE, FINE_THETA_RANGE + 1e-9, FINE_THETA_STEP):
            theta = (th0 + dth) % 360.0
            B = build_B(theta)
            areaB = int(B.sum())
            C = correlate_score_map(FA, B, S, (N - M, N - M))
            score_map = np.where(search_mask, 2.0 * C / (areaA + areaB), -np.inf)
            iy, ix = np.unravel_index(int(np.argmax(score_map)), score_map.shape)
            # 亚像素：3×3 抛物线拟合
            dy = dx = 0.0
            if 0 < iy < score_map.shape[0] - 1 and 0 < ix < score_map.shape[1] - 1:
                f0 = score_map[iy, ix]
                fy = (score_map[iy + 1, ix] - score_map[iy - 1, ix]) / 2
                fyy = score_map[iy + 1, ix] - 2 * f0 + score_map[iy - 1, ix]
                fx = (score_map[iy, ix + 1] - score_map[iy, ix - 1]) / 2
                fxx = score_map[iy, ix + 1] - 2 * f0 + score_map[iy, ix - 1]
                if fyy < 0:
                    dy = float(np.clip(-fy / fyy, -1, 1))
                if fxx < 0:
                    dx = float(np.clip(-fx / fxx, -1, 1))
            t_hat = map_to_translation(iy + dy, ix + dx)
            refined.append({"theta_deg": theta, "t_3857": t_hat, "raster_f1": float(score_map[iy, ix])})
    refined.sort(key=lambda h: -h["raster_f1"])
    # 去重：Top-3 粗峰可能收敛到同一细峰（按 θ+平移 10m 精度去重）
    seen, dedup = set(), []
    for h in refined:
        key = (round(h["theta_deg"], 3), round(h["t_3857"][0] / 10), round(h["t_3857"][1] / 10))
        if key not in seen:
            seen.add(key)
            dedup.append(h)
    refined = dedup
    t_fine1 = time.perf_counter()

    # --- 精确 F1 爬山抛光（shapely，亚栅格精度） -----------------------
    roads_in_domain = [r for r in roads if r.intersects(
        shapely.box(x_min, anchor[1] - GRID_RADIUS, x_min + 2 * GRID_RADIUS, anchor[1] + GRID_RADIUS))]
    road_buf = shapely.union_all([r.buffer(BUFFER_M) for r in roads_in_domain])
    road_area = float(road_buf.area)

    def exact(theta, t):
        moved = [shapely.LineString(apply_sim(pts, theta, t)) for pts in local_pts]
        cb = shapely.union_all([ln.buffer(BUFFER_M) for ln in moved])
        inter = cb.intersection(road_buf).area
        return 2.0 * inter / (cb.area + road_area)

    best = refined[0]
    th_hat, t_hat = best["theta_deg"], np.array(best["t_3857"])
    step_m, step_deg = 5.0, 0.5
    cur = exact(th_hat, t_hat)
    while step_m >= 1.0:
        improved = False
        for dth in (0.0, step_deg, -step_deg):
            for dt in ((0, 0), (step_m, 0), (-step_m, 0), (0, step_m), (0, -step_m),
                       (step_m, step_m), (step_m, -step_m), (-step_m, step_m), (-step_m, -step_m)):
                cand_th, cand_t = th_hat + dth, t_hat + np.array(dt, dtype=float)
                f = exact(cand_th, cand_t)
                if f > cur + 1e-9:
                    cur, th_hat, t_hat, improved = f, cand_th, cand_t, True
        if not improved:
            step_m /= 2.5
            step_deg /= 2.5
    t_polish1 = time.perf_counter()

    # --- 残差与结果 ----------------------------------------------------
    dtheta = (th_hat - theta_true + 180.0) % 360.0 - 180.0
    result = {
        "experiment": "road-match P1: buffer-overlap F1 + grid scan recovers known similarity transform",
        "seed": SEED,
        "T_true": {
            "theta_deg": theta_true,
            "t_3857": [float(t_true[0]), float(t_true[1])],
            "scale": t_meta["scale_true"],
            "convention": t_meta["convention"],
        },
        "T_recovered": {
            "theta_deg": float(th_hat % 360.0),
            "t_3857": [float(t_hat[0]), float(t_hat[1])],
            "scale": 1.0,
            "exact_f1": float(cur),
        },
        "residuals": {
            "translation_m": float(np.hypot(*(t_hat - t_true))),
            "rotation_deg": float(dtheta),
            "rotation_deg_abs": float(abs(dtheta)),
        },
        "success_criteria": {
            "translation_residual_lt_50m": bool(np.hypot(*(t_hat - t_true)) < 50.0),
            "rotation_residual_lt_5deg": bool(abs(dtheta) < 5.0),
        },
        "top5_coarse": [
            {"theta_deg": th, "t_3857": [float(v) for v in map_to_translation(iy, ix)],
             "raster_f1": s}
            for s, th, iy, ix in top5_coarse],
        "top5_fine": [
            {"theta_deg": h["theta_deg"], "t_3857": [float(v) for v in h["t_3857"]],
             "raster_f1": h["raster_f1"]}
            for h in refined[:5]],
        "scan_config": {
            "res_m_per_px": RES, "grid_radius_m": GRID_RADIUS, "search_radius_m": SEARCH_RADIUS,
            "buffer_m": BUFFER_M, "coarse_theta_step_deg": COARSE_THETA_STEP,
            "fine_theta_range_deg": FINE_THETA_RANGE, "fine_theta_step_deg": FINE_THETA_STEP,
            "fft_size": S, "grid_px": N, "template_px": M,
            "translation_eval": "FFT 互相关，在搜索域内对全部 5m 像素平移评分（优于 50m 步长）",
        },
        "timing_s": {
            "coarse_scan": round(t_coarse1 - t_coarse0, 2),
            "fine_scan": round(t_fine1 - t_fine0, 2),
            "exact_polish": round(t_polish1 - t_fine1, 2),
            "total_scan": round(t_polish1 - t0, 2),
        },
        "provenance": {
            "delivery_gpkg": DELIVERY_GPKG,
            "delivery_sha256": meta["source_delivery_sha256"],
            "osm_cache": str(cache),
            "truth_frame": "EPSG:3857",
        },
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- 可视化：overlay.geojson（4326）+ overlay.svg ------------------
    write_overlay(truth, local, local_pts, th_hat, t_hat, anchor, result, roads_in_domain)
    print(json.dumps({
        "T_true": result["T_true"], "T_recovered": result["T_recovered"],
        "residuals": result["residuals"], "criteria": result["success_criteria"],
        "timing_s": result["timing_s"],
    }, indent=2, ensure_ascii=False))


def write_overlay(truth, local, local_pts, th_hat, t_hat, anchor, result, roads):
    """输出 overlay.geojson（EPSG:4326）与 overlay.svg（无需 matplotlib）。"""
    def to4326(lines):
        feats = []
        for ln in lines:
            xy = np.asarray(ln.coords)
            lon, lat = FROM_3857.transform(xy[:, 0], xy[:, 1])
            feats.append([[float(a), float(b)] for a, b in zip(lon, lat)])
        return feats

    recovered = [shapely.LineString(apply_sim(pts, th_hat, t_hat)) for pts in local_pts]
    feats = []
    for name, lines, color in (
            ("osm_road", roads, "#999999"),
            ("cable_truth", truth, "#1a9850"),
            ("cable_recovered", recovered, "#d73027"),
            ("cable_local_at_anchor", [shapely.LineString(pts + anchor) for pts in local_pts], "#4575b4")):
        for coords in to4326(lines):
            feats.append({"type": "Feature", "properties": {"layer": name, "stroke": color},
                          "geometry": {"type": "LineString", "coordinates": coords}})
    for i, h in enumerate(result["top5_coarse"]):
        lon, lat = FROM_3857.transform(h["t_3857"][0], h["t_3857"][1])
        feats.append({"type": "Feature",
                      "properties": {"layer": "hypothesis_top5", "rank": i + 1,
                                     "theta_deg": h["theta_deg"], "raster_f1": h["raster_f1"]},
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}})
    for name, pt in (("anchor_truth_center", anchor),
                     ("T_true_t", np.array(result["T_true"]["t_3857"])),
                     ("T_recovered_t", np.array(result["T_recovered"]["t_3857"]))):
        lon, lat = FROM_3857.transform(pt[0], pt[1])
        feats.append({"type": "Feature", "properties": {"layer": name},
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]}})
    gj = {"type": "FeatureCollection",
          "properties": {"note": "EPSG:4326; cable_local_at_anchor=模拟本地系几何平移到锚点（未旋转）"},
          "features": feats}
    (RESULTS / "overlay.geojson").write_text(json.dumps(gj), encoding="utf-8")

    # --- SVG：x/y 为 EPSG:3857 相对锚点的米 ---
    W = H = 900
    span = GRID_RADIUS * 2
    def sx(x): return (x - anchor[0]) / span * W + W / 2
    def sy(y): return H / 2 - (y - anchor[1]) / span * H
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" style="background:#faf8f5">']
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#faf8f5"/>')
    def path_of(lines):
        segs = []
        for ln in lines:
            xy = np.asarray(ln.coords)
            d = "M" + " L".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in xy)
            segs.append(d)
        return segs
    for d in path_of(roads):
        parts.append(f'<path d="{d}" stroke="#b0a89f" stroke-width="1.2" fill="none"/>')
    for d in path_of([shapely.LineString(pts + anchor) for pts in local_pts]):
        parts.append(f'<path d="{d}" stroke="#7b9cc4" stroke-width="1.5" fill="none" opacity="0.6"/>')
    for d in path_of(truth):
        parts.append(f'<path d="{d}" stroke="#2e7d52" stroke-width="2.5" fill="none"/>')
    for d in path_of(recovered):
        parts.append(f'<path d="{d}" stroke="#c2452f" stroke-width="2" fill="none" stroke-dasharray="6,4"/>')
    tt = np.array(result["T_true"]["t_3857"]); tr = np.array(result["T_recovered"]["t_3857"])
    parts.append(f'<circle cx="{sx(tt[0]):.1f}" cy="{sy(tt[1]):.1f}" r="7" fill="#2e7d52"/>')
    parts.append(f'<circle cx="{sx(tr[0]):.1f}" cy="{sy(tr[1]):.1f}" r="6" fill="none" stroke="#c2452f" stroke-width="2.5"/>')
    parts.append(f'<circle cx="{W/2}" cy="{H/2}" r="4" fill="#555"/>')
    res = result["residuals"]
    parts.append(f'<text x="14" y="26" font-family="sans-serif" font-size="15" fill="#333">'
                 f'road-match P1 — Δt={res["translation_m"]:.1f} m, Δθ={res["rotation_deg"]:.2f}°, '
                 f'F1={result["T_recovered"]["exact_f1"]:.3f}</text>')
    parts.append(f'<text x="14" y="{H-14}" font-family="sans-serif" font-size="12" fill="#777">'
                 f'灰=OSM道路 绿=真值缆线 红虚=恢复 蓝=本地系@锚点 绿点=T_true.t 红圈=T_rec.t</text>')
    parts.append('</svg>')
    (RESULTS / "overlay.svg").write_text("".join(parts), encoding="utf-8")

    # --- PNG（PIL 渲染，matplotlib 缺失时的可检查图像） ---
    S2 = 4  # 超采样倍数，抗锯齿
    img = Image.new("RGB", (W * S2, H * S2), (250, 248, 245))
    dr = ImageDraw.Draw(img)

    def draw_lines(lines, color, width):
        for ln in lines:
            xy = np.asarray(ln.coords)
            pts = [(sx(p[0]) * S2, sy(p[1]) * S2) for p in xy]
            dr.line(pts, fill=color, width=width * S2)

    draw_lines(roads, (176, 168, 159), 1)                                    # OSM 道路：灰
    draw_lines([shapely.LineString(pts + anchor) for pts in local_pts],
               (123, 156, 196), 1)                                          # 本地系@锚点：蓝
    draw_lines(truth, (46, 125, 82), 3)                                     # 真值缆线：绿
    draw_lines(recovered, (194, 69, 47), 2)                                 # 恢复缆线：红
    for pt, color, r in ((tt, (46, 125, 82), 7), (tr, (194, 69, 47), 6),
                         (anchor, (85, 85, 85), 4)):
        cxp, cyp = sx(pt[0]) * S2, sy(pt[1]) * S2
        dr.ellipse([cxp - r * S2, cyp - r * S2, cxp + r * S2, cyp + r * S2],
                   outline=color, width=2 * S2)
    img = img.resize((W, H), Image.LANCZOS)
    img.save(RESULTS / "overlay.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["extract", "selftest", "sanity", "scan", "all"])
    args = ap.parse_args()
    if args.phase in ("extract", "all"):
        phase_extract()
    if args.phase in ("selftest",):
        phase_selftest()
    if args.phase in ("sanity", "all"):
        phase_sanity()
    if args.phase in ("scan", "all"):
        phase_selftest()
        phase_scan()


if __name__ == "__main__":
    main()
