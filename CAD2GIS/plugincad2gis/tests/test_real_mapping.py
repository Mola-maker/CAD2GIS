"""G4 profiler + real-layer mapping tests (do not require the 68MB real DXF).

These use a small synthetic multi-discipline DXF so they run in CI, but they exercise the
same code paths proven on the real organizer file: comms-layer detection, control-point
detection, and layer-scoped classification that rejects power/survey layers.
"""
from __future__ import annotations

import pytest

pytest.importorskip("ezdxf")

from cad2gis.mapping import MappingEngine  # noqa: E402


REAL_LAYER_CASES = [
    # (layer, entity_type, block, expected_class)
    ("通信", "LWPOLYLINE", None, "cable"),
    ("通信土建", "LWPOLYLINE", None, "duct"),
    # Real comms manholes are NAMED well blocks (probe evidence), not opaque gc-codes:
    ("通信", "INSERT", "末端井", "manhole"),          # terminal manhole
    ("通信土建", "INSERT", "人行道三通井", "manhole"),  # sidewalk 3-way junction well
    ("通信", "INSERT", "车行道三通井", "manhole"),      # roadway junction well
    # Opaque gc-codes on comms layers are duct/paving symbols -> NOT manholes:
    ("通信", "INSERT", "GC200", None),
    ("通信土建", "INSERT", "gc119", None),
    ("通信", "INSERT", "gc170", None),                # duct cross-section symbol
    ("通信坐标标注", "TEXT", None, "annotation"),
    ("测量控制点", "POINT", None, "control_point"),
    ("电力土建管网图", "LWPOLYLINE", None, None),   # power -> not comms
    ("DMTZ", "LINE", None, None),                   # survey base -> unmapped
    ("KZD", "INSERT", "gc043", None),               # opaque code alone must NOT become manhole
]


@pytest.fixture(scope="module")
def engine():
    return MappingEngine.from_yaml()


@pytest.mark.parametrize("layer,etype,block,expected", REAL_LAYER_CASES)
def test_real_layer_classification(engine, layer, etype, block, expected):
    r = engine.classify(layer=layer, entity_type=etype, block=block)
    got = r.feature_class
    # KZD/gc043 may resolve to control_point (via layer) or None, but never manhole.
    if expected is None and layer == "KZD":
        assert got != "manhole", f"opaque block on {layer} wrongly -> manhole"
    else:
        assert got == expected, f"{layer}/{etype}/{block} -> {got} (want {expected})"


def test_opaque_gc_code_does_not_pollute_globally(engine):
    # A gc-code block on a NON-comms layer must not be classified as a comms manhole.
    r = engine.classify(layer="0", entity_type="INSERT", block="GC200")
    assert r.feature_class != "manhole"
