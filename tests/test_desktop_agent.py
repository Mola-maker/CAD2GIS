"""Real SDK and MCP integration, durable settings and read-only database behavior."""
import asyncio
import json
import os
import sqlite3
from types import SimpleNamespace
from pathlib import Path

import pytest

from cad2gis.application import ApplicationConfig
from cad2gis.desktop_agent import AgentStore, DesktopAgent
from cad2gis.desktop_database import query, schema
from cad2gis.desktop_settings import AISettings


def configured(tmp_path):
    settings = AISettings(tmp_path / '.state')
    settings.save({'provider': 'new-api', 'base_url': 'http://127.0.0.1:19876/v1/chat/completions',
                   'model': 'fixture-model', 'api_key': 'test-private-key', 'token_field': 'max_tokens'})
    return settings


def test_config_restart_masking_and_shared_job_environment(tmp_path):
    settings = configured(tmp_path)
    restored = AISettings(settings.directory)
    assert restored.require()['api_key'] == 'test-private-key'
    assert restored.require()['base_url'] == 'http://127.0.0.1:19876/v1'
    assert 'test-private-key' not in json.dumps(restored.public())
    assert restored.environment()['NEW_API_API_KEY'] == 'test-private-key'
    assert restored.environment()['CAD2GIS_LLM_TOKEN_FIELD'] == 'max_tokens'
    if os.name == 'nt':
        assert 'test-private-key' not in settings.file.read_text(encoding='utf-8')
    restored.save({'provider': 'new-api', 'api_key': ''})
    assert restored.require()['api_key'] == 'test-private-key'
    restored.save({'provider': 'new-api', 'clear_key': True})
    with pytest.raises(ValueError, match='密钥'):
        restored.require()


def test_database_paging_and_write_denial(tmp_path):
    path = tmp_path / 'project.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE evidence (id INTEGER PRIMARY KEY, label TEXT)')
        db.executemany('INSERT INTO evidence VALUES (?,?)', [(i, f'对象{i}') for i in range(105)])
    config = ApplicationConfig((tmp_path,))
    assert schema(config, str(path))['tables'][0]['columns'] == ['id', 'label']
    assert query(config, str(path), table='evidence')['has_more']
    assert query(config, str(path), table='evidence', page=3)['rows'] == [[i, f'对象{i}'] for i in range(100,105)]
    for sql in ['DELETE FROM evidence', 'PRAGMA table_info(evidence)', "SELECT load_extension('bad')", "SELECT * FROM pragma_table_info('evidence')"]:
        with pytest.raises(ValueError):
            query(config, str(path), sql=sql)
    assert len(query(config, str(path), table='evidence', page_size=200)['rows']) == 105
    for sql in [None, [], 7]:
        with pytest.raises(ValueError):
            query(config, str(path), sql=sql)


def test_provider_alias_uses_canonical_saved_profile(tmp_path, monkeypatch):
    monkeypatch.delenv('NEW_API_API_KEY', raising=False)
    settings = AISettings(tmp_path / '.state')
    public = settings.save({'provider': 'new_api', 'base_url': 'http://127.0.0.1:19876/v1',
                            'model': 'fixture-model', 'api_key': 'test-private-key'})
    assert public['active'] == 'new-api'
    assert public['profiles']['new-api']['configured']
    restored = AISettings(settings.directory)
    assert restored.require()['api_key'] == 'test-private-key'
    assert restored.environment()['NEW_API_MODEL'] == 'fixture-model'
    restored.save({'provider': 'new-api', 'model': 'updated-model'})
    assert restored.require('new_api')['model'] == 'updated-model'
    assert restored.require('new_api')['api_key'] == 'test-private-key'
    assert set(restored._load()['profiles']) == {'new-api'}


