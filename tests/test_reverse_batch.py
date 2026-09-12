"""Real GIS/DXF batch execution, all-input preflight, and partial-failure evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import threading

import ezdxf
import pytest

from cad2gis import reverse, reverse_batch, reverse_verify, reverse_writer
from cad2gis.reverse_models import ReverseConversionError


def source_at(tmp_path: Path, name: str = "source") -> Path:
    source = tmp_path / f"{name}.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "name": name,
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [{"type": "Feature", "id": name, "properties": {"label": f"点 {name}"},
                      "geometry": {"type": "Point", "coordinates": [100, 200, 3]}}]}), encoding="utf-8")
    return source


def parser():
    result = argparse.ArgumentParser()
    reverse_batch.register(result.add_subparsers(dest="command", required=True))
    return result


def test_real_geojson_and_geopackage_run_concurrently_with_canonical_verification(tmp_path, monkeypatch):
    from test_reverse_reader import _gpkg

    sources = [source_at(tmp_path), _gpkg(tmp_path)]
    originals = [source.read_bytes() for source in sources]
    plan = reverse_batch.prepare_batch(sources, options={"label_field": "label"})
    assert plan["schema"] == "cad2gis.reverse-batch.v1"
    assert len(list(tmp_path.iterdir())) == 2
    assert [job["source_sha256"] for job in plan["jobs"]] == [hashlib.sha256(value).hexdigest() for value in originals]
    assert all(job["options"]["registration_manifest"] is None for job in plan["jobs"])
    barrier = threading.Barrier(2)
    entered = []
    real_convert = reverse.reverse_convert

    def concurrently(*args, **kwargs):
        entered.append(threading.get_ident())
        barrier.wait(timeout=15)
        return real_convert(*args, **kwargs)

    monkeypatch.setattr(reverse, "reverse_convert", concurrently)
    output = tmp_path / "batch"
    report = reverse_batch.run_batch(plan, run_dir=output, workers=2)
    assert len(set(entered)) == 2
    assert report["status"] == "complete"
    assert report["verified_count"] == 2
    assert report["failed_count"] == 0
    assert report["job_count"] == 2
    assert [job["job_id"] for job in report["jobs"]] == [job["job_id"] for job in plan["jobs"]]
    assert json.loads((output / "batch_plan.json").read_text(encoding="utf-8")) == plan
    assert json.loads((output / "batch_report.json").read_text(encoding="utf-8")) == report
    assert report["plan_sha256"] == hashlib.sha256((output / "batch_plan.json").read_bytes()).hexdigest()
    for job in report["jobs"]:
        assert job["status"] == "verified"
        assert job["error"] is None
        assert job["verification"]["status"] == "verified"
        drawing = Path(job["run_dir"]) / "drawing.dxf"
        assert not ezdxf.readfile(drawing).audit().has_errors
        assert set(Path(path).name for path in job["retained_artifacts"]) == {"drawing.dxf", "attributes.jsonl", "manifest.json"}
    assert [source.read_bytes() for source in sources] == originals


def test_all_jobs_preflight_before_creating_even_output_parent(tmp_path):
    sources = [source_at(tmp_path, "one"), source_at(tmp_path, "two")]
    plan = reverse_batch.prepare_batch(sources)
    sources[1].write_text(sources[1].read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ReverseConversionError) as caught:
        reverse_batch.run_batch(plan, run_dir=tmp_path / "new-parent" / "run")
    assert caught.value.code == "REVERSE_SOURCE_CHANGED"
    assert not (tmp_path / "new-parent").exists()


@pytest.mark.parametrize("identifier", ["../escape", "/absolute", "a/b", "a\\b", ".", "..", "CON", "LPT1", "a.", "a b", "x" * 65, ""])
def test_unsafe_job_identifiers_never_create_outputs(tmp_path, identifier):
    plan = reverse_batch.prepare_batch([source_at(tmp_path)])
    plan["jobs"][0]["job_id"] = identifier
    with pytest.raises(ReverseConversionError, match="job_id"):
        reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert not (tmp_path / "batch").exists()


@pytest.mark.parametrize("options", [
    {"run_dir": "escape"}, {"label_height": 0}, {"label_height": True}, {"label_height": float("nan")},
    {"label_height": float("inf")}, {"layers": "roads"}, {"layers": []}, {"layers": ["roads", "roads"]},
    {"layers": [False]}, {"label_field": []}, {"source_crs": True}, {"target_crs": " "},
    {"target_crs": "EPSG:4326"}, {"target_crs": "EPSG:999999"}, {"partition": "main"},
    {"registration_manifest": "relative.json", "target_crs": "EPSG:3857"},
])
def test_invalid_options_are_rejected_before_output_creation(tmp_path, options):
    plan = reverse_batch.prepare_batch([source_at(tmp_path)])
    plan["jobs"][0]["options"].update(options)
    with pytest.raises(ReverseConversionError):
        reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert not (tmp_path / "batch").exists()


@pytest.mark.parametrize("workers", [0, -1, 9, 1.5, True, "2", None])
def test_worker_bound_is_strict(tmp_path, workers):
    with pytest.raises(ReverseConversionError, match="workers"):
        reverse_batch.run_batch({}, run_dir=tmp_path / "batch", workers=workers)
    assert not (tmp_path / "batch").exists()


@pytest.mark.parametrize("change", ["duplicate_id", "duplicate_source", "relative_source", "missing_source", "bad_digest", "schema", "unknown_job_option"])
def test_plan_errors_fail_before_any_artifacts(tmp_path, change):
    plan = reverse_batch.prepare_batch([source_at(tmp_path, "one"), source_at(tmp_path, "two")])
    if change == "duplicate_id":
        plan["jobs"][1]["job_id"] = plan["jobs"][0]["job_id"].upper()
    elif change == "duplicate_source":
        plan["jobs"][1]["source"] = plan["jobs"][0]["source"]
    elif change == "relative_source":
        plan["jobs"][1]["source"] = "two.geojson"
    elif change == "missing_source":
        Path(plan["jobs"][1]["source"]).unlink()
    elif change == "bad_digest":
        plan["jobs"][1]["source_sha256"] = "unknown"
    elif change == "schema":
        plan["schema"] = "cad2gis.reverse-batch.v99"
    else:
        plan["jobs"][1]["output"] = "elsewhere"
    with pytest.raises(ReverseConversionError):
        reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert not (tmp_path / "batch").exists()


def test_real_partial_write_failure_preserves_verified_sibling_and_explicit_errors(tmp_path, monkeypatch):
    plan = reverse_batch.prepare_batch([source_at(tmp_path, "good"), source_at(tmp_path, "bad")])
    real_write = reverse_writer.write_dxf

    def fail_one(features, destination, **kwargs):
        if features[0].feature_id == "bad":
            destination.write_bytes(b"unfinished DXF")
            raise OSError("simulated writer failure")
        return real_write(features, destination, **kwargs)

    monkeypatch.setattr(reverse_writer, "write_dxf", fail_one)
    report = reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert report["status"] == "partial_failed"
    assert (report["verified_count"], report["failed_count"]) == (1, 1)
    good, bad = report["jobs"]
    assert good["verification"]["status"] == "verified"
    assert Path(good["run_dir"], "drawing.dxf").is_file()
    assert bad["error"]["message"] == "simulated writer failure"
    assert bad["output"] is None
    assert bad["retained_artifacts"] == []
    assert not Path(bad["run_dir"]).exists()
    assert not list((tmp_path / "batch").glob(".*.reverse-*"))


def test_post_publication_verifier_failure_retains_artifacts_without_success_claim(tmp_path, monkeypatch):
    plan = reverse_batch.prepare_batch([source_at(tmp_path)])

    def reject(*args, **kwargs):
        raise ReverseConversionError("geometry comparison failed", code="REVERSE_VERIFICATION_FAILED")

    monkeypatch.setattr(reverse_verify, "verify_reverse", reject)
    report = reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert report["status"] == "failed"
    job = report["jobs"][0]
    assert job["status"] == "failed"
    assert job["output"]["status"] == "converted"
    assert job["verification"] is None
    assert job["error"]["code"] == "REVERSE_VERIFICATION_FAILED"
    assert len(job["retained_artifacts"]) == 3


def test_execution_rechecks_input_after_global_preflight(tmp_path, monkeypatch):
    source = source_at(tmp_path)
    plan = reverse_batch.prepare_batch([source])
    real_execute = reverse_batch._execute_job

    def change_before_worker(job, output):
        source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return real_execute(job, output)

    monkeypatch.setattr(reverse_batch, "_execute_job", change_before_worker)
    report = reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert report["status"] == "failed"
    assert report["jobs"][0]["error"]["code"] == "REVERSE_SOURCE_CHANGED"
    assert report["jobs"][0]["retained_artifacts"] == []


def test_change_after_conversion_is_failed_and_retained(tmp_path, monkeypatch):
    source = source_at(tmp_path)
    plan = reverse_batch.prepare_batch([source])
    real_convert = reverse.reverse_convert

    def convert_then_change(*args, **kwargs):
        converted = real_convert(*args, **kwargs)
        source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return converted

    monkeypatch.setattr(reverse, "reverse_convert", convert_then_change)
    report = reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert report["status"] == "failed"
    assert report["jobs"][0]["error"]["code"] == "REVERSE_SOURCE_CHANGED"
    assert len(report["jobs"][0]["retained_artifacts"]) == 3


def test_existing_output_is_preserved(tmp_path):
    output = tmp_path / "batch"
    output.mkdir()
    sentinel = output / "batch_report.json"
    sentinel.write_text("preserve existing report", encoding="utf-8")
    with pytest.raises(ReverseConversionError) as caught:
        reverse_batch.run_batch({}, run_dir=output)
    assert caught.value.code == "REVERSE_OUTPUT_EXISTS"
    assert sentinel.read_text() == "preserve existing report"


def test_registration_manifest_hash_is_explicit_and_rechecked(tmp_path):
    from test_reverse_registration import direct_manifest

    source = source_at(tmp_path)
    manifest = {**direct_manifest(), "schema_version": "cad2gis-run-manifest-v4",
        "pipeline": "cad2gis-reviewed-project-evidence-first-v4",
        "source": {"path": "original.dwg", "sha256": "a" * 64},
        "artifacts": {"delivery": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}}
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = reverse_batch.prepare_batch([source], options={"registration_manifest": path})
    assert plan["jobs"][0]["registration_manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert plan["jobs"][0]["options"]["partition"] is None
    report = reverse_batch.run_batch(plan, run_dir=tmp_path / "native")
    assert report["status"] == "complete"
    assert report["jobs"][0]["output"]["coordinate_mode"] == "native_registered"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ReverseConversionError) as caught:
        reverse_batch.run_batch(plan, run_dir=tmp_path / "changed")
    assert caught.value.code == "REVERSE_SOURCE_CHANGED"
    assert not (tmp_path / "changed").exists()


def test_cli_prepare_run_and_existing_plan_protection(tmp_path):
    source = source_at(tmp_path)
    plan_path = tmp_path / "plan.json"
    command = parser().parse_args(["reverse-batch", "prepare", str(source), "--plan", str(plan_path), "--json"])
    prepared, code = command.handler(command)
    assert code == 0
    assert prepared["status"] == "prepared"
    assert prepared["job_count"] == 1
    before = plan_path.read_bytes()
    with pytest.raises(ReverseConversionError) as caught:
        command.handler(command)
    assert caught.value.code == "REVERSE_OUTPUT_EXISTS"
    assert plan_path.read_bytes() == before
    run = parser().parse_args(["reverse-batch", "run", "--plan", str(plan_path), "--run-dir", str(tmp_path / "batch"), "--workers", "1", "--json"])
    report, code = run.handler(run)
    assert code == 0
    assert report["status"] == "complete"


def test_cli_failed_verification_returns_failure_exit_status(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(reverse_batch.prepare_batch([source_at(tmp_path)])), encoding="utf-8")
    monkeypatch.setattr(reverse_verify, "verify_reverse", lambda *args, **kwargs: {"status": "unknown"})
    command = parser().parse_args(["reverse-batch", "run", "--plan", str(plan_path), "--run-dir", str(tmp_path / "batch")])
    report, code = command.handler(command)
    assert report["status"] == "failed"
    assert code == 2


def test_duplicate_json_members_rejected_before_output(tmp_path):
    source = source_at(tmp_path)
    plan = reverse_batch.prepare_batch([source])
    plan_path = tmp_path / "ambiguous.json"
    encoded = json.dumps(plan)
    plan_path.write_text(encoded[:-1] + ', "jobs": []}', encoding="utf-8")
    with pytest.raises(ReverseConversionError, match="Duplicate"):
        reverse_batch.run_batch(plan_path, run_dir=tmp_path / "batch")
    assert not (tmp_path / "batch").exists()


def test_prepare_does_not_alias_caller_options(tmp_path):
    options = {"layers": ["source"]}
    plan = reverse_batch.prepare_batch([source_at(tmp_path)], options=options)
    snapshot = copy.deepcopy(plan)
    options["layers"].append("modified")
    assert plan == snapshot


@pytest.mark.parametrize("option", ["label_field", "layer_field"])
def test_selected_fields_are_validated_across_all_jobs_before_output(tmp_path, option):
    plan = reverse_batch.prepare_batch([source_at(tmp_path, "one"), source_at(tmp_path, "two")])
    plan["jobs"][1]["options"][option] = "missing"
    with pytest.raises(ReverseConversionError, match="field"):
        reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert not (tmp_path / "batch").exists()
