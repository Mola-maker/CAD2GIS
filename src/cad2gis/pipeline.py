"""Canonical public orchestration facade for CAD2GIS.

The CLI, experiment wrappers, and QGIS integrations should call this module.
Configuration discovery happens here; backend discovery and invocation stay in
``cad2gis.runtime``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import runtime


class ProjectConfigurationError(ValueError):
    """A project is missing a required config or contains an ambiguous one."""


@dataclass(frozen=True)
class ProjectConfiguration:
    source_profile: Path
    mapping_registry: Path
    gcp_profile: Path | None


_CONFIG_PATTERNS: dict[str, tuple[str, ...]] = {
    "source_profile": ("source_profile.json",),
    "mapping_registry": ("mapping_registry.json",),
    "gcp_profile": ("gcp_profile.json",),
}

_PROJECT_MANIFEST_NAMES = ("cad2gis-project.json", "project.json")


def _existing_file(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ProjectConfigurationError(f"{label} does not exist: {resolved}")
    return resolved


def _source_file(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source drawing does not exist: {source}")
    return source


def _config_directories(project_dir: Path) -> tuple[Path, ...]:
    # ``--project`` may point at a project root or directly at its config dir.
    directories = (project_dir / "config", project_dir, project_dir / ".cad2gis")
    unique: list[Path] = []
    for directory in directories:
        if directory not in unique:
            unique.append(directory)
    return tuple(unique)


def _project_manifest(project_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    matches = [
        (directory / name).resolve()
        for directory in _config_directories(project_dir)
        for name in _PROJECT_MANIFEST_NAMES
        if (directory / name).is_file()
    ]
    matches = sorted(set(matches))
    if len(matches) > 1:
        rendered = ", ".join(str(path) for path in matches)
        raise ProjectConfigurationError(f"project manifest is ambiguous: {rendered}")
    if not matches:
        return None
    path = matches[0]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectConfigurationError(f"cannot read project manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectConfigurationError(f"project manifest must be a JSON object: {path}")
    return path, payload


def _manifest_config_path(
    manifest: tuple[Path, dict[str, Any]] | None, kind: str
) -> Path | None:
    if manifest is None:
        return None
    manifest_path, payload = manifest
    config = payload.get("config", payload.get("configuration", {}))
    value: Any = None
    if isinstance(config, dict):
        value = config.get(kind)
    if value is None:
        value = payload.get(kind)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectConfigurationError(
            f"project manifest field {kind!r} must be a non-empty path"
        )
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return _existing_file(candidate, f"project manifest {kind}")


def _discover_config(
    project_dir: Path,
    kind: str,
    *,
    required: bool,
    manifest: tuple[Path, dict[str, Any]] | None = None,
) -> Path | None:
    selected = _manifest_config_path(manifest, kind)
    if selected is not None:
        return selected
    patterns = _CONFIG_PATTERNS[kind]
    matches: set[Path] = set()
    for directory in _config_directories(project_dir):
        if not directory.is_dir():
            continue
        for name in patterns:
            candidate = directory / name
            if candidate.is_file():
                matches.add(candidate.resolve())
        # Project prefixes are supported generically; no drawing/customer name
        # is encoded in the canonical package.
        for candidate in directory.glob(f"*_{kind}.json"):
            if candidate.is_file():
                matches.add(candidate.resolve())

    if not matches:
        if required:
            expected = ", ".join(patterns)
            raise ProjectConfigurationError(
                f"project config {kind!r} was not found under {project_dir} "
                f"(expected {expected})"
            )
        return None
    if len(matches) > 1:
        rendered = ", ".join(str(path) for path in sorted(matches))
        raise ProjectConfigurationError(f"project config {kind!r} is ambiguous: {rendered}")
    return next(iter(matches))


def resolve_project_configuration(
    *,
    project_dir: str | Path | None = None,
    source_profile: str | Path | None = None,
    mapping_registry: str | Path | None = None,
    gcp_profile: str | Path | None = None,
) -> ProjectConfiguration:
    """Resolve explicit config paths and fill omissions from ``project_dir``."""

    project: Path | None = None
    if project_dir is not None:
        project = Path(project_dir).expanduser().resolve()
        if not project.is_dir():
            raise ProjectConfigurationError(f"project directory does not exist: {project}")
    manifest = _project_manifest(project) if project is not None else None

    if source_profile is not None:
        resolved_source = _existing_file(source_profile, "source profile")
    elif project is not None:
        resolved_source = _discover_config(
            project, "source_profile", required=True, manifest=manifest
        )
        assert resolved_source is not None
    else:
        raise ProjectConfigurationError("source_profile is required when project_dir is omitted")

    if mapping_registry is not None:
        resolved_mapping = _existing_file(mapping_registry, "mapping registry")
    elif project is not None:
        resolved_mapping = _discover_config(
            project, "mapping_registry", required=True, manifest=manifest
        )
        assert resolved_mapping is not None
    else:
        raise ProjectConfigurationError("mapping_registry is required when project_dir is omitted")

    if gcp_profile is not None:
        resolved_gcp = _existing_file(gcp_profile, "GCP profile")
    elif project is not None:
        resolved_gcp = _discover_config(
            project, "gcp_profile", required=False, manifest=manifest
        )
    else:
        resolved_gcp = None

    return ProjectConfiguration(
        source_profile=resolved_source,
        mapping_registry=resolved_mapping,
        gcp_profile=resolved_gcp,
    )


def convert_project(
    *,
    source: str | Path,
    run_dir: str | Path,
    project_dir: str | Path | None = None,
    source_profile: str | Path | None = None,
    mapping_registry: str | Path | None = None,
    gcp_profile: str | Path | None = None,
) -> Any:
    """Resolve project configuration and run the architecture-v3 conversion."""

    source_path = _source_file(source)
    run_path = Path(run_dir).expanduser().resolve()
    if run_path.exists() and not run_path.is_dir():
        raise NotADirectoryError(f"run directory path is not a directory: {run_path}")

    configuration = resolve_project_configuration(
        project_dir=project_dir,
        source_profile=source_profile,
        mapping_registry=mapping_registry,
        gcp_profile=gcp_profile,
    )
    return runtime.call_conversion_backend(
        source=source_path,
        run_dir=run_path,
        source_profile=configuration.source_profile,
        mapping_registry=configuration.mapping_registry,
        gcp_profile=configuration.gcp_profile,
    )


# Compatibility for callers that used the old experiment-oriented verb.
convert = convert_project


def inspect_source(
    *, source: str | Path, project_dir: str | Path | None = None
) -> Any:
    """Inspect a source through the optional project-profile backend port."""

    kwargs: dict[str, Any] = {"source": _source_file(source)}
    if project_dir is not None:
        kwargs["project_dir"] = Path(project_dir).expanduser().resolve()
    return runtime.call_project_backend("inspect_source", **kwargs)


def bootstrap_project(
    *, source: str | Path, project_dir: str | Path, force: bool = False
) -> Any:
    """Create a reviewed project skeleton through the backend profile builder."""

    return runtime.call_project_backend(
        "bootstrap_project",
        source=_source_file(source),
        project_dir=Path(project_dir).expanduser().resolve(),
        force=force,
    )


def validate_project(*, project_dir: str | Path) -> Any:
    """Validate a bootstrapped project through the backend profile builder."""

    project = Path(project_dir).expanduser().resolve()
    if not project.is_dir():
        raise ProjectConfigurationError(f"project directory does not exist: {project}")
    return runtime.call_project_backend(
        "validate_project",
        project_dir=project,
    )
