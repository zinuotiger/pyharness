"""S07 immutable execution authority on the same project control journal."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from tests.unit.test_source_grants import grant_app, PID
from tests.unit.test_run_attempts import ready, submission, submit
from tests.unit.test_product_tasks import ref


@pytest.fixture
async def execution_ready(grant_app):
    from pyharness.application.execution_contracts import PreviewExecution, StartExecution
    from pyharness.application.platform_models import AgentDefinition
    app, client, source = grant_app
    task = await ready(client, source)
    attempt = (await submit(client, submission(task))).json()
    agent = AgentDefinition(name='S07 published', agent_id='a-' + '2' * 32,
        current_version=1, status='published').model_dump()
    sha = hashlib.sha256(json.dumps(agent, sort_keys=True).encode()).hexdigest()
    selection = dict(schema_version=1, attempt_ref=ref(attempt, 'attempt_id'),
        agent_ref=dict(agent_id=agent['agent_id'], version=1, sha256=sha))
    description = dict(agent_snapshot=agent, model_fingerprint='b' * 64, boundary='disabled')
    state = SimpleNamespace(app=app, client=client, source=source,
        store=app.service.projects.store, selection=selection, description=description,
        authorize=lambda: app.service.projects._authorize(write=True),
        describe=lambda: description.copy(), preview_model=PreviewExecution, start_model=StartExecution)
    yield state


async def preview(env):
    return await env.store.preview_execution(PID, env.preview_model.model_validate(env.selection),
        authorize=env.authorize, describe=env.describe)


def start_command(env, value, **changes):
    return env.start_model.model_validate(dict(env.selection, execution_id='ex-' + '3' * 16,
        confirmation_digest=value['confirmation_digest'], confirm_host_boundary=False, **changes))


async def commit(env, command, key='start-once'):
    return await env.store.authorize_execution(PID, command, command_id=key,
        authorize=env.authorize, describe=env.describe)


async def test_preview_is_read_only_and_authority_binds_exact_attempt(execution_ready):
    env = execution_ready; before = env.store.log_path.read_bytes()
    value = await preview(env)
    assert value['persisted'] is False and not value['execution_authorized']
    assert value['attempt_ref'] == env.selection['attempt_ref']
    assert len(value['confirmation_digest']) == 64
    assert env.store.log_path.read_bytes() == before
    assert not env.app.service._engines and not env.app.service._queues
    result, first = await commit(env, start_command(env, value))
    assert first and result['execution_authorized'] and result['status'] == 'authorized'
    assert result['attempt_ref'] == env.selection['attempt_ref']
    assert result['agent_ref'] == env.selection['agent_ref']
    assert 'local-operator' not in str(result) and str(env.source) not in str(result)


async def test_concurrent_keys_and_restart_observe_without_reauthorizing(execution_ready):
    from pyharness.application.workspace_store import WorkspaceStore
    env = execution_ready; command = start_command(env, await preview(env))
    values = await asyncio.gather(commit(env, command), commit(env, command))
    assert [x[1] for x in values] == [True, False]
    assert values[0][0] == values[1][0]
    before = env.store.log_path.read_bytes()
    again, first = await commit(env, command)
    assert not first and again == values[0][0]
    await env.store.close()
    env.store = WorkspaceStore(env.store.root, tenant_id='default')
    env.app.service.projects.store = env.store
    observed = await env.store.execution_submission(PID, 'start-once', authorize=env.authorize)
    assert observed['status'] == 'committed' and observed['execution'] == again
    assert env.store.log_path.read_bytes() == before
    assert not env.app.service._engines and not env.app.service._queues


async def test_unknown_commit_reconciles_disk_without_redispatch(execution_ready, monkeypatch):
    from pyharness.errors import PyHError, raise_code
    env = execution_ready; command = start_command(env, await preview(env))
    verify = env.store._verify_commit
    def lost(*args):
        verify(*args); raise_code('PERS-202', reason='synthetic_lost_ack')
    monkeypatch.setattr(env.store, '_verify_commit', lost)
    with pytest.raises(PyHError): await commit(env, command)
    before = env.store.log_path.read_bytes()
    value = await env.store.execution_submission(PID, 'start-once', authorize=env.authorize)
    assert value['status'] == 'committed' and env.store._failed
    assert env.store.log_path.read_bytes() == before
    assert not env.app.service._engines and not env.app.service._queues


@pytest.mark.parametrize('field', ['model_fingerprint', 'boundary'])
async def test_changed_execution_boundary_rejects_stale_preview(execution_ready, field):
    from pyharness.errors import PyHError
    env = execution_ready; command = start_command(env, await preview(env))
    env.description[field] = 'c' * 64 if field == 'model_fingerprint' else 'host_unrestricted'
    if field == 'boundary':env.description['agent_snapshot']['sandbox_profile']['mode'] = 'host_approved'
    before = env.store.log_path.read_bytes()
    with pytest.raises(PyHError): await commit(env, command)
    assert env.store.log_path.read_bytes() == before


async def test_an_attempt_cannot_gain_a_second_execution_under_another_key(execution_ready):
    from pyharness.errors import PyHError
    env = execution_ready; command = start_command(env, await preview(env))
    await commit(env, command)
    before = env.store.log_path.read_bytes()
    other = command.model_copy(update={'execution_id': 'ex-' + '4' * 16})
    with pytest.raises(PyHError): await commit(env, other, key='second-execution')
    assert env.store.log_path.read_bytes() == before


@pytest.mark.parametrize('changes', [dict(schema_version=True), dict(created_by='owner'),
    dict(tenant_id='other'), dict(host_path='C:/private'), dict(confirmation_digest='a'*64),
    dict(execution_authorized=True)])
async def test_preview_rejects_client_authority(execution_ready, changes):
    env = execution_ready
    with pytest.raises(ValidationError): env.preview_model.model_validate(dict(env.selection, **changes))


async def test_host_execution_requires_explicit_confirmation(execution_ready):
    from pyharness.errors import PyHError
    env = execution_ready; env.description['boundary'] = 'host_unrestricted'
    env.description['agent_snapshot']['sandbox_profile']['mode'] = 'host_approved'
    env.selection['agent_ref']['sha256'] = hashlib.sha256(json.dumps(env.description['agent_snapshot'], sort_keys=True).encode()).hexdigest()
    value = await preview(env); command = start_command(env, value)
    before = env.store.log_path.read_bytes()
    with pytest.raises(PyHError): await commit(env, command)
    assert env.store.log_path.read_bytes() == before
