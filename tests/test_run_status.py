from __future__ import annotations

import json
from pathlib import Path

import pytest

import cad2gis.cad2gis_v3.run_status as run_status_module
from cad2gis.cad2gis_v3.run_status import (
    RunStatus,
    derive_run_status,
    publish_verified_alias,
)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"entity_count": 0}, RunStatus.FAILED),
        (
            {
                "entity_count": 0,
                "serious_failures": ["reader_failed"],
                "warning_count": 1,
            },
            RunStatus.FAILED,
        ),
        (
            {"entity_count": 3, "serious_failures": ["crs_unknown"]},
            RunStatus.UNSAFE,
        ),
        (
            {
                "entity_count": 3,
                "serious_failures": ["crs_unknown"],
                "warning_count": 1,
                "unsupported_total": 1,
            },
            RunStatus.UNSAFE,
        ),
        ({"entity_count": 3, "warning_count": 1}, RunStatus.CONDITIONAL),
        ({"entity_count": 3}, RunStatus.VERIFIED),
    ],
)
def test_status_precedence(facts: dict[str, object], expected: RunStatus) -> None:
    assert derive_run_status(**facts) is expected


@pytest.mark.parametrize(
    "facts",
    [
        {"reader_skips": 1},
        {"reader_incompleteness": ["missing_xref"]},
        {"errored_total": 1},
    ],
)
def test_reader_loss_and_errors_are_unsafe(facts: dict[str, object]) -> None:
    assert derive_run_status(entity_count=3, **facts) is RunStatus.UNSAFE


def test_errored_entities_remain_unsafe_when_source_entities_exist() -> None:
    assert derive_run_status(entity_count=3, errored_total=3) is RunStatus.UNSAFE


@pytest.mark.parametrize("field", ["reader_incomplete", "reader_incompleteness"])
def test_reader_incompleteness_flag_is_unsafe(field: str) -> None:
    assert derive_run_status(entity_count=3, **{field: True}) is RunStatus.UNSAFE


@pytest.mark.parametrize(
    "field", ["unresolved_total", "unsupported_total", "abstained_total"]
)
def test_incomplete_semantic_totals_are_conditional(field: str) -> None:
    assert derive_run_status(entity_count=3, **{field: 1}) is RunStatus.CONDITIONAL


def test_iterables_and_warnings_do_not_become_serious_failures() -> None:
    assert (
        derive_run_status(
            entity_count=3,
            serious_failures=(failure for failure in ()),
            warnings=(warning for warning in ("low_confidence",)),
        )
        is RunStatus.CONDITIONAL
    )
    assert (
        derive_run_status(
            entity_count=3,
            serious_failures=(failure for failure in ("geometry_invalid",)),
            warnings=["low_confidence"],
        )
        is RunStatus.UNSAFE
    )


@pytest.mark.parametrize("value", [True, -1, 1.5, "3", None])
def test_entity_count_is_a_nonnegative_integer(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="entity_count"):
        derive_run_status(entity_count=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "warning_count",
        "reader_skips",
        "unresolved_total",
        "unsupported_total",
        "abstained_total",
        "errored_total",
    ],
)
def test_status_counts_are_nonnegative_integers(field: str) -> None:
    with pytest.raises((TypeError, ValueError), match=field):
        derive_run_status(entity_count=3, **{field: True})


