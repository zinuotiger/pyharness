"""Remote acceptance contracts runnable locally without Docker or paid models."""
import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from scripts.platform_real_model_smoke import parser,smoke
from tests.unit.test_platform_safety import app
from pyharness.errors import PyHError
from pyharness.application.platform_projection import project


@pytest.mark.controlled_process
def test_browser_synthetic_windows_known_folders_exist(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from scripts.ci_verify import environment
    env=environment(tmp_path/'isolated')
    home=Path(env['USERPROFILE'])
    assert Path(env['LOCALAPPDATA'])==home/'AppData/Local'
    assert Path(env['APPDATA'])==home/'AppData/Roaming'
    assert Path(env['LOCALAPPDATA']).is_dir() and Path(env['APPDATA']).is_dir()
    if os.name=='nt':
        code='import ctypes; b=ctypes.create_unicode_buffer(260); result=ctypes.windll.shell32.SHGetFolderPathW(None,28,None,0,b); assert result==0,result; print(b.value)'
        result=subprocess.check_output([sys.executable,'-B','-c',code],env=env,text=True).strip()
        assert Path(result).resolve()==Path(env['LOCALAPPDATA']).resolve()


def test_browser_uses_existing_chrome_before_edge_on_hosted_windows(tmp_path,monkeypatch):
    from scripts.platform_browser_smoke import find_browser
    monkeypatch.setenv('PROGRAMFILES',str(tmp_path));monkeypatch.delenv('PROGRAMFILES(X86)',raising=False)
    chrome=tmp_path/'Google/Chrome/Application/chrome.exe';edge=tmp_path/'Microsoft/Edge/Application/msedge.exe'
    for path in (chrome,edge):path.parent.mkdir(parents=True);path.write_bytes(b'')
    assert find_browser()==str(chrome)
    chrome.unlink()
    assert find_browser()==str(edge)

def options(*extra):
    return parser().parse_args(['--execute','--base-url','http://127.0.0.1:1/v1','--model','synthetic','--key-env','SYNTHETIC_MODEL_KEY','--input-price','1','--output-price','2',*extra])

async def test_model_smoke_is_explicit_and_missing_credentials_refused():
    assert (await smoke(parser().parse_args([])))['reason']=='explicit_execute_required'
    assert (await smoke(options()))['reason']=='credential_missing'

async def test_model_smoke_fake_transport_one_call_bounded_and_no_secret(monkeypatch):
    secret='synthetic-private-value';monkeypatch.setenv('SYNTHETIC_MODEL_KEY',secret);seen=[]
    def handle(request):
        seen.append(request);body=json.loads(request.content)
        assert body['max_tokens']==128 and 'tools' not in body
        return httpx.Response(200,json={'choices':[{'message':{'content':secret}}],'usage':{'prompt_tokens':40,'completion_tokens':10}})
    result=await smoke(options(),transport=httpx.MockTransport(handle))
    assert len(seen)==1 and result['status']=='passed' and result['real_model'] is False
    assert secret not in json.dumps(result) and not result['response_content_logged']

@pytest.mark.parametrize('args,reason',[
    (['--max-tokens','999'],'budget_out_of_bounds'),(['--max-cost','0.001','--input-price','1000'],'estimated_budget_exceeded'),
    (['--input-price','nan'],'explicit_positive_prices_required'),(['--base-url','http://external.invalid'],'https_or_loopback_required')])
async def test_model_smoke_refuses_before_transport(args,reason):
    def forbidden(request):raise AssertionError('must not call transport')
    result=await smoke(options(*args),transport=httpx.MockTransport(forbidden))
    assert result['reason']==reason and result['calls']==0

async def test_model_smoke_transport_failure_is_redacted(monkeypatch):
    monkeypatch.setenv('SYNTHETIC_MODEL_KEY','synthetic-value')
    def fail(request):raise httpx.ConnectError('synthetic-value',request=request)
    result=await smoke(options(),transport=httpx.MockTransport(fail))
    assert result['status']=='failed' and 'synthetic-value' not in json.dumps(result)

async def test_model_smoke_timeout_and_no_retry(monkeypatch):
    monkeypatch.setenv('SYNTHETIC_MODEL_KEY','synthetic-value');calls=[]
    async def hang(request):calls.append(1);await asyncio.Event().wait()
    result=await smoke(options('--timeout','1'),transport=httpx.MockTransport(hang))
    assert result['status']=='failed' and len(calls)==1

async def test_failed_test_patch_cannot_be_applied_even_if_requested(app):
    p=app.service.platform;sid=await app.service.create_session();log=await app.service.require_session(sid)
    item=await p.record_generated(SimpleNamespace(session=log,task_id='synthetic'),'changes.patch',b'patch',
        manifest=[{'path':'add.py','before_sha256':None,'after':'fixed'}],validation_status='failed')
    with pytest.raises(PyHError,match='patch_validation_failed'):await p.request_artifact_action(item['artifact_id'],'apply')
    with pytest.raises(PyHError,match='patch_validation_failed'):
        await p.commit_artifact_action(item['artifact_id'],'workspace.apply_patch',SimpleNamespace(session=log))

async def test_major_read_apis_and_status_contracts(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as client:
        for endpoint in ('dashboard','sessions','runs','approvals','agents','artifacts','knowledge','sandboxes','usage','system/health','connections','schedules'):
            response=await client.get('/api/v1/'+endpoint)
            assert response.status_code==200,(endpoint,response.text)
            assert isinstance(response.json(),(dict,list))
        assert (await client.get('/api/v1/runs/missing')).status_code==404
        assert (await client.post('/api/v1/runs',json={'text':[]})).status_code==422


def test_trace_keeps_only_valid_typed_sandbox_id_and_redacts_commands():
    ident='ph-'+'a'*32
    event=SimpleNamespace(type='platform.sandbox',payload={'action':'executed','record':{'sandbox_id':ident},
        'command_summary':'api_key=sk-'+'b'*40,'exit_code':0},seq=1,task_id='t',ts='2026-09-26T00:00:00Z',trace=None)
    trace=project('s',[event])['trace'][0]
    assert trace['sandbox_id']==ident and 'sk-' not in trace['output_summary']
    event.payload['record']['sandbox_id']='secret-value'
    assert project('s',[event])['trace'][0]['sandbox_id'] is None


@pytest.mark.parametrize('result',[{'exit_code':1},{'running':True},{'cancelled':True},{'error_code':'failed'},
                                 {'running':True,'exit_code':0},{'cancelled':True,'exit_code':0},
                                 {'error_code':'failed','exit_code':0},{'exit_code':None},
                                 {'exit_code':'0'},{'exit_code':False}])
async def test_background_failure_or_incomplete_marks_patch_unapplicable(tmp_path,monkeypatch,result):
    from pyharness.application.platform_runtime import PlatformRuntime
    from pyharness.application.platform_models import AgentDefinition,SandboxProfile
    from pyharness.core.sandbox import SandboxManager
    from tests.unit.test_platform_service import FakeBackend
    manager=SandboxManager(tmp_path/'sandboxes',docker=FakeBackend())
    rec=await manager.create('s~t',SandboxProfile(mode='isolated'))
    (manager.workspace(rec.sandbox_id)/'code.py').write_text('after')
    captured=[]
    async def generated(ctx,name,raw,**kwargs):captured.append((name,kwargs))
    async def append(*args,**kwargs):pass
    platform=SimpleNamespace(sandboxes=manager,record_generated=generated)
    runtime=PlatformRuntime(platform,AgentDefinition(name='synthetic'),'s')
    runtime.sandboxes['t']=rec.sandbox_id;runtime.baselines['t']={'code.py':'before'}
    runtime.process_tokens['t']=['owned-process']
    monkeypatch.setattr(manager,'process_status',lambda ident,token:result)
    try:
        await runtime.finish(SimpleNamespace(task_id='t',session=SimpleNamespace(append=append)))
        assert next(meta for name,meta in captured if name=='changes.patch')['validation_status']=='failed'
    finally:await manager.close()


async def test_background_validation_is_captured_before_snapshot_pause(tmp_path,monkeypatch):
    from pyharness.application.platform_runtime import PlatformRuntime
    from pyharness.application.platform_models import AgentDefinition,SandboxProfile
    from pyharness.core.sandbox import SandboxManager
    from tests.unit.test_platform_service import FakeBackend
    from contextlib import asynccontextmanager
    manager=SandboxManager(tmp_path/'sandboxes',docker=FakeBackend())
    rec=await manager.create('s~t',SandboxProfile(mode='isolated'))
    (manager.workspace(rec.sandbox_id)/'code.py').write_text('after')
    result={'running':True};captured=[];order=[]
    def status(ident,token):order.append('status');return dict(result)
    @asynccontextmanager
    async def snapshot(ident):
        order.append('snapshot');result.clear();result.update(running=False,exit_code=0)
        yield manager.workspace(ident)
    async def generated(ctx,name,raw,**kwargs):captured.append((name,raw,kwargs))
    async def append(*args,**kwargs):
        # Session append also yields; every owned token must be sampled first.
        result.clear();result.update(running=False,exit_code=0)
    runtime=PlatformRuntime(SimpleNamespace(sandboxes=manager,record_generated=generated),AgentDefinition(name='synthetic'),'s')
    runtime.sandboxes['t']=rec.sandbox_id;runtime.baselines['t']={'code.py':'before'};runtime.process_tokens['t']=['owned-process','second-owned-process']
    monkeypatch.setattr(manager,'process_status',status);monkeypatch.setattr(manager,'snapshot',snapshot)
    try:
        await runtime.finish(SimpleNamespace(task_id='t',session=SimpleNamespace(append=append)))
        assert order==['status','status','snapshot']
        assert next(meta for name,raw,meta in captured if name=='changes.patch')['validation_status']=='failed'
        report=json.loads(next(raw for name,raw,meta in captured if name=='test-report.json'))
        assert len(report)==2 and all(c['exit_code']==-1 and c['incomplete'] for c in report)
    finally:await manager.close()
