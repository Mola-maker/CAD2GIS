#!/usr/bin/env python3
"""
CAD2GIS Basic Converter — DWG → GeoPackage
Reads .dwg via LibreDWG, writes QGIS-ready GeoPackage.
No intermediate formats. No GDAL DWG driver.
"""

import ctypes
import math
import os
import subprocess
import sys
import tempfile
from collections import defaultdict

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# ── Ctypes bridge to LibreDWG (bypasses SWIG encoding bugs) ──────────────
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
    """Safe LWPOLYLINE point extraction via C API."""
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
        pts.append(
            QgsPointXY(
                ctypes.c_double.from_address(pts_ptr + off).value,
                ctypes.c_double.from_address(pts_ptr + off + 8).value,
            )
        )
    _libc.free(pts_ptr)
    return pts


# ── LibreDWG SWIG imports ─────────────────────────────────────────────────
sys.path.insert(0, "/usr/local/lib/python3.12/dist-packages")
from LibreDWG import (  # noqa: E402
    Dwg_Data,
    dwg_read_file,
    new_Dwg_Object_Array,
    Dwg_Object_Array_getitem,
    DWG_SUPERTYPE_ENTITY,
    DWG_TYPE_LINE,
    DWG_TYPE_LWPOLYLINE,
    DWG_TYPE_CIRCLE,
    DWG_TYPE_ARC,
    DWG_TYPE_TEXT,
    DWG_TYPE_MTEXT,
    DWG_TYPE_INSERT,
    DWG_TYPE_POINT,
)

# Type names for attribute tagging
TYPE_NAMES = {}
import LibreDWG  # noqa: E402

for name in dir(LibreDWG):
    if name.startswith("DWG_TYPE_"):
        TYPE_NAMES[getattr(LibreDWG, name)] = name[9:]

# ── QGIS ──────────────────────────────────────────────────────────────────
from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsFields,
    QgsVectorFileWriter,
    QgsCoordinateReferenceSystem,
)
from qgis.PyQt.QtCore import QVariant  # noqa: E402

QgsApplication.setPrefixPath("/usr", True)
qgs = QgsApplication([], False)
qgs.initQgis()


# ── Geometry Extraction ───────────────────────────────────────────────────
def extract_geometry(entity, dwg_type):
    """Extract QgsGeometry from a DWG entity. Returns QgsGeometry (may be empty)."""
    try:
        if dwg_type == DWG_TYPE_LINE:
            li = entity.tio.LINE
            return QgsGeometry.fromPolylineXY(
                [QgsPointXY(li.start.x, li.start.y), QgsPointXY(li.end.x, li.end.y)]
            )

        elif dwg_type == DWG_TYPE_LWPOLYLINE:
            pts = _lwpoline_points(entity)
            if len(pts) < 2:
                return QgsGeometry()
            if entity.tio.LWPOLYLINE.flag & 1:
                pts.append(pts[0])
            return QgsGeometry.fromPolylineXY(pts)

        elif dwg_type == DWG_TYPE_CIRCLE:
            c = entity.tio.CIRCLE
            n = 72
            pts = [
                QgsPointXY(
                    c.center.x + c.radius * math.cos(2 * math.pi * j / n),
                    c.center.y + c.radius * math.sin(2 * math.pi * j / n),
                )
                for j in range(n + 1)
            ]
            return QgsGeometry.fromPolygonXY([pts])

        elif dwg_type == DWG_TYPE_ARC:
            ar = entity.tio.ARC
            sa, ea = ar.start_angle, ar.end_angle
            if ea < sa:
                ea += 2 * math.pi
            n = max(10, int(36 * (ea - sa) / (2 * math.pi)))
            pts = [
                QgsPointXY(
                    ar.center.x + ar.radius * math.cos(sa + j * (ea - sa) / n),
                    ar.center.y + ar.radius * math.sin(sa + j * (ea - sa) / n),
                )
                for j in range(n + 1)
            ]
            return QgsGeometry.fromPolylineXY(pts)

        elif dwg_type == DWG_TYPE_TEXT:
            t = entity.tio.TEXT
            return QgsGeometry.fromPointXY(QgsPointXY(t.ins_pt.x, t.ins_pt.y))

        elif dwg_type == DWG_TYPE_MTEXT:
            mt = entity.tio.MTEXT
            return QgsGeometry.fromPointXY(QgsPointXY(mt.ins_pt.x, mt.ins_pt.y))

        elif dwg_type == DWG_TYPE_INSERT:
            ins = entity.tio.INSERT
            return QgsGeometry.fromPointXY(QgsPointXY(ins.ins_pt.x, ins.ins_pt.y))

        elif dwg_type == DWG_TYPE_POINT:
            p = entity.tio.POINT
            return QgsGeometry.fromPointXY(QgsPointXY(p.x, p.y))

    except Exception:
        pass
    return QgsGeometry()


