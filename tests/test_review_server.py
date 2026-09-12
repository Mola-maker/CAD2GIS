from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from cad2gis.agent_mcp import MCPServiceError, audit_run, prepare_review_workspace
from cad2gis import review_server
from cad2gis.review_server import (
    ReviewConflictError,
    ReviewServerError,
    SQLiteReviewStore,
    create_review_app,
)


def _matching_fixture(tmp_path, monkeypatch):
    from cad2gis.cad2gis_v3.georef import DirectTransformer
    from cad2gis.cad2gis_v3.osm_matching import match_osm
    from pyproj import Transformer

    run, delivery, manifest_path = _run_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    nominal = DirectTransformer("EPSG:32749", "EPSG:32749")
    monkeypatch.setattr(review_server, "_registration_transformer", lambda _: nominal)
    monkeypatch.setattr(review_server.GeoPackageProvider, "layers", lambda _: [])
    monkeypatch.setattr(review_server.GeoPackageProvider, "geojson", lambda *a, **k: {
        "type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
        "geometry": {"type": "Point", "coordinates": [500000., 9200000.]}}]})
    manifest["profiles"] = {}
    for name in ("source_profile", "mapping_registry"):
        profile = tmp_path / f"{name}.json"
        profile.write_text("{}")
        manifest["profiles"][name] = {"path": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest()}
    _, record = match_osm(source=run / "fixture.dwg", source_sha256=manifest["source"]["sha256"],
        features=[], nominal=nominal, coordinate_domain={"passed": True})
    manifest["osm_matching"] = record
    manifest_path.write_text(json.dumps(manifest))
    workspace = tmp_path / "matching-review"
    client = TestClient(create_review_app(run, workspace_dir=workspace))
    lonlat = Transformer.from_crs(32749, 4326, always_xy=True)
    for index, (x, y, role, shift) in enumerate([
            (500000, 9200000, "train", 10), (500100, 9200100, "train", 10),
            (500050, 9200050, "check", 110)]):
        response = client.post("/api/review/features", json={"expected_revision": 0,
            "feature": {"type": "Feature", "id": f"gcp:{index}",
                "geometry": {"type": "Point", "coordinates": list(lonlat.transform(x + shift, y - 5))},
                "properties": {"_kind": "cad_map_gcp", "cad_x": x, "cad_y": y, "role": role}}})
        assert response.status_code == 200, response.text
    return client, run, workspace, manifest, nominal


def _matching_request(client, **extra):
    capture = client.get("/api/registration").json()
    return {"expected_controls_sha256": capture["controls_sha256"],
        "expected_match_sha256": capture["matching"]["match_sha256"],
        "expected_match_revision": capture["matching"]["revision"], **extra}


def test_osm_preview_and_rematch_share_original_native_transform(tmp_path, monkeypatch):
    from cad2gis.cad2gis_v3.osm_matching import load_match_profile
    from pyproj import Transformer

    client, run, workspace, manifest, nominal = _matching_fixture(tmp_path, monkeypatch)
    capture = client.get("/api/registration").json()
    assert capture["matching_ready"] is True
    assert capture["activation_ready"] is False  # OSM refinement does not activate surveyed GCP acceptance.
    assert capture["preview_fit"]["check_metrics"]["rmse_m"] > 90
    preview = client.get("/api/registration/preview/CABLE", params={"expected_controls_sha256": capture["controls_sha256"]})
    assert preview.status_code == 200, preview.text
    result = client.post("/api/matching/rematch", json=_matching_request(client)).json()
    assert result["status"] == "prepared"
    transform, _ = load_match_profile(result["profile"]["path"], manifest["source"]["sha256"], nominal)
    point = transform.point([500000, 9200000])
    expected = Transformer.from_crs(32749, 4326, always_xy=True).transform(*point)
    assert preview.json()["features"][0]["geometry"]["coordinates"] == pytest.approx(expected)
    assert "--match-profile" in result["conversion_command"]
    assert "--gcp-profile" not in result["conversion_command"]
    assert "--source-run" not in result["conversion_command"]  # A conversion run is not a native snapshot.
    assert result["current_run_dir"] == str(run)
    assert result["matching"]["absolute_accuracy_verified"] is False


@pytest.mark.parametrize("key,value", [("expected_controls_sha256", "stale"),
    ("expected_match_sha256", "stale"), ("expected_match_revision", 99)])
def test_rematch_rejects_stale_preview_before_writing(tmp_path, monkeypatch, key, value):
    client, _, workspace, _, _ = _matching_fixture(tmp_path, monkeypatch)
    payload = _matching_request(client, execute=True)
    payload[key] = value
    response = client.post("/api/matching/rematch", json=payload)
    assert response.status_code == 409
    assert not (workspace / "matching").exists()


