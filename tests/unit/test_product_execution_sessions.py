"""Product input journals remain private through the legacy session surfaces."""
import pytest
from pyharness.application.accounts import current_actor
from pyharness.errors import PyHError
from tests.unit.test_execution_authorization import execution_ready, preview, start_command, commit
from tests.unit.test_source_grants import grant_app


@pytest.fixture
async def private_run(execution_ready):
    env = execution_ready
    env.receipt, _ = await commit(env, start_command(env, await preview(env)))
    env.sid = env.receipt['session_id']
    env.manager = env.app.service.surface_mgr()
    binding = dict(schema_version=1, execution_id=env.receipt['execution_id'],
        phase='bound', authorization_digest=env.receipt['digest'], authorization_seq=env.receipt['source_seq'],
        subject_ref='local-operator', reason=None)
    env.journal = await env.manager.create_product_session(env.sid, binding, task_id=env.receipt['task_id'])
    await env.journal.append('user.message', {'content': 'private-product-material'}, actor='user',
        task_id=env.receipt['task_id'], sync=True)
    yield env


async def test_authorized_legacy_read_is_read_only_and_does_not_construct_runtime(private_run):
    env = private_run; service = env.app.service
    before = env.manager._stores[env.sid].path.read_bytes()
    view = await service.require_session(env.sid)
    assert view.derive_messages()[-1]['content'] == 'private-product-material'
    with pytest.raises(PyHError):
        await view.append('user.message', {'content': 'injected'}, actor='user', sync=True)
    assert env.manager._stores[env.sid].path.read_bytes() == before
    assert not service._engines and not service._queues


async def test_other_subject_cannot_observe_cached_log_or_lists(private_run):
    env = private_run; service = env.app.service
    service._logs[env.sid] = env.journal
    token = current_actor.set({'tenant': service.tenant_id, 'user_id': 'other-team-owner'})
    try:
        with pytest.raises(PyHError): await service.require_session(env.sid)
        assert env.sid not in {row['sid'] for row in (await service.list_sessions())['sessions']}
        with pytest.raises(PyHError): await service.session_messages(env.sid)
        with pytest.raises(PyHError): await service.session_timeline(env.sid)
        with pytest.raises(PyHError): await service.event_detail(env.sid, 3)
        with pytest.raises(PyHError): await service.platform.history_search('private', sid=env.sid)
    finally:
        current_actor.reset(token)


async def test_legacy_input_queue_and_delete_cannot_mutate_product_execution(private_run):
    env = private_run; service = env.app.service
    before = env.manager._stores[env.sid].path.read_bytes()
    for operation in [lambda: service.queue_for(env.sid, env.journal),
                      lambda: service.create_message(env.sid, 'injected'),
                      lambda: service.delete_session(env.sid),
                      lambda: service.platform.start_run({'session_id': env.sid, 'text': 'injected'})]:
        with pytest.raises(PyHError): await operation()
    assert env.manager._stores[env.sid].path.read_bytes() == before
    assert not service._engines and not service._queues


async def test_bad_product_tail_never_invokes_repair_or_reopens_writer(private_run, monkeypatch):
    env = private_run; service = env.app.service; calls = []
    async def repair(*args): calls.append(True)
    monkeypatch.setattr(env.manager, '_ensure_repaired', repair)
    path = env.manager._stores[env.sid].path
    with path.open('ab') as stream: stream.write(b'{broken')
    before = path.read_bytes()
    with pytest.raises(PyHError): await service.require_session(env.sid)
    assert not calls and path.read_bytes() == before


async def test_reserved_prefix_without_binding_is_hidden_and_never_repaired(execution_ready):
    env = execution_ready; service = env.app.service
    sid = 's-pa-' + 'f' * 32
    before = list(service._tenant_sessions_dir().glob('*'))
    with pytest.raises(PyHError): await service.require_session(sid)
    assert list(service._tenant_sessions_dir().glob('*')) == before


async def test_binding_creation_is_once_and_persists_before_exposing_handle(private_run):
    env = private_run
    before = env.manager._stores[env.sid].path.read_bytes()
    with pytest.raises(PyHError):
        await env.manager.create_product_session(env.sid,
            env.journal.get(2).payload, task_id=env.receipt['task_id'])
    assert env.manager._stores[env.sid].path.read_bytes() == before


async def test_private_events_are_excluded_from_legacy_all_session_stream(private_run):
    from pyharness.desktop.projection import StreamClient
    env = private_run; subscriber = StreamClient(tenant=env.app.service.tenant_id)
    async with env.app.hub.register(subscriber):
        await env.app.hub.forward('user.message', env.journal.get(3))
        assert subscriber.queue.empty()


async def test_private_approval_and_question_routes_recheck_the_current_subject(private_run):
    from types import SimpleNamespace
    env = private_run; service = env.app.service; decisions = []
    async def approve(aid, **kwargs):decisions.append(aid)
    request = SimpleNamespace(session_id=env.sid, args_summary='private-provider-arguments', tool='fs.write_file')
    approval = SimpleNamespace(_pending={9: request}, approve_async=approve)
    question = SimpleNamespace(_pending={8: object()}, _session=env.journal,
        pending_list=lambda: [dict(ask_id=8, question='private-question')], answer=lambda *a, **kw: decisions.append(8))
    service._approvals[env.sid] = approval
    service._engines[env.sid] = SimpleNamespace(ask=question, subagent=None, approval=approval)
    token = current_actor.set({'tenant': service.tenant_id, 'user_id': 'other-team-owner'})
    try:
        assert (await service.pending_approvals())['count'] == 0
        assert (await service.pending_asks())['count'] == 0
        with pytest.raises(PyHError):await service.decide_approval(9, 'approve', sid=env.sid)
        with pytest.raises(PyHError):await service.answer_ask(8, text='injected', sid=env.sid)
        assert decisions == []
    finally:
        current_actor.reset(token)
        service._engines.pop(env.sid, None)


@pytest.mark.parametrize('change', [dict(execution_id='ex-'+'8'*16),
    dict(authorization_digest='f'*64), dict(subject_ref='other-owner'), {}])
async def test_semantically_mismatched_execution_facts_fail_closed_without_repair(private_run, change):
    env = private_run
    payload = dict(env.journal.get(2).payload, phase='completed', **change)
    await env.journal.append('execution.lifecycle', payload, actor='system',
        task_id=env.receipt['task_id'], sync=True)
    path = env.manager._stores[env.sid].path; before = path.read_bytes()
    with pytest.raises(PyHError, match='PERS-202'):await env.app.service.require_session(env.sid)
    assert path.read_bytes() == before
