"""Selected-path evidence, conversion routing and preservation of old results."""
import asyncio
import json
from pathlib import Path

import pytest

from cad2gis.application import ApplicationConfig
from cad2gis.desktop_workspace import SelectedConversion, SelectedWorkspace


def workspace(root, **context):
    config = ApplicationConfig((root,), state_dir=root / '.state')
    return SelectedWorkspace(context, config.roots, lambda path, exists=True: config.path(path, exists=exists))


def test_context_distinguishes_planned_paths_and_lists_actual_nested_files(tmp_path):
    source = tmp_path / 'drawing.dwg'
    source.write_bytes(b'fixture')
    project = tmp_path / 'project'
    (project / 'config').mkdir(parents=True)
    actual = project / 'config/source_profile.json'
    actual.write_text('{}')
    selected = workspace(tmp_path, source=str(source), project=str(project), run=str(tmp_path / 'run-1'))
    context = selected.snapshot()
    assert context['paths']['project']['kind'] == 'directory'
    assert context['paths']['run'] == {'path': str(tmp_path / 'run-1'), 'exists': False, 'kind': 'planned'}
    assert context['existing_files']['project'] == [str(actual)]
    assert selected.files()['entries'][0]['path'] == str(project / 'config')
    assert selected.files(str(project / 'config'))['entries'][0]['path'] == str(actual)
    assert not selected.files(str(tmp_path / 'run-1'))['exists']
    with pytest.raises(ValueError, match='目录'):
        selected.files(str(source))


def test_listing_is_paged_and_does_not_reveal_credentials(tmp_path):
    (tmp_path / '.state').mkdir()
    for name in ('a.json', 'b.json', 'c.json'):
        (tmp_path / name).write_text('{}')
    selected = workspace(tmp_path)
    first = selected.files(page_size=2)
    assert first['total'] == 3 and first['has_more']
    assert [entry['name'] for entry in selected.files(page=2, page_size=2)['entries']] == ['c.json']
    with pytest.raises(ValueError):
        selected.files(str(tmp_path / '.state'))
    with pytest.raises(ValueError):
        selected.files(str(tmp_path.parent))


@pytest.mark.parametrize('approved', [True, False, None])
def test_conversion_routes_once_and_preserves_previous_run(tmp_path, approved):
    source, project, old_run = tmp_path / 'input.dwg', tmp_path / 'project', tmp_path / 'run-1'
    source.write_bytes(b'fixture')
    old_run.mkdir()
    (old_run / 'keep.txt').write_text('original')
    if approved is not None:
        (project / 'config').mkdir(parents=True)
        for name in ('source_profile.json', 'mapping_registry.json'):
            (project / 'config' / name).write_text('{}')
    selected = workspace(tmp_path, source=str(source), project=str(project), run=str(old_run), osm_place='Tomohon')
    calls = []

    async def call(name, args):
        calls.append((name, dict(args)))
        if name == 'validate_project':
            return {'result': json.dumps({'conversion_allowed': approved})}
        run = Path(args['run_dir'])
        assert not run.exists() and run != project
        assert args['osm_place'] == 'Tomohon'
        assert args['matching'] == 'osm' and args['svg_mode'] == 'candidate'
        run.mkdir()
        manifest, delivery = run / 'run_manifest.json', run / 'delivery.gpkg'
        manifest.write_text('{}')
        delivery.write_bytes(b'fixture')
        return {'result': json.dumps({'run_manifest': str(manifest), 'delivery': str(delivery), 'run_status': 'CONDITIONAL'})}

    async def scenario():
        conversion = SelectedConversion(selected, call)
        first = await conversion.execute()
        assert await conversion.execute() == first
        result = conversion.verified_result()
        assert result['drawing'] == str(source) and result['run_dir'] != str(old_run)
    asyncio.run(scenario())
    assert [name for name, _ in calls] == (['validate_project'] if approved is not None else []) + [
        'run_conversion' if approved else 'auto_onboard_and_convert']
    assert (old_run / 'keep.txt').read_text() == 'original'


