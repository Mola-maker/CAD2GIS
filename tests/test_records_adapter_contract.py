from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad2gis.cad2gis_v3.curation import CurationError, validate_review_bundle
from cad2gis.reader.records_adapter import load_records, validate_bundle_facts


SOURCE_SHA = "a" * 64


def _digest(value: object) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def review_bundle_path(tmp_path: Path) -> Path:
    # One valid proposal-only object: geometry is a fingerprint, never points.
    facts = {
        "source_file_name": "drawing.dwg", "handle": "10A",
        "layout": "Model", "layout_role": "model", "cad_role": "model",
        "layer": "CABLE", "entity_type": "LWPOLYLINE", "block_name": "",
        "block_attributes": {}, "text": "", "dimension_measurement_present": False,
        "dimension_text_override": "", "owner_handle": "1",
        "extraction_backend": "test-reader", "reader_backend_status": "complete",
        "measurements": {
            "native_length": 10.0, "dimension_measurement": None,
            "native_length_source": "ordered_wcs_vertices",
            "unit": "source_drawing_unit", "immutable": True,
        },
        "style": {"aci_color": 3, "true_color": "", "linetype": "Continuous", "lineweight": -1},
        "rotation": 0.0, "scale_factors": [1.0, 1.0, 1.0],
        "disposition": "retained", "raw_properties": {},
        "shape_binding": {"vertex_count": 2, "native_fingerprint": "b" * 64},
    }
    full_fact_sha = "c" * 64
    candidate_facts = {
        "batch_index": 0, "batch_size": 1,
        "object_fact_sha256": _digest([full_fact_sha]),
        "signature": {
            name: facts[name] for name in (
                "layout_role", "entity_type", "layer", "block_name", "cad_role",
                "disposition", "reader_backend_status",
            )
        },
    }
    payload = {
        "schema_version": "cad2gis.review_bundle.v2",
        "source": {"dwg_name": "drawing.dwg", "dwg_sha256": SOURCE_SHA},
        "evidence": {"file_name": "evidence.gpkg", "sha256": "d" * 64},
        "policy": {
            "allowed_actions": ["select", "rank", "abstain"],
            "conversion_import_allowed": False, "coordinate_payloads_visible": False,
            "forbidden_mutations": [
                "attributes", "coordinates", "crs", "geometry", "ids", "labels",
                "layers", "lengths", "span_measurements", "topology",
            ],
            "immutable_measurements_visible": True,
            "stage": "curate_after_deterministic_readcad_and_candidate_evidence",
        },
        "objects": [{
            "evidence_id": "entity:1", "fact_sha256": _digest(facts),
            "full_fact_sha256": full_fact_sha, "facts": facts,
        }],
        "candidates": [{
            "candidate_id": "candidate:1", "task_id": "task:1",
            "kind": "inventory_batch", "allowed_class": "CAD_INVENTORY_BATCH",
            "evidence_ids": ["entity:1"], "facts": candidate_facts,
            "facts_sha256": _digest(candidate_facts),
        }],
        "tasks": [{
            "task_id": "task:1", "kind": "inventory_review",
            "candidate_ids": ["candidate:1"], "evidence_ids": ["entity:1"],
            "allowed_actions": ["select", "abstain"],
        }],
        "coverage": {
            "object_count": 1, "inventory_task_count": 1,
            "inventory_covered_objects": 1, "untasked_objects": 0,
            "multiply_tasked_objects": 0,
        },
    }
    payload["bundle_sha256"] = _digest(payload)
    validated = validate_review_bundle(payload)
    path = tmp_path / "review-bundle.json"
    path.write_text(json.dumps(validated.to_dict()), encoding="utf-8")
    return path


def test_valid_review_bundle_cannot_be_materialized(review_bundle_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="proposal-only.*SourceEntity"):
        load_records(review_bundle_path)


def test_unversioned_fact_mapping_cannot_be_materialized(tmp_path: Path) -> None:
    path = tmp_path / "unversioned.json"
    path.write_text('{"objects": [{"facts": {}}]}', encoding="utf-8")
    with pytest.raises(NotImplementedError, match=r"ingest\(source, profile\)"):
        load_records(path)


def test_integrity_validation_does_not_authorize_conversion(review_bundle_path: Path) -> None:
    profile = SimpleNamespace(source_sha256=SOURCE_SHA)
    result = validate_bundle_facts(review_bundle_path, profile)
    payload = json.loads(review_bundle_path.read_text(encoding="utf-8"))
    assert result == {
        "bundle_path": str(review_bundle_path), "objects_count": 1, "facts_count": 1,
        "schema_version": "cad2gis.review_bundle.v2",
        "bundle_sha256": payload["bundle_sha256"], "source_sha256": SOURCE_SHA,
        "conversion_import_allowed": False,
    }


def test_integrity_validation_rejects_tampered_bundle(review_bundle_path: Path) -> None:
    payload = json.loads(review_bundle_path.read_text(encoding="utf-8"))
    payload["source"]["dwg_name"] = "changed.dwg"
    review_bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CurationError, match="hash mismatch"):
        validate_bundle_facts(review_bundle_path, SimpleNamespace(source_sha256=SOURCE_SHA))


def test_integrity_validation_rejects_wrong_source(review_bundle_path: Path) -> None:
    with pytest.raises(ValueError, match="source SHA-256 does not match"):
        validate_bundle_facts(review_bundle_path, SimpleNamespace(source_sha256="e" * 64))
