"""Regressions for the isolated proposal-only curation boundary."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cad2gis_v3.curation import (
    CurationError,
    PROPOSAL_SCHEMA_VERSION,
    build_review_bundle,
    load_review_bundle,
    proposal_json_schema,
    validate_proposal,
    validate_review_bundle,
    write_json_atomic,
)


def _make_evidence(tmp_path: Path):
    dwg = tmp_path / "only-project.dwg"
    dwg.write_bytes(b"immutable-dwg-fixture")
    source_sha = hashlib.sha256(dwg.read_bytes()).hexdigest()
    evidence = tmp_path / "evidence.gpkg"
    connection = sqlite3.connect(evidence)
    connection.executescript("""
        CREATE TABLE cad_entities (
            entity_key TEXT, source_sha256 TEXT, source_file TEXT,
            cad_handle TEXT, cad_layout TEXT, layout_role TEXT, cad_role TEXT,
            dwg_layer TEXT, dwg_type TEXT, block_name TEXT,
            block_attributes TEXT, text TEXT, native_points TEXT,
            dimension_value REAL, aci_color INTEGER, true_color TEXT,
            linetype TEXT, lineweight INTEGER, rotation REAL,
            disposition TEXT, scale_x REAL, scale_y REAL, scale_z REAL,
            raw_properties TEXT
        );
        CREATE TABLE feature_candidates (
            feature_key TEXT, feature_class TEXT, geometry_kind TEXT,
            geometry_role TEXT, source_entity_key TEXT, source_handle TEXT,
            source_layer TEXT, attributes TEXT, display_label TEXT,
            label_provenance TEXT
        );
        CREATE TABLE annotation_assignment_candidates (
            annotation_key TEXT, target_class TEXT, target_key TEXT,
            target_handle TEXT, distance_native_m REAL, selected INTEGER,
            status TEXT, rule_id TEXT, family_id TEXT
        );
    """)
    entities = [
        (
            "E-INSERT", source_sha, str(dwg), "10", "Model", "plan", "mapped",
            "FAT DWG", "INSERT", "*U11",
            json.dumps({"FAT_ID": "DMPH-1.010.A01", "X": 123.4}), "", 
            json.dumps([[123.4, 456.7], [124.4, 457.7]]), None, 1, "", "CONTINUOUS",
            25, 0.0, "mapped", 1.0, 1.0, 1.0,
            json.dumps({
                "effective_block_name": "FAT",
                "insertion_point": [123.4, 456.7],
                "cad_x": 123.4,
                "source_note": "safe deterministic text",
            }),
        ),
        (
            "E-TEXT", source_sha, str(dwg), "11", "Model", "plan", "annotation",
            "FAT", "TEXT", "", "{}", "DMPH-1.010.A01",
            json.dumps([[130.0, 460.0]]), 15.0, 2, "", "CONTINUOUS", 20, 0.0,
            "annotation", None, None, None, "{}",
        ),
    ]
    connection.executemany(
        "INSERT INTO cad_entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        entities,
    )
    features = [
        (
            "F-1", "BOITE", "Point", "SOURCE_INSTANCE", "E-INSERT", "10", "FAT DWG",
            json.dumps({
                "CODE": "DMPH-1.010.A01",
                "LONGUEUR": 987.6,
                "span_metrics": [{"length": 15.0, "x": 123.4}],
            }),
            "DMPH-1.010.A01", "DWG_DIRECT",
        ),
        (
            "F-2", "PTECH", "Point", "SOURCE_INSTANCE", "E-INSERT", "10", "FAT DWG",
            json.dumps({"TYPE": "candidate alternative"}), "", "UNAVAILABLE",
        ),
    ]
    connection.executemany(
        "INSERT INTO feature_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", features,
    )
    connection.execute(
        "INSERT INTO annotation_assignment_candidates VALUES (?,?,?,?,?,?,?,?,?)",
        ("E-TEXT", "BOITE", "F-1", "10", 12.5, 1, "selected", "RULE-1", "fat"),
    )
    connection.commit()
    connection.close()
    return dwg, evidence


def _feature_task(bundle):
    return next(task for task in bundle.tasks if task["kind"] == "feature_review")


def _proposal(bundle, task, *, action="select", candidate_ids=None):
    if candidate_ids is None:
        candidate_ids = [task["candidate_ids"][0]] if action == "select" else []
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "bundle_sha256": bundle.bundle_sha256,
        "source_sha256": bundle.source_sha256,
        "evidence_sha256": bundle.evidence_sha256,
        "decisions": [{
            "task_id": task["task_id"],
            "action": action,
            "candidate_ids": candidate_ids,
            "evidence_ids": list(task["evidence_ids"]),
            "confidence": 0.75,
            "rationale": "Existing evidence supports this proposal.",
        }],
    }


def test_bundle_covers_every_object_with_immutable_measurements_but_no_coordinates(tmp_path):
    dwg, evidence = _make_evidence(tmp_path)
    bundle = build_review_bundle(evidence, dwg)
    assert len(bundle.payload["objects"]) == 2
    assert {item["evidence_id"] for item in bundle.payload["objects"]} == {"E-INSERT", "E-TEXT"}
    assert bundle.payload["coverage"] == {
        "object_count": 2,
        "inventory_task_count": 2,
        "inventory_covered_objects": 2,
        "untasked_objects": 0,
        "multiply_tasked_objects": 0,
    }
    inventory_ids = {
        evidence_id
        for task in bundle.tasks if task["kind"] == "inventory_review"
        for evidence_id in task["evidence_ids"]
    }
    assert inventory_ids == {"E-INSERT", "E-TEXT"}
    insert = next(item for item in bundle.payload["objects"] if item["evidence_id"] == "E-INSERT")
    assert insert["facts"]["raw_properties"] == {
        "effective_block_name": "FAT", "source_note": "safe deterministic text",
    }
    assert insert["facts"]["block_attributes"] == {"FAT_ID": "DMPH-1.010.A01"}
    assert insert["facts"]["measurements"] == {
        "native_length": None,
        "dimension_measurement": None,
        "native_length_source": "",
        "unit": "source_drawing_unit",
        "immutable": True,
    }
    dimension = next(item for item in bundle.payload["objects"] if item["evidence_id"] == "E-TEXT")
    assert dimension["facts"]["measurements"]["dimension_measurement"] == 15.0
    assert insert["facts"]["shape_binding"]["vertex_count"] == 2
    visible = json.dumps({
        "objects": bundle.payload["objects"],
        "candidates": bundle.payload["candidates"],
    }, ensure_ascii=False)
    for forbidden in (
        "123.4", "456.7", "native_points", "insertion_point", "cad_x",
    ):
        assert forbidden not in visible
    cable_like = next(item for item in bundle.payload["candidates"] if item["candidate_id"] == "F-1")
    assert cable_like["facts"]["attributes"] == {
        "CODE": "DMPH-1.010.A01",
        "LONGUEUR": 987.6,
        "span_metrics": [{"length": 15.0}],
    }
    annotation = next(
        item for item in bundle.payload["candidates"]
        if item["kind"] == "annotation_assignment"
    )
    assert annotation["facts"]["distance_native_m"] == 12.5
    loaded = load_review_bundle(
        write_json_atomic(tmp_path / "bundle.json", bundle.to_dict())
    )
    assert loaded.bundle_sha256 == bundle.bundle_sha256


def test_bundle_hash_and_dwg_binding_fail_closed(tmp_path):
    dwg, evidence = _make_evidence(tmp_path)
    bundle = build_review_bundle(evidence, dwg)
    tampered = bundle.to_dict()
    tampered["objects"][0]["facts"]["text"] = "tampered"
    with pytest.raises(CurationError, match="fact_sha256|hash mismatch"):
        validate_review_bundle(tampered)
    missing_inventory = bundle.to_dict()
    missing_inventory["coverage"]["untasked_objects"] = 1
    with pytest.raises(CurationError, match="coverage.untasked_objects"):
        validate_review_bundle(missing_inventory)
    dwg.write_bytes(b"changed")
    with pytest.raises(CurationError, match="DWG SHA-256"):
        build_review_bundle(evidence, dwg)


def test_proposal_can_only_select_rank_or_abstain_existing_ids(tmp_path):
    dwg, evidence = _make_evidence(tmp_path)
    bundle = build_review_bundle(evidence, dwg)
    task = _feature_task(bundle)
    selected = validate_proposal(_proposal(bundle, task), bundle)
    assert selected.decisions[0].candidate_ids[0] in task["candidate_ids"]
    ranked = _proposal(bundle, task, action="rank", candidate_ids=list(task["candidate_ids"]))
    assert validate_proposal(ranked, bundle).decisions[0].action == "rank"
    abstain = _proposal(bundle, task, action="abstain")
    assert validate_proposal(abstain, bundle).decisions[0].candidate_ids == ()

    unknown = _proposal(bundle, task)
    unknown["decisions"][0]["candidate_ids"] = ["invented-id"]
    with pytest.raises(CurationError, match="not allowed"):
        validate_proposal(unknown, bundle)
    injected = _proposal(bundle, task)
    injected["decisions"][0]["coordinates"] = [1, 2]
    with pytest.raises(CurationError, match="Forbidden"):
        validate_proposal(injected, bundle)
    smuggled = _proposal(bundle, task)
    smuggled["decisions"][0]["rationale"] = "coordinate=123.4,456.7"
    with pytest.raises(CurationError, match="must not embed"):
        validate_proposal(smuggled, bundle)
    stale = _proposal(bundle, task)
    stale["source_sha256"] = "0" * 64
    with pytest.raises(CurationError, match="does not match"):
        validate_proposal(stale, bundle)

    inventory_task = next(task for task in bundle.tasks if task["kind"] == "inventory_review")
    inventory = _proposal(bundle, inventory_task)
    assert validate_proposal(inventory, bundle).decisions[0].evidence_ids
    inventory["decisions"][0]["evidence_ids"] = []
    with pytest.raises(CurationError, match="acknowledge every object"):
        validate_proposal(inventory, bundle)


def test_provider_schema_is_strict_and_bound_to_one_task(tmp_path):
    dwg, evidence = _make_evidence(tmp_path)
    bundle = build_review_bundle(evidence, dwg)
    task = _feature_task(bundle)
    schema = proposal_json_schema(bundle, task["task_id"])
    assert schema["additionalProperties"] is False
    decision = schema["properties"]["decisions"]["items"]
    assert decision["additionalProperties"] is False
    assert decision["properties"]["task_id"]["enum"] == [task["task_id"]]
    assert set(decision["properties"]["candidate_ids"]["items"]["enum"]) == set(task["candidate_ids"])
    inventory_task = next(item for item in bundle.tasks if item["kind"] == "inventory_review")
    inventory_schema = proposal_json_schema(bundle, inventory_task["task_id"])
    inventory_evidence = inventory_schema["properties"]["decisions"]["items"]["properties"]["evidence_ids"]
    assert inventory_evidence["minItems"] == len(inventory_task["evidence_ids"])
    assert inventory_evidence["maxItems"] == len(inventory_task["evidence_ids"])


def test_curate_import_does_not_import_offline_pipeline_or_gis_dependencies(tmp_path):
    script = (
        "import sys; import cad2gis_v3.curation; "
        "assert 'cad2gis_v3.pipeline' not in sys.modules; "
        "assert 'cad2gis_v3.curation_service' not in sys.modules; "
        "assert not any(name.startswith('cad2gis_v3.curation_providers') for name in sys.modules); "
        "assert 'pyproj' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_offline_pipeline_does_not_import_curation_or_open_network():
    script = """