def extract_text(entity, dwg_type):
    """Extract text string from TEXT/MTEXT."""
    try:
        if dwg_type == DWG_TYPE_TEXT:
            return entity.tio.TEXT.text_value
        elif dwg_type == DWG_TYPE_MTEXT:
            return entity.tio.MTEXT.text
    except Exception:
        pass
    return ""


# ── Core Converter ────────────────────────────────────────────────────────
def convert_dwg(dwg_path, gpkg_path, crs="EPSG:0", offset_x=0, offset_y=0,
                ox_a=0, oy_a=0, ox_b=0, oy_b=0):
    """Convert a single DWG file to GeoPackage.

    Supports two coordinate regimes within the same DWG:
      Regime A (Y > 100,000): Y values preserved in UTM northing, X shifted
      Regime B (Y < 100,000): local engineering coordinates, both shifted

    Offsets are applied per-entity based on its Y coordinate.
    """
    print(f"Reading: {dwg_path} (CRS: {crs})")
    print(f"  Regime A (Y>100K): X+{ox_a} Y+{oy_a}")
    print(f"  Regime B (Y<100K): X+{ox_b} Y+{oy_b}")
    data = Dwg_Data()
    data.object = new_Dwg_Object_Array(500000)
    err = dwg_read_file(dwg_path, data)
    print(f"  LibreDWG error: {err}, objects: {data.num_objects}")

    # Phase 1: Extract entities, group by layer + geom type
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

        geom = extract_geometry(entity, dwg_type)
        if geom.isEmpty():
            continue
        # Determine regime from bounding box center Y (always safe)
        cy = geom.boundingBox().center().y()
        # Apply correct per-regime offset
        if cy > 100000:
            if ox_a or oy_a:
                geom.translate(ox_a, oy_a)
        else:
            if ox_b or oy_b:
                geom.translate(ox_b, oy_b)

        layer = _layer_name(entity_ptr)
        geom_type_name = {  # map DWG type → GeoPackage geometry type
            DWG_TYPE_LINE: "LineString",
            DWG_TYPE_LWPOLYLINE: "LineString",
            DWG_TYPE_CIRCLE: "Polygon",
            DWG_TYPE_ARC: "LineString",
            DWG_TYPE_TEXT: "Point",
            DWG_TYPE_MTEXT: "Point",
            DWG_TYPE_INSERT: "Point",
            DWG_TYPE_POINT: "Point",
        }.get(dwg_type, "Geometry")

        text_val = extract_text(entity, dwg_type)
        regime = "A" if cy > 100000 else "B"  # tag regime for layer splitting
        layer_features[(layer, geom_type_name, regime)].append(
            {"geom": geom, "dwg_type": TYPE_NAMES.get(dwg_type, f"T{dwg_type}"),
             "dwg_type_id": dwg_type, "layer": layer, "text": text_val, "regime": regime}
        )

        if (i + 1) % 50000 == 0:
            print(f"  ... {i + 1} objects, {sum(len(v) for v in layer_features.values())} features extracted")

    print(f"  Total: {sum(len(v) for v in layer_features.values())} features in {len(layer_features)} groups")

    # Phase 2: Write GeoPackage
    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)

    layer_count = 0
    feat_count = 0
    total_groups = len(layer_features)

    for (layer_name, geom_type_name, regime), feat_list in layer_features.items():
        # Safe layer name for GPKG
        safe = layer_name.encode("ascii", errors="replace").decode("ascii")
        # Tag regime for mixed layers
        has_both = any(k[0] == layer_name and k[2] != regime for k in layer_features)
        if has_both:
            safe = safe + "_" + regime
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
        if not safe or safe[0].isdigit():
            safe = "L_" + safe
        safe = safe[:50]

        has_text = any(f["text"] for f in feat_list)

        # Build memory layer
        try:
            fields = QgsFields()
            fields.append(QgsField("dwg_type", QVariant.String))
            fields.append(QgsField("dwg_type_id", QVariant.Int))
            fields.append(QgsField("layer", QVariant.String))
            if has_text:
                fields.append(QgsField("text", QVariant.String))

            uri = f"{geom_type_name}?crs={crs}"
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

            # Write via temp GeoJSON then ogr2ogr (reliable multi-layer GPKG)
            with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
                tmp_path = tmp.name

            result = QgsVectorFileWriter.writeAsVectorFormat(
                mem_layer, tmp_path, "UTF-8",
                QgsCoordinateReferenceSystem(crs), "GeoJSON",
            )
            if result[0] != 0:  # QgsVectorFileWriter.NoError
                print(f"  [{layer_count + 1}/{total_groups}] {safe}: temp write error {result}")
                os.unlink(tmp_path)
                continue

            cmd = ["ogr2ogr", "-f", "GPKG", "-a_srs", crs, "-nln", safe, gpkg_path, tmp_path]
            if layer_count > 0:
                cmd.insert(1, "-update")
                cmd.insert(2, "-append")

            run = subprocess.run(cmd, capture_output=True, text=True)
            os.unlink(tmp_path)

            if run.returncode != 0:
                print(f"  [{layer_count + 1}/{total_groups}] {safe}: ogr2ogr error {run.stderr[:150]}")
            else:
                layer_count += 1
                feat_count += len(features)
                print(f"  [{layer_count}/{total_groups}] {safe}: {len(features)} features")

        except Exception as e:
            print(f"  [{layer_count + 1}/{total_groups}] {safe}: error {e}")

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
    crs_arg = "EPSG:32648"
    ox_a, oy_a = 292539, -405   # Regime A: Y preserved, X offset
    ox_b, oy_b = 589239, 3203295  # Regime B: local, X+Y offset

    for a in sys.argv[1:]:
        if a.startswith("--crs="):
            crs_arg = a.split("=", 1)[1]
        elif a.startswith("--ox-a="):
            ox_a = int(a.split("=", 1)[1])
        elif a.startswith("--oy-a="):
            oy_a = int(a.split("=", 1)[1])
        elif a.startswith("--ox-b="):
            ox_b = int(a.split("=", 1)[1])
        elif a.startswith("--oy-b="):
            oy_b = int(a.split("=", 1)[1])

    total_l, total_f = 0, 0
    for tgt in targets:
        in_path = os.path.join(src_dir, tgt)
        out_path = os.path.join(out_dir, f"{os.path.splitext(tgt)[0]}.gpkg")
        # suffix mixed-regime layers with _A or _B
        lc, fc = convert_dwg(in_path, out_path, crs_arg, 0, 0, ox_a, oy_a, ox_b, oy_b)
        total_l += lc
        total_f += fc

    print(f"===== Total: {total_l} layers, {total_f} features =====")
    qgs.exitQgis()
