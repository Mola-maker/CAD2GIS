"""Bounded native DWG serialization with explicit, inspectable runtime evidence.

AutoCAD Core Console is optional and runs only when these APIs are called.
Successful serialization is not a geometry-fidelity claim: callers must compare
the native DXF readback with the GIS-to-DXF source manifest.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import io
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from typing import Any

from cad2gis.reverse_models import ReverseConversionError

BACKEND = "autocad_core_console"
DWG_SIGNATURE = b"AC1032"
BINARY_DXF_SIGNATURE = b"AutoCAD Binary DXF\r\n\x1a\x00"
_VERSION = re.compile(r"Version Number:\s*([^\r\n]+)")


def _fail(message: str, code: str, **details: Any) -> ReverseConversionError:
    error = ReverseConversionError(message, code=code)
    error.details = details
    return error


def _safe_path(value: Path | str, name: str) -> Path:
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as exc:
        raise _fail(f"Invalid {name} path", "REVERSE_INPUT_INVALID") from exc
    if any(character in str(path) for character in ('\r', '\n', '\x00', '"')):
        raise _fail(f"Unsafe character in {name} path", "REVERSE_INPUT_INVALID")
    # Keep the final component lexical so an existing dangling output symlink
    # cannot be resolved away before the no-overwrite check.
    return Path(os.path.abspath(path))


def resolve_runtime(runtime: Path | None = None) -> Path:
    """Resolve an explicit/environment/discovered Core Console; never install it."""
    from cad2gis.reader.autocad import _configured_accoreconsole_path

    if os.name != "nt":
        raise _fail("DWG export requires Windows AutoCAD Core Console",
                    "REVERSE_DWG_RUNTIME_UNAVAILABLE")
    try:
        candidate = _safe_path(runtime, "runtime") if runtime is not None else None
        resolved, _ = _configured_accoreconsole_path(candidate)
        return _safe_path(resolved, "runtime")
    except ReverseConversionError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise _fail(f"AutoCAD Core Console is unavailable: {exc}",
                    "REVERSE_DWG_RUNTIME_UNAVAILABLE") from exc


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bound_read(path: Path, *, code: str, keep_bytes: bool = False) -> tuple[dict[str, Any], bytes | None]:
    """Hash and identify one open file, never a sequence of pathname lookups."""
    def metadata(value: os.stat_result) -> tuple[int, ...]:
        return (value.st_dev, value.st_ino, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)
    chunks = [] if keep_bytes else None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            if chunks is not None:
                chunks.append(block)
        after = os.fstat(stream.fileno())
        # The pathname must still name that descriptor's file. Checking it
        # alone before/after a separate open permits a swap-and-restore race.
        named = path.stat()
    # Windows/Python 3.12 can expose different ctime meanings through stat and
    # fstat. Compare ctime only within the same descriptor API; identity, size
    # and mtime must also agree with the current pathname.
    if metadata(before) != metadata(after) or metadata(named)[:4] != metadata(after)[:4]:
        raise _fail(f"File changed while reading native conversion evidence: {path}", code,
                    changed_path=str(path))
    return {"sha256": digest.hexdigest(), "bytes": after.st_size,
            "identity": (after.st_dev, after.st_ino),
            "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns}, b"".join(chunks) if chunks is not None else None


def _snapshot(path: Path, *, code: str) -> dict[str, Any]:
    return _bound_read(path, code=code)[0]


def _decode_log(raw: bytes) -> str:
    return raw.decode("utf-16-le" if b"\x00" in raw[:100] else "utf-8", errors="replace")


def _validate_dxf(path: Path, *, output: bool) -> dict[str, Any]:
    import ezdxf

    code = "REVERSE_DWG_INVALID_OUTPUT" if output else "REVERSE_INPUT_INVALID"
    try:
        snapshot, data = _bound_read(path, code=code, keep_bytes=True)
        binary = data.startswith(BINARY_DXF_SIGNATURE)
        if output and not binary:
            raise _fail("Expected native binary DXF readback to preserve floating-point precision", code)
        if binary:
            from ezdxf.document import Drawing
            from ezdxf.lldxf.tagger import binary_tags_loader

            document = Drawing.load(binary_tags_loader(data, errors="surrogateescape"))
        else:
            from ezdxf.filemanagement import dxf_stream_info

            info = dxf_stream_info(io.StringIO(data.decode("utf-8", errors="surrogateescape")))
            document = ezdxf.read(io.StringIO(data.decode(info.encoding, errors="surrogateescape")))
        entity_count = len(document.modelspace())
        audit = document.audit()
    except ReverseConversionError:
        raise
    except Exception as exc:
        raise _fail(f"DXF cannot be reopened: {type(exc).__name__}: {exc}", code) from exc
    if audit.errors or audit.fixes:
        raise _fail(
            f"DXF structural audit is not clean: {len(audit.errors)} errors, "
            f"{len(audit.fixes)} repairs required", code,
            audit_errors=len(audit.errors), audit_fixes=len(audit.fixes),
        )
    return {"dxf_version": document.dxfversion, "dxf_encoding": "binary" if binary else "ascii",
            "entity_count": entity_count,
            "audit_errors": 0, "audit_fixes": 0, "_content_snapshot": snapshot}


def _validate_dwg(path: Path, *, output: bool) -> dict[str, Any]:
    code = "REVERSE_DWG_INVALID_OUTPUT" if output else "REVERSE_INPUT_INVALID"
    try:
        snapshot, data = _bound_read(path, code=code, keep_bytes=True)
        signature = data[:6]
        size = len(data)
    except OSError as exc:
        raise _fail(f"DWG is missing or unreadable: {path}", code) from exc
    if signature != DWG_SIGNATURE or size < 256:
        raise _fail("Expected a non-truncated AutoCAD 2018 (AC1032) DWG", code)
    return {"dwg_version": "AC1032", "signature_verified": True,
            "geometry_fidelity_verified": False, "_content_snapshot": snapshot}


def _cleanup(work: Path) -> str | None:
    """Remove only our generated workspace; repair read-only file attributes.

    ACL-denied cleanup is reported with its retained path, never hidden or fixed
    by changing user security policy.
    """
    def writable_retry(function: Any, path: str, _error: Any) -> None:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(path)

    try:
        shutil.rmtree(work, onerror=writable_retry)
    except OSError as exc:
        return f"Temporary workspace retained at {work}: {type(exc).__name__}: {exc}"
    return None


@contextmanager
def native_readback_workspace(*, prefix: str = "cad2gis-dwg-verify-",
                              parent: Path | None = None) -> Iterator[Path]:
    """Keep native failure evidence and any unconfirmed live child's workspace."""
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", prefix):
        raise _fail("Invalid native readback workspace prefix", "REVERSE_INPUT_INVALID")
    parent_path = _safe_path(parent, "native workspace parent") if parent is not None else None
    if parent_path is not None and not parent_path.is_dir():
        raise _fail("Native readback workspace parent must be an existing directory",
                    "REVERSE_INPUT_INVALID")
    try:
        work = Path(tempfile.mkdtemp(prefix=prefix, dir=parent_path)).resolve()
    except OSError as exc:
        raise _fail(f"Cannot create native readback workspace: {exc}", "REVERSE_DWG_IO_ERROR") from exc
    pending = None
    retained = False
    try:
        yield work
    except BaseException as exc:
        pending = exc
        details = getattr(exc, "details", {})
        code = getattr(exc, "code", "")
        retained = (bool(isinstance(code, str) and code.startswith("REVERSE_DWG_"))
                    or bool(isinstance(details, dict) and details.get("process_may_be_running")))
        if retained:
            exc.retained_artifact_path = work
            if not isinstance(details, dict):
                details = {}
            exc.details = {**details, "unverified_workspace": str(work)}
        raise
    finally:
        if not retained:
            warning = _cleanup(work)
            if warning:
                if pending is not None:
                    # Cleanup must not replace the failure that explains why
                    # verification failed, including KeyboardInterrupt.
                    pending.add_note(warning)
                    pending.retained_artifact_path = work
                    if isinstance(pending, ReverseConversionError):
                        details = getattr(pending, "details", {})
                        pending.details = {**(details if isinstance(details, dict) else {}),
                                           "cleanup_warning": warning,
                                           "unverified_workspace": str(work)}
                else:
                    error = _fail(warning, "REVERSE_DWG_IO_ERROR", cleanup_warning=warning,
                                  unverified_workspace=str(work))
                    error.retained_artifact_path = work
                    raise error


