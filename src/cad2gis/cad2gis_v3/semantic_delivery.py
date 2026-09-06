"""Project a committed semantic revision into reviewed canonical assets.

The first bridge deliberately supports source-backed labels and compatible
class confirmations. Creation/deletion and measurement replacement need their
own reviewed mapping/segment contracts and fail explicitly here.
"""
from __future__ import annotations

import json
from pathlib import Path

from .semantic_stage import SemanticContractError, _digest, _read_only, _sha256_path
from .semantic_store import _verified_result

_COMPATIBLE = {
    "CENTRAL_SITE": {"SITE"}, "SUPPORT": {"PTECH"},
    "DISTRIBUTION_NODE": {"BOITE"}, "ACCESS_NODE": {"BOITE"},
    "PREMISE": {"IMB"}, "NETWORK_ROUTE": {"CABLE"},
    "NETWORK_SEGMENT": {"CABLE"}, "ZONE": {"ZPM", "ZNRO"},
}


def load_published_revision(store: Path, job_id: str, snapshot_sha256: str):
    """Read a pinned historical revision; the mutable head is not authority."""
    with _read_only(Path(store).resolve()) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT * FROM compile_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None or row["state"] != "published":
            raise SemanticContractError("semantic delivery requires a published compile job")
        job = dict(row)
        binding = db.execute("SELECT binding_json FROM source_bindings WHERE id=1").fetchone()
        if binding is None or json.loads(binding[0])["snapshot_sha256"] != snapshot_sha256:
            raise SemanticContractError("semantic store belongs to a different source snapshot")
        manifest = _verified_result(job)
        if manifest["snapshot_sha256"] != snapshot_sha256:
            raise SemanticContractError("compiled revision belongs to a different source snapshot")
        decisions = [json.loads(row[0]) for row in db.execute(
            "SELECT h.decision_json FROM decision_history h WHERE h.revision="
            "(SELECT MAX(p.revision) FROM decision_history p WHERE p.entity_key=h.entity_key AND p.revision<=?) "
            "ORDER BY h.entity_key", (job["revision"],))]
    with _read_only(Path(job["result_manifest"]).parent / "semantic.gpkg") as compiled:
        for decision in decisions:
            ledger = compiled.execute("SELECT terminal_state FROM semantic_entity_ledger WHERE entity_key=?",
                                      (decision["entity_key"],)).fetchone()
            if ledger is None or ledger[0] != decision["terminal_state"]:
                raise SemanticContractError("committed decision history differs from compiled ledger")
            if decision["terminal_state"] == "CONSUMED_BY_FEATURE":
                row = compiled.execute("SELECT semantic_class,source_label_entity_key,source_dimension_entity_key FROM semantic_features WHERE primary_entity_key=?",
                                       (decision["entity_key"],)).fetchone()
                expected = tuple(decision.get(key) for key in ("class_id", "label_entity_key", "dimension_entity_key"))
                if row is None or tuple(row) != expected:
                    raise SemanticContractError("committed decision history differs from compiled feature")
    receipt = {
        "schema_version": "cad2gis.semantic_delivery.v1", "job_id": job_id,
        "generation": job["generation"], "revision": job["revision"],
        "snapshot_sha256": snapshot_sha256,
        "manifest_sha256": _sha256_path(Path(job["result_manifest"])),
        "semantic_gpkg_sha256": manifest["semantic_gpkg_sha256"],
        "decisions_sha256": _digest(decisions), "accepted_run_id": None,
        "authority": "committed_revision_candidate_not_engineering_acceptance",
    }
    return decisions, receipt


def apply_revision(features, entities, decisions: list[dict], receipt: dict) -> dict:
    """Validate the entire change first, then apply exact source text only."""
    source = {entity.entity_key: entity for entity in entities}
    owners = {}
    for feature in features:
        if feature.feature_class != "INFRASTRUCTURE":
            owners.setdefault(feature.source_entity_key, []).append(feature)
    pending = []
    for decision in decisions:
        key = decision["entity_key"]
        if decision.get("terminal_state") != "CONSUMED_BY_FEATURE":
            raise SemanticContractError(f"Canonical terminal-state changes require a reviewed mapping: {key}")
        matches = owners.get(key, [])
        if len(matches) != 1:
            raise SemanticContractError(f"Semantic delivery requires exactly one existing canonical asset: {key}")
        feature = matches[0]
        selected_class = decision.get("class_id")
        if selected_class not in (None, "GENERIC_ASSET") and feature.feature_class not in _COMPATIBLE.get(selected_class, set()):
            raise SemanticContractError(f"Semantic class conflicts with reviewed canonical mapping: {key}: {selected_class}")
        dimension = decision.get("dimension_entity_key")
        if dimension and dimension not in {
            metric.get("dimension_entity_key") for metric in feature.attributes.get("span_metrics", [])
        }:
            raise SemanticContractError(f"New dimension binding requires a reviewed canonical segment decision: {key}")
        label_key = decision.get("label_entity_key")
        label = source.get(label_key) if label_key else None
        if label_key and (label is None or not label.text):
            raise SemanticContractError(f"Selected label has no exact source text: {label_key}")
        pending.append((feature, decision, label))
    changed = []
    provenance = f"SEMANTIC_REVISION:{receipt['job_id']}:{receipt['revision']}"
    for feature, decision, label in pending:
        before = feature.display_label
        if label is not None:
            feature.display_label = str(label.text)
            feature.label_provenance = provenance + ":" + label.entity_key
            feature.field_provenance["display_label"] = feature.label_provenance
        if decision.get("class_id"):
            feature.attributes["semantic_class"] = decision["class_id"]
            feature.field_provenance["semantic_class"] = provenance
        feature.lineage.append({
            "operation": "apply_committed_semantic_revision", **receipt,
            "source_entity_key": feature.source_entity_key,
            "label_entity_key": decision.get("label_entity_key"),
            "candidate_ids": decision.get("candidate_ids", []),
            "geometry_changed": False,
        })
        changed.append({"feature_key": feature.feature_key, "source_entity_key": feature.source_entity_key,
                        "before_label": before, "after_label": feature.display_label,
                        "label_entity_key": decision.get("label_entity_key")})
    return {**receipt, "affected_features": changed, "geometry_changed": False}
