"""Native boundary guards; actual AutoCAD qualification is a separate artifact."""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import subprocess
import sys

import ezdxf
import pytest

from cad2gis import reverse_dwg as dwg
from cad2gis.reverse_models import ReverseConversionError


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.dxf"
    document = ezdxf.new("R2018")
    document.modelspace().add_point((1, 2, 3))
    document.saveas(path)
    return path


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = tmp_path / "accoreconsole.exe"
    path.write_bytes(b"test-runtime-not-executable")
    monkeypatch.setattr(dwg, "resolve_runtime", lambda runtime=None: path)
    return path


def fake_native(monkeypatch, *, artifact=None, hook=None):
    def run(command, work, log, timeout):
        assert command[0].endswith("accoreconsole.exe")
        assert (work / "convert.scr").read_text(encoding="ascii").endswith("_.QUIT\n")
        assert Path(command[command.index("/i") + 1]).parent == work
        if "/readonly" in command:
            assert "\n_Binary\n" in (work / "convert.scr").read_text(encoding="ascii")
        if hook:
            hook(work)
        if artifact is not None:
            artifact(work)
        log.write_bytes(b"native boundary test")
        return {"exit_code": 0, "runtime_version": "test-only", "log_path": str(log)}
    monkeypatch.setattr(dwg, "_run_core", run)


def valid_signature(work):
    (work / "converted.dwg").write_bytes(b"AC1032" + b"\0" * 300)


def test_existing_output_preserved_before_any_runtime_lookup(source, tmp_path, monkeypatch):
    output = tmp_path / "existing.dwg"
    output.write_bytes(b"user drawing")
    monkeypatch.setattr(dwg, "resolve_runtime", lambda *_: pytest.fail("must not launch"))
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert failure.value.code == "REVERSE_OUTPUT_EXISTS"
    assert output.read_bytes() == b"user drawing"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, None, "abc"])
def test_invalid_timeout_fails_before_runtime_lookup(source, tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(dwg, "resolve_runtime", lambda *_: pytest.fail("must not launch"))
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg", timeout=timeout)
    assert failure.value.code == "REVERSE_INPUT_INVALID"


@pytest.mark.parametrize("unsafe", ['break\nquit.dwg', 'break\rquit.dwg', 'bad"name.dwg', 'bad\0name.dwg'])
def test_unsafe_path_characters_rejected(source, tmp_path, unsafe):
    with pytest.raises(ReverseConversionError, match="Unsafe character"):
        dwg.convert_dxf_to_dwg(source, tmp_path / unsafe)


def test_exit_zero_without_artifact_is_failure(source, tmp_path, runtime, monkeypatch):
    fake_native(monkeypatch)
    output = tmp_path / "output.dwg"
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"
    assert Path(failure.value.details["log_path"]).is_file()
    assert not output.exists()
    assert not list(tmp_path.glob(".cad2gis-dwg-*"))


@pytest.mark.parametrize("content", [b"", b"AC1032", b"AC1018" + b"x" * 300, b"not-a-drawing" * 100])
def test_wrong_or_truncated_dwg_not_published(source, tmp_path, runtime, monkeypatch, content):
    fake_native(monkeypatch, artifact=lambda work: (work / "converted.dwg").write_bytes(content))
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg")
    assert failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"
    assert not (tmp_path / "output.dwg").exists()


def test_destination_created_during_native_process_is_never_overwritten(source, tmp_path, runtime, monkeypatch):
    output = tmp_path / "raced.dwg"
    fake_native(monkeypatch, artifact=valid_signature,
                hook=lambda _: output.write_bytes(b"other writer owns this"))
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert failure.value.code == "REVERSE_OUTPUT_EXISTS"
    assert output.read_bytes() == b"other writer owns this"


def test_denied_exclusive_open_never_deletes_another_writers_file(source, tmp_path, runtime, monkeypatch):
    output = tmp_path / "denied.dwg"
    fake_native(monkeypatch, artifact=valid_signature,
                hook=lambda _: output.write_bytes(b"preserve despite denied open"))
    original_open = Path.open
    def guarded_open(path, mode="r", *args, **kwargs):
        if path == output and mode == "xb":
            raise PermissionError("another writer owns inaccessible destination")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert failure.value.code == "REVERSE_DWG_IO_ERROR"
    assert output.read_bytes() == b"preserve despite denied open"


def test_staged_input_open_failure_removes_only_our_new_partial_output(source, tmp_path, runtime, monkeypatch):
    output = tmp_path / "output.dwg"
    fake_native(monkeypatch, artifact=valid_signature)
    original_open = Path.open
    def fail_publication_read(path, mode="r", *args, **kwargs):
        if path.name == "converted.dwg" and mode == "rb" and output.exists():
            raise PermissionError("staged artifact became unreadable before publication")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_publication_read)
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert failure.value.code == "REVERSE_DWG_IO_ERROR"
    assert not output.exists()


