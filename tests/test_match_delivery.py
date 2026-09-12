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
