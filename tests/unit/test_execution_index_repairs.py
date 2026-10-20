"""Bounded derived-index repair over actual isolated SQLite and immutable journals."""
import copy,sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
import httpx,pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_product_execution_http import command
from tests.unit.test_execution_liveness import held
from tests.unit.test_source_grants import PID


def endpoint(started):return f'/api/v1/projects/{PID}/executions/'+started['execution_id']+'/index-repairs'
def plan_endpoint(started):return endpoint(started).removesuffix('index-repairs')+'recovery-plan'


def repair(started,plan,ordinal=1):
    return dict(schema_version=1,repair_id='er-'+str(ordinal)*16,
        execution_ref={k:started[k] for k in ('execution_id','revision','digest','source_seq')},plan_digest=plan['plan_digest'])


@asynccontextmanager
async def cold_stale_index(env,finish=None):
    p=env.app.service.projects;body=await command(env);body['confirm_host_boundary']=True
    started=await p.start_execution(PID,body,'index-repair-source')
    if finish is not None:await finish(env,started)
    await wait_terminal(p,started['execution_id'])
    cfg=copy.deepcopy(env.app.service.ctx.settings);db=Path(env.app.service.effective_settings().storage.db_path)
    journal=env.app.service._engines[started['session_id']].session._persistence.path
    await env.app.service.shutdown();original=journal.read_bytes()
    with sqlite3.connect(db) as conn:
        conn.execute('DELETE FROM events_fts WHERE session_id=?',(started['session_id'],))
        conn.execute('DELETE FROM fts_rows WHERE session_id=?',(started['session_id'],))
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as client:
            yield app,client,started,journal,original,db
    finally:await app.service.shutdown()


async def test_actual_index_rebuild_is_audited_idempotent_and_never_changes_original_journal(running_product):
    env=running_product
    async with cold_stale_index(env) as (app,client,started,journal,original,db):
        observed=await client.get(plan_endpoint(started));assert observed.status_code==200,observed.text
        plan=observed.json();assert plan['index_observation']['state']=='stale'
        body=repair(started,plan)
        r=await client.post(endpoint(started),json=body,headers={'Idempotency-Key':'repair-index-once'})
        assert r.status_code==200,r.text
        value=r.json();assert value['phase']=='succeeded' and value['attempt']==1 and value['index_observation']['state']=='current'
        assert value['persisted'] is True and journal.read_bytes()==original and env.adapter.n==1
        assert not app.service._engines and not app.service._queues
        control=app.service.projects.store.log_path.read_bytes()
        same=await client.post(endpoint(started),json=body,headers={'Idempotency-Key':'repair-index-once'})
        assert same.status_code==200 and same.json()['digest']==value['digest'],same.text
        query=await client.get(f'/api/v1/projects/{PID}/index-repair-submissions/by-key?key=repair-index-once')
        assert query.status_code==200 and query.json()['status']=='committed' and query.json()['repair']['phase']=='succeeded',query.text
        assert control==app.service.projects.store.log_path.read_bytes() and journal.read_bytes()==original and env.adapter.n==1


async def test_repeated_index_fault_is_persistently_limited_to_two_attempts(running_product,monkeypatch):
    from pyharness.errors import raise_code
    calls=[]
    async def failed(self,*,session_id=None):calls.append(session_id);raise_code('PERS-202',reason='synthetic index rebuild failure')
    monkeypatch.setattr('pyharness.core.query_maintenance.QueryMaintenance.rebuild',failed)
    env=running_product
    async with cold_stale_index(env) as (app,client,started,journal,original,db):
        for ordinal in (1,2):
            plan=(await client.get(plan_endpoint(started))).json()
            r=await client.post(endpoint(started),json=repair(started,plan,ordinal),headers={'Idempotency-Key':'failed-index-'+str(ordinal)})
            assert r.status_code==200,r.text
            assert r.json()['phase']=='failed' and r.json()['attempt']==ordinal
        before=app.service.projects.store.log_path.read_bytes();plan=(await client.get(plan_endpoint(started))).json()
        r=await client.post(endpoint(started),json=repair(started,plan,3),headers={'Idempotency-Key':'third-fault-new-key'})
        assert r.status_code in (400,403,409,422),r.text
        assert 'index_repair_limit_reached' in r.text
        assert calls==[started['session_id']]*2 and before==app.service.projects.store.log_path.read_bytes()
        assert journal.read_bytes()==original and env.adapter.n==1
        # The ceiling is a control-log fact, not a per-process counter.
        cfg=copy.deepcopy(app.service.ctx.settings);await app.service.shutdown();fresh=DesktopApp(cli.assemble_ctx(cfg))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':fresh._api_token}) as again:
                plan=(await again.get(plan_endpoint(started))).json()
                r=await again.post(endpoint(started),json=repair(started,plan,4),headers={'Idempotency-Key':'fourth-after-restart'})
                assert r.status_code==403 and 'index_repair_limit_reached' in r.text,r.text
                assert calls==[started['session_id']]*2 and journal.read_bytes()==original
        finally:await fresh.service.shutdown()


