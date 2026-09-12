import json

import pytest

from cad2gis.match_delivery import (
    PublishError,
    _delivery_root,
    _parse_osm_data,
    _revision,
    _revision_directory,
    _search_bbox,
    _matching_scopes,
    publish,
)


def test_reviewed_revisions_get_their_own_delivery_directory():
    assert _revision_directory(2) == "r2"
    assert _revision_directory(11) == "r11"
    for invalid in (0, -1, True, "2"):
        with pytest.raises(PublishError, match="positive integer"):
            _revision_directory(invalid)


def test_run_without_matching_evidence_cannot_be_published():
    with pytest.raises(PublishError, match="no OSM matching record"):
        _revision({"run_status": "COMPLETED"})
    with pytest.raises(PublishError, match="no valid revision"):
        _revision({"osm_matching": {"status": "matched"}})


def test_published_root_is_the_project_delivery_directory(tmp_path):
    run = tmp_path / "project" / "run.review" / "matching" / "r2-abc" / "run"
    run.mkdir(parents=True)
    assert _delivery_root(run, None) == tmp_path / "project" / "delivery"
    explicit = tmp_path / "elsewhere"
    assert _delivery_root(run, explicit) == explicit


def test_plain_run_directory_requires_an_explicit_delivery_root(tmp_path):
    run = tmp_path / "project" / "run"
    run.mkdir(parents=True)
    with pytest.raises(PublishError, match="--delivery-root"):
        _delivery_root(run, None)


def test_osm_snapshots_are_bound_per_scope(tmp_path):
    main = tmp_path / "main.json"
    main.write_text("{}", encoding="utf-8")
    panel = tmp_path / "panel.json"
    panel.write_text("{}", encoding="utf-8")
    assert _parse_osm_data([str(main), f"EMR28560={panel}"]) == {
        "main": main.resolve(), "EMR28560": panel.resolve(),
    }
    with pytest.raises(PublishError, match="Unsafe OSM snapshot scope"):
        _parse_osm_data([f"../escape={main}"])
    with pytest.raises(PublishError, match="Duplicate OSM snapshot"):
        _parse_osm_data([str(main), str(main)])
    with pytest.raises(PublishError, match="does not exist"):
        _parse_osm_data([str(tmp_path / "missing.json")])


def test_review_extent_is_expanded_before_sending_it_to_overpass():
    bbox = _search_bbox([124.88, 1.30, 124.90, 1.32])
    assert bbox["west"] < 124.88 < bbox["east"]
    assert bbox["south"] < 1.30 < bbox["north"]
    assert bbox["west"] == pytest.approx(124.88 - 0.02 * 0.06 - 0.001)


def test_existing_revision_delivery_is_never_overwritten(tmp_path):
    run = tmp_path / "project" / "run.review" / "matching" / "r2-abc" / "run"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps({"osm_matching": {"revision": 2, "status": "matched"}}), encoding="utf-8",
    )
    target = tmp_path / "project" / "delivery" / "r2"
    target.mkdir(parents=True)
    with pytest.raises(PublishError, match="already exists"):
        publish(run)
    assert not any(target.iterdir())


def test_missing_run_manifest_is_rejected_before_any_qgis_work(tmp_path):
    run = tmp_path / "project" / "run.review" / "matching" / "r2-abc" / "run"
    run.mkdir(parents=True)
    with pytest.raises(PublishError, match="Run manifest not found"):
        publish(run)