def test_two_rematches_preserve_old_results_controls_and_restart(tmp_path, monkeypatch):
    client, original, workspace, manifest, nominal = _matching_fixture(tmp_path, monkeypatch)
    original_bytes = (original / "delivery.gpkg").read_bytes()
    source = original / "fixture.dwg"

    def convert(options):
        new_run = options["run_dir"]
        new_run.mkdir()
        record = json.loads(options["match_profile"].read_text(encoding="utf-8"))
        artifact = new_run / "delivery.gpkg"
        artifact.write_bytes(f"matched-version-{record['revision']}".encode())
        candidate = json.loads(json.dumps(manifest))
        candidate["source"]["path"] = str(source)
        candidate["osm_matching"] = record
        candidate["artifacts"]["delivery"] = {"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
        _add_svg_fixture(new_run, candidate, label=f"version-{record['revision']}")
        (new_run / "run_manifest.json").write_text(json.dumps(candidate))
        assert options["llm"] == "assist" and options["domain"] == "auto"
        assert options["svg_mode"] == "candidate" and options["svg_font_dirs"] == ()

    monkeypatch.setattr(review_server, "_execute_matching_conversion", convert)
    first_payload = _matching_request(client, execute=True)
    first = client.post("/api/matching/rematch", json=first_payload).json()
    assert first["status"] == "completed", first
    first_svg_url = client.get("/api/run").json()["svg_candidates"]["report_url"]
    assert f"version-{first['matching']['revision']}" in client.get(first_svg_url).text
    first_profile = Path(first["profile"]["path"]).read_bytes()
    first_delivery = Path(first["next_run_dir"], "delivery.gpkg").read_bytes()
    assert client.post("/api/matching/rematch", json=first_payload).status_code == 409
    second = client.post("/api/matching/rematch", json=_matching_request(client, execute=True)).json()
    assert second.get("status") == "completed", second
    second_svg_url = client.get("/api/run").json()["svg_candidates"]["report_url"]
    assert second_svg_url != first_svg_url
    assert client.get(first_svg_url).status_code == 409
    assert f"version-{second['matching']['revision']}" in client.get(second_svg_url).text
    assert second["next_run_dir"] != first["next_run_dir"]
    assert second["matching"]["revision"] == first["matching"]["revision"] + 1
    assert second["matching"]["translation_3857"] == pytest.approx(first["matching"]["translation_3857"])
    assert Path(first["profile"]["path"]).read_bytes() == first_profile
    assert Path(first["next_run_dir"], "delivery.gpkg").read_bytes() == first_delivery
    assert (original / "delivery.gpkg").read_bytes() == original_bytes
    assert len(client.get("/api/review/features").json()["features"]) == 3
    resumed = TestClient(create_review_app(original, workspace_dir=workspace))
    assert resumed.get("/api/run").json()["run_dir"] == second["next_run_dir"]
    assert resumed.get("/api/run").json()["svg_candidates"]["report_url"] == second_svg_url
    assert len(resumed.get("/api/review/features").json()["features"]) == 3
    same_workspace = TestClient(create_review_app(second["next_run_dir"], workspace_dir=workspace))
    assert len(same_workspace.get("/api/review/features").json()["features"]) == 3
    # Opening the generated run separately also recovers its frozen native picks.
    separate = TestClient(create_review_app(second["next_run_dir"], workspace_dir=tmp_path / "separate"))
    assert len(separate.get("/api/review/features").json()["features"]) == 3
    for feature in separate.get("/api/review/features").json()["features"]:
        separate.delete(f"/api/review/features/{feature['id']}", params={"expected_revision": 1})
    reopened = TestClient(create_review_app(second["next_run_dir"], workspace_dir=tmp_path / "separate"))
    assert reopened.get("/api/review/features").json()["features"] == []
    Path(second["next_run_dir"], "delivery.gpkg").write_bytes(b"tampered")
    with pytest.raises(ReviewServerError, match="delivery SHA-256"):
        create_review_app(original, workspace_dir=workspace)


def test_failed_rematch_keeps_active_result_and_retry_gets_new_version_directory(tmp_path, monkeypatch):
    client, run, _, _, _ = _matching_fixture(tmp_path, monkeypatch)
    def fail(_):
        raise ValueError("fixture conversion failed")
    monkeypatch.setattr(review_server, "_execute_matching_conversion", fail)
    payload = _matching_request(client, execute=True)
    first = client.post("/api/matching/rematch", json=payload).json()
    second = client.post("/api/matching/rematch", json=payload).json()
    assert first["status"] == second["status"] == "failed"
    assert first["next_run_dir"] != second["next_run_dir"]
    assert client.get("/api/run").json()["run_dir"] == str(run)
    assert len(client.get("/api/review/features").json()["features"]) == 3


def test_rematch_inherits_bound_decisions_and_refuses_unreproducible_semantics(tmp_path, monkeypatch):
    _, run, _, manifest, _ = _matching_fixture(tmp_path, monkeypatch)
    pack = tmp_path / "decision-pack.json"
    pack.write_text("{\"operations\": []}")
    manifest["artifacts"]["decision_pack"] = {"path": str(pack), "sha256": hashlib.sha256(pack.read_bytes()).hexdigest()}
    options = review_server._matching_conversion_options(manifest, run, tmp_path / "match.json", tmp_path / "next")
    assert options["decision_pack"] == pack
    assert options["llm"] == "assist"
    manifest["semantic_revision"] = {"job_id": "old-job", "revision": 3}
    with pytest.raises(ReviewServerError, match="semantic"):
        review_server._matching_conversion_options(manifest, run, tmp_path / "match.json", tmp_path / "next")


@pytest.mark.parametrize("mode", ["candidate", "off", None])
def test_rematch_inherits_svg_mode_and_repeats_each_font_directory(tmp_path, monkeypatch, mode):
    _, run, _, manifest, _ = _matching_fixture(tmp_path, monkeypatch)
    fonts = [tmp_path / "first font directory", tmp_path / "font's second directory"]
    if mode:
        manifest["svg_candidates"] = {"mode": mode,
            "font_dirs": [str(path) for path in fonts] if mode == "candidate" else []}
    options = review_server._matching_conversion_options(
        manifest, run, tmp_path / "match.json", tmp_path / "next")
    assert options["svg_mode"] == (mode or "candidate")
    expected_fonts = tuple(fonts) if mode == "candidate" else ()
    assert options["svg_font_dirs"] == expected_fonts
    command = review_server._matching_command(options)
    assert "--svg-mode" in command
    assert "--svg-font-dirs" not in command
    assert command.count("--svg-font-dir ") == len(expected_fonts)
    assert "WindowsPath(" not in command and "PosixPath(" not in command
    if expected_fonts:
        assert "first font directory" in command


@pytest.mark.parametrize("font_dirs", ["font-dir", [None], [""]])
def test_rematch_rejects_invalid_svg_font_binding(tmp_path, monkeypatch, font_dirs):
    _, run, _, manifest, _ = _matching_fixture(tmp_path, monkeypatch)
    manifest["svg_candidates"] = {"mode": "candidate", "font_dirs": font_dirs}
    with pytest.raises(ReviewServerError, match="SVG font directories"):
        review_server._matching_conversion_options(manifest, run, tmp_path / "match.json", tmp_path / "next")


@pytest.mark.parametrize("name", ["delivery_partitions", "spatial_regions", "osm_anchor"])
@pytest.mark.parametrize("change", ["changed", "removed", "appeared"])
def test_rematch_rejects_changed_optional_conversion_inputs(tmp_path, monkeypatch, name, change):
    _, run, _, manifest, _ = _matching_fixture(tmp_path, monkeypatch)
    auxiliary = tmp_path / f"{name}.json"
    descriptor = {"path": str(auxiliary), "kind": name}
    if change == "appeared":
        descriptor["exists"] = False
    else:
        auxiliary.write_text('{"reviewed": true}', encoding="utf-8")
        descriptor["sha256"] = hashlib.sha256(auxiliary.read_bytes()).hexdigest()
    manifest["conversion_snapshot"] = {"artifacts": {name: descriptor}}
    args = (manifest, run, tmp_path / "match.json", tmp_path / "next")
    assert review_server._matching_conversion_options(*args)["mapping_registry"] == tmp_path / "mapping_registry.json"
    if change == "removed":
        auxiliary.unlink()
    else:
        auxiliary.write_text('{"reviewed": false}', encoding="utf-8")
    with pytest.raises(ReviewServerError, match=name):
        review_server._matching_conversion_options(*args)


@pytest.mark.parametrize("scoped", [False, True])
def test_partition_rematch_uses_parent_binding_and_keeps_selected_partition(tmp_path, monkeypatch, scoped):
    original_client, original, workspace, manifest, _ = _matching_fixture(tmp_path, monkeypatch)
    controls = original_client.get("/api/review/features").json()["features"]
    source = original / "fixture.dwg"
    region = "EMR28560"

    def write_partition(root, parent):
        part = root / region
        part.mkdir()
        delivery = part / "delivery.gpkg"
        delivery.write_bytes(b"partition")
        counts = {"CABLE": 1}
        parent["delivery_partitions"] = {region: {"path": str(delivery),
            "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(), "delivery_counts": counts}}
        (part / "partition_manifest.json").write_text(json.dumps({"region_id": region, "delivery_counts": counts}))
        _add_svg_fixture(root, parent)
        (root / "run_manifest.json").write_text(json.dumps(parent), encoding="utf-8")
        return part

    original_part = write_partition(original, manifest)
    client = TestClient(create_review_app(original_part, workspace_dir=tmp_path / "partition-review"))
    svg_url = client.get("/api/run").json()["svg_candidates"]["report_url"]
    assert client.get(svg_url).status_code == 200
    for feature in controls:
        assert client.post("/api/review/features", json={"expected_revision": 0, "feature": feature}).status_code == 200
    capture = client.get("/api/registration").json()
    assert capture["matching_ready"] is True
    assert capture["preview_match"]["region_id"] == region
    assert capture["preview_match"]["parent_run_sha256"] == hashlib.sha256((original / "run_manifest.json").read_bytes()).hexdigest()

    def convert(options):
        root = options["run_dir"]
        root.mkdir()
        candidate = json.loads(json.dumps(manifest))
        candidate["source"]["path"] = str(source)
        candidate["osm_matching"] = json.loads(options["match_profile"].read_text(encoding="utf-8"))
        if scoped:
            artifact = root / "delivery.gpkg"
            artifact.write_bytes(b"scoped-matching-result")
            candidate["delivery_partition"] = {"region_id": region}
            candidate["delivery_partitions"] = {}
            candidate["delivery_counts"] = {"CABLE": 1}
            candidate["artifacts"]["delivery"] = {"path": str(artifact),
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
            _add_svg_fixture(root, candidate)
            (root / "run_manifest.json").write_text(json.dumps(candidate), encoding="utf-8")
        else:
            write_partition(root, candidate)

    monkeypatch.setattr(review_server, "_execute_matching_conversion", convert)
    result = client.post("/api/matching/rematch", json=_matching_request(client, execute=True)).json()
    assert result["status"] == "completed", result
    assert Path(result["current_run_dir"]) == Path(result["next_run_dir"]) / ("" if scoped else region)
    assert client.get("/api/run").json()["delivery_counts"] == {"CABLE": 1}
    next_svg_url = client.get("/api/run").json()["svg_candidates"]["report_url"]
    assert next_svg_url != svg_url
    assert client.get(next_svg_url).status_code == 200
    for selected in (original_part, Path(result["current_run_dir"])):
        reopened = TestClient(create_review_app(selected, workspace_dir=tmp_path / "partition-review"))
        assert reopened.get("/api/run").json()["run_dir"] == result["current_run_dir"]
        assert len(reopened.get("/api/review/features").json()["features"]) == 3


def test_matching_record_uses_selected_partition_before_main_record():
    main = {"region_id": "MAIN", "status": "matched"}
    region = {"region_id": "EMR28560", "status": "coarse"}
    manifest = {"osm_matching": main, "delivery_partition": {"region_id": "EMR28560"},
        "delivery_partitions": {"EMR28560": {"osm_matching": region}}}
    assert review_server._matching_record(manifest) is region
    manifest["delivery_partitions"] = {}
    manifest["delivery_partition"]["osm_matching"] = region
    assert review_server._matching_record(manifest) is region


def _feature(feature_id: str = "review:1") -> dict:
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": "LineString",
            "coordinates": [[123.0, 0.5], [123.001, 0.501]],
        },
        "properties": {
            "review_status": "needs_correction",
            "review_note": "Observed offset",
        },
    }


def _run_fixture(tmp_path) -> tuple:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "fixture.dwg"
    source.write_bytes(b"immutable-source-fixture")
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"immutable-delivery-fixture")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "modes": {"domain": "auto", "llm": "assist"},
        "source_entity_count": 1538,
        "delivery_counts": {"SITE": 1, "CABLE": 33},
        "source": {
            "path": "fixture.dwg",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "artifacts": {
            "delivery": {"path": str(delivery), "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest()},
        },
        "crs": {
            "source_crs": "EPSG:32749",
            "target_crs": "EPSG:32749",
        },
        "validation": {},
        "reasoning": {},
    }
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir, delivery, manifest_path


def _add_svg_fixture(run_dir, manifest, *, label="SVG fixture"):
    root = run_dir / "svg-candidates"
    root.mkdir()
    (root / "symbols").mkdir()
    report = {"source_sha256": manifest["source"]["sha256"],
        "all_points_accounted_for": 2, "dispositions": {"SOURCE_INSTANCE_ONLY": 2}}
    (root / "correspondence.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "correspondence.html").write_text(f'<h1>{label}</h1><a href="symbols/instance-a.svg"><img src="symbols/instance-a.png"></a>', encoding="utf-8")
    (root / "symbols.sqlite3").write_bytes(b"bound-store-fixture")
    (root / "symbols" / "instance-a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    import base64
    (root / "symbols" / "instance-a.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII="))
    manifest["svg_candidates"] = {"mode": "candidate", "status": "completed",
        "auto_applied": False, "font_dirs": [], "output_dir": str(root),
        "report": str(root / "correspondence.json"), "html": str(root / "correspondence.html"),
        "store": str(root / "symbols.sqlite3"), "files": {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}}
    for name, filename in [("report", "correspondence.json"), ("html", "correspondence.html"),
            ("store", "symbols.sqlite3")]:
        manifest["artifacts"][f"svg_candidates_{name}"] = {"path": str(root / filename),
            "sha256": manifest["svg_candidates"]["files"][filename]}
    return root


def test_review_serves_only_bound_svg_files_from_current_run(tmp_path):
    run, _, manifest_path = _run_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    root = _add_svg_fixture(run, manifest)
    # A copied run uses its adjacent files even if the original paths still exist.
    other = tmp_path / "other-report.html"
    other.write_text("other run")
    manifest["svg_candidates"]["html"] = str(other)
    manifest["artifacts"]["svg_candidates_html"]["path"] = str(other)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    client = TestClient(create_review_app(run, workspace_dir=tmp_path / "review"))
    summary = client.get("/api/run").json()["svg_candidates"]
    assert summary["status"] == "completed" and summary["auto_applied"] is False
    assert summary["point_count"] == 2
    assert "files" not in summary
    url = summary["report_url"]
    prefix = url.rsplit("/", 1)[0]
    response = client.get(url)
    assert response.status_code == 200 and "SVG fixture" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "sandbox" in response.headers["content-security-policy"]
    for relative in ("correspondence.json", "symbols.sqlite3", "symbols/instance-a.svg", "symbols/instance-a.png"):
        assert client.get(f"{prefix}/{relative}").status_code == 200
    image = client.get(f"{prefix}/symbols/instance-a.png")
    assert image.headers["content-type"] == "image/png"
    assert image.content == (root / "symbols/instance-a.png").read_bytes()
    (root / "symbols/instance-a.png").write_bytes(b"changed")
    assert client.get(f"{prefix}/symbols/instance-a.png").status_code == 404
    (root / "private.json").write_text('{"private": true}')
    assert client.get(f"{prefix}/private.json").status_code == 404
    assert client.get(url.replace(prefix, "/api/svg-candidates/" + "0" * 64)).status_code == 409
    (root / "symbols" / "instance-a.svg").write_text("changed")
    assert client.get(f"{prefix}/symbols/instance-a.svg").status_code == 404
    (root / "correspondence.html").write_text("changed")
    assert client.get(url).status_code == 404
    unavailable = client.get("/api/run").json()["svg_candidates"]
    assert unavailable["report_url"] is None and "SHA-256" in unavailable["report_blocker"]


def test_svg_report_serves_bound_png_previews_and_csv_queue(tmp_path):
    from PIL import Image

    run, _, manifest_path = _run_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    root = _add_svg_fixture(run, manifest)
    png_path = root / "symbols" / "instance-a.png"
    Image.new("RGB", (2, 1), "red").save(png_path)
    png = png_path.read_bytes()
    queue = "source_handle,status\nA,待人工复核\n".encode()
    (root / "svg-review-queue.csv").write_bytes(queue)
    (root / "correspondence.html").write_text(
        '<img src="symbols/instance-a.png"><a href="svg-review-queue.csv">Queue</a>',
        encoding="utf-8",
    )
    for relative in ("symbols/instance-a.png", "svg-review-queue.csv", "correspondence.html"):
        manifest["svg_candidates"]["files"][relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    manifest["artifacts"]["svg_candidates_html"]["sha256"] = manifest["svg_candidates"]["files"]["correspondence.html"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with TestClient(create_review_app(run, workspace_dir=tmp_path / "review")) as client:
        url = client.get("/api/run").json()["svg_candidates"]["report_url"]
        prefix = url.rsplit("/", 1)[0]
        assert 'src="symbols/instance-a.png"' in client.get(url).text
        for relative, expected, media_type in (
            ("symbols/instance-a.png", png, "image/png"),
            ("svg-review-queue.csv", queue, "text/csv"),
        ):
            response = client.get(f"{prefix}/{relative}")
            assert response.status_code == 200
            assert response.headers["content-type"].split(";")[0] == media_type
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.content == expected

        # Adding supported suffixes never makes unsealed or outside-run files public.
        (root / "symbols" / "unsealed.png").write_bytes(png)
        (root / "unsealed.csv").write_bytes(queue)
        for relative in ("symbols/unsealed.png", "unsealed.csv", "%2e%2e/run_manifest.json", "symbols/instance-a.png.exe"):
            assert client.get(f"{prefix}/{relative}").status_code == 404
        for relative in ("symbols/instance-a.png", "svg-review-queue.csv"):
            (root / relative).write_bytes(b"changed after sealing")
            response = client.get(f"{prefix}/{relative}")
            assert response.status_code == 404
            assert "SHA-256 mismatch" in response.json()["detail"]


@pytest.mark.parametrize("relative", ["../fixture.dwg", "symbols/../../fixture.dwg", "/fixture.dwg",
    "symbols\\instance-a.svg", "C:/secret.json", "private.txt"])
def test_svg_artifact_rejects_unsafe_paths_and_unapproved_suffixes(tmp_path, relative):
    run, _, manifest_path = _run_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    _add_svg_fixture(run, manifest)
    with pytest.raises(ReviewServerError, match="SVG candidate artifact"):
        review_server._svg_candidate_path(manifest, run, relative)


@pytest.mark.parametrize("record", [None, {"mode": "off", "status": "disabled", "font_dirs": []}])
def test_review_svg_status_handles_legacy_and_disabled_runs(tmp_path, record):
    run, _, manifest_path = _run_fixture(tmp_path)
    if record:
        manifest = json.loads(manifest_path.read_text())
        manifest["svg_candidates"] = record
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    client = TestClient(create_review_app(run, workspace_dir=tmp_path / "review"))
    svg = client.get("/api/run").json()["svg_candidates"]
    assert svg["status"] == ("disabled" if record else "not_recorded")
    assert svg["report_url"] is None
    run_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert client.get(f"/api/svg-candidates/{run_hash}/correspondence.html").status_code == 404


def test_svg_report_rejects_changed_manifest_and_foreign_source_receipt(tmp_path):
    run, _, manifest_path = _run_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    root = _add_svg_fixture(run, manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    client = TestClient(create_review_app(run, workspace_dir=tmp_path / "review"))
    original_url = client.get("/api/run").json()["svg_candidates"]["report_url"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert client.get(original_url).status_code == 409
    receipt = root / "correspondence.json"
    receipt.write_text(json.dumps({"source_sha256": "different-source"}), encoding="utf-8")
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    manifest["svg_candidates"]["files"]["correspondence.json"] = digest
    manifest["artifacts"]["svg_candidates_report"]["sha256"] = digest
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reopened = TestClient(create_review_app(run, workspace_dir=tmp_path / "review-2"))
    summary = reopened.get("/api/run").json()["svg_candidates"]
    assert summary["report_url"] is None
    assert "different CAD source" in summary["report_blocker"]


def test_canonical_registration_preview_uses_target_crs_and_excludes_checks(tmp_path, monkeypatch):
    from cad2gis.native_runtime import ensure_osgeo_runtime
    ensure_osgeo_runtime()
    from cad2gis.cad2gis_v3.georef import DirectTransformer
    from pyproj import Transformer

    run, _, _ = _run_fixture(tmp_path)
    nominal = DirectTransformer("EPSG:32749", "EPSG:32749")
    monkeypatch.setattr(review_server, "_registration_transformer", lambda _: nominal)
    monkeypatch.setattr(review_server.GeoPackageProvider, "geojson", lambda *a, **k: {
        "type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
        "geometry": {"type": "Point", "coordinates": [500000., 9200000.]}}]})
    client = TestClient(create_review_app(run, workspace_dir=tmp_path / "web"))
    to_lonlat = Transformer.from_crs(32749, 4326, always_xy=True)
    for i, (x, y, role, dx) in enumerate([(500000, 9200000, "train", 10),
            (500100, 9200010, "train", 10), (500020, 9200100, "train", 10),
            (500050, 9200050, "check", 110)]):
        lon, lat = to_lonlat.transform(x+dx, y-5)
        response = client.post("/api/review/features", json={"expected_revision": 0, "actor": "test",
            "feature": {"type": "Feature", "id": f"gcp:{i}",
            "geometry": {"type": "Point", "coordinates": [lon,lat]},
            "properties": {"_kind": "cad_map_gcp", "cad_x": x, "cad_y": y, "role": role}}})
        assert response.status_code == 200, response.text
    capture = client.get("/api/registration").json()
    fit = capture["preview_fit"]
    assert fit["selected_model"] == "translation"
    assert fit["target_crs"] == "EPSG:32749"
    assert fit["train_metrics"]["rmse_m"] < 1e-6
    assert fit["check_metrics"]["rmse_m"] == pytest.approx(100, abs=1e-6)
    assert fit["validation"]["passed"] is None  # preview is not engineering acceptance
    endpoint = "/api/registration/preview/CABLE"
    response = client.get(endpoint, params={"expected_controls_sha256": capture["controls_sha256"]})
    assert response.status_code == 200, response.text
    point = response.json()["features"][0]["geometry"]["coordinates"]
    assert point == pytest.approx(to_lonlat.transform(500010, 9199995), abs=1e-8)
    assert client.get(endpoint, params={"expected_controls_sha256": "stale"}).status_code == 409


def test_registration_preview_rejects_modified_profile(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    with pytest.raises(ReviewServerError, match="hash mismatch"):
        review_server._registration_transformer({"profiles": {"source_profile": {"path": str(profile), "sha256": "0"*64}}})


def test_sqlite_review_store_is_revisioned_and_conflict_safe(tmp_path) -> None:
    store = SQLiteReviewStore(
        tmp_path / "review.sqlite3", session_id="run:source",
    )
    created = store.upsert(
        _feature(), expected_revision=0, actor="tester",
    )
    assert created["revision"] == 1
    collection = store.feature_collection()
    assert collection["features"][0]["properties"]["_review_revision"] == 1

    with pytest.raises(ReviewConflictError, match="revision is 1"):
        store.upsert(_feature(), expected_revision=0, actor="stale-client")

    deleted = store.delete(
        "review:1", expected_revision=1, actor="tester",
    )
    assert deleted["revision"] == 2
    assert store.feature_collection()["features"] == []
    events = store.events()
    assert [event["operation"] for event in events] == [
        "upsert", "delete",
    ]
    assert [event["operation"] for event in store.events(after=events[0]["event_id"])] == [
        "delete",
    ]


def test_review_store_rejects_non_wgs84_or_nonfinite_geometry(tmp_path) -> None:
    store = SQLiteReviewStore(
        tmp_path / "review.sqlite3", session_id="run:source",
    )
    invalid = _feature()
    invalid["geometry"]["coordinates"][0][0] = 500.0
    with pytest.raises(ReviewServerError, match="EPSG:4326"):
        store.upsert(invalid, expected_revision=0, actor="tester")


def test_sqlite_review_connections_close_after_reads_writes_and_conflicts(tmp_path, monkeypatch):
    original = sqlite3.connect
    connections = []

    def tracked(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(review_server.sqlite3, "connect", tracked)
    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="one")
    store.upsert(_feature(), expected_revision=0, actor="tester")
    with pytest.raises(ReviewConflictError):
        store.upsert(_feature(), expected_revision=0, actor="stale")
    assert len(store.feature_collection()["features"]) == 1
    assert len(store.events()) == 1
    store.delete("review:1", expected_revision=1, actor="tester")
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")
    restored = SQLiteReviewStore(store.path, session_id="one")
    assert [event["revision"] for event in restored.events()] == [1, 2]
    assert restored.feature_collection()["features"] == []


def test_review_event_replay_uses_insertion_order_across_sessions_and_restart(tmp_path):
    from contextlib import closing

    store = SQLiteReviewStore(tmp_path / "review.sqlite3", session_id="active")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.executemany("INSERT INTO review_events VALUES(?,?,?,?,?,?,?)", (
            (f"event:{i}", "active" if i % 3 == 0 else "other", "f", "upsert", 1, "{}", str(3000-i))
            for i in range(3000)
        ))
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT event_id FROM review_events WHERE session_id=? ORDER BY rowid LIMIT ?",
            ("active", 200),
        ).fetchall()
    assert not any("TEMP B-TREE" in row[3] for row in plan)
    restored = SQLiteReviewStore(store.path, session_id="active")
    collected, after = [], ""
    while batch := restored.events(after=after, limit=137):
        collected.extend(event["event_id"] for event in batch)
        after = batch[-1]["event_id"]
    assert collected == [f"event:{i}" for i in range(0, 3000, 3)]
    assert restored.events(after="event:1") == []


def test_review_relocates_copied_run_artifact_only_by_matching_hash(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir = tmp_path / "copied-run"
    run_dir.mkdir()
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"portable-delivery")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "source": {"path": "/old-host/drawing.dwg", "sha256": "a" * 64},
        "artifacts": {
            "delivery": {
                "path": "/old-host/run/delivery.gpkg",
                "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(),
            },
        },
        "crs": {"source_crs": "EPSG:32749", "target_crs": "EPSG:32749"},
        "validation": {},
        "reasoning": {},
    }
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    client = TestClient(create_review_app(run_dir, workspace_dir=tmp_path / "review"))
    assert client.get("/api/health").json()["status"] == "ok"
    run = client.get("/api/run").json()
    assert run["source_available"] is False
    assert "SHA-256" in run["source_blocker"]

    manifest["artifacts"]["delivery"]["sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReviewServerError, match="SHA-256 mismatch"):
        create_review_app(run_dir, workspace_dir=tmp_path / "review-2")


def test_mcp_run_audit_verifies_hashes_counts_and_source_replay(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = run_dir / "drawing.dwg"
    source.write_bytes(b"source")
    delivery = run_dir / "delivery.gpkg"
    delivery.write_bytes(b"delivery")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "source": {
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "artifacts": {
            "delivery": {
                "path": str(delivery),
                "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(),
            },
        },
        "delivery_counts": {"CABLE": 2},
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )

    class FakeProvider:
        def __init__(self, _path):
            pass

        def layers(self):
            return [{"name": "CABLE", "feature_count": 2}]

    monkeypatch.setattr(review_server, "GeoPackageProvider", FakeProvider)
    passed = audit_run(str(run_dir))
    assert passed["audit_status"] == "PASS"
    assert passed["source_replay"]["available"] is True

    delivery.write_bytes(b"tampered")
    failed = audit_run(str(run_dir))
    assert failed["audit_status"] == "FAIL"
    assert "artifact:delivery" in failed["failures"]


@pytest.mark.parametrize("query", ["?review=selected-result", "?review=selected-result&demo=1"])
def test_selected_review_serves_map_without_historical_demo_before_javascript(
    tmp_path, query,
) -> None:
    run_dir, _, _ = _run_fixture(tmp_path)
    client = TestClient(create_review_app(run_dir, workspace_dir=tmp_path / "review"))
    response = client.get("/" + query)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    # Assert against the response itself: hiding after scripts run still flashes
    # the old page and lets ?demo=1 replace real API requests with fixture data.
    assert '<body class="live-review focused-review">' in page
    for retained in ('id="console-app"', 'id="local-map"', 'id="map"',
                     'id="gcp-role"', 'id="preview-gcp"', 'id="export-gcp"',
                     'id="visual-dialog"', "./assets/vendor/ol.js", "./assets/app.js"):
        assert retained in page
    for removed in ('id="hero-page"', 'id="plugin-guide"', 'id="process-terminal"',
                    "hero-motion.js", "demo-fixture.js", "Hutabohu", "Gorontalo",
                    "9,717", "557e01413c39", "public-demo-only"):
        assert removed not in page
    assert "./assets/review.css" in page
    assert client.get("/api/run").json()["source_entity_count"] == 1538
    assert 'id="hero-page"' not in client.get("/").text


def test_review_app_keeps_run_artifacts_immutable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir, delivery, manifest_path = _run_fixture(tmp_path)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (delivery, manifest_path)
    }
    workspace = tmp_path / "review-workspace"
    client = TestClient(
        create_review_app(run_dir, workspace_dir=workspace),
    )

    assert client.get("/api/health").json()["status"] == "ok"
    run_summary = client.get("/api/run").json()
    assert run_summary["source_entity_count"] == 1538
    assert run_summary["delivery_counts"] == {"SITE": 1, "CABLE": 33}
    page = client.get("/").text
    assert "核对当前地图匹配" in page
    assert 'id="hero-page"' not in page
    assert 'hero-motion.js' not in page
    assert 'demo-fixture.js' not in page
    assert 'data-process-terminal' not in page
    assert '"optionalGcp":true' in page
    assert 'id="stage-registration" class="stage-card registration-card" hidden' in page
    assert './assets/review.css' in page
    response = client.post("/api/review/features", json={
        "feature": _feature(),
        "expected_revision": 0,
        "actor": "test-client",
    })
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    features = client.get("/api/review/features").json()
    assert len(features["features"]) == 1
    conflict = client.post("/api/review/features", json={
        "feature": _feature(),
        "expected_revision": 0,
        "actor": "stale-client",
    })
    assert conflict.status_code == 409

    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (delivery, manifest_path)
    }
    assert after == before
    assert (workspace / "review.sqlite3").is_file()


def test_review_app_transfers_coordinates_and_exports_active_profile(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir, _, _ = _run_fixture(tmp_path)
    workspace = tmp_path / "review-workspace"
    client = TestClient(
        create_review_app(run_dir, workspace_dir=workspace),
    )
    controls = [
        ("train", 0.0, 0.0, 112.70, -7.45),
        ("train", 100.0, 0.0, 112.71, -7.45),
        ("train", 0.0, 100.0, 112.70, -7.44),
        ("train", 100.0, 100.0, 112.71, -7.44),
        ("check", 20.0, 20.0, 112.702, -7.448),
        ("check", 80.0, 20.0, 112.708, -7.448),
        ("check", 50.0, 80.0, 112.705, -7.442),
    ]
    for index, (role, x, y, lon, lat) in enumerate(controls):
        response = client.post("/api/review/features", json={
            "expected_revision": 0,
            "actor": "test-client",
            "feature": {
                "type": "Feature",
                "id": f"gcp:{index}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": {
                    "_kind": "cad_map_gcp",
                    "role": role,
                    "cad_x": x,
                    "cad_y": y,
                },
            },
        })
        assert response.status_code == 200

    capture = client.get("/api/registration")
    assert capture.status_code == 200
    assert capture.json()["activation_ready"] is True
    assert capture.json()["controls"][0]["target_crs"] == "EPSG:32749"
    assert capture.json()["controls"][0]["target_easting"] > 100_000

    exported = client.post(
        "/api/registration/export", json={"activate": True},
    )
    assert exported.status_code == 200, exported.text
    result = exported.json()
    assert result["profile"]["enabled"] is True
    assert result["absolute_accuracy_verified"] is False
    assert "--gcp-profile" in result["conversion_command"]
    assert "--llm assist" in result["conversion_command"]
    assert "--domain auto" in result["conversion_command"]
    assert result["source_run_modes"] == {"domain": "auto", "llm": "assist"}
    assert (workspace / "web_gcp_profile.json").is_file()

    (run_dir / "fixture.dwg").unlink()
    archived = client.post(
        "/api/registration/export", json={"activate": True},
    ).json()
    assert archived["source_available"] is False
    assert archived["conversion_command"] is None
    assert "SHA-256" in archived["source_blocker"]


def test_mcp_prepares_separate_review_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "source": {"sha256": "b" * 64},
    }), encoding="utf-8")

    result = prepare_review_workspace(str(run_dir), port=9876)

    assert result["immutable_delivery"] is True
    assert result["url"] == "http://127.0.0.1:9876"
    assert "cad2gis review" in result["launch_command"]
    assert (tmp_path / "run.review" / "review.sqlite3").is_file()
    with sqlite3.connect(result["database"]) as database:
        database.execute("CREATE TABLE preserve_existing_review (value TEXT)")
        database.execute("INSERT INTO preserve_existing_review VALUES ('reviewed')")
    again = prepare_review_workspace(str(run_dir), workspace_dir=result["workspace_dir"], port=9876)
    assert again == result
    with sqlite3.connect(result["database"]) as database:
        assert database.execute("SELECT value FROM preserve_existing_review").fetchall() == [("reviewed",)]


@pytest.mark.parametrize("location,marker", [
    ("self", "run_manifest.json"), ("other", "run_manifest.json"),
    ("other", "source_manifest.json"), ("other", "partition_manifest.json"),
])
def test_mcp_prepare_rejects_immutable_workspace_ancestors(tmp_path, monkeypatch, location, marker):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({"source": {"sha256": "b" * 64}}))
    immutable = run if location == "self" else tmp_path / "other-run"
    immutable.mkdir(exist_ok=True)
    if location != "self":
        (immutable / marker).write_text("{}")
    before = {p.relative_to(immutable): p.read_bytes() for p in immutable.rglob("*") if p.is_file()}
    with pytest.raises(MCPServiceError, match="outside immutable"):
        prepare_review_workspace(str(run), workspace_dir=str(immutable / "nested" / "review"))
    assert {p.relative_to(immutable): p.read_bytes() for p in immutable.rglob("*") if p.is_file()} == before
    assert not (immutable / "nested").exists()


@pytest.mark.parametrize("redirect", ["workspace", "database"])
def test_mcp_prepare_rejects_links_into_immutable_runs(tmp_path, monkeypatch, redirect):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({"source": {"sha256": "b" * 64}}))
    immutable = tmp_path / "other-run"
    immutable.mkdir()
    (immutable / "run_manifest.json").write_text("{}")
    target = immutable / "review.sqlite3"
    target.write_bytes(b"immutable database")
    workspace = tmp_path / "review"
    try:
        if redirect == "workspace":
            workspace.symlink_to(immutable, target_is_directory=True)
        else:
            workspace.mkdir()
            (workspace / "review.sqlite3").symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(MCPServiceError, match="outside immutable"):
        prepare_review_workspace(str(run), workspace_dir=str(workspace))
    assert target.read_bytes() == b"immutable database"


