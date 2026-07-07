"""DXF profiler (story G4) — inventory an unknown drawing before mapping.

Real organizer drawings are large (80k+ entities, 100+ layers, 160+ blocks) and mix many
disciplines (comms/power/water/survey base) in one file. Before semantic mapping we profile
the drawing so the pipeline (and a human) can see what's actually there and adapt the mapping
dictionary to real layer/block names — the antidote to a self-fulfilling synthetic benchmark.

Encoding note: these DXFs declare $DWGCODEPAGE ANSI_936; ezdxf decodes layer/block/text to
correct UTF-8. Any 'mojibake' is only the Windows GBK *console* re-corrupting on print — so we
always write UTF-8 artifacts, never rely on stdout for CJK.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Profile:
    source: str
    dxfversion: str = ""
    acad_release: str = ""
    encoding: str = ""
    n_entities: int = 0
    layers_defined: int = 0
    blocks_defined: int = 0
    entity_types: dict = field(default_factory=dict)
    layer_counts: dict = field(default_factory=dict)
    block_counts: dict = field(default_factory=dict)
    comms_layers: list = field(default_factory=list)
    control_point_layers: list = field(default_factory=list)
    extent: Optional[tuple] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def write_json(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)


# Heuristic markers for discipline/role detection on real drawings.
_COMMS_MARKERS = ("通信", "光缆", "光纤")
_COMMS_CODE_PREFIXES = ("TX", "GX")
_CONTROL_MARKERS = ("控制点", "坐标", "节点")
_CONTROL_CODES = ("KZD", "GXYZ", "GPS")


def profile_dxf(path: str) -> Profile:
    import ezdxf

    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    prof = Profile(
        source=os.path.basename(path),
        dxfversion=doc.dxfversion,
        acad_release=doc.acad_release,
        encoding=getattr(doc, "encoding", ""),
        layers_defined=len(doc.layers),
        blocks_defined=len(doc.blocks),
    )
    etypes: Counter = Counter()
    layer_counts: Counter = Counter()
    block_counts: Counter = Counter()
    for e in msp:
        etypes[e.dxftype()] += 1
        layer_counts[getattr(e.dxf, "layer", "?")] += 1
        if e.dxftype() == "INSERT":
            block_counts[e.dxf.name] += 1
    prof.n_entities = sum(etypes.values())
    prof.entity_types = dict(etypes.most_common())
    prof.layer_counts = dict(layer_counts.most_common())
    prof.block_counts = dict(block_counts.most_common())

    def _is_comms(layer: str) -> bool:
        u = layer.upper()
        return any(m in layer for m in _COMMS_MARKERS) or u.startswith(_COMMS_CODE_PREFIXES)

    def _is_control(layer: str) -> bool:
        u = layer.upper()
        return any(m in layer for m in _CONTROL_MARKERS) or any(u.startswith(c) for c in _CONTROL_CODES)

    prof.comms_layers = sorted([l for l in layer_counts if _is_comms(l)])
    prof.control_point_layers = sorted([l for l in layer_counts if _is_control(l)])

    try:
        emin = doc.header.get("$EXTMIN", (0, 0, 0))
        emax = doc.header.get("$EXTMAX", (0, 0, 0))
        prof.extent = (float(emin[0]), float(emin[1]), float(emax[0]), float(emax[1]))
    except Exception:  # noqa: BLE001
        prof.extent = None
    return prof
