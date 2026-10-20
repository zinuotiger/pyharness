"""Actual private workspace/KV/context snapshots, never permission to replay."""
import base64,hashlib,json
import pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_product_execution_http import command
from tests.unit.test_product_execution_file_effects import OPTIONS,execute_writes
from tests.unit.test_source_grants import PID


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def state_blob(env,fact):
    p=fact.payload;path=env.app.service.platform.root/'execution-state'/p['state_digest']
    raw=path.read_bytes();assert len(raw)==p['state_size'] and hashlib.sha256(raw).hexdigest()==p['state_digest']
    return json.loads(raw)


@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_real_file_state_is_bound_to_actual_context_and_exact_checkpoint(running_product):
    env=running_product;result,log=await execute_writes(env)
    checkpoints=[e for e in log.events_after(0) if e.type=='execution.checkpoint']
    states=[e for e in log.events_after(0) if e.type=='execution.state_checkpoint']
    assert len(states)==len(checkpoints)==2,'logical checkpoints have no actual restorable state image'
    for i,(cp,state) in enumerate(zip(checkpoints,states)):
        p=state.payload;assert p['status']=='captured' and p['replay_authorized'] is False
        assert p['checkpoint_id']==cp.payload['checkpoint_id'] and p['checkpoint_seq']==cp.seq and p['checkpoint_digest']==digest(cp.model_dump(mode='json'))
        assert state.actor=='system' and state.task_id==result['task_id']
        bundle=state_blob(env,state)
        assert bundle['execution_id']==result['execution_id'] and bundle['authorization_digest']==result['digest']
        assert bundle['session_id']==result['session_id'] and bundle['task_id']==result['task_id']
        assert bundle['checkpoint_seq']==cp.seq and bundle['messages_digest']==digest(env.messages[i])
        assert bundle['messages']==env.messages[i] and bundle['budget']['counter_injected'] is True
        assert bundle['budget']['limits']['max_out_tokens']>0 and bundle['budget']['usage']['out_tokens']>=0
        files={f['path']:f for f in bundle['files']};raw=base64.b64decode(files['unchanged.txt']['data_b64'],validate=True)
        assert raw==(b'exact-selected-input\r\n' if i==0 else b'synthetic-output\n')
        assert hashlib.sha256(raw).hexdigest()==files['unchanged.txt']['sha256']
    assert env.adapter.n==2 and (env.source/'unchanged.txt').read_bytes()==b'exact-selected-input\r\n'


@pytest.mark.parametrize('running_product',[[dict(id='state-kv-once',name='storage.kv',args=dict(op='set',key='state-proof',value='actual-owned-kv'))]],indirect=True)
async def test_actual_kv_bytes_and_missing_state_are_distinguished(running_product):
    env=running_product;body=await command(env);p=env.app.service.projects
    await p.start_execution(PID,body,'actual-kv-state');result=await wait_terminal(p,body['execution_id'])
    log=env.app.service._engines[result['session_id']].session
    states=[e for e in log.events_after(0) if e.type=='execution.state_checkpoint']
    assert len(states)==2,'KV execution ran without durable actual state observations'
    before,after=[state_blob(env,s) for s in states]
    assert before['kv_present'] is False and before['kv_data_b64'] is None
    assert after['kv_present'] is True and json.loads(base64.b64decode(after['kv_data_b64'],validate=True))=={'state-proof':'actual-owned-kv'}
    assert before['workspace_backend']==after['workspace_backend']=='disabled' and before['files']==after['files']==[]
    assert env.adapter.n==2


async def test_failed_state_fact_confirmation_never_calls_model(running_product,monkeypatch):
    from pyharness.core.session import SessionLog
    env=running_product;original=SessionLog.append
    async def failed(self,type_,*args,**kwargs):
        if type_=='execution.state_checkpoint':raise OSError('synthetic state fact write failure')
        return await original(self,type_,*args,**kwargs)
    monkeypatch.setattr(SessionLog,'append',failed)
    body=await command(env);p=env.app.service.projects
    await p.start_execution(PID,body,'failed-state-fact');result=await wait_terminal(p,body['execution_id'])
    assert result['status']=='failed' and env.adapter.n==0


async def launch(env,key):
    body=await command(env);p=env.app.service.projects
    await p.start_execution(PID,body,key);result=await wait_terminal(p,body['execution_id'])
    return result,env.app.service._engines[result['session_id']].session


async def test_fake_state_ack_without_disk_fact_is_zero_model(running_product,monkeypatch):
    from pyharness.core.session import SessionLog
    env=running_product;original=SessionLog.append
    async def fake(self,type_,payload,**kwargs):
        if type_=='execution.state_checkpoint':
            before=self.get(self.stats()['seq']);return before.model_copy(update=dict(seq=before.seq+1,type=type_,payload=payload,actor='system',task_id=kwargs['task_id']))
        return await original(self,type_,payload,**kwargs)
    monkeypatch.setattr(SessionLog,'append',fake)
    result,log=await launch(env,'fake-state-ack')
    assert result['status']=='failed' and env.adapter.n==0 and not any(e.type=='execution.state_checkpoint' for e in log.events_after(0))