def test_review_console_exposes_toc_copy_and_accessibility_contracts(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    run_dir, _, _ = _run_fixture(tmp_path)
    client = TestClient(create_review_app(run_dir, workspace_dir=tmp_path / "review"))

    page = client.get("/")
    assert page.status_code == 200
    assert '<meta name="theme-color" content="#f5f8f7">' in page.text
    assert 'aria-label="审查步骤"' in page.text
    assert 'data-tab="console"' in page.text
    assert 'id="copy-command"' in page.text
    assert 'id="copy-coordinate"' in page.text
    assert './assets/demo-fixture.js' not in page.text

    script = client.get("/assets/app.js")
    assert script.status_code == 200
    assert "navigator.clipboard" in script.text
    assert "terminalEvent" in script.text
    assert "/geojson`" in script.text
    assert 'dataProjection: "EPSG:4326"' in script.text
    assert 'featureProjection: "EPSG:3857"' in script.text
    assert "CAD geometry; no DIMENSION" not in script.text
    assert 'feature.get("display_label")' in script.text
    assert "run.source_entity_count" in script.text
    assert "run.delivery_counts" in script.text
    assert "declutter: true" in script.text

    demo = client.get("/assets/demo-fixture.js")
    assert demo.status_code == 200
    assert "CAD2GIS_DERIVED_FIXTURE" in demo.text
    assert "selectProject" in demo.text
    assert "window.CAD2GIS_DEMO" in demo.text

    stylesheet = client.get("/assets/styles.css")
    assert stylesheet.status_code == 200
    assert "color-scheme: light" in stylesheet.text
    assert "--accent: #006c67" in stylesheet.text
    assert "color-scheme: dark" not in stylesheet.text
    assert "prefers-reduced-motion" in stylesheet.text

    for packaged_asset in (
        "hero-evidence-graph.svg",
        "noto-sans-sc-subset.woff2",
    ):
        response = client.get(f"/assets/{packaged_asset}")
        assert response.status_code == 200
        assert response.content

    traversal = client.get("/assets/../pyproject.toml")
    assert traversal.status_code == 404


def _partition_fixture(tmp_path):
    run, _, manifest_path = _run_fixture(tmp_path)
    partition = run / "EMR28560"
    partition.mkdir()
    delivery = partition / "delivery.gpkg"
    delivery.write_bytes(b"partition-specific-delivery")
    evidence = run / "evidence.gpkg"
    evidence.write_bytes(b"shared-parent-evidence")
    counts = {"BOITE": 6, "PTECH": 6, "EMR": 1, "INFRASTRUCTURE": 1, "CABLE": 6}
    parent = json.loads(manifest_path.read_text())
    parent["artifacts"]["evidence"] = {
        "path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    parent["delivery_partitions"] = {"EMR28560": {
        "path": str(delivery), "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(),
        "delivery_counts": counts}}
    manifest_path.write_text(json.dumps(parent), encoding="utf-8")
    partition_manifest = partition / "partition_manifest.json"
    partition_manifest.write_text(json.dumps({
        "schema_version": "cad2gis-delivery-partition-v1", "region_id": "EMR28560",
        "delivery_counts": counts}), encoding="utf-8")
    return partition, parent, manifest_path, partition_manifest


