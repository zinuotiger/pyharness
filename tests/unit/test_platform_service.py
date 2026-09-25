"""Real service/queue/event store contracts with a deterministic model adapter."""
import asyncio
import base64
import json
from types import SimpleNamespace
import pytest
import httpx
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.platform_models import AgentDefinition, SandboxProfile
from pyharness.application.platform_projection import project, summary
from pyharness.application.platform_service import TEMPLATES
from pyharness.core.sandbox import SandboxManager, safe_path, private_copy
from pyharness.core.knowledge import ExactKeywordKnowledgeProvider
from pyharness.errors import PyHError
from tests.support import isolated_settings


@pytest.fixture
async def platform(tmp_path, adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    app = DesktopApp(cli.assemble_ctx(cfg))
    yield app
    await app.service.shutdown()


async def test_run_real_queue_persistence_and_version(platform):
    p = platform.service.platform
    draft = p.save_agent(TEMPLATES[0])
    version = p.publish_agent(draft['agent_id'])
    result = await p.start_run({'agent_id':draft['agent_id'],'agent_version':1,'text':'hello'})
    q = platform.service._queues[result['session_id']]
    completed = await asyncio.wait_for(q.wait_for(result['task_id']), 10)
    assert completed.ok
    run = await p.run(result['run_id'])
    assert run['status'] == 'completed'
    assert run['agent_version'] == 1
    assert run['token_usage'] > 0
    changed = {**draft,'system_prompt':'changed'}
    p.save_agent(changed, draft['agent_id'])
    assert p.version(draft['agent_id'],1) == version
    assert p.publish_agent(draft['agent_id'])['version'] == 2
    assert (await p.run(result['run_id']))['agent_version'] == 1
    log = await platform.service.require_session(result['session_id'])
    assert project(result['session_id'],log.events_after(0))['runs'][0]['status']=='completed'


async def test_dashboard_empty_api_and_auth(platform):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=platform.api),base_url='http://127.0.0.1') as client:
        assert (await client.get('/api/v1/dashboard')).status_code == 401
        client.headers['X-PyHarness-Token']=platform._api_token
        d=(await client.get('/api/v1/dashboard')).json()
        assert d['running']==0 and d['usage']['tokens']==0
        assert d['sessions']==[]
        assert (await client.get('/')).status_code==200
        invalid=await client.post('/api/v1/agents',json={'name':'x','timeout':0})
        assert invalid.status_code==422


async def test_artifact_upload_integrity_delete(platform):
    p=platform.service.platform
    sid=await platform.service.create_session()
    record=await p.upload({'session_id':sid,'filename':'说明.md','data_base64':base64.b64encode(b'# test').decode()})
    _,raw=await p.artifact_content(record['artifact_id'])
    assert raw==b'# test'
    path=p.root/'artifacts'/record['storage_path']
    path.write_bytes(b'tampered')
    with pytest.raises(PyHError,match='artifact_integrity_failed'):
        await p.artifact_content(record['artifact_id'])
    await p.delete_artifact(record['artifact_id'])
    assert not path.exists()
    assert (await p.artifacts())['artifacts']==[]


@pytest.mark.parametrize('name', ['../a','/etc/passwd','C:/secret','a\\b','a/../b','.'])
def test_path_traversal_rejected(tmp_path,name):
    with pytest.raises(PyHError):
        safe_path(tmp_path,name)


def test_safe_path_accepts_normal_file(tmp_path):
    assert safe_path(tmp_path,'nested/file.txt')==tmp_path/'nested/file.txt'


async def test_knowledge_exact_citations_and_no_evidence(platform):
    p=platform.service.platform
    source=await p.add_knowledge({'name':'rules','text':'project: PyHarness\nmode: isolated'})
    hits=await p.query_knowledge('PyHarness')
    assert hits['answer_allowed']
    assert hits['citations'][0]['source_id']==source['source_id']
    assert hits['citations'][0]['line']==1
    assert not (await p.query_knowledge('missing evidence'))['answer_allowed']
    p.delete_knowledge(source['source_id'])
    assert p.knowledge_list()['sources']==[]


