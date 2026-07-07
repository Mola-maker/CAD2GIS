"""GeoPackage warehouse writer (story G10) — standardized入库 to a QGIS-ready GeoPackage.

Writes the converted FeatureCollection to a GeoPackage (.gpkg) with:
  - one LAYER per feature class, fields normalized to the PUBLISHED_SCHEMA (with provenance),
  - geometry optionally georeferenced by the G9 transform (drawing coords -> local survey grid),
  - METADATA TABLES (non-spatial): conversion manifest, CRS/transform record, per-class QC metrics,
    source-file hash + rule/dictionary version — the audit trail that backs the lossless claim.

GeoPandas/pyogrio is the writer. A GeoPackage is a single portable file that QGIS opens directly;
shipped .qml styles (styles/) are applied per layer in the plugin. PostGIS is the documented upgrade
path (same schema), not built here.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional

from ..model import FeatureCollection
from .schema import PUBLISHED_SCHEMA, schema_for

RULE_VERSION = "comms_symbols.yaml v1 + block_codes.yaml v1"

_STYLES_DIR = os.path.join(os.path.dirname(__file__), "styles")


def styles_dir() -> str:
    """Directory holding the shipped per-class QGIS .qml styles."""
    return _STYLES_DIR


def qml_for(feature_class: str) -> Optional[str]:
    """Path to the shipped .qml style for a feature class, or None if not shipped."""
    p = os.path.join(_STYLES_DIR, f"{feature_class}.qml")
    return p if os.path.exists(p) else None


@dataclass
class WarehouseReport:
    path: str
    layers_written: dict = field(default_factory=dict)   # class -> feature count
    metadata_tables: list = field(default_factory=list)
    attribute_completeness: dict = field(default_factory=dict)  # class -> fraction of required fields filled
    crs: Optional[str] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _file_sha256(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _feature_row(f, transform=None) -> tuple[dict, object]:
    """Map one Feature's attributes to its published-schema fields + provenance; return (attrs, geom).

    When a transform (G9 TransformFit) is supplied, the geometry is moved from drawing coords to the
    local survey grid so the warehoused feature is georeferenced.
    """
    from shapely.ops import transform as shp_transform

    sch = schema_for(f.feature_class)
    a: dict = {}
    src = f.source
    prov = {
        "src_file": src.file, "src_layer": src.layer, "src_block": src.block,
        "src_handle": src.handle, "src_entity": src.entity_type,
        "confidence": round(float(f.confidence), 3), "map_rule": f.attributes.get("resolved_by") or None,
    }
    # class-specific fields pulled from the feature's raw attributes
    raw = f.attributes
    if sch:
        for spec in sch.fields:
            if spec.name in prov:
                a[spec.name] = prov[spec.name]
            elif spec.name == "length_m":
                a[spec.name] = round(float(getattr(f.geometry, "length", 0.0)), 3)
            elif spec.name == "area_m2":
                a[spec.name] = round(float(getattr(f.geometry, "area", 0.0)), 3)
            elif spec.name in ("node_type",):
                a[spec.name] = raw.get("block")
            elif spec.name == "spec":
                a[spec.name] = (raw.get("_map_evidence") or {}).get("matched_label")
            elif spec.name == "point_id":
                a[spec.name] = raw.get("point_id") or raw.get("text")
            else:
                a[spec.name] = raw.get(spec.name)
    geom = f.geometry
    if transform is not None and geom is not None:
        from ..gcp import apply_transform

        geom = shp_transform(lambda xx, yy, zz=None: apply_transform(transform, xx, yy), geom)
    return a, geom


def _completeness(rows: list[dict], sch) -> float:
    """Fraction of (required field, row) cells that are non-null — the attribute-completeness metric."""
    req = sch.required_fields() if sch else []
    if not req or not rows:
        return 1.0
    total = len(req) * len(rows)
    filled = sum(1 for r in rows for k in req if r.get(k) not in (None, ""))
    return filled / total if total else 1.0


def write_geopackage(
    coll: FeatureCollection,
    out_path: str,
    *,
    transform=None,
    manifest: Optional[dict] = None,
    qc: Optional[dict] = None,
    source_path: Optional[str] = None,
) -> WarehouseReport:
    import geopandas as gpd
    import pandas as pd

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)  # fresh write (idempotent conversion output)

    rep = WarehouseReport(path=out_path, crs=coll.crs)
    by_class: dict[str, list] = {}
    for f in coll.features:
        if f.feature_class and f.feature_class in PUBLISHED_SCHEMA:
            by_class.setdefault(f.feature_class, []).append(f)

    for cls, feats in by_class.items():
        sch = schema_for(cls)
        attrs_list, geoms = [], []
        for f in feats:
            a, g = _feature_row(f, transform=transform)
            if g is None or getattr(g, "is_empty", True):
                continue
            attrs_list.append(a)
            geoms.append(g)
        if not geoms:
            continue
        gdf = gpd.GeoDataFrame(attrs_list, geometry=geoms)
        # GeoPackage layer name = feature class; no CRS EPSG assigned (local grid, per G9)
        gdf.to_file(out_path, layer=cls, driver="GPKG")
        rep.layers_written[cls] = len(geoms)
        rep.attribute_completeness[cls] = round(_completeness(attrs_list, sch), 4)

    # Embed shipped .qml styles into the GeoPackage layer_styles table so QGIS auto-applies them.
    _embed_layer_styles(out_path, list(rep.layers_written.keys()))

    # ---- metadata tables (non-spatial) ----
    meta_rows = {
        "key": ["source_file", "source_sha256", "rule_version", "crs", "n_layers", "n_features"],
        "value": [
            os.path.basename(source_path) if source_path else (coll.source_file or ""),
            _file_sha256(source_path) if source_path else "",
            RULE_VERSION,
            coll.crs or "",
            str(len(rep.layers_written)),
            str(sum(rep.layers_written.values())),
        ],
    }
    _write_table(out_path, "cad2gis_manifest", meta_rows)
    rep.metadata_tables.append("cad2gis_manifest")

    if transform is not None:
        tr = transform.to_dict() if hasattr(transform, "to_dict") else dict(transform)
        _write_table(out_path, "cad2gis_transform", {"key": list(tr.keys()),
                                                     "value": [str(v) for v in tr.values()]})
        rep.metadata_tables.append("cad2gis_transform")

    if qc:
        _write_table(out_path, "cad2gis_qc", {"key": list(qc.keys()),
                                              "value": [str(v) for v in qc.values()]})
        rep.metadata_tables.append("cad2gis_qc")

    if manifest:
        _write_table(out_path, "cad2gis_runinfo", {"key": list(manifest.keys()),
                                                   "value": [str(v) for v in manifest.values()]})
        rep.metadata_tables.append("cad2gis_runinfo")

    return rep


def _embed_layer_styles(gpkg_path: str, layers: list[str]) -> None:
    """Write each shipped .qml into the GeoPackage `layer_styles` table (QGIS reads this on load,
    so the comms layers open pre-styled — part of the standardized-warehousing deliverable)."""
    import sqlite3

    styled = [(l, qml_for(l)) for l in layers if qml_for(l)]
    if not styled:
        return
    con = sqlite3.connect(gpkg_path)
    try:
        cur = con.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS layer_styles (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 f_table_catalog TEXT, f_table_schema TEXT, f_table_name TEXT,
                 f_geometry_column TEXT, styleName TEXT, styleQML TEXT, styleSLD TEXT,
                 useAsDefault BOOLEAN, description TEXT, owner TEXT, ui TEXT, update_time TIMESTAMP)"""
        )
        for layer, qml_path in styled:
            with open(qml_path, "r", encoding="utf-8") as fh:
                qml = fh.read()
            cur.execute(
                """INSERT INTO layer_styles
                   (f_table_name, f_geometry_column, styleName, styleQML, useAsDefault, description)
                   VALUES (?,?,?,?,?,?)""",
                (layer, "geom", layer, qml, 1, f"cad2gis default style for {layer}"),
            )
        con.commit()
    except Exception:  # noqa: BLE001 - styling is best-effort; layers are valid without it
        pass
    finally:
        con.close()


def _write_table(gpkg_path: str, table: str, cols: dict) -> None:
    """Write a non-spatial attribute table into the GeoPackage via sqlite (GDAL aspatial table)."""
    import pandas as pd
    import sqlite3

    df = pd.DataFrame(cols)
    con = sqlite3.connect(gpkg_path)
    try:
        df.to_sql(table, con, if_exists="replace", index=False)
        # register as an aspatial GeoPackage table so QGIS lists it
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO gpkg_contents (table_name, data_type, identifier) VALUES (?,?,?)",
            (table, "attributes", table),
        )
        con.commit()
    except Exception:  # noqa: BLE001 - gpkg_contents may not accept 'attributes' on all builds; table still exists
        pass
    finally:
        con.close()
