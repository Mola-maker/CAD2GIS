"""Build the small, repo-native display font used by the CAD2GIS Hero."""

from __future__ import annotations

import math
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "cad2gis" / "webdemo" / "assets" / "cad2gis-hero-display.woff2"
UPM = 1000
CAP = 720
STROKE = 112


def rect(pen: TTGlyphPen, x0: float, y0: float, x1: float, y1: float, reverse: bool = False) -> None:
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if reverse:
        points.reverse()
    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()


def polygon(pen: TTGlyphPen, points: list[tuple[float, float]], reverse: bool = False) -> None:
    points = list(reversed(points)) if reverse else points
    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()


def diagonal(pen: TTGlyphPen, start: tuple[float, float], end: tuple[float, float], width: float = STROKE) -> None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    nx = -dy / length * width / 2
    ny = dx / length * width / 2
    polygon(pen, [
        (start[0] + nx, start[1] + ny),
        (end[0] + nx, end[1] + ny),
        (end[0] - nx, end[1] - ny),
        (start[0] - nx, start[1] - ny),
    ])


def glyph(char: str) -> tuple[object, int]:
    pen = TTGlyphPen(None)
    width = 680
    left = 70
    right = 610
    mid = 304
    if char == " ":
        return pen.glyph(), 320
    if char == "-":
        rect(pen, 120, 304, 560, 416)
        return pen.glyph(), 680
    if char == "/":
        diagonal(pen, (130, 0), (550, CAP), 94)
        return pen.glyph(), 680
    if char == ".":
        rect(pen, 210, 0, 330, 120)
        return pen.glyph(), 420
    if char == ",":
        rect(pen, 210, 0, 330, 120)
        diagonal(pen, (300, 80), (230, -120), 78)
        return pen.glyph(), 420
    if char == ":":
        rect(pen, 210, 475, 330, 595)
        rect(pen, 210, 0, 330, 120)
        return pen.glyph(), 420
    if char == "_":
        rect(pen, 80, -18, 600, 72)
        return pen.glyph(), 680

    if char == "A":
        diagonal(pen, (92, 0), (340, CAP), 124)
        diagonal(pen, (588, 0), (340, CAP), 124)
        rect(pen, 182, 245, 498, 355)
    elif char == "B":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, left, CAP - STROKE, right - 95, CAP)
        rect(pen, left, mid - STROKE / 2, right - 95, mid + STROKE / 2)
        rect(pen, left, 0, right - 95, STROKE)
        rect(pen, right - 175, mid + 10, right, CAP - STROKE)
        rect(pen, right - 175, STROKE, right, mid - 10)
    elif char == "C":
        rect(pen, 170, CAP - STROKE, right, CAP)
        rect(pen, 170, 0, right, STROKE)
        rect(pen, left, STROKE, left + STROKE, CAP - STROKE)
    elif char == "D":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, left, CAP - STROKE, right - 100, CAP)
        rect(pen, left, 0, right - 100, STROKE)
        rect(pen, right - 175, STROKE, right, CAP - STROKE)
    elif char == "E":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, left, CAP - STROKE, right, CAP)
        rect(pen, left, mid - STROKE / 2, right - 75, mid + STROKE / 2)
        rect(pen, left, 0, right, STROKE)
    elif char == "F":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, left, CAP - STROKE, right, CAP)
        rect(pen, left, mid - STROKE / 2, right - 75, mid + STROKE / 2)
    elif char == "G":
        rect(pen, 170, CAP - STROKE, right, CAP)
        rect(pen, left, STROKE, left + STROKE, CAP - STROKE)
        rect(pen, 170, 0, right, STROKE)
        rect(pen, mid, mid - STROKE / 2, right, mid + STROKE / 2)
        rect(pen, right - STROKE, mid - STROKE / 2, right, STROKE)
    elif char == "H":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, right - STROKE, 0, right, CAP)
        rect(pen, left, mid - STROKE / 2, right, mid + STROKE / 2)
    elif char == "I":
        rect(pen, 110, CAP - STROKE, 570, CAP)
        rect(pen, 284, STROKE, 396, CAP - STROKE)
        rect(pen, 110, 0, 570, STROKE)
        width = 680
    elif char == "J":
        rect(pen, 110, CAP - STROKE, 570, CAP)
        rect(pen, right - STROKE, STROKE, right, CAP - STROKE)
        rect(pen, 150, 0, right - STROKE, STROKE)
        rect(pen, left, STROKE, left + STROKE, 210)
    elif char == "K":
        rect(pen, left, 0, left + STROKE, CAP)
        diagonal(pen, (left + 135, mid), (right, CAP), 112)
        diagonal(pen, (left + 135, mid), (right, 0), 112)
    elif char == "L":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, left, 0, right, STROKE)
    elif char == "M":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, right - STROKE, 0, right, CAP)
        diagonal(pen, (left + STROKE / 2, CAP), (mid, 160), 112)
        diagonal(pen, (mid, 160), (right - STROKE / 2, CAP), 112)
    elif char == "N":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, right - STROKE, 0, right, CAP)
        diagonal(pen, (left + STROKE / 2, 0), (right - STROKE / 2, CAP), 112)
    elif char == "O":
        rect(pen, left, 0, right, CAP)
        rect(pen, left + 170, 150, right - 170, CAP - 150, reverse=True)
    elif char == "P":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, left, CAP - STROKE, right - 85, CAP)
        rect(pen, left, mid - STROKE / 2, right - 85, mid + STROKE / 2)
        rect(pen, right - 175, mid + 10, right, CAP - STROKE)
    elif char == "Q":
        rect(pen, left, 0, right, CAP)
        rect(pen, left + 170, 150, right - 170, CAP - 150, reverse=True)
        diagonal(pen, (390, 160), (610, -50), 96)
    elif char == "R":
        rect(pen, left, 0, left + STROKE, CAP)
        rect(pen, left, CAP - STROKE, right - 100, CAP)
        rect(pen, left, mid - STROKE / 2, right - 100, mid + STROKE / 2)
        rect(pen, right - 185, mid + 10, right, CAP - STROKE)
        diagonal(pen, (left + 145, mid), (right, 0), 112)
    elif char == "S":
        rect(pen, 125, CAP - STROKE, right, CAP)
        rect(pen, left, mid, left + STROKE, CAP - STROKE)
        rect(pen, left, mid - STROKE / 2, right - 85, mid + STROKE / 2)
        rect(pen, right - STROKE, STROKE, right, mid)
        rect(pen, 125, 0, right, STROKE)
    elif char == "T":
        rect(pen, 90, CAP - STROKE, 590, CAP)
        rect(pen, 284, 0, 396, CAP - STROKE)
    elif char == "U":
        rect(pen, left, STROKE, left + STROKE, CAP)
        rect(pen, right - STROKE, STROKE, right, CAP)
        rect(pen, 170, 0, right - 170, STROKE)
    elif char == "V":
        diagonal(pen, (90, CAP), (340, 0), 124)
        diagonal(pen, (590, CAP), (340, 0), 124)
    elif char == "W":
        diagonal(pen, (70, CAP), (200, 0), 104)
        diagonal(pen, (200, 0), (340, 470), 104)
        diagonal(pen, (340, 470), (480, 0), 104)
        diagonal(pen, (480, 0), (610, CAP), 104)
        width = 780
        right = 710
    elif char == "X":
        diagonal(pen, (90, 0), (590, CAP), 124)
        diagonal(pen, (590, 0), (90, CAP), 124)
    elif char == "Y":
        diagonal(pen, (90, CAP), (340, 360), 124)
        diagonal(pen, (590, CAP), (340, 360), 124)
        rect(pen, 284, 0, 396, 390)
    elif char == "Z":
        rect(pen, 90, CAP - STROKE, 590, CAP)
        rect(pen, 90, 0, 590, STROKE)
        diagonal(pen, (110, STROKE), (570, CAP - STROKE), 124)
    elif char.isdigit():
        digit_ops = {
            "0": lambda: (rect(pen, left, 0, right, CAP), rect(pen, 220, 150, 460, CAP - 150, reverse=True)),
            "1": lambda: (rect(pen, 284, 0, 396, CAP), rect(pen, 180, CAP - STROKE, 500, CAP)),
            "2": lambda: (rect(pen, 120, CAP - STROKE, 560, CAP), rect(pen, right - STROKE, mid, right, CAP - STROKE), rect(pen, 120, mid - STROKE / 2, 560, mid + STROKE / 2), diagonal(pen, (right - 65, mid), (120, STROKE), 112), rect(pen, 120, 0, 560, STROKE)),
            "3": lambda: (rect(pen, 120, CAP - STROKE, 560, CAP), rect(pen, right - STROKE, STROKE, right, CAP - STROKE), rect(pen, 120, mid - STROKE / 2, 520, mid + STROKE / 2), rect(pen, 120, 0, 560, STROKE)),
            "4": lambda: (diagonal(pen, (120, mid), (420, CAP), 112), rect(pen, 365, 0, 477, CAP), rect(pen, 120, mid - STROKE / 2, 560, mid + STROKE / 2)),
            "5": lambda: (rect(pen, 120, CAP - STROKE, 560, CAP), rect(pen, left, mid, left + STROKE, CAP - STROKE), rect(pen, 120, mid - STROKE / 2, 520, mid + STROKE / 2), rect(pen, right - STROKE, STROKE, right, mid), rect(pen, 120, 0, 560, STROKE)),
            "6": lambda: (rect(pen, 120, CAP - STROKE, 560, CAP), rect(pen, left, STROKE, left + STROKE, CAP - STROKE), rect(pen, 120, mid - STROKE / 2, 520, mid + STROKE / 2), rect(pen, right - STROKE, STROKE, right, mid), rect(pen, 120, 0, 560, STROKE)),
            "7": lambda: (rect(pen, 90, CAP - STROKE, 590, CAP), diagonal(pen, (540, CAP - STROKE), (150, 0), 112)),
            "8": lambda: (rect(pen, left, 0, right, CAP), rect(pen, left + 170, 150, right - 170, CAP - 150, reverse=True), rect(pen, left + 170, mid - 58, right - 170, mid + 58, reverse=True)),
            "9": lambda: (rect(pen, left, mid, left + STROKE, CAP - STROKE), rect(pen, left, CAP - STROKE, right, CAP), rect(pen, right - STROKE, 0, right, CAP - STROKE), rect(pen, 120, 0, 560, STROKE), rect(pen, 120, mid - STROKE / 2, 520, mid + STROKE / 2)),
        }
        digit_ops[char]()
    else:
        rect(pen, 110, 0, 570, STROKE)
        width = 680
    return pen.glyph(), width


