"""GCP georeferencing (story G9) — recover a real-world transform from in-drawing coordinates.

Historical comms drawings are in LOCAL-ENGINEERING coordinates with no CRS. But the DS-04
organizer drawing annotates surveyed real-world coordinates at node positions as paired
`X=<northing>` / `Y=<easting>` text labels (259 of them). Each label's INSERTION POINT (drawing
coords) paired with its PARSED value (real-world coords) is a Ground Control Point. From these we
fit a transform (similarity/Helmert, then affine) and report residuals/RMSE/max-error so the
positional-accuracy dimension is measured against real ground truth, not assumed.

Model selection (per the independent review): prefer the SIMPLEST model that fits — a similarity
(4-param: scale, rotation, tx, ty) is expected here because the drawing is a rigid survey grid
(with an axis swap). Affine (6-param) is only justified if it reduces RMSE materially. We report
both and pick by residual with a parsimony guard. The transform record (model, params, src/tgt
CRS, residuals, GCP count, outliers) is persisted for the GeoPackage metadata (G10).

Pure numpy least-squares — no external georeferencing dependency.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

# Chinese survey labels: X is Northing, Y is Easting (axes swapped vs CAD x=East/y=North).
_X_RE = re.compile(r"X\s*=\s*(-?\d+\.?\d*)")
_Y_RE = re.compile(r"Y\s*=\s*(-?\d+\.?\d*)")


@dataclass
class GCP:
    src_x: float          # drawing coordinate (from the label's insertion point)
    src_y: float
    dst_x: float          # real-world coordinate (parsed from the label text)
    dst_y: float
    label: str = ""


@dataclass
class TransformFit:
    model: str                       # "similarity" | "affine"
    params: list = field(default_factory=list)  # model coefficients (row-major)
    n_gcps: int = 0
    rmse: float = 0.0
    max_error: float = 0.0
    residuals: list = field(default_factory=list)  # per-GCP residual distance
    src_crs: str = "local-engineering"
    dst_crs: Optional[str] = None
    outliers: list = field(default_factory=list)   # indices dropped as outliers

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "params": [float(p) for p in self.params],
            "n_gcps": self.n_gcps,
            "rmse": round(self.rmse, 4),
            "max_error": round(self.max_error, 4),
            "src_crs": self.src_crs,
            "dst_crs": self.dst_crs,
            "outliers": list(self.outliers),
        }


def extract_gcps_from_labels(path: str, pair_radius: float = 3.0) -> list[GCP]:
    """Pair X=/Y= labels that annotate the same node into GCPs.

    Each node carries an `X=<northing>` and a `Y=<easting>` label placed close together. We pair
    an X-label with the nearest Y-label within pair_radius and use the X-label's insertion point
    as the drawing (source) coordinate.
    """
    import ezdxf

    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    x_labels: list[tuple[float, float, float, str]] = []
    y_labels: list[tuple[float, float, float]] = []
    for e in msp:
        et = e.dxftype()
        if et not in ("TEXT", "MTEXT"):
            continue
        t = (e.text if et == "MTEXT" else e.dxf.text) or ""
        t = t.strip()
        mx, my = _X_RE.search(t), _Y_RE.search(t)
        ins = e.dxf.insert
        if mx:
            x_labels.append((float(ins[0]), float(ins[1]), float(mx.group(1)), t))
        if my:
            y_labels.append((float(ins[0]), float(ins[1]), float(my.group(1))))

    gcps: list[GCP] = []
    used: set[int] = set()
    for dx, dy, northing, txt in x_labels:
        best_j, best_d = None, pair_radius
        for j, (yx, yy, easting) in enumerate(y_labels):
            if j in used:
                continue
            d = math.hypot(yx - dx, yy - dy)
            if d <= best_d:
                best_j, best_d = j, d
        if best_j is not None:
            used.add(best_j)
            easting = y_labels[best_j][2]
            # dst: real-world (easting=Y as X-East, northing=X as Y-North) -> standard (E, N)
            gcps.append(GCP(src_x=dx, src_y=dy, dst_x=easting, dst_y=northing, label=txt))
    return gcps


def _fit_similarity(src, dst):
    """Least-squares 4-param similarity: [X;Y] = [a -b; b a][x;y] + [tx;ty]. Handles rotation+
    uniform scale+translation (and reflection via the affine fallback). Returns params a,b,tx,ty."""
    import numpy as np

    n = len(src)
    A = np.zeros((2 * n, 4))
    B = np.zeros(2 * n)
    for i, (s, d) in enumerate(zip(src, dst)):
        x, y = s
        X, Y = d
        A[2 * i] = [x, -y, 1, 0]
        A[2 * i + 1] = [y, x, 0, 1]
        B[2 * i] = X
        B[2 * i + 1] = Y
    p, *_ = np.linalg.lstsq(A, B, rcond=None)
    return p  # a, b, tx, ty


def _apply_similarity(p, x, y):
    a, b, tx, ty = p
    return a * x - b * y + tx, b * x + a * y + ty


def _fit_affine(src, dst):
    """Least-squares 6-param affine: X = a x + b y + c ; Y = d x + e y + f. Handles the axis
    swap/reflection present in survey grids exactly."""
    import numpy as np

    n = len(src)
    A = np.zeros((2 * n, 6))
    B = np.zeros(2 * n)
    for i, (s, d) in enumerate(zip(src, dst)):
        x, y = s
        X, Y = d
        A[2 * i] = [x, y, 1, 0, 0, 0]
        A[2 * i + 1] = [0, 0, 0, x, y, 1]
        B[2 * i] = X
        B[2 * i + 1] = Y
    p, *_ = np.linalg.lstsq(A, B, rcond=None)
    return p  # a,b,c,d,e,f


def _apply_affine(p, x, y):
    a, b, c, d, e, f = p
    return a * x + b * y + c, d * x + e * y + f


def _residuals(gcps, apply, params):
    res = []
    for g in gcps:
        px, py = apply(params, g.src_x, g.src_y)
        res.append(math.hypot(px - g.dst_x, py - g.dst_y))
    return res


def _rmse(res):
    return math.sqrt(sum(r * r for r in res) / len(res)) if res else 0.0


def fit_transform(
    gcps: list[GCP],
    dst_crs: Optional[str] = None,
    outlier_sigma: float = 3.0,
    affine_gain: float = 0.5,
) -> TransformFit:
    """Fit the simplest justified transform with one robust outlier-rejection pass.

    1. Fit similarity + affine on all GCPs.
    2. Drop GCPs whose similarity residual exceeds outlier_sigma * RMSE (survey typos/misplaced
       labels), refit.
    3. Pick affine over similarity only if it reduces RMSE by >= affine_gain fraction (parsimony).
    """
    import numpy as np  # noqa: F401  (ensures numpy present; used by _fit_*)

    if len(gcps) < 3:
        return TransformFit(model="none", n_gcps=len(gcps), dst_crs=dst_crs)

    src = [(g.src_x, g.src_y) for g in gcps]
    dst = [(g.dst_x, g.dst_y) for g in gcps]
    sim = _fit_similarity(src, dst)
    sim_res = _residuals(gcps, _apply_similarity, sim)
    sim_rmse = _rmse(sim_res)

    # outlier rejection on the similarity residuals
    outliers = []
    if sim_rmse > 0:
        thresh = outlier_sigma * sim_rmse
        keep = [i for i, r in enumerate(sim_res) if r <= thresh]
        outliers = [i for i in range(len(gcps)) if i not in keep]
        if outliers and len(keep) >= 3:
            gk = [gcps[i] for i in keep]
            src = [(g.src_x, g.src_y) for g in gk]
            dst = [(g.dst_x, g.dst_y) for g in gk]
            sim = _fit_similarity(src, dst)
            sim_res = _residuals(gk, _apply_similarity, sim)
            sim_rmse = _rmse(sim_res)
            gcps_used = gk
        else:
            gcps_used = gcps
            outliers = []
    else:
        gcps_used = gcps

    aff = _fit_affine(src, dst)
    aff_res = _residuals(gcps_used, _apply_affine, aff)
    aff_rmse = _rmse(aff_res)

    # parsimony: prefer similarity unless affine cuts RMSE by >= affine_gain fraction
    if sim_rmse > 0 and aff_rmse <= sim_rmse * (1 - affine_gain):
        model, params, res, rmse = "affine", aff, aff_res, aff_rmse
    else:
        model, params, res, rmse = "similarity", list(sim), sim_res, sim_rmse

    return TransformFit(
        model=model,
        params=list(params),
        n_gcps=len(gcps_used),
        rmse=rmse,
        max_error=max(res) if res else 0.0,
        residuals=[round(r, 4) for r in res],
        dst_crs=dst_crs,
        outliers=outliers,
    )


def apply_transform(fit: TransformFit, x: float, y: float) -> tuple[float, float]:
    if fit.model == "affine":
        return _apply_affine(fit.params, x, y)
    if fit.model == "similarity":
        return _apply_similarity(fit.params, x, y)
    return x, y


def refine_gcps_to_nodes(
    gcps: list[GCP],
    node_positions: list[tuple[float, float]],
    predict_radius: float = 15.0,
) -> list[GCP]:
    """Consensus re-pairing (Codex G9 #2): the X=/Y= TEXT label is placed a few metres BESIDE the
    node it describes, so the label's insertion point is a noisy source coordinate. Instead of
    using the label insertion point, we re-pair each surveyed coordinate to the real node:
      1. fit a rough transform from the label-insertion GCPs,
      2. forward-transform every node position into real-world coords,
      3. match each GCP's surveyed dst coord to the node whose transformed position is closest,
      4. replace the GCP source with that node's true drawing position, then refit.
    Matching by predicted real-world position (not raw label proximity) avoids dense-area mis-pair.
    """
    if len(gcps) < 3 or not node_positions:
        return gcps
    rough = fit_transform(gcps)
    if rough.model == "none":
        return gcps
    # transformed node positions (drawing -> real world)
    tnodes = [apply_transform(rough, nx, ny) for nx, ny in node_positions]
    refined: list[GCP] = []
    used: set[int] = set()
    for g in gcps:
        best_k, best_d = None, None
        for k, (tx, ty) in enumerate(tnodes):
            if k in used:
                continue
            d = math.hypot(tx - g.dst_x, ty - g.dst_y)
            if best_d is None or d < best_d:
                best_k, best_d = k, d
        if best_k is not None and best_d is not None and best_d <= predict_radius:
            used.add(best_k)
            nx, ny = node_positions[best_k]
            refined.append(GCP(src_x=nx, src_y=ny, dst_x=g.dst_x, dst_y=g.dst_y, label=g.label))
    # if consensus matched too few, keep the original label GCPs (never make it worse)
    return refined if len(refined) >= max(3, len(gcps) // 2) else gcps


def write_transform_record(fit: TransformFit, path: str, extra: Optional[dict] = None) -> None:
    """Persist the transform record (for GeoPackage metadata, G10) — the georeferencing audit trail."""
    import json
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rec = fit.to_dict()
    if extra:
        rec.update(extra)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
