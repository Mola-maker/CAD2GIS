"""Replay real source routes against frozen roads, without DWG or network access."""
import hashlib
import json
import math
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3 import osm_matching
from cad2gis.cad2gis_v3.georef import GeoDataTransformer
from cad2gis.cad2gis_v3.units import build_unit_crs_contract
from cad2gis.pipeline import resolve_project_configuration
from cad2gis.qgis_review import coordinate_pair
from baseline_materials import baseline_nominal_routes

ROOT = Path(__file__).resolve().parents[1]
# Regression assertions remain pinned; real route coordinates are read from the
# protected QGZ at runtime instead of duplicated in a committed JSON specimen.
CASES = {
    'lamteh_main': ('8cfb0e111e5f92696b38da9e2cf0233955a6b292604c6ede1c6d9a1686392f3f',
                    [31.889182695836283, 45.62468652750254]),
    'lamteh_sf': ('7612644b99cfab311910069cfdc06e3845536ea93b2097785e08df6fc6b17a8c',
                  [29.79780034637025, 47.22677313767478]),
    'tinggede': ('053202788281d676ec367b0496838710e24876e0849da622dd2115c7d5d0bd83',
                 [-5.831823489861377, -16.44178131427027]),
}


@pytest.mark.parametrize('name', CASES)
def test_real_routes_accept_bounded_candidates_with_relaxed_thresholds(name):
    routes, match = baseline_nominal_routes(name)
    paths = list((ROOT / 'baselines' / name / 'config/.osm-cache').glob('osm-roads-*.json'))
    assert len(paths) == 1
    raw = paths[0].read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CASES[name][0]
    cache = json.loads(raw)
    assert osm_matching._digest(cache['roads']) == cache['sha256']
    translation, quality = osm_matching._match_routes(
        routes, osm_matching._normalise_roads(cache['roads']),
        math.cos(math.radians(match['base']['reference_latitude'])), [0.0, 0.0],
    )
    assert translation == pytest.approx(CASES[name][1], abs=0.01)
    assert quality['reason'] is None
    assert quality['candidates'][0]['refinement_converged']
    assert quality['candidates'][0]['observability']['observable']
    assert quality['thresholds']['supported_route_fraction'] == 0.70
    assert quality['thresholds']['confidence_gap'] == 0.05
    assert quality['thresholds']['minimum_normal_eigenvalue'] == 0.035


@pytest.mark.parametrize('name', CASES)
def test_project_selected_controls_remain_source_bound_and_drive_real_transform(name):
    config = resolve_project_configuration(project_dir=ROOT / 'baselines' / name, discover_match=True)
    profile = json.loads(config.source_profile.read_text(encoding='utf-8'))
    crs, drawing = profile['crs'], profile['drawing']
    units = build_unit_crs_contract(
        drawing['dwg_insunits'], crs['source_crs'], crs['target_crs'],
        source_coordinate_scale_to_m=drawing['source_coordinate_scale_to_m'],
        source_coordinate_scale_reviewed=True,
    )
    nominal = GeoDataTransformer(crs['source_crs'], crs['target_crs'], unit_contract=units,
                                 geodata_registration=crs['geodata_registration'])
    source_sha256 = profile['source_binding']['source_sha256']
    transformed, record = osm_matching.load_match_profile(config.match_profile, source_sha256, nominal)
    assert record['status'] == 'matched'
    assert record['revision'] == 2
    for control in record['controls']:
        point = transformed.point([control['cad_x'], control['cad_y']])
        error = nominal.target_length_to_m(math.dist(point, [control['target_easting'], control['target_northing']]))
        assert error < 14
    if name.startswith('lamteh'):
        evidence = json.loads(config.match_profile.with_suffix('.evidence.json').read_text(encoding='utf-8'))
        assert evidence['match_sha256'] == record['match_sha256']
        assert evidence['source_sha256'] == record['source_sha256']
        rows = {row['cad_handle']: row for row in evidence['source_text']}
        controls = {control['point_id']: control for control in record['controls']}
        for label, identity, coordinate in [('MR.KLDYA.P001', '1CC2AC', '1CC2AE'),
                                            ('MR.KLDYA.P137', '1CC30A', '1CC30C')]:
            assert rows[identity]['owner_handle'] == rows[coordinate]['owner_handle']
            assert rows[identity]['text'] == 'EXT.' + label
            assert coordinate_pair(rows[coordinate]['text']) == (controls[label]['longitude'], controls[label]['latitude'])
        assert record['translation_3857'] == pytest.approx([35.25554061215371, 48.88529218023177])
