from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.semantic_stage import (
    DECISION_SCHEMA,
    SemanticContractError,
    compile_semantics,
    list_semantic_candidates,
    prepare_semantics,
    validate_semantics,
    write_semantic_decision_pack,
)
from cad2gis.cad2gis_v3.source_export import export_source


class _Inventory(list[dict]):
    def __init__(self, values: list[dict], diagnostics: dict) -> None:
        super().__init__(values)
        self.diagnostics = diagnostics


def _record(key: str, kind: str, points, *, text="", block="", layer="0"):
    return {
        "entity_key": key,
        "source_sha256": "a" * 64,
        "source_file": "fixture.dwg",
        "handle": key,
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": layer,
        "object_name": f"AcDb{kind.title()}",
        "dwg_type_name": kind,
        "points": points,
        "centroid": points[0],
        "closed": False,
        "text": text,
        "block_name": block,
        "block_attributes": {},
        "native_length": 1.0 if len(points) > 1 else None,
        "raw_properties": {},
    }


@pytest.fixture
def source_run(tmp_path: Path) -> Path:
    source = tmp_path / "fixture.dwg"
    source.write_bytes(b"fixture")
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    records = _Inventory(
        [
            {**_record("block-1", "INSERT", [(0.0, 0.0)], block="FAT", layer="FAT"), "source_sha256": digest},
            {**_record("label-1", "TEXT", [(0.1, 0.0)], text="FAT-001", layer="LABEL"), "source_sha256": digest},
            {**_record("line-1", "LINE", [(0.0, 0.0), (1.0, 0.0)], layer="CABLE"), "source_sha256": digest},
        ],
        {"inventory_complete": True, "skipped_rows": 0},
    )
    root = tmp_path / "source-run"
    export_source(source=source, run_dir=root, records=records)
    return root


def _pack(path: Path, source_sha: str, decisions: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": DECISION_SCHEMA,
                "source_sha256": source_sha,
                "candidates_sha256": "",
                "decisions": decisions,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_empty_pack_preserves_every_entity_as_unresolved(source_run: Path):
    prepared = prepare_semantics(source_run=source_run)
    assert prepared["schema_version"] == "cad2gis.semantic_prepare.v2"
    relationships = prepared["relationship_evidence"]
    assert relationships["line_candidate_count"] == 1
    assert relationships["endpoints_with_node_candidates"] == 1
    line = next(
        item
        for item in list_semantic_candidates(
            prepare_manifest=source_run / "semantic_prepare" / "manifest.json",
            limit=100,
        )["items"]
        if item["primary_entity_key"] == "line-1"
    )
    endpoint = line["relationship_evidence"]["topology"]["endpoints"][0]
    assert endpoint["connected_or_nearby_nodes"][0]["entity_key"] == "block-1"
    assert endpoint["connected_or_nearby_nodes"][0]["connection_state"] == (
        "EXACT_SOURCE_ENDPOINT"
    )
    pack = _pack(
        source_run / "empty-decisions.json", prepared["source_sha256"], []
    )
    value = json.loads(pack.read_text(encoding="utf-8"))
    value["candidates_sha256"] = prepared["candidates"]["sha256"]
    pack.write_text(json.dumps(value), encoding="utf-8")
    result = compile_semantics(
        source_run=source_run,
        prepare_manifest=source_run / "semantic_prepare" / "manifest.json",
        decision_pack=pack,
    )
    validation = result["validation"]
    assert validation["valid"] is True
    assert validation["source_entity_count"] == 3
    assert validation["terminal_state_counts"] == {"UNRESOLVED": 3}
    assert not (source_run / "delivery.gpkg").exists()


def test_feature_uses_source_geometry_and_observed_label(source_run: Path):
    prepared = prepare_semantics(source_run=source_run)
    manifest = source_run / "semantic_prepare" / "manifest.json"
    page = list_semantic_candidates(prepare_manifest=manifest, limit=100)
    block = next(item for item in page["items"] if item["primary_entity_key"] == "block-1")
    observed_label = next(
        item for item in block["label_candidates"] if item["entity_key"] == "label-1"
    )
    pack = _pack(
        source_run / "decisions.json",
        prepared["source_sha256"],
        [
            {
                "assembly_id": block["assembly_id"],
                "terminal_state": "CONSUMED_BY_FEATURE",
                "semantic_class": "ACCESS_NODE",
                "semantic_subtype": "FAT",
                "source_entity_keys": ["block-1", "label-1"],
                "source_label_entity_key": observed_label["entity_key"],
                "confidence": 0.99,
                "evidence": ["block:FAT", "label:FAT-001"],
                "evidence_ids": [block["candidate_evidence_id"]],
            }
        ],
    )
    value = json.loads(pack.read_text(encoding="utf-8"))
    value["candidates_sha256"] = prepared["candidates"]["sha256"]
    pack.write_text(json.dumps(value), encoding="utf-8")
    result = compile_semantics(
        source_run=source_run,
        prepare_manifest=manifest,
        decision_pack=pack,
        force=True,
    )
    with sqlite3.connect(result["semantic_gpkg"]) as connection:
        feature = connection.execute(
            "SELECT semantic_class,semantic_subtype,display_label,primary_entity_key,source_table "
            "FROM semantic_features"
        ).fetchone()
        assert feature == ("ACCESS_NODE", "FAT", "FAT-001", "block-1", "source_blocks")
        views = {
            row[0] for row in connection.execute(
                "SELECT table_name FROM gpkg_contents WHERE table_name LIKE 'semantic_%'"
            )
        }
        assert "semantic_access_node_blocks" in views
        assert connection.execute(
            "SELECT entity_key,display_label FROM semantic_access_node_blocks"
        ).fetchone() == ("block-1", "FAT-001")
    assert validate_semantics(result["semantic_gpkg"])["valid"] is True


def test_rejects_invented_label_entity(source_run: Path):
    prepared = prepare_semantics(source_run=source_run)
    manifest = source_run / "semantic_prepare" / "manifest.json"
    block = next(
        item
        for item in list_semantic_candidates(prepare_manifest=manifest)["items"]
        if item["primary_entity_key"] == "block-1"
    )
    pack = _pack(
        source_run / "bad.json",
        prepared["source_sha256"],
        [{
            "assembly_id": block["assembly_id"],
            "terminal_state": "CONSUMED_BY_FEATURE",
            "semantic_class": "ACCESS_NODE",
            "source_label_entity_key": "invented-label",
            "evidence_ids": [block["candidate_evidence_id"]],
        }],
    )
    value = json.loads(pack.read_text(encoding="utf-8"))
    value["candidates_sha256"] = prepared["candidates"]["sha256"]
    pack.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SemanticContractError, match="observed candidate"):
        compile_semantics(
            source_run=source_run,
            prepare_manifest=manifest,
            decision_pack=pack,
        )


