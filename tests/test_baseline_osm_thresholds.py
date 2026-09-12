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

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'tests/data/baseline-osm-thresholds.json').read_text(encoding='utf-8'))['cases']


@pytest.mark.parametrize('name', CASES)
def test_real_routes_accept_bounded_candidates_with_relaxed_thresholds(name):
    case = CASES[name]
    raw = (ROOT / case['cache_file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == case['cache_sha256']
    cache = json.loads(raw)
    assert osm_matching._digest(cache['roads']) == cache['sha256']
    translation, quality = osm_matching._match_routes(
        case['routes_3857'], osm_matching._normalise_roads(cache['roads']),
        math.cos(math.radians(case['reference_latitude'])), [0.0, 0.0],
    )
    assert translation == pytest.approx(case['expected_translation_3857'], abs=0.01)
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
    transformed, record = osm_matching.load_match_profile(config.match_profile, CASES[name]['source_sha256'], nominal)
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
