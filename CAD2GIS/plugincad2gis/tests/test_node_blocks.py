"""Node-block parsing tests — well/manhole INSERTs must become POINT nodes, not scatter lines.

Regression guard for the real-data fix: comms manholes are drawn as named well blocks
(末端井/三通井/四通井/直通井). If the parser explodes them into interior linework, their node
identity is lost and the network has no nodes. They must be emitted as a Point at insertion.
"""
from __future__ import annotations

import pytest

pytest.importorskip("ezdxf")
import ezdxf  # noqa: E402

from cad2gis.parse import _NODE_BLOCK_RE, parse_dxf  # noqa: E402


def test_node_block_regex_matches_well_families():
    for name in ["末端井", "人行道三通井", "人行道四通井", "车行道直通井", "检查井", "人孔", "手孔"]:
        assert _NODE_BLOCK_RE.search(name), f"{name} should be a node block"
    for name in ["gc170", "GC200", "gc043", "vxcvxcvxcvxc"]:
        assert not _NODE_BLOCK_RE.search(name), f"{name} must NOT be a node block"


def test_well_insert_becomes_point_node(tmp_path):
    # Build a tiny DXF: a well block inserted once + a plain block that should explode.
    doc = ezdxf.new("R2018")
    blk = doc.blocks.new(name="末端井")
    blk.add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], close=True)
    other = doc.blocks.new(name="gc999")
    other.add_line((0, 0), (2, 0))
    msp = doc.modelspace()
    msp.add_blockref("末端井", (100, 200), dxfattribs={"layer": "通信"})
    msp.add_blockref("gc999", (300, 400), dxfattribs={"layer": "通信"})
    path = str(tmp_path / "wells.dxf")
    doc.saveas(path)

    coll, stats = parse_dxf(path)
    # The well INSERT is one Point at its insertion; the gc999 block is exploded to a line.
    well_pts = [f for f in coll.features
                if f.source.block == "末端井" and f.geometry.geom_type == "Point"]
    assert len(well_pts) == 1
    assert well_pts[0].attributes.get("is_node_block") is True
    assert abs(well_pts[0].geometry.x - 100) < 1e-6 and abs(well_pts[0].geometry.y - 200) < 1e-6
    # gc999 exploded (not emitted as a node point).
    assert not any(f.source.block == "gc999" and f.attributes.get("is_node_block") for f in coll.features)


def test_reviewed_symbol_block_becomes_point(tmp_path):
    # gc170 is a reviewed opaque symbol in block_codes.yaml -> emit as a Point at the insertion
    # (preserving the handle) so the hit-vector can classify it; gc999 (unknown) still explodes.
    doc = ezdxf.new("R2018")
    sym = doc.blocks.new(name="gc170")
    sym.add_circle((0, 0), radius=1.0)  # a single-primitive duct cross-section symbol
    other = doc.blocks.new(name="gc999")
    other.add_line((0, 0), (2, 0))
    msp = doc.modelspace()
    msp.add_blockref("gc170", (50, 60), dxfattribs={"layer": "ZBTZ"})
    msp.add_blockref("gc999", (70, 80), dxfattribs={"layer": "ZBTZ"})
    path = str(tmp_path / "symbols.dxf")
    doc.saveas(path)

    coll, stats = parse_dxf(path)
    sym_pts = [f for f in coll.features
               if f.source.block == "gc170" and f.geometry.geom_type == "Point"]
    assert len(sym_pts) == 1
    assert sym_pts[0].attributes.get("is_symbol_block") is True
    assert sym_pts[0].source.handle is not None  # handle preserved for hit-vector matching
    assert abs(sym_pts[0].geometry.x - 50) < 1e-6 and abs(sym_pts[0].geometry.y - 60) < 1e-6
    # gc999 is not a reviewed symbol -> exploded, no symbol point emitted for it.
    assert not any(f.source.block == "gc999" and f.attributes.get("is_symbol_block") for f in coll.features)
