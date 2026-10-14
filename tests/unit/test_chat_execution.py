"""New chat workflows: synthetic model/reviewer and injected isolation where noted."""
import asyncio
import base64
from types import SimpleNamespace
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.platform_models import SandboxProfile
from pyharness.application.automatic_review import AutomaticReviewer
from pyharness.core.sandbox import SandboxManager
from pyharness.core.remote_sandbox import RemoteSandboxBackend, validate_address
from pyharness.remote_sandbox import create_app
from pyharness.errors import PyHError
from tests.support import isolated_settings
from tests.unit.test_platform_service import FakeBackend
from tests.unit.test_approval_races import stack, cleanup


@pytest.fixture
async def app(tmp_path, adapter_factory):
    cfg=isolated_settings(tmp_path); adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    yield app
    await app.service.shutdown()


async def test_create_publish_reuse_atomic_retry_and_switch_attachments(app):
    p=app.service.platform
    body={'idempotency_key':'retry-stable-01','definition':{'name':'中文助手','system_prompt':'明确职责'}}
    first=p.chat_setup.create_use(body)
    assert first==p.chat_setup.create_use(body)
    assert p.chat_setup.create_use({**body,'idempotency_key':'different-key-02'})['reused']
    assert len(p.agents()['agents'])==1
    draft=p.agents()['agents'][0]
    p.save_agent({**draft,'description':'unpublished capabilities'},draft['agent_id'])
    assert p.chat_setup.assistants()['agents'][0]['description']!='unpublished capabilities'
    with pytest.raises(PyHError,match='idempotency_conflict'):
        p.chat_setup.create_use({**body,'definition':{'name':'different'}})
    run=await p.start_run({'agent_id':first['agent_id'],'agent_version':1,'text':'hello'})
    assert (await asyncio.wait_for(app.service._queues[run['session_id']].wait_for(run['task_id']),5)).ok
    cfg=await p.chat_setup.configuration(run['session_id'])
    assert cfg['bound'] and cfg['definition']['name']=='中文助手'
    item=await p.upload({'session_id':run['session_id'],'filename':'输入.txt','data_base64':base64.b64encode('中文输入'.encode()).decode()})
    switched=await p.chat_setup.switch({'session_id':run['session_id'],'agent_id':first['agent_id'],'version':1,'artifact_ids':[item['artifact_id']]})
    assert switched['sid']!=run['session_id']
    new=switched['attachments'][0]
    assert new['artifact_id']!=item['artifact_id'] and new['session_id']==switched['sid']
    assert (await p.artifact_content(new['artifact_id']))[1]=='中文输入'.encode()
    with pytest.raises(PyHError,match='artifact_owner_mismatch'):
        await p.chat_setup.switch({'session_id':switched['sid'],'agent_id':first['agent_id'],'version':1,'artifact_ids':[item['artifact_id']]})


async def test_remote_secrets_write_only_and_https(app):
    p=app.service.platform
    secret='synthetic-test-token-'+'x'*32
    row=p.chat_setup.save_remote({'name':'本地测试','address':'http://127.0.0.1:60500','token':secret})
    assert secret not in str(p.chat_setup.remotes())
    assert secret not in (p.root/'configuration.json').read_text('utf-8')
    assert p.chat_setup.remote_settings(row['id'])[1]==secret
    for address in ('http://example.com','https://user:password@example.com','https://example.com/?token=abc'):
        with pytest.raises(PyHError): validate_address(address)


async def test_chat_creation_keeps_team_host_prohibition(app,monkeypatch):
    from pyharness.application.accounts import Accounts
    monkeypatch.setattr(Accounts,'manages_tenant',lambda *args:True)
    with pytest.raises(PyHError,match='account_host_execution_forbidden'):
        app.service.platform.chat_setup.create_use({'idempotency_key':'team-host-denial','definition':{
            'name':'forbidden','sandbox_profile':{'mode':'host_approved'}}})
    assert not app.service.platform.agents()['agents']


async def test_execution_readiness_distinguishes_engine_image_and_chat(app,monkeypatch):
    p=app.service.platform;b=p.sandboxes.backends['isolated']
    async def down(): return {'status':'unavailable'}
    monkeypatch.setattr(b,'health',down)
    assert (await p.chat_setup.readiness({'mode':'disabled'}))['ready']
    assert not (await p.chat_setup.readiness({'mode':'isolated'}))['engine_connected']
    async def up(): return {'status':'available'}
    async def missing(args,**kw): return {'exit_code':1,'output':'linux'} if args[0]=='image' else {'exit_code':0,'output':'linux'}
    monkeypatch.setattr(b,'health',up);monkeypatch.setattr(b,'_command',missing)
    result=await p.chat_setup.readiness({'mode':'isolated'})
    assert result['engine_connected'] and not result['image_present'] and not result['ready']


