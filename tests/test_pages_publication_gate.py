from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_local_demo import _publication_gate_passed, _validate_demo


ROOT = Path(__file__).resolve().parents[1]
DEMO_ASSETS = ROOT / "src" / "cad2gis" / "webdemo" / "original-demo" / "assets"


def test_current_public_catalog_uses_an_existing_retained_preview() -> None:
    catalog = json.loads(
        (DEMO_ASSETS / "demo-catalog.json").read_text(encoding="utf-8")
    )
    assert [project["id"] for project in catalog["projects"]] == ["kletek"]
    project = catalog["projects"][0]
    assert project['publication_scope'] == 'retained_bundled_preview'
    assert project['absolute_accuracy_verified'] is False
    assert "未提供实测 GCP" in project["accuracy_note"]
    assert _publication_gate_passed(project)
    _validate_demo()
    assert not _publication_gate_passed({**project, 'preview_provenance': 'new-unreviewed-preview'})
    assert not (DEMO_ASSETS / 'demo-data.json').exists()
    assert (DEMO_ASSETS / project['fixture']).is_file()
    assert not list(DEMO_ASSETS.glob("demo-data-*.json"))


def test_future_cases_require_authoritative_calibration_and_checkpoints() -> None:
    osm_only = {
        "id": "kletek",
        "publication_gate": {
            "calibration_status": "verified",
            "control_authority": "openstreetmap",
            "independent_checkpoint_count": 4,
            "spatial_coverage_passed": True,
            "run_status": "VERIFIED",
        },
    }
    assert not _publication_gate_passed(osm_only)

    surveyed = {
        "id": "kletek",
        "publication_gate": {
            "calibration_status": "verified",
            "control_authority": "surveyed",
            "independent_checkpoint_count": 3,
            "spatial_coverage_passed": True,
            "run_status": "VERIFIED",
        },
    }
    assert _publication_gate_passed(surveyed)

    surveyed["publication_gate"]["independent_checkpoint_count"] = 2
    assert not _publication_gate_passed(surveyed)


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'])
def test_retained_preview_verifies_the_same_content_on_lf_and_crlf_checkouts(monkeypatch, newline) -> None:
    fixture = (DEMO_ASSETS / '../../demo-data-kletek.json').resolve()
    raw = fixture.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', newline)
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path: raw if path.resolve() == fixture else read_bytes(path))
    _validate_demo()


def test_new_preview_cannot_self_declare_retained_authorization(monkeypatch) -> None:
    import hashlib
    catalog_path = DEMO_ASSETS / 'demo-catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    project = catalog['projects'][0]
    fixture = (DEMO_ASSETS / project['fixture']).resolve()
    data = json.loads(fixture.read_bytes())
    # A new specimen can be internally self-consistent; it still has no place
    # in the fixed set of previews retained by this change.
    project['id'] = catalog['default_project'] = 'new-unreviewed-project'
    project['source_sha256'] = 'a' * 64
    data['provenance'].update(project_id=project['id'], source_sha256=project['source_sha256'])
    raw = json.dumps(data).encode('utf-8')
    project['fixture_sha256'] = hashlib.sha256(raw).hexdigest()
    read_text, read_bytes = Path.read_text, Path.read_bytes
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs:
                        json.dumps(catalog) if path == catalog_path else read_text(path, *args, **kwargs))
    monkeypatch.setattr(Path, 'read_bytes', lambda path: raw if path.resolve() == fixture else read_bytes(path))
    with pytest.raises(ValueError, match='calibration gate failed'):
        _validate_demo()


@pytest.mark.parametrize('field,value', [
    ('source_sha256', 'a' * 64), ('fixture_sha256', 'b' * 64),
    ('fixture', '../../new-preview.json'), ('new_publication_authorization', True),
])
def test_retained_preview_identity_cannot_be_redeclared(field, value) -> None:
    catalog = json.loads((DEMO_ASSETS / 'demo-catalog.json').read_text(encoding='utf-8'))
    project = catalog['projects'][0]
    assert not _publication_gate_passed({**project, field: value})


def test_retained_preview_rejects_changed_bytes_under_the_existing_id(monkeypatch) -> None:
    fixture = (DEMO_ASSETS / '../../demo-data-kletek.json').resolve()
    data = json.loads(fixture.read_bytes())
    data['provenance']['source_sha256'] = 'b' * 64
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path:
                        json.dumps(data).encode('utf-8') if path.resolve() == fixture else read_bytes(path))
    with pytest.raises(ValueError, match='preview binding failed'):
        _validate_demo()
