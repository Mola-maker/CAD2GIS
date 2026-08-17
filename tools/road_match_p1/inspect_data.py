# -*- coding: utf-8 -*-
"""P1 前置勘察：只读检查 hutabohu 基线 gpkg 的图层、CRS、范围与 GCP 内容。
运行: conda run -n cad2gis python tools/road_match_p1/inspect_data.py
"""
import sqlite3
import struct
import sys

BASE = r"E:\branch_CAD2GIS\CAD2GIS\.worktrees\robustness\baselines"
TARGETS = [
    ("delivery", BASE + r"\apd_hutabohu\delivery\apd_delivery.gpkg"),
    ("gcp_capture", BASE + r"\apd_hutabohu\gcp_capture.gpkg"),
    ("gcp_ready_delivery", BASE + r"\apd_hutabohu_gcp_ready\apd_delivery.gpkg"),
]


def parse_gpkg_geom_header(blob):
    """解析 GeoPackageBinary header，返回 (srs_id, envelope_or_None)。"""
    if blob is None or len(blob) < 8:
        return None, None
    if blob[0:2] != b"GP":
        return None, None
    flags = blob[3]
    srs_id = struct.unpack("<i", blob[4:8])[0]
    env_type = (flags >> 1) & 0x07
    envelope = None
    if env_type == 1:
        envelope = struct.unpack("<4d", blob[8:40])  # minx maxx miny maxy
    elif env_type in (2, 3):
        envelope = struct.unpack("<6d", blob[8:56])
    elif env_type == 4:
        envelope = struct.unpack("<8d", blob[8:72])
    return srs_id, envelope


def inspect(name, path):
    print("=" * 25, name, "=" * 25)
    print("path:", path)
    con = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
    cur = con.cursor()
    print("-- gpkg_spatial_ref_sys:")
    for row in cur.execute(
        "SELECT srs_name, srs_id, organization, organization_coordsys_id FROM gpkg_spatial_ref_sys"
    ):
        print("   ", row)
    print("-- gpkg_geometry_columns:")
    geom_tables = []
    for row in cur.execute(
        "SELECT table_name, column_name, geometry_type_name, srs_id FROM gpkg_geometry_columns"
    ):
        print("   ", row)
        geom_tables.append((row[0], row[1]))
    print("-- gpkg_contents:")
    try:
        for row in cur.execute(
            "SELECT table_name, data_type, identifier, min_x, min_y, max_x, max_y, srs_id FROM gpkg_contents"
        ):
            print("   ", row)
    except Exception as e:
        print("    ERR", e)
    for tbl, col in geom_tables:
        try:
            n = cur.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            print(f"-- table {tbl}: {n} rows")
            cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{tbl}")').fetchall()]
            print("   columns:", cols)
            # 包络：取几何 header envelope 的 min/max
            xs0, ys0, xs1, ys1 = [], [], [], []
            for (blob,) in cur.execute(f'SELECT "{col}" FROM "{tbl}" WHERE "{col}" IS NOT NULL'):
                srs_id, env = parse_gpkg_geom_header(blob)
                if env:
                    xs0.append(env[0]); xs1.append(env[1]); ys0.append(env[2]); ys1.append(env[3])
            if xs0:
                print(f"   extent: X [{min(xs0):.3f}, {max(xs1):.3f}]  Y [{min(ys0):.3f}, {max(ys1):.3f}]")
            # 打印前 3 行的非几何属性，帮助理解 schema
            attr_cols = [c for c in cols if c != col]
            rows = cur.execute(f'SELECT {", ".join('"' + c + '"' for c in attr_cols)} FROM "{tbl}" LIMIT 3').fetchall()
            for r in rows:
                print("   sample:", r)
        except Exception as e:
            print(f"   ERR table {tbl}: {e}")
    con.close()


for name, p in TARGETS:
    try:
        inspect(name, p)
    except Exception as e:
        print("FATAL", name, e)