def test_database_nonfinite_values_are_json_safe_without_changing_storage(tmp_path):
    path = tmp_path / 'measurements.sqlite'
    values = [(float('inf'),), (float('-inf'),), (1.25,), (None,)]
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE measurements (value REAL)')
        db.executemany('INSERT INTO measurements VALUES (?)', values)
    result = query(ApplicationConfig((tmp_path,)), str(path),
                   sql='SELECT value FROM measurements ORDER BY rowid')
    assert result['rows'] == [['inf'], ['-inf'], [1.25], [None]]
    json.dumps(result, allow_nan=False)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT value FROM measurements ORDER BY rowid').fetchall() == values


def test_conversation_pages_are_stable_and_scoped(tmp_path):
    store = AgentStore(tmp_path, 'one')
    record = store.create('中文项目')
    for i in range(85):
        store.message(record['id'], 'user', str(i))
    first = store.messages(record['id'])
    second = store.messages(record['id'], before=first['before'])
    assert [x['text'] for x in first['messages']] == list(map(str, range(45,85)))
    assert [x['text'] for x in second['messages']] == list(map(str, range(5,45)))
    with pytest.raises(ValueError):
        AgentStore(tmp_path, 'two').conversation(record['id'])


def test_existing_conversation_database_migrates_without_losing_messages(tmp_path):
    with sqlite3.connect(tmp_path / 'conversations.sqlite3') as db:
        db.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, scope TEXT, title TEXT, created REAL, updated REAL, history TEXT DEFAULT '[]', plan TEXT DEFAULT '[]')")
        db.execute("INSERT INTO conversations (id,scope,title,created,updated) VALUES ('saved','one','保留的对话',1,1)")
    store = AgentStore(tmp_path, 'one')
    store.message('saved', 'user', '继续转换')
    assert store.messages('saved')['context'] == {}
    assert store.messages('saved')['messages'][0]['text'] == '继续转换'
    assert AgentStore(tmp_path, 'one').conversation('saved')['title'] == '保留的对话'


def test_one_sentence_conversion_binds_real_paths_and_returns_result(tmp_path, monkeypatch):
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    source = tmp_path / 'imported.dwg'
    source.write_bytes(b'fixture')
    calls = []

    async def conversion_rpc(_bridge, name, arguments):
        calls.append((name, dict(arguments)))
        assert name == 'auto_onboard_and_convert'
        assert arguments['source'] == str(source)
        assert arguments['project_dir'] == str(tmp_path / 'project')
        assert arguments['osm_place'] == 'Tomohon'
        run = Path(arguments['run_dir'])
        run.mkdir()
        manifest, delivery = run / 'run_manifest.json', run / 'delivery.gpkg'
        manifest.write_text('{}')
        delivery.write_bytes(b'fixture')
        return {'result': json.dumps({'run_manifest': str(manifest), 'delivery': str(delivery), 'run_status': 'CONDITIONAL'})}

    monkeypatch.setattr('cad2gis.desktop_agent.MCPToolBridge.call', conversion_rpc)

    async def scenario():
        settings = configured(tmp_path)
        agent = DesktopAgent(ApplicationConfig((tmp_path,), state_dir=settings.directory), settings, SimpleNamespace(active=''))
        turns = []

        async def stream(messages, info):
            turns.append(messages)
            assert {'convert_selected_drawing', 'list_project_files'} <= {tool.name for tool in info.function_tools}
            if len(turns) == 1:
                yield {0: DeltaToolCall(name='workspace_context', json_args='{}', tool_call_id='context')}
            elif len(turns) == 2:
                results = [p.content for m in messages for p in m.parts if getattr(p, 'tool_name', '') == 'workspace_context' and getattr(p, 'part_kind', '') == 'tool-return']
                assert results[-1]['paths']['project']['kind'] == 'planned'
                yield {0: DeltaToolCall(name='convert_selected_drawing', json_args='{}', tool_call_id='convert')}
            else:
                yield '转换已完成，可以打开结果核对地图。'

        agent.model_override = FunctionModel(stream_function=stream)
        started = agent.start({'message': '转换当前图纸', 'source': str(source), 'project': str(tmp_path / 'project'), 'run': str(tmp_path / 'run-1'), 'osm_place': 'Tomohon'})
        await asyncio.wait_for(agent.task, 40)
        state = agent.store.run(started['id'])
        assert state['status'] == 'succeeded', state['error']
        assert Path(state['result']['run_manifest']).is_file()
        assert state['result']['drawing'] == str(source)
        history = agent.store.messages(started['conversation'])
        assert history['result'] == state['result']
        assert history['context']['run'] == state['result']['run_dir']
        assert len(calls) == 1
    asyncio.run(scenario())


