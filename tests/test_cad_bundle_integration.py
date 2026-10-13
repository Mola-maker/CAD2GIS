import json
import os

import pytest


@pytest.mark.skipif(not os.environ.get('CAD2GIS_CAD_BUNDLE_COMMAND'), reason='optional pinned sidecar not configured')
def test_real_sidecar_edit_preserves_source_and_converts(tmp_path):
    from tools.verify_cad_bundle import verify_bundle
    report = verify_bundle(tmp_path / 'workflow', tmp_path / 'verification.json')
    assert report['source_preserved'] is True
    assert report['tool_count'] == 15
    assert report['edited_entity_count'] == report['source_entity_count'] + 1
    assert report['conversion_source_sha256'] == report['edited_sha256']
    assert report['conversion_source_sha256'] != report['source_sha256']
    assert report['delivery_exists'] is True
    assert report['run_status'] == 'CONDITIONAL'
    assert json.loads((tmp_path / 'verification.json').read_text()) == report
