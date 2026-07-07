"""Feature hit-vector extraction (story G8b) — the multi-signal classification context.

Layer name alone cannot classify the real comms drawings: on the DS-04 organizer file 2,170
INSERTs sit on the 通信 layers but resolve to only 23 distinct block codes, split into
  (a) NAMED well blocks (末端井/三通井/四通井/直通井) = the real manholes, and
  (b) OPAQUE gc* codes that are NOT self-describing — gc170 is a duct cross-section symbol,
      gc043 is a paving/surface-restoration symbol — and layer+block-code cannot tell them apart.

The signal that DOES separate them is the **nearest TEXT label**: gc170/gc013* sit next to
duct-hole labels (3孔PVC110 / 3孔BD100 / 12孔PVC110), while gc043/gc041 sit next to paving
labels (地砖 / 水泥 / 砼). So we extract a per-INSERT hit vector and let the engine consult a
reviewed block-code table + the nearest-text evidence to resolve the opaque codes.

This module is pure extraction — no classification, no shapely-heavy topology. It builds a
spatial index of TEXT/MTEXT once and answers nearest-label queries for each INSERT. The result
(`FeatureContext`) is consumed by `MappingEngine.classify_context()`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeatureContext:
    """The multi-signal hit vector for one CAD entity (an INSERT block reference)."""

    layer: Optional[str] = None
    block: Optional[str] = None
    entity_type: Optional[str] = None
    handle: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    nearest_text: Optional[str] = None      # label text of the nearest annotation (winner)
    nearest_text_dist: Optional[float] = None
    text_candidates: list = field(default_factory=list)  # [(text, dist), ...] ranked kNN, for the gate
    attrib_text: list = field(default_factory=list)       # ATTRIB values carried by the INSERT itself
    fingerprint: dict = field(default_factory=dict)       # primitive-type -> count for the block def

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def block_fingerprint(doc, block_name: str) -> dict:
    """Count primitive entity types inside a block definition — a scale/rotation-invariant
    shape signature (e.g. {'CIRCLE': 1} for a duct cross-section, LWPOLYLINE-heavy for a well)."""
    from collections import Counter

    try:
        blk = doc.blocks.get(block_name)
    except Exception:  # noqa: BLE001
        return {}
    c: Counter = Counter()
    for e in blk:
        c[e.dxftype()] += 1
    return dict(c)


@dataclass
class _TextIndex:
    """Lightweight spatial index over annotation points for nearest-label queries."""

    xs: list = field(default_factory=list)
    ys: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    _tree: object = None

    def build(self):
        try:
            from shapely import STRtree
            from shapely.geometry import Point

            geoms = [Point(x, y) for x, y in zip(self.xs, self.ys)]
            self._tree = STRtree(geoms) if geoms else None
            self._geoms = geoms
        except Exception:  # noqa: BLE001
            self._tree = None
        return self

    def nearest(self, x: float, y: float, radius: float, k: int = 5):
        """Return the k nearest labels within radius as a ranked [(text, dist), ...] list.

        Codex review: a single nearest label is brittle when a paving note sits slightly closer
        than the true duct label. Returning the top-k lets the evidence gate check whether ANY
        candidate is a duct label while a paving-veto can still reject if only paving terms appear.
        """
        cands: list[tuple[str, float]] = []
        if self._tree is not None:
            from shapely.geometry import Point

            p = Point(x, y)
            for j in self._tree.query(p.buffer(radius)):
                d = p.distance(self._geoms[j])
                if d <= radius:
                    cands.append((self.texts[j], d))
        else:
            for tx, ty, tt in zip(self.xs, self.ys, self.texts):
                d = math.hypot(tx - x, ty - y)
                if d <= radius:
                    cands.append((tt, d))
        cands.sort(key=lambda c: c[1])
        return cands[:k]


def build_text_index(doc) -> _TextIndex:
    """Index every TEXT/MTEXT insertion point + its string from a DXF modelspace."""
    idx = _TextIndex()
    msp = doc.modelspace()
    for e in msp:
        et = e.dxftype()
        if et not in ("TEXT", "MTEXT"):
            continue
        try:
            ins = e.dxf.insert
            txt = (e.text if et == "MTEXT" else e.dxf.text) or ""
        except Exception:  # noqa: BLE001
            continue
        txt = txt.strip()
        if not txt:
            continue
        idx.xs.append(float(ins[0]))
        idx.ys.append(float(ins[1]))
        idx.texts.append(txt)
    return idx.build()


def extract_insert_contexts(path: str, text_radius: float = 5.0) -> list[FeatureContext]:
    """Extract a hit vector for every INSERT in the drawing.

    text_radius is in drawing units — the DS-04 file is in metres-scale local-engineering
    coords, and the probe showed duct/paving labels sit within a few units of their symbol.
    """
    import ezdxf

    doc = ezdxf.readfile(path)
    tindex = build_text_index(doc)
    fp_cache: dict[str, dict] = {}
    out: list[FeatureContext] = []
    for e in doc.modelspace():
        if e.dxftype() != "INSERT":
            continue
        bname = e.dxf.name or ""
        ins = e.dxf.insert
        x, y = float(ins[0]), float(ins[1])
        if bname not in fp_cache:
            fp_cache[bname] = block_fingerprint(doc, bname)
        cands = tindex.nearest(x, y, text_radius)
        # ATTRIB values carried by this INSERT itself are the strongest label evidence (Codex #3).
        attribs: list[str] = []
        try:
            for a in e.attribs:
                t = (a.dxf.text or "").strip()
                if t:
                    attribs.append(t)
        except Exception:  # noqa: BLE001
            pass
        out.append(
            FeatureContext(
                layer=getattr(e.dxf, "layer", None),
                block=bname,
                entity_type="INSERT",
                handle=getattr(e.dxf, "handle", None),
                x=x,
                y=y,
                nearest_text=cands[0][0] if cands else None,
                nearest_text_dist=cands[0][1] if cands else None,
                text_candidates=cands,
                attrib_text=attribs,
                fingerprint=fp_cache[bname],
            )
        )
    return out
