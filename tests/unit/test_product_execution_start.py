"""S07 real agent-loop/queue over captured product input; model is a scripted adapter."""
import asyncio
import copy
import json
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings
from tests.unit.test_source_grants import PID, GID
from tests.unit.test_run_attempts import ready, submission, submit
from tests.unit.test_product_tasks import ref
from tests.unit.test_selected_owned_inputs import RAW


@pytest.fixture
async def running_product(tmp_path, adapter_factory, request, monkeypatch):
    from types import SimpleNamespace
    cfg = isolated_settings(tmp_path / 'app')
    options = getattr(request, 'param', [])
    options = options if isinstance(options, dict) else dict(script=options)
    script = options.get('script', [])
    if 'raw' in options:
        monkeypatch.setattr('tests.unit.test_selected_owned_inputs.RAW', options['raw'])
    adapter = adapter_factory(cfg, script)
    controls = SimpleNamespace(after_chat=None)
    messages = []; original = adapter.chat
    async def capture(values, tools=None, *, ctx):
        messages.append(copy.deepcopy(values))
        result = await original(values, tools, ctx=ctx)
        if controls.after_chat is not None:await controls.after_chat(ctx, result)
        return result
    adapter.chat = capture
    app = DesktopApp(cli.assemble_ctx(cfg)); source = tmp_path / 'captured-source'; source.mkdir()
    (source / 'unchanged.txt').write_bytes(b'unchanged\r\n')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1',
                                headers={'X-PyHarness-Token': app._api_token}) as client:
        response = await client.post('/api/v1/projects', json=dict(project_id=PID, expected_revision=0, name='execution test'),
            headers={'Idempotency-Key': 'project'})
        assert response.status_code == 200
        task = await ready(client, source); attempt = (await submit(client, submission(task))).json()
        agent = app.service.platform.save_agent(dict(name='Published execution test',
            allowed_tools=sorted({c['name'] for c in script}), **options.get('agent', {})))
        version = app.service.platform.publish_agent(agent['agent_id'])
        selection = dict(schema_version=1, attempt_ref=ref(attempt, 'attempt_id'),
            agent_ref=dict(agent_id=version['agent_id'], version=version['version'], sha256=version['sha256']))
        yield SimpleNamespace(app=app, client=client, source=source, task=task, attempt=attempt,
            selection=selection, adapter=adapter, messages=messages, controls=controls)
    await app.service.shutdown()