class FakeBackend:
    def __init__(self, error=None):
        self.error=error
        self.executions=0
        self.destroyed=[]
        self.roots={}
    async def create(self,record,root,profile):
        self.roots[record.sandbox_id]=root
        if self.error=='create':
            raise PyHError('sandbox_unavailable')
    async def execute(self,record,command,**kwargs):
        self.executions+=1
        if self.error=='execute':
            raise RuntimeError('injected failure')
        return {'exit_code':0,'output':'synthetic','truncated':False}
    async def freeze(self,record): pass
    async def thaw(self,record): pass
    async def file_operation(self,record,name,args):
        path=safe_path(self.roots[record.sandbox_id],args['path'])
        if name=='fs.write_file':
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(args['content'],encoding='utf-8')
            return 'fake backend wrote private file'
        if name=='fs.read_file': return path.read_text('utf-8')
        raise AssertionError(name)
    async def destroy(self,record):
        if self.error=='destroy':
            raise RuntimeError('injected cleanup failure')
        self.destroyed.append(record.sandbox_id)


async def test_isolated_unavailable_never_falls_back(tmp_path):
    backend=FakeBackend('create')
    m=SandboxManager(tmp_path/'sandboxes',docker=backend)
    called=[]
    async def host(*args,**kw):
        called.append(1)
        raise AssertionError('host must never run')
    m.backends['host_approved'].create=host
    with pytest.raises(PyHError):
        await m.create('run',SandboxProfile(mode='isolated'))
    assert called==[] and backend.executions==0 and not m.records
    assert not list((tmp_path/'sandboxes').iterdir())


async def test_disabled_zero_execution(tmp_path):
    backend=FakeBackend()
    m=SandboxManager(tmp_path/'sandboxes',docker=backend)
    with pytest.raises(PyHError):
        await m.create('run',SandboxProfile())
    assert not m.records and backend.executions==0
    assert not (tmp_path/'sandboxes').exists()


async def test_two_private_sandboxes_and_cleanup_failure(tmp_path):
    backend=FakeBackend()
    m=SandboxManager(tmp_path/'sandboxes',docker=backend)
    a,b=await asyncio.wait_for(asyncio.gather(m.create('a',SandboxProfile(mode='isolated')),m.create('b',SandboxProfile(mode='isolated'))),5)
    (m.workspace(a.sandbox_id)/'a.txt').write_text('a')
    assert not (m.workspace(b.sandbox_id)/'a.txt').exists()
    backend.error='destroy'
    with pytest.raises(RuntimeError):
        await m.destroy(a.sandbox_id)
    assert a.status=='cleanup_failed'
    backend.error=None
    await m.close()
    assert all(r.status=='destroyed' for r in m.records.values())
    assert not list((tmp_path/'sandboxes').iterdir())


def test_private_copy_omits_secret_files(tmp_path):
    source=tmp_path/'source';source.mkdir()
    (source/'.env').write_text('synthetic secret')
    (source/'credentials.yaml').write_text('synthetic secret')
    (source/'main.py').write_text('print(1)')
    dest=tmp_path/'dest';dest.mkdir()
    private_copy(source,dest)
    assert [p.name for p in dest.iterdir()]==['main.py']


def test_trace_redaction():
    assert 'sk-' not in summary('api_key=sk-'+'A'*40)


def test_recovery_does_not_claim_running():
    e=SimpleNamespace(type='task.enqueued',payload={'task_id':'t-1'},ts='2026-01-01T00:00:00Z',seq=1,task_id=None)
    r=project('s-test',[e])['runs'][0]
    assert r['status']=='failed' and r['error_code']=='runtime_interrupted'