@pytest.mark.parametrize('outcome',['approve','deny','manual','timeout','malformed'])
async def test_independent_review_outcomes_and_same_completion_owner(outcome):
    provider,queue,log,ctx,call=await stack()
    class Reviewer:
        identity='reviewer:synthetic-test'
        timeout=.03
        async def evaluate(self, request):
            assert request['args']==call.args and request['scope']['session_id']==log.sid
            if outcome=='timeout': await asyncio.sleep(1)
            if outcome=='malformed': return {'bad':'result'}
            return {'decision':outcome,'reason':'synthetic review'}
    provider._reviewer=Reviewer()
    definition=SimpleNamespace(sandbox_profile=SandboxProfile(mode='isolated'),allowed_tools=['fs.write_file'])
    ctx.platform_runtime=SimpleNamespace(definition=definition)
    provider._platform_runtime=ctx.platform_runtime
    request=asyncio.create_task(provider.request(call,'exact operation',ctx))
    try:
        await asyncio.wait_for(queue.paused.wait(),1)
        for _ in range(100):
            reviews=[e for e in log.events_after(0) if e.type=='approval.reviewed']
            if reviews: break
            await asyncio.sleep(.01)
        assert reviews
        if outcome in ('approve','deny'):
            assert await asyncio.wait_for(request,1)==('granted' if outcome=='approve' else 'denied')
            assert not provider._pending and not provider._suspended
            assert [e for e in log.events_after(0) if e.type=='approval.'+('granted' if outcome=='approve' else 'denied')][0].payload['by']==Reviewer.identity
        else:
            assert reviews[0].payload['decision']=='manual' and not request.done()
            await provider.deny_async(reviews[0].payload['approval_id'],by='cli:human')
            assert await request=='denied'
        with pytest.raises(PyHError): await provider.approve_async(reviews[0].payload['approval_id'],by=Reviewer.identity)
    finally: await cleanup(provider,request)


def test_conservative_reviewer_mandatory_human_and_scope():
    reviewer=AutomaticReviewer()
    base={'tool':'fs.write_file','args':{'path':'hello.txt','content':'中文'},'context':['write hello.txt'],
        'scope':{'mode':'isolated','allowed_tools':['fs.write_file','exec.python_run']}}
    assert reviewer.check(base)['decision']=='approve'
    assert reviewer.check({**base,'args':{'path':'../escape','content':''}})['decision']=='deny'
    assert reviewer.check({**base,'scope':{**base['scope'],'mode':'host_approved'}})['decision']=='manual'
    assert reviewer.check({**base,'tool':'workspace.export_artifact'})['decision']=='manual'
    assert reviewer.check({**base,'context':['just say hello']})['decision']=='manual'
    assert reviewer.check({**base,'context':[]})['decision']=='manual'
    assert reviewer.check({**base,'context':['write other.txt']})['decision']=='manual'
    assert reviewer.check({**base,'context':['print hello'],'tool':'exec.python_run','args':{'code':"print('hello')"}})['decision']=='approve'
    assert reviewer.check({**base,'tool':'exec.python_run','args':{'code':"__import__('os').system('anything')"}})['decision']=='manual'


def test_next_task_after_interrupted_segment_keeps_its_own_success():
    from pyharness.application.platform_projection import project
    rows=[('task.enqueued',{'task_id':'old'}),('segment.start',{'task_id':'old'}),
        ('task.enqueued',{'task_id':'new'}),('segment.start',{'task_id':'new'}),
        ('agent.message',{'content':'explicit result'}),('task.completed',{'task_id':'new','reason':'ok'})]
    events=[SimpleNamespace(seq=i+1,type=k,payload=p,ts='2026-10-10T00:00:00Z',task_id=None,trace=None) for i,(k,p) in enumerate(rows)]
    runs={r['task_id']:r for r in project('synthetic-session',events)['runs']}
    assert runs['old']['status']=='failed' and runs['old']['error_code']=='runtime_interrupted'
    assert runs['new']['status']=='completed'


def test_approval_after_task_completion_keeps_explicit_run_identity():
    from pyharness.application.platform_projection import project
    rows=[('task.enqueued',{'task_id':'finished'}),('segment.start',{'task_id':'finished'}),
        ('agent.message',{'content':'done'}),('task.completed',{'task_id':'finished','reason':'ok'}),
        ('segment.end',{'task_id':'finished'}),('approval.requested',{'tool':'workspace.export_artifact','ttl_ms':1000})]
    events=[SimpleNamespace(seq=i+1,type=k,payload=p,ts='2026-10-10T00:00:00Z',
        task_id='finished' if k=='approval.requested' else None,trace=None) for i,(k,p) in enumerate(rows)]
    view=project('synthetic-session',events)
    assert view['approvals'][0]['run_id']=='synthetic-session~finished'
    assert view['runs'][0]['status']=='completed'


