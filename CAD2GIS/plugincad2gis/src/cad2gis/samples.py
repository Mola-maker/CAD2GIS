"""Synthetic communications-infrastructure DXF fixture generator.

Produces a small, fully-known drawing that exercises the whole pipeline:
- Chinese comms-infra layer names (drives semantic mapping G6)
- points (manholes/poles), lines (cable/duct), a closed polygon (equipment room)
- text annotation (kept separate, G5)
- three DELIBERATE topology errors (overshoot/dangle, duplicate segment, unclosed polygon)
  so topology cleaning (G7) and the labeled benchmark (G2) have known-answer inputs.

Because every entity here is authored by us, this fixture doubles as ground truth:
feature counts, classes, and connectivity are known exactly.
"""
from __future__ import annotations

import os

# Ground-truth summary (used by the G2 benchmark / tests).
GROUND_TRUTH = {
    "layers": {
        "GX_光缆": "cable",      # line
        "GD_管道": "duct",       # line
        "RK_人孔": "manhole",    # point
        "GG_杆": "pole",         # point
        "JF_机房": "room",       # closed polyline -> polygon
        "ZS_注记": "annotation",  # text
    },
    # NOTE: room=2 post-cleaning — the true closed room PLUS the "unclosed_room_polygon"
    # which is a GEOMETRY error fixed by closing (a valid 2nd room), not a duplicate to delete.
    "counts": {"manhole": 3, "pole": 3, "room": 2, "annotation": 3},
    "injected_errors": ["cable_overshoot_dangle", "duplicate_cable_segment", "unclosed_room_polygon"],
}


def generate(out_dir: str = "samples") -> str:
    """Write `synthetic_comms.dxf` into *out_dir* and return its path."""
    import ezdxf

    os.makedirs(out_dir, exist_ok=True)
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6  # 6 = meters
    msp = doc.modelspace()

    for name, color in {
        "GX_光缆": 3, "GD_管道": 5, "RK_人孔": 1,
        "GG_杆": 2, "JF_机房": 4, "ZS_注记": 7,
    }.items():
        doc.layers.add(name, color=color)

    # Equipment room: closed polygon.
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 25), (0, 25)], close=True,
                       dxfattribs={"layer": "JF_机房"})

    # Manholes (POINT) + annotation.
    for i, (x, y) in enumerate([(60, 5), (120, 5), (180, 5)]):
        msp.add_point((x, y), dxfattribs={"layer": "RK_人孔"})
        msp.add_text(f"RK{i + 1}", dxfattribs={"layer": "ZS_注记"}).set_placement((x, y + 2))

    # Poles (POINT).
    for x, y in [(60, 40), (120, 40), (180, 40)]:
        msp.add_point((x, y), dxfattribs={"layer": "GG_杆"})

    # Cable route (connects the three manholes) and duct route.
    msp.add_lwpolyline([(60, 5), (120, 5), (180, 5)], dxfattribs={"layer": "GX_光缆"})
    msp.add_lwpolyline([(60, 40), (120, 40), (180, 40)], dxfattribs={"layer": "GD_管道"})

    # --- deliberate topology errors (known answers for G7 cleaning + G2 benchmark) ---
    msp.add_line((180, 5), (188, 5), dxfattribs={"layer": "GX_光缆"})          # overshoot / dangle
    msp.add_lwpolyline([(60, 5), (120, 5)], dxfattribs={"layer": "GX_光缆"})   # duplicate segment
    msp.add_lwpolyline([(80, 60), (110, 60), (110, 80), (80, 80), (80, 60.5)],  # unclosed room
                       dxfattribs={"layer": "JF_机房"})

    path = os.path.join(out_dir, "synthetic_comms.dxf")
    doc.saveas(path)
    return path


if __name__ == "__main__":
    print(generate())
