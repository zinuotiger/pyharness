"""S07 concrete file observations and exact generated artifact references.

Real local private workspace, queue, governance, approval and artifact bytes;
inference is ScriptedAdapter. Explicit approval below is a synthetic test action.
"""
import asyncio,hashlib,json
import pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_source_grants import PID

RAW=b'exact-selected-input\r\n'
AFTER='synthetic-output\n'
OPTIONS=dict(raw=RAW,script=[dict(id='real-file-write',name='fs.write_file',
    args=dict(path='unchanged.txt',content=AFTER))],agent=dict(sandbox_profile=dict(mode='host_approved')))


async def execute_writes(env):
    projects=env.app.service.projects;preview=await projects.execution_preview(PID,env.selection)
    started=await projects.start_execution(PID,dict(env.selection,execution_id='ex-'+'e'*16,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'write-for-receipt')
    approved=[]
    async with asyncio.timeout(15):
        while True:
            pending=(await env.app.service.pending_approvals())['pending']
            spine=env.app.service._engines[started['session_id']]
            owned={started['session_id']}|set(spine.subagent._children)
            for request in pending:
                if request['session_id'] in owned:
                    await env.app.service.decide_approval(request['approval_id'],'approve',sid=request['session_id'])
                    approved.append(request['approval_id'])
            state=env.app.service._queues[started['session_id']].status()
            if state.running!=started['task_id'] and started['task_id'] not in state.waiting:break
            await asyncio.sleep(.01)
    assert approved,'actual governed write never requested its manual approval'
    result=await wait_terminal(projects,started['execution_id'])
    assert result['status']=='completed',result
    return result,env.app.service._engines[started['session_id']].session


async def written(env):
    captured=(env.source/'unchanged.txt').read_bytes()
    result,log=await execute_writes(env)
    artifact=next(e for e in log.events_after(0) if e.type=='platform.artifact' and
        e.payload['action']=='generated' and e.payload['record']['filename']=='unchanged.txt')
    item=artifact.payload['record']
    path=env.app.service.platform.root/'artifacts'/item['storage_path']
    assert path.read_bytes()==AFTER.encode() and item['sha256']==hashlib.sha256(AFTER.encode()).hexdigest()
    assert (env.source/'unchanged.txt').read_bytes()==captured
    return result,artifact,path


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_real_file_change_is_bound_to_its_actual_returned_invocation(running_product):
    result,artifact,path=await written(running_product)
    returned=next(e for e in result['effects'] if e['phase']=='returned')
    assert returned['file_changes']==[dict(path='unchanged.txt',kind='modified',
        before_sha256=hashlib.sha256(RAW).hexdigest(),after_sha256=hashlib.sha256(AFTER.encode()).hexdigest())]


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_execution_receipt_links_real_artifact_version_without_exporting_body(running_product):
    result,event,path=await written(running_product)
    linked=next(a for a in result['artifacts'] if a['artifact_id']==event.payload['record']['artifact_id'])
    assert linked['source_seq']==event.seq and linked['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert linked['integrity']=='verified' and not linked['download_allowed']
    assert 'storage_path' not in linked and 'content' not in linked


MULTIPLE=dict(OPTIONS,script=[
    dict(id='write-one',name='fs.write_file',args=dict(path='unchanged.txt',content='one\r\n')),
    dict(id='append-two',name='fs.write_file',args=dict(path='unchanged.txt',content='two\n',mode='append')),
    dict(id='patch-three',name='fs.patch',args=dict(path='unchanged.txt',old='two',new='three'))])


@pytest.mark.parametrize('running_product',[MULTIPLE],indirect=True)
async def test_repeated_write_append_and_patch_observe_each_real_intermediate_version(running_product):
    result,log=await execute_writes(running_product)
    returned=[e for e in result['effects'] if e['phase']=='returned']
    values=[RAW,b'one\r\n',b'one\r\ntwo\n',b'one\r\nthree\n']
    assert len(returned)==3 and len({e['started_seq'] for e in returned})==3
    for i,effect in enumerate(returned):
        assert effect['file_observation']=='observed' and effect['observation_scope']=='target'
        assert effect['file_changes']==[dict(path='unchanged.txt',kind='modified',
            before_sha256=hashlib.sha256(values[i]).hexdigest(),after_sha256=hashlib.sha256(values[i+1]).hexdigest())]
    assert not result['unknown_effects'] and result['business_acceptance']=='not_evaluated'


@pytest.mark.parametrize('running_product',[dict(OPTIONS,raw=b'\xef\xbb\xbfselected\r\n')],indirect=True)
async def test_original_bom_crlf_hash_is_not_normalized(running_product):
    result,_,_=await written(running_product)
    returned=next(e for e in result['effects'] if e['phase']=='returned')
    assert returned['file_changes'][0]['before_sha256']==hashlib.sha256(b'\xef\xbb\xbfselected\r\n').hexdigest()


@pytest.mark.parametrize('change',['tamper','missing'])
@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_physical_artifact_change_never_keeps_verified_receipt(running_product,change):
    env=running_product;result,event,path=await written(env)
    log=env.app.service._engines[result['session_id']].session;before=log._persistence.path.read_bytes()
    if change=='tamper':path.write_bytes(b'changed-outside-artifact-record')
    else:path.unlink()
    observed=await env.app.service.projects.execution(PID,result['execution_id'])
    item=next(a for a in observed['artifacts'] if a['artifact_id']==event.payload['record']['artifact_id'])
    assert item['integrity']==('mismatch' if change=='tamper' else 'unavailable')
    assert observed['status']=='completed' and observed['unknown_effects'] and observed['business_acceptance']=='not_evaluated'
    assert log._persistence.path.read_bytes()==before


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_fresh_application_reads_artifacts_without_writer_engine_or_repair(running_product,monkeypatch):
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    env=running_product;result,event,path=await written(env)
    before=env.app.service.projects.store.log_path.read_bytes()
    fresh=DesktopApp(cli.assemble_ctx(env.app.service.ctx.settings))
    async def forbidden(*args):raise AssertionError('artifact observation invoked repair')
    monkeypatch.setattr(fresh.service.surface_mgr(),'_ensure_repaired',forbidden)
    try:
        observed=await fresh.service.projects.execution(PID,result['execution_id'])
        assert observed['artifacts']==result['artifacts']
        assert not fresh.service._engines and not fresh.service._queues
        assert fresh.service.projects.store._session is None
        assert fresh.service.projects.store.log_path.read_bytes()==before
        from pyharness.errors import PyHError
        with pytest.raises(PyHError):await fresh.service.platform.artifact_content(event.payload['record']['artifact_id'])
    finally:await fresh.service.shutdown()


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_failed_after_snapshot_reports_unavailable_not_empty_changes(running_product,monkeypatch):
    from pyharness.application.execution_file_effects import FileObservation
    from pyharness.errors import PyHError
    original=FileObservation.capture;count=0
    async def failed_after(self):
        nonlocal count
        count+=1
        if count==2:raise PyHError('PERS-202')
        return await original(self)
    monkeypatch.setattr(FileObservation,'capture',failed_after)
    result,event,path=await written(running_product)
    returned=next(e for e in result['effects'] if e['phase']=='returned')
    assert returned['file_observation']=='unavailable' and returned['file_changes'] is None
    assert returned['observation_reason']=='snapshot_failed' and result['unknown_effects']


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_actual_partial_write_then_provider_raises_stays_unknown_without_replay(running_product,monkeypatch):
    from pyharness.application.platform_runtime import PlatformRuntime
    original=PlatformRuntime._execute_governed;calls=[]
    async def fail_after(self,name,args,ctx,provider):
        result=await original(self,name,args,ctx,provider)
        if name=='fs.write_file':calls.append(True);raise RuntimeError('synthetic failure after actual write')
        return result
    monkeypatch.setattr(PlatformRuntime,'_execute_governed',fail_after)
    env=running_product;result,event,path=await written(env)
    assert result['unknown_effects'] and calls==[True]
    assert [e['phase'] for e in result['effects']]==['started','unknown']
    assert result['effects'][-1]['file_changes'] is None
    for _ in range(2):await env.app.service.projects.execution(PID,result['execution_id'])
    assert calls==[True] and path.read_bytes()==AFTER.encode()


BINARY=dict(OPTIONS,agent=dict(sandbox_profile=dict(mode='host_approved'),permission_policy='standard'),
    script=[dict(id='binary-and-delete',name='exec.python_run',args=dict(code=
        "from pathlib import Path\nPath('unchanged.txt').unlink()\nPath('binary.bin').write_bytes(bytes([0,255,13,10]))"))])


@pytest.mark.controlled_process
@pytest.mark.parametrize('running_product',[BINARY],indirect=True)
async def test_real_command_creation_and_deletion_keep_raw_binary_effects(running_product):
    result,log=await execute_writes(running_product)
    returned=next(e for e in result['effects'] if e['phase']=='returned')
    assert returned['observation_scope']=='workspace' and returned['provider_ok'] is True
    assert returned['file_changes']==[
        dict(path='binary.bin',kind='created',before_sha256=None,after_sha256=hashlib.sha256(bytes([0,255,13,10])).hexdigest()),
        dict(path='unchanged.txt',kind='deleted',before_sha256=hashlib.sha256(RAW).hexdigest(),after_sha256=None)]
    binary=next(a for a in result['artifacts'] if a['filename']=='binary.bin')
    assert binary['integrity']=='verified' and not binary['download_allowed']


DELEGATED=dict(OPTIONS,script=[dict(id='writer-child',name='subagent.spawn',args=dict(task='Write unchanged.txt in your private workspace'))])


@pytest.mark.parametrize('running_product',[DELEGATED],indirect=True)
async def test_child_file_receipts_keep_child_identity_when_aggregated_by_root(running_product):
    from tests.unit.test_product_execution_children import publish_tools
    from tests.conftest import ScriptedAdapter
    env=running_product;publish_tools(env,'fs.write_file');original=env.adapter.chat
    child=ScriptedAdapter(env.adapter.model,OPTIONS['script'])
    async def routed(messages,tools=None,*,ctx):
        return await (child.chat if getattr(ctx,'subagent_depth',0) else original)(messages,tools,ctx=ctx)
    env.adapter.chat=routed
    result,root=await execute_writes(env)
    assert len(result['children'])==1 and result['children'][0]['status']=='completed'
    sid=result['children'][0]['session_id']
    assert result['artifacts'] and all(a['session_id']==sid and a['task_id']=='subagent:'+sid for a in result['artifacts'])
    assert all(a['integrity']=='verified' for a in result['artifacts']) and not result['unknown_effects']
    log=await env.app.service.require_session(sid)
    returned=next(e.payload for e in log.events_after(0) if e.type=='execution.effect' and e.payload['phase']=='returned')
    assert returned['file_changes'][0]['before_sha256']==hashlib.sha256(RAW).hexdigest()
    assert returned['file_changes'][0]['after_sha256']==hashlib.sha256(AFTER.encode()).hexdigest()
    assert (env.source/'unchanged.txt').read_bytes()==RAW


@pytest.mark.parametrize('change',['path','artifact_owner','export'])
@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_semantically_forged_file_or_artifact_facts_fail_closed_without_repair(running_product,change,monkeypatch):
    env=running_product;result,event,path=await written(env);service=env.app.service
    journal=service._engines[result['session_id']].session._persistence.path
    events=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines() if line.strip()]
    if change=='path':
        returned=next(e for e in events if e['type']=='execution.effect' and e['payload']['phase']=='returned')
        returned['payload']['file_changes'][0]['path']='different-target.txt'
    else:
        target=next(e for e in events if e['seq']==event.seq)
        if change=='artifact_owner':target['payload']['record']['session_id']='s-pa-'+'f'*32
        else:target['payload']['record'].update(download_allowed=True,approval_required=False)
    journal.write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events),encoding='utf-8')
    before=journal.read_bytes()
    async def forbidden(*args):raise AssertionError('forged execution history invoked repair')
    monkeypatch.setattr(service.surface_mgr(),'_ensure_repaired',forbidden)
    observed=await service.projects.execution(PID,result['execution_id'])
    assert observed['status']=='unknown' and observed['unknown_effects'] and observed['effects'] is None
    assert journal.read_bytes()==before


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_revoked_source_and_foreign_subject_cannot_read_receipt_artifact_bytes(running_product,monkeypatch):
    from pyharness.application.accounts import current_actor
    from pyharness.errors import PyHError
    from tests.unit.test_source_grants import GID
    env=running_product;result,event,path=await written(env)
    def forbidden(*args):raise AssertionError('unauthorized receipt read reached artifact bytes')
    monkeypatch.setattr('pyharness.application.execution_artifact_receipts.read_regular',forbidden)
    token=current_actor.set(dict(tenant='default',user_id='other-team-owner'))
    try:
        with pytest.raises(PyHError):await env.app.service.projects.execution(PID,result['execution_id'])
    finally:current_actor.reset(token)
    response=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
        headers={'Idempotency-Key':'withdraw-artifact-receipts'})
    assert response.status_code==200
    with pytest.raises(PyHError):await env.app.service.projects.execution(PID,result['execution_id'])


