"""CRS classification heuristics (story G4/G9, pure-python part).

Historical comms drawings frequently carry NO georeference: coordinates may be true
lon/lat, a projected grid (China Gauss-Kruger / CGCS2000), local engineering coordinates,
or paper/layout units. Before any CRS assignment or GCP transform (G9), classify the
drawing extent so the pipeline picks the right georeferencing path. This is a heuristic
guess with a confidence, never a silent assumption.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CRSGuess:
    label: str  # geographic | projected | local-engineering | layout | unknown
    confidence: float
    note: str = ""
    epsg: Optional[int] = None

    def to_dict(self) -> dict:
        return {"label": self.label, "confidence": round(self.confidence, 3), "note": self.note, "epsg": self.epsg}


def classify_extent(minx: float, miny: float, maxx: float, maxy: float) -> CRSGuess:
    spanx, spany = maxx - minx, maxy - miny

    # Geographic degrees: fully within lon/lat bounds.
    if -180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90:
        if spanx <= 5 and spany <= 5 and abs(minx) < 1 and abs(miny) < 1:
            return CRSGuess("local-engineering", 0.5, "tiny extent near origin; ambiguous with geographic")
        # In a China comms context, assume CGCS2000 geographic (EPSG:4490).
        return CRSGuess("geographic", 0.8, "within lon/lat bounds", epsg=4490)

    # China Gauss-Kruger / UTM-like projected grid: large planar eastings/northings.
    if 1e5 <= maxx <= 6e7 and 1e6 <= maxy <= 6e6:
        return CRSGuess("projected", 0.6, "large planar coords (Gauss-Kruger / UTM-like)")

    # Very small positive extents can be paper/layout space (mm).
    if 0 <= minx and maxx <= 2000 and 0 <= miny and maxy <= 2000 and spanx <= 2000 and spany <= 2000:
        return CRSGuess("local-engineering", 0.55, "small planar extent; local engineering or layout coords")

    return CRSGuess("local-engineering", 0.5, "coords not geographic/projected; treat as local")