def build() -> None:
    characters = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/_.,:"
    glyph_order = [".notdef"] + [char if char != " " else "space" for char in characters]
    glyph_order = list(dict.fromkeys(glyph_order))
    glyphs = {".notdef": glyph("-")[0]}
    metrics: dict[str, tuple[int, int]] = {".notdef": (680, 70)}
    for char in characters:
        name = "space" if char == " " else char
        glyphs[name], advance = glyph(char)
        metrics[name] = (advance, 70)

    cmap = {ord(char): ("space" if char == " " else char) for char in characters}
    font_builder = FontBuilder(UPM, isTTF=True)
    font_builder.setupGlyphOrder(glyph_order)
    font_builder.setupCharacterMap(cmap)
    font_builder.setupGlyf(glyphs)
    font_builder.setupHorizontalMetrics(metrics)
    font_builder.setupHorizontalHeader(ascent=900, descent=-200)
    font_builder.setupOS2(
        sTypoAscender=900,
        sTypoDescender=-200,
        usWinAscent=900,
        usWinDescent=200,
        usWeightClass=800,
        sxHeight=500,
        sCapHeight=CAP,
    )
    font_builder.setupNameTable({
        "familyName": "CAD2GIS Hero Display",
        "styleName": "Block Geometry",
        "fullName": "CAD2GIS Hero Display Block Geometry",
        "psName": "CAD2GISHeroDisplay-BlockGeometry",
        "uniqueFontIdentifier": "CAD2GIS Hero Display 1.0",
        "version": "Version 1.000",
    })
    font_builder.setupPost(keepGlyphNames=True)
    font = font_builder.font
    font.recalcTimestamp = False
    # OpenType timestamps are seconds since 1904; pin them to Unix epoch so
    # repeated builds produce the same WOFF2 bytes.
    font["head"].created = 2082844800
    font["head"].modified = 2082844800
    font.flavor = "woff2"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    font.save(OUTPUT)
    print(f"generated {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