def test_partition_api_reports_its_own_counts_and_retains_parent_source_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    partition, parent, manifest_path, partition_manifest = _partition_fixture(tmp_path)
    original = {path: path.read_bytes() for path in (
        manifest_path, partition_manifest, partition / "delivery.gpkg")}
    original_provider = review_server.GeoPackageProvider
    assert original_provider(partition / "delivery.gpkg")._evidence_path() == partition.parent / "evidence.gpkg"
    observed = []

    class Provider:
        def __init__(self, path):
            observed.append(path)

        def layers(self):
            return [{"name": name, "feature_count": count}
                    for name, count in parent["delivery_partitions"]["EMR28560"]["delivery_counts"].items()]

    monkeypatch.setattr(review_server, "GeoPackageProvider", Provider)
    client = TestClient(create_review_app(partition, workspace_dir=tmp_path / "review-partition"))
    summary = client.get("/api/run").json()
    counts = parent["delivery_partitions"]["EMR28560"]["delivery_counts"]
    assert summary["delivery_counts"] == counts
    assert sum(summary["delivery_counts"].values()) == 20
    assert summary["delivery_counts"] != parent["delivery_counts"]
    assert sum(layer["feature_count"] for layer in client.get("/api/layers").json()["layers"]) == 20
    assert summary["run_dir"] == str(partition)
    assert summary["source"] == parent["source"]
    assert summary["source_available"] is True
    assert summary["source_resolved_path"] == str(partition.parent / "fixture.dwg")
    assert summary["parent_run_provenance"] == {
        "run_dir": str(partition.parent), "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "run_status": "CONDITIONAL", "source_entity_count_scope": "parent_run",
        "validation_scope": "parent_run"}
    assert observed == [partition / "delivery.gpkg"]
    assert all(path.read_bytes() == value for path, value in original.items())


