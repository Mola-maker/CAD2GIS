"""MCP delivery adapters retain source, path, matching and artifact gates."""

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from cad2gis import agent_qgis
from cad2gis.agent_mcp import MCPServiceError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(agent_qgis.match_delivery, "_resolve_qgis_python",
        lambda _explicit: pytest.fail("Invalid input reached QGIS runtime discovery"))
    return tmp_path


def project(root, datasource="./delivery.gpkg|layername=CABLE", attachments=()):
    root.mkdir(parents=True, exist_ok=True)
    (root / "delivery.gpkg").write_bytes(b"source database fixture")
    path = root / "delivery.qgz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("delivery.qgs", '<qgis><projectlayers><maplayer><provider>ogr</provider>'
            f'<datasource>{datasource}</datasource></maplayer></projectlayers></qgis>')
        for attachment in attachments:
            archive.writestr(attachment, b"embedded source database")
    return path


@pytest.mark.parametrize("scope", ["main", "EMR28560"])
@pytest.mark.parametrize("status", ["abstained", "unavailable", "coarse"])
def test_standard_delivery_keeps_matching_rejection(workspace, scope, status):
    run = workspace / "run"
    run.mkdir()
    manifest = {"osm_matching": {"revision": 1, "status": "matched"},
                "delivery_counts": {"CABLE": 1}, "delivery_partitions": {}}
    if scope == "main":
        manifest["osm_matching"]["status"] = status
    else:
        manifest["delivery_partitions"][scope] = {"delivery_counts": {"CABLE": 1},
                                                "osm_matching": {"status": status}}
    (run / "run_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(MCPServiceError, match=f"Scope {scope} has OSM matching status {status}"):
        agent_qgis.publish_matched_delivery(str(run), str(workspace / "published"), {})
    assert not (workspace / "published").exists()


def test_standard_delivery_requires_frozen_snapshot_per_scope(workspace):
    run = workspace / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "osm_matching": {"revision": 1, "status": "matched"}, "delivery_counts": {"CABLE": 1}}))
    with pytest.raises(MCPServiceError, match="frozen OSM snapshot"):
        agent_qgis.publish_matched_delivery(str(run), str(workspace / "published"))
    assert not (workspace / "published").exists()


def test_review_rejects_foreign_datasource_before_qgis(workspace):
    foreign = workspace.parent / "foreign.gpkg"
    path = project(workspace / "project", str(foreign).replace("\\", "/") + "|layername=CABLE")
    snapshot = workspace / "osm.json"
    snapshot.write_text("{}")
    with pytest.raises(MCPServiceError, match="outside configured"):
        agent_qgis.build_qgis_review(str(path), str(snapshot), str(workspace / "new/review.qgz"))
    assert not (workspace / "new").exists()


def test_review_checks_embedded_attachment_existence(workspace):
    path = project(workspace / "project", "attachment:missing.gpkg|layername=CABLE")
    with pytest.raises(MCPServiceError, match="Missing QGZ GeoPackage attachment"):
        agent_qgis._project_inputs(path)
    path = project(workspace / "project", "attachment:present.gpkg|layername=CABLE", ["present.gpkg"])
    assert agent_qgis._project_inputs(path) == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize("output_name", ["run/preview.png", "source/preview.png", "partition/preview.png"])
def test_verification_never_writes_inside_immutable_artifacts(workspace, output_name):
    path = project(workspace / "project")
    directory = workspace / Path(output_name).parent
    directory.mkdir()
    name = {"run": "run_manifest.json", "source": "source_manifest.json", "partition": "partition_manifest.json"}[directory.name]
    (directory / name).write_text("{}")
    with pytest.raises(MCPServiceError, match="outside immutable"):
        agent_qgis.verify_qgis_review(str(path), str(workspace / output_name))


@pytest.mark.parametrize("existing", ["preview.png", "preview.json"])
def test_verification_preserves_existing_outputs(workspace, existing):
    path = project(workspace / "project")
    sentinel = workspace / existing
    sentinel.write_bytes(b"existing evidence")
    with pytest.raises(MCPServiceError, match="already exists"):
        agent_qgis.verify_qgis_review(str(path), str(workspace / "preview.png"))
    assert sentinel.read_bytes() == b"existing evidence"


