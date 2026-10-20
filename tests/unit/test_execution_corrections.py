"""S08 trusted correction contracts at the real next logical round, not in-flight tools."""
import asyncio,hashlib,json
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_product_execution_http import command
from tests.unit.test_source_grants import PID,GID

SCRIPT=[dict(id='correction-kv',name='storage.kv',args=dict(op='set',key='once',value='original-tool'))]

def correction(started,**changes):
    return dict(schema_version=1,correction_id='cr-'+'1'*16,expected_revision=0,
        execution_ref={k:started[k] for k in ('execution_id','revision','digest','source_seq')},
        text='Supplement: report the exact KV value without repeating its write.',**changes)

async def held_execution(env):
    entered=asyncio.Event();resume=asyncio.Event()
    async def hold(ctx,response):entered.set();await resume.wait()
    env.controls.after_chat=hold;body=await command(env)
    started=await env.app.service.projects.start_execution(PID,body,'correction-run')
    await asyncio.wait_for(entered.wait(),5)
    return started,resume

def endpoint(started):return f'/api/v1/projects/{PID}/executions/'+started['execution_id']+'/corrections'

@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_trusted_pending_correction_reaches_next_actual_model_context_once(running_product):
    env=running_product;started,resume=await held_execution(env)
    try:
        log=env.app.service._engines[started['session_id']].session;before=list(log.events_after(0))
        body=correction(started)
        pending=await env.client.post(endpoint(started),json=body,headers={'Idempotency-Key':'first-correction'})
        assert pending.status_code==200,pending.text
        assert pending.headers.get('cache-control')=='no-store'
        p=pending.json();assert p['revision']==1 and p['application_status']=='pending' and p['persisted'] is True
        assert list(log.events_after(0))==before and env.adapter.n==1
        assert body['text'] not in str(env.messages[0])
        same=await env.client.post(endpoint(started),json=body,headers={'Idempotency-Key':'first-correction'})
        assert same.status_code==200 and same.json()['digest']==p['digest']
        env.controls.after_chat=None;resume.set()
        await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=8)
        assert env.adapter.n==2 and body['text'] in str(env.messages[1])
        applied=[e for e in log.events_after(0) if e.type=='execution.correction_applied']
        assert len(applied)==1 and applied[0].payload['correction_ref']=={k:p[k] for k in ('correction_id','revision','digest','source_seq')}
        effects=[e for e in log.events_after(0) if e.type=='execution.effect' and e.payload['phase']=='started']
        assert len(effects)==1 and effects[0].payload['call_id']=='correction-kv'
        view=await env.client.get(endpoint(started))
        assert view.status_code==200 and view.json()['corrections'][0]['application_status']=='applied',view.text
    finally:env.controls.after_chat=None;resume.set()


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_dropped_real_http_correction_reply_reconciles_original_key_without_new_revision(running_product):
    import httpx
    env=running_product;started,resume=await held_execution(env)
    class DropReply(httpx.AsyncBaseTransport):
        async def handle_async_request(self,request):
            response=await httpx.ASGITransport(app=env.app.api).handle_async_request(request)
            assert response.status_code==200
            await response.aread()
            raise httpx.ReadError('synthetic committed HTTP reply lost',request=request)
    try:
        body=correction(started)
        async with httpx.AsyncClient(transport=DropReply(),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':env.app._api_token}) as lost:
            with pytest.raises(httpx.ReadError):await lost.post(endpoint(started),json=body,headers={'Idempotency-Key':'lost-correction'})
        query=await env.client.get(f'/api/v1/projects/{PID}/correction-submissions/by-key?key=lost-correction')
        assert query.status_code==200 and query.json()['status']=='committed',query.text
        value=query.json()['correction'];assert value['revision']==1 and value['application_status']=='pending'
        repeated=await env.client.post(endpoint(started),json=body,headers={'Idempotency-Key':'lost-correction'})
        assert repeated.status_code==200 and repeated.json()['digest']==value['digest']
        view=await env.client.get(endpoint(started));assert view.json()['total']==1 and env.adapter.n==1
    finally:env.controls.after_chat=None;resume.set()


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_forged_applied_reference_is_rejected_even_with_recomputed_checkpoint_hashes(running_product):
    from pyharness.application.execution_effects import flush_confirmed
    env=running_product;started,resume=await held_execution(env)
    try:
        r=await env.client.post(endpoint(started),json=correction(started),headers={'Idempotency-Key':'forge-application'})
        assert r.status_code==200,r.text
        env.controls.after_chat=None;resume.set()
        await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=8)
        log=env.app.service._engines[started['session_id']].session;await flush_confirmed(log)
        path=log._persistence.path;rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        applied=next(e for e in rows if e['type']=='execution.correction_applied');applied['payload']['correction_ref']['digest']='f'*64
        for i,e in enumerate(rows):
            if e['type']=='execution.checkpoint':
                e['payload']['prefix_digest']=hashlib.sha256(json.dumps(rows[:i],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        from pyharness.events import Envelope
        from pyharness.application.execution_checkpoints import validate_checkpoints
        validate_checkpoints([Envelope.model_validate(e) for e in rows],started['execution_id'],started['digest'],started['source_seq'],started['task_id'])
        raw=('\n'.join(json.dumps(e,ensure_ascii=False,separators=(',',':')) for e in rows)+'\n').encode();path.write_bytes(raw)
        observed=await env.client.get(f'/api/v1/projects/{PID}/executions/'+started['execution_id'])
        assert observed.status_code==200 and observed.json()['status']=='unknown' and observed.json()['unknown_effects'],observed.text
        assert path.read_bytes()==raw and env.adapter.n==2
    finally:env.controls.after_chat=None;resume.set()


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_cold_correction_view_validates_original_application_without_writer_or_model(running_product,monkeypatch):
    import copy,httpx
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from pyharness.application.execution_effects import flush_confirmed
    env=running_product;started,resume=await held_execution(env)
    try:
        r=await env.client.post(endpoint(started),json=correction(started),headers={'Idempotency-Key':'cold-correction'})
        assert r.status_code==200,r.text
        env.controls.after_chat=None;resume.set()
        await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=8)
        log=env.app.service._engines[started['session_id']].session;await flush_confirmed(log)
        control=env.app.service.projects.store.log_path.read_bytes();journal=log._persistence.path.read_bytes()
        cold=DesktopApp(cli.assemble_ctx(copy.deepcopy(env.app.service.ctx.settings)))
        def forbidden(*args,**kwargs):raise AssertionError('cold correction read opened a writer')
        monkeypatch.setattr(cold.service.projects.store,'_open',forbidden)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
                view=await client.get(endpoint(started))
                assert view.status_code==200 and view.json()['corrections'][0]['application_status']=='applied',view.text
                assert view.json()['can_submit'] is False
                observed=await client.get(f'/api/v1/projects/{PID}/correction-submissions/by-key?key=cold-correction')
                assert observed.status_code==200 and observed.json()['status']=='committed',observed.text
            assert not cold.service._engines and not cold.service._queues and env.adapter.n==2
            assert control==env.app.service.projects.store.log_path.read_bytes() and journal==log._persistence.path.read_bytes()
        finally:await cold.service.shutdown()
    finally:env.controls.after_chat=None;resume.set()


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_failed_applied_confirmation_never_sends_next_model_or_repeats_completed_tool(running_product,monkeypatch):
    from pyharness.core.session import SessionLog
    env=running_product;started,resume=await held_execution(env);original=SessionLog.append
    try:
        r=await env.client.post(endpoint(started),json=correction(started),headers={'Idempotency-Key':'failed-apply'})
        assert r.status_code==200,r.text
        async def fail(self,type_,*args,**kwargs):
            if type_=='execution.correction_applied':raise OSError('synthetic applied confirmation failed')
            return await original(self,type_,*args,**kwargs)
        monkeypatch.setattr(SessionLog,'append',fail)
        env.controls.after_chat=None;resume.set()
        result=await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=8)
        assert not result.ok and env.adapter.n==1
        log=env.app.service._engines[started['session_id']].session
        assert not any(e.type=='execution.correction_applied' for e in log.events_after(0))
        effects=[e.payload['phase'] for e in log.events_after(0) if e.type=='execution.effect']
        assert effects==['started','returned']
        kv=env.app.service._tenant_sessions_dir()/(started['session_id']+'.kv.json')
        assert json.loads(kv.read_text(encoding='utf-8'))==dict(once='original-tool')
    finally:env.controls.after_chat=None;resume.set()


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_materialization_failure_keeps_correction_pending_without_applied_claim(running_product,monkeypatch):
    from pyharness.application import vision
    env=running_product;started,resume=await held_execution(env);original=vision.model_messages
    try:
        r=await env.client.post(endpoint(started),json=correction(started),headers={'Idempotency-Key':'materialization-failure'})
        assert r.status_code==200,r.text
        async def fail(runtime,messages,ctx):
            if any('Trusted task supplement' in str(m.get('content')) for m in messages):
                raise OSError('synthetic actual materialization unavailable')
            return await original(runtime,messages,ctx)
        monkeypatch.setattr(vision,'model_messages',fail)
        env.controls.after_chat=None;resume.set()
        result=await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=8)
        assert not result.ok and env.adapter.n==1
        view=await env.client.get(endpoint(started))
        assert view.status_code==200 and view.json()['corrections'][0]['application_status']=='pending',view.text
        log=env.app.service._engines[started['session_id']].session
        assert not any(e.type=='execution.correction_applied' for e in log.events_after(0))
    finally:env.controls.after_chat=None;resume.set()

