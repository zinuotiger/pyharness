"""Next S07 slice; real delegation/queues/logs and scripted inference only."""
import asyncio,copy,json
import pytest
from pyharness.application.accounts import current_actor
from pyharness.errors import PyHError
from tests.conftest import ScriptedAdapter
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_source_grants import PID,GID


SCRIPT=[dict(id='owned-delegate',name='subagent.spawn',args=dict(task='synthetic-private-delegated-intent'))]


async def launch(env,ident):
    projects=env.app.service.projects;preview=await projects.execution_preview(PID,env.selection)
    return await projects.start_execution(PID,dict(env.selection,execution_id=ident,
        confirmation_digest=preview['confirmation_digest']),'delegate-start')


def publish_tools(env,*tools):
    platform=env.app.service.platform
    definition=copy.deepcopy(platform.agents()['agents'][0])
    definition['allowed_tools']=sorted(set(definition['allowed_tools'])|set(tools))
    platform.save_agent(definition,definition['agent_id']);version=platform.publish_agent(definition['agent_id'])
    env.selection['agent_ref']={k:version[k] for k in ('agent_id','version','sha256')}


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_delegated_log_has_private_namespace_and_own_bound_lifecycle(running_product):
    env=running_product;started=await launch(env,'ex-'+'3'*16)
    result=await wait_terminal(env.app.service.projects,started['execution_id'])
    root=env.app.service.surface_mgr()._logs[started['session_id']]
    spawned=[e for e in root.events_after(0) if e.type=='subagent.spawned']
    assert len(spawned)==1 and result['status']=='completed'
    sid=spawned[0].payload['sub_id']
    assert sid.startswith('s-pa-'),'Product delegation used a publicly readable legacy namespace'
    log=await env.app.service.require_session(sid)
    facts=[e for e in log.events_after(0) if e.type=='execution.child']
    assert [e.payload['phase'] for e in facts]==['bound','running','completed']
    assert facts[0].payload['execution_id']==started['execution_id']


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_other_subject_cannot_read_completed_child_through_legacy_session_api(running_product):
    env=running_product;started=await launch(env,'ex-'+'4'*16)
    await wait_terminal(env.app.service.projects,started['execution_id'])
    root=env.app.service.surface_mgr()._logs[started['session_id']]
    sid=next(e.payload['sub_id'] for e in root.events_after(0) if e.type=='subagent.spawned')
    token=current_actor.set(dict(tenant='default',user_id='other-team-owner'))
    try:
        with pytest.raises(PyHError):await env.app.service.require_session(sid)
        assert sid not in {r['sid'] for r in (await env.app.service.list_sessions())['sessions']}
    finally:current_actor.reset(token)


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_withdrawal_during_child_model_prevents_child_tool_and_next_model(running_product):
    env=running_product;platform=env.app.service.platform
    definition=copy.deepcopy(platform.agents()['agents'][0]);definition['allowed_tools'].append('storage.kv')
    platform.save_agent(definition,definition['agent_id']);version=platform.publish_agent(definition['agent_id'])
    env.selection['agent_ref']={k:version[k] for k in ('agent_id','version','sha256')}
    child=ScriptedAdapter(env.adapter.model,[dict(id='child-private-write',name='storage.kv',
        args=dict(op='set',key='must-not-write-after-withdrawal',value='synthetic-private'))])
    original=env.adapter.chat
    async def routed(messages,tools=None,*,ctx):
        if getattr(ctx,'subagent_depth',0)>0:
            response=await child.chat(messages,tools,ctx=ctx)
            if child.n==1:
                result=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
                    headers={'Idempotency-Key':'withdraw-child'})
                assert result.status_code==200,result.text
            return response
        return await original(messages,tools,ctx=ctx)
    env.adapter.chat=routed;ident='ex-'+'5'*16
    try:await launch(env,ident)
    except PyHError as exc:assert exc.code=='GRD-403'
    resources,_=env.app.service.projects.store._cancellation_state()
    target=resources[ident]['execution_authorization']
    outcome=await env.app.service._queues[target['session_id']].wait_for(target['task_id'],timeout=5)
    assert not outcome.ok
    assert child.n==1,'Withdrawn product child made another model request'
    assert not list(env.app.service._tenant_sessions_dir().glob('*.kv.json')),'Withdrawn product child wrote private KV'


