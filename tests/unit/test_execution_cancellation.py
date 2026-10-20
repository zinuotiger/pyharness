"""Next S07 slice: reducing authority remains possible without source body access."""
import asyncio
import json
import pytest
from pyharness.errors import PyHError
from tests.unit.test_source_grants import grant_app,PID,GID
from tests.unit.test_execution_authorization import execution_ready,preview,start_command,commit


async def cancel_ready(env):
    receipt,_=await commit(env,start_command(env,await preview(env)))
    from pyharness.application.execution_contracts import CancelExecution
    ref={k:receipt[k] for k in ('execution_id','revision','digest','source_seq')}
    return receipt,CancelExecution.model_validate(dict(schema_version=1,cancellation_id='ec-'+'1'*16,execution_ref=ref))


async def cancel(env,command,key='cancel-original'):
    return await env.store.cancel_execution(PID,command,command_id=key,authorize=env.authorize)


async def test_cancel_once_and_reopen_original_key_without_queue_or_model(execution_ready):
    from pyharness.application.workspace_store import WorkspaceStore
    env=execution_ready;receipt,command=await cancel_ready(env);before=env.store.log_path.read_bytes()
    (a,first),(b,second)=await asyncio.gather(cancel(env,command),cancel(env,command))
    assert first is True and second is False and a==b
    assert a['persisted'] is True and a['execution_ref']==command.execution_ref.model_dump()
    assert a['status']=='cancellation_requested' and not a.get('execution_stopped',False)
    raw=env.store.log_path.read_bytes();assert raw.startswith(before) and raw!=before
    assert str(env.source) not in json.dumps(a) and 'local-operator' not in json.dumps(a)
    await env.store.close();env.store=WorkspaceStore(env.store.root,tenant_id='default')
    env.app.service.projects.store=env.store
    value=await env.store.execution_cancellation_submission(PID,'cancel-original',authorize=env.authorize)
    assert value['status']=='committed' and value['cancellation']==a
    assert env.store.log_path.read_bytes()==raw
    assert env.store._store is None and env.store._session is None and env.store._lease is None
    assert not env.app.service._queues and not env.app.service._engines


async def test_revoked_source_and_archived_project_do_not_remove_stop_authority(execution_ready):
    env=execution_ready;receipt,command=await cancel_ready(env)
    revoked=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
        headers={'Idempotency-Key':'revoke-before-stop'})
    assert revoked.status_code==200,revoked.text
    archived=await env.client.post('/api/v1/projects',json=dict(project_id=PID,expected_revision=1,name='archived',archived=True),
        headers={'Idempotency-Key':'archive-before-stop'})
    assert archived.status_code==200,archived.text
    value,first=await cancel(env,command)
    assert first and value['execution_ref']['digest']==receipt['digest']
    assert not env.app.service._queues and not env.app.service._engines


async def test_foreign_actor_or_inexact_reference_cannot_cancel(execution_ready):
    env=execution_ready;_,command=await cancel_ready(env);before=env.store.log_path.read_bytes()
    with pytest.raises(PyHError):
        await env.store.cancel_execution(PID,command,command_id='foreign',authorize=lambda:'other-user')
    bad=command.model_copy(update={'execution_ref':command.execution_ref.model_copy(update={'digest':'0'*64})})
    with pytest.raises(PyHError):await cancel(env,bad,'wrong-reference')
    assert env.store.log_path.read_bytes()==before


async def test_stop_does_not_need_available_private_source_content(execution_ready,monkeypatch):
    env=execution_ready;_,command=await cancel_ready(env)
    def forbidden(*args):raise AssertionError('Stopping work tried to revalidate private source bodies')
    monkeypatch.setattr(env.store,'_verify_snapshot_content',forbidden)
    monkeypatch.setattr(env.store,'_verify_workspace_content',forbidden)
    value,first=await cancel(env,command)
    assert first and value['persisted'] is True
    observed=await env.store.execution_cancellation_submission(PID,'cancel-original',authorize=env.authorize)
    assert observed['cancellation']==value


async def test_committed_cancel_blocks_next_execution_boundary(execution_ready):
    env=execution_ready;receipt,command=await cancel_ready(env)
    await cancel(env,command)
    with pytest.raises(asyncio.CancelledError):
        env.store.checked_execution(PID,receipt['execution_id'],authorize=env.authorize,
            describe=env.describe,expected_digest=receipt['digest'])


async def test_lost_cancel_receipt_is_read_only_reconcilable_without_reopening_writer(execution_ready,monkeypatch):
    env=execution_ready;_,command=await cancel_ready(env);verify=env.store._verify_commit
    def lost(store,event):
        verify(store,event);raise OSError('synthetic response lost after physical commit')
    monkeypatch.setattr(env.store,'_verify_commit',lost)
    with pytest.raises(PyHError):await cancel(env,command)
    assert env.store._failed is True
    before=env.store.log_path.read_bytes()
    value=await env.store.execution_cancellation_submission(PID,'cancel-original',authorize=env.authorize)
    assert value['status']=='committed' and value['writer_requires_reopen'] is True
    assert env.store._failed is True and env.store.log_path.read_bytes()==before


@pytest.mark.parametrize('extra',[dict(actor='other'),dict(destination='C:/outside'),dict(schema_version=True)])
async def test_cancel_command_has_no_client_authority_or_destinations(execution_ready,extra):
    from pydantic import ValidationError
    env=execution_ready;_,command=await cancel_ready(env);before=env.store.log_path.read_bytes()
    with pytest.raises(ValidationError):type(command).model_validate(dict(command.model_dump(),**extra))
    assert env.store.log_path.read_bytes()==before


async def test_bad_control_tail_is_rejected_without_repair_or_writer_reopen(execution_ready):
    from pyharness.application.workspace_store import WorkspaceStore
    env=execution_ready;_,command=await cancel_ready(env);await cancel(env,command)
    path=env.store.log_path;await env.store.close()
    with path.open('ab') as stream:stream.write(b'{broken')
    before=path.read_bytes();env.store=WorkspaceStore(env.store.root,tenant_id='default')
    env.app.service.projects.store=env.store
    with pytest.raises(PyHError):await env.store.execution_cancellation_submission(PID,'cancel-original',authorize=env.authorize)
    assert path.read_bytes()==before and env.store._store is None and env.store._session is None


async def test_shape_valid_inexact_stop_reference_cannot_be_replayed(execution_ready):
    from pyharness.application.workspace_store import WorkspaceStore,canonical,digest_of
    env=execution_ready;_,command=await cancel_ready(env);receipt,_=await cancel(env,command)
    path=env.store.log_path;row=json.loads(env.store._blob(receipt['digest']).read_bytes())
    await env.store.close()
    row['execution_ref']['source_seq']+=1;raw=canonical(row);digest=digest_of(raw)
    env.store._blob(digest).write_bytes(raw)
    events=[json.loads(line) for line in path.read_bytes().splitlines()]
    events[-1]['payload'].update(digest=digest,blob_ref=digest,
        request_digest=env.store._cancellation_request(PID,type(command).model_validate(
            {k:row[k] for k in ('schema_version','cancellation_id','execution_ref')})))
    path.write_bytes(b'\n'.join(canonical(event) for event in events)+b'\n');before=path.read_bytes()
    env.store=WorkspaceStore(env.store.root,tenant_id='default');env.app.service.projects.store=env.store
    with pytest.raises(PyHError):await env.store.execution_cancellation_submission(PID,'cancel-original',authorize=env.authorize)
    assert path.read_bytes()==before and env.store._store is None