@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_competing_correction_revisions_conflict_and_newest_pending_supersedes(running_product):
    env=running_product;started,resume=await held_execution(env)
    try:
        first=correction(started)
        r=await env.client.post(endpoint(started),json=first,headers={'Idempotency-Key':'correction-v1'})
        assert r.status_code==200,r.text
        commands=[dict(first,expected_revision=1,text='Replacement supplement '+str(i)) for i in range(2)]
        rows=await asyncio.gather(*[env.client.post(endpoint(started),json=b,headers={'Idempotency-Key':'correction-race-'+str(i)}) for i,b in enumerate(commands)])
        assert sorted(r.status_code for r in rows)==[200,409],[r.text for r in rows]
        winner=next(r.json() for r in rows if r.status_code==200)
        assert winner['revision']==2
        view=(await env.client.get(endpoint(started))).json()
        assert view['corrections'][0]['application_status']=='superseded' and view['corrections'][1]['application_status']=='pending'
        env.controls.after_chat=None;resume.set()
        await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=8)
        assert winner['text'] in str(env.messages[1]) and env.adapter.n==2
        log=env.app.service._engines[started['session_id']].session
        assert [e.payload['correction_ref']['revision'] for e in log.events_after(0) if e.type=='execution.correction_applied']==[2]
    finally:env.controls.after_chat=None;resume.set()