def test_actual_mcp_tool_roundtrip_and_recoverable_error(tmp_path, monkeypatch):
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    import traceback
    monkeypatch.setattr('cad2gis.desktop_agent.safe_ai_error', lambda exc: ''.join(traceback.format_exception(exc)))

    async def scenario():
        settings = configured(tmp_path)
        agent = DesktopAgent(ApplicationConfig((tmp_path,), state_dir=settings.directory), settings, SimpleNamespace(active=''))
        calls = []

        async def stream(messages, info):
            calls.append(messages)
            names = {t.name for t in info.function_tools}
            assert {'get_capabilities', 'workspace_context', 'write_project_json'} <= names
            step = len(calls)
            if step == 1:
                yield {0: DeltaToolCall(name='get_capabilities', json_args='{}', tool_call_id='cap')}
            elif step == 2:
                yield {0: DeltaToolCall(name='read_project_text', json_args=json.dumps({'path': str(tmp_path / 'missing.json')}), tool_call_id='missing')}
            elif step == 3:
                assert any(getattr(p,'part_kind','') == 'retry-prompt' for m in messages for p in m.parts)
                yield {0: DeltaToolCall(name='write_project_json', json_args=json.dumps({'path':str(tmp_path / 'evidence.json'), 'content':{'verified':True}}), tool_call_id='write')}
            else:
                yield '已检查工具并生成证据。'

        agent.model_override = FunctionModel(stream_function=stream)
        run = agent.start({'message':'检查能力，读取资料，创建证据'})
        await asyncio.wait_for(agent.task, 40)
        state = agent.store.run(run['id'])
        assert state['status'] == 'succeeded', state['error']
        assert len(agent.tools) >= 40
        assert json.loads((tmp_path / 'evidence.json').read_text()) == {'verified':True}
        assert len(calls) == 4
        assert state['usage']['tool_calls'] >= 2
        assert '已检查' in agent.store.messages(run['conversation'])['messages'][-1]['text']
    asyncio.run(scenario())