async def test_governed_code_change_approval_patch_export_and_restart(tmp_path,adapter_factory):
    """Fake model + fake isolation, real governance/queue/file/approval/patch facts."""
    from pyharness.core import llm
    from pyharness.core.sandbox import SandboxManager
    cfg=isolated_settings(tmp_path)
    adapter=adapter_factory(cfg,[{'id':'write-fix','name':'fs.write_file',
        'args':{'path':'add.py','content':'def add(a, b):\n    return a + b\n'}}, {'id':'test-fix','name':'exec.python_run','args':{'code':'from add import add; assert add(2, 3) == 5'}}])
    app=DesktopApp(cli.assemble_ctx(cfg))
    p=app.service.platform
    p.sandboxes=SandboxManager(tmp_path/'test-sandboxes',docker=FakeBackend())
    pending=asyncio.Queue()
    async def observe(kind,event):
        pending.put_nowait((event.session_id,event.seq))
    app.ctx.bus.subscribe('approval.requested',observe,owner='platform-e2e')
    sid=await app.service.create_session()
    root=__import__('pathlib').Path(app.service._session_workspace(sid))
    root.mkdir(parents=True,exist_ok=True)
    target=root/'add.py'
    original='def add(a, b):\n    return a - b\n'
    target.write_text(original)
    draft=p.save_agent(TEMPLATES[1]);p.publish_agent(draft['agent_id'])
    try:
        submitted=await p.start_run({'session_id':sid,'agent_id':draft['agent_id'],'agent_version':1,'text':'Fix add'})
        approved_sid,aid=await asyncio.wait_for(pending.get(),5)
        assert approved_sid==sid and target.read_text()==original
        await app.service.decide_approval(aid,'approve',sid=sid)
        _,aid=await asyncio.wait_for(pending.get(),5)
        await app.service.decide_approval(aid,'approve',sid=sid)
        result=await asyncio.wait_for(app.service._queues[sid].wait_for(submitted['task_id']),10)
        assert result.ok
        assert target.read_text()==original, 'sandbox changes must not update source'
        artifacts=(await p.artifacts())['artifacts']
        assert any(a['filename']=='test-report.json' for a in artifacts)
        patch=next(a for a in artifacts if a['filename']=='changes.patch')
        assert not patch['download_allowed']
        with pytest.raises(PyHError):
            await p.artifact_content(patch['artifact_id'])
        _,raw=await p.artifact_content(patch['artifact_id'],preview=True)
        assert b'+    return a + b' in raw
        operation=await p.request_artifact_action(patch['artifact_id'],'apply')
        _,aid=await asyncio.wait_for(pending.get(),5)
        assert target.read_text()==original
        await app.service.decide_approval(aid,'approve',sid=sid)
        outcome=await asyncio.wait_for(p.actions[operation['operation_id']],5)
        assert outcome['ok'],outcome
        assert target.read_text()=='def add(a, b):\n    return a + b\n'
        operation=await p.request_artifact_action(patch['artifact_id'],'export')
        _,aid=await asyncio.wait_for(pending.get(),5)
        await app.service.decide_approval(aid,'approve',sid=sid)
        assert (await asyncio.wait_for(p.actions[operation['operation_id']],5))['ok']
        assert (await p.artifact_content(patch['artifact_id']))[1]==raw
        assert all(r.status=='destroyed' for r in p.sandboxes.records.values())
    finally:
        await app.service.shutdown()
    reloaded=DesktopApp(cli.assemble_ctx(cfg))
    try:
        assert (await reloaded.service.platform.run(submitted['run_id']))['status']=='completed'
        assert (await reloaded.service.platform.artifact_content(patch['artifact_id']))[1]==raw
        assert len((await reloaded.service.platform.approvals())['approvals'])==4
    finally:
        await reloaded.service.shutdown()


def test_patch_failure_rolls_back_all_files(tmp_path):
    from pyharness.core.patches import apply_manifest,digest
    from pyharness.core.tenant_settings import TenantSettingsStore
    (tmp_path/'a').write_bytes(b'old a');(tmp_path/'b').write_bytes(b'old b')
    changes=[{'path':name,'before_sha256':digest(('old '+name).encode()),'after':'new '+name} for name in ('a','b')]
    calls=0
    def writer(path,raw):
        nonlocal calls
        calls+=1
        TenantSettingsStore._atomic_write(path,raw)
        if calls==2:
            raise OSError('injected after replace')
    with pytest.raises(OSError):
        apply_manifest(tmp_path,changes,writer=writer)
    assert (tmp_path/'a').read_bytes()==b'old a' and (tmp_path/'b').read_bytes()==b'old b'


def test_patch_stale_baseline_zero_writes(tmp_path):
    from pyharness.core.patches import apply_manifest
    (tmp_path/'a').write_bytes(b'old')
    with pytest.raises(PyHError):
        apply_manifest(tmp_path,[{'path':'a','before_sha256':'wrong','after':'new'}])
    assert (tmp_path/'a').read_bytes()==b'old'