@pytest.mark.parametrize('depth',[1,2,3])
@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_nested_delegation_keeps_real_caller_budgets_effects_and_read_only_history(running_product,depth,monkeypatch):
    env=running_product;publish_tools(env,'storage.kv');original=env.adapter.chat;children={};observed={}
    async def route(messages,tools=None,*,ctx):
        level=getattr(ctx,'subagent_depth',0)
        if not level:return await original(messages,tools,ctx=ctx)
        sid=ctx.session.sid
        if sid not in children:
            call=dict(id='nested-'+str(level),name='subagent.spawn',args=dict(task='synthetic-level-'+str(level+1))) if level<depth else dict(
                id='leaf-effect',name='storage.kv',args=dict(op='set',key='delegated-leaf',value='synthetic'))
            children[sid]=ScriptedAdapter(env.adapter.model,[call])
            observed[sid]=dict(depth=level,limits=ctx.scope.limits.model_dump(),deadline=ctx.platform_runtime.product_execution.root.deadline)
        return await children[sid].chat(messages,tools,ctx=ctx)
    env.adapter.chat=route
    started=await launch(env,'ex-'+'6'*16);result=await wait_terminal(env.app.service.projects,started['execution_id'])
    assert result['status']=='completed' and len(children)==depth
    service=env.app.service;spine=service._engines[started['session_id']];root=spine.session
    admissions=[e for e in root.events_after(0) if e.type=='execution.child']
    assert [e.payload['depth'] for e in admissions]==list(range(1,depth+1))
    assert not spine.subagent._children and not spine.platform_runtime.product_children.runtimes
    assert len({v['deadline'] for v in observed.values()})==1
    before={p:p.read_bytes() for p in service._tenant_sessions_dir().glob('*.jsonl')}
    async def forbidden(*args,**kwargs):raise AssertionError('read-only child lookup constructed or repaired a writer')
    monkeypatch.setattr(service.surface_mgr(),'_ensure_repaired',forbidden)
    previous=None
    for admission in admissions:
        p=admission.payload;sid=p['child_session_id'];log=await service.require_session(sid)
        assert [e.payload['phase'] for e in log.events_after(0) if e.type=='execution.child']==['bound','running','completed']
        assert p['invoker_session_id']==(previous.payload['child_session_id'] if previous else root.sid)
        assert p['parent_admission_seq']==(previous.seq if previous else None)
        assert p['child_limits']==observed[sid]['limits']
        assert children[sid].n==2
        effects=[e for e in log.events_after(0) if e.type=='execution.effect']
        assert [e.payload['phase'] for e in effects]==['started','returned']
        assert {e.task_id for e in effects}=={'subagent:'+sid}
        previous=admission
    assert all(path.read_bytes()==body for path,body in before.items())
    kv=service._tenant_sessions_dir()/(root.sid+'.kv.json')
    assert json.loads(kv.read_text())=={'delegated-leaf':'synthetic'}


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_root_stop_cancels_waiting_child_model_and_closes_owned_child_resources(running_product):
    from tests.unit.test_product_execution_cancellation import command
    env=running_product;entered=asyncio.Event();resume=asyncio.Event();child_sid=[];original=env.adapter.chat
    async def route(messages,tools=None,*,ctx):
        if getattr(ctx,'subagent_depth',0):
            child_sid.append(ctx.session.sid);entered.set();await resume.wait()
        return await original(messages,tools,ctx=ctx)
    env.adapter.chat=route;started=await launch(env,'ex-'+'7'*16)
    try:
        await asyncio.wait_for(entered.wait(),5)
        await env.app.service.projects.cancel_execution(PID,command(started),'stop-child-model')
        result=await wait_terminal(env.app.service.projects,started['execution_id'])
        assert result['status']=='cancelled'
        spine=env.app.service._engines[started['session_id']];proxy=spine.platform_runtime.product_children
        assert not spine.subagent._children and not proxy.runtimes and not proxy.logs
        assert not proxy.original._stores and not proxy.original._child_buses
        assert not proxy.original.approval_providers and not proxy.original.ask_providers
        log=await env.app.service.require_session(child_sid[0])
        assert [e.payload['phase'] for e in log.events_after(0) if e.type=='execution.child']==['bound','running','cancelled']
        assert not any(e.type=='execution.effect' for e in log.events_after(0))
    finally:resume.set()


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_revoked_child_can_be_stopped_without_restoring_source_read_authority(running_product,monkeypatch):
    env=running_product;service=env.app.service;entered=asyncio.Event();resume=asyncio.Event();children=[];original=env.adapter.chat
    async def route(messages,tools=None,*,ctx):
        if getattr(ctx,'subagent_depth',0):
            children.append(ctx.session.sid);entered.set();await resume.wait()
        return await original(messages,tools,ctx=ctx)
    env.adapter.chat=route;started=await launch(env,'ex-'+'8'*16)
    try:
        await asyncio.wait_for(entered.wait(),5)
        response=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),
            headers={'Idempotency-Key':'withdraw-before-child-stop'})
        assert response.status_code==200
        verify_snapshot=service.projects.store._verify_snapshot_content
        verify_workspace=service.projects.store._verify_workspace_content
        def forbidden(*args):raise AssertionError('stopping a child tried to read withdrawn source bytes')
        monkeypatch.setattr(service.projects.store,'_verify_snapshot_content',forbidden)
        monkeypatch.setattr(service.projects.store,'_verify_workspace_content',forbidden)
        token=current_actor.set(dict(tenant='default',user_id='other-team-owner'))
        try:
            with pytest.raises(PyHError):await service.cancel_subagent(started['session_id'],children[0])
        finally:current_actor.reset(token)
        assert await service.cancel_subagent(started['session_id'],children[0]) is True
        result=await service._queues[started['session_id']].wait_for(started['task_id'],timeout=5)
        assert not result.ok
        assert not service._engines[started['session_id']].subagent._children
        monkeypatch.setattr(service.projects.store,'_verify_snapshot_content',verify_snapshot)
        monkeypatch.setattr(service.projects.store,'_verify_workspace_content',verify_workspace)
        with pytest.raises(PyHError):await service.require_session(children[0])
    finally:resume.set()


