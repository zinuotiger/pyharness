"""Protocol simulation is distinct from opt-in real provider verification."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyharness.application.codex_runner import CodexRunner
from pyharness.application.platform_models import AgentDefinition
from pyharness.integrations.codex_transport import CodexTransport, CodexDisconnected


async def test_real_stdio_framing_disconnect_and_close(tmp_path):
    import sys
    server = tmp_path/'server.py'
    server.write_text('''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='stop': break
 print(json.dumps({'id':m['id'],'result':{'ok':True}}),flush=True)
''')
    client = CodexTransport([sys.executable, str(server)], timeout=2)
    assert (await client.connect())['ok']
    assert (await client.request('test', {}))['ok']
    with pytest.raises(CodexDisconnected):
        await client.request('stop', {})
    await client.close()
    await client.close()
    assert client.process.returncode is not None


async def test_inherited_mcp_is_disabled_before_model_thread(tmp_path,monkeypatch):
    import sys
    from pyharness.integrations import codex_transport
    script=tmp_path/'cli.py'
    launch=tmp_path/'launch.json'
    script.write_text('''import json,sys,pathlib
args=sys.argv[1:]
if '--version' in args: print('codex-cli 0.162.1');sys.exit()
if 'mcp' in args:
 print(json.dumps([{'name':'foreign','enabled':not any('mcp_servers.foreign=' in x for x in args)}]));sys.exit()
pathlib.Path(sys.argv[0]).with_name('launch.json').write_text(json.dumps(args))
for line in sys.stdin:
 value=json.loads(line)
 if 'id' in value: print(json.dumps({'id':value['id'],'result':{}}),flush=True)
''')
    monkeypatch.setattr(codex_transport,'codex_command',lambda *args:[sys.executable,str(script),'app-server'])
    async with CodexTransport() as client:
        assert client.disabled_mcp_servers==('foreign',)
    args=json.loads(launch.read_text())
    assert any(x.startswith('mcp_servers.foreign={enabled=false,') for x in args)
    assert 'features.apps=false' in args


class Feed:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.sent = []

    async def next_event(self):
        return next(self.rows)

    async def send(self, item):
        self.sent.append(item)


def runner(rows):
    obj = object.__new__(CodexRunner)
    obj.transport = Feed(rows)
    obj.thread_id, obj.turn_id, obj.task_id = 'thr', 'turn', 'task'
    obj.runtime = SimpleNamespace(definition=AgentDefinition(name='test'), command_results={})
    obj.facts = []
    async def fact(action, record):
        obj.facts.append((action, record))
    obj._fact = fact
    return obj


def notification(method, **params):
    return {'method':method,'params':{'threadId':'thr','turnId':'turn',**params}}


@pytest.mark.parametrize(('status','reason'), [('completed','complete'),('failed','failed'),('interrupted','cancelled')])
async def test_terminal_mapping_and_no_reasoning_leak(status, reason):
    rows = [notification('item/completed', item={'id':'secret','type':'reasoning','text':'PRIVATE'}),
            notification('item/completed', item={'id':'a','type':'agentMessage','text':'answer'}),
            notification('item/completed', item={'id':'a','type':'agentMessage','text':'answer'}),
            notification('turn/completed',turn={'id':'turn','status':status})]
    obj = runner(rows)
    messages = []
    async def append(*args, **kwargs):
        messages.append(args)
    result = await obj._stream(SimpleNamespace(session=SimpleNamespace(append=append)))
    assert result.reason == reason
    assert 'PRIVATE' not in json.dumps(obj.facts)
    assert len([f for f in obj.facts if f[0]=='item_completed']) == 1
    assert len(messages) == (1 if status == 'completed' else 0)


async def test_permission_escalation_is_never_granted():
    obj = runner([])
    await obj._server_request({'id':42,'method':'item/permissions/requestApproval',
        'params':{'permissions':{'network':{'enabled':True}}}}, None)
    assert obj.transport.sent[-1]['result']['permissions'] == {}


async def test_governed_codex_queue_artifacts_resume_and_cancel(tmp_path, monkeypatch):
    from pyharness import cli
    from pyharness.application.service import ApplicationService
    from pyharness.application import codex_runner
    from pyharness.web import settings_for
    from pyharness.integrations import codex_transport
    class Server:
        instances = []
        block = False
        def __init__(self, **kwargs):
            self.queue = asyncio.Queue()
            self.requests = []
            self.closed = False
            self.instances.append(self)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): self.closed = True
        async def request(self, method, params):
            self.requests.append((method,params))
            if method in {'thread/start','thread/resume'}:
                return {'thread':{'id':'thr'}}
            if method == 'turn/interrupt':
                return {}
            if method == 'turn/start':
                assert params['sandboxPolicy']['networkAccess'] is False
                assert params['approvalPolicy']=='untrusted'
                if not self.block:
                    Path(params['cwd'],'result.txt').write_text('actual private output')
                    for row in [notification('item/started',item={'id':'cmd','type':'commandExecution'}),
                        notification('item/completed',item={'id':'cmd','type':'commandExecution','exitCode':0,'aggregatedOutput':'1 passed'}),
                        notification('item/completed',item={'id':'answer','type':'agentMessage','text':'complete'}),
                        notification('turn/completed',turn={'id':'turn','status':'completed'})]:
                        self.queue.put_nowait(row)
                return {'turn':{'id':'turn'}}
            return {}
        async def next_event(self): return await self.queue.get()
        async def send(self,item): pass
    monkeypatch.setattr(codex_runner,'CodexTransport',Server)
    monkeypatch.setattr(codex_transport,'codex_command',lambda *args:['codex','app-server'])
    # Normal Web startup has no configured PyHarness model or credential.
    service = ApplicationService(cli.assemble_ctx(settings_for(tmp_path)))
    async def approve(run):
        for _ in range(200):
            pending = await service.pending_approvals()
            for item in pending['pending']:
                if item['session_id']==run['session_id'] and item['tool']=='codex.run':
                    await service.decide_approval(item['approval_id'],'approve',sid=run['session_id'])
                    return
            await asyncio.sleep(0.01)
        raise AssertionError('Missing governed external executor approval')
    try:
        p = service.platform
        agent = p.save_agent({'name':'Codex','executor':'codex','permission_policy':'standard',
            'codex_tools':'native',
            'allowed_tools':['codex.run'],'sandbox_profile':{'mode':'host_approved'}})
        p.publish_agent(agent['agent_id'])
        run = await p.start_run({'text':'produce result','agent_id':agent['agent_id'],'agent_version':1})
        await approve(run)
        assert (await service._queues[run['session_id']].wait_for(run['task_id'])).ok
        detail = await p.run(run['run_id'])
        assert detail['executor']=='codex' and detail['result_summary']=='complete'
        assert 'result.txt' in [a['filename'] for a in detail['artifacts']]
        assert 'test-report.json' in [a['filename'] for a in detail['artifacts']]
        assert Server.instances[0].closed
        Server.block = True
        second = await p.start_run({'text':'wait','session_id':run['session_id']})
        await approve(second)
        for _ in range(100):
            if len(Server.instances)>1 and any(method=='turn/start' for method,params in Server.instances[-1].requests): break
            await asyncio.sleep(0.01)
        await p.cancel(second['run_id'])
        result = await service._queues[second['session_id']].wait_for(second['task_id'])
        assert not result.ok and result.code=='cancelled'
        assert any(method=='thread/resume' for method,params in Server.instances[-1].requests)
        assert any(method=='turn/interrupt' for method,params in Server.instances[-1].requests)
        assert Server.instances[-1].closed
        assert (await p.run(second['run_id']))['status']=='cancelled'
    finally:
        await service.shutdown()
