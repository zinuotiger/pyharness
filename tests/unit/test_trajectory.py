"""Synthetic events; real authenticated service/persistence where stated."""
from types import SimpleNamespace
import asyncio
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.trajectory import project_trajectory, trajectory_page
from pyharness.events.payload import WorkflowStepPayload
from tests.support import isolated_settings

def event(seq,kind,payload,trace=None,task_id=None,sid='s-trajectory'):
    return SimpleNamespace(seq=seq,type=kind,payload=payload,trace=trace,task_id=task_id,
        session_id=sid,ts=f'2026-10-13T01:00:{seq:02d}+00:00')

def events_for_tool():
    return [event(1,'task.enqueued',{'task_id':'t'}),event(2,'task.started',{'task_id':'t'}),
        event(3,'segment.start',{'task_id':'t'}),event(4,'llm.request',{'model':'synthetic','messages':['PRIVATE REQUEST']}),
        event(5,'llm.response',{'request_seq':4,'content':'PRIVATE RESPONSE','reasoning':'PRIVATE THOUGHT'}),
        event(6,'tool.call',{'call_id':'c','name':'fs.read_file','args':{'api_key':'DO-NOT-COPY'}},trace={'parent_seq':5}),
        event(7,'approval.requested',{'tool':'fs.read_file'},trace={'call_id':'c'}),
        event(8,'approval.granted',{'approval_id':7}),
        event(9,'tool.error',{'call_id':'c','message':'synthetic failure'}),
        event(10,'task.failed',{'task_id':'t','error':'synthetic_failed'})]

def test_merged_operations_have_explicit_parent_wait_and_terminal_status():
    view=project_trajectory('s-trajectory',events_for_tool())
    tool=next(n for n in view['nodes'] if n['type']=='tool.call')
    model=next(n for n in view['nodes'] if n['type']=='llm.request')
    assert tool['parent_span_id']==model['span_id']
    assert tool['status']=='failed' and tool['duration_ms']==3000
    assert tool['waits'][0]['status']=='approved'
    assert tool['event_seqs']==[6,7,8,9,10]
    assert [t['status'] for t in tool['transitions']]==['running','waiting_approval','running','failed']
    assert model['duration_ms']==1000
    text=str(view)
    for secret in ['PRIVATE REQUEST','PRIVATE RESPONSE','PRIVATE THOUGHT','DO-NOT-COPY']:
        assert secret not in text

def test_delta_pages_upsert_same_operation_without_losing_late_results():
    events=events_for_tool();view=project_trajectory('s-trajectory',events)
    seen={};after=0
    for _ in events:
        page=trajectory_page('s-trajectory',events,view,after_seq=after,limit=1)
        assert page['to_seq']>after
        seen.update({n['span_id']:n for n in page['trace']})
        after=page['to_seq']
    assert len(seen)==len(view['nodes'])
    assert seen['s-trajectory~6']['status']=='failed'
    assert not trajectory_page('s-trajectory',events,view,after_seq=after)['trace']

def test_public_resource_ids_survive_display_text_redaction():
    task='9e3903b914844daebf220f73b94063be'
    run_id='s-trajectory~'+task
    artifact_id='f-'+task
    events=[event(1,'task.enqueued',{'task_id':task}),
        event(2,'segment.start',{'task_id':task}),
        event(3,'agent.message',{'content':'token=synthetic-secret'}),
        event(4,'platform.artifact',{'action':'created','record':{
            'artifact_id':artifact_id,'run_id':run_id,'filename':'token=synthetic-secret',
            'media_type':'text/plain','size':3}})]
    view=project_trajectory('s-trajectory',events)
    assert view['runs'][0]['run_id']==run_id
    assert view['runs'][0]['task_id']==task
    assert view['artifacts'][0]['artifact_id']==artifact_id
    assert view['artifacts'][0]['run_id']==run_id
    assert 'synthetic-secret' not in str(view)
    assert len(trajectory_page('s-trajectory',events,view,run_id=run_id)['artifacts'])==1

def test_legacy_workflow_has_no_guessed_run_link_and_new_records_have_exact_link():
    old=[event(1,'workflow.step',{'wf_name':'old','step_idx':0,'action':'synthetic','state':'started'}),
         event(2,'workflow.step',{'wf_name':'old','step_idx':0,'action':'synthetic','state':'done'})]
    assert 'linked_run_id' not in project_trajectory('s-trajectory',old)['nodes'][0]
    WorkflowStepPayload.model_validate(old[0].payload)
    new=old+[event(3,'workflow.step',{'wf_name':'new','step_idx':0,'action':'synthetic','state':'started','task_id':'actual-task'})]
    assert project_trajectory('s-trajectory',new)['nodes'][-1]['linked_run_id']=='s-trajectory~actual-task'
    assert len(trajectory_page('s-trajectory',new,project_trajectory('s-trajectory',new),workflow='old')['trace'])==1

def test_parallel_children_use_unique_session_lanes_and_explicit_parent():
    events=[event(1,'segment.start',{'task_id':'t'}),
        event(2,'subagent.spawned',{'sub_id':'s-child-a','parent_seq':1,'task':'synthetic A'}),
        event(3,'subagent.spawned',{'sub_id':'s-child-b','parent_seq':1,'task':'synthetic B'}),
        event(4,'subagent.joined',{'sub_id':'s-child-a','summary':'done'})]
    nodes=project_trajectory('s-trajectory',events,active_children=[{'sub_id':'s-child-b'}])['nodes']
    assert {n['lane'] for n in nodes}=={'s-child-a','s-child-b'}
    assert {n['parent_span_id'] for n in nodes}=={'s-trajectory~1'}
    assert [n['status'] for n in nodes]==['completed','running']

