"""Lazy discovery and invocation of the architecture-v3 backend.

The installable :mod:`cad2gis` package is deliberately a lightweight public
API and CLI.  The conversion implementation is a separately deployable
backend because its GDAL/AutoCAD runtime cannot be made a portable wheel.

Supported backend deployments are explicit:

* an importable ``cad2gis_v3`` package installed in the active environment;
* ``CAD2GIS_BACKEND_PATH`` pointing at a directory containing
  ``cad2gis_v3/__init__.py`` and its sibling reader modules; or
* the repository backend when this package is used from an editable checkout.

A wheel installed elsewhere never searches the current working directory for
an accidental ``experiment`` tree.  This keeps deployment failures visible
through :mod:`cad2gis.doctor` instead of coupling a wheel to checkout-only
files.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

BACKEND_PATH_ENV = "CAD2GIS_BACKEND_PATH"
BACKEND_PACKAGE = "cad2gis_v3"
PROJECT_BACKEND_MODULES = (
    "cad2gis_v3.project_profile",
    "cad2gis_v3.profile_builder",
)


class BackendUnavailable(RuntimeError):
    """The conversion backend or one of its runtime dependencies is absent."""


class BackendContractError(RuntimeError):
    """The discovered backend does not expose the expected public contract."""


def _configured_roots() -> Iterator[Path]:
    configured = os.environ.get(BACKEND_PATH_ENV, "")
    for raw_path in configured.split(os.pathsep):
        if raw_path.strip():
            yield Path(raw_path).expanduser()


def _editable_backend_root() -> Path | None:
    """Return the repository backend only for a real ``src`` checkout."""

    package_dir = Path(__file__).resolve().parent
    src_dir = package_dir.parent
    repository_root = src_dir.parent
    if src_dir.name != "src" or not (repository_root / "pyproject.toml").is_file():
        return None
    candidate = repository_root / "experiment" / "py_scripts"
    if (candidate / BACKEND_PACKAGE / "__init__.py").is_file():
        return candidate.resolve()
    return None


def _valid_backend_root(candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if (resolved / BACKEND_PACKAGE / "__init__.py").is_file():
        return resolved
    return None


def _importable_backend_location() -> str | None:
    try:
        spec = importlib.util.find_spec(BACKEND_PACKAGE)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None:
        return None
    if spec.origin:
        return str(Path(spec.origin).resolve())
    if spec.submodule_search_locations:
        first = next(iter(spec.submodule_search_locations), None)
        if first:
            return str(Path(first).resolve())
    return BACKEND_PACKAGE


def backend_deployment() -> dict[str, str | None]:
    """Describe the selected backend without importing backend code."""

    configured = list(_configured_roots())
    if configured:
        for candidate in configured:
            root = _valid_backend_root(candidate)
            if root is not None:
                return {
                    "mode": "external_path",
                    "location": str((root / BACKEND_PACKAGE).resolve()),
                }
        return {
            "mode": "invalid_external_path",
            "location": None,
        }

    importable = _importable_backend_location()
    if importable is not None:
        return {"mode": "installed_package", "location": importable}

    editable = _editable_backend_root()
    if editable is not None:
        return {
            "mode": "editable_checkout",
            "location": str((editable / BACKEND_PACKAGE).resolve()),
        }
    return {"mode": "missing", "location": None}


def backend_location() -> str | None:
    """Return a backend location without importing backend code."""

    return backend_deployment()["location"]


def _backend_root_for_import() -> Path | None:
    configured = list(_configured_roots())
    if configured:
        for candidate in configured:
            root = _valid_backend_root(candidate)
            if root is not None:
                return root
        return None
    return _editable_backend_root()


def _prepare_backend_import() -> None:
    configured = list(_configured_roots())
    if configured:
        root = _backend_root_for_import()
        if root is None:
            rendered = os.pathsep.join(str(path) for path in configured)
            raise BackendUnavailable(
                f"{BACKEND_PATH_ENV} does not contain a valid cad2gis_v3 deployment: "
                f"{rendered}"
            )
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
            importlib.invalidate_caches()
        return
    if _importable_backend_location() is not None:
        return
    root = _backend_root_for_import()
    if root is None:
        raise BackendUnavailable(
            "CAD2GIS v3 backend was not found. Install an importable cad2gis_v3 "
            f"deployment or set {BACKEND_PATH_ENV} to the directory containing "
            "cad2gis_v3 and its sibling reader modules; run `cad2gis doctor`."
        )
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
        importlib.invalidate_caches()


def _missing_dependency_error(
    module_name: str, exc: ModuleNotFoundError
) -> BackendUnavailable:
    dependency = exc.name or "an unknown dependency"
    return BackendUnavailable(
        f"cannot load {module_name}: missing runtime dependency {dependency!r}; "
        "run `cad2gis doctor` and use env/environment.yml"
    )


def load_backend_module(module_name: str) -> ModuleType:
    """Import one backend module, translating dependency failures for the CLI."""

    _prepare_backend_import()
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name in {BACKEND_PACKAGE, module_name}:
            raise BackendUnavailable(f"backend module {module_name!r} was not found") from exc
        raise _missing_dependency_error(module_name, exc) from exc
    except ImportError as exc:
        raise BackendUnavailable(f"cannot load {module_name}: {exc}") from exc
    except OSError as exc:
        # Native GIS extension imports commonly surface a missing DLL as OSError.
        raise BackendUnavailable(f"cannot load {module_name}: {exc}") from exc


def call_conversion_backend(
    *,
    source: Path,
    run_dir: Path,
    source_profile: Path,
    mapping_registry: Path,
    gcp_profile: Path | None,
) -> Any:
    """Construct the v3 request and invoke its canonical ``convert`` function."""

    backend = load_backend_module("cad2gis_v3.pipeline")
    request_type = getattr(backend, "ConversionRequest", None)
    convert = getattr(backend, "convert", None)
    if request_type is None or not callable(convert):
        raise BackendContractError(
            "cad2gis_v3.pipeline must expose ConversionRequest and convert"
        )
    request = request_type(
        source=Path(source),
        run_dir=Path(run_dir),
        source_profile=Path(source_profile),
        mapping_registry=Path(mapping_registry),
        gcp_profile=Path(gcp_profile) if gcp_profile is not None else None,
    )
    return convert(request)


def call_project_backend(operation: str, /, **kwargs: Any) -> Any:
    """Invoke a stable project-profile port on either supported backend module."""

    _prepare_backend_import()
    discovered: list[str] = []
    for module_name in PROJECT_BACKEND_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            if exc.name == BACKEND_PACKAGE:
                break
            raise _missing_dependency_error(module_name, exc) from exc
        except ImportError as exc:
            raise BackendUnavailable(f"cannot load {module_name}: {exc}") from exc
        except OSError as exc:
            raise BackendUnavailable(f"cannot load {module_name}: {exc}") from exc
        discovered.append(module_name)
        function = getattr(module, operation, None)
        if callable(function):
            return function(**kwargs)

    if discovered:
        modules = ", ".join(discovered)
        raise BackendContractError(f"{modules} do not expose {operation}()")
    expected = " or ".join(PROJECT_BACKEND_MODULES)
    raise BackendUnavailable(
        f"project-profile backend is not installed; expected {expected}"
    )


def backend_contract() -> Mapping[str, Any]:
    """Return the stable deployment contract used by ``doctor`` and docs."""

    deployment = backend_deployment()
    return {
        "package": BACKEND_PACKAGE,
        "environment_variable": BACKEND_PATH_ENV,
        "supported_modes": (
            "installed_package",
            "external_path",
            "editable_checkout",
        ),
        "selected_mode": deployment["mode"],
        "location": deployment["location"],
        "external_path_requirement": (
            "directory containing cad2gis_v3 plus sibling reader modules"
        ),
        "wheel_bundles_backend": False,
    }
