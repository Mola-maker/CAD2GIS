#!/usr/bin/env python3
"""
CAD2GIS Converter — DWG → GeoPackage (EPSG:3857)
Pure Web Mercator output for Tianditu overlay.
No intermediate CRS. No post-processing.
"""

import ctypes
import math
import os
import subprocess
import sys
import tempfile
from collections import defaultdict

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# ── Ctypes bridge to LibreDWG ─────────────────────────────────────────────
_lib = ctypes.CDLL("/usr/local/lib/libredwg.so")
_libc = ctypes.CDLL("libc.so.6")

_lib.dwg_ent_get_layer_name.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_lib.dwg_ent_get_layer_name.restype = ctypes.c_char_p
_lib.dwg_ent_lwpline_get_numpoints.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_lib.dwg_ent_lwpline_get_numpoints.restype = ctypes.c_int
_lib.dwg_ent_lwpline_get_points.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_lib.dwg_ent_lwpline_get_points.restype = ctypes.c_void_p
_libc.free.argtypes = [ctypes.c_void_p]


def _cstr(raw):
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.hex()


def _layer_name(entity_ptr):
    err = ctypes.c_int(0)
    return _cstr(_lib.dwg_ent_get_layer_name(entity_ptr, ctypes.byref(err)))


def _lwpoline_points(entity):
    try:
        lw_ptr = int(entity.tio.LWPOLYLINE.this)
    except Exception:
        return []
    err = ctypes.c_int(0)
    npts = _lib.dwg_ent_lwpline_get_numpoints(lw_ptr, ctypes.byref(err))
    if err.value or npts < 2:
        return []
    pts_ptr = _lib.dwg_ent_lwpline_get_points(lw_ptr, ctypes.byref(err))
    if err.value or not pts_ptr:
        return []
    pts = []
    for j in range(npts):
        off = j * 16
        pts.append((
            ctypes.c_double.from_address(pts_ptr + off).value,
            ctypes.c_double.from_address(pts_ptr + off + 8).value,
        ))
    _libc.free(pts_ptr)
    return pts


# ── LibreDWG SWIG imports ─────────────────────────────────────────────────
sys.path.insert(0, "/usr/local/lib/python3.12/dist-packages")
from LibreDWG import (  # noqa: E402
    Dwg_Data, dwg_read_file, new_Dwg_Object_Array, Dwg_Object_Array_getitem,
    DWG_SUPERTYPE_ENTITY,
    DWG_TYPE_LINE, DWG_TYPE_LWPOLYLINE, DWG_TYPE_CIRCLE, DWG_TYPE_ARC,
    DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_INSERT, DWG_TYPE_POINT,
)

TYPE_NAMES = {}
import LibreDWG  # noqa: E402
for name in dir(LibreDWG):
    if name.startswith("DWG_TYPE_"):
        TYPE_NAMES[getattr(LibreDWG, name)] = name[9:]

# ── QGIS ──────────────────────────────────────────────────────────────────
from qgis.core import (  # noqa: E402
    QgsApplication, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsFields, QgsVectorFileWriter, QgsCoordinateReferenceSystem,
)
from qgis.PyQt.QtCore import QVariant  # noqa: E402
from pyproj import Transformer  # noqa: E402

QgsApplication.setPrefixPath("/usr", True)
qgs = QgsApplication([], False)
qgs.initQgis()

# ── Coordinate transform: local UTM 48N → EPSG:3857 ──────────────────────
_utm_to_3857 = Transformer.from_crs("EPSG:32648", "EPSG:3857")

# Regime offsets (DWG → UTM 48N), then reproject to EPSG:3857
OX_A, OY_A = 292539, -405    # Regime A: Y preserved in UTM northing
OX_B, OY_B = 589239, 3203295  # Regime B: local engineering grid


def dwg_to_3857(x, y, regime):
    """Convert DWG coordinates directly to EPSG:3857."""
    if regime == "A":
        ex, ey = x + OX_A, y + OY_A
    else:
        ex, ey = x + OX_B, y + OY_B
    mx, my = _utm_to_3857.transform(ex, ey)
    return mx, my


