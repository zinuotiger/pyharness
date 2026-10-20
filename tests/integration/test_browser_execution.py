"""Real formal queue→Guard→MCP→Chrome→HTTP side effect.

Only inference is the original ScriptedAdapter, one scripted response per turn;
human approval actions below are explicit synthetic test decisions.
"""
import asyncio
import copy
import json
import os
import pytest
from tests.conftest import ScriptedAdapter
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_source_grants import PID
from tests.integration.test_controlled_browser import local_page, NODE, PW, CHROME, identities, assert_gone
from pyharness.application.browser_setup import browser_configuration

pytestmark=[pytest.mark.controlled_process,pytest.mark.skipif(os.name!='nt',reason='Windows browser carrier')]


@pytest.mark.parametrize('decision',['approve','deny'])
async def test_formal_queue_approval_controls_real_submission_and_original_effects(running_product,local_page,decision):
    env=running_product;origin,submissions=local_page;platform=env.app.service.platform
    config=browser_configuration(dict(node=str(NODE),playwright=str(PW),executable=str(CHROME),origins=[origin]))['connection']
    config['config']['enabled']=True;name=config['config']['name'];platform.save_connection(config)
    definition=platform.save_agent(dict(name='Governed browser proof',permission_policy='standard',
        sandbox_profile=dict(mode='host_approved'),mcp_connections=[name],
        allowed_tools=['mcp.'+name+'.'+tool for tool in ['open','observe','act','close']]))
    version=platform.publish_agent(definition['agent_id'])
    selection=copy.deepcopy(env.selection);selection['agent_ref']={k:version[k] for k in ['agent_id','version','sha256']}
    turns=[];observed=[];children=[]
    async def chat(messages,tools=None,*,ctx):
        turn=len(turns);calls=[]
        if turn==0:calls=[dict(id='browser-open',name='mcp.'+name+'.open',args=dict(url=origin))]
        elif turn==1:
            message=next(m for m in reversed(messages) if m['role']=='tool')
            content=json.loads(message['content'])
            # Original model context uses the governed result envelope.
            value=json.loads(content['summary']) if isinstance(content,dict) and 'summary' in content else content
            observed.append(value)
            row=value['targets'][0]
            calls=[dict(id='browser-action',name='mcp.'+name+'.act',
                args=dict(session=value['session'],generation=value['generation'],target=row['target'],label=row['label'],action='click'))]
        elif turn==2:
            calls=[dict(id='browser-close',name='mcp.'+name+'.close',args={})]
        turns.append(calls)
        adapter=ScriptedAdapter(ctx.config.llm.model,calls)
        return await adapter.chat(messages,tools,ctx=ctx)
    env.adapter.chat=chat
    projects=env.app.service.projects;preview=await projects.execution_preview(PID,selection)
    started=await projects.start_execution(PID,dict(selection,execution_id='ex-'+'b'*16,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'browser-real-'+decision)
    approvals=[]
    try:
        async with asyncio.timeout(40):
            while True:
                spine=env.app.service._engines.get(started['session_id'])
                if spine and spine._mcp_clients and spine._mcp_clients[0].transport._proc:
                    proc=spine._mcp_clients[0].transport._proc
                    if len(turns)>=2 and not children:children=identities(proc.pid)
                for request in (await env.app.service.pending_approvals())['pending']:
                    if request['session_id']!=started['session_id']:continue
                    choice=decision if 'browser-action' in json.dumps(request) or '.act' in json.dumps(request) else 'approve'
                    approvals.append((copy.deepcopy(request),choice))
                    await env.app.service.decide_approval(request['approval_id'],choice,sid=started['session_id'])
                queue=env.app.service._queues[started['session_id']].status()
                if queue.running!=started['task_id'] and started['task_id'] not in queue.waiting:break
                await asyncio.sleep(.02)
        result=await projects.execution(PID,started['execution_id'])
        assert len(turns)==4,(turns,result)
        assert observed and observed[0]['untrusted']
        assert len(approvals)==3 and any(choice==decision and '.act' in json.dumps(row) for row,choice in approvals)
        assert submissions==(['/submit?target=correct'] if decision=='approve' else [])
        effects=result['effects'];actual=[e for e in effects if e['call_id']=='browser-action']
        assert [e['phase'] for e in actual]==(['started','returned'] if decision=='approve' else [])
        journal=env.app.service._engines[started['session_id']].session
        assert any(e.type=='approval.'+('granted' if decision=='approve' else 'denied') for e in journal.events_after(0))
        assert children
    finally:
        # Explicitly close this session, not any other application process.
        await env.app.service.shutdown()
    assert_gone(children)