@pytest.mark.parametrize("status", ["abstained", "unavailable", "coarse"])
def test_unaccepted_match_cannot_be_published_as_matched(tmp_path, status):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "osm_matching": {"revision": 1, "status": status,
                         "quality": {"reason": "weak_or_ambiguous_network_alignment"}},
        "delivery_counts": {"PTECH": 3},
    }))
    with pytest.raises(PublishError, match=f"status {status}"):
        publish(run, delivery_root=tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()


def test_each_populated_partition_must_have_accepted_matching():
    manifest = {"osm_matching": {"revision": 1, "status": "unavailable"},
                "delivery_counts": {"CABLE": 0},
                "delivery_partitions": {"A": {"delivery_counts": {"CABLE": 2},
                                               "osm_matching": {"revision": 1, "status": "matched"}}}}
    assert _matching_scopes(manifest)["A"]["status"] == "matched"
    manifest["delivery_partitions"]["A"]["osm_matching"]["status"] = "abstained"
    with pytest.raises(PublishError, match="Scope A"):
        _matching_scopes(manifest)


def test_publish_skips_empty_main_and_reviews_matched_partition(tmp_path, monkeypatch):
    from cad2gis import match_delivery

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "osm_matching": {"revision": 1, "status": "unavailable"},
        "delivery_counts": {"CABLE": 0},
        "delivery_partitions": {"A": {
            "delivery_counts": {"CABLE": 2},
            "osm_matching": {"revision": 1, "status": "matched"},
        }},
    }))
    monkeypatch.setattr(match_delivery, "_resolve_qgis_python", lambda _: tmp_path / "python")
    monkeypatch.setattr(match_delivery, "package_delivery", lambda *_: {
        "source_sha256": "a" * 64, "run_manifest_sha256": "b" * 64,
        "run_status": "COMPLETED", "deliveries": [{"scope": "main"}, {"scope": "A"}],
    })
    monkeypatch.setattr(match_delivery, "_run_qgis", lambda *_: None)
    reviewed = []

    def review(entry, *args, **kwargs):
        reviewed.append(entry["scope"])
        assert kwargs["matching"]["status"] == "matched"
        return {"scope": entry["scope"]}

    monkeypatch.setattr(match_delivery, "_publish_scope", review)
    result = publish(run, delivery_root=tmp_path / "delivery")
    assert reviewed == ["A"]
    assert result["scopes"] == [{"scope": "A"}]


@pytest.mark.parametrize("change", ["snapshot", "receipt", "transient"])
def test_publication_rejects_changing_frozen_snapshot_before_atomic_publish(tmp_path, monkeypatch, change):
    from pathlib import Path
    from cad2gis import match_delivery

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "osm_matching": {"revision": 1, "status": "matched"}, "delivery_counts": {"CABLE": 1}}))
    snapshot = tmp_path / "osm.json"
    snapshot.write_text('{"elements": []}')
    original = snapshot.read_bytes()

    def package(_run, output):
        output.mkdir()
        (output / "delivery.gpkg").write_bytes(b"canonical delivery")
        return {"source_sha256": "a" * 64, "run_manifest_sha256": "b" * 64,
                "run_status": "CONDITIONAL", "deliveries": [{"scope": "main",
                "project": "delivery.qgz", "gpkg_sha256": match_delivery._file_sha256(output / "delivery.gpkg")}]}

    def qgis(_python, module, arguments):
        output = Path(arguments[arguments.index("--output") + 1])
        if module == "cad2gis.svg_delivery":
            output.mkdir()
            (output / "delivery.qgz").write_bytes(b"svg fixture")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"review fixture")
        actual = match_delivery._file_sha256(snapshot)
        if "--standalone" in arguments and change == "receipt":
            actual = "f" * 64
        output.with_suffix(".review.json").write_text(json.dumps({"osm_source_sha256": actual}))
        if "--standalone" not in arguments and change in {"snapshot", "transient"}:
            snapshot.write_text('{"elements": [{"id": 2}]}')
        if "--standalone" in arguments and change == "transient":
            snapshot.write_bytes(original)

    monkeypatch.setattr(match_delivery, "_resolve_qgis_python", lambda _: tmp_path / "python")
    monkeypatch.setattr(match_delivery, "package_delivery", package)
    monkeypatch.setattr(match_delivery, "_run_qgis", qgis)
    root = tmp_path / "delivery"
    with pytest.raises(PublishError, match="OSM snapshot changed"):
        publish(run, delivery_root=root, osm_data=[str(snapshot)])
    assert not (root / "r1").exists()
    assert not list(root.glob(".r1.partial-*"))


