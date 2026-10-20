"""Real account DB and authenticated setup; product calls use the same server actor."""
import asyncio
from contextlib import contextmanager
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.accounts import Accounts,current_actor
from pyharness.errors import PyHError
from tests.support import isolated_settings
from tests.unit.test_source_grants import PID,GID
from tests.unit.test_run_attempts import ready,submit,submission
from tests.unit.test_product_tasks import ref


@contextmanager
def actor(value):
    token=current_actor.set(value)
    try:yield
    finally:current_actor.reset(token)


@pytest.mark.parametrize('delegated',[False,True])
@pytest.mark.parametrize('via_http',[False,True])
async def test_managed_same_team_owner_cannot_stop_creator_and_role_changes_recheck_cached_runtime(tmp_path,adapter_factory,delegated,via_http,monkeypatch):
    cfg=isolated_settings(tmp_path/'app')
    script=[dict(id='team-child',name='subagent.spawn',args=dict(task='synthetic-private-team-child'))] if delegated else []
    adapter=adapter_factory(cfg,script);children=[];requests=2 if delegated else 1
    entered=asyncio.Event();resume=asyncio.Event();original=adapter.chat
    async def hold(messages,tools=None,*,ctx):
        result=await original(messages,tools,ctx=ctx)
        if not delegated or getattr(ctx,'subagent_depth',0):
            if delegated:children.append(ctx.session.sid)
            entered.set();await resume.wait()
        return result
    adapter.chat=hold
    app=DesktopApp(cli.assemble_ctx(cfg));transport=httpx.ASGITransport(app=app.api)
    accounts=Accounts(app.service._storage_root());team='product-team'
    source=tmp_path/'source';source.mkdir();(source/'unchanged.txt').write_bytes(b'captured\r\n')
    try:
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as alice, \
                   httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as bob:
            identities=[]
            for client,name in ((alice,'product-alice'),(bob,'product-bob')):
                created=await client.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                    json=dict(username=name,password='synthetic-team-password',tenant=team))
                assert created.status_code==201,created.text
                login=await client.post('/api/account/login',json=dict(username=name,password='synthetic-team-password',tenant=team))
                assert login.status_code==200,login.text
                identities.append(accounts.authenticate(client.cookies.get('pyharness_account')))
            alice_actor,bob_actor=identities
            model=await alice.post('/api/settings/models',json=dict(id='only-team',model=cfg.llm.model,
                base_url='https://synthetic.invalid/v1',api_key='synthetic-team-private-key'))
            assert model.status_code==200,model.text
            assert (await alice.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=0,name='team execution'),
                headers={'Idempotency-Key':'project'})).status_code==200
            task=await ready(alice,source);attempt=(await submit(alice,submission(task))).json()
            agent=(await alice.post('/api/v1/agents',json=dict(name='Private team execution',
                allowed_tools=['subagent.spawn'] if delegated else []))).json()
            published=await alice.post('/api/v1/agents/'+agent['agent_id']+'/publish')
            assert published.status_code==200,published.text
            version=published.json();service=app._service_registry.get(team);projects=service.projects
            selection=dict(schema_version=1,attempt_ref=ref(attempt,'attempt_id'),
                agent_ref={k:version[k] for k in ('agent_id','version','sha256')})
            base=f'/api/v1/projects/{PID}'
            if via_http:
                preview_response=await alice.post(base+'/execution-previews',json=selection)
                assert preview_response.status_code==200,preview_response.text
                preview=preview_response.json()
                response=await alice.post(base+'/executions',json=dict(selection,execution_id='ex-'+'2'*16,
                    confirmation_digest=preview['confirmation_digest']),headers={'Idempotency-Key':'team-start'})
                assert response.status_code==200,response.text
                started=response.json()
            else:
                with actor(alice_actor):
                    preview=await projects.execution_preview(PID,selection)
                    started=await projects.start_execution(PID,dict(selection,execution_id='ex-'+'2'*16,
                        confirmation_digest=preview['confirmation_digest']),'team-start')
            await asyncio.wait_for(entered.wait(),5)
            if via_http and not delegated:
                monkeypatch.setattr('pyharness.desktop.accounts_api.ACTIVE_RUN_QUOTA',1)
                repeated=await alice.post(base+'/executions',json=dict(selection,execution_id='ex-'+'2'*16,
                    confirmation_digest=preview['confirmation_digest']),headers={'Idempotency-Key':'team-start'})
                assert repeated.status_code==200 and repeated.json()['execution_id']==started['execution_id'],repeated.text
                second_attempt=await submit(alice,submission(task,attempt_id='ra-'+'f'*16),'team-second-attempt')
                assert second_attempt.status_code==200,second_attempt.text
                second_selection=dict(selection,attempt_ref=ref(second_attempt.json(),'attempt_id'))
                second_preview=await alice.post(base+'/execution-previews',json=second_selection)
                assert second_preview.status_code==200,second_preview.text
                refused=await alice.post(base+'/executions',json=dict(second_selection,execution_id='ex-'+'4'*16,
                    confirmation_digest=second_preview.json()['confirmation_digest']),headers={'Idempotency-Key':'over-active-quota'})
                assert refused.status_code==429 and refused.json()['code']=='active_run_quota',refused.text
                assert adapter.n==requests and len(service._queues)==1
            body=dict(schema_version=1,cancellation_id='ec-'+'3'*16,
                execution_ref={k:started[k] for k in ('execution_id','revision','digest','source_seq')})
            before=projects.store.log_path.read_bytes()
            if via_http and not delegated:
                correction_path=base+'/executions/'+started['execution_id']+'/corrections'
                correction_body=dict(schema_version=1,correction_id='cr-'+'7'*16,expected_revision=0,
                    execution_ref=body['execution_ref'],text='Trusted owner supplement pending at the next root context.')
                corrected=await alice.post(correction_path,json=correction_body,headers={'Idempotency-Key':'team-correction'})
                assert corrected.status_code==200 and corrected.json()['application_status']=='pending',corrected.text
                before=projects.store.log_path.read_bytes()
                assert (await bob.get(correction_path)).status_code==403
                assert (await bob.post(correction_path,json=correction_body,headers={'Idempotency-Key':'foreign-correction'})).status_code==403
                recovery_path=base+'/executions/'+started['execution_id']+'/recovery-plan'
                repair_path=base+'/executions/'+started['execution_id']+'/index-repairs'
                plan=await alice.get(recovery_path);assert plan.status_code==200,plan.text
                repair_body=dict(schema_version=1,repair_id='er-'+'8'*16,execution_ref=body['execution_ref'],plan_digest=plan.json()['plan_digest'])
                assert (await bob.get(recovery_path)).status_code==403
                assert (await bob.post(repair_path,json=repair_body,headers={'Idempotency-Key':'foreign-index-repair'})).status_code==403
            if via_http:
                response=await bob.get(base+'/run-attempts/'+attempt['attempt_id']+'/execution-control')
                assert response.status_code==403 and response.headers.get('cache-control')=='no-store'
                response=await bob.get(base+'/executions/'+started['execution_id'])
                assert response.status_code==403 and response.headers.get('cache-control')=='no-store'
                response=await bob.post(base+'/execution-cancellations',json=body,headers={'Idempotency-Key':'foreign-http-stop'})
                assert response.status_code==403,response.text
            with actor(bob_actor):
                with pytest.raises(PyHError):await projects.cancel_execution(PID,body,'foreign-team-stop')
                with pytest.raises(PyHError):await projects.execution(PID,started['execution_id'])
                with pytest.raises(PyHError):await service.require_session(started['session_id'])
                if delegated:
                    with pytest.raises(PyHError):await service.require_session(children[0])
                    with pytest.raises(PyHError):await service.cancel_subagent(started['session_id'],children[0])
                assert started['session_id'] not in {r['sid'] for r in (await service.list_sessions())['sessions']}
            assert projects.store.log_path.read_bytes()==before and adapter.n==requests
            if delegated:
                with actor(alice_actor):
                    child=await service.require_session(children[0])
                    assert child.tenant_id==team and child.get(2).payload['subject_ref']==alice_actor['user_id']
            accounts.membership(team,'product-alice','viewer')
            if via_http:
                if not delegated:
                    assert (await alice.get(correction_path)).status_code==403
                    assert (await alice.post(correction_path,json=dict(correction_body,expected_revision=1),headers={'Idempotency-Key':'viewer-correction'})).status_code==403
                    assert (await alice.get(recovery_path)).status_code==403
                    assert (await alice.post(repair_path,json=repair_body,headers={'Idempotency-Key':'viewer-index-repair'})).status_code==403
                response=await alice.get(base+'/run-attempts/'+attempt['attempt_id']+'/execution-control')
                assert response.status_code==403 and response.headers.get('cache-control')=='no-store'
                response=await alice.post(base+'/execution-cancellations',json=body,headers={'Idempotency-Key':'viewer-http-stop'})
                assert response.status_code==403,response.text
            with actor(alice_actor):
                with pytest.raises(PyHError):await projects.cancel_execution(PID,body,'viewer-stop')
                if delegated:
                    with pytest.raises(PyHError):await service.cancel_subagent(started['session_id'],children[0])
            accounts.membership(team,'product-alice','member')
            if via_http and not delegated:
                corrected=await alice.post(correction_path,json=dict(correction_body,expected_revision=1,text='Trusted member update.'),headers={'Idempotency-Key':'member-correction'})
                assert corrected.status_code==200 and corrected.json()['revision']==2,corrected.text
                plan=await alice.get(recovery_path);assert plan.status_code==200,plan.text
                member_repair=await alice.post(repair_path,json=dict(repair_body,plan_digest=plan.json()['plan_digest']),headers={'Idempotency-Key':'member-live-index-repair'})
                assert member_repair.status_code==403 and 'index_repair_not_permitted' in member_repair.text,member_repair.text
            revoked=await bob.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
                headers={'Idempotency-Key':'team-withdraw'})
            assert revoked.status_code==200,revoked.text
            if via_http and not delegated:
                assert (await alice.post(correction_path,json=dict(correction_body,expected_revision=2),headers={'Idempotency-Key':'withdrawn-correction'})).status_code==403
                assert (await alice.get(recovery_path)).status_code==403
                assert (await alice.post(repair_path,json=repair_body,headers={'Idempotency-Key':'withdrawn-index-repair'})).status_code==403
            if via_http:
                response=await alice.get(base+'/run-attempts/'+attempt['attempt_id']+'/execution-control')
                assert response.status_code==200 and response.json()['execution_ref']['execution_id']==started['execution_id'],response.text
                response=await alice.post(base+'/execution-cancellations',json=body,headers={'Idempotency-Key':'creator-member-stop'})
                assert response.status_code==200,response.text
            else:
                with actor(alice_actor):await projects.cancel_execution(PID,body,'creator-member-stop')
            result=await service._queues[started['session_id']].wait_for(started['task_id'],timeout=4)
            assert not result.ok and result.code=='cancelled' and adapter.n==requests
            assert not service._engines[started['session_id']].subagent._children
            with actor(alice_actor):
                observed=await projects.cancellation_submission(PID,'creator-member-stop')
                assert observed['cancellation']['execution_stopped'] is True
                with pytest.raises(PyHError):await service.require_session(started['session_id'])
            accounts.membership(team,'product-alice','remove')
            if via_http:
                response=await alice.get(base+'/cancellation-submissions/by-key',params={'key':'creator-member-stop'})
                assert response.status_code in (401,403),response.text
            with actor(alice_actor):
                with pytest.raises(PyHError):await projects.cancellation_submission(PID,'creator-member-stop')
    finally:
        resume.set()
        for service in app._service_registry.all():await service.shutdown()