@pytest.mark.parametrize("replacement", [b"path replaced after copy", b"AC1032" + b"\0" * 300])
def test_postcopy_replacement_never_becomes_verified_or_deleted(source, tmp_path, runtime, monkeypatch, replacement):
    output = tmp_path / "output.dwg"
    fake_native(monkeypatch, artifact=valid_signature)
    original_snapshot = dwg._snapshot
    replaced = False
    def race_snapshot(path, *, code):
        nonlocal replaced
        if path == output and not replaced:
            replaced = True
            foreign = tmp_path / "foreign.dwg"
            foreign.write_bytes(replacement)
            foreign.replace(output)
        return original_snapshot(path, code=code)
    monkeypatch.setattr(dwg, "_snapshot", race_snapshot)
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"
    assert failure.value.retained_artifact_path == output
    assert output.read_bytes() == replacement


def test_postcopy_inplace_change_never_becomes_verified_or_deleted(source, tmp_path, runtime, monkeypatch):
    output = tmp_path / "output.dwg"
    replacement = b"AC1032" + b"changed by another writer" * 20
    fake_native(monkeypatch, artifact=valid_signature)
    original_snapshot = dwg._snapshot
    changed = False
    def race_snapshot(path, *, code):
        nonlocal changed
        if path == output and not changed:
            changed = True
            output.write_bytes(replacement)
        return original_snapshot(path, code=code)
    monkeypatch.setattr(dwg, "_snapshot", race_snapshot)
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"
    assert output.read_bytes() == replacement


def test_invalid_output_cannot_borrow_hash_from_temporary_valid_replacement(source, tmp_path, runtime, monkeypatch):
    """Regression for the review's real swap-and-restore pathname probe."""
    output = tmp_path / "output.dwg"
    valid = b"AC1032" + b"\0" * 300
    invalid = b"BAD032" + b"\0" * 300
    fake_native(monkeypatch, artifact=valid_signature)
    original_snapshot, original_hash = dwg._snapshot, dwg._hash
    corrupted = False

    def before_snapshot(path, *, code):
        nonlocal corrupted
        if path == output and not corrupted:
            output.write_bytes(invalid)
            corrupted = True
        return original_snapshot(path, code=code)

    def borrow_valid_hash(path):
        if path == output:
            saved = tmp_path / "saved-invalid.dwg"
            output.replace(saved)
            output.write_bytes(valid)
            try:
                return original_hash(output)
            finally:
                output.unlink()
                saved.replace(output)
        return original_hash(path)

    monkeypatch.setattr(dwg, "_snapshot", before_snapshot)
    monkeypatch.setattr(dwg, "_hash", borrow_valid_hash)
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, output)
    assert corrupted and failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"
    assert output.read_bytes() == invalid