def test_resumed_queue_and_answered_question_are_closed_operations():
    events=[event(1,'queue.suspended',{'reason':'approval'}),event(2,'queue.resumed',{'reason':'approved'}),
        event(3,'user.question',{'question':'synthetic question'}),event(4,'user.answer',{'ask_id':3,'text':'synthetic answer'})]
    nodes=project_trajectory('s-trajectory',events)['nodes']
    assert len(nodes)==2
    assert all(n['status']=='completed' and n['duration_ms']==1000 for n in nodes)

def test_unlinked_retry_and_missing_child_handle_do_not_claim_live_execution():
    events=[event(1,'llm.retry',{'attempt':1,'delay_ms':100}),
        event(2,'subagent.spawned',{'sub_id':'s-old-child','parent_seq':1,'task':'synthetic'})]
    nodes=project_trajectory('s-trajectory',events)['nodes']
    assert nodes[0]['status']=='recorded'
    assert nodes[1]['status']=='unknown'

def test_same_named_workflow_reexecution_keeps_separate_task_operations():
    events=[event(1,'workflow.step',{'wf_name':'repeat','step_idx':0,'action':'synthetic','state':'started','task_id':'first'}),
        event(2,'workflow.step',{'wf_name':'repeat','step_idx':0,'action':'synthetic','state':'done','task_id':'first'}),
        event(3,'workflow.step',{'wf_name':'repeat','step_idx':0,'action':'synthetic','state':'started','task_id':'second'}),
        event(4,'workflow.step',{'wf_name':'repeat','step_idx':0,'action':'synthetic','state':'done','task_id':'second'})]
    nodes=project_trajectory('s-trajectory',events)['nodes']
    assert len(nodes)==2
    assert {n['linked_run_id'] for n in nodes}=={'s-trajectory~first','s-trajectory~second'}
    assert all(n['duration_ms']==1000 and not n['parent_span_id'] for n in nodes)

async def test_real_api_auth_pagination_scope_and_no_execution(tmp_path):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        sid=await app.service.create_session()
        log=await app.service.require_session(sid)
        for i in range(520):await log.append('agent.message',{'content':f'synthetic public result {i}'},actor='agent')
        url=f'/api/v1/sessions/{sid}/trajectory'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as c:
            assert (await c.get(url)).status_code==401
            c.headers['X-PyHarness-Token']=app._api_token
            first=(await c.get(url,params={'limit':500})).json()
            assert first['trace_truncated'] and len(first['trace'])==499
            second=(await c.get(url,params={'after_seq':first['to_seq'],'limit':500})).json()
            assert not second['trace_truncated'] and len(second['trace'])==21
            assert not ({n['span_id'] for n in first['trace']}&{n['span_id'] for n in second['trace']})
            assert (await c.get(url,params={'run_id':'foreign~task'})).status_code==404
            assert (await c.get(url,params={'workflow':'absent'})).status_code==404
            assert (await c.get(url,params={'limit':501})).status_code==422
        assert not app.service._queues and not app.service._engines
    finally:await app.service.shutdown()

async def test_new_workflow_persists_exact_queue_links_and_survives_restart(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path);adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        wf=await app.service.platform.workflows.start({'steps':['synthetic first','synthetic second'],'idempotency_key':'trace-bind'})
        await asyncio.wait_for(app.service.platform.workflows.wait(wf['workflow_id']),10)
        data=await app.service.platform.trajectory(wf['session_id'],workflow=wf['workflow_id'])
        nodes=[n for n in data['trace'] if n['type']=='workflow.step']
        actual={r['run_id'] for r in (await app.service.platform.runs())['runs']}
        assert {n['linked_run_id'] for n in nodes}==actual
        assert all(n['status']=='completed' for n in nodes)
    finally:await app.service.shutdown()
    restored=DesktopApp(cli.assemble_ctx(cfg))
    try:
        data=await restored.service.platform.trajectory(wf['session_id'],workflow=wf['workflow_id'])
        assert {n['linked_run_id'] for n in data['trace'] if n['type']=='workflow.step'}==actual
        assert not restored.service._engines
    finally:await restored.service.shutdown()

async def test_trajectory_rejects_cross_tenant_session_even_with_valid_login(tmp_path):
    from pyharness.application.accounts import Accounts
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    accounts=Accounts(app._storage_root())
    accounts.provision('trace-observer','synthetic-password-one','trace-team-one')
    accounts.provision('trace-outsider','synthetic-password-two','trace-team-two')
    own=app._service_registry.get('trace-team-one')
    foreign=app._service_registry.get('trace-team-two')
    sid=await own.create_session();other=await foreign.create_session()
    await (await foreign.require_session(other)).append('agent.message',{'content':'FOREIGN-SYNTHETIC-NOT-ALLOWED'},actor='agent')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as c:
            assert (await c.post('/api/account/login',json={'username':'trace-observer','password':'synthetic-password-one'})).status_code==200
            assert (await c.get(f'/api/v1/sessions/{sid}/trajectory')).status_code==200
            denied=await c.get(f'/api/v1/sessions/{other}/trajectory')
            assert denied.status_code==403 and 'FOREIGN-SYNTHETIC-NOT-ALLOWED' not in denied.text
    finally:
        for service in app._service_registry.all():await service.shutdown()