def test_publication_rejects_missing_populated_scope(tmp_path, monkeypatch):
    from cad2gis import match_delivery

    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "osm_matching": {"revision": 1, "status": "matched"}, "delivery_counts": {"CABLE": 1},
        "delivery_partitions": {"A": {"delivery_counts": {"CABLE": 1}, "osm_matching": {"status": "matched"}}}}))
    monkeypatch.setattr(match_delivery, "_resolve_qgis_python", lambda _: tmp_path / "python")
    monkeypatch.setattr(match_delivery, "package_delivery", lambda *_: {
        "source_sha256": "a" * 64, "run_manifest_sha256": "b" * 64,
        "run_status": "CONDITIONAL", "deliveries": [{"scope": "main"}]})
    monkeypatch.setattr(match_delivery, "_run_qgis", lambda *_: None)
    monkeypatch.setattr(match_delivery, "_publish_scope", lambda entry, *args, **kwargs: {"scope": entry["scope"]})
    root = tmp_path / "delivery"
    with pytest.raises(PublishError, match="every populated matching scope"):
        publish(run, delivery_root=root)
    assert not (root / "r1").exists()
    assert not list(root.glob(".r1.partial-*"))
def test_qgis_environment_excludes_only_bundled_native_paths(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace
    from cad2gis import match_delivery

    package = tmp_path / "site-packages" / "pyramids"
    bundled = package.parent / "pyramids_gis.libs"
    custom = tmp_path / "custom-qgis"
    similar = package.parent / "pyramids_gis.libs-user"
    monkeypatch.setattr(match_delivery.importlib.util, "find_spec", lambda _: SimpleNamespace(origin=str(package / "__init__.py")))
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, [bundled, custom, similar])))
    monkeypatch.setenv("GDAL_DRIVER_PATH", str(package / "gdalplugins"))
    monkeypatch.setenv("GDAL_DATA", str(custom / "gdal"))
    monkeypatch.setenv("PROJ_LIB", str(package / "share" / "proj"))
    monkeypatch.setenv("CAD2GIS_QGIS_FONT", str(custom / "font.ttf"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "different-python"))
    before = dict(os.environ)
    result = match_delivery._qgis_environment()
    assert result["PATH"] == os.pathsep.join(map(str, [custom, similar]))
    assert "GDAL_DRIVER_PATH" not in result and "PROJ_LIB" not in result
    assert result["GDAL_DATA"] == str(custom / "gdal")
    assert result["CAD2GIS_QGIS_FONT"] == str(custom / "font.ttf")
    assert "PYTHONHOME" not in result
    assert dict(os.environ) == before


def test_qgis_probe_and_execution_share_isolated_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cad2gis import match_delivery

    executable = tmp_path / "python"
    executable.touch()
    calls = []
    monkeypatch.setattr(match_delivery, "_qgis_environment", lambda: {"PATH": "qgis-only"})
    monkeypatch.setattr(match_delivery.subprocess, "Popen", lambda command, **kwargs: calls.append(kwargs) or SimpleNamespace(wait=lambda timeout: 0))
    monkeypatch.setattr(match_delivery.subprocess, "run", lambda command, **kwargs: calls.append(kwargs) or SimpleNamespace(returncode=0))
    assert match_delivery._resolve_qgis_python(executable) == executable
    match_delivery._run_qgis(executable, "cad2gis.qgis_review_verify", [])
    assert len(calls) == 2
    assert all(call["env"]["PATH"] == "qgis-only" for call in calls)
    assert all(call["stdin"] == match_delivery.subprocess.DEVNULL for call in calls)
