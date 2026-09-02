"""Receipt completeness and cache safety are independent of feature counts."""

from __future__ import annotations

import copy
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cad2gis.cad2gis_v3.implementation import (  # noqa: E402
    PRODUCTION_CONVERSION_FILES, production_conversion_provenance,
)
from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity  # noqa: E402
from cad2gis.cad2gis_v3.stage_contract import StageRecorder, canonical_sha256  # noqa: E402


def _feature():
    return Feature(
        "f1", "CABLE", "LineString", [(0.0, 0.0), (10.0, 0.0)],
        "e1", "h1", "FIBER", "source", CadStyle(),
        display_label="route-a", label_provenance="DWG_TEXT:h2",
    )


@pytest.mark.parametrize("field,value", [
    ("display_label", "route-b"),
    ("label_provenance", "DWG_TEXT:h3"),
    ("attributes", {"dimension_length_m": 11.0}),
    ("field_provenance", {"dimension_length_m": "DWG_DIMENSION:h4"}),
    ("lineage", [{"operation": "reviewed-transform"}]),
    ("style", CadStyle(aci_color=3)),
    ("native_points", [(0.0, 0.0), (11.0, 0.0)]),
])
def test_full_feature_state_changes_digest_even_when_keys_and_counts_match(field, value):
    feature = _feature()
    changed = replace(feature, **{field: value})
    receipts = []
    for item in (feature, changed):
        recorder = StageRecorder()
        recorder.run(
            "classify", version="v1", inputs={"source": "same"},
            operation=lambda: [item], summarize=lambda result: {"count": len(result)},
        )
        receipts.append(recorder.receipts[0])
    assert receipts[0]["output_summary_sha256"] == receipts[1]["output_summary_sha256"]
    assert receipts[0]["output_sha256"] != receipts[1]["output_sha256"]


def test_reader_raw_facts_are_not_replaced_by_entity_key_fingerprint():
    entity = SourceEntity.from_record({
        "entity_key": "source-1", "raw_properties": {"block_base_point": [0, 0]},
    })
    changed = replace(entity, raw_properties={"block_base_point": [1, 0]})
    assert canonical_sha256(entity) != canonical_sha256(changed)


@pytest.mark.parametrize("changed", [
    {"implementation": "new"}, {"runtime": "new"}, {"gcp": "new"},
    {"decision_pack": "new"}, {"llm": "assist"},
])
def test_context_changes_invalidate_stage_identity(changed):
    keys = []
    for context in ({}, changed):
        recorder = StageRecorder(context=context)
        recorder.run(
            "stage", version="v1", inputs={"source": "same"},
            operation=lambda: [], summarize=lambda _: {"count": 0},
        )
        keys.append(recorder.receipts[0]["cache_key"])
    assert keys[0] != keys[1]


def test_in_place_topology_output_and_receipt_are_snapshotted():
    feature = _feature()
    recorder = StageRecorder()

    def topology():
        feature.attributes["dimension_length_m"] = 10
        return {"relations": []}

    recorder.run(
        "topology", version="v1", inputs={"features": [feature]},
        operation=topology, summarize=lambda _: {"attributes": feature.attributes},
        fingerprint=lambda result: {"features": [feature], "result": result},
    )
    receipt = copy.deepcopy(recorder.receipts[0])
    feature.attributes["dimension_length_m"] = 99
    assert recorder.receipts[0] == receipt
    manifest = recorder.manifest()
    manifest["stages"][0]["output_summary"].clear()
    assert recorder.receipts[0] == receipt
    assert not receipt["cacheable"]
    assert not receipt["deterministic"]
    assert receipt["cache_status"] == "disabled_receipt_only"


def test_canonical_hash_rejects_unknown_objects_and_nonfinite_numbers():
    with pytest.raises(TypeError):
        canonical_sha256(object())
    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            canonical_sha256({"value": value})
    assert canonical_sha256({"b", "a"}) == canonical_sha256(frozenset(["a", "b"]))
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})


def test_failed_stage_does_not_publish_a_success_receipt():
    recorder = StageRecorder()

    def fail():
        raise RuntimeError("incomplete writer")

    with pytest.raises(RuntimeError, match="incomplete writer"):
        recorder.run(
            "writer", version="v1", inputs={}, operation=fail, summarize=lambda _: {},
        )
    assert recorder.receipts == []


def test_production_identity_covers_reader_plan_and_artifact_boundaries():
    expected = {
        "reader/resolver.py", "reader/libredwg_cli.py", "reader/libredwg.py",
        "native_runtime.py",
        "cad2gis_v3/plan_domain.py", "cad2gis_v3/spatial_filter.py",
        "cad2gis_v3/source_gpkg.py", "cad2gis_v3/stage_contract.py",
        "cad2gis_v3/artifact_io.py", "cad2gis_v3/evidence_index.py",
    }
    assert expected.issubset(PRODUCTION_CONVERSION_FILES)
    provenance = production_conversion_provenance()
    assert expected.issubset(item["path"] for item in provenance["files"])