@pytest.mark.parametrize("mutation", ["count", "boolean_count", "missing_entry", "wrong_region", "wrong_path", "wrong_sha", "missing_sha"])
def test_partition_review_rejects_unbound_or_inconsistent_manifest(tmp_path, mutation):
    partition, parent, manifest_path, partition_manifest = _partition_fixture(tmp_path)
    child = json.loads(partition_manifest.read_text())
    entry = parent["delivery_partitions"]["EMR28560"]
    if mutation == "count":
        child["delivery_counts"]["CABLE"] = 99
    elif mutation == "boolean_count":
        child["delivery_counts"]["EMR"] = True
    elif mutation == "missing_entry":
        parent["delivery_partitions"] = {}
    elif mutation == "wrong_region":
        child["region_id"] = "OTHER"
    elif mutation == "wrong_path":
        entry["path"] = str(partition.parent / "OTHER/delivery.gpkg")
    elif mutation == "wrong_sha":
        entry["sha256"] = "a" * 64
    elif mutation == "missing_sha":
        entry["sha256"] = ""
    manifest_path.write_text(json.dumps(parent), encoding="utf-8")
    partition_manifest.write_text(json.dumps(child), encoding="utf-8")
    with pytest.raises(ReviewServerError, match="(?i)partition"):
        create_review_app(partition, workspace_dir=tmp_path / "review-invalid")