@pytest.mark.parametrize('change',['materials','intent','future_decision','inflated_budget'])
@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_consistently_rebound_child_cannot_borrow_invalid_parent_history(running_product,change,monkeypatch):
    from pyharness.application.execution_child_graph import event_digest
    from pyharness.events import Envelope
    env=running_product;started=await launch(env,'ex-'+'9'*16)
    await wait_terminal(env.app.service.projects,started['execution_id'])
    service=env.app.service;directory=service._tenant_sessions_dir();root_path=directory/(started['session_id']+'.jsonl')
    root=[json.loads(line) for line in root_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    admission=next(e for e in root if e['type']=='execution.child');p=admission['payload']
    if change=='materials':p['materials_digest']='f'*64
    elif change=='intent':p['spec_digest']='e'*64
    elif change=='future_decision':p['decision_seq']=root[-1]['seq']
    else:
        p['parent_limits']['max_out_tokens']*=2
        p['child_limits']['max_out_tokens']*=2
    digest=event_digest(Envelope.model_validate(admission));child_path=directory/(p['child_session_id']+'.jsonl')
    child=[json.loads(line) for line in child_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    for event in child:
        if event['type']=='execution.child':
            old=event['payload'];event['payload']=dict(p,phase=old['phase'],reason=old['reason'],
                admission_seq=admission['seq'],admission_digest=digest)
    for path,events in ((root_path,root),(child_path,child)):
        path.write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events),encoding='utf-8')
    before={path:path.read_bytes() for path in (root_path,child_path)}
    async def forbidden(*args):raise AssertionError('invalid private history invoked repair')
    monkeypatch.setattr(service.surface_mgr(),'_ensure_repaired',forbidden)
    with pytest.raises(PyHError,match='PERS-202'):await service.require_session(p['child_session_id'])
    assert all(path.read_bytes()==body for path,body in before.items())


PARALLEL_SCRIPT=[dict(id='delegate-'+str(i),name='subagent.spawn',args=dict(task='synthetic-parallel-'+str(i))) for i in range(2)]