@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_correction_rejects_client_authority_and_inexact_execution(running_product):
    env=running_product;started,resume=await held_execution(env)
    try:
        body=correction(started);before=env.app.service.projects.store.log_path.read_bytes()
        for change in [dict(created_by='forged'),dict(schema_version=True),dict(allowed_tools=['exec.shell_run'])]:
            r=await env.client.post(endpoint(started),json=dict(body,**change),headers={'Idempotency-Key':'bad-correction'})
            assert r.status_code==422 and r.headers.get('cache-control')=='no-store',r.text
        r=await env.client.post(endpoint(started),json=dict(body,execution_ref=dict(body['execution_ref'],source_seq=body['execution_ref']['source_seq']+1)),headers={'Idempotency-Key':'wrong-exact-execution'})
        assert r.status_code in (403,409),r.text
        assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==1
    finally:env.controls.after_chat=None;resume.set()


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_malformed_unicode_supplement_is_rejected_without_control_append(running_product):
    env=running_product;started,resume=await held_execution(env)
    try:
        before=env.app.service.projects.store.log_path.read_bytes();body=correction(started)
        body['text']='bad scalar '+chr(0xD800)
        response=await env.client.post(endpoint(started),content=json.dumps(body,ensure_ascii=True).encode('ascii'),
            headers={'Content-Type':'application/json','Idempotency-Key':'malformed-unicode'})
        assert response.status_code==422,response.text
        assert before==env.app.service.projects.store.log_path.read_bytes() and env.adapter.n==1
    finally:env.controls.after_chat=None;resume.set()

@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_source_withdrawal_blocks_new_correction_and_pending_application(running_product):
    env=running_product;started,resume=await held_execution(env)
    try:
        first=correction(started)
        r=await env.client.post(endpoint(started),json=first,headers={'Idempotency-Key':'correction-before-revoke'})
        assert r.status_code==200,r.text
        revoked=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-before-apply'})
        assert revoked.status_code==200,revoked.text
        r=await env.client.post(endpoint(started),json=dict(first,expected_revision=1,text='Cannot revive authority'),headers={'Idempotency-Key':'after-withdraw'})
        assert r.status_code==403,r.text
        assert (await env.client.get(endpoint(started))).status_code==403
        env.controls.after_chat=None;resume.set()
        result=await env.app.service._queues[started['session_id']].wait_for(started['task_id'],timeout=8)
        assert not result.ok and env.adapter.n==1
        log=env.app.service._engines[started['session_id']].session
        assert not any(e.type=='execution.correction_applied' for e in log.events_after(0))
        assert not (env.app.service._tenant_sessions_dir()/(started['session_id']+'.kv.json')).exists()
    finally:env.controls.after_chat=None;resume.set()