def test_network_segment_requires_relationship_evidence_and_source_length(
    source_run: Path,
) -> None:
    prepared = prepare_semantics(source_run=source_run)
    manifest = source_run / "semantic_prepare" / "manifest.json"
    line = next(
        item
        for item in list_semantic_candidates(prepare_manifest=manifest)["items"]
        if item["primary_entity_key"] == "line-1"
    )
    decision = {
        "assembly_id": line["assembly_id"],
        "terminal_state": "CONSUMED_BY_FEATURE",
        "semantic_class": "NETWORK_SEGMENT",
        "source_entity_keys": ["line-1"],
        "evidence_ids": [line["candidate_evidence_id"]],
    }
    pack = _pack(source_run / "network.json", prepared["source_sha256"], [decision])
    value = json.loads(pack.read_text(encoding="utf-8"))
    value["candidates_sha256"] = prepared["candidates"]["sha256"]
    pack.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SemanticContractError, match="relationship evidence"):
        compile_semantics(
            source_run=source_run,
            prepare_manifest=manifest,
            decision_pack=pack,
        )

    decision["evidence_ids"].append(line["relationship_evidence"]["evidence_id"])
    value["decisions"] = [decision]
    pack.write_text(json.dumps(value), encoding="utf-8")
    compiled = compile_semantics(
        source_run=source_run,
        prepare_manifest=manifest,
        decision_pack=pack,
    )
    assert compiled["validation"]["network_length_audit"] == {
        "feature_count": 1,
        "with_positive_source_native_length": 1,
        "missing_or_nonpositive_count": 0,
        "source_native_length_total": 1.0,
        "passed": True,
    }


def test_rejects_decisions_bound_to_stale_candidates(source_run: Path):
    prepared = prepare_semantics(source_run=source_run)
    pack = _pack(source_run / "stale.json", prepared["source_sha256"], [])
    value = json.loads(pack.read_text(encoding="utf-8"))
    value["candidates_sha256"] = "0" * 64
    pack.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SemanticContractError, match="candidates_sha256 mismatch"):
        compile_semantics(
            source_run=source_run,
            prepare_manifest=source_run / "semantic_prepare" / "manifest.json",
            decision_pack=pack,
        )


def test_batch_decision_expands_to_source_entities(source_run: Path):
    prepared = prepare_semantics(source_run=source_run)
    manifest = source_run / "semantic_prepare" / "manifest.json"
    block_batch = next(
        item for item in prepared["batches"] if item["source_table"] == "source_blocks"
    )
    result = write_semantic_decision_pack(
        prepare_manifest=manifest,
        output=source_run / "batch.json",
        decisions=[],
        batch_decisions=[{
            "batch_id": block_batch["batch_id"],
            "terminal_state": "RETAINED_AS_REFERENCE",
            "evidence": ["batch:test"],
        }],
        host="pytest",
        model="none",
    )
    assert result["batch_decision_count"] == 1
    assert result["assigned_entity_count"] == block_batch["count"]
    compiled = compile_semantics(
        source_run=source_run,
        prepare_manifest=manifest,
        decision_pack=source_run / "batch.json",
    )
    assert compiled["validation"]["terminal_state_counts"] == {
        "RETAINED_AS_REFERENCE": 1,
        "UNRESOLVED": 2,
    }
