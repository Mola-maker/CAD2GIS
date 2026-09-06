import json

import pytest

from cad2gis.visual_audit import verified_semantic_labels


@pytest.mark.parametrize("tamper", [None, "snapshot", "revision", "owner", "source_text", "provenance"])
def test_semantic_visual_audit_requires_full_revision_source_binding(tamper):
    receipt = dict(schema_version="cad2gis.semantic_delivery.v1", job_id="job", generation=1,
                   revision=2, snapshot_sha256="snapshot", manifest_sha256="manifest",
                   semantic_gpkg_sha256="gpkg", decisions_sha256="decisions", authority="candidate")
    operation = dict(receipt, operation="apply_committed_semantic_revision", source_entity_key="asset",
                     label_entity_key="label", geometry_changed=False)
    snapshot = {"snapshot_sha256": "snapshot"}
    source = {"label": {"text": "原图标签 １２"}}
    props = {"label_provenance": "SEMANTIC_REVISION:job:2:label"}
    if tamper == "snapshot":
        snapshot["snapshot_sha256"] = "other"
    elif tamper == "revision":
        operation["revision"] = 3
    elif tamper == "owner":
        operation["source_entity_key"] = "other"
    elif tamper == "source_text":
        source.clear()
    elif tamper == "provenance":
        props["label_provenance"] = "forged"
    props["lineage_json"] = json.dumps([operation])
    result = verified_semantic_labels(props, source, {"semantic_revision": receipt}, snapshot, "asset")
    assert result == ([] if tamper else [{"entity_key": "label", "text": "原图标签 １２"}])
