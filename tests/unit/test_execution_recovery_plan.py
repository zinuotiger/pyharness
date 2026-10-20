"""Recovery diagnostics bind actual old facts; GET has no restore or repair authority."""
import asyncio,copy,hashlib,json
from pathlib import Path
import httpx,pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_execution_liveness import held
from tests.unit.test_product_execution_cancellation import command as stop
from tests.unit.test_source_grants import PID,GID


def endpoint(started):return f'/api/v1/projects/{PID}/executions/'+started['execution_id']+'/recovery-plan'


async def test_live_recovery_plan_has_exact_journal_anchor_and_never_restores(running_product):
    from pyharness.application.execution_checkpoints import prefix_digest
    env=running_product;started,release=await held(env)
    try:
        log=env.app.service._engines[started['session_id']].session
        from pyharness.application.execution_effects import flush_confirmed
        await flush_confirmed(log)
        control=env.app.service.projects.store.log_path.read_bytes();raw=log._persistence.path.read_bytes()
        r=await env.client.get(endpoint(started));assert r.status_code==200,r.text
        v=r.json();assert r.headers['cache-control']=='no-store' and v['execution_ref']=={k:started[k] for k in ('execution_id','revision','digest','source_seq')}
        assert v['continuation_authorized'] is False and v['automatic_dispatch'] is False and v['repair_authorized'] is False
        # Captured state is now implemented; existence still confers no launch
        # authority and never removes the original live-writer prohibition.
        assert 'execution_live' in v['continuation_blockers'] and 'continuation_gate_required' in v['continuation_blockers']
        assert v['restore_state_available'] is True
        assert v['journals'][0]['prefix_digest']==prefix_digest(list(log.events_after(0)))
        assert v['journals'][0]['observed_seq']==log.stats()['seq']
        assert v['latest_checkpoint']['checkpoint_id']==next(e.payload['checkpoint_id'] for e in log.events_after(0) if e.type=='execution.checkpoint')
        assert env.adapter.n==1 and raw==log._persistence.path.read_bytes() and control==env.app.service.projects.store.log_path.read_bytes()
    finally:release.set()


@pytest.mark.parametrize('running_product',[dict(raw=b'exact-selected-input\r\n',script=[dict(id='plan-child',name='subagent.spawn',args=dict(task='Write private workspace output'))],agent=dict(sandbox_profile=dict(mode='host_approved')))],indirect=True)
async def test_child_artifact_corruption_blocks_recovery_without_repair_or_replay(running_product):
    from tests.unit.test_product_execution_children import publish_tools
    from tests.unit.test_product_execution_file_effects import execute_writes,OPTIONS
    from tests.conftest import ScriptedAdapter
    env=running_product;publish_tools(env,'fs.write_file');original=env.adapter.chat;child=ScriptedAdapter(env.adapter.model,OPTIONS['script'])
    async def routed(messages,tools=None,*,ctx):return await (child.chat if getattr(ctx,'subagent_depth',0) else original)(messages,tools,ctx=ctx)
    env.adapter.chat=routed;receipt,root=await execute_writes(env)
    path=env.app.service.platform.root/'artifacts'/receipt['artifacts'][0]['artifact_id']
    path.write_bytes(b'actual child artifact changed')
    r=await env.client.get(endpoint(receipt));assert r.status_code==200,r.text
    v=r.json();assert v['unknown_effects'] is True and 'unknown_effects' in v['continuation_blockers']
    assert len(v['journals'])==2 and any(a['integrity']=='mismatch' for a in v['artifacts'])
    assert path.read_bytes()==b'actual child artifact changed' and child.n==2 and env.adapter.n==2


async def test_cold_plan_is_readonly_and_missing_index_is_not_created(running_product,monkeypatch,tmp_path):
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    env=running_product;started,release=await held(env);cfg=copy.deepcopy(env.app.service.ctx.settings)
    db=tmp_path/'never-created-index.sqlite';cfg.storage.db_path=str(db)
    cold=DesktopApp(cli.assemble_ctx(cfg))
    def forbidden(*a,**kw):raise AssertionError('cold plan opened writer')
    monkeypatch.setattr(cold.service.projects.store,'_open',forbidden)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            r=await client.get(endpoint(started));assert r.status_code==200,r.text
            v=r.json();assert v['execution_status']=='interrupted' and v['index_observation']['state']=='not_built'
            assert not db.exists() and not cold.service._engines and not cold.service._queues and env.adapter.n==1
            db.write_bytes(b'not a sqlite database')
            r=await client.get(endpoint(started));assert r.status_code==200,r.text
            assert r.json()['index_observation']['state']=='unavailable' and db.read_bytes()==b'not a sqlite database'
            assert str(db) not in r.text
    finally:await cold.service.shutdown();release.set()


async def test_bad_checkpoint_blocks_plan_without_repairing_authoritative_journal(running_product):
    from tests.unit.test_product_execution_http import command
    env=running_product;body=await command(env);p=env.app.service.projects
    started=await p.start_execution(PID,body,'bad-plan-prefix');await wait_terminal(p,started['execution_id'])
    path=env.app.service._engines[started['session_id']].session._persistence.path
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    next(e for e in rows if e['type']=='execution.checkpoint')['payload']['prefix_digest']='0'*64
    raw=('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n').encode();path.write_bytes(raw)
    r=await env.client.get(endpoint(started));assert r.status_code==200,r.text
    v=r.json();assert v['execution_status']=='unknown' and 'journal_unconfirmed' in v['continuation_blockers'] and not v['journals']
    assert v['continuation_authorized'] is False and v['repair_authorized'] is False and path.read_bytes()==raw and env.adapter.n==1


async def test_withdrawn_source_denies_plan_without_returning_journal_or_private_body(running_product):
    env=running_product;started,release=await held(env)
    try:
        r=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'revoke-plan-input'})
        assert r.status_code==200,r.text
        r=await env.client.get(endpoint(started));assert r.status_code==403,r.text
        assert r.headers['cache-control']=='no-store' and started['session_id'] not in r.text and env.adapter.n==1
    finally:release.set()


async def test_recovery_diagnostics_recognize_actual_state_without_granting_continuation(running_product):
    env=running_product;started,release=await held(env)
    try:
        r=await env.client.get(endpoint(started));assert r.status_code==200,r.text
        value=r.json()
        assert value['restore_state_available'] is True,'diagnostics still claim no state although actual state was captured'
        assert 'continuation_gate_required' in value['continuation_blockers']
        assert 'continuation_not_implemented' not in value['continuation_blockers']
        assert value['continuation_authorized'] is False and value['automatic_dispatch'] is False and env.adapter.n==1
    finally:release.set()
