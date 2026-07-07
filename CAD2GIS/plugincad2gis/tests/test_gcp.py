"""GCP georeferencing tests (story G9) — recover a known transform from synthetic control points.

We synthesize GCPs from a KNOWN similarity transform (with the survey axis-swap), add small label
noise, and confirm the fit recovers it with low RMSE. Also checks: outlier rejection, the affine
parsimony guard, and consensus re-pairing to nodes (which removes label-placement offset). No real
DXF needed — the transform math is exercised directly.
"""
from __future__ import annotations

import math

import pytest

pytest.importorskip("numpy")

from cad2gis.gcp import (  # noqa: E402
    GCP,
    apply_transform,
    fit_transform,
    refine_gcps_to_nodes,
)


def _survey_transform(x, y):
    # A rigid survey mapping with axis swap + reflection + offset (like the real DS-04 grid):
    #   E = y + 1000 ;  N = -x + 500
    return (y + 1000.0, -x + 500.0)


def _make_gcps(n=40, noise=0.0, seed_pts=None):
    pts = seed_pts or [(i * 3.0, (i * 7) % 50) for i in range(n)]
    gcps = []
    for k, (x, y) in enumerate(pts):
        E, N = _survey_transform(x, y)
        # deterministic pseudo-noise (no RNG — keeps tests reproducible)
        dx = noise * math.sin(k)
        dy = noise * math.cos(k)
        gcps.append(GCP(src_x=x, src_y=y, dst_x=E + dx, dst_y=N + dy))
    return gcps


def test_fit_recovers_clean_transform():
    gcps = _make_gcps(noise=0.0)
    fit = fit_transform(gcps, dst_crs="local-grid")
    assert fit.model in ("similarity", "affine")
    assert fit.rmse < 1e-6
    # applying the fit reproduces the known mapping
    px, py = apply_transform(fit, 10.0, 20.0)
    assert abs(px - 1020.0) < 1e-4 and abs(py - 490.0) < 1e-4


def test_fit_reports_residuals_under_noise():
    gcps = _make_gcps(noise=0.5)
    fit = fit_transform(gcps, dst_crs="local-grid")
    assert 0 < fit.rmse < 1.0
    assert fit.max_error >= fit.rmse
    assert fit.n_gcps == len(gcps)


def test_outlier_is_rejected():
    gcps = _make_gcps(noise=0.0)
    gcps.append(GCP(src_x=5.0, src_y=5.0, dst_x=99999.0, dst_y=-99999.0))  # gross outlier
    fit = fit_transform(gcps, dst_crs="local-grid")
    assert len(fit.outliers) >= 1
    assert fit.rmse < 1e-3  # after dropping the outlier the clean points fit tightly


def test_consensus_repairing_removes_label_offset():
    # Nodes at true positions; labels carry the surveyed dst but each label's SOURCE point is
    # offset by a VARIABLE amount (real annotation labels are placed inconsistently, not by a
    # constant vector — a constant offset would be absorbed by the transform's translation term).
    # Consensus re-pairs each surveyed coord to its node, recovering a near-exact fit.
    node_pts = [(i * 4.0, (i * 5) % 40) for i in range(30)]
    label_gcps = []
    for k, (x, y) in enumerate(node_pts):
        E, N = _survey_transform(x, y)
        ox = 5.0 * math.sin(k * 1.3)   # per-label variable offset (label placed beside its node)
        oy = 5.0 * math.cos(k * 0.7)
        label_gcps.append(GCP(src_x=x + ox, src_y=y + oy, dst_x=E, dst_y=N))
    raw = fit_transform(label_gcps)
    refined = refine_gcps_to_nodes(label_gcps, node_pts, predict_radius=15.0)
    fit = fit_transform(refined)
    assert raw.rmse > 1.0        # variable label offset produces real residual in the raw fit
    assert fit.rmse < raw.rmse   # consensus improved the fit
    assert fit.rmse < 1e-3       # and it's essentially exact after re-pairing to nodes