def test_matching_profile_preserves_initial_pick_and_undo_in_a_new_workspace(tmp_path, monkeypatch):
    client, run, _, manifest, _ = _matching_fixture(tmp_path, monkeypatch)
    original = client.get("/api/review/features").json()["features"][0]
    target = original["geometry"]["coordinates"]
    edited = client.post("/api/review/features/gcp:0/adjust", json={
        "operation": "nudge", "direction": "east", "step_m": 0.1, "expected_revision": 1,
    })
    assert edited.status_code == 200, edited.text
    result = client.post("/api/matching/rematch", json=_matching_request(client)).json()
    assert result["status"] == "prepared", result
    record = result["matching"]
    assert record["controls"][0]["editor_state"]["initial_target"] == target
    saved = tmp_path / "opened-result"
    saved.mkdir()
    delivery = saved / "delivery.gpkg"
    delivery.write_bytes(b"new immutable result")
    manifest["osm_matching"] = record
    manifest["artifacts"]["delivery"] = {"path": str(delivery),
        "sha256": hashlib.sha256(delivery.read_bytes()).hexdigest()}
    (saved / "run_manifest.json").write_text(json.dumps(manifest))
    reopened = TestClient(create_review_app(saved, workspace_dir=tmp_path / "new-workspace"))
    restored = reopened.get("/api/review/features").json()["features"][0]
    assert restored["properties"]["_gcp_edit"]["initial_target"] == target
    undone = reopened.post("/api/review/features/gcp:0/adjust", json={
        "operation": "undo", "expected_revision": 1,
    })
    assert undone.status_code == 200, undone.text
    assert undone.json()["payload"]["geometry"]["coordinates"] == target
    assert undone.json()["payload"]["properties"]["cad_x"] == original["properties"]["cad_x"]