def test_conversion_failure_keeps_same_mutation_identity(tmp_path):
    from pydantic_ai import ModelRetry
    source = tmp_path / 'input.dwg'
    source.write_bytes(b'fixture')
    calls = []

    async def call(name, arguments):
        calls.append((name, dict(arguments)))
        Path(arguments['run_dir']).mkdir(exist_ok=True)
        config = Path(arguments['project_dir']) / 'config'
        config.mkdir(parents=True, exist_ok=True)
        for filename in ('source_profile.json', 'mapping_registry.json'):
            (config / filename).write_text('{}')
        raise ModelRetry('committed=unknown; inspect before retry')

    async def scenario():
        conversion = SelectedConversion(workspace(tmp_path, source=str(source)), call)
        for _ in range(2):
            with pytest.raises(ModelRetry):
                await conversion.execute()
    asyncio.run(scenario())
    assert calls[0] == calls[1]  # MCPToolBridge can reject the same uncertain write.
    assert calls[1][0] == 'auto_onboard_and_convert'


@pytest.mark.parametrize('layout', ['root', 'config', '.cad2gis', 'manifest'])
def test_existing_configuration_layout_uses_canonical_converter(tmp_path, layout):
    source = tmp_path / 'input.dwg'
    source.write_bytes(b'fixture')
    project = tmp_path / 'project'
    directory = project / ('custom' if layout == 'manifest' else layout) if layout != 'root' else project
    directory.mkdir(parents=True)
    for name in ('source_profile.json', 'mapping_registry.json'):
        (directory / name).write_text('{}')
    if layout == 'manifest':
        (project / 'project.json').write_text(json.dumps({'config': {
            'source_profile': 'custom/source_profile.json', 'mapping_registry': 'custom/mapping_registry.json'}}))
    selected_project = directory if layout == 'config' else project
    calls = []

    async def call(name, args):
        calls.append(name)
        return {'result': '{}'}

    selected = workspace(tmp_path, source=str(source), project=str(selected_project))
    asyncio.run(SelectedConversion(selected, call).execute())
    assert calls == ['run_conversion']
    assert str(directory / 'source_profile.json') in selected.snapshot()['existing_files']['project']


def test_success_without_artifacts_is_not_published_or_replayed(tmp_path):
    source = tmp_path / 'input.dwg'
    source.write_bytes(b'fixture')
    calls = []

    async def call(name, arguments):
        calls.append(name)
        return {'result': '{}'}

    async def scenario():
        conversion = SelectedConversion(workspace(tmp_path, source=str(source)), call)
        for _ in range(2):
            await conversion.execute()
            with pytest.raises(ValueError):
                conversion.verified_result()
    asyncio.run(scenario())
    assert len(calls) == 1


def test_unconfigured_project_keeps_unmanaged_files_and_starts_onboarding(tmp_path):
    source = tmp_path / 'input.dwg'
    source.write_bytes(b'fixture')
    project = tmp_path / 'project'
    (project / 'config').mkdir(parents=True)
    readme = project / 'README.txt'
    readme.write_text('user notes')
    calls = []

    async def call(name, args):
        calls.append(name)
        return {'result': '{}'}

    asyncio.run(SelectedConversion(workspace(tmp_path, source=str(source), project=str(project)), call).execute())
    assert calls == ['auto_onboard_and_convert']
    assert readme.read_text() == 'user notes'


@pytest.mark.parametrize('name,content', [('project.json', 'broken JSON'), ('source_profile.json', '{}')])
def test_broken_existing_configuration_does_not_bootstrap(tmp_path, name, content):
    source = tmp_path / 'input.dwg'
    source.write_bytes(b'fixture')
    project = tmp_path / 'project'
    project.mkdir()
    (project / name).write_text(content)

    async def call(name, args):
        pytest.fail('Broken configuration must not reach any mutation')

    with pytest.raises(ValueError):
        asyncio.run(SelectedConversion(workspace(tmp_path, source=str(source), project=str(project)), call).execute())
    assert (project / name).read_text() == content
