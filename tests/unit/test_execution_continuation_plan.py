"""Exact private state observations must not silently grant continuation."""
import copy
import httpx,pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_execution_liveness import held
from tests.unit.test_product_execution_http import command
from tests.unit.test_source_grants import PID,GID

def endpoint(ex):return f'/api/v1/projects/{PID}/executions/{ex}/continuation-plan'

async def test_live_state_available_is_separate_from_permission_to_continue(running_product):
    env=running_product;started,release=await held(env)
    try:
        log=env.app.service._engines[started['session_id']].session
        from pyharness.application.execution_effects import flush_confirmed
        await flush_confirmed(log)
        raw=log._persistence.path.read_bytes();control=env.app.service.projects.store.log_path.read_bytes()
        r=await env.client.get(endpoint(started['execution_id']));assert r.status_code==200,r.text
        p=r.json();assert p['state_available'] and p['state']['integrity']=='verified'
        assert {'execution_live','journal_writer_active'}<=set(p['blockers'])
        assert not p['can_continue'] and not p['continuation_authorized'] and not p['automatic_dispatch']
        assert p['state']['filesystem_capture']=='no_workspace' and p['state']['kv_present'] is False
        assert r.headers['cache-control']=='no-store' and 'messages' not in p['state'] and 'kv_data_b64' not in p['state']
        assert env.adapter.n==1 and raw==log._persistence.path.read_bytes() and control==env.app.service.projects.store.log_path.read_bytes()
    finally:release.set()

async def test_cold_state_plan_does_not_take_over_original_writer(running_product,monkeypatch):
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    env=running_product;started,release=await held(env);cold=DesktopApp(cli.assemble_ctx(copy.deepcopy(env.app.service.ctx.settings)))
    def forbidden(*a,**kw):raise AssertionError('observation opened writer')
    monkeypatch.setattr(cold.service.projects.store,'_open',forbidden)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            r=await client.get(endpoint(started['execution_id']));assert r.status_code==200,r.text
            p=r.json();assert p['execution_status']=='interrupted' and p['state_available'] and 'journal_writer_active' in p['blockers']
        assert not cold.service._engines and not cold.service._queues and env.adapter.n==1
    finally:await cold.service.shutdown();release.set()

async def test_bad_state_plan_does_not_repair_private_blob(running_product):
    env=running_product;body=await command(env);p=env.app.service.projects
    await p.start_execution(PID,body,'bad-continue-blob');done=await wait_terminal(p,body['execution_id'])
    log=env.app.service._engines[done['session_id']].session
    fact=next(e for e in log.events_after(0) if e.type=='execution.state_checkpoint')
    path=env.app.service.platform.root/'execution-state'/fact.payload['state_digest'];path.write_bytes(b'bad actual blob')
    journal=log._persistence.path.read_bytes();r=await env.client.get(endpoint(done['execution_id']));assert r.status_code==200,r.text
    v=r.json();assert not v['state_available'] and 'state_integrity_unconfirmed' in v['blockers'] and not v['can_continue']
    assert path.read_bytes()==b'bad actual blob' and journal==log._persistence.path.read_bytes() and env.adapter.n==1

async def test_changed_current_model_blocks_continuation_without_mutating_original(running_product):
    env=running_product;started,release=await held(env)
    try:
        env.app.service.ctx.settings.llm.temperature=.123
        r=await env.client.get(endpoint(started['execution_id']));assert r.status_code==200,r.text
        p=r.json();assert p['state_available'] and 'configuration_changed' in p['blockers'] and not p['can_continue'] and env.adapter.n==1
    finally:release.set()

async def test_latest_model_result_is_not_hidden_by_older_state_snapshot(running_product):
    env=running_product;body=await command(env);p=env.app.service.projects
    await p.start_execution(PID,body,'completed-not-continuable');await wait_terminal(p,body['execution_id'])
    r=await env.client.get(endpoint(body['execution_id']));assert r.status_code==200,r.text
    v=r.json();assert v['state_available'] and {'execution_completed','checkpoint_superseded'}<=set(v['blockers']) and not v['can_continue']

async def test_withdrawn_source_refuses_state_plan_and_private_identity(running_product):
    env=running_product;started,release=await held(env)
    try:
        r=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'continue-source-revoke'});assert r.status_code==200,r.text
        r=await env.client.get(endpoint(started['execution_id']));assert r.status_code==403,r.text
        assert r.headers['cache-control']=='no-store' and started['session_id'] not in r.text and env.adapter.n==1
    finally:release.set()