def test_dxf_parser_validates_same_bytes_as_bound_hash(source, monkeypatch):
    original = ezdxf.read
    before = source.read_bytes()

    def replace_during_parse(stream):
        # A parser that reopens the path could validate these substituted bytes
        # while attributing the audit to another file's hash.
        source.write_bytes(b"not the DXF represented by the captured buffer")
        return original(stream)

    monkeypatch.setattr(ezdxf, "read", replace_during_parse)
    result = dwg._validate_dxf(source, output=False)
    assert result["_content_snapshot"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert result["_content_snapshot"]["sha256"] != hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["audit_errors"] == result["audit_fixes"] == 0


def test_source_changes_during_staging_rejected_even_if_copy_matches_new_source(source, tmp_path, runtime, monkeypatch):
    original_copy = dwg.shutil.copyfile
    def changed_copy(src, dest):
        # A source hash checked only after staging would miss this change.
        source.write_bytes(source.read_bytes() + b"\n")
        return original_copy(src, dest)
    monkeypatch.setattr(dwg.shutil, "copyfile", changed_copy)
    monkeypatch.setattr(dwg, "_run_core", lambda *args: pytest.fail("changed source must not reach native runtime"))
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg")
    assert failure.value.code == "REVERSE_INPUT_INVALID"
    assert not (tmp_path / "output.dwg").exists()


def test_source_changes_during_native_execution_rejected_before_publication(source, tmp_path, runtime, monkeypatch):
    fake_native(monkeypatch, artifact=valid_signature,
                hook=lambda _: source.write_bytes(source.read_bytes() + b"\n"))
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg")
    assert failure.value.code == "REVERSE_INPUT_INVALID"
    assert not (tmp_path / "output.dwg").exists()


def test_input_staged_and_hashes_describe_artifact_without_fidelity_claim(source, tmp_path, runtime, monkeypatch):
    source_bytes = source.read_bytes()
    fake_native(monkeypatch, artifact=valid_signature,
                hook=lambda work: (work / "source.dxf").write_bytes(b"native modified its own copy"))
    report = dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg")
    assert source.read_bytes() == source_bytes
    assert report["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert report["runtime_sha256"] == hashlib.sha256(runtime.read_bytes()).hexdigest()
    assert report["output_sha256"] == hashlib.sha256((tmp_path / "output.dwg").read_bytes()).hexdigest()
    assert report["output_sha256"] == report["validated_output_sha256"]
    assert report["output_bytes"] == report["validated_output_bytes"]
    assert report["geometry_fidelity_verified"] is False
    assert report["signature_verified"] is True
    assert Path(report["log_path"]).is_file()
    assert not list(tmp_path.glob(".cad2gis-dwg-*"))


def test_native_readback_requires_dxf_parse(source, tmp_path, runtime, monkeypatch):
    source_dwg = tmp_path / "source.dwg"
    source_dwg.write_bytes(b"AC1032" + b"x" * 300)
    fake_native(monkeypatch, artifact=lambda work: (work / "converted.dxf").write_text("not DXF"))
    with pytest.raises(ReverseConversionError) as failure:
        dwg.read_dwg_as_dxf(source_dwg, tmp_path / "readback.dxf")
    assert failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"
    assert not (tmp_path / "readback.dxf").exists()


def test_native_readback_rejects_dxf_requiring_repairs(tmp_path, runtime, monkeypatch):
    source_dwg = tmp_path / "source.dwg"
    source_dwg.write_bytes(b"AC1032" + b"x" * 300)
    def broken_dxf(work):
        document = ezdxf.new("R2018")
        document.modelspace().add_line((0, 0), (1, 2), dxfattribs={"linetype": "UNDEFINED"})
        document.saveas(work / "converted.dxf", fmt="bin")
    fake_native(monkeypatch, artifact=broken_dxf)
    with pytest.raises(ReverseConversionError) as failure:
        dwg.read_dwg_as_dxf(source_dwg, tmp_path / "readback.dxf")
    assert failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"
    assert failure.value.details["audit_fixes"] > 0


def test_native_readback_clean_dxf_publishes_with_audit(tmp_path, runtime, monkeypatch):
    source_dwg = tmp_path / "source.dwg"
    source_dwg.write_bytes(b"AC1032" + b"x" * 300)
    def clean_dxf(work):
        document = ezdxf.new("R2018")
        document.modelspace().add_point((2, 4, 6))
        document.saveas(work / "converted.dxf", fmt="bin")
    fake_native(monkeypatch, artifact=clean_dxf)
    report = dwg.read_dwg_as_dxf(source_dwg, tmp_path / "readback.dxf")
    assert report["entity_count"] == 1
    assert report["audit_errors"] == report["audit_fixes"] == 0
    assert report["dxf_encoding"] == "binary"
    assert report["geometry_fidelity_verified"] is False


def test_native_ascii_readback_rejected_instead_of_silent_precision_loss(tmp_path, runtime, monkeypatch):
    source_dwg = tmp_path / "source.dwg"
    source_dwg.write_bytes(b"AC1032" + b"x" * 300)
    def ascii_dxf(work):
        document = ezdxf.new("R2018")
        document.modelspace().add_point((13902051.6043152, 0))
        document.saveas(work / "converted.dxf")
    fake_native(monkeypatch, artifact=ascii_dxf)
    with pytest.raises(ReverseConversionError, match="binary DXF") as failure:
        dwg.read_dwg_as_dxf(source_dwg, tmp_path / "readback.dxf")
    assert failure.value.code == "REVERSE_DWG_INVALID_OUTPUT"


def test_process_failure_preserves_real_exit_and_log(tmp_path):
    log = tmp_path / "process.log"
    with pytest.raises(ReverseConversionError) as failure:
        dwg._run_core([sys.executable, "-c", "print('native error'); raise SystemExit(9)"],
                      tmp_path, log, 10)
    assert failure.value.code == "REVERSE_DWG_PROCESS_FAILED"
    assert failure.value.details["exit_code"] == 9
    assert b"native error" in log.read_bytes()


def test_process_timeout_stops_only_owned_real_process(tmp_path, monkeypatch):
    created = []
    original = subprocess.Popen
    def capture(*args, **kwargs):
        child = original(*args, **kwargs)
        created.append(child)
        return child
    monkeypatch.setattr(dwg.subprocess, "Popen", capture)
    with pytest.raises(ReverseConversionError) as failure:
        dwg._run_core([sys.executable, "-c", "import time; time.sleep(30)"],
                      tmp_path, tmp_path / "timeout.log", 0.1)
    assert failure.value.code == "REVERSE_DWG_TIMEOUT"
    assert failure.value.details["process_may_be_running"] is False
    assert len(created) == 1 and created[0].poll() is not None


def test_wait_interruption_stops_owned_live_child_before_workspace_cleanup(source, tmp_path, runtime, monkeypatch):
    created = []
    original = subprocess.Popen
    def interrupted_native(_command, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        created.append(child)
        original_wait = child.wait
        first_wait = True
        def interrupted_wait(*args, **kwargs):
            nonlocal first_wait
            if first_wait:
                first_wait = False
                raise KeyboardInterrupt()
            return original_wait(*args, **kwargs)
        child.wait = interrupted_wait
        return child
    monkeypatch.setattr(dwg.subprocess, "Popen", interrupted_native)
    with pytest.raises(KeyboardInterrupt):
        dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg")
    assert len(created) == 1 and created[0].poll() is not None
    assert not (tmp_path / "output.dwg").exists()
    assert not list(tmp_path.glob(".cad2gis-dwg-*"))


def test_unconfirmed_interrupted_child_retains_workspace(source, tmp_path, runtime, monkeypatch):
    class UnstoppableChild:
        pid = 12345
        def poll(self):
            return None
        def wait(self, timeout=None):
            raise KeyboardInterrupt()
        def kill(self):
            raise PermissionError("cannot confirm owned process termination")
    monkeypatch.setattr(dwg.subprocess, "Popen", lambda *args, **kwargs: UnstoppableChild())
    with pytest.raises(ReverseConversionError) as failure:
        dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg")
    assert failure.value.code == "REVERSE_DWG_PROCESS_INTERRUPTED"
    assert failure.value.details["process_may_be_running"] is True
    retained = Path(failure.value.details["workspace"])
    assert retained.is_dir() and (retained / "source.dxf").is_file()
    assert not (tmp_path / "output.dwg").exists()


def test_launch_access_denied_has_explicit_failure_not_fallback(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("blocked by runtime policy")
    monkeypatch.setattr(dwg.subprocess, "Popen", denied)
    with pytest.raises(ReverseConversionError) as failure:
        dwg._run_core(["fake.exe"], tmp_path, tmp_path / "denied.log", 1)
    assert failure.value.code == "REVERSE_DWG_PROCESS_FAILED"
    assert "blocked by runtime policy" in str(failure.value)


def test_cleanup_handles_read_only_generated_files(tmp_path):
    work = tmp_path / "generated"
    work.mkdir()
    file = work / "readonly.tmp"
    file.write_text("generated")
    file.chmod(stat.S_IREAD)
    assert dwg._cleanup(work) is None
    assert not work.exists()


def test_cleanup_denial_is_reported_without_masking_result(source, tmp_path, runtime, monkeypatch):
    fake_native(monkeypatch, artifact=valid_signature)
    monkeypatch.setattr(dwg, "_cleanup", lambda work: f"ACL denies cleanup: {work}")
    report = dwg.convert_dxf_to_dwg(source, tmp_path / "output.dwg")
    assert "ACL denies cleanup" in report["cleanup_warning"]
    assert (tmp_path / "output.dwg").exists()


def test_readback_workspace_cleans_success(tmp_path, monkeypatch):
    monkeypatch.setattr(dwg.tempfile, "tempdir", str(tmp_path))
    with dwg.native_readback_workspace() as work:
        assert work.parent == tmp_path
        (work / "fresh.dxf").write_text("generated")
    assert not work.exists()


def test_readback_workspace_uses_explicit_existing_parent(tmp_path):
    admitted = tmp_path / "admitted"
    admitted.mkdir()
    with dwg.native_readback_workspace(parent=admitted) as work:
        assert work.parent == admitted
    assert not work.exists()


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_readback_workspace_rejects_non_directory_parent(tmp_path, kind):
    parent = tmp_path / kind
    if kind == "file":
        parent.write_text("not a directory")
    with pytest.raises(ReverseConversionError) as failure:
        with dwg.native_readback_workspace(parent=parent):
            pytest.fail("invalid parent must not create workspace")
    assert failure.value.code == "REVERSE_INPUT_INVALID"
    assert not list(tmp_path.glob("cad2gis-dwg-verify-*"))


def test_readback_workspace_retains_typed_native_failure_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(dwg.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(ReverseConversionError) as failure:
        with dwg.native_readback_workspace() as work:
            (work / "native.log").write_text("failure evidence")
            raise ReverseConversionError("native process failed", code="REVERSE_DWG_PROCESS_FAILED")
    assert work.is_dir() and (work / "native.log").is_file()
    assert failure.value.retained_artifact_path == work
    assert failure.value.details["unverified_workspace"] == str(work)


def test_readback_workspace_retains_unconfirmed_child_even_for_other_error(tmp_path, monkeypatch):
    monkeypatch.setattr(dwg.tempfile, "tempdir", str(tmp_path))
    error = dwg._fail("unconfirmed child", "REVERSE_VERIFICATION_FAILED", process_may_be_running=True)
    with pytest.raises(ReverseConversionError):
        with dwg.native_readback_workspace() as work:
            raise error
    assert work.is_dir() and error.retained_artifact_path == work


def test_readback_workspace_cleans_other_failure_without_masking(tmp_path, monkeypatch):
    monkeypatch.setattr(dwg.tempfile, "tempdir", str(tmp_path))
    error = ReverseConversionError("geometry mismatch", code="REVERSE_VERIFICATION_FAILED")
    with pytest.raises(ReverseConversionError) as failure:
        with dwg.native_readback_workspace() as work:
            raise error
    assert failure.value is error
    assert not work.exists()


def test_readback_workspace_cleanup_failure_never_masks_pending_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(dwg.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(dwg, "_cleanup", lambda work: "ACL denied generated workspace cleanup")
    error = ReverseConversionError("geometry mismatch", code="REVERSE_VERIFICATION_FAILED")
    with pytest.raises(ReverseConversionError) as failure:
        with dwg.native_readback_workspace() as work:
            raise error
    assert failure.value is error
    assert error.retained_artifact_path == work
    assert "ACL denied" in error.details["cleanup_warning"]
