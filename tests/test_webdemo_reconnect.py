"""Exercise live review reconnect and competing HTTP refresh responses."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


APP = Path(__file__).resolve().parents[1] / "src/cad2gis/webdemo/app.js"


def _node(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for WebDemo behavior tests")
    result = subprocess.run(
        [node, "-e", script, str(APP)], check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_reconnect_refreshes_missed_controls_before_reporting_synchronized():
    result = _node(r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sockets = [], timers = [], labels = new Map();
let visible = ['gcp:1'], stored = ['gcp:1'], calls = 0;
class Socket {
  static OPEN = 1;
  constructor() { this.readyState = 1; sockets.push(this); }
  close() { this.readyState = 3; this.onclose(); }
}
const context = vm.createContext({
  window: {}, location: {protocol:'http:', host:'localhost'},
  applicationReviewId: null, reviewPageExpired: false,
  $: id => {if (!labels.has(id)) labels.set(id, {}); return labels.get(id);},
  terminalEvent: () => {}, setTimeout: fn => timers.push(fn), WebSocket: Socket,
  loadControls: async () => {calls++; visible = [...stored]; return true;},
});
const body = source.slice(source.indexOf('const connectSocket ='), source.indexOf('const boot ='));
vm.runInContext(body + '\nconnectSocket();', context);
(async () => {
  sockets[0].onopen();
  await sockets[0].onmessage({data: JSON.stringify({type:'connected'})});
  sockets[0].close();
  stored = ['gcp:2', 'gcp:3']; // Also verifies a missed deletion.
  timers.shift()();
  sockets[1].onopen();
  const duringHandshake = labels.get('#connection-label').textContent;
  await sockets[1].onmessage({data: JSON.stringify({type:'connected'})});
  process.stdout.write(JSON.stringify({calls, visible, stored, duringHandshake,
    status: labels.get('#connection-label').textContent}));
})().catch(error => {console.error(error); process.exitCode = 1;});
""")
    assert result["calls"] == 2
    assert result["visible"] == result["stored"] == ["gcp:2", "gcp:3"]
    assert result["duringHandshake"] != "实时同步"
    assert result["status"] == "实时同步"


def test_older_control_refresh_cannot_overwrite_newer_response():
    result = _node(r"""
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const pending = [], renders = [];
const context = vm.createContext({
  fetchJSON: () => new Promise(resolve => pending.push(resolve)),
  ol: {proj: {fromLonLat: value => value}},
  renderControls: registration => renders.push(registration.marker),
});
const body = source.slice(source.indexOf('let controlsRequest ='), source.indexOf('const nearestCadEvidence ='));
vm.runInContext('let controls = [];\n' + body, context);
(async () => {
  const old = vm.runInContext('loadControls()', context);
  const current = vm.runInContext('loadControls()', context);
  const collection = id => ({features: [{id, geometry:{coordinates:[1,2]}, properties:{_kind:'cad_map_gcp',cad_x:1,cad_y:2}}]});
  pending[2](collection('new')); pending[3]({controls:[],marker:'new'});
  await current;
  pending[0](collection('old')); pending[1]({controls:[],marker:'old'});
  await old;
  process.stdout.write(JSON.stringify({renders, id:vm.runInContext('controls[0].id',context)}));
})().catch(error => {console.error(error); process.exitCode=1;});
""")
    assert result == {"renders": ["new"], "id": "new"}


def test_obsolete_socket_refresh_cannot_report_newer_pending_state_as_synchronized():
    result = _node(r"""
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sockets = [], pending = [], labels = new Map();
class Socket {
  static OPEN = 1;
  constructor() { this.readyState = 1; sockets.push(this); }
}
const context = vm.createContext({
  window: {}, location: {protocol:'http:', host:'localhost'}, WebSocket: Socket,
  applicationReviewId: null, reviewPageExpired: false,
  $: id => {if (!labels.has(id)) labels.set(id, {}); return labels.get(id);},
  terminalEvent: () => {}, setTimeout: () => {},
  loadControls: () => new Promise(resolve => pending.push(resolve)),
});
const body = source.slice(source.indexOf('const connectSocket ='), source.indexOf('const boot ='));
vm.runInContext(body + '\nconnectSocket();', context);
(async () => {
  sockets[0].onopen();
  const first = sockets[0].onmessage({data: JSON.stringify({type:'connected'})});
  const latest = sockets[0].onmessage({data: JSON.stringify({type:'review_event'})});
  pending[0](false); await first;
  const obsolete = labels.get('#connection-label').textContent;
  pending[1](true); await latest;
  process.stdout.write(JSON.stringify({obsolete, latest:labels.get('#connection-label').textContent}));
})().catch(error => {console.error(error); process.exitCode=1;});
""")
    assert result["obsolete"] == "正在同步"
    assert result["latest"] == "实时同步"
