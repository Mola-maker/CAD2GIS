"""Exercise the real SDK, MCP subprocess and converter with an authored DWG.

The model is a deterministic protocol fixture, and the OSM data is authored
locally. No provider credentials, user drawings or remote model calls are used.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


async def run(bundle, output):
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from cad2gis.application import ApplicationConfig
    from cad2gis.desktop_agent import DesktopAgent
    from cad2gis.desktop_settings import AISettings
    from cad2gis.agent_tool_bridge import MCPToolBridge

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from smoke_synthetic_conversion import run as create_fixture
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / 'fixture'
    create_fixture(bundle, fixture)
    original_manifest = fixture / 'run/run_manifest.json'
    original_hash = hashlib.sha256(original_manifest.read_bytes()).hexdigest()
    settings = AISettings(output / '.state')
    settings.save({'provider': 'new-api', 'base_url': 'http://127.0.0.1:19876/v1',
                   'model': 'local-protocol-fixture', 'api_key': 'fixture-only-not-a-provider-key'})
    agent = DesktopAgent(ApplicationConfig((output,), state_dir=settings.directory), settings, SimpleNamespace(active=''))
    calls, steps = [], []
    original_call = MCPToolBridge.call

    async def with_fixture_osm(self, name, arguments):
        calls.append(name)
        if name == 'run_conversion':
            arguments = {**arguments, 'osm_data': str(fixture / 'osm-fixture.json')}
        return await original_call(self, name, arguments)

    async def stream(messages, info):
        steps.append(True)
        name = 'workspace_context' if len(steps) == 1 else 'convert_selected_drawing'
        if len(steps) <= 3:  # The second conversion call must return the same receipt.
            yield {0: DeltaToolCall(name=name, json_args='{}', tool_call_id=f'fixture-{len(steps)}')}
        else:
            yield '转换已完成，可以打开结果核对地图。'

    agent.model_override = FunctionModel(stream_function=stream)
    MCPToolBridge.call = with_fixture_osm
    try:
        started = agent.start({'message': '转换当前图纸', 'source': str(fixture / 'generated.dwg'),
                               'project': str(fixture / 'project'), 'run': str(fixture / 'run')})
        await asyncio.wait_for(agent.task, 180)
    finally:
        MCPToolBridge.call = original_call
    state = agent.store.run(started['id'])
    assert state['status'] == 'succeeded', state['error']
    assert calls == ['validate_project', 'run_conversion'], calls
    result = state['result']
    assert result['run_dir'] != str(fixture / 'run')
    assert hashlib.sha256(original_manifest.read_bytes()).hexdigest() == original_hash
    manifest = json.loads(Path(result['run_manifest']).read_text(encoding='utf-8'))
    assert manifest['osm_matching']['status'] == 'matched', manifest['osm_matching']['status']
    assert all(abs(value - expected) < 0.2 for value, expected in zip(
        manifest['osm_matching']['translation_3857'], [100.0, 200.0]))
    assert manifest['svg_candidates']['status'] == 'completed'
    assert Path(result['delivery']).is_file()
    report = {'status': 'passed', 'model': 'deterministic fixture', 'mcp': 'real subprocess',
              'conversion': 'real DWG and cached OSM', 'calls': calls, 'result': result,
              'previous_run_unchanged': True, 'duplicate_conversion_calls': 0}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    os.environ.pop('CAD2GIS_BACKEND_PATH', None)
    asyncio.run(run(arguments.bundle.resolve(), arguments.output.resolve()))
