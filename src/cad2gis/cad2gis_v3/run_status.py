"""Run status derivation and verified-delivery alias policy."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import TypeAlias


class RunStatus(str, Enum):
    VERIFIED = "VERIFIED"
    CONDITIONAL = "CONDITIONAL"
    UNSAFE = "UNSAFE"
    FAILED = "FAILED"


_CountInput: TypeAlias = int | Iterable[object] | None
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


def _count_input(value: _CountInput, name: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a nonnegative integer or iterable")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{name} must be nonnegative")
        return value
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a nonnegative integer or iterable")
    try:
        return sum(1 for _ in value)
    except TypeError as exc:
        raise TypeError(f"{name} must be a nonnegative integer or iterable") from exc


def _combine_counts(
    primary: _CountInput,
    alias: _CountInput,
    primary_name: str,
    alias_name: str,
) -> int:
    return _count_input(primary, primary_name) + _count_input(alias, alias_name)


def _count_incompleteness(value: _CountInput, name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    return _count_input(value, name)


def derive_run_status(
    *,
    entity_count: int,
    serious_failures: _CountInput = None,
    warning_count: int = 0,
    warnings: _CountInput = None,
    reader_skips: _CountInput = None,
    reader_incomplete: _CountInput = None,
    reader_incompleteness: _CountInput = None,
    unresolved_total: _CountInput = None,
    unsupported_total: _CountInput = None,
    abstained_total: _CountInput = None,
    errored_total: _CountInput = None,
    reader_skip_count: _CountInput = None,
    reader_incomplete_count: _CountInput = None,
    reader_incompleteness_count: _CountInput = None,
    unresolved_count: _CountInput = None,
    unsupported_count: _CountInput = None,
    abstained_count: _CountInput = None,
    errored_count: _CountInput = None,
) -> RunStatus:
    """Derive a run status from deterministic pipeline facts.

    Reader loss, explicit serious failures, and reader errors make a run
    unsafe. Warnings and incomplete semantic coverage remain conditional while
    retaining usable output. A run without source entities always fails.
    """

    if isinstance(entity_count, bool) or not isinstance(entity_count, int):
        raise TypeError("entity_count must be a nonnegative integer")
    if entity_count < 0:
        raise ValueError("entity_count must be nonnegative")

    serious_count = _count_input(serious_failures, "serious_failures")
    warning_total = _count_input(warning_count, "warning_count") + _count_input(
        warnings, "warnings"
    )
    reader_skip_total = _combine_counts(
        reader_skips, reader_skip_count, "reader_skips", "reader_skip_count"
    )
    reader_incomplete_total = _count_incompleteness(
        reader_incomplete, "reader_incomplete"
    ) + _count_incompleteness(reader_incompleteness, "reader_incompleteness")
    reader_incomplete_total += _count_incompleteness(
        reader_incomplete_count, "reader_incomplete_count"
    ) + _count_incompleteness(
        reader_incompleteness_count, "reader_incompleteness_count"
    )
    unresolved = _combine_counts(
        unresolved_total, unresolved_count, "unresolved_total", "unresolved_count"
    )
    unsupported = _combine_counts(
        unsupported_total,
        unsupported_count,
        "unsupported_total",
        "unsupported_count",
    )
    abstained = _combine_counts(
        abstained_total, abstained_count, "abstained_total", "abstained_count"
    )
    errored = _combine_counts(
        errored_total, errored_count, "errored_total", "errored_count"
    )

    if entity_count == 0:
        return RunStatus.FAILED
    if serious_count or reader_skip_total or reader_incomplete_total or errored:
        return RunStatus.UNSAFE
    if warning_total or unresolved or unsupported or abstained:
        return RunStatus.CONDITIONAL
    return RunStatus.VERIFIED


def _validate_status(status: object) -> RunStatus:
    if not isinstance(status, RunStatus):
        raise TypeError("status must be a RunStatus value")
    return status


def _resolve_path(value: object, name: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{name} must be a path")
    try:
        raw_value = os.fspath(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be a path") from exc
    if isinstance(raw_value, bytes) or not str(raw_value).strip():
        raise ValueError(f"{name} must not be empty")
    try:
        path = Path(value).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid path") from exc
    return path


def _validate_run_dir(run_dir: object) -> Path:
    resolved = _resolve_path(run_dir, "run_dir")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"run_dir must identify a directory: {resolved}")
    return resolved


def _validate_alias_path(alias_path: object) -> Path:
    resolved = _resolve_path(alias_path, "alias_path")
    if resolved.exists() and not resolved.is_file():
        raise ValueError(f"alias_path must identify a file: {resolved}")
    if resolved.parent.exists() and not resolved.parent.is_dir():
        raise ValueError(f"alias_path parent must be a directory: {resolved.parent}")
    return resolved


def _validate_manifest_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("manifest_sha256 must be a 64-character SHA-256 digest")
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("manifest_sha256 must be a 64-character SHA-256 digest")
    return value.lower()


def publish_verified_alias(
    alias_path: str | os.PathLike[str],
    status: RunStatus,
    run_dir: str | os.PathLike[str],
    manifest_sha256: str,
) -> Path | None:
    """Publish a canonical pointer only for a verified run.

    Non-verified statuses do not create, replace, or otherwise write the
    alias. Verified pointers are written to a same-directory temporary file
    and atomically replaced after the complete JSON payload is durable.
    """

    validated_status = _validate_status(status)
    if validated_status is not RunStatus.VERIFIED:
        return None

    resolved_alias = _validate_alias_path(alias_path)
    resolved_run_dir = _validate_run_dir(run_dir)
    normalized_sha256 = _validate_manifest_sha256(manifest_sha256)

    resolved_alias.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_sha256": normalized_sha256,
        "run_dir": str(resolved_run_dir),
        "status": validated_status.value,
    }
    data = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")

    file_descriptor = -1
    temporary: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{resolved_alias.name}.",
            suffix=".tmp",
            dir=resolved_alias.parent,
        )
        temporary = Path(temporary_name)
        with os.fdopen(file_descriptor, "wb") as stream:
            file_descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, resolved_alias)
    except Exception:
        if file_descriptor != -1:
            os.close(file_descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return resolved_alias


__all__ = ["RunStatus", "derive_run_status", "publish_verified_alias"]