# ── Geometry Extraction ───────────────────────────────────────────────────
def extract_geometry_3857(entity, dwg_type, regime):
    """Extract geometry from DWG entity, already in EPSG:3857."""
    try:
        if dwg_type == DWG_TYPE_LINE:
            li = entity.tio.LINE
            x1, y1 = dwg_to_3857(li.start.x, li.start.y, regime)
            x2, y2 = dwg_to_3857(li.end.x, li.end.y, regime)
            return QgsGeometry.fromPolylineXY([QgsPointXY(x1, y1), QgsPointXY(x2, y2)])

        elif dwg_type == DWG_TYPE_LWPOLYLINE:
            pts = _lwpoline_points(entity)
            if len(pts) < 2:
                return QgsGeometry()
            xformed = [QgsPointXY(*dwg_to_3857(px, py, regime)) for px, py in pts]
            if entity.tio.LWPOLYLINE.flag & 1:
                xformed.append(xformed[0])
            return QgsGeometry.fromPolylineXY(xformed)

        elif dwg_type == DWG_TYPE_CIRCLE:
            c = entity.tio.CIRCLE
            n = 72
            pts = []
            for j in range(n + 1):
                angle = 2 * math.pi * j / n
                dx, dy = c.radius * math.cos(angle), c.radius * math.sin(angle)
                mx, my = dwg_to_3857(c.center.x + dx, c.center.y + dy, regime)
                pts.append(QgsPointXY(mx, my))
            return QgsGeometry.fromPolygonXY([pts])

        elif dwg_type == DWG_TYPE_ARC:
            ar = entity.tio.ARC
            sa, ea = ar.start_angle, ar.end_angle
            if ea < sa:
                ea += 2 * math.pi
            n = max(10, int(36 * (ea - sa) / (2 * math.pi)))
            pts = []
            for j in range(n + 1):
                angle = sa + j * (ea - sa) / n
                dx, dy = ar.radius * math.cos(angle), ar.radius * math.sin(angle)
                mx, my = dwg_to_3857(ar.center.x + dx, ar.center.y + dy, regime)
                pts.append(QgsPointXY(mx, my))
            return QgsGeometry.fromPolylineXY(pts)

        elif dwg_type == DWG_TYPE_TEXT:
            t = entity.tio.TEXT
            mx, my = dwg_to_3857(t.ins_pt.x, t.ins_pt.y, regime)
            return QgsGeometry.fromPointXY(QgsPointXY(mx, my))

        elif dwg_type == DWG_TYPE_MTEXT:
            mt = entity.tio.MTEXT
            mx, my = dwg_to_3857(mt.ins_pt.x, mt.ins_pt.y, regime)
            return QgsGeometry.fromPointXY(QgsPointXY(mx, my))

        elif dwg_type == DWG_TYPE_INSERT:
            ins = entity.tio.INSERT
            mx, my = dwg_to_3857(ins.ins_pt.x, ins.ins_pt.y, regime)
            return QgsGeometry.fromPointXY(QgsPointXY(mx, my))

        elif dwg_type == DWG_TYPE_POINT:
            p = entity.tio.POINT
            mx, my = dwg_to_3857(p.x, p.y, regime)
            return QgsGeometry.fromPointXY(QgsPointXY(mx, my))

    except Exception:
        pass
    return QgsGeometry()


def extract_text(entity, dwg_type):
    try:
        if dwg_type == DWG_TYPE_TEXT:
            return entity.tio.TEXT.text_value
        elif dwg_type == DWG_TYPE_MTEXT:
            return entity.tio.MTEXT.text
    except Exception:
        pass
    return ""


def _geom_type_name(dwg_type):
    return {
        DWG_TYPE_LINE: "LineString", DWG_TYPE_LWPOLYLINE: "LineString",
        DWG_TYPE_CIRCLE: "Polygon", DWG_TYPE_ARC: "LineString",
        DWG_TYPE_TEXT: "Point", DWG_TYPE_MTEXT: "Point",
        DWG_TYPE_INSERT: "Point", DWG_TYPE_POINT: "Point",
    }.get(dwg_type, "Geometry")


