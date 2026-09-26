"""Bounded fault injection; all files/identities/backends are synthetic."""
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
import pytest
import httpx
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.platform_models import SandboxProfile
from pyharness.core.sandbox import SandboxManager,DockerSandboxBackend,safe_path
from pyharness.errors import PyHError
from tests.support import isolated_settings
from tests.unit.test_platform_service import FakeBackend


@pytest.fixture
async def app(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    yield app
    await app.service.shutdown()


async def test_api_status_contract_and_idempotent_cancel(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as c:
        assert (await c.get('/api/v1/runs/missing')).status_code==404
        assert (await c.post('/api/v1/runs',json={'text':'x','retry_count':7})).status_code==422
        assert (await c.post('/api/v1/runs',json={'text':['x']})).status_code==422
        r=(await c.post('/api/v1/runs',json={'text':'hello'})).json()
        await asyncio.wait_for(app.service._queues[r['session_id']].wait_for(r['task_id']),5)
        for _ in range(2): assert (await c.post('/api/v1/runs/'+r['run_id']+'/cancel')).status_code==200
        assert (await c.post('/api/v1/runs/'+r['run_id']+'/retry')).status_code==409
        usage=(await c.get('/api/v1/usage',params={'run_id':r['run_id']})).json()
        assert usage['run_count']==1 and usage['tokens']>0 and usage['success_rate']==1
        assert (await c.get('/api/v1/usage',params={'bad':'value'})).status_code==422


async def test_version_diff_deprecate_and_archive(app):
    p=app.service.platform
    draft=p.save_agent({'name':'test'});ident=draft['agent_id'];p.publish_agent(ident)
    p.save_agent({**draft,'system_prompt':'new policy'},ident);p.publish_agent(ident)
    assert 'new policy' in p.agent_action(ident,'diff',{'from':1,'to':2})['diff']
    p.agent_action(ident,'deprecate-version',{'version':1})
    with pytest.raises(PyHError): await p.start_run({'agent_id':ident,'agent_version':1,'text':'hi'})
    with pytest.raises(PyHError): await p.schedule_session({'agent_id':ident,'agent_version':1})
    p.agent_action(ident,'deprecate')
    with pytest.raises(PyHError): await p.start_run({'agent_id':ident,'agent_version':2,'text':'hi'})
    with pytest.raises(PyHError): await p.schedule_session({'agent_id':ident,'agent_version':2})
    assert p.version(ident,1)['snapshot']['system_prompt']==''
    sid=await app.service.create_session()
    await p.session_metadata(sid,{'archived':True,'tags':['test'],'title':'saved title'})
    row=next(s for s in (await p.sessions())['sessions'] if s['sid']==sid)
    assert row['archived'] and row['tags']==['test'] and row['title']=='saved title'
    await p.session_metadata(sid,{'archived':False})


async def test_schedule_binding_edit_failure_is_atomic(app,monkeypatch):
    p=app.service.platform
    sid=await p.schedule_session({})
    await app.service.schedule_action(sid,'add',name='daily',kind='interval',expr='3600',intent='safe intent')
    spine=app.service._engines[sid]
    assert spine.platform_runtime.definition.sandbox_profile.mode=='disabled'
    before=spine.schedule._jobs['daily']
    async def broken(*a,**k): raise OSError('injected persistence failure')
    with monkeypatch.context() as m:
        m.setattr(spine.session,'append',broken)
        with pytest.raises(OSError): await app.service.schedule_action(sid,'edit',name='daily',kind='interval',expr='7200',intent='changed')
    assert spine.schedule._jobs['daily'] is before
    await app.service.schedule_action(sid,'edit',name='daily',kind='interval',expr='7200',intent='changed')
    assert spine.schedule._jobs['daily'].expr=='7200'


async def test_artifact_persistence_failure_cleans_bytes(app,monkeypatch):
    p=app.service.platform;sid=await app.service.create_session();log=await app.service.require_session(sid)
    async def broken(*a,**k): raise OSError('injected log failure')
    monkeypatch.setattr(log,'append',broken)
    with pytest.raises(OSError): await p.upload({'session_id':sid,'filename':'input.txt','data_base64':base64.b64encode(b'synthetic').decode()})
    assert not list((p.root/'artifacts').iterdir())


async def test_docker_contract_is_nonroot_offline_bounded(tmp_path,monkeypatch):
    backend=DockerSandboxBackend(tmp_path/'config');calls=[]
    async def command(args,**kwargs):
        calls.append((args,kwargs));return {'exit_code':0,'output':'ok','ok':True}
    monkeypatch.setattr(backend,'_command',command)
    manager=SandboxManager(tmp_path/'sandboxes',docker=backend)
    record=await manager.create('run',SandboxProfile(mode='isolated',memory_mb=128,pids_limit=16))
    args=next(args for args,_ in calls if args[0]=='run')
    for flag in ('--network=none','--cap-drop=ALL','--read-only','--security-opt=no-new-privileges','--pull=never'):
        assert flag in args
    assert '--privileged' not in args and not any('docker.sock' in a for a in args)
    assert args[args.index('--memory')+1]=='128m' and args[args.index('--pids-limit')+1]=='16'
    assert any(a.startswith('--user=') and a!='--user=0:0' for a in args)
    await manager.execute(record.sandbox_id,'print(1)',python=True)
    assert calls[-1][1]['limit']==65536 and calls[-1][1]['timeout']==60
    await manager.close();assert not list((tmp_path/'sandboxes').iterdir())


async def test_backend_exec_failure_no_host_fallback(tmp_path):
    backend=FakeBackend('execute');manager=SandboxManager(tmp_path/'sandboxes',docker=backend)
    record=await manager.create('run',SandboxProfile(mode='isolated'))
    with pytest.raises(RuntimeError): await manager.execute(record.sandbox_id,'synthetic')
    assert not manager.backends['host_approved'].roots
    await manager.close()


async def test_process_cancel_and_cross_run_ownership(tmp_path):
    entered=asyncio.Event();cancelled=asyncio.Event()
    class Backend(FakeBackend):
        async def execute(self,*args,**kwargs):
            entered.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
    manager=SandboxManager(tmp_path/'sandboxes',docker=Backend())
    a,b=await asyncio.gather(*(manager.create(r,SandboxProfile(mode='isolated')) for r in ('a','b')))
    process=await manager.start_process(a.sandbox_id,'owned fake process')
    await asyncio.wait_for(entered.wait(),2)
    with pytest.raises(PyHError): manager.process_status(b.sandbox_id,process['pid'])
    result=await asyncio.wait_for(manager.kill_process(a.sandbox_id,process['pid']),3)
    assert result['cancelled'] and cancelled.is_set()
    await manager.close()


@pytest.mark.parametrize('name',['.env/../x','NUL','aux.txt','file.','x ','a:b','//server/share','a/../../secret'])
def test_reserved_and_secret_traversal(tmp_path,name):
    with pytest.raises(PyHError): safe_path(tmp_path,name)


def test_symlink_boundary(tmp_path):
    import os
    root=tmp_path/'root';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    try: os.symlink(outside,root/'link',target_is_directory=True)
    except OSError as exc:
        pytest.skip('OS does not grant symlink creation: '+str(exc.winerror))
    with pytest.raises(PyHError): safe_path(root,'link/secret')


async def test_knowledge_remote_failure_and_switch(app,monkeypatch):
    from pyharness.core.knowledge import RemoteRagKnowledgeProvider
    p=app.service.platform
    row=p.save_connection({'type':'remote_rag','name':'remote','endpoint':'https://synthetic.example.test'})
    await p.connection_action(row['connection_id'],'enable')
    p.knowledge_action('settings','provider',{'provider':row['connection_id']})
    async def unavailable(*a,**k): raise asyncio.TimeoutError()
    monkeypatch.setattr(RemoteRagKnowledgeProvider,'query',unavailable)
    with pytest.raises(PyHError,match='knowledge_unavailable'): await p.query_knowledge('question')
    assert p.knowledge_list()['status']=='unavailable'
    p.knowledge_action('settings','provider',{'provider':'exact_keyword'})
    assert not (await p.query_knowledge('question'))['answer_allowed']


async def test_model_missing_and_secret_command_rejected(app):
    p=app.service.platform
    with pytest.raises(PyHError): await p.connection_action('model:missing','test')
    with pytest.raises(PyHError): p.save_connection({'type':'mcp','config':{'name':'bad','command':['tool','--token','synthetic']}})


async def test_mcp_disconnect_and_status(app,monkeypatch):
    from pyharness.core import mcp
    p=app.service.platform
    row=p.save_connection({'type':'mcp','config':{'name':'test','command':['synthetic-only'],'enabled':False}})
    async def broken(self): raise ConnectionError('synthetic disconnect')
    monkeypatch.setattr(mcp.McpClient,'connect',broken)
    assert (await p.connection_action(row['connection_id'],'test'))['status']=='unavailable'
    assert not p.connection_clients
    assert p.connections()[0]['status']=='disabled'


async def test_failed_create_and_cleanup_retains_owner(tmp_path,monkeypatch):
    backend=DockerSandboxBackend(tmp_path/'client');broken=True
    async def command(args,**kw):
        return {'exit_code':0 if args[0]=='info' or (args[0]=='rm' and not broken) else 1,'output':'injected'}
    monkeypatch.setattr(backend,'_command',command)
    manager=SandboxManager(tmp_path/'sandboxes',docker=backend)
    with pytest.raises(BaseExceptionGroup): await manager.create('run',SandboxProfile(mode='isolated'))
    assert len(manager.records)==1
    record=next(iter(manager.records.values()))
    assert record.status=='cleanup_failed' and (manager.root/record.sandbox_id).exists()
    broken=False
    await manager.close()
    assert record.status=='destroyed' and not (manager.root/record.sandbox_id).exists()


async def test_snapshot_freezes_before_host_access_and_thaws_on_failure(tmp_path):
    events=[]
    class Backend(FakeBackend):
        async def freeze(self,record): events.append('freeze')
        async def thaw(self,record): events.append('thaw')
    manager=SandboxManager(tmp_path/'sandboxes',docker=Backend())
    record=await manager.create('run',SandboxProfile(mode='isolated'))
    with pytest.raises(RuntimeError):
        async with manager.snapshot(record.sandbox_id):
            assert events==['freeze']
            raise RuntimeError('read failure')
    assert events==['freeze','thaw']
    await manager.close()


async def test_restart_orphan_recovery_requires_owned_labels(app,monkeypatch):
    import json
    from pyharness.application.platform_models import SandboxRecord
    p=app.service.platform;sid=await app.service.create_session();log=await app.service.require_session(sid)
    ident='ph-'+'a'*32
    record=SandboxRecord(sandbox_id=ident,run_id=sid+'~old',backend='isolated',status='running',
        workspace_mode='empty',cpus=1,memory_mb=128,pids_limit=16)
    await log.append('platform.sandbox',{'action':'created','record':record.model_dump()},actor='system',sync=True)
    backend=p.sandboxes.backends['isolated'];owned=False
    async def command(args,**kw):
        if args[0]=='inspect':return {'exit_code':0,'output':json.dumps({'pyharness.managed':'true','pyharness.owner':backend.owner_label if owned else 'foreign'})}
        return {'exit_code':0,'output':''}
    monkeypatch.setattr(backend,'_command',command)
    assert (await p.sandbox_records())[0]['status']=='orphaned'
    with pytest.raises(PyHError): await p.destroy_sandbox(ident)
    assert ident not in p.sandboxes.records
    owned=True
    assert (await p.destroy_sandbox(ident))['status']=='destroyed'
    assert (await p.destroy_sandbox(ident))['status']=='destroyed'


@pytest.mark.parametrize('decision',['approval.granted','approval.denied','approval.timeout'])
def test_completed_run_not_reopened_by_artifact_approval(decision):
    from pyharness.application.platform_projection import project
    def event(seq,kind,p):return SimpleNamespace(seq=seq,type=kind,payload=p,ts='2026-09-26T01:00:00Z',task_id='t',trace=None)
    events=[event(1,'task.enqueued',{'task_id':'t'}),event(2,'task.started',{'task_id':'t'}),
        event(3,'agent.message',{'content':'explicit success'}),event(4,'task.completed',{'task_id':'t'}),event(5,'approval.requested',{'tool':'workspace.export_artifact','args_summary':'synthetic','risk':'high','ttl_ms':1000}),
        event(6,decision,{'approval_id':5})]
    assert project('s',events)['runs'][0]['status']=='completed'


async def test_runs_sorted_globally(app,monkeypatch):
    async def views():return {'runs':[{'run_id':'new','queued_at':'2026-09-26T03:00:00Z','started_at':None},
        {'run_id':'old','queued_at':'2026-09-25T03:00:00Z','started_at':None}], 'steps':[]}
    monkeypatch.setattr(app.service.platform,'all_views',views)
    assert [r['run_id'] for r in (await app.service.platform.runs())['runs']]==['new','old']


async def test_large_broker_input_uses_stdin_not_windows_argv(tmp_path,monkeypatch):
    from pyharness.application.platform_models import SandboxRecord
    import json
    backend=DockerSandboxBackend(tmp_path/'client')
    record=SandboxRecord(sandbox_id='ph-'+'b'*32,run_id='r',backend='isolated',status='running',workspace_mode='empty',cpus=1,memory_mb=128,pids_limit=16)
    backend.profiles[record.sandbox_id]=SandboxProfile(mode='isolated')
    captured=[]
    async def command(args,**kw):
        captured.append((args,kw));return {'exit_code':0,'output':'{"value":"written"}'}
    monkeypatch.setattr(backend,'_command',command)
    content='x'*100000
    assert await backend.file_operation(record,'fs.write_file',{'path':'large.txt','content':content})=='written'
    args,kw=captured[0]
    assert '-i' in args and len(' '.join(args))<10000
    assert json.loads(kw['input_data'])['content']==content
    assert 'os.O_NONBLOCK|os.O_NOFOLLOW' in args[-1]
    compile(args[-1],'<container broker>','exec')


def test_regular_reader_rejects_special_descriptor(tmp_path,monkeypatch):
    import os,stat
    from pyharness.core.sandbox import read_regular
    path=tmp_path/'normal';path.write_bytes(b'bounded')
    assert read_regular(path,20)==b'bounded'
    original=os.fstat
    def fake_fifo(fd):
        info=original(fd)
        return SimpleNamespace(st_mode=stat.S_IFIFO,st_size=info.st_size)
    monkeypatch.setattr(os,'fstat',fake_fifo)
    with pytest.raises(PyHError,match='file_not_regular'):read_regular(path,20)


@pytest.mark.controlled_process
async def test_private_local_git_clone_is_committed_bounded_and_clean(tmp_path):
    import os,subprocess
    from pyharness.core.sandbox import private_git_clone
    source=tmp_path/'source';source.mkdir();dest=tmp_path/'copy';dest.mkdir()
    env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0')
    def git(*args):
        subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-c','commit.gpgsign=false',*args],cwd=source,env=env,
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=10)
    git('init','--quiet')
    (source/'code.py').write_bytes(b'committed input')
    git('add','--','code.py')
    git('-c','user.name=Synthetic Test','-c','user.email=synthetic@example.invalid','commit','--quiet','-m','synthetic input')
    (source/'code.py').write_bytes(b'uncommitted input')
    (source/'.env').write_bytes(b'synthetic secret')
    await asyncio.wait_for(private_git_clone(source,dest),30)
    assert (dest/'code.py').read_bytes()==b'committed input'
    assert not (dest/'.private-clone').exists() and not (dest/'.env').exists()
    assert (source/'code.py').read_bytes()==b'uncommitted input'


async def test_platform_http_operation_owned_by_service_shutdown(app,monkeypatch):
    entered=asyncio.Event();cleaned=asyncio.Event()
    async def dashboard():
        entered.set()
        try: await asyncio.Event().wait()
        finally: cleaned.set()
    monkeypatch.setattr(app.service.platform,'dashboard',dashboard)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as client:
        request=asyncio.create_task(client.get('/api/v1/dashboard'))
        await asyncio.wait_for(entered.wait(),3)
        await asyncio.wait_for(app.service.shutdown(),5)
        await asyncio.gather(request,return_exceptions=True)
        assert cleaned.is_set() and not app.service._admissions


async def test_platform_http_shutdown_owned_by_declared_tenant(app,monkeypatch):
    default=app.service
    tenant=app._service_registry.get('synthetic-acme')
    token=app._tenant_store().api_token('synthetic-acme')
    entered=asyncio.Event();cleaned=asyncio.Event()
    async def dashboard():
        entered.set()
        try: await asyncio.Event().wait()
        finally: cleaned.set()
    monkeypatch.setattr(tenant.platform,'dashboard',dashboard)
    headers={'X-PyHarness-Token':app._api_token,'X-PyHarness-Tenant':'synthetic-acme','X-PyHarness-Tenant-Token':token}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers=headers) as client:
            request=asyncio.create_task(client.get('/api/v1/dashboard'))
            await asyncio.wait_for(entered.wait(),3)
            assert tenant._admissions and not getattr(default,'_admissions',{})
            await asyncio.wait_for(tenant.shutdown(),5)
            await asyncio.gather(request,return_exceptions=True)
            assert cleaned.is_set() and not tenant._admissions
            assert (await client.get('/api/v1/dashboard',headers={'X-PyHarness-Tenant':'default'})).status_code==200
    finally:
        await tenant.shutdown()
