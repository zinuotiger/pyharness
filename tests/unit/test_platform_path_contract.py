"""Parameter rejection occurs before approvals, allocation and providers."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from pyharness import cli
from pyharness.core import llm
from pyharness.core.sandbox import SandboxManager
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings
from tests.unit.test_llm import FakeTransport, _raw, _usage
from tests.unit.test_platform_service import FakeBackend


def response(path, n=1):
    call=SimpleNamespace(id='path-'+str(n),type='function',function=SimpleNamespace(name='fs_read_file',arguments=json.dumps({'path':path})))
    return _raw('',tool_calls=[call],usage=_usage(20,8))


async def setup(tmp_path,monkeypatch,responses):
    cfg=isolated_settings(tmp_path);cfg.llm.retry.attempts=1;cfg.llm.degrade.enabled=False
    transport=FakeTransport()
    async def complete(req):
        transport.requests.append(req)
        return responses[min(len(transport.requests)-1,len(responses)-1)]
    transport.complete=complete
    monkeypatch.setattr(llm,'build_client',lambda *args:transport)
    app=DesktopApp(cli.assemble_ctx(cfg));service=app.service;p=service.platform
    service.save_model_profile({'id':'synthetic','model':cfg.llm.model,'base_url':'https://synthetic.invalid','api_key':'synthetic-path-key'})
    backend=FakeBackend();p.sandboxes=SandboxManager(tmp_path/'sandboxes',docker=backend)
    sid=await service.create_session();root=Path(service._session_workspace(sid));root.mkdir(parents=True,exist_ok=True)
    for name in ('calculator.py','test_calculator.py','README.md'):(root/name).write_text('synthetic-original')
    log=await service.require_session(sid)
    await log.append('session.renamed',{'new_title':'Synthetic path test','by':'user'},actor='user')
    agent=p.save_agent({'name':'relative file test','allowed_tools':['fs.read_file'],
        'sandbox_profile':{'mode':'isolated'},'approval_rules':['approve:fs.read_file'],'max_rounds':3})
    p.publish_agent(agent['agent_id'])
    return app,transport,backend,sid,root,agent


@pytest.mark.parametrize('path,recoverable',[
    ('/workspace/calculator.py',True),('/workspace/test_calculator.py',True),
    ('../calculator.py',False),('C:\\users\\synthetic',False),('\\\\server\\share',False),
    ('file:///synthetic',False),('/etc/passwd',False),('/workspace/../etc/passwd',False),
])
async def test_invalid_path_zero_approval_provider_container_side_effect(tmp_path,monkeypatch,path,recoverable):
    app,transport,backend,sid,root,agent=await setup(tmp_path,monkeypatch,[response(path),_raw('Stopped safely',usage=_usage(8,2))])
    try:
        p=app.service.platform
        run=await p.start_run({'session_id':sid,'agent_id':agent['agent_id'],'agent_version':1,'text':'Read sample'})
        await asyncio.wait_for(app.service._queues[sid].wait_for(run['task_id']),10)
        events=list((await app.service.require_session(sid)).events_after(0))
        assert len(transport.requests)==(2 if recoverable else 1)
        assert not any(e.type in ('approval.requested','tool.call','tool.result','platform.artifact','platform.sandbox') for e in events)
        error=next(e.payload for e in events if e.type=='tool.error')
        safe=json.dumps(error)
        assert 'invalid_workspace_path' in safe and 'calculator.py' in safe
        assert str(tmp_path) not in safe and 'synthetic-path-key' not in safe and path not in safe
        assert not backend.roots and backend.executions==0 and not p.sandboxes.records
        assert all(f.read_text()=='synthetic-original' for f in root.iterdir())
    finally:await app.service.shutdown()


async def test_one_format_correction_reaches_normal_approval_with_two_requests(tmp_path,monkeypatch):
    app,transport,backend,sid,root,agent=await setup(tmp_path,monkeypatch,[response('/workspace/calculator.py'),response('calculator.py',2)])
    requested=asyncio.Event()
    async def observe(*args):requested.set()
    app.ctx.bus.subscribe('approval.requested',observe,owner='path-test')
    try:
        p=app.service.platform
        run=await p.start_run({'session_id':sid,'agent_id':agent['agent_id'],'agent_version':1,'text':'Read sample'})
        await asyncio.wait_for(requested.wait(),10)
        assert (await p.run(run['run_id']))['status']=='waiting_approval'
        assert len(transport.requests)==2 and transport.pings==0 and not backend.roots
        assert any('invalid_workspace_path' in str(m.get('content')) for m in transport.requests[1]['messages'] if m['role']=='tool')
        assert 'Relative workspace path only' in json.dumps(transport.requests[1]['tools'])
        await p.cancel(run['run_id'])
        assert not backend.roots and (root/'calculator.py').read_text()=='synthetic-original'
    finally:await app.service.shutdown()


async def test_repeated_format_error_is_terminal_after_one_correction(tmp_path,monkeypatch):
    app,transport,backend,sid,root,agent=await setup(tmp_path,monkeypatch,[response('/workspace/calculator.py'),response('/workspace/calculator.py',2)])
    try:
        run=await app.service.platform.start_run({'session_id':sid,'agent_id':agent['agent_id'],'agent_version':1,'text':'Read sample'})
        await asyncio.wait_for(app.service._queues[sid].wait_for(run['task_id']),10)
        assert (await app.service.platform.run(run['run_id']))['status']=='failed'
        assert len(transport.requests)==2 and not backend.roots
    finally:await app.service.shutdown()


@pytest.mark.parametrize('path',['calculator.py','.','/workspace','/workspace/calculator.py'])
def test_symlink_root_is_terminal_before_any_approval(tmp_path,monkeypatch,path):
    from pyharness.application.platform_runtime import PlatformRuntime
    from pyharness.application.platform_models import AgentDefinition
    root=tmp_path/'source';root.mkdir();(root/'calculator.py').write_text('original')
    real=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda p: p==root or real(p))
    runtime=PlatformRuntime(SimpleNamespace(service=SimpleNamespace(_session_workspace=lambda sid:root)),AgentDefinition(name='test'),'s')
    ctx=SimpleNamespace(task_id='t',session=SimpleNamespace(events_after=lambda seq:[]))
    result=runtime.validate_file_call('fs.list_dir' if path in ('.','/workspace') else 'fs.read_file',{'path':path},ctx)
    assert result['code']=='invalid_workspace_path' and result['recoverable'] is False
    assert not runtime.sandboxes and (root/'calculator.py').read_text()=='original'


async def test_relative_test_file_read_requires_approval_and_denial_has_no_effect(tmp_path,monkeypatch):
    app,transport,backend,sid,root,agent=await setup(tmp_path,monkeypatch,[response('test_calculator.py'),_raw('Denied safely',usage=_usage(8,2))])
    requested=asyncio.Event()
    async def observe(*args):requested.set()
    app.ctx.bus.subscribe('approval.requested',observe,owner='read-test')
    try:
        run=await app.service.platform.start_run({'session_id':sid,'agent_id':agent['agent_id'],'agent_version':1,'text':'Read sample'})
        await asyncio.wait_for(requested.wait(),10)
        assert (await app.service.platform.run(run['run_id']))['status']=='waiting_approval'
        log=await app.service.require_session(sid)
        pending=next(e for e in log.events_after(0) if e.type=='approval.requested')
        await app.service.decide_approval(pending.seq,'deny',sid=sid)
        await asyncio.wait_for(app.service._queues[sid].wait_for(run['task_id']),10)
        assert not backend.roots and backend.executions==0
        assert all(f.read_text()=='synthetic-original' for f in root.iterdir())
    finally:await app.service.shutdown()