async def wait_terminal(projects, execution_id):
    for _ in range(300):
        result = await projects.execution(PID, execution_id)
        if result['status'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            queue = projects.service._queues.get(result['session_id'])
            if queue is None or queue.status().running is None:
                from pyharness.application.execution_effects import flush_confirmed
                journal = projects.service.surface_mgr()._logs.get(result['session_id'])
                if journal is not None:await flush_confirmed(journal)
                return result
        await asyncio.sleep(0.01)
    raise AssertionError('product execution never reached a terminal observation')


async def test_explicit_start_uses_exact_captured_data_once_and_queries_are_read_only(running_product):
    env = running_product; projects = env.app.service.projects; store = projects.store
    (env.source / 'unchanged.txt').write_bytes(b'host-new-body-not-selected')
    before = store.log_path.read_bytes()
    preview = await projects.execution_preview(PID, env.selection)
    assert store.log_path.read_bytes() == before and env.adapter.n == 0
    command = dict(env.selection, execution_id='ex-' + '5' * 16,
        confirmation_digest=preview['confirmation_digest'], confirm_host_boundary=False)
    first, second = await asyncio.gather(projects.start_execution(PID, command, 'real-start'),
        projects.start_execution(PID, command, 'real-start'))
    assert first['execution_id'] == second['execution_id']
    result = await wait_terminal(projects, command['execution_id'])
    assert result['status'] == 'completed' and result['execution_started'] is True
    assert result['business_acceptance'] == 'not_evaluated'
    assert env.adapter.n == 1
    observed = str(env.messages)
    assert 'host-new-body-not-selected' not in observed
    prompt = next(m['content'] for m in env.messages[0] if m['role'] == 'user' and m['content'].startswith('Execute the objective'))
    material = json.loads(prompt.split('\n', 1)[1])
    assert material['files'][0]['text'].encode('utf-8') == RAW
    assert [item['path'] for item in material['files']] == ['unchanged.txt']
    assert material['task']['objective'] == env.task['objective']
    journal = env.app.service.surface_mgr()._logs[result['session_id']]
    types = [e.type for e in journal.events_after(0)]
    assert types.count('task.enqueued') == 1
    assert [e.payload['phase'] for e in journal.events_after(0) if e.type == 'execution.lifecycle'] == ['bound', 'running', 'completed']
    before_control = store.log_path.read_bytes(); before_journal = journal._persistence.path.read_bytes()
    assert (await projects.start_execution(PID, command, 'real-start'))['status'] == 'completed'
    for _ in range(2):await projects.execution(PID, command['execution_id'])
    assert store.log_path.read_bytes() == before_control and journal._persistence.path.read_bytes() == before_journal
    assert env.adapter.n == 1 and (env.source / 'unchanged.txt').read_bytes() == b'host-new-body-not-selected'


async def test_model_change_after_preview_cannot_authorize_or_create_runtime(running_product):
    env = running_product; projects = env.app.service.projects
    preview = await projects.execution_preview(PID, env.selection)
    before = projects.store.log_path.read_bytes()
    env.app.service.ctx.settings.llm.temperature = 0.123
    with pytest.raises(PyHError):
        await projects.start_execution(PID, dict(env.selection, execution_id='ex-'+'6'*16,
            confirmation_digest=preview['confirmation_digest']), 'changed-model')
    assert projects.store.log_path.read_bytes() == before
    assert env.adapter.n == 0 and not env.app.service._queues and not env.app.service._engines


KV_SCRIPT = [dict(id='owned-kv', name='storage.kv', args=dict(op='set', key='proof', value='owned-value'))]


@pytest.mark.parametrize('running_product', [KV_SCRIPT], indirect=True)
async def test_real_tool_effect_and_second_model_round_share_original_authorization(running_product):
    env = running_product; projects = env.app.service.projects
    preview = await projects.execution_preview(PID, env.selection)
    command = dict(env.selection, execution_id='ex-'+'7'*16, confirmation_digest=preview['confirmation_digest'])
    await projects.start_execution(PID, command, 'actual-kv')
    result = await wait_terminal(projects, command['execution_id'])
    assert result['status'] == 'completed' and env.adapter.n == 2
    assert not result['unknown_effects']
    assert [e['phase'] for e in result['effects']] == ['started', 'returned']
    assert {e['call_id'] for e in result['effects']} == {'owned-kv'}
    assert result['effects'][1]['started_seq'] == result['effects'][0]['source_seq']
    path = env.app.service._tenant_sessions_dir() / (result['session_id'] + '.kv.json')
    assert json.loads(path.read_text(encoding='utf-8')) == {'proof': 'owned-value'}


@pytest.mark.parametrize('running_product', [KV_SCRIPT], indirect=True)
async def test_revocation_during_model_response_prevents_next_tool_and_model(running_product):
    env = running_product; projects = env.app.service.projects
    preview = await projects.execution_preview(PID, env.selection)
    async def revoke(ctx, response):
        if env.adapter.n == 1:
            result = await env.client.post(f'/api/v1/source-grants/{GID}/revoke', json=dict(expected_revision=1),
                headers={'Idempotency-Key':'withdraw-inflight'})
            assert result.status_code == 200, result.text
    env.controls.after_chat = revoke
    ident = 'ex-'+'8'*16
    try:await projects.start_execution(PID, dict(env.selection, execution_id=ident,
        confirmation_digest=preview['confirmation_digest']), 'withdraw-run')
    except PyHError as exc:assert exc.code == 'GRD-403'
    resources, _ = projects.store._grant_state(); value = resources[ident]['execution_authorization']
    queue = env.app.service._queues[value['session_id']]
    result = await queue.wait_for(value['task_id'], timeout=4)
    assert not result.ok and env.adapter.n == 1
    path = env.app.service._tenant_sessions_dir() / (value['session_id'] + '.kv.json')
    assert not path.exists()
    journal = env.app.service.surface_mgr()._logs[value['session_id']]
    assert not any(e.type == 'execution.effect' for e in journal.events_after(0))
    with pytest.raises(PyHError, match='GRD-403'):await env.app.service.require_session(value['session_id'])
