"""A successful QGIS subprocess cannot publish unbound output receipts."""

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from cad2gis import match_delivery


def _signed_matching(revision=1):
    record = {"schema_version": "cad2gis.osm-matching.v1", "revision": revision, "status": "matched",
              "source_sha256": "a" * 64, "authority": "relative_osm", "absolute_accuracy_verified": False}
    record["match_sha256"] = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return record


def _publisher(tmp_path, monkeypatch, corrupt=None, corrupt_standalone=False):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "osm_matching": _signed_matching(), "delivery_counts": {"CABLE": 1},
    }))
    snapshot = tmp_path / "osm.json"
    snapshot.write_text('{"elements": []}')
    cad_bytes = b"canonical fixture database"
    cad_sha = hashlib.sha256(cad_bytes).hexdigest()
    context = {"schema": "cad2gis.qgis-matching-context.v1", "delivery_sha256": cad_sha,
               "source_sha256": "a" * 64, "run_manifest_sha256": "b" * 64, "matching": _signed_matching()}

    def package(_run, output):
        output.mkdir()
        (output / "delivery.gpkg").write_bytes(cad_bytes)
        return {"source_sha256": "a" * 64, "run_manifest_sha256": "b" * 64,
                "run_status": "CONDITIONAL", "deliveries": [{"scope": "main", "project": "delivery.qgz",
                "gpkg_sha256": cad_sha}]}

    def qgis(_python, module, arguments):
        output = Path(arguments[arguments.index("--output") + 1])
        if module == "cad2gis.svg_delivery":
            output.mkdir()
            (output / "delivery.qgz").write_bytes(b"svg fixture")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        standalone = "--standalone" in arguments
        external_osm = b"cached OSM fixture"
        standalone_osm = b"independently generated OSM database bytes"
        if standalone:
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("review.qgs", '<qgis><projectlayers>'
                    '<maplayer><provider>ogr</provider><datasource>attachment:cad.gpkg|layername=CABLE</datasource></maplayer>'
                    '<maplayer><provider>ogr</provider><datasource>attachment:osm.gpkg|layername=roads</datasource></maplayer>'
                    '</projectlayers></qgis>')
                archive.writestr("cad.gpkg", cad_bytes)
                archive.writestr("osm.gpkg", standalone_osm)
        else:
            output.write_bytes(b"review fixture " + output.name.encode())
        (output.parent / "osm-basemap.gpkg").write_bytes(external_osm)
        receipt = {"schema": "cad2gis.qgis-osm-review.v1", "sha256": match_delivery._file_sha256(output),
                   "input_project_sha256": match_delivery._file_sha256(Path(arguments[arguments.index("--project") + 1])),
                   "standalone": standalone, "cad_database_hashes": [cad_sha],
                   "osm_database_sha256": hashlib.sha256(standalone_osm if standalone else external_osm).hexdigest(),
                   "geometry_and_attributes_unchanged": True, "absolute_accuracy_verified": False,
                   "osm_source_sha256": match_delivery._file_sha256(snapshot), "canonical_matching": context}
        if corrupt and standalone is corrupt_standalone:
            corrupt(receipt)
        output.with_suffix(".review.json").write_text(json.dumps(receipt))

    monkeypatch.setattr(match_delivery, "_resolve_qgis_python", lambda _: tmp_path / "python")
    monkeypatch.setattr(match_delivery, "package_delivery", package)
    monkeypatch.setattr(match_delivery, "_run_qgis", qgis)
    root = tmp_path / "published"
    return lambda: match_delivery.publish(run, delivery_root=root, osm_data=[str(snapshot)]), root


def test_matching_receipts_bound_to_actual_staged_files_are_published(tmp_path, monkeypatch):
    publish, root = _publisher(tmp_path, monkeypatch)
    result = publish()
    assert result["run_status"] == "CONDITIONAL"
    assert result["scopes"][0]["geometry_and_attributes_unchanged"] is True
    assert {path.name for path in (root / "r1").iterdir()} == set(match_delivery.DELIVERY_FILES)


@pytest.mark.parametrize("standalone", [False, True], ids=["editable", "standalone"])
@pytest.mark.parametrize("field,bad", [
    ("sha256", "f" * 64), ("cad_database_hashes", ["f" * 64]), ("standalone", "not-a-bool"),
    ("geometry_and_attributes_unchanged", False), ("absolute_accuracy_verified", True),
    ("input_project_sha256", "f" * 64), ("osm_database_sha256", "f" * 64),
    ("schema", "unknown"), ("canonical_matching", None),
])
def test_invalid_receipts_do_not_expose_an_atomic_revision(tmp_path, monkeypatch, standalone, field, bad):
    publish, root = _publisher(tmp_path, monkeypatch, lambda receipt: receipt.update({field: bad}), standalone)
    with pytest.raises(match_delivery.PublishError, match="receipt"):
        publish()
    assert not (root / "r1").exists()
    assert not list(root.glob(".r1.partial-*"))


@pytest.mark.parametrize("standalone", [False, True], ids=["editable", "standalone"])
@pytest.mark.parametrize("change", ["valid_other_revision", "different_run_manifest", "invalid_record_hash"])
def test_matching_receipt_cannot_substitute_another_binding(tmp_path, monkeypatch, standalone, change):
    def corrupt(receipt):
        context = dict(receipt["canonical_matching"])
        if change == "valid_other_revision":
            context["matching"] = _signed_matching(2)
        elif change == "different_run_manifest":
            context["run_manifest_sha256"] = "c" * 64
        else:
            context["matching"] = {**context["matching"], "match_sha256": "f" * 64}
        receipt["canonical_matching"] = context

    publish, root = _publisher(tmp_path, monkeypatch, corrupt, standalone)
    with pytest.raises(match_delivery.PublishError, match="matching receipt"):
        publish()
    assert not (root / "r1").exists()
    assert not list(root.glob(".r1.partial-*"))