@pytest.mark.parametrize('running_product',[DELEGATED],indirect=True)
async def test_root_stop_while_child_waits_approval_cleans_children_and_both_workspaces(running_product):
    from tests.unit.test_product_execution_children import publish_tools
    from tests.unit.test_product_execution_cancellation import command
    from tests.conftest import ScriptedAdapter
    env=running_product;publish_tools(env,'fs.write_file');original=env.adapter.chat
    child=ScriptedAdapter(env.adapter.model,OPTIONS['script'])
    async def routed(messages,tools=None,*,ctx):
        return await (child.chat if getattr(ctx,'subagent_depth',0) else original)(messages,tools,ctx=ctx)
    env.adapter.chat=routed;projects=env.app.service.projects
    preview=await projects.execution_preview(PID,env.selection)
    started=await projects.start_execution(PID,dict(env.selection,execution_id='ex-'+'d'*16,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'waiting-child-approval')
    async with asyncio.timeout(10):
        while True:
            pending=(await env.app.service.pending_approvals())['pending']
            request=next((p for p in pending if p['session_id']!=started['session_id']),None)
            if request:break
            await asyncio.sleep(.01)
    observation=await projects.execution(PID,started['execution_id'])
    health=observation['control_observation']
    assert health['activity']=='waiting_approval' and health['heartbeat']['state']=='fresh'
    assert health['approval_waits']==[dict(session_id=request['session_id'],approval_id=request['approval_id'],ttl_ms=120000)]
    await projects.cancel_execution(PID,command(started),'stop-unapproved-child')
    result=await wait_terminal(projects,started['execution_id'])
    assert result['status']=='cancelled' and child.n==1
    spine=env.app.service._engines[started['session_id']];proxy=spine.platform_runtime.product_children
    assert not spine.subagent._children and not proxy.runtimes and not proxy.logs
    assert not proxy.original._stores and not proxy.original._child_buses and not proxy.original.approval_providers
    assert not (await env.app.service.pending_approvals())['pending']
    assert not list(env.app.service.platform.sandboxes.root.iterdir())
    assert (env.source/'unchanged.txt').read_bytes()==RAW


@pytest.mark.parametrize('running_product',[OPTIONS,DELEGATED],indirect=True)
async def test_withdrawal_during_cleanup_cannot_publish_completed_file_task(running_product):
    from tests.unit.test_product_execution_children import publish_tools
    from tests.unit.test_source_grants import GID
    from tests.conftest import ScriptedAdapter
    env=running_product;delegated=env.adapter.calls[0]['name']=='subagent.spawn'
    if delegated:
        publish_tools(env,'fs.write_file');original=env.adapter.chat
        child=ScriptedAdapter(env.adapter.model,OPTIONS['script'])
        async def routed(messages,tools=None,*,ctx):
            return await (child.chat if getattr(ctx,'subagent_depth',0) else original)(messages,tools,ctx=ctx)
        env.adapter.chat=routed
    manager=env.app.service.platform.sandboxes;backend=manager.backends['host_approved']
    destroy=backend.destroy;entered=asyncio.Event();resume=asyncio.Event();owned=[]
    async def held(record):
        if ('~subagent:' in record.run_id)==delegated:
            owned.append(record);entered.set();await resume.wait()
        await destroy(record)
    backend.destroy=held;projects=env.app.service.projects
    preview=await projects.execution_preview(PID,env.selection)
    started=await projects.start_execution(PID,dict(env.selection,execution_id='ex-'+'b'*16,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'cleanup-withdrawal')
    try:
        async with asyncio.timeout(10):
            while not entered.is_set():
                for request in (await env.app.service.pending_approvals())['pending']:
                    await env.app.service.decide_approval(request['approval_id'],'approve',sid=request['session_id'])
                await asyncio.sleep(.01)
        assert owned and any(p.exists() for p in manager.root.iterdir())
        response=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
            headers={'Idempotency-Key':'withdraw-during-cleanup'})
        assert response.status_code==200
        resume.set()
        result=await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=5)
        assert not result.ok,'Execution published success after authority changed during cleanup'
        if delegated:
            sid=owned[0].run_id.split('~',1)[0]
            events=[json.loads(line) for line in (env.app.service._tenant_sessions_dir()/(sid+'.jsonl')).read_text(encoding='utf-8').splitlines()]
            phases=[e['payload']['phase'] for e in events if e['type']=='execution.child']
            assert phases==['bound','running','failed']
        assert not list(manager.root.iterdir()) and not backend.roots
    finally:resume.set()