async def test_live_execution_never_becomes_repairable_from_an_index_plan(running_product):
    env=running_product;started,release=await held(env)
    try:
        plan=(await env.client.get(plan_endpoint(started))).json();before=env.app.service.projects.store.log_path.read_bytes()
        r=await env.client.post(endpoint(started),json=repair(started,plan),headers={'Idempotency-Key':'do-not-repair-live'})
        assert r.status_code in (400,403,409,422),r.text
        assert before==env.app.service.projects.store.log_path.read_bytes() and env.adapter.n==1
    finally:release.set()


@pytest.mark.parametrize('mode',['failed-start','fake-start','failed-result'])
async def test_unconfirmed_control_receipt_never_blindly_repeats_index_mutation(running_product,monkeypatch,mode):
    from pyharness.core.session import SessionLog
    from pyharness.core.query_maintenance import QueryMaintenance
    original_append=SessionLog.append;original_rebuild=QueryMaintenance.rebuild;calls=[]
    async def rebuild(self,*,session_id=None):calls.append(session_id);return await original_rebuild(self,session_id=session_id)
    monkeypatch.setattr(QueryMaintenance,'rebuild',rebuild)
    async with cold_stale_index(running_product) as (app,client,started,journal,original,db):
        async def append(self,type_,payload,**kwargs):
            if type_=='workspace.resource_committed' and payload.get('resource_kind')=='index_repair':
                revision=payload['revision']
                if mode=='failed-start' and revision==1 or mode=='failed-result' and revision==2:raise OSError('synthetic repair receipt failure')
                if mode=='fake-start' and revision==1:
                    before=self.get(self.stats()['seq']);return before.model_copy(update=dict(seq=before.seq+1,type=type_,payload=payload,actor='user'))
            return await original_append(self,type_,payload,**kwargs)
        monkeypatch.setattr(SessionLog,'append',append)
        body=repair(started,(await client.get(plan_endpoint(started))).json())
        r=await client.post(endpoint(started),json=body,headers={'Idempotency-Key':'uncertain-index-repair'})
        assert r.status_code!=200 and 'PERS-202' in r.text,r.text
        assert calls==([started['session_id']] if mode=='failed-result' else []) and journal.read_bytes()==original
        monkeypatch.setattr(SessionLog,'append',original_append)
        query=await client.get(f'/api/v1/projects/{PID}/index-repair-submissions/by-key?key=uncertain-index-repair')
        assert query.status_code==200,query.text
        if mode=='failed-result':
            assert query.json()['status']=='committed' and query.json()['repair']['phase']=='started'
            # Reopen the poisoned owner through a fresh app; original-key retry
            # may observe the start, but may not perform the repair again.
            cfg=copy.deepcopy(app.service.ctx.settings);await app.service.shutdown();fresh=DesktopApp(cli.assemble_ctx(cfg))
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':fresh._api_token}) as again:
                    r=await again.post(endpoint(started),json=body,headers={'Idempotency-Key':'uncertain-index-repair'})
                    assert r.status_code==200 and r.json()['phase']=='started',r.text
                    assert calls==[started['session_id']] and journal.read_bytes()==original
            finally:await fresh.service.shutdown()
        else:assert query.json()['status']=='not_found'


