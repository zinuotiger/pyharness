"""S08 actual product queues and private journals; scripted inference only."""
import asyncio,hashlib,json
import pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_product_execution_http import command
from tests.unit.test_source_grants import PID

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

async def launch(env):
    body=await command(env)
    await env.app.service.projects.start_execution(PID,body,'checkpoint-start')
    result=await wait_terminal(env.app.service.projects,body['execution_id'])
    log=env.app.service._engines[result['session_id']].session
    return result,log

async def test_real_checkpoint_confirms_exact_prefix_and_actual_materialized_context(running_product):
    env=running_product;result,log=await launch(env)
    facts=[e for e in log.events_after(0) if e.type=='execution.checkpoint']
    assert len(facts)==1,'actual model ran without one durable logical-round checkpoint'
    event=facts[0];p=event.payload
    assert event.actor=='system' and event.task_id==result['task_id']
    assert p['execution_id']==result['execution_id'] and p['authorization_digest']==result['digest']
    assert p['authorization_seq']==result['source_seq'] and p['prefix_seq']==event.seq-1
    assert p['prefix_digest']==digest([e.model_dump(mode='json') for e in log.events_after(0) if e.seq<event.seq])
    assert p['materialized_messages_digest']==digest(env.messages[0])
    assert p['replay_authorized'] is False and not p['effect_replay_blocked']
    assert len(result['checkpoints'])==1 and result['checkpoints'][0]['source_seq']==event.seq
    assert result['checkpoints'][0]['checkpoint_id']==p['checkpoint_id']
    assert 'materialized_messages' not in p and 'content' not in p

async def test_checkpoint_semantic_corruption_is_unknown_without_repair_or_dispatch(running_product):
    from pyharness.application.execution_effects import flush_confirmed
    env=running_product;result,log=await launch(env);await flush_confirmed(log)
    path=log._persistence.path;rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    cp=next(e for e in rows if e['type']=='execution.checkpoint')
    cp['payload']['prefix_digest']='0'*64
    raw=('\n'.join(json.dumps(e,ensure_ascii=False,separators=(',',':')) for e in rows)+'\n').encode();path.write_bytes(raw)
    observed=await env.app.service.projects.execution(PID,result['execution_id'])
    assert observed['status']=='unknown' and observed['unknown_effects']
    assert path.read_bytes()==raw and env.adapter.n==1

@pytest.mark.parametrize('running_product',[[dict(id='checkpoint-kv',name='storage.kv',args=dict(op='set',key='cp',value='actual-value'))]],indirect=True)
async def test_second_round_checkpoint_covers_actual_returned_effect_without_replay(running_product):
    env=running_product;result,log=await launch(env)
    checkpoints=[e for e in log.events_after(0) if e.type=='execution.checkpoint']
    assert len(checkpoints)==2 and env.adapter.n==2
    assert checkpoints[0].payload['effect_count']==0
    assert checkpoints[1].payload['effect_count']==1 and checkpoints[1].payload['pending_effect_count']==0
    assert checkpoints[1].payload['unknown_effect_count']==0 and not checkpoints[1].payload['effect_replay_blocked']
    assert [e.payload['phase'] for e in log.events_after(0) if e.type=='execution.effect']==['started','returned']

async def test_unconfirmed_checkpoint_prevents_actual_model(running_product,monkeypatch):
    from pyharness.core.session import SessionLog
    env=running_product;original=SessionLog.append
    async def fail(self,type_,*args,**kwargs):
        if type_=='execution.checkpoint':raise OSError('synthetic checkpoint disk unavailable')
        return await original(self,type_,*args,**kwargs)
    monkeypatch.setattr(SessionLog,'append',fail)
    result,_=await launch(env)
    assert result['status']=='failed' and env.adapter.n==0


async def test_fabricated_checkpoint_ack_without_disk_fact_is_zero_model(running_product,monkeypatch):
    from pyharness.core.session import SessionLog
    env=running_product;original=SessionLog.append
    async def fake(self,type_,payload,**kwargs):
        if type_=='execution.checkpoint':
            before=self.get(self.stats()['seq'])
            return before.model_copy(update=dict(seq=before.seq+1,type=type_,payload=payload,actor='system',task_id=kwargs['task_id']))
        return await original(self,type_,payload,**kwargs)
    monkeypatch.setattr(SessionLog,'append',fake)
    result,log=await launch(env)
    assert result['status']=='failed' and env.adapter.n==0
    assert not any(e.type=='execution.checkpoint' for e in log.events_after(0))


