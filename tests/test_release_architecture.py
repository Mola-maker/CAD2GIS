import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.fetch_derived_release import unpack


@pytest.mark.parametrize('name', ['../escape', '/escape', 'C:/escape', 'folder\\escape', 'raw.dwg'])
def test_release_rejects_unsafe_paths_before_writing(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        entry = zipfile.ZipInfo('placeholder')
        entry.filename = name
        bundle.writestr(entry, 'data')
    with pytest.raises(ValueError):
        unpack(archive, tmp_path / 'published')
    assert not (tmp_path / 'published').exists()


def test_mcp_forwards_svg_options_and_returns_candidate_location(tmp_path, monkeypatch):
    from cad2gis import agent_mcp, pipeline
    captured = {}
    def conversion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run_status='CONDITIONAL', run_manifest_path='manifest', source_path='source',
                               evidence_path='evidence', delivery_path='delivery', style_manifest_path='styles',
                               counts={}, diagnostics={'svg_candidates': {'html': 'candidate/correspondence.html'}})
    monkeypatch.setattr(agent_mcp, '_path', lambda path, **kwargs: Path(path))
    monkeypatch.setattr(pipeline, 'convert_project', conversion)
    result = agent_mcp.run_conversion('input.dwg', 'run', 'project', svg_mode='candidate', svg_font_dirs=['fonts'])
    assert captured['svg_mode'] == 'candidate'
    assert captured['svg_font_dirs'] == (Path('fonts'),)
    assert result['svg_candidates']['html'].endswith('correspondence.html')


def test_svg_preflight_prevents_conversion_when_output_exists(tmp_path, monkeypatch):
    from cad2gis import pipeline, runtime
    source = tmp_path / 'source.dwg'
    source.write_bytes(b'source')
    (tmp_path / 'run-svg-candidates').mkdir()
    monkeypatch.setattr(runtime, 'call_conversion_backend', lambda **k: pytest.fail('Backend must not run'))
    with pytest.raises(FileExistsError):
        pipeline.convert_project(source=source, run_dir=tmp_path / 'run', svg_mode='candidate')


def test_release_manifest_names_public_derived_assets_only():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'docs/derived-release.json').read_text())
    assert manifest['raw_dwg_included'] is False
    assert manifest['complete_legend_correspondence_verified'] is False
    assert len(manifest['sha256']) == 64
