import asyncio
import pytest
from pyharness import cli
from pyharness.core import llm
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings
from tests.unit.test_llm import FakeTransport, _raw, _usage
from tests.unit.test_platform_vision import upload


@pytest.mark.parametrize('change',['profile','endpoint'])
async def test_existing_runtime_cannot_receive_image_after_connection_change(tmp_path,monkeypatch,change):
    cfg=isolated_settings(tmp_path);app=DesktopApp(cli.assemble_ctx(cfg));s=app.service;p=s.platform
    transport=FakeTransport(result=_raw('text',model='synthetic',usage=_usage(5,2)))
    monkeypatch.setattr(llm,'build_client',lambda *args:transport)
    profile={'id':'a','model':'synthetic','base_url':'https://a.invalid/v1','api_key':'synthetic-key','supports_vision':True,'fallback_models':[]}
    try:
        s.save_model_profile(profile);sid=await s.create_session();log=await s.require_session(sid)
        await log.append('session.renamed',{'new_title':'test','by':'user'},actor='user')
        run=await p.start_run({'session_id':sid,'text':'plain text'})
        assert (await asyncio.wait_for(s._queues[sid].wait_for(run['task_id']),10)).ok
        assert len(transport.requests)==1
        changed={**profile,'base_url':'https://b.invalid/v1','id':'b' if change=='profile' else 'a'}
        s.save_model_profile(changed);s.activate_model_profile(changed['id'])
        item=await upload(p,sid)
        with pytest.raises(PyHError,match='vision_runtime_restart_required'):
            await p.start_run({'session_id':sid,'text':'image','use_vision':True,'artifact_ids':[item['artifact_id']]})
        assert len(transport.requests)==1
    finally:await s.shutdown()


@pytest.mark.parametrize('matched',[True,False])
async def test_child_queries_its_own_evidence_before_model(tmp_path,adapter_factory,matched):
    from types import SimpleNamespace
    from pyharness.core.subagent import SubagentSpec
    cfg=isolated_settings(tmp_path);adapter=adapter_factory(cfg);app=DesktopApp(cli.assemble_ctx(cfg));s=app.service;p=s.platform
    try:
        source=await p.add_knowledge({'name':'evidence','text':'alpha permitted evidence'})
        agent=p.save_agent({'name':'child grounded','require_evidence':True,'knowledge_sources':[source['source_id']]});p.publish_agent(agent['agent_id'])
        sid=await p.schedule_session({'agent_id':agent['agent_id'],'agent_version':1})
        _,spine=await s.public_spine_for(sid)
        child=await spine.subagent.spawn(SubagentSpec(task='alpha' if matched else 'zzmissingzz',depth=1),SimpleNamespace(scope=spine.scope,round_seq=1))
        if matched:
            await spine.subagent.join(child,timeout=10)
        else:
            with pytest.raises(PyHError,match='knowledge_no_evidence'):
                await spine.subagent.join(child,timeout=10)
        assert adapter.n==(1 if matched else 0)
        events=list((await s.require_session(sid)).events_after(0))
        assert any(e.type==('subagent.joined' if matched else 'subagent.failed') for e in events)
    finally:await s.shutdown()


async def test_vision_retry_preserves_artifacts_and_revalidates(tmp_path,monkeypatch):
    cfg=isolated_settings(tmp_path);app=DesktopApp(cli.assemble_ctx(cfg));s=app.service;p=s.platform
    try:
        sid=await s.create_session();item=await upload(p,sid);log=await s.require_session(sid)
        from pyharness.application.platform_models import AgentDefinition
        await log.append('user.message',{'content':'look','meta':{'platform':{'task_id':'failed','input_length':4,'use_vision':True,'artifact_ids':[item['artifact_id']],'definition':AgentDefinition(name='vision retry').model_dump()}}},actor='user')
        async def view(ident):return {'session_id':sid,'task_id':'failed','status':'failed','retry_count':0}
        monkeypatch.setattr(p,'run',view)
        # No vision-capable connection: retry must fail, rather than succeeding
        # as a text-only task. Capture the normal validated request too.
        original=p.start_run;seen=[]
        async def start(body):seen.append(body);return await original(body)
        monkeypatch.setattr(p,'start_run',start)
        with pytest.raises(PyHError):await p.retry('synthetic-run')
        assert seen[0]['use_vision'] is True and seen[0]['artifact_ids']==[item['artifact_id']]
        assert not s._queues
    finally:await s.shutdown()


@pytest.mark.parametrize('sources',['none','missing','unmatched'])
async def test_actual_scheduler_cannot_bypass_evidence_requirement(tmp_path,adapter_factory,sources):
    cfg=isolated_settings(tmp_path);adapter=adapter_factory(cfg);app=DesktopApp(cli.assemble_ctx(cfg));s=app.service;p=s.platform
    try:
        source=await p.add_knowledge({'name':'evidence','text':'alpha beta'})
        ids=[] if sources=='none' else ['missing'] if sources=='missing' else [source['source_id']]
        agent=p.save_agent({'name':'scheduled grounded','require_evidence':True,'knowledge_sources':ids});p.publish_agent(agent['agent_id'])
        sid=await p.schedule_session({'agent_id':agent['agent_id'],'agent_version':1})
        log=await s.require_session(sid)
        await log.append('session.renamed',{'new_title':'synthetic schedule','by':'user'},actor='user')
        await s.schedule_action(sid,'add',name='evidence_check',kind='interval',expr='3600',intent='zzmissingzz',is_risky=False)
        await s.schedule_action(sid,'run_now',name='evidence_check')
        task=next(e.payload['task_id'] for e in log.events_after(0) if e.type=='task.enqueued')
        result=await asyncio.wait_for(s._queues[sid].wait_for(task),10)
        assert not result.ok and adapter.n==0
        assert any(e.type=='task.failed' for e in log.events_after(0))
    finally:await s.shutdown()
