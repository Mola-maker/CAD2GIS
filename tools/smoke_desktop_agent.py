"""Exercise a packaged application's AI HTTP/streaming/MCP chain with a local protocol fixture."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit, parse_qs


def run(bundle: Path, output: Path):
    import httpx
    output.mkdir(parents=True, exist_ok=False)
    projects = output / 'projects'
    projects.mkdir()
    database = projects / 'evidence.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE evidence (id INTEGER PRIMARY KEY, label TEXT)')
        db.executemany('INSERT INTO evidence VALUES (?,?)',[(i,f'对象 {i}') for i in range(105)])
    requests = []
    cancel_waiting, release_cancel = threading.Event(), threading.Event()
    fixture_text = projects / 'notes.txt'
    fixture_text.write_text('Windows 中文 AI 输入输出核验', encoding='utf-8')
    steps = [
        ('workspace_context', {}),
        ('list_project_files', {}),
        ('read_project_text', {'path': str(projects / 'missing.txt')}),
        ('read_project_text', {'path': str(fixture_text)}),
        ('write_project_json', {'path': str(projects / 'written.json'), 'content': {'checked': True}}),
        ('query_project_database', {'path': str(database)}),
        ('get_capabilities', {}),
    ]
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            names = {x['function']['name'] for x in body['tools']}
            returned = [message for message in body['messages'] if message['role'] == 'tool']
            mode = next((mode for mode in ('FIXTURE_CANCEL', 'FIXTURE_FAILURE')
                         if any(mode in str(message.get('content', '')) for message in body['messages']
                                if message['role'] == 'user')), 'success')
            if 'connection_probe' in names:
                selected = [('connection_probe', {})]
                mode = 'probe'
            elif mode == 'FIXTURE_CANCEL':
                selected = [('write_project_json', {'path': str(projects / 'before-cancel.json'), 'content': {'retained': True}})]
            elif mode == 'FIXTURE_FAILURE':
                selected = [('workspace_context', {})]
                if returned:
                    self.send_response(503)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': {'message': 'local-fixture-key provider failure', 'type': 'server_error'}}).encode())
                    return
            else:
                selected = steps
            completed = len(returned) >= len(selected)
            if completed:
                message = {'role': 'assistant', 'content': 'CAD2GIS_TOOL_READY / 已读取实际工具能力。'}
            else:
                tool, arguments = selected[len(returned)]
                assert tool in names
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': f'{mode}-{len(returned)}', 'type': 'function',
                    'function': {'name': tool, 'arguments': json.dumps(arguments, ensure_ascii=False)},
                }]}
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
            self.end_headers()
            packet = {'id':'fixture','created':1,'model':'fixture-model'}
            if body.get('stream'):
                if not completed:
                    message['tool_calls'][0]['index']=0
                packet.update(object='chat.completion.chunk',choices=[{'index':0,'delta':message,'finish_reason':None}])
                self.wfile.write(('data: '+json.dumps(packet)+'\n\n').encode())
                self.wfile.flush()
                if mode == 'FIXTURE_CANCEL' and completed:
                    cancel_waiting.set()
                    release_cancel.wait(40)
                    return
                packet['choices']=[{'index':0,'delta':{},'finish_reason':'stop' if completed else 'tool_calls'}]
                self.wfile.write(('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode())
            else:
                packet.update(object='chat.completion',choices=[{'index':0,'message':message,'finish_reason':'stop' if completed else 'tool_calls'}],usage={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15})
                self.wfile.write(json.dumps(packet).encode())
    model = ThreadingHTTPServer(('127.0.0.1',0),Model)
    thread = threading.Thread(target=model.serve_forever,daemon=True)
    thread.start()
    env = dict(os.environ)
    for key in ('PYTHONPATH','PYTHONHOME','CAD2GIS_BACKEND_PATH',
                'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(key,None)
    env['CAD2GIS_BUNDLE_ROOT']=str(bundle)
    process = None
    try:
        with (output/'stderr.log').open('w',encoding='utf-8') as errors:
            python = bundle / ('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
            identity = json.loads(subprocess.check_output([str(python), '-I', '-B', '-X', 'utf8', '-c',
                "import json; from cad2gis.mcp_diagnostics import diagnose; d=diagnose(scope='identity',tool_schemas=[]); print(json.dumps({'status':d['status'],'differences':d['identity_differences']}))"],env=env,text=True,encoding='utf-8',stdin=subprocess.DEVNULL,timeout=40))
            assert identity['status'] == 'ok', identity
            process = subprocess.Popen([str(python),'-I','-B','-X','utf8','-m','cad2gis.application','--project-root',str(projects),'--state-dir',str(output/'private-state'),'--no-browser'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf-8',env=env)
            lines = queue.Queue()
            threading.Thread(target=lambda:[lines.put(line) for line in process.stdout],daemon=True).start()
            announcement = lines.get(timeout=40).strip().removeprefix('CAD2GIS: ')
            parsed = urlsplit(announcement)
            base = f'{parsed.scheme}://{parsed.netloc}'
            with httpx.Client(base_url=base,headers={'Origin':base},timeout=60,trust_env=False) as client:
                def post(path,data):
                    r=client.post('/_app/api/'+path,json=data)
                    r.raise_for_status()
                    return r.json()
                def get(path):
                    r=client.get('/_app/api/'+path)
                    r.raise_for_status()
                    return r.json()
                def wait_run(identifier):
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        state = get('agent/' + identifier)
                        if state['status'] != 'running':
                            return state
                        time.sleep(.2)
                    raise AssertionError(f'Agent did not finish: {identifier}')

                def assert_pair(state, call_id, name, terminal='tool_result'):
                    registered = [event for event in state['events'] if event.get('tool_call_id') == call_id]
                    assert [(event['kind'], event['name']) for event in registered] == [
                        ('tool_call', name), (terminal, name)], registered
                    assert all(event['run_id'] == state['id'] for event in registered)
                    return registered[-1]
                post('login',{'token':parse_qs(parsed.fragment)['token'][0]})
                post('ai/config',{'provider':'new-api','base_url':f'http://127.0.0.1:{model.server_port}/v1','api_key':'local-fixture-key','model':'fixture-model','token_field':'max_tokens'})
                probe=post('ai/test',{})
                assert probe['connected'] and probe['tool_calling'],probe
                started=post('agent',{'message':'读取实际 CAD2GIS 工具能力'})
                state = wait_run(started['id'])
                assert state['status']=='succeeded',state
                for index, (tool, _) in enumerate(steps):
                    assert_pair(state, f'success-{index}', tool, 'tool_error' if index == 2 else 'tool_result')
                assert 'Windows 中文 AI 输入输出核验' in assert_pair(state, 'success-3', 'read_project_text')['detail']
                assert json.loads((projects / 'written.json').read_text()) == {'checked': True}
                agent_requests = [body for body in requests if any(tool['function']['name'] == 'get_capabilities' for tool in body['tools'])]
                advertised = {tool['function']['name'] for tool in agent_requests[0]['tools']}
                assert len(advertised) == 66, len(advertised)
                assert {'publish_matched_delivery', 'start_review_server', 'verify_qgis_review', 'convert_selected_drawing'} <= advertised
                assert '"ctx"' not in json.dumps(agent_requests[0]['tools'])
                assert '已读取' in get('conversations/'+started['conversation'])['messages'][-1]['text']
                cancelled_start = post('agent', {'message': 'FIXTURE_CANCEL 保存后等待取消'})
                assert cancel_waiting.wait(40), 'Provider stream did not reach cancellable state'
                try:
                    post('agent/' + cancelled_start['id'] + '/cancel', {})
                finally:
                    release_cancel.set()
                cancelled = wait_run(cancelled_start['id'])
                assert cancelled['status'] == 'cancelled', cancelled
                assert_pair(cancelled, 'FIXTURE_CANCEL-0', 'write_project_json')
                assert json.loads((projects / 'before-cancel.json').read_text()) == {'retained': True}
                failed_start = post('agent', {'message': 'FIXTURE_FAILURE 检查错误状态'})
                failed = wait_run(failed_start['id'])
                assert failed['status'] == 'failed' and failed['error'], failed
                assert_pair(failed, 'FIXTURE_FAILURE-0', 'workspace_context')
                for name, outcome in (('success', state), ('cancelled', cancelled), ('failed', failed)):
                    assert 'local-fixture-key' not in json.dumps(outcome)
                    (output / f'agent-{name}.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding='utf-8')
                page=post('database/query',{'path':str(database),'table':'evidence','page':3})
                assert len(page['rows'])==5 and not page['has_more']
                assert len(get('docs/architecture')['content'])>200
                assert 'local-fixture-key' not in json.dumps(get('ai/config'))
                post('shutdown',{})
            process.wait(timeout=20)
            assert process.returncode==0
        result={'bundle':str(bundle),'identity':identity,'model_fixture':'local OpenAI-compatible HTTP; no external AI account used','ai_probe':probe,'agent_status':state['status'],'tool_calls':state['usage']['tool_calls'],'registered_model_tools':len(advertised),'mcp_tools':len(advertised)-7,'registered_call_pairs':len(steps),'cancel_status':cancelled['status'],'provider_failure_status':failed['status'],'http_requests':len(requests),'database_page_3_rows':len(page['rows']),'shutdown':'clean'}
        (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        release_cancel.set()
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        model.shutdown()
        model.server_close()
        thread.join(5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.bundle.resolve(),args.output.resolve())
