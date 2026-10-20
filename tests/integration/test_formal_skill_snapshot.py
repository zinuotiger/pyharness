"""A real published Agent injects and loads its original frozen skill view."""
import asyncio
import json
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


async def test_formal_system_injection_and_tool_load_ignore_classic_reload(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path)
    target=tmp_path/'skills'/'sample'/'SKILL.md';target.parent.mkdir(parents=True)
    target.write_text('---\nname: sample\ndescription: fixed sample\n---\nS15-FORMAL-ORIGINAL',encoding='utf-8')
    adapter=adapter_factory(cfg,[{'id':'load-original','name':'skill.load','args':{'name':'sample'}}])
    entered=asyncio.Event();release=asyncio.Event();requests=[]
    original=adapter.chat
    async def observed(messages,tools=None,*,ctx):
        requests.append(json.loads(json.dumps(messages)))
        if len(requests)==1:
            entered.set();await release.wait()
        return await original(messages,tools,ctx=ctx)
    adapter.chat=observed
    app=DesktopApp(cli.assemble_ctx(cfg));service=app.service
    try:
        service.save_model_profile({'id':'synthetic','model':cfg.llm.model,'base_url':'https://synthetic.invalid',
            'api_key':'synthetic-skill-key'})
        platform=service.platform
        definition=platform.save_agent({'name':'Fixed skill Agent','skills':['sample'],
            'allowed_tools':['skill.load'],'sandbox_profile':{'mode':'disabled'},'max_rounds':3})
        platform.publish_agent(definition['agent_id'])
        sid=await service.create_session()
        log=await service.require_session(sid)
        await log.append('session.renamed',{'new_title':'Fixed skill acceptance','by':'user'},actor='user')
        run=await platform.start_run({'session_id':sid,'agent_id':definition['agent_id'],'agent_version':1,'text':'Load selected skill.'})
        await asyncio.wait_for(entered.wait(),10)
        spine=service._engines[sid]
        before=spine.skills.snapshot('sample')
        assert before['frozen'] is True
        target.write_text('---\nname: sample\ndescription: changed\n---\nS15-FORMAL-CHANGED',encoding='utf-8')
        reload=await service.reload_skills()
        assert any(row.get('frozen') for row in reload['results'])
        assert spine.skills.snapshot('sample')==before
        release.set()
        assert (await asyncio.wait_for(service._queues[sid].wait_for(run['task_id']),10)).ok
        assert 'S15-FORMAL-ORIGINAL' in requests[0][0]['content']
        assert 'S15-FORMAL-CHANGED' not in json.dumps(requests)
        assert any(m.get('role')=='tool' and 'S15-FORMAL-ORIGINAL' in m.get('content','') for m in requests[1])
        assert len([e for e in log.events_after(0) if e.type=='skill.used'])==1
        assert service.skills_mgr().load('sample')['body']=='S15-FORMAL-CHANGED'
    finally:
        release.set();await service.shutdown()