def test_verification_rejects_tampered_qgz_receipt_before_runtime(workspace):
    path = project(workspace / "project")
    path.with_suffix(".review.json").write_text(json.dumps({"sha256": "0" * 64}))
    with pytest.raises(MCPServiceError, match="QGZ hash mismatch"):
        agent_qgis.verify_qgis_review(str(path), str(workspace / "preview.png"))
    assert not (workspace / "preview.png").exists()


def test_visual_audit_rejects_unknown_partition_without_creating_output(workspace):
    source, run = workspace / "source", workspace / "run"
    source.mkdir()
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({"delivery_partitions": {}}))
    with pytest.raises(MCPServiceError, match="Unknown canonical delivery partition"):
        agent_qgis.audit_visual_conversion(str(source), str(run), str(workspace / "audit"), "not-observed")
    assert not (workspace / "audit").exists()


def test_svg_delivery_rejects_different_source_before_qgis(workspace):
    baseline, assets = workspace / "baseline", workspace / "assets"
    baseline.mkdir()
    assets.mkdir()
    (baseline / "delivery-manifest.json").write_text(json.dumps({"source_sha256": "a" * 64}))
    (assets / "correspondence.json").write_text(json.dumps({"source_sha256": "b" * 64}))
    with pytest.raises(MCPServiceError, match="different source bindings"):
        agent_qgis.build_source_svg_delivery(str(baseline), str(assets), str(workspace / "output"))
    assert not (workspace / "output").exists()


def test_review_rejects_linked_datasource_before_runtime(workspace):
    path = project(workspace / "project", "./linked.gpkg|layername=CABLE")
    try:
        (path.parent / "linked.gpkg").symlink_to(path.parent / "delivery.gpkg")
    except OSError:
        pytest.skip("This account cannot create symlinks")
    with pytest.raises(MCPServiceError, match="symlinks or junctions"):
        agent_qgis._project_inputs(path)


@pytest.mark.parametrize("changed", ["cad_database_hashes", "osm_source_sha256"])
def test_built_review_must_bind_admitted_cad_and_osm(workspace, monkeypatch, changed):
    path = project(workspace / "project")
    snapshot = workspace / "osm.json"
    snapshot.write_text("{}")
    output = workspace / "new/review.qgz"
    monkeypatch.setattr(agent_qgis.match_delivery, "_resolve_qgis_python", lambda _: workspace / "python")

    def qgis(_python, _module, _arguments):
        output.parent.mkdir()
        output.write_bytes(b"rendered project fixture")
        receipt = {"sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                   "input_project_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "cad_database_hashes": [hashlib.sha256((path.parent / "delivery.gpkg").read_bytes()).hexdigest()],
                   "osm_source_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}
        receipt[changed] = ["f" * 64] if changed == "cad_database_hashes" else "f" * 64
        output.with_suffix(".review.json").write_text(json.dumps(receipt))

    monkeypatch.setattr(agent_qgis.match_delivery, "_run_qgis", qgis)
    with pytest.raises(MCPServiceError, match="database or frozen OSM source binding changed"):
        agent_qgis.build_qgis_review(str(path), str(snapshot), str(output))
@pytest.mark.parametrize("invalid", ["missing", "empty_runtime", "missing_glyphs"])
def test_verification_requires_new_font_evidence(workspace, monkeypatch, invalid):
    path = project(workspace / "project")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".review.json").write_text(json.dumps({"sha256": sha}))
    image = workspace / "render.png"
    monkeypatch.setattr(agent_qgis.match_delivery, "_resolve_qgis_python", lambda _: Path("python"))
    def render(*args):
        image.write_bytes(b"rendered pixels")
        result = {"project_sha256": sha, "snapshot_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                  "qgis_read": True, "all_layers_valid": True, "cad_bytes_unchanged": True}
        if invalid != "missing":
            result["font_runtime"] = {"available_family_count": 0 if invalid == "empty_runtime" else 1,
                                      "resolved_family": "Arial"}
            result["label_font_qa"] = {"all_rendered_glyphs_available": invalid != "missing_glyphs",
                                       "fonts": [{"resolved_family": "Arial"}], "annotations_checked": 3}
        image.with_suffix(".json").write_text(json.dumps(result))
    monkeypatch.setattr(agent_qgis.match_delivery, "_run_qgis", render)
    with pytest.raises(MCPServiceError, match="font evidence"):
        agent_qgis.verify_qgis_review(str(path), str(image))
