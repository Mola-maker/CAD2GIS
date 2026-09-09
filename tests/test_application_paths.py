"""Portable review files must remain inside the admitted project roots."""

import json
from pathlib import Path

import pytest

from cad2gis.application import ApplicationConfig
from cad2gis.review_server import _PORTABLE_ARTIFACT_PATHS


@pytest.mark.parametrize("relative", [
    *[str(path) for path in _PORTABLE_ARTIFACT_PATHS.values()],
    "private.dwg", "source/private.dwg",
])
def test_portable_fallback_rejects_resolved_outside_root(tmp_path, monkeypatch, relative):
    root = tmp_path / "allowed"
    run = root / "run"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(json.dumps({
        "source": {"path": str(tmp_path / "missing-origin" / "private.dwg"), "sha256": "a" * 64},
    }))
    config = ApplicationConfig((root,))
    escaped = run / relative
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == escaped:
            return tmp_path / "outside" / path.name
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="允许"):
        config.admit_run(run, root / "run.review")


def test_missing_old_provenance_keeps_portable_run_admissible(tmp_path):
    root = tmp_path / "allowed"
    run = root / "run"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(json.dumps({
        "source": {"path": str(tmp_path / "missing-origin" / "private.dwg"), "sha256": "a" * 64},
    }))
    ApplicationConfig((root,)).admit_run(run, root / "run.review")