async def test_lost_committed_repair_http_reply_reconciles_original_key_without_rebuild(running_product):
    async with cold_stale_index(running_product) as (app,client,started,journal,original,db):
        body=repair(started,(await client.get(plan_endpoint(started))).json())
        class DropReply(httpx.AsyncBaseTransport):
            async def handle_async_request(self,request):
                response=await httpx.ASGITransport(app=app.api).handle_async_request(request)
                assert response.status_code==200;await response.aread()
                raise httpx.ReadError('synthetic committed index reply lost',request=request)
        async with httpx.AsyncClient(transport=DropReply(),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as lost:
            with pytest.raises(httpx.ReadError):await lost.post(endpoint(started),json=body,headers={'Idempotency-Key':'lost-index-reply'})
        before=app.service.projects.store.log_path.read_bytes()
        r=await client.get(f'/api/v1/projects/{PID}/index-repair-submissions/by-key?key=lost-index-reply')
        assert r.status_code==200 and r.json()['status']=='committed' and r.json()['repair']['phase']=='succeeded',r.text
        assert before==app.service.projects.store.log_path.read_bytes() and journal.read_bytes()==original and running_product.adapter.n==1


async def test_plan_cannot_repair_a_different_index_with_the_same_stale_watermark(running_product):
    import shutil
    async with cold_stale_index(running_product) as (app,client,started,journal,original,db):
        body=repair(started,(await client.get(plan_endpoint(started))).json())
        other=db.with_name('other-index.sqlite');shutil.copy2(db,other)
        with sqlite3.connect(other) as conn:
            conn.execute('DELETE FROM events_fts WHERE session_id=?',(started['session_id'],))
            conn.execute('DELETE FROM fts_rows WHERE session_id=?',(started['session_id'],))
        before=other.read_bytes();control=app.service.projects.store.log_path.read_bytes()
        app.service.ctx.settings.storage.db_path=str(other)
        r=await client.post(endpoint(started),json=body,headers={'Idempotency-Key':'do-not-change-index-scope'})
        assert r.status_code in (400,403,409,422),r.text
        assert other.read_bytes()==before and control==app.service.projects.store.log_path.read_bytes()
        assert journal.read_bytes()==original and running_product.adapter.n==1


@pytest.mark.parametrize('running_product',[dict(script=[dict(id='denied-index-source',name='fs.write_file',args=dict(path='unchanged.txt',content='never write'))],agent=dict(sandbox_profile=dict(mode='host_approved')))],indirect=True)
async def test_actual_manual_denial_is_never_treated_as_an_automatically_repairable_failure(running_product):
    import asyncio
    async def deny(env,started):
        async with asyncio.timeout(8):
            while True:
                pending=(await env.app.service.pending_approvals())['pending']
                if pending:break
                await asyncio.sleep(.01)
        request=pending[0]
        await env.app.service.decide_approval(request['approval_id'],'deny',sid=request['session_id'])
    async with cold_stale_index(running_product,finish=deny) as (app,client,started,journal,original,db):
        plan=(await client.get(plan_endpoint(started))).json()
        assert 'security_refusal' in plan['continuation_blockers']
        before=app.service.projects.store.log_path.read_bytes()
        r=await client.post(endpoint(started),json=repair(started,plan),headers={'Idempotency-Key':'do-not-auto-fix-denial'})
        assert r.status_code==403,r.text
        assert journal.read_bytes()==original and before==app.service.projects.store.log_path.read_bytes()


async def test_journal_change_after_plan_observation_cannot_lend_old_plan_to_new_source(running_product,monkeypatch):
    import json
    from pyharness.application import product_index_repairs as module
    actual=module.observe_recovery_plan
    async with cold_stale_index(running_product) as (app,client,started,journal,original,db):
        body=repair(started,(await client.get(plan_endpoint(started))).json())
        async def changed(*args,**kwargs):
            plan=await actual(*args,**kwargs)
            rows=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
            next(e for e in rows if e['type']=='agent.message')['payload']['content']='synthetic source changed after observation'
            journal.write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in rows)+'\n',encoding='utf-8')
            return plan
        monkeypatch.setattr(module,'observe_recovery_plan',changed)
        control=app.service.projects.store.log_path.read_bytes()
        r=await client.post(endpoint(started),json=body,headers={'Idempotency-Key':'old-plan-new-source'})
        assert r.status_code in (400,403,409,422,500,503),r.text
        assert control==app.service.projects.store.log_path.read_bytes()
        with sqlite3.connect(db) as conn:assert conn.execute('SELECT count(*) FROM events_fts WHERE session_id=?',(started['session_id'],)).fetchone()[0]==0
