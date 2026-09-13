"""Offline benchmark provenance, CRS handling, and interpretation contracts."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import struct
import sys
from types import SimpleNamespace

from pyproj import CRS
import pytest
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import benchmark_osm_matching as benchmark  # noqa: E402
from baseline_materials import baseline_nominal_routes  # noqa: E402


def make_layer(path, layer, routes, srs_id=3857):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT,column_name TEXT,srs_id INTEGER)")
        db.execute("CREATE TABLE gpkg_spatial_ref_sys (srs_id INTEGER,definition TEXT)")
        db.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?)", (layer, srs_id))
        db.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?)", (srs_id, CRS.from_epsg(srs_id).to_wkt()))
        db.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY,geom BLOB,osm_id TEXT,name TEXT)')
        for index, points in enumerate(routes):
            geometry = LineString(points)
            xmin, ymin, xmax, ymax = geometry.bounds
            blob = b"GP\x00\x03" + struct.pack("<i4d", srs_id, xmin, xmax, ymin, ymax) + geometry.wkb
            db.execute(f'INSERT INTO "{layer}" VALUES (?, ?, ?, ?)', (index + 1, blob, str(index + 500), ""))


def test_gpkg_reader_uses_declared_crs_and_does_not_modify_input(tmp_path):
    path = tmp_path / "roads.gpkg"
    make_layer(path, "roads", [[(0, 0), (1, 0)]], 4326)
    before = benchmark.file_hash(path)
    features, metadata = benchmark.read_lines(path, "roads")
    assert features[0]["points"][1] == pytest.approx([111319.49079327357, 0])
    assert metadata["declared_srs_id"] == 4326
    assert metadata["sha256"] == before == benchmark.file_hash(path)
    assert features[0]["attributes"]["osm_id"] == "500"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("blob", [b"not-gpkg", b"GP\x00\x01" + struct.pack("<i", 4326) + LineString([(0, 0), (1, 1)]).wkb,
                                  b"GP\x00\x11" + struct.pack("<i", 3857) + b"invalid"])
def test_gpkg_reader_rejects_invalid_header_crs_or_empty_geometry(blob):
    with pytest.raises(ValueError):
        benchmark.decode_geometry(blob, 3857)


def test_perturbation_reports_equivariance_without_ground_truth_claim(tmp_path):
    routes = [[(100, 200), (200, 200), (220, 250)], [(200, 200), (200, 120), (270, 100)]]
    make_layer(tmp_path / "delivery.gpkg", "CABLE", routes)
    make_layer(tmp_path / "osm-basemap.gpkg", "roads", routes)

    def relative_fit(values, roads, scale, initial):
        # An arbitrary common bias cancels in equivariance, demonstrating why
        # zero equivariance error must never be called geolocation accuracy.
        return [130 - values[0][0][0], 180 - values[0][0][1]], {"reason": None}

    result = benchmark.benchmark_case(SimpleNamespace(_match_routes=relative_fit), tmp_path, [12, -7])
    assert result["unperturbed"]["translation_3857"] == [30, -20]
    assert result["perturbation"]["accepted_solution_equivariance_error_m_approx"] == pytest.approx(0, abs=1e-10)
    assert result["absolute_accuracy_verified"] is False
    assert "not_geolocation_ground_truth" in result["input_role"]


def test_rejected_fit_has_no_perturbation_accuracy_number(tmp_path):
    routes = [[(0, 0), (100, 0), (100, 100)]]
    make_layer(tmp_path / "delivery.gpkg", "CABLE", routes)
    make_layer(tmp_path / "osm-basemap.gpkg", "roads", routes)
    matcher = SimpleNamespace(_match_routes=lambda *_: (None, {"reason": "ambiguous"}))
    result = benchmark.benchmark_case(matcher, tmp_path, [10, 5])
    assert result["perturbation"]["accepted_solution_equivariance_error_m_approx"] is None
    assert result["perturbation"]["both_fits_accepted"] is False


def test_benchmark_detects_mutated_input_vectors(tmp_path):
    routes = [[(0, 0), (100, 0), (100, 100)]]
    make_layer(tmp_path / "delivery.gpkg", "CABLE", routes)
    make_layer(tmp_path / "osm-basemap.gpkg", "roads", routes)

    def mutate(values, *_):
        values[0][0][0] += 10
        return None, {}

    with pytest.raises(ValueError, match="mutated"):
        benchmark.benchmark_case(SimpleNamespace(_match_routes=mutate), tmp_path)


def test_taipa_numerical_candidates_converge_before_ambiguity_decision(tmp_path):
    """A 20-iteration cap left 0.385 m duplicates of the same local optimum.

    Read the protected QGZ and source-bound road snapshot at runtime. Joining
    ordered source segments and undoing the recorded translation recovers the
    nominal source routes without keeping another committed geometry copy.
    A known perturbation checks the solution independently of its map position.
    """
    from cad2gis.cad2gis_v3 import osm_matching

    # Keep this regression pinned even if a future conversion republishes the
    # protected baseline. Deliberate fixture updates require reviewing the fit.
    assert benchmark.file_hash(ROOT / 'baselines/taipa/delivery/view-with-source-SVG.qgz') == (
        'dd60a386e9d52dad53ca13e0c93365461aae19800deee0e8603cd75fed4de199'
    )
    import hashlib
    # Git may check this text out with LF or CRLF; pin identical JSON bytes
    # after explicitly normalizing only line endings, not coordinate values.
    raw = (ROOT / 'baselines/taipa/config/matching-manual-gcp.json').read_bytes()
    assert hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() == (
        '9d9f07252ffd4f463a6da329c433d498d55f81bff80a11356aa127903f4cb280'
    )
    routes, match = baseline_nominal_routes('taipa')
    make_layer(tmp_path / 'delivery.gpkg', 'CABLE', routes)
    make_layer(tmp_path / 'osm-basemap.gpkg', 'roads', [road['points'] for road in match['osm_snapshot']['roads']])
    result = benchmark.benchmark_case(osm_matching, tmp_path, [12, -7])
    fit = result["unperturbed"]
    assert fit["accepted"] is True
    assert result['perturbation']['both_fits_accepted'] is True
    assert result['perturbation']['accepted_solution_equivariance_error_m_approx'] < 0.1
    assert fit["quality"]["candidates"][0]["refinement_converged"] is True
    assert fit["quality"]["confidence_gap"] >= 0.06
    assert result["absolute_accuracy_verified"] is False


def test_taipa_text_specimen_pin_is_checkout_independent():
    import hashlib
    raw = (ROOT / 'baselines/taipa/config/matching-manual-gcp.json').read_bytes()
    lf = raw.replace(b'\r\n', b'\n')
    for checkout in (lf, lf.replace(b'\n', b'\r\n')):
        assert hashlib.sha256(checkout.replace(b'\r\n', b'\n')).hexdigest() == (
            '9d9f07252ffd4f463a6da329c433d498d55f81bff80a11356aa127903f4cb280'
        )