async def test_automatic_review_real_engine_queue_fake_model_and_isolation(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path)
    adapter_factory(cfg,[{'id':'write-automatic','name':'fs.write_file','args':{'path':'hello.txt','content':'中文测试'}}])
    app=DesktopApp(cli.assemble_ctx(cfg));p=app.service.platform
    p.sandboxes=SandboxManager(tmp_path/'synthetic-isolation',docker=FakeBackend())
    try:
        a=p.chat_setup.create_use({'idempotency_key':'automatic-real-queue','definition':{'name':'auto','permission_policy':'standard','allowed_tools':['fs.write_file'],
            'sandbox_profile':{'mode':'isolated'},'review_mode':'automatic'}})
        run=await p.start_run({'agent_id':a['agent_id'],'agent_version':a['version'],'text':'write hello.txt'})
        result=await asyncio.wait_for(app.service._queues[run['session_id']].wait_for(run['task_id']),5)
        assert result.ok,result
        approvals=(await p.approvals())['approvals']
        assert approvals[0]['status']=='approved' and approvals[0]['review']['reviewer'].startswith('reviewer:')
        assert 'hello.txt' in approvals[0]['details']['arguments']
        with pytest.raises(PyHError,match='approval_owner_mismatch'):
            await p.decide(approvals[0]['id'],'approve',{'session_id':'wrong','call_id':approvals[0]['call_id'],'run_id':run['run_id']})
    finally: await app.service.shutdown()


class WorkerDouble(FakeBackend):
    async def health(self): return {'status':'available'}
    async def _command(self,*args,**kwargs): return {'exit_code':0,'output':'linux'}


async def test_remote_protocol_files_ownership_quota_cancel_and_client(tmp_path):
    """Local ASGI integration, injected isolation; NOT real Docker or cross-machine."""
    manager=SandboxManager(tmp_path/'worker/workspaces',docker=WorkerDouble())
    app=create_app(tmp_path/'worker',{'alice':'a'*32,'bob':'b'*32},manager=manager,max_active=1)
    transport=httpx.ASGITransport(app=app)
    backend=RemoteSandboxBackend(lambda ident:('http://127.0.0.1','a'*32),transport=transport)
    client_manager=SandboxManager(tmp_path/'client');client_manager.backends['remote']=backend
    source=tmp_path/'inputs';source.mkdir();(source/'输入.txt').write_text('input',encoding='utf-8')
    profile=SandboxProfile(mode='remote',remote_connection='local-test')
    try:
        record=await client_manager.create('task',profile,source)
        ident=backend.handles[record.sandbox_id][1]
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1',headers={'Authorization':'Bearer '+'b'*32}) as other:
            assert (await other.get('/v1/tasks/'+ident)).status_code==404
            assert (await other.delete('/v1/tasks/'+ident)).status_code==404
            assert (await other.post('/v1/tasks/'+ident+'/files',json={})).status_code==404
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1',headers={'Authorization':'Bearer '+'a'*32}) as own:
            r=await own.post('/v1/tasks/'+'c'*32,json={'profile':profile.model_dump(),'files':{}})
            assert r.status_code==429
        await client_manager.file_operation(record.sandbox_id,'fs.write_file',{'path':'result.txt','content':'result'})
        async with client_manager.snapshot(record.sandbox_id) as root:
            assert (root/'result.txt').read_text()=='result' and (root/'输入.txt').read_text()=='input'
        assert (await client_manager.execute(record.sandbox_id,"print('test')",python=True))['exit_code']==0
        await client_manager.destroy(record.sandbox_id)
        assert app.state.tasks[ident]['status']=='deleted'
    finally:
        await client_manager.close();await manager.close()


async def test_remote_disconnect_never_resubmits_or_falls_back(tmp_path):
    isolation=WorkerDouble()
    manager=SandboxManager(tmp_path/'worker/workspaces',docker=isolation)
    app=create_app(tmp_path/'worker',{'alice':'a'*32},manager=manager)
    asgi=httpx.ASGITransport(app=app)
    class Disconnect(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            if request.method=='GET' and '/tasks/' in request.url.path: raise httpx.ReadError('synthetic lost connection')
            return await asgi.handle_async_request(request)
    backend=RemoteSandboxBackend(lambda ident:('http://127.0.0.1','a'*32),transport=Disconnect())
    local=SandboxManager(tmp_path/'client');local.backends['remote']=backend
    try:
        record=await local.create('disconnect',SandboxProfile(mode='remote',remote_connection='test'))
        ident=backend.handles[record.sandbox_id][1]
        with pytest.raises(PyHError,match='remote_disconnected'):
            await local.execute(record.sandbox_id,'synthetic command')
        assert isolation.executions<=1 and app.state.tasks[ident]['status']=='deleted'
        assert not local.backends['host_approved'].roots
    finally: await local.close();await manager.close()