def _stop_owned_process(process: subprocess.Popen) -> BaseException | None:
    """Confirm termination of one owned child even if cancellation races exit."""
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
    except BaseException as exc:
        # An exit racing kill is still confirmed by this same Popen handle.
        try:
            if process.poll() is not None:
                return None
        except BaseException:
            pass
        return exc
    return None


def _run_core(command: list[str], work: Path, log: Path, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    with log.open("xb") as stream:
        try:
            process = subprocess.Popen(
                command, cwd=work, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise _fail(f"AutoCAD Core Console could not start: {exc}; log: {log}",
                        "REVERSE_DWG_PROCESS_FAILED", log_path=str(log)) from exc
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stop_error = _stop_owned_process(process)
            if stop_error is not None:
                raise _fail(
                    f"AutoCAD exceeded {timeout:g}s; owned process {process.pid} "
                    f"could not be confirmed stopped: {stop_error}; log: {log}",
                    "REVERSE_DWG_TIMEOUT", pid=process.pid, process_may_be_running=True,
                    log_path=str(log), workspace=str(work),
                ) from exc
            raise _fail(f"AutoCAD exceeded {timeout:g}s and was stopped; log: {log}",
                        "REVERSE_DWG_TIMEOUT", pid=process.pid, process_may_be_running=False,
                        log_path=str(log), returncode=process.returncode) from exc
        except BaseException as exc:
            stop_error = _stop_owned_process(process)
            if stop_error is not None:
                raise _fail(
                    f"AutoCAD was interrupted; owned process {process.pid} could not "
                    f"be confirmed stopped: {type(stop_error).__name__}: {stop_error}; log: {log}",
                    "REVERSE_DWG_PROCESS_INTERRUPTED", pid=process.pid, process_may_be_running=True,
                    log_path=str(log), workspace=str(work), interruption=type(exc).__name__,
                ) from exc
            # Preserve KeyboardInterrupt/SystemExit semantics only after the
            # child is stopped; the caller can now safely clean its workspace.
            raise
    info = {"pid": process.pid, "exit_code": returncode,
            "elapsed_seconds": time.monotonic() - started,
            "timeout_seconds": timeout, "log_path": str(log), "log_sha256": _hash(log)}
    log_text = _decode_log(log.read_bytes())
    match = _VERSION.search(log_text)
    info["runtime_version"] = match.group(1).strip() if match else None
    if returncode != 0:
        raise _fail(f"AutoCAD exited {returncode}; log: {log}. "
                    "A sandbox/profile restriction must be resolved explicitly; no fallback was used.",
                    "REVERSE_DWG_PROCESS_FAILED", **info)
    return info


def _convert(source: Path, output: Path, *, runtime: Path | None,
             timeout: float, write_dwg: bool) -> dict[str, Any]:
    source = _safe_path(source, "source")
    output = _safe_path(output, "output")
    input_extension, output_extension = (".dxf", ".dwg") if write_dwg else (".dwg", ".dxf")
    if source.suffix.lower() != input_extension or not source.is_file():
        raise _fail(f"Source must be an existing {input_extension} file", "REVERSE_INPUT_INVALID")
    if output.suffix.lower() != output_extension or source == output:
        raise _fail(f"Output must be a distinct {output_extension} file", "REVERSE_INPUT_INVALID")
    if os.path.lexists(output):
        raise _fail(f"Output already exists: {output}", "REVERSE_OUTPUT_EXISTS")
    if isinstance(timeout, bool):
        raise _fail("Timeout must be a positive finite number", "REVERSE_INPUT_INVALID")
    try:
        seconds = float(timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _fail("Timeout must be a positive finite number", "REVERSE_INPUT_INVALID") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise _fail("Timeout must be a positive finite number", "REVERSE_INPUT_INVALID")
    executable = resolve_runtime(runtime)
    report: dict[str, Any] = {}
    work = None
    pending_error = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".cad2gis-dwg-", dir=output.parent)).resolve()
        staged_source = work / ("source" + input_extension)
        staged_output = work / ("converted" + output_extension)
        source_snapshot = _snapshot(source, code="REVERSE_INPUT_INVALID")
        shutil.copyfile(source, staged_source)
        source_hash = _hash(staged_source)
        if (source_hash != source_snapshot["sha256"]
                or source_snapshot != _snapshot(source, code="REVERSE_INPUT_INVALID")):
            raise _fail("Source changed while staging native conversion", "REVERSE_INPUT_INVALID")
        (_validate_dxf if write_dwg else _validate_dwg)(staged_source, output=False)
        runtime_hash = _hash(executable)
        # Fixed relative filenames prevent script command injection and avoid
        # AutoCAD script encoding dependencies for user-supplied Unicode paths.
        commands = (f'_.SAVEAS\n_2018\n"{staged_output.name}"\n_.QUIT\n' if write_dwg
                    # ASCII DXFOUT at precision 16 can lose several ULPs for
                    # large projected coordinates. Binary retains the native
                    # double values without relaxing the caller's tolerance.
                    else f'_.DXFOUT\n"{staged_output.name}"\n_Binary\n_.QUIT\n')
        script = work / "convert.scr"
        script.write_text(commands, encoding="ascii")
        log = output.parent / f"{output.name}.autocad-{uuid.uuid4().hex}.log"
        command = [str(executable)]
        if not write_dwg:
            command.append("/readonly")
        command.extend(["/i", str(staged_source), "/s", str(script)])
        execution = _run_core(command, work, log, seconds)
        if not staged_output.is_file():
            raise _fail(f"AutoCAD exited successfully without the required output; log: {log}",
                        "REVERSE_DWG_INVALID_OUTPUT", **execution)
        try:
            validation = (_validate_dwg if write_dwg else _validate_dxf)(staged_output, output=True)
            validated_snapshot = validation.pop("_content_snapshot")
            if validated_snapshot != _snapshot(staged_output, code="REVERSE_DWG_INVALID_OUTPUT"):
                raise _fail("Native output changed during validation", "REVERSE_DWG_INVALID_OUTPUT")
            if source_snapshot != _snapshot(source, code="REVERSE_INPUT_INVALID"):
                raise _fail("Source changed during native conversion", "REVERSE_INPUT_INVALID")
        except ReverseConversionError as exc:
            exc.details.update(execution)
            raise
        # Exclusive creation prevents overwriting a destination created during
        # the native process. Publication copies only the validated generated file.
        created_identity = None
        try:
            with output.open("xb") as destination:
                created_stat = os.fstat(destination.fileno())
                created_identity = (created_stat.st_dev, created_stat.st_ino)
                with staged_output.open("rb") as origin:
                    shutil.copyfileobj(origin, destination)
        except FileExistsError as exc:
            raise _fail(f"Output already exists: {output}", "REVERSE_OUTPUT_EXISTS") from exc
        except OSError:
            # Never delete a pre-existing file after an access-denied open, or
            # a replacement created by another writer during a failed copy.
            if created_identity is not None and output.is_file():
                current_stat = output.stat()
                if (current_stat.st_dev, current_stat.st_ino) == created_identity:
                    output.unlink()
            raise
        try:
            if source_snapshot != _snapshot(source, code="REVERSE_INPUT_INVALID"):
                raise _fail("Source changed before native output publication completed",
                            "REVERSE_INPUT_INVALID", output_path=str(output))
            # Make the published pathname the final file observation before
            # constructing success metadata; report only its bound snapshot.
            published_snapshot = _snapshot(output, code="REVERSE_DWG_INVALID_OUTPUT")
            if (published_snapshot["identity"] != created_identity
                    or published_snapshot["sha256"] != validated_snapshot["sha256"]
                    or published_snapshot["bytes"] != validated_snapshot["bytes"]):
                raise _fail("Published native output changed or was replaced after copying",
                            "REVERSE_DWG_INVALID_OUTPUT", output_path=str(output),
                            expected_sha256=validated_snapshot["sha256"],
                            observed_sha256=published_snapshot["sha256"])
        except (OSError, ReverseConversionError) as exc:
            # Do not unlink: another writer may now own the pathname, even
            # when it replaced our file with byte-identical content.
            if isinstance(exc, ReverseConversionError):
                failure = exc
            else:
                failure = _fail(f"Published native output is no longer readable: {exc}",
                                "REVERSE_DWG_INVALID_OUTPUT")
            failure.details.update(execution)
            failure.details["output_path"] = str(output)
            failure.retained_artifact_path = output
            raise failure
        report = {
            "schema_version": "cad2gis.reverse_dwg.v1", "backend": BACKEND,
            "operation": "dxf_to_dwg" if write_dwg else "dwg_to_dxf",
            "runtime_path": str(executable), "runtime_sha256": runtime_hash,
            "source_path": str(source), "source_sha256": source_hash,
            "output_path": str(output), "output_sha256": published_snapshot["sha256"],
            "output_bytes": published_snapshot["bytes"],
            "validated_output_sha256": validated_snapshot["sha256"],
            "validated_output_bytes": validated_snapshot["bytes"],
            **execution, **validation,
            "geometry_fidelity_verified": False,
        }
        return report
    except ReverseConversionError as exc:
        pending_error = exc
        raise
    except OSError as exc:
        pending_error = _fail(f"Native conversion file operation failed: {exc}", "REVERSE_DWG_IO_ERROR")
        raise pending_error from exc
    finally:
        running = bool(pending_error and getattr(pending_error, "details", {}).get("process_may_be_running"))
        if work is not None and not running:
            warning = _cleanup(work)
            if warning:
                if pending_error is not None:
                    pending_error.details["cleanup_warning"] = warning
                else:
                    report["cleanup_warning"] = warning


def convert_dxf_to_dwg(source_dxf: Path, output_dwg: Path, *,
                       runtime: Path | None = None, timeout: float = 120) -> dict[str, Any]:
    """Write a new DWG 2018; caller must verify native readback fidelity."""
    return _convert(source_dxf, output_dwg, runtime=runtime, timeout=timeout, write_dwg=True)


def read_dwg_as_dxf(source_dwg: Path, output_dxf: Path, *,
                    runtime: Path | None = None, timeout: float = 120) -> dict[str, Any]:
    """Export a generated DWG 2018 to a new DXF and require a clean audit."""
    return _convert(source_dwg, output_dxf, runtime=runtime, timeout=timeout, write_dwg=False)


__all__ = ["convert_dxf_to_dwg", "read_dwg_as_dxf", "resolve_runtime", "native_readback_workspace"]
