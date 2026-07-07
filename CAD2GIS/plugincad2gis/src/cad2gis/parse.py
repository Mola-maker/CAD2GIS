"""DXF parser (story G3) — ezdxf is the AUTHORITATIVE semantic parser.

Converts a DXF modelspace into a typed FeatureCollection with full provenance. Design points
(per the independent review):
  - ezdxf, not the OGR DXF driver, is authoritative (blocks/ATTRIB/xdata/OCS fidelity).
  - INSERT/blocks are exploded so nested entities become first-class features.
  - ARC/CIRCLE/ELLIPSE/SPLINE flatten to vertices with a *scale-aware chord-height tolerance*;
    original curve metadata is retained in attributes for traceability.
  - TEXT/MTEXT are kept as separate annotation points (annotation != a shapefile feature).
  - Every feature carries a SourceRef (file/layer/block/handle/entity_type) — the lossless claim.

This stage does geometry extraction only; semantic classification is G6 (mapping engine),
run by the pipeline after parsing.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .model import Feature, FeatureCollection, SourceRef

# Block-name patterns that denote a POINT FACILITY (a node) rather than decorative linework.
# Real comms nodes are drawn as named well blocks: 末端井/三通井/四通井/直通井/检查井 + 人孔/手孔.
# Such INSERTs are emitted as a point at the insertion instead of being exploded to lines.
_NODE_BLOCK_RE = re.compile(r"井$|井[0-9]*$|末端井|三通井|四通井|直通井|检查井|人孔|手孔|人手孔|manhole|handhole")


def _load_symbol_block_names() -> set[str]:
    """Names of reviewed opaque symbol blocks (from block_codes.yaml), lowercased.

    A duct cross-section symbol like gc170 is a POINT SYMBOL (one CIRCLE at the insertion), not
    linework — exploding it strips the INSERT handle/layer/block and loses the fact that it IS a
    single classifiable node. So, exactly like the well blocks, we emit reviewed symbol blocks as
    a Point at the insertion, preserving the handle so the hit-vector (nearest-text) can classify
    it downstream. Blocks NOT in the table still explode as before.
    """
    import os as _os

    path = _os.path.join(_os.path.dirname(__file__), "mapping", "block_codes.yaml")
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return {str(c["code"]).lower() for c in data.get("codes", []) if c.get("code")}
    except Exception:  # noqa: BLE001 - table is optional; core parser must not depend on it
        return set()


@dataclass
class ParseStats:
    entities_seen: int = 0
    features_out: int = 0
    blocks_exploded: int = 0
    curves_flattened: int = 0
    skipped: int = 0
    by_type: Optional[dict] = None


def _chord_tol(extent_span: float) -> float:
    """Scale-aware chord-height tolerance for curve flattening (~0.1% of extent, clamped)."""
    if extent_span <= 0:
        return 0.01
    return min(max(extent_span * 0.001, 0.001), 1.0)


def _point_feature(x, y, etype, layer, handle, source_file, block=None, attrs=None) -> Feature:
    from shapely.geometry import Point

    return Feature(
        Point(x, y),
        None,
        dict(attrs or {}),
        SourceRef(file=source_file, layer=layer, block=block, handle=handle, entity_type=etype),
    )


def _flatten_points(entity, tol: float):
    """Return a list of (x, y) approximating a curved entity, using ezdxf flattening."""
    try:
        return [(p.x, p.y) for p in entity.flattening(tol)]
    except Exception:  # noqa: BLE001 - not all entities support flattening
        return None


def parse_dxf(path: str) -> tuple[FeatureCollection, ParseStats]:
    import ezdxf
    from shapely.geometry import LineString, Point, Polygon

    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    coll = FeatureCollection(source_file=os.path.basename(path))
    stats = ParseStats(by_type={})
    symbol_blocks = _load_symbol_block_names()

    # Drawing extent for scale-aware tolerance.
    try:
        ext_min = doc.header.get("$EXTMIN", (0, 0, 0))
        ext_max = doc.header.get("$EXTMAX", (1000, 1000, 0))
        span = max(ext_max[0] - ext_min[0], ext_max[1] - ext_min[1])
    except Exception:  # noqa: BLE001
        span = 1000.0
    tol = _chord_tol(span)
    coll.metadata["chord_tol"] = tol
    coll.metadata["units_insunits"] = int(doc.header.get("$INSUNITS", 0))

    def emit(f: Feature):
        coll.add(f)
        stats.features_out += 1

    def handle_entity(e, block_name: Optional[str] = None):
        etype = e.dxftype()
        stats.entities_seen += 1
        stats.by_type[etype] = stats.by_type.get(etype, 0) + 1
        layer = getattr(e.dxf, "layer", None)
        handle = getattr(e.dxf, "handle", None)
        src = dict(file=coll.source_file, layer=layer, block=block_name, handle=handle)

        if etype == "POINT":
            emit(_point_feature(e.dxf.location.x, e.dxf.location.y, etype, layer, handle, coll.source_file, block_name))
        elif etype == "LINE":
            emit(Feature(LineString([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]),
                         None, {}, SourceRef(entity_type=etype, **src)))
        elif etype in ("LWPOLYLINE", "POLYLINE"):
            pts = _flatten_points(e, tol)
            if not pts:
                try:
                    pts = [(p[0], p[1]) for p in e.get_points("xy")]  # LWPOLYLINE fallback
                except Exception:  # noqa: BLE001
                    stats.skipped += 1
                    return
            closed = bool(getattr(e, "closed", False) or getattr(e.dxf, "flags", 0) & 1)
            if closed and len(pts) >= 3:
                emit(Feature(Polygon(pts), None, {"closed": True}, SourceRef(entity_type=etype, **src)))
            elif len(pts) >= 2:
                emit(Feature(LineString(pts), None, {"closed": False}, SourceRef(entity_type=etype, **src)))
            else:
                stats.skipped += 1
        elif etype in ("ARC", "ELLIPSE", "SPLINE"):
            pts = _flatten_points(e, tol)
            if pts and len(pts) >= 2:
                stats.curves_flattened += 1
                emit(Feature(LineString(pts), None, {"flattened_from": etype, "chord_tol": tol},
                             SourceRef(entity_type=etype, **src)))
            else:
                stats.skipped += 1
        elif etype == "CIRCLE":
            # Comms symbols drawn as circles -> node point at center; keep radius.
            c = e.dxf.center
            emit(Feature(Point(c.x, c.y), None, {"radius": float(e.dxf.radius), "from": "CIRCLE"},
                         SourceRef(entity_type=etype, **src)))
        elif etype in ("TEXT", "MTEXT"):
            try:
                ins = e.dxf.insert if etype == "MTEXT" else e.dxf.insert
                txt = e.text if etype == "MTEXT" else e.dxf.text
            except Exception:  # noqa: BLE001
                ins, txt = (0, 0, 0), ""
            emit(Feature(Point(ins[0], ins[1]), None, {"text": txt, "annotation": True},
                         SourceRef(entity_type=etype, **src)))
        elif etype == "INSERT":
            # A block reference. If its name marks a POINT FACILITY (a well/manhole/handhole),
            # emit a Point AT THE INSERTION to preserve the node's identity — otherwise exploding
            # it into interior LINE/LWPOLYLINE sub-entities loses the fact that it IS a node (the
            # real comms manholes 末端井/三通井/四通井/直通井 are drawn this way). Non-node blocks are
            # exploded as before so their linework still converts.
            bname = e.dxf.name or ""
            ins = e.dxf.insert
            if _NODE_BLOCK_RE.search(bname):
                emit(_point_feature(ins[0], ins[1], "INSERT", layer, handle, coll.source_file, bname,
                                    {"block": bname, "is_node_block": True}))
            elif bname.lower() in symbol_blocks:
                # A reviewed opaque symbol block (e.g. gc170 duct cross-section). Emit as a Point
                # at the insertion — preserving handle/layer/block — so the downstream hit-vector
                # (nearest-text) can classify it. Exploding would strip the handle and lose identity.
                emit(_point_feature(ins[0], ins[1], "INSERT", layer, handle, coll.source_file, bname,
                                    {"block": bname, "is_symbol_block": True}))
            else:
                stats.blocks_exploded += 1
                try:
                    for sub in e.virtual_entities():
                        handle_entity(sub, block_name=bname)
                except Exception:  # noqa: BLE001
                    emit(_point_feature(ins[0], ins[1], "INSERT", layer, handle, coll.source_file, bname,
                                        {"block": bname}))
        else:
            stats.skipped += 1

    for e in msp:
        handle_entity(e)

    return coll, stats