def test_cancel_preserves_completed_mutation_for_next_turn(tmp_path):
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall

    async def scenario():
        settings = configured(tmp_path)
        agent = DesktopAgent(ApplicationConfig((tmp_path,), state_dir=settings.directory), settings, SimpleNamespace(active=''))
        ready = asyncio.Event()
        calls = 0
        async def stream(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {0: DeltaToolCall(name='write_project_json', json_args=json.dumps({'path':str(tmp_path/'saved.json'),'content':{'done':1}}), tool_call_id='saved')}
            else:
                ready.set()
                await asyncio.sleep(60)
                yield 'unexpected'
        agent.model_override = FunctionModel(stream_function=stream)
        run = agent.start({'message':'创建一个证据文件'})
        await asyncio.wait_for(ready.wait(), 35)
        await agent.cancel(run['id'])
        assert agent.store.run(run['id'])['status'] == 'cancelled'
        history = agent.store.conversation(run['conversation'])['history']
        assert 'saved.json' in history and 'interrupted' in history
        assert (tmp_path/'saved.json').exists()
        assert not agent.active
    asyncio.run(scenario())


@pytest.mark.parametrize("keep_failing", [False, True])
def test_real_mcp_failure_reaches_model_retry_and_has_bounded_usage(tmp_path, keep_failing):
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall

    async def scenario():
        settings = configured(tmp_path)
        agent = DesktopAgent(ApplicationConfig((tmp_path,), state_dir=settings.directory), settings, SimpleNamespace(active=""))
        calls = 0

        async def stream(messages, _info):
            nonlocal calls
            calls += 1
            if calls > 1:
                retries = [part for message in messages for part in message.parts
                           if getattr(part, "part_kind", "") == "retry-prompt"]
                assert retries and "ARTIFACT_NOT_FOUND" in str(retries[-1].content)
                assert "recovery" in str(retries[-1].content)
            if calls == 1 or keep_failing:
                yield {0: DeltaToolCall(name="inspect_run", json_args=json.dumps({"run_dir": str(tmp_path / "missing-run")}),
                                       tool_call_id=f"missing-{calls}")}
            else:
                yield "该目录不存在，尚未取得转换结果。"

        agent.model_override = FunctionModel(stream_function=stream)
        run = agent.start({"message": "检查转换结果"})
        await asyncio.wait_for(agent.task, 40)
        state = agent.store.run(run["id"])
        assert state["status"] == ("failed" if keep_failing else "succeeded"), state
        assert calls == (3 if keep_failing else 2)
        assert state["usage"]["tool_attempts"] == (3 if keep_failing else 1)
        assert state["usage"]["requests"] == calls
        assert not any(event["kind"] == "tool_result" and event["name"] == "inspect_run" for event in state["events"])
        assert any(event["kind"] == "tool_error" and event["name"] == "inspect_run" for event in state["events"])
    asyncio.run(scenario())


def test_openai_http_protocol_probe_and_streamed_mcp_run(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from cad2gis.desktop_agent import test_connection

    # This is a loopback protocol fixture; a host's proxy must not affect it or
    # require optional SOCKS dependencies before the first local request.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            assert self.path == '/v1/chat/completions'
            assert self.headers['Authorization'] == 'Bearer test-private-key'
            tools = {t['function']['name'] for t in body['tools']}
            tool = 'connection_probe' if 'connection_probe' in tools else 'get_capabilities'
            completed = any(m['role'] == 'tool' for m in body['messages'])
            message = {'role':'assistant', 'content':'CAD2GIS_TOOL_READY' if tool == 'connection_probe' else '已读取 CAD2GIS 能力。'} if completed else {
                'role':'assistant','content':None,'tool_calls':[{'id':'probe','type':'function','function':{'name':tool,'arguments':'{}'}}]}
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
            self.end_headers()
            common = {'id':'fixture','created':1,'model':'fixture-model'}
            if body.get('stream'):
                delta = dict(message)
                if 'tool_calls' in delta:
                    delta['tool_calls'][0]['index'] = 0
                packet = {**common,'object':'chat.completion.chunk','choices':[{'index':0,'delta':delta,'finish_reason':None}]}
                self.wfile.write(('data: '+json.dumps(packet)+'\n\n').encode())
                packet['choices'] = [{'index':0,'delta':{},'finish_reason':'stop' if completed else 'tool_calls'}]
                self.wfile.write(('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode())
            else:
                packet = {**common,'object':'chat.completion','choices':[{'index':0,'message':message,'finish_reason':'stop' if completed else 'tool_calls'}],
                          'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
                self.wfile.write(json.dumps(packet).encode())
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        settings = configured(tmp_path)
        settings.save({'provider':'new-api','base_url':f'http://127.0.0.1:{server.server_port}/v1'})
        result = asyncio.run(test_connection(settings))
        assert result['connected'] and result['tool_calling'], result
        async def run():
            agent = DesktopAgent(ApplicationConfig((tmp_path,), state_dir=settings.directory),settings,SimpleNamespace(active=''))
            started = agent.start({'message':'读取真实工具能力'})
            await asyncio.wait_for(agent.task,35)
            result = agent.store.run(started['id'])
            assert result['status'] == 'succeeded', result
            assert any(e['name']=='get_capabilities' and e['kind']=='tool_result' for e in result['events'])
        asyncio.run(run())
        assert len(requests) == 4
        assert any(r.get('stream') for r in requests)
        assert requests[-1]['max_tokens'] == 4096
        assert 'max_completion_tokens' not in requests[-1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
