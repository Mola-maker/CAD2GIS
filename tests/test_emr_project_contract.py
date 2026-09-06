"""Reviewed project admission agrees with the existing native EMR recognizer."""
from dataclasses import replace
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.project_profile import _reviewed_contract_state
from cad2gis.cad2gis_v3.semantics import classify_entities


def reviewed_contract():
    root = Path(__file__).resolve().parents[1] / "baselines/manado-tomohon_uplink/config"
    profile = SourceProfile.load(root / "source_profile.json")
    registry = MappingRegistry.load(root / "mapping_registry.json", profile.source_sha256)
    return profile, registry


def test_native_emr_asset_is_admitted_without_inventing_a_registry_rule():
    profile, registry = reviewed_contract()
    label = SourceEntity.from_record({
        "entity_key": "equipment-label", "source_sha256": profile.source_sha256,
        "source_file": "reviewed.dwg", "handle": "123", "layout": "Model",
        "layout_role": "model", "cad_role": "model", "layer": "POLE ID",
        "dwg_type_name": "MTEXT", "text": "EMR-28560",
        "points": [[13896538.0, 145561.0]], "centroid": [13896538.0, 145561.0],
    })
    features, _, _, _ = classify_entities([label], registry)
    equipment = [feature for feature in features if feature.feature_class == "EMR"]
    assert len(equipment) == 1
    assert equipment[0].source_entity_key == label.entity_key
    assert equipment[0].native_points == [list(label.centroid)]
    assert equipment[0].field_provenance["CODE"] == "DWG_DIRECT:emr-label"
    assert "EMR" not in registry.block_families
    assert "EMR" not in registry.field_rules
    actual = replace(profile, expectations=replace(profile.expectations, feature_counts={"EMR": 1}))
    status, allowed, _, issues = _reviewed_contract_state(actual, registry)
    assert status == "reviewed_ready" and allowed and issues == []


@pytest.mark.parametrize("unsupported", ["UNKNOWN_ASSET", "EMR_OTHER", "emr"])
def test_other_unmapped_asset_classes_remain_rejected(unsupported):
    profile, registry = reviewed_contract()
    changed = replace(profile, expectations=replace(
        profile.expectations, feature_counts={"EMR": 1, unsupported: 1}))
    with pytest.raises(ValueError, match="no mapping rules"):
        _reviewed_contract_state(changed, registry)