@pytest.mark.parametrize('running_product',[PARALLEL_SCRIPT],indirect=True)
async def test_sibling_children_keep_sequential_tool_semantics_and_distinct_invocations(running_product):
    env=running_product;original=env.adapter.chat;children={};counter=[];active=[]
    async def route(messages,tools=None,*,ctx):
        if not getattr(ctx,'subagent_depth',0):
            if not counter:counter.append(ctx.counters)
            return await original(messages,tools,ctx=ctx)
        sid=ctx.session.sid
        children.setdefault(sid,ScriptedAdapter(env.adapter.model,[]))
        active.append(len(ctx.platform_runtime.product_children.manager._children))
        return await children[sid].chat(messages,tools,ctx=ctx)
    env.adapter.chat=route;started=await launch(env,'ex-'+'a'*16)
    result=await wait_terminal(env.app.service.projects,started['execution_id'])
    assert result['status']=='completed' and len(children)==2
    assert active==[1,1],'The existing AgentLoop deliberately executes tool calls sequentially'
    root=env.app.service._engines[started['session_id']].session
    admissions=[e.payload for e in root.events_after(0) if e.type=='execution.child']
    assert len(admissions)==2 and len({p['tool_call_seq'] for p in admissions})==2
    assert {p['call_id'] for p in admissions}=={'delegate-0','delegate-1'}
    usage=counter[0].task_total()
    assert usage.in_tokens==400 and usage.out_tokens==80
    for sid in children:
        log=await env.app.service.require_session(sid)
        assert [e.payload['phase'] for e in log.events_after(0) if e.type=='execution.child']==['bound','running','completed']


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_actual_child_adapter_sends_scaled_output_limit_and_settles_parent_usage(running_product):
    from pyharness.core.llm import OpenAICompatAdapter
    from tests.unit.test_llm import FakeTransport,_raw,_usage
    env=running_product;original=env.adapter.chat;requests=[];counter=[]
    async def route(messages,tools=None,*,ctx):
        if not getattr(ctx,'subagent_depth',0):
            if not counter:counter.append(ctx.counters)
            return await original(messages,tools,ctx=ctx)
        transport=FakeTransport(result=_raw('synthetic-child-answer',usage=_usage(10,20),model=env.adapter.model))
        adapter=OpenAICompatAdapter(env.adapter.model,cfg=ctx.config,transport=transport)
        try:
            result=await adapter.chat(messages,tools,ctx=ctx)
            requests.extend(transport.requests)
            assert requests[-1]['max_tokens']==ctx.scope.limits.max_out_tokens==1000
            assert ctx.counters.task_total().out_tokens==20
            return result
        finally:await adapter.aclose()
    env.adapter.chat=route;started=await launch(env,'ex-'+'b'*16)
    result=await wait_terminal(env.app.service.projects,started['execution_id'])
    assert result['status']=='completed' and len(requests)==1
    usage=counter[0].task_total()
    assert usage.in_tokens==210 and usage.out_tokens==60 and env.adapter.n==2


@pytest.mark.parametrize('running_product',[SCRIPT],indirect=True)
async def test_corrupt_parent_journal_prevents_child_effect_after_model_returns(running_product):
    env=running_product;publish_tools(env,'storage.kv');original=env.adapter.chat
    child=ScriptedAdapter(env.adapter.model,[dict(id='after-corruption',name='storage.kv',
        args=dict(op='set',key='must-not-write',value='synthetic'))])
    async def route(messages,tools=None,*,ctx):
        if not getattr(ctx,'subagent_depth',0):return await original(messages,tools,ctx=ctx)
        response=await child.chat(messages,tools,ctx=ctx)
        if child.n==1:
            root=ctx.platform_runtime.product_children.spine.session
            with root._persistence.path.open('ab') as stream:stream.write(b'{broken-parent-tail')
        return response
    env.adapter.chat=route;ident='ex-'+'c'*16
    try:await launch(env,ident)
    except PyHError as exc:assert exc.code=='PERS-202'
    resources,_=env.app.service.projects.store._cancellation_state();value=resources[ident]['execution_authorization']
    result=await env.app.service._queues[value['session_id']].wait_for(value['task_id'],timeout=5)
    assert not result.ok and child.n==1
    assert not list(env.app.service._tenant_sessions_dir().glob('*.kv.json'))