async def test_wrong_checkpoint_companion_is_unknown_in_all_original_readers_without_repair(running_product):
    from pyharness.errors import PyHError
    env=running_product;result,log=await launch(env,'wrong-state-companion')
    path=log._persistence.path;rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    next(e for e in rows if e['type']=='execution.state_checkpoint')['payload']['checkpoint_digest']='f'*64
    raw=('\n'.join(json.dumps(e,ensure_ascii=False) for e in rows)+'\n').encode();path.write_bytes(raw)
    view=await env.app.service.projects.execution(PID,result['execution_id'])
    assert view['status']=='unknown' and view['unknown_effects']
    with pytest.raises(PyHError):await env.app.service.require_session(result['session_id'])
    assert path.read_bytes()==raw and env.adapter.n==1


async def test_corrupt_private_blob_never_keeps_verified_state_or_repairs_bytes(running_product):
    env=running_product;result,log=await launch(env,'corrupt-state-blob')
    state=next(e for e in log.events_after(0) if e.type=='execution.state_checkpoint')
    path=env.app.service.platform.root/'execution-state'/state.payload['state_digest'];raw=b'{"bad":"synthetic blob replacement"}'
    journal=log._persistence.path.read_bytes();path.write_bytes(raw)
    view=await env.app.service.projects.execution(PID,result['execution_id'])
    assert view['status']=='completed' and view['state_checkpoints'][-1]['current_integrity']=='unconfirmed'
    assert view['state_checkpoints'][-1]['replay_authorized'] is False
    assert all('messages' not in row and 'kv_data_b64' not in row for row in view['state_checkpoints'])
    assert path.read_bytes()==raw and log._persistence.path.read_bytes()==journal and env.adapter.n==1


@pytest.mark.parametrize('running_product',[dict(script=[],agent=dict(sandbox_profile=dict(mode='host_approved')))],indirect=True)
async def test_snapshot_limit_is_explicit_unavailable_and_never_claims_empty_workspace(running_product,monkeypatch):
    from pyharness.application import execution_state_capture as module
    monkeypatch.setattr(module,'MAX_STATE_TOTAL_FILE_BYTES',1)
    env=running_product;body=await command(env);body['confirm_host_boundary']=True;p=env.app.service.projects
    await p.start_execution(PID,body,'snapshot-cap');result=await wait_terminal(p,body['execution_id'])
    log=env.app.service._engines[result['session_id']].session
    state=next(e.payload for e in log.events_after(0) if e.type=='execution.state_checkpoint')
    assert state['status']=='unavailable' and state['unavailable_reason']=='snapshot_limit' and state['state_digest'] is None and state['kv_present'] is None
    assert env.adapter.n==1 and result['status']=='completed'


@pytest.mark.parametrize('running_product',[dict(script=[],agent=dict(sandbox_profile=dict(mode='host_approved')))],indirect=True)
async def test_actual_empty_directory_is_not_lost_from_state(running_product,monkeypatch):
    from pyharness.application.platform_runtime import PlatformRuntime
    actual=PlatformRuntime.materialize_messages;env=running_product
    async def prepare(self,messages,ctx):
        ident=self.sandboxes.get(str(ctx.task_id))
        if ident is not None:
            root=self.platform.sandboxes.workspace(ident);(root/'empty-output').mkdir(exist_ok=True,mode=0o700)
        return await actual(self,messages,ctx)
    monkeypatch.setattr(PlatformRuntime,'materialize_messages',prepare)
    body=await command(env);body['confirm_host_boundary']=True;p=env.app.service.projects
    await p.start_execution(PID,body,'state-empty-directory');result=await wait_terminal(p,body['execution_id'])
    log=env.app.service._engines[result['session_id']].session;state=next(e for e in log.events_after(0) if e.type=='execution.state_checkpoint')
    blob=state_blob(env,state)
    assert blob.get('filesystem_capture')=='files_and_directories' and any(d['path']=='empty-output' for d in blob.get('directories',[]))
    assert not any(f['path'].startswith('empty-output/') for f in blob['files']) and env.adapter.n==1


@pytest.mark.parametrize('running_product',[dict(script=[],agent=dict(memory_scope='team'))],indirect=True)
async def test_shared_memory_scope_is_not_misreported_as_missing_session_kv(running_product):
    env=running_product;result,log=await launch(env,'shared-memory-state-boundary')
    state=next(e.payload for e in log.events_after(0) if e.type=='execution.state_checkpoint')
    assert state['status']=='unavailable' and state['unavailable_reason']=='shared_memory_not_captured'
    assert state['kv_present'] is None and state['state_digest'] is None and result['status']=='completed' and env.adapter.n==1


@pytest.mark.parametrize('change',[dict(replay_authorized=True),dict(schema_version=True),dict(checkpoint_seq=True),dict(state_digest=None),dict(extra_path='C:/not-allowed')])
async def test_state_payload_is_strict_and_never_accepts_replay_permission(running_product,change):
    from pydantic import ValidationError
    from pyharness.events.payload_state_checkpoint import ExecutionStateCheckpointPayload
    result,log=await launch(running_product,'strict-state-payload')
    payload=next(e.payload for e in log.events_after(0) if e.type=='execution.state_checkpoint')
    with pytest.raises(ValidationError):ExecutionStateCheckpointPayload.model_validate(dict(payload,**change))
