"""Canonical public orchestration facade for CAD2GIS.

The CLI, experiment wrappers, and QGIS integrations should call this module.
Configuration discovery happens here; backend discovery and invocation stay in
``cad2gis.runtime``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import runtime


class ProjectConfigurationError(ValueError):
    """A project is missing a required config or contains an ambiguous one."""


class SourceNotFoundError(FileNotFoundError):
    """The source drawing required by conversion is absent."""


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
_CONFIG_LABELS = {
    "source_profile": "source profile",
    "mapping_registry": "mapping registry",
    "gcp_profile": "GCP profile",
}
_REQUIRED_CONFIGS = frozenset({"source_profile", "mapping_registry"})

_PROJECT_MANIFEST_NAMES = ("cad2gis-project.json", "project.json")
# APD = As Plan Drawing; SF = Subfeeder.  ``ftth_apd`` is the FTTH
# As-Plan-Drawing conversion domain.
_VALID_DOMAINS = frozenset({"auto", "generic", "ftth_apd"})
_VALID_LLM_MODES = frozenset({"off", "observe", "assist"})


def _validate_mode(value: object, allowed: frozenset[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


def _existing_file(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ProjectConfigurationError(f"{label} does not exist: {resolved}")
    return resolved


def _source_file(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise SourceNotFoundError(f"source drawing does not exist: {source}")
    return source


def _config_directories(project_dir: Path) -> tuple[Path, ...]:
    # ``--project`` may point at a project root or directly at its config dir.
    directories = (project_dir / "config", project_dir, project_dir / ".cad2gis")
    unique: list[Path] = []
    for directory in directories:
        if directory not in unique:
            unique.append(directory)
    return tuple(unique)


def _unique_match(paths: Iterable[Path], *, ambiguous: str) -> Path | None:
    matches = sorted({path.resolve() for path in paths})
    if len(matches) > 1:
        rendered = ", ".join(str(path) for path in matches)
        raise ProjectConfigurationError(f"{ambiguous}: {rendered}")
    return matches[0] if matches else None


def _project_manifest(project_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    path = _unique_match(
        (
            directory / name
            for directory in _config_directories(project_dir)
            for name in _PROJECT_MANIFEST_NAMES
            if (directory / name).is_file()
        ),
        ambiguous="project manifest is ambiguous",
    )
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectConfigurationError(f"cannot read project manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectConfigurationError(f"project manifest must be a JSON object: {path}")
    return path, payload


def _config_candidates(project_dir: Path, kind: str) -> Iterable[Path]:
    for directory in _config_directories(project_dir):
        if not directory.is_dir():
            continue
        yield from (
            directory / name
            for name in _CONFIG_PATTERNS[kind]
            if (directory / name).is_file()
        )
        # Project prefixes are supported generically; no drawing/customer name
        # is encoded in the canonical package.
        yield from (
            candidate
            for candidate in directory.glob(f"*_{kind}.json")
            if candidate.is_file()
        )


def _resolve_config(
    *,
    explicit: str | Path | None,
    project: Path | None,
    manifest: tuple[Path, dict[str, Any]] | None,
    kind: str,
) -> Path | None:
    required = kind in _REQUIRED_CONFIGS
    if explicit is not None:
        return _existing_file(explicit, _CONFIG_LABELS[kind])
    if project is not None:
        return _discover_config(project, kind, required=required, manifest=manifest)
    if required:
        raise ProjectConfigurationError(
            f"{kind} is required when project_dir is omitted"
        )
    return None


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
    match = _unique_match(
        _config_candidates(project_dir, kind),
        ambiguous=f"project config {kind!r} is ambiguous",
    )
    if match is None:
        if required:
            expected = ", ".join(_CONFIG_PATTERNS[kind])
            raise ProjectConfigurationError(
                f"project config {kind!r} was not found under {project_dir} "
                f"(expected {expected})"
            )
        return None
    return match


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

    resolved_source = cast(
        Path,
        _resolve_config(
            explicit=source_profile,
            project=project,
            manifest=manifest,
            kind="source_profile",
        ),
    )
    resolved_mapping = cast(
        Path,
        _resolve_config(
            explicit=mapping_registry,
            project=project,
            manifest=manifest,
            kind="mapping_registry",
        ),
    )
    resolved_gcp = _resolve_config(
        explicit=gcp_profile,
        project=project,
        manifest=manifest,
        kind="gcp_profile",
    )

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
    decision_pack: str | Path | None = None,
    domain: str = "auto",
    llm: str = "off",
    source_run: str | Path | None = None,
    semantic_store: str | Path | None = None,
    semantic_job: str = "",
    geometry_repairs: str = "legacy",
    svg_mode: str = "off",
    svg_font_dirs: tuple[str | Path, ...] = (),
) -> Any:
    """Resolve project configuration and run the architecture-v3 conversion."""

    if svg_mode not in {"off", "candidate"}:
        raise ProjectConfigurationError("svg_mode must be off or candidate; legend review is explicit")
    if svg_font_dirs and svg_mode == "off":
        raise ProjectConfigurationError("svg_font_dirs requires SVG candidate mode")
    if bool(semantic_store) != bool(semantic_job) or (semantic_store and not source_run):
        raise ProjectConfigurationError("semantic_store and semantic_job require each other and source_run")

    _validate_mode(domain, _VALID_DOMAINS, "domain")
    _validate_mode(llm, _VALID_LLM_MODES, "llm")
    if decision_pack is not None and llm == "off":
        raise ProjectConfigurationError(
            "decision_pack requires --llm observe or --llm assist"
        )
    source_path = _source_file(source)
    run_path = Path(run_dir).expanduser().resolve()
    if run_path.exists() and not run_path.is_dir():
        raise NotADirectoryError(f"run directory path is not a directory: {run_path}")
    if svg_mode == "candidate":
        from .symbol_workflow import preflight
        preflight(run_path.parent / (run_path.name + "-svg-candidates"), font_dirs=svg_font_dirs)

    configuration = resolve_project_configuration(
        project_dir=project_dir,
        source_profile=source_profile,
        mapping_registry=mapping_registry,
        gcp_profile=gcp_profile,
    )
    resolved_decision_pack = (
        None if decision_pack is None else _existing_file(decision_pack, "decision pack")
    )
    result = runtime.call_conversion_backend(
        source=source_path,
        run_dir=run_path,
        source_profile=configuration.source_profile,
        mapping_registry=configuration.mapping_registry,
        gcp_profile=configuration.gcp_profile,
        decision_pack=resolved_decision_pack,
        domain=domain,
        llm=llm,
        **({"source_run": Path(source_run).expanduser().resolve()} if source_run is not None else {}),
        **({"semantic_store": Path(semantic_store).expanduser().resolve(), "semantic_job": semantic_job} if semantic_store is not None else {}),
        **({"geometry_repairs": geometry_repairs} if geometry_repairs != "legacy" else {}),
    )
    if svg_mode == "candidate":
        from .symbol_workflow import prepare
        candidate_path = run_path.parent / (run_path.name + "-svg-candidates")
        try:
            prepare(source_path, sorted(run_path.rglob("delivery.gpkg")),
                    candidate_path, font_dirs=svg_font_dirs)
        except Exception as exc:
            # The optional presentation store has its own atomic publication boundary.
            # Keep the valid conversion and expose its location when the optional phase fails.
            failure = RuntimeError(f"SVG candidate stage failed; canonical run retained at {run_path}: {exc}")
            failure.artifact_status = str(run_path)
            raise failure from exc
        result.diagnostics["svg_candidates"] = {"mode": "candidate", "auto_applied": False,
                                                "report": str(candidate_path / "correspondence.json"),
                                                "html": str(candidate_path / "correspondence.html")}
    return result


# Compatibility for callers that used the old experiment-oriented verb.
convert = convert_project


def export_source(
    *, source: str | Path, run_dir: str | Path, source_crs: str | None = None,
) -> dict[str, Any]:
    """Freeze reader facts without requiring semantic mapping or registration."""
    backend = runtime.load_backend_module("cad2gis.cad2gis_v3.source_export")
    return backend.export_source(
        source=_source_file(source), run_dir=Path(run_dir).expanduser().resolve(),
        source_crs=source_crs,
    )


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


def prepare_ai_onboarding(*, project_dir: str | Path) -> Any:
    """Return one task-bound evidence bundle and strict AI proposal schema."""

    from .cad2gis_v3.onboarding import prepare_onboarding_bundle

    return prepare_onboarding_bundle(project_dir)


def apply_ai_onboarding(
    *,
    source: str | Path,
    project_dir: str | Path,
    proposal: dict[str, Any],
    proposer: dict[str, Any],
) -> Any:
    """Compile a source-bound AI proposal and derive exact admission gates."""

    from .cad2gis_v3.onboarding import compile_onboarding_proposal

    return compile_onboarding_proposal(
        source=_source_file(source),
        project_dir=Path(project_dir).expanduser().resolve(),
        proposal=proposal,
        proposer=proposer,
    )


def auto_onboard_project(
    *,
    source: str | Path,
    project_dir: str | Path,
    provider: str | None = None,
    force_bootstrap: bool = False,
    llm_mode: str = "off",
) -> Any:
    """Bootstrap, request an AI proposal, compile it, and validate admission."""

    from .cad2gis_v3.onboarding import auto_onboard_with_provider

    return auto_onboard_with_provider(
        source=_source_file(source),
        project_dir=Path(project_dir).expanduser().resolve(),
        provider_id=provider,
        force_bootstrap=force_bootstrap,
        llm_mode=llm_mode,
    )
