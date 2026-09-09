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
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            names = {x['function']['name'] for x in body['tools']}
            tool = 'connection_probe' if 'connection_probe' in names else 'get_capabilities'
            completed = any(m['role']=='tool' for m in body['messages'])
            message = {'role':'assistant','content':'CAD2GIS_TOOL_READY / 已读取实际工具能力。'} if completed else {'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':tool,'arguments':'{}'}}]}
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
            self.end_headers()
            packet = {'id':'fixture','created':1,'model':'fixture-model'}
            if body.get('stream'):
                if not completed:
                    message['tool_calls'][0]['index']=0
                packet.update(object='chat.completion.chunk',choices=[{'index':0,'delta':message,'finish_reason':None}])
                self.wfile.write(('data: '+json.dumps(packet)+'\n\n').encode())
                packet['choices']=[{'index':0,'delta':{},'finish_reason':'stop' if completed else 'tool_calls'}]
                self.wfile.write(('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode())
            else:
                packet.update(object='chat.completion',choices=[{'index':0,'message':message,'finish_reason':'stop' if completed else 'tool_calls'}],usage={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15})
                self.wfile.write(json.dumps(packet).encode())
    model = ThreadingHTTPServer(('127.0.0.1',0),Model)
    thread = threading.Thread(target=model.serve_forever,daemon=True)
    thread.start()
    env = dict(os.environ)
    for key in ('PYTHONPATH','PYTHONHOME','CAD2GIS_BACKEND_PATH'):
        env.pop(key,None)
    env['CAD2GIS_BUNDLE_ROOT']=str(bundle)
    process = None
    try:
        with (output/'stderr.log').open('w',encoding='utf-8') as errors:
            python = bundle / ('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')
            identity = json.loads(subprocess.check_output([str(python), '-I', '-B', '-X', 'utf8', '-c',
                "import json; from cad2gis.mcp_diagnostics import diagnose; d=diagnose(scope='identity',tool_schemas=[]); print(json.dumps({'status':d['status'],'differences':d['identity_differences']}))"],env=env,text=True,encoding='utf-8'))
            assert identity['status'] == 'ok', identity
            process = subprocess.Popen([str(python),'-I','-B','-X','utf8','-m','cad2gis.application','--project-root',str(projects),'--state-dir',str(output/'private-state'),'--no-browser'],stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf-8',env=env)
            lines = queue.Queue()
            threading.Thread(target=lambda:[lines.put(line) for line in process.stdout],daemon=True).start()
            announcement = lines.get(timeout=40).strip().removeprefix('CAD2GIS: ')
            parsed = urlsplit(announcement)
            base = f'{parsed.scheme}://{parsed.netloc}'
            with httpx.Client(base_url=base,headers={'Origin':base},timeout=60) as client:
                def post(path,data):
                    r=client.post('/_app/api/'+path,json=data)
                    r.raise_for_status()
                    return r.json()
                def get(path):
                    r=client.get('/_app/api/'+path)
                    r.raise_for_status()
                    return r.json()
                post('login',{'token':parse_qs(parsed.fragment)['token'][0]})
                post('ai/config',{'provider':'new-api','base_url':f'http://127.0.0.1:{model.server_port}/v1','api_key':'local-fixture-key','model':'fixture-model','token_field':'max_tokens'})
                probe=post('ai/test',{})
                assert probe['connected'] and probe['tool_calling'],probe
                started=post('agent',{'message':'读取实际 CAD2GIS 工具能力'})
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    state=get('agent/'+started['id'])
                    if state['status']!='running':
                        break
                    time.sleep(.2)
                assert state['status']=='succeeded',state
                assert any(e['name']=='get_capabilities' and e['kind']=='tool_result' for e in state['events'])
                assert '已读取' in get('conversations/'+started['conversation'])['messages'][-1]['text']
                page=post('database/query',{'path':str(database),'table':'evidence','page':3})
                assert len(page['rows'])==5 and not page['has_more']
                assert len(get('docs/architecture')['content'])>200
                assert 'local-fixture-key' not in json.dumps(get('ai/config'))
                post('shutdown',{})
            process.wait(timeout=20)
            assert process.returncode==0
        result={'bundle':str(bundle),'identity':identity,'model_fixture':'local OpenAI-compatible HTTP; no external AI account used','ai_probe':probe,'agent_status':state['status'],'tool_calls':state['usage']['tool_calls'],'http_requests':len(requests),'database_page_3_rows':len(page['rows']),'shutdown':'clean'}
        (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    finally:
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