def test_registration_export_keeps_the_original_coverage_policy(tmp_path, monkeypatch):
    run, _, manifest_path = _run_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    template = Path(__file__).resolve().parents[1] / "experiment/config/apd_source_profile.json"
    source_profile = json.loads(template.read_text())
    source_profile.update(source_sha256=manifest["source"]["sha256"],
                          source_crs="EPSG:32749", target_crs="EPSG:32749")
    source = tmp_path / "source-profile.json"
    source.write_text(json.dumps(source_profile))
    manifest["profiles"] = {"source_profile": {"path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}
    manifest_path.write_text(json.dumps(manifest))
    client = TestClient(create_review_app(run, workspace_dir=tmp_path / "review"))
    for index, (role, x, y) in enumerate([
        ("train", 0, 0), ("train", 100, 0), ("train", 0, 100), ("train", 100, 100),
        ("check", 20, 20), ("check", 80, 20), ("check", 50, 80),
    ]):
        response = client.post("/api/review/features", json={"expected_revision": 0,
            "feature": {"type": "Feature", "id": f"gcp:{index}",
                "geometry": {"type": "Point", "coordinates": [112.7 + x * 0.0001, -7.45 + y * 0.0001]},
                "properties": {"_kind": "cad_map_gcp", "role": role, "cad_x": x, "cad_y": y}}})
        assert response.status_code == 200, response.text
    capture = client.get("/api/registration").json()
    export = client.post("/api/registration/export", json={"activate": True,
        "expected_controls_sha256": capture["controls_sha256"]})
    assert export.status_code == 200, export.text
    result = export.json()
    emitted = json.loads(Path(result["source_profile"]).read_text())
    assert emitted == source_profile
    assert result["coverage_policy"]["final_conversion_enforces_bound_coverage"] is True
    old_profile = Path(result["profile"]["path"]).read_bytes()
    assert client.post("/api/registration/export", json={"activate": True,
        "expected_controls_sha256": "stale"}).status_code == 409
    assert Path(result["profile"]["path"]).read_bytes() == old_profile