import socket
import sys

class NetworkForbidden(RuntimeError):
    pass

def deny(*args, **kwargs):
    raise NetworkForbidden('network attempted during offline pipeline import')

socket.create_connection = deny
socket.socket.connect = deny
import cad2gis_v3.pipeline
assert 'cad2gis_v3.curation' not in sys.modules
assert 'cad2gis_v3.curation_cli' not in sys.modules
assert 'cad2gis_v3.curation_service' not in sys.modules
assert not any(name.startswith('cad2gis_v3.curation_providers') for name in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_cloud_cli_without_credentials_fails_before_network(tmp_path):
    dwg, evidence = _make_evidence(tmp_path)
    bundle = build_review_bundle(evidence, dwg)
    bundle_path = write_json_atomic(tmp_path / "bundle.json", bundle.to_dict())
    env = os.environ.copy()
    for name in tuple(env):
        if (
            name.startswith("CAD2GIS_LLM_")
            or name.startswith("DEEPSEEK_")
            or name.startswith("NEW_API_")
        ):
            env.pop(name)
    completed = subprocess.run(
        [
            sys.executable, str(Path(__file__).parent / "curate_v3.py"), "cloud",
            "--bundle", str(bundle_path), "--task-id", _feature_task(bundle)["task_id"],
            "--out-proposal", str(tmp_path / "proposal.json"),
            "--out-audit", str(tmp_path / "audit.json"),
        ],
        cwd=Path(__file__).parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "missing environment settings" in completed.stderr
    assert not (tmp_path / "proposal.json").exists()
    assert not (tmp_path / "audit.json").exists()
