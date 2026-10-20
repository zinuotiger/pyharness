"""Real original queue failures become private, zero-effect public candidates."""
import hashlib
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.accounts import current_actor
from pyharness.application.workspace_store import canonical
from pyharness.errors import PyHError
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_product_context_observation import completed
from tests.unit.test_run_observation_comparison_api import second
from tests.unit.test_source_grants import PID,GID

URL='/api/v1/projects/'+PID+'/failure-candidates'


async def preview(env,ident):
    return await env.client.post(URL,json=dict(schema_version=1,execution_id=ident))


async def test_actual_failure_preview_preserves_control_bytes_and_never_replays_calls(running_product):
    env=running_product;failure=await second(env,failure=True)
    assert failure['status']=='failed'
    before=env.app.service.projects.store.log_path.read_bytes();calls=env.adapter.n
    response=await preview(env,failure['execution_id'])
    assert response.status_code==200,response.text
    value=response.json()
    assert value['status']=='confirmation_required' and not value['persisted'] and not value['execution_authorized']
    assert len(value['candidate_digest'])==len(value['origin']['prefix_digest'])==64
    assert value['origin']['execution_ref']['execution_id']==failure['execution_id']
    assert value['origin']['failure_kind']=='execution_failed'
    through=value['origin']['observed_seq']
    assert through>=failure['observed_seq']
    original=env.app.service.surface_mgr()._logs[failure['session_id']]
    prefix=[e.model_dump(mode='json') for e in original.events_after(0) if e.seq<=through]
    assert len(prefix)==through
    assert hashlib.sha256(canonical(prefix)).hexdigest()==value['origin']['prefix_digest']
    assert response.headers['cache-control']=='no-store'
    assert env.adapter.n==calls and env.app.service.projects.store.log_path.read_bytes()==before


async def test_completed_run_is_not_silently_labelled_a_failure(running_product):
    env=running_product;success=await completed(env)
    response=await preview(env,success['execution_id'])
    assert response.status_code==422 and response.json()['reason']=='regression_failure_required',response.text
    assert response.headers['cache-control']=='no-store'


async def test_actual_other_subject_and_source_revocation_block_candidates(running_product):
    env=running_product;failure=await second(env,failure=True);calls=env.adapter.n
    token=current_actor.set(dict(tenant='default',user_id='other-real-subject'))
    try:
        # API-token middleware intentionally installs its own local operator.
        # Exercise the actual requester at the shared application boundary.
        with pytest.raises(PyHError) as error:
            await env.app.service.projects.failure_candidate(PID,dict(schema_version=1,execution_id=failure['execution_id']))
        assert error.value.code=='GRD-403'
    finally:current_actor.reset(token)
    withdrawn=await env.client.post('/api/v1/source-grants/'+GID+'/revoke',
        json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-regression-source'})
    assert withdrawn.status_code==200,withdrawn.text
    response=await preview(env,failure['execution_id'])
    assert response.status_code==403 and response.headers['cache-control']=='no-store',response.text
    assert env.adapter.n==calls


async def test_cold_candidate_preview_creates_no_writer_engine_queue_or_new_model_calls(running_product):
    env=running_product;failure=await second(env,failure=True);calls=env.adapter.n
    cfg=env.app.service.ctx.settings
    roots=(env.app.service.projects.store.log_path.parent,env.app.service._tenant_sessions_dir())
    await env.app.service.shutdown()
    journals={p:hashlib.sha256(p.read_bytes()).hexdigest() for root in roots for p in root.rglob('*.jsonl')}
    cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',
                headers={'X-PyHarness-Token':cold._api_token}) as client:
            response=await client.post(URL,json=dict(schema_version=1,execution_id=failure['execution_id']))
            assert response.status_code==200,response.text
        assert cold.service.projects.store._store is None and not cold.service._engines and not cold.service._queues
        assert env.adapter.n==calls and all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in journals.items())
    finally:await cold.service.shutdown()


async def test_real_terminal_failure_prefix_stays_confirmable_after_later_close_facts(running_product):
    from tests.unit.test_regression_dataset_api import confirmed_case,save_case
    env=running_product;failure=await second(env,failure=True)
    response=await preview(env,failure['execution_id']);assert response.status_code==200,response.text
    value=response.json();original=env.app.service.surface_mgr()._logs[failure['session_id']]
    events=list(original.events_after(0));terminal=next(e.seq for e in events if e.type=='execution.lifecycle' and e.payload['phase']=='failed')
    fixed=dict(value['origin'],observed_seq=terminal,
        prefix_digest=hashlib.sha256(canonical([e.model_dump(mode='json') for e in events if e.seq<=terminal])).hexdigest())
    value['origin']=fixed
    fields='schema_version project_id origin observed_input_text observed_input_state observed_output_text observed_output_state root_tool_call_count policy assertion_version'.split()
    value['candidate_digest']=hashlib.sha256(canonical({k:value[k] for k in fields})).hexdigest()
    calls=env.adapter.n
    response=await save_case(env,confirmed_case(value),'confirm-frozen-terminal-prefix')
    assert response.status_code==200,response.text
    assert response.json()['selection']['origin']==fixed and env.adapter.n==calls


async def test_confirmed_body_cannot_relabel_a_successful_source_as_a_real_failure(running_product):
    from tests.unit.test_regression_dataset_api import CID,save_case
    from tests.unit.test_product_tasks import ref
    env=running_product;success=await completed(env);original=env.app.service.surface_mgr()._logs[success['session_id']]
    events=list(original.events_after(0))
    origin=dict(tenant_id=env.app.service.tenant_id,subject_ref='local-operator',execution_ref=ref(success,'execution_id'),
        session_id=success['session_id'],observed_seq=events[-1].seq,
        prefix_digest=hashlib.sha256(canonical([e.model_dump(mode='json') for e in events])).hexdigest(),
        failure_kind='execution_failed',acceptance_ref=None)
    body=dict(schema_version=1,case_id=CID,expected_revision=0,project_revision=1,title='不得伪造失败',origin=origin,
        candidate_digest='0'*64,input_text='Explicit input',assertions=[dict(kind='output_contains',expected='done')],confirmed=True)
    before=env.app.service.projects.store.log_path.read_bytes();response=await save_case(env,body,'forged-failure-type')
    assert response.status_code==409 and response.json()['reason']=='regression_failure_prefix',response.text
    assert env.app.service.projects.store.log_path.read_bytes()==before