async def test_remote_file_observation_never_uploads_or_replaces_remote_workspace(tmp_path):
    """Real manager/preparation; in-process remote protocol, not physical isolation."""
    from types import SimpleNamespace
    from pyharness.core.sandbox import SandboxManager,WorkspaceInputPreparation
    from pyharness.core.sandbox_models import SandboxProfile
    from pyharness.application.execution_file_effects import FileObservation
    class Remote:
        def __init__(self):self.downloads=0;self.uploads=0
        async def create(self,record,root,profile):assert (root/'input.txt').read_bytes()==RAW
        async def destroy(self,record):pass
        async def download(self,record):self.downloads+=1
        async def upload(self,record):self.uploads+=1
    backend=Remote();manager=SandboxManager(tmp_path/'owned');manager.backends['remote']=backend
    def populate(root):(root/'input.txt').write_bytes(RAW)
    preparation=WorkspaceInputPreparation(hashlib.sha256(RAW).hexdigest(),populate,lambda:None,lambda root:None)
    record=await manager.create_for_attempt('test-read-only-observation',SandboxProfile(mode='remote'),preparation,authorize=lambda:None)
    async def sandbox(ctx):return record.sandbox_id
    runtime=SimpleNamespace(product_execution=SimpleNamespace(authorize=lambda:None),sandbox=sandbox,
        locks={},platform=SimpleNamespace(sandboxes=manager))
    try:
        observer=FileObservation(runtime,'fs.write_file',{'path':'input.txt'},SimpleNamespace(task_id='observation'))
        await observer.before()
        assert backend.downloads==1 and backend.uploads==0,'A read-only file observation uploaded the remote workspace'
        async with manager.snapshot(record.sandbox_id):pass
        assert backend.uploads==1,'The original mutable snapshot contract must remain intact'
    finally:await manager.destroy(record.sandbox_id)
