"""Real post-read membership, historical provenance and final-dispatch boundaries."""
import json
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_product_context_observation import completed,BASE
from tests.unit.test_delivery_handoffs import delivery_team
from tests.unit.test_source_grants import PID,GID


async def test_real_member_withdrawal_after_context_read_prevents_private_http_response(delivery_team,monkeypatch):
    import pyharness.application.product_observations as observation
    env=delivery_team;env.accounts.membership('delivery-team','delivery-stranger','owner')
    original=observation.read_execution_log;changed=[]
    async def withdraw_after_read(*args,**kwargs):
        log=await original(*args,**kwargs)
        env.accounts.membership('delivery-team','delivery-owner','remove');changed.append(True)
        return log
    monkeypatch.setattr(observation,'read_execution_log',withdraw_after_read)
    response=await env.owner.get(BASE+'/executions/'+env.result['execution_id']+'/contexts')
    assert changed and response.status_code==403,response.text
    assert response.headers['cache-control']=='no-store' and 'public_view' not in response.text


@pytest.mark.parametrize('field',['messages_digest','execution_id','prefix_seq'])
async def test_typed_but_forged_historical_context_provenance_is_rejected_on_cold_read(running_product,field):
    env=running_product;result=await completed(env);service=env.app.service
    path=service.surface_mgr()._logs[result['session_id']]._persistence.path;cfg=service.ctx.settings
    await service.shutdown();lines=path.read_bytes().splitlines(keepends=True);changed=[]
    for n,line in enumerate(lines):
        row=json.loads(line)
        if row['type']!='execution.context':continue
        row['payload'][field]=('0'*64 if field=='messages_digest' else 'ex-'+'d'*16 if field=='execution_id' else row['seq'])
        lines[n]=(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8');changed.append(True)
    assert changed,'The test must modify an actual persisted context, not a synthetic detached model'
    path.write_bytes(b''.join(lines));cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            response=await client.get(BASE+'/executions/'+result['execution_id']+'/contexts')
        assert response.status_code==503,response.text
        assert response.json()['reason']=='public_context_record_binding'
        assert not cold.service._engines and not cold.service._queues and cold.service.projects.store._store is None
        assert env.adapter.n==1
    finally:await cold.service.shutdown()


async def test_source_withdrawal_after_prepared_fact_prevents_the_actual_adapter_call(running_product,monkeypatch):
    import pyharness.application.execution_context_observation as observation
    env=running_product;original=observation.record_context;changed=[]
    async def withdraw_after_preparation(*args,**kwargs):
        await original(*args,**kwargs)
        response=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-after-context'})
        assert response.status_code==200,response.text
        changed.append(True)
    monkeypatch.setattr(observation,'record_context',withdraw_after_preparation)
    projects=env.app.service.projects;preview=await projects.execution_preview(PID,env.selection);ident='ex-'+'d'*16
    try:await projects.start_execution(PID,dict(env.selection,execution_id=ident,confirmation_digest=preview['confirmation_digest']),'context-then-withdraw')
    except PyHError as exc:assert exc.code=='GRD-403'
    resources,_=projects.store._grant_state();authorization=resources[ident]['execution_authorization']
    queue=env.app.service._queues[authorization['session_id']]
    result=await queue.wait_for(authorization['task_id'],timeout=10)
    assert changed and not result.ok and env.adapter.n==0
    events=list(env.app.service.surface_mgr()._logs[authorization['session_id']].events_after(0))
    assert len([e for e in events if e.type=='execution.context'])==1
    assert not any(e.type in ('llm.response','execution.effect') for e in events)