# ── Core Converter ────────────────────────────────────────────────────────
def convert_dwg_3857(dwg_path, gpkg_path):
    print(f"Reading: {dwg_path}")
    data = Dwg_Data()
    data.object = new_Dwg_Object_Array(500000)
    err = dwg_read_file(dwg_path, data)
    print(f"  LibreDWG error: {err}, objects: {data.num_objects}")

    layer_features = defaultdict(list)

    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.supertype != DWG_SUPERTYPE_ENTITY:
            continue

        entity = obj.tio.entity
        entity_ptr = int(entity.this)
        dwg_type = obj.type

        # Determine regime from raw geometry centroid Y
        try:
            bbox = obj.tio.entity  # placeholder, compute below
        except Exception:
            continue

        # Quick regime check: sample first coordinate
        regime = "B"
        cy = 0
        try:
            if dwg_type == DWG_TYPE_LINE:
                cy = entity.tio.LINE.start.y
            elif dwg_type == DWG_TYPE_LWPOLYLINE:
                pts = _lwpoline_points(entity)
                cy = pts[0][1] if pts else 0
            elif dwg_type == DWG_TYPE_CIRCLE:
                cy = entity.tio.CIRCLE.center.y
            elif dwg_type == DWG_TYPE_ARC:
                cy = entity.tio.ARC.center.y
            elif dwg_type == DWG_TYPE_TEXT:
                cy = entity.tio.TEXT.ins_pt.y
            elif dwg_type == DWG_TYPE_MTEXT:
                cy = entity.tio.MTEXT.ins_pt.y
            elif dwg_type == DWG_TYPE_INSERT:
                cy = entity.tio.INSERT.ins_pt.y
            elif dwg_type == DWG_TYPE_POINT:
                cy = entity.tio.POINT.y
            else:
                cy = 0
            if cy > 100000:
                regime = "A"
        except Exception:
            pass

        # Filter sheet-layout artifacts: DWG Y < -100,000 is layout space
        if cy < -100000:
            continue

        geom = extract_geometry_3857(entity, dwg_type, regime)
        if geom.isEmpty():
            continue

        # Filter out-of-bounds projected coordinates
        bbox = geom.boundingBox()
        if bbox.yMaximum() < -9000000 or bbox.yMinimum() > 20000000:
            continue

        layer = _layer_name(entity_ptr)
        gtype = _geom_type_name(dwg_type)
        text_val = extract_text(entity, dwg_type)

        layer_features[(layer, gtype, regime)].append({
            "geom": geom, "dwg_type": TYPE_NAMES.get(dwg_type, f"T{dwg_type}"),
            "dwg_type_id": dwg_type, "layer": layer, "text": text_val,
        })

        if (i + 1) % 50000 == 0:
            print(f"  ... {i + 1} objects, {sum(len(v) for v in layer_features.values())} features")

    total_feats = sum(len(v) for v in layer_features.values())
    print(f"  Extracted: {total_feats} features in {len(layer_features)} groups")

    # Write GeoPackage (EPSG:3857, no post-reprojection)
    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)

    crs_3857 = QgsCoordinateReferenceSystem("EPSG:3857")
    layer_count, feat_count = 0, 0

    for (layer_name, gtype, regime), feat_list in layer_features.items():
        safe = layer_name.encode("ascii", errors="replace").decode("ascii")
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
        if not safe or safe[0].isdigit():
            safe = "L_" + safe
        safe = safe[:50]
        # Tag mixed-regime layers
        has_both = any(k[0] == layer_name and k[2] != regime for k in layer_features)
        if has_both:
            safe = safe + "_" + regime

        has_text = any(f["text"] for f in feat_list)

        try:
            fields = QgsFields()
            fields.append(QgsField("dwg_type", QVariant.String))
            fields.append(QgsField("dwg_type_id", QVariant.Int))
            fields.append(QgsField("layer", QVariant.String))
            if has_text:
                fields.append(QgsField("text", QVariant.String))

            uri = f"{gtype}?crs=EPSG:3857"
            mem_layer = QgsVectorLayer(uri, safe, "memory")
            dp = mem_layer.dataProvider()
            dp.addAttributes([fields.at(j) for j in range(fields.count())])
            mem_layer.updateFields()

            features = []
            for fdata in feat_list:
                f = QgsFeature(mem_layer.fields())
                f.setGeometry(fdata["geom"])
                f["dwg_type"] = fdata["dwg_type"]
                f["dwg_type_id"] = fdata["dwg_type_id"]
                f["layer"] = fdata["layer"]
                if has_text:
                    f["text"] = fdata["text"]
                features.append(f)

            dp.addFeatures(features)
            mem_layer.updateExtents()

            with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
                tmp_path = tmp.name

            result = QgsVectorFileWriter.writeAsVectorFormat(
                mem_layer, tmp_path, "UTF-8", crs_3857, "GeoJSON",
            )
            if result[0] != 0:
                print(f"  [{layer_count + 1}/{len(layer_features)}] {safe}: write error")
                os.unlink(tmp_path)
                continue

            cmd = ["ogr2ogr", "-f", "GPKG", "-a_srs", "EPSG:3857", "-nln", safe, gpkg_path, tmp_path]
            if layer_count > 0:
                cmd.insert(1, "-update")
                cmd.insert(2, "-append")

            run = subprocess.run(cmd, capture_output=True, text=True)
            os.unlink(tmp_path)

            if run.returncode != 0:
                print(f"  [{layer_count + 1}/{len(layer_features)}] {safe}: ogr2ogr error")
            else:
                layer_count += 1
                feat_count += len(features)
                print(f"  [{layer_count}/{len(layer_features)}] {safe}: {len(features)} features")

        except Exception as e:
            print(f"  [{layer_count + 1}/{len(layer_features)}] {safe}: error {e}")

    print(f"  Done: {layer_count} layers, {feat_count} features → {gpkg_path}\n")
    return layer_count, feat_count


# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source", "重庆市綦江区东溪镇")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)

    targets = [
        "DS-02 通信总平面布置图.dwg",
        "DS-04 通信分平面布置图.dwg",
    ]

    total_l, total_f = 0, 0
    for tgt in targets:
        in_path = os.path.join(src_dir, tgt)
        out_path = os.path.join(out_dir, f"{os.path.splitext(tgt)[0]}_3857.gpkg")
        lc, fc = convert_dwg_3857(in_path, out_path)
        total_l += lc
        total_f += fc

    print(f"===== Total: {total_l} layers, {total_f} features (EPSG:3857) =====")
    qgs.exitQgis()