def test_verified_alias_has_canonical_absolute_pointer(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "verified"
    run_dir.mkdir(parents=True)
    alias = tmp_path / "aliases" / "latest_verified.json"
    manifest_sha256 = "A" * 64

    result = publish_verified_alias(alias, RunStatus.VERIFIED, run_dir, manifest_sha256)

    expected = {
        "manifest_sha256": manifest_sha256.lower(),
        "run_dir": str(run_dir.resolve()),
        "status": "VERIFIED",
    }
    expected_bytes = (
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    assert result == alias.resolve()
    assert alias.read_bytes() == expected_bytes
    assert json.loads(alias.read_text(encoding="utf-8")) == expected


def test_alias_accepts_a_valid_not_yet_created_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "verified"
    alias = tmp_path / "aliases" / "latest_verified.json"

    publish_verified_alias(alias, RunStatus.VERIFIED, run_dir, "a" * 64)

    payload = json.loads(alias.read_text(encoding="utf-8"))
    assert payload["run_dir"] == str(run_dir.resolve())


@pytest.mark.parametrize(
    "status", [RunStatus.CONDITIONAL, RunStatus.UNSAFE, RunStatus.FAILED]
)
def test_non_verified_status_preserves_existing_alias(
    status: RunStatus, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    alias = tmp_path / "latest_verified.json"
    original = b"previous verified pointer\n"
    alias.write_bytes(original)

    result = publish_verified_alias(alias, status, run_dir, "b" * 64)

    assert result is None
    assert alias.read_bytes() == original


def test_non_verified_status_does_not_create_alias_or_parent(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    alias = tmp_path / "missing" / "latest_verified.json"

    assert publish_verified_alias(alias, RunStatus.UNSAFE, run_dir, "b" * 64) is None
    assert not alias.exists()
    assert not alias.parent.exists()


def test_non_verified_status_is_a_noop_before_other_argument_validation(
    tmp_path: Path,
) -> None:
    alias = tmp_path / "missing" / "latest_verified.json"

    assert (
        publish_verified_alias(alias, RunStatus.UNSAFE, object(), "not-a-digest")
        is None
    )
    assert not alias.exists()
    assert not alias.parent.exists()


def test_alias_replacement_failure_preserves_bytes_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    alias = tmp_path / "latest_verified.json"
    original = b"previous verified pointer\n"
    alias.write_bytes(original)
    before_names = {path.name for path in alias.parent.iterdir()}

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated alias replacement failure")

    monkeypatch.setattr(run_status_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated alias replacement failure"):
        publish_verified_alias(alias, RunStatus.VERIFIED, run_dir, "c" * 64)

    assert alias.read_bytes() == original
    assert {path.name for path in alias.parent.iterdir()} == before_names


def test_alias_write_failure_preserves_bytes_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    alias = tmp_path / "latest_verified.json"
    original = b"previous verified pointer\n"
    alias.write_bytes(original)
    before_names = {path.name for path in alias.parent.iterdir()}

    def fail_fsync(file_descriptor: int) -> None:
        raise OSError("simulated alias fsync failure")

    monkeypatch.setattr(run_status_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated alias fsync failure"):
        publish_verified_alias(alias, RunStatus.VERIFIED, run_dir, "d" * 64)

    assert alias.read_bytes() == original
    assert {path.name for path in alias.parent.iterdir()} == before_names


@pytest.mark.parametrize(
    ("status", "run_dir", "manifest_sha256", "message"),
    [
        ("VERIFIED", "run", "a" * 64, "status"),
        (RunStatus.VERIFIED, "file", "a" * 64, "run_dir"),
        (RunStatus.VERIFIED, "run", "a" * 63, "manifest_sha256"),
        (RunStatus.VERIFIED, "run", "g" * 64, "manifest_sha256"),
    ],
)
def test_alias_validates_status_run_directory_and_digest(
    status: object,
    run_dir: str,
    manifest_sha256: str,
    message: str,
    tmp_path: Path,
) -> None:
    existing_run = tmp_path / "run"
    existing_run.mkdir()
    invalid_run_file = tmp_path / "not-a-directory"
    invalid_run_file.write_text("file", encoding="utf-8")
    resolved_run_dir = existing_run if run_dir == "run" else tmp_path / run_dir
    if run_dir == "file":
        resolved_run_dir = invalid_run_file
    alias = tmp_path / "latest_verified.json"

    with pytest.raises((TypeError, ValueError), match=message):
        publish_verified_alias(alias, status, resolved_run_dir, manifest_sha256)  # type: ignore[arg-type]


def test_alias_rejects_directory_destination(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    alias_directory = tmp_path / "alias"
    alias_directory.mkdir()

    with pytest.raises(ValueError, match="alias"):
        publish_verified_alias(alias_directory, RunStatus.VERIFIED, run_dir, "a" * 64)