@pytest.mark.parametrize('change',[dict(replay_authorized=True),dict(schema_version=True),dict(prefix_seq=True),
    dict(effect_count=0,unknown_effect_count=1,effect_replay_blocked=True),dict(created_by='forged')])
async def test_checkpoint_schema_never_accepts_replay_authority_or_incoherent_counts(running_product,change):
    from pydantic import ValidationError
    from pyharness.events.payload_checkpoint import ExecutionCheckpointPayload
    result,log=await launch(running_product)
    p=next(e.payload for e in log.events_after(0) if e.type=='execution.checkpoint')
    with pytest.raises(ValidationError):ExecutionCheckpointPayload.model_validate(dict(p,**change))


@pytest.mark.parametrize('running_product',[[dict(id='checkpoint-child',name='subagent.spawn',args=dict(task='Child checkpoint proof'))]],indirect=True)
async def test_child_checkpoints_are_bound_to_their_own_journal_and_task(running_product):
    env=running_product;result,root=await launch(env)
    sid=next(e.payload['sub_id'] for e in root.events_after(0) if e.type=='subagent.spawned')
    child=await env.app.service.require_session(sid)
    child_facts=[e for e in child.events_after(0) if e.type=='execution.checkpoint']
    root_facts=[e for e in root.events_after(0) if e.type=='execution.checkpoint']
    assert len(root_facts)==2 and len(child_facts)==1
    p=child_facts[0].payload
    assert child_facts[0].task_id=='subagent:'+sid and child_facts[0].session_id==sid
    assert p['authorization_digest']==result['digest'] and p['execution_id']==result['execution_id']
    assert p['prefix_digest']==digest([e.model_dump(mode='json') for e in child.events_after(0) if e.seq<child_facts[0].seq])


@pytest.mark.parametrize('running_product',[[dict(id='checkpoint-uncertain',name='storage.kv',args=dict(op='set',key='cp',value='actually-written'))]],indirect=True)
async def test_later_unknown_effect_overrides_an_earlier_clear_checkpoint(running_product,monkeypatch):
    from pyharness.application.platform_runtime import PlatformRuntime
    from tests.unit.test_product_execution_cancellation import command as stop_command
    original=PlatformRuntime._execute_governed;entered=asyncio.Event();resume=asyncio.Event();env=running_product
    async def held(self,name,args,ctx,provider):
        value=await original(self,name,args,ctx,provider)
        if name=='storage.kv':entered.set();await resume.wait()
        return value
    monkeypatch.setattr(PlatformRuntime,'_execute_governed',held)
    body=await command(env)
    started=await env.app.service.projects.start_execution(PID,body,'checkpoint-unknown')
    try:
        await asyncio.wait_for(entered.wait(),5)
        kv=env.app.service._tenant_sessions_dir()/(started['session_id']+'.kv.json')
        assert json.loads(kv.read_text(encoding='utf-8'))==dict(cp='actually-written')
        await env.app.service.projects.cancel_execution(PID,stop_command(started),'checkpoint-stop')
        result=await wait_terminal(env.app.service.projects,body['execution_id'])
        assert result['status']=='cancelled' and result['checkpoints'][-1]['effect_replay_blocked'] is False
        assert result['current_session_effect_observation']['effect_replay_blocked'] is True
        assert result['current_session_effect_observation']['unknown_effect_count']==1 and env.adapter.n==1
        plan=await env.client.get(f'/api/v1/projects/{PID}/executions/'+started['execution_id']+'/recovery-plan')
        assert plan.status_code==200,plan.text
        assert plan.json()['unknown_effects'] and 'unknown_effects' in plan.json()['continuation_blockers'] and plan.json()['continuation_authorized'] is False
        assert env.adapter.n==1 and json.loads(kv.read_text(encoding='utf-8'))==dict(cp='actually-written')
    finally:resume.set()
