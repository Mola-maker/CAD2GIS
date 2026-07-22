#!/usr/bin/env python3
"""
Georeference pre-processing for DWG local engineering grids.
See documentation at python/georeference.py (module docstring).
"""
import math

INDONESIA_CRS_CANDIDATES = [
    32646, 32647, 32648, 32649, 32650, 32651, 32652,
    23830, 23831, 23832, 23833, 23834, 23835, 23836, 23837, 23838, 23839,
    23840, 23841, 23842, 23843, 23844, 23845, 23846, 23847, 23848, 23849,
]

def compute_helmert(gcp_pairs):
    if len(gcp_pairs) < 2:
        return None
    n = len(gcp_pairs)
    sum_mx = sum_my = sum_mx2 = sum_my2 = sum_mxmy = 0.0
    sum_lon = sum_lat = sum_lon_mx = sum_lon_my = sum_lat_mx = sum_lat_my = 0.0
    for (mx, my), (lon, lat) in gcp_pairs:
        sum_mx += mx; sum_my += my
        sum_mx2 += mx * mx; sum_my2 += my * my; sum_mxmy += mx * my
        sum_lon += lon; sum_lon_mx += lon * mx; sum_lon_my += lon * my
        sum_lat += lat; sum_lat_mx += lat * mx; sum_lat_my += lat * my
    cx_m = sum_mx / n; cy_m = sum_my / n
    cx_g = sum_lon / n; cy_g = sum_lat / n
    num_a = num_b = denom = 0.0
    for (mx, my), (lon, lat) in gcp_pairs:
        dmx = mx - cx_m; dmy = my - cy_m
        dlon = lon - cx_g; dlat = lat - cy_g
        num_a += dmx * dlon + dmy * dlat
        num_b += dmx * dlat - dmy * dlon
        denom += dmx * dmx + dmy * dmy
    if denom < 1e-12:
        return None
    a = num_a / denom; b = num_b / denom
    scale = math.sqrt(a * a + b * b)
    theta = math.atan2(b, a)
    tx = cx_g - (a * cx_m - b * cy_m)
    ty = cy_g - (b * cx_m + a * cy_m)
    residuals = []
    for (mx, my), (lon, lat) in gcp_pairs:
        pred_lon = a * mx - b * my + tx
        pred_lat = b * mx + a * my + ty
        d_lon_m = (lon - pred_lon) * 111320.0 * math.cos(math.radians(lat))
        d_lat_m = (lat - pred_lat) * 111320.0
        residuals.append(math.sqrt(d_lon_m ** 2 + d_lat_m ** 2))
    rmse = math.sqrt(sum(r * r for r in residuals) / n)
    return {"scale": scale, "theta_rad": theta, "tx": tx, "ty": ty,
            "residuals": residuals, "rmse": rmse}

def apply_helmert(x, y, helmert):
    a = helmert["scale"] * math.cos(helmert["theta_rad"])
    b = helmert["scale"] * math.sin(helmert["theta_rad"])
    return a * x - b * y + helmert["tx"], b * x + a * y + helmert["ty"]

def try_identify_crs(gcp_pairs, candidates=None):
    from osgeo import osr
    candidates = candidates or INDONESIA_CRS_CANDIDATES
    best_epsg, best_conf = None, 0.0
    for epsg in candidates:
        try:
            src = osr.SpatialReference(); src.ImportFromEPSG(epsg)
            dst = osr.SpatialReference(); dst.ImportFromEPSG(4326)
            t = osr.CoordinateTransformation(dst, src)
            deltas = []
            for (mx, my), (lon, lat) in gcp_pairs:
                px, py, _ = t.TransformPoint(lon, lat)
                deltas.append(math.sqrt((px - mx) ** 2 + (py - my) ** 2))
            mean_d = sum(deltas) / len(deltas)
            if mean_d < 10.0:
                conf = 1.0 - (mean_d / 50.0)
                if conf > best_conf:
                    best_epsg, best_conf = epsg, conf
        except Exception:
            continue
    return (best_epsg, round(best_conf, 4)) if best_epsg and best_conf > 0.3 else (None, 0)

_GEOREF_TRANSFORM = None

def get_transform(): return _GEOREF_TRANSFORM

def set_transform(helmert):
    global _GEOREF_TRANSFORM
    _GEOREF_TRANSFORM = helmert

def transform_point(x, y):
    if _GEOREF_TRANSFORM is None:
        return x, y
    return apply_helmert(x, y, _GEOREF_TRANSFORM)
