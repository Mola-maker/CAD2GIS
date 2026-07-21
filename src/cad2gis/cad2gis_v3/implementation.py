"""Reproducible provenance for the deterministic production conversion.

The production fingerprint is intentionally built from an explicit allow-list.
Offline review, curation and model-provider code therefore cannot silently
change the identity of the deterministic CAD-to-GIS implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from collections.abc import Iterable, Mapping
from typing import Any


IMPLEMENTATION_SCHEMA_VERSION = "cad2gis-implementation-provenance-v1"
PRODUCTION_CONVERSION_SCOPE = "production-conversion"
PRODUCTION_CONVERSION_SCOPE_VERSION = 3
CONVERSION_SNAPSHOT_SCHEMA_VERSION = "cad2gis-conversion-snapshot-v1"

# Paths are relative to experiment/py_scripts.  Keep this list explicit: adding
# a runtime conversion dependency is an architectural decision and must update
# the recorded scope, while optional review/provider modules remain outside it.
PRODUCTION_CONVERSION_FILES = (
    "apd_rules.py",
    "autocad_reader.py",
    "cad2gis_v3/__init__.py",
    "cad2gis_v3/calibration.py",
    "cad2gis_v3/cli.py",
    "cad2gis_v3/config.py",
    "cad2gis_v3/curve_geometry.py",
    "cad2gis_v3/evidence.py",
    "cad2gis_v3/georef.py",
    "cad2gis_v3/gpkg_metadata.py",
    "cad2gis_v3/implementation.py",
    "cad2gis_v3/ingest.py",
    "cad2gis_v3/model.py",
    "cad2gis_v3/pipeline.py",
    "cad2gis_v3/ports.py",
    "cad2gis_v3/project_profile.py",
    "cad2gis_v3/runtime_provenance.py",
    "cad2gis_v3/semantics.py",
    "cad2gis_v3/spatial_coverage.py",
    "cad2gis_v3/styles.py",
    "cad2gis_v3/topology.py",
    "cad2gis_v3/units.py",
    "cad2gis_v3/warehouse.py",
    "convert_v3.py",
    "schema_config.py",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized_relative_paths(relative_paths: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for raw_path in relative_paths:
        path = PurePosixPath(str(raw_path).replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
            raise ValueError(f"Implementation scope path must be relative: {raw_path!r}")
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        raise ValueError("Implementation scope contains duplicate paths")
    return tuple(sorted(normalized))


def build_implementation_provenance(
    root: Path,
    *,
    scope: str,
    scope_version: int,
    relative_paths: Iterable[str],
) -> dict:
    """Build a path-stable, content-addressed implementation descriptor."""
    root = Path(root).resolve()
    if not scope or not scope.strip():
        raise ValueError("Implementation scope must be non-empty")
    if isinstance(scope_version, bool) or not isinstance(scope_version, int) or scope_version < 1:
        raise ValueError("Implementation scope_version must be a positive integer")

    files = []
    for relative_path in _normalized_relative_paths(relative_paths):
        path = root.joinpath(*PurePosixPath(relative_path).parts)
        if not path.is_file():
            raise FileNotFoundError(
                f"Implementation scope file does not exist: {relative_path}"
            )
        content = path.read_bytes()
        files.append({
            "path": relative_path,
            "sha256": _sha256_bytes(content),
            "size_bytes": len(content),
        })

    descriptor = {
        "schema_version": IMPLEMENTATION_SCHEMA_VERSION,
        "scope": scope.strip(),
        "scope_version": scope_version,
        "files": files,
    }
    canonical = json.dumps(
        descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {**descriptor, "sha256": _sha256_bytes(canonical)}


def production_conversion_provenance(root: Path | None = None) -> dict:
    """Return provenance for only the deterministic production conversion."""
    if root is None:
        root = Path(__file__).resolve().parent.parent
    return build_implementation_provenance(
        root,
        scope=PRODUCTION_CONVERSION_SCOPE,
        scope_version=PRODUCTION_CONVERSION_SCOPE_VERSION,
        relative_paths=PRODUCTION_CONVERSION_FILES,
    )


def implementation_manifest_fields(provenance: dict | None = None) -> dict:
    """Emit the v3 manifest object plus its legacy digest compatibility field."""
    implementation = (
        production_conversion_provenance() if provenance is None else dict(provenance)
    )
    digest = implementation.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Implementation provenance has no valid sha256")
    return {
        "implementation_sha256": digest,
        "implementation": implementation,
    }


class SnapshotVerificationError(RuntimeError):
    """Raised when an immutable conversion input or implementation changed."""

    def __init__(self, message: str, *, mismatches: Iterable[str] = ()):
        self.mismatches = tuple(str(item) for item in mismatches)
        super().__init__(message)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("ascii"))


def _artifact_record(path: str | os.PathLike[str], *, kind: str) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Conversion snapshot {kind} does not exist: {resolved}")
    content = resolved.read_bytes()
    return {
        "kind": str(kind),
        "path": str(resolved),
        "sha256": _sha256_bytes(content),
        "size_bytes": len(content),
    }


def _snapshot_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in snapshot.items()
        if str(key) != "snapshot_sha256"
    }


def _validate_digest(value: Any, name: str) -> str:
    digest = str(value).strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return digest


def freeze_conversion_snapshot(
    source: str | os.PathLike[str],
    source_profile: str | os.PathLike[str],
    mapping_registry: str | os.PathLike[str],
    gcp_profile: str | os.PathLike[str] | None = None,
    *,
    code_root: str | os.PathLike[str] | None = None,
    code_paths: Iterable[str] | None = None,
    runtime: Mapping[str, Any] | None = None,
    reader_inventory: Any = None,
) -> dict[str, Any]:
    """Freeze all byte-addressed conversion inputs before reading a drawing.

    The returned mapping is a value object: callers must not mutate it.  The
    publication boundary calls :func:`verify_conversion_snapshot`, which
    detects both disk changes and accidental descriptor mutation.  Code is
    fingerprinted through the same explicit production allow-list used by the
    regular implementation manifest.
    """

    root = Path(code_root).expanduser().resolve() if code_root is not None else (
        Path(__file__).resolve().parent.parent
    )
    relative_paths = tuple(code_paths) if code_paths is not None else PRODUCTION_CONVERSION_FILES
    implementation = build_implementation_provenance(
        root,
        scope=PRODUCTION_CONVERSION_SCOPE,
        scope_version=PRODUCTION_CONVERSION_SCOPE_VERSION,
        relative_paths=relative_paths,
    )
    artifacts = {
        "source": _artifact_record(source, kind="source"),
        "source_profile": _artifact_record(source_profile, kind="source_profile"),
        "mapping_registry": _artifact_record(mapping_registry, kind="mapping_registry"),
        "gcp_profile": (
            None if gcp_profile is None else _artifact_record(gcp_profile, kind="gcp_profile")
        ),
    }
    snapshot: dict[str, Any] = {
        "schema_version": CONVERSION_SNAPSHOT_SCHEMA_VERSION,
        "code_root": str(root),
        "artifacts": artifacts,
        "implementation": implementation,
        # Convenience digests make the manifest inspectable without parsing
        # nested artifact records, while the records remain authoritative.
        "source_sha256": artifacts["source"]["sha256"],
        "source_profile_sha256": artifacts["source_profile"]["sha256"],
        "mapping_registry_sha256": artifacts["mapping_registry"]["sha256"],
        "gcp_profile_sha256": (
            None if artifacts["gcp_profile"] is None else artifacts["gcp_profile"]["sha256"]
        ),
    }
    if runtime is not None:
        # Runtime identity is intentionally kept separate from implementation
        # bytes, but may be captured at the same conversion-start boundary.
        snapshot["runtime_provenance"] = json.loads(_canonical_json(runtime))
    elif reader_inventory is not None:
        from .runtime_provenance import collect_runtime_provenance

        snapshot["runtime_provenance"] = collect_runtime_provenance(
            reader_inventory=reader_inventory
        )
    snapshot["snapshot_sha256"] = _canonical_sha256(snapshot)
    return json.loads(_canonical_json(snapshot))


def _verify_artifact(
    name: str,
    expected: Mapping[str, Any],
    *,
    override: str | os.PathLike[str] | None = None,
) -> tuple[bool, str]:
    if not isinstance(expected, Mapping):
        return False, f"artifacts.{name}: descriptor is not an object"
    path = override if override is not None else expected.get("path")
    if path is None:
        return False, f"artifacts.{name}: no path recorded"
    try:
        actual = _artifact_record(path, kind=name)
    except (OSError, ValueError) as exc:
        return False, f"artifacts.{name}: {exc}"
    for field in ("sha256", "size_bytes"):
        if actual[field] != expected.get(field):
            return False, (
                f"artifacts.{name}.{field}: expected {expected.get(field)!r}, "
                f"got {actual[field]!r}"
            )
    return True, str(path)


def verify_conversion_snapshot(
    snapshot: Mapping[str, Any],
    *,
    source: str | os.PathLike[str] | None = None,
    source_profile: str | os.PathLike[str] | None = None,
    mapping_registry: str | os.PathLike[str] | None = None,
    gcp_profile: str | os.PathLike[str] | None = None,
    code_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Re-read and verify every frozen byte before publication.

    Any mismatch raises :class:`SnapshotVerificationError`; there is no
    warning-only mode at the publication boundary.  Optional path overrides
    are intended for an adapter that stages an input under a different path,
    while retaining the expected digest from the original snapshot.
    """

    if not isinstance(snapshot, Mapping):
        raise SnapshotVerificationError("Conversion snapshot must be a mapping")
    mismatches: list[str] = []
    if snapshot.get("schema_version") != CONVERSION_SNAPSHOT_SCHEMA_VERSION:
        mismatches.append(
            "schema_version: expected "
            f"{CONVERSION_SNAPSHOT_SCHEMA_VERSION!r}, got {snapshot.get('schema_version')!r}"
        )
    expected_snapshot_digest = snapshot.get("snapshot_sha256")
    try:
        expected_snapshot_digest = _validate_digest(
            expected_snapshot_digest, "snapshot_sha256"
        )
    except ValueError as exc:
        mismatches.append(str(exc))
        expected_snapshot_digest = None
    if expected_snapshot_digest is not None:
        actual_snapshot_digest = _canonical_sha256(_snapshot_payload(snapshot))
        if actual_snapshot_digest != expected_snapshot_digest:
            mismatches.append(
                "snapshot descriptor changed: expected "
                f"{expected_snapshot_digest}, got {actual_snapshot_digest}"
            )

    artifacts = snapshot.get("artifacts")
    if not isinstance(artifacts, Mapping):
        mismatches.append("artifacts: descriptor is not an object")
        artifacts = {}
    overrides = {
        "source": source,
        "source_profile": source_profile,
        "mapping_registry": mapping_registry,
        "gcp_profile": gcp_profile,
    }
    checked: list[str] = []
    for name in ("source", "source_profile", "mapping_registry"):
        expected = artifacts.get(name)
        ok, detail = _verify_artifact(name, expected, override=overrides[name])
        checked.append(name)
        if not ok:
            mismatches.append(detail)
    expected_gcp = artifacts.get("gcp_profile")
    checked.append("gcp_profile")
    if expected_gcp is None:
        if overrides["gcp_profile"] is not None:
            mismatches.append("artifacts.gcp_profile: snapshot has no GCP profile")
    else:
        ok, detail = _verify_artifact(
            "gcp_profile", expected_gcp, override=overrides["gcp_profile"]
        )
        if not ok:
            mismatches.append(detail)

    implementation = snapshot.get("implementation")
    if not isinstance(implementation, Mapping):
        mismatches.append("implementation: descriptor is not an object")
    else:
        try:
            root = (
                Path(code_root).expanduser().resolve()
                if code_root is not None
                else Path(str(snapshot.get("code_root", ""))).expanduser().resolve()
            )
            relative_paths = tuple(
                str(item["path"])
                for item in implementation.get("files", ())
                if isinstance(item, Mapping) and "path" in item
            )
            current = build_implementation_provenance(
                root,
                scope=str(implementation.get("scope", PRODUCTION_CONVERSION_SCOPE)),
                scope_version=int(
                    implementation.get("scope_version", PRODUCTION_CONVERSION_SCOPE_VERSION)
                ),
                relative_paths=relative_paths,
            )
            if current != dict(implementation):
                mismatches.append("implementation: production code changed")
            checked.append("implementation")
        except (OSError, TypeError, ValueError) as exc:
            mismatches.append(f"implementation: {exc}")

    if mismatches:
        raise SnapshotVerificationError(
            "Conversion snapshot verification failed: " + "; ".join(mismatches),
            mismatches=mismatches,
        )
    return {
        "verified": True,
        "schema_version": CONVERSION_SNAPSHOT_SCHEMA_VERSION,
        "checked": checked,
        "mismatches": [],
        "snapshot_sha256": expected_snapshot_digest,
    }


def conversion_snapshot_manifest_fields(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable snapshot and its scalar manifest digest."""

    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")
    digest = _validate_digest(snapshot.get("snapshot_sha256"), "snapshot_sha256")
    actual = _canonical_sha256(_snapshot_payload(snapshot))
    if actual != digest:
        raise ValueError(
            "Conversion snapshot descriptor hash mismatch: "
            f"expected {digest}, got {actual}"
        )
    value = json.loads(_canonical_json(snapshot))
    return {
        "conversion_snapshot_sha256": digest,
        "conversion_snapshot": value,
    }
