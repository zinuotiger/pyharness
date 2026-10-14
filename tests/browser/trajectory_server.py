"""Isolated UI acceptance server: synthetic facts, real PyHarness HTTP and logs.

Never points to user data or a provider. Test-only mutation endpoints are not
part of PyHarness production routes. Credentials remain in ignored tmp/ files.
"""
import asyncio
import json
import sys
import os
import uuid
import base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import uvicorn
from fastapi import Request, HTTPException
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings

async def main():
    root=Path('tmp/trajectory-ui-data-'+uuid.uuid4().hex[:8]).resolve();root.mkdir(parents=True,exist_ok=True)
    app=DesktopApp(cli.assemble_ctx(isolated_settings(root)))
    scenarios={}
    async def session(title):
        sid=await app.service.create_session()
        log=await app.service.require_session(sid)
        await log.append('user.message',{'content':'【模拟验收】'+title},actor='user',sync=True)
        await app.service.platform.session_metadata(sid,{'title':'【模拟验收】'+title})
        return sid,log
    async def run(log,tid,failed=False):
        await log.append('task.enqueued',{'task_id':tid,'pos':1},actor='system')
        await log.append('task.started',{'task_id':tid},actor='system')
        segment=await log.append('segment.start',{'task_id':tid},actor='system')
        req=await log.append('llm.request',{'model':'synthetic','role':'main_agent'},actor='llm')
        response=await log.append('llm.response',{'model':'synthetic','request_seq':req.seq,'finish_reason':'stop','content':'SYNTHETIC_PRIVATE_MODEL_BODY'},actor='llm')
        call=await log.append('tool.call',{'name':'fs.read_file','args':{},'raw_args':{},'call_id':tid+'-call'},actor='tool',trace={'parent_seq':response.seq})
        await asyncio.sleep(.04)
        if failed:await log.append('tool.error',{'name':'fs.read_file','call_id':tid+'-call','code':'TLB-synthetic','message':'模拟文件不存在'},actor='tool')
        else:await log.append('tool.result',{'name':'fs.read_file','call_id':tid+'-call','ok':True,'summary':'【模拟验收】已读取示例文件','truncated':False},actor='tool')
        if not failed:await log.append('agent.message',{'content':'【模拟验收】结果已生成；没有请求真实模型。'},actor='agent')
        await log.append('task.failed' if failed else 'task.completed',{'task_id':tid,'reason':'synthetic_failure' if failed else 'completed',**({'error':'synthetic_failure'} if failed else {})},actor='system')
        await log.append('segment.end',{'task_id':tid,'start_seq':segment.seq},actor='system')
        return call.seq
    for name,failed in [('normal',False),('failure',True)]:
        sid,log=await session('普通完成' if not failed else '工具失败')
        await run(log,name,failed)
        scenarios[name]={'sid':sid,'run_id':sid+'~'+name}
        if not failed:
            artifact=await app.service.platform.upload({'session_id':sid,'filename':'synthetic-result.txt',
                'data_base64':base64.b64encode('【模拟验收】有关任务产物内容'.encode()).decode()})
            await log.append('platform.artifact',{'action':'created','record':{**artifact,'run_id':sid+'~'+name}},actor='system')
    sid,log=await session('审批等待与增量更新')
    req=await log.append('llm.request',{'model':'synthetic'},actor='llm')
    response=await log.append('llm.response',{'model':'synthetic','request_seq':req.seq,'finish_reason':'stop','content':'private body'},actor='llm')
    call=await log.append('tool.call',{'name':'fs.write_file','args':{},'raw_args':{},'call_id':'approval-call'},actor='tool',trace={'parent_seq':response.seq})
    approval=await log.append('approval.requested',{'tool':'fs.write_file','args_summary':'【模拟验收】写入合成示例','ttl_ms':600000},actor='tool',trace={'call_id':'approval-call'})
    scenarios['approval']={'sid':sid,'call_seq':call.seq,'approval_seq':approval.seq}
    sid,log=await session('并行子任务')
    parent=await log.append('agent.message',{'content':'【模拟验收】两个子任务生命周期重叠'},actor='agent')
    child_ids=[]
    for label in ['A','B']:
        child_id,child_log=await session('子任务 '+label)
        await child_log.append('agent.message',{'content':'【模拟验收】子任务独立输出'},actor='agent')
        await log.append('subagent.spawned',{'sub_id':child_id,'parent_seq':parent.seq,'task':'【模拟验收】子任务 '+label},actor='agent')
        child_ids.append(child_id)
    await asyncio.sleep(.05)
    for child_id in child_ids:await log.append('subagent.joined',{'sub_id':child_id,'summary':'【模拟验收】已完成'},actor='agent')
    scenarios['parallel']={'sid':sid,'children':child_ids}
    sid,log=await session('工作流失败与关联')
    wf='wf-synthetic-browser'
    await log.append('user.message',{'content':'【模拟验收】工作流','meta':{'workflow':{'workflow_id':wf,'name':'【模拟验收】工作流','total_steps':2,'idempotency_key':'synthetic-browser','fingerprint':'synthetic'}}},actor='user')
    for i in range(2):
        await log.append('workflow.step',{'wf_name':wf,'step_idx':i,'action':'【模拟验收】步骤 '+str(i+1),'state':'started','task_id':'wf-task-'+str(i)},actor='system')
        await run(log,'wf-task-'+str(i),failed=i==1)
        await log.append('workflow.step',{'wf_name':wf,'step_idx':i,'action':'【模拟验收】步骤 '+str(i+1),'state':'failed' if i==1 else 'done'},actor='system')
    scenarios['workflow']={'sid':sid,'name':wf}
    sid,log=await session('旧工作流无任务关联')
    legacy='wf-legacy-synthetic'
    await log.append('user.message',{'content':'【模拟验收】旧工作流','meta':{'workflow':{'workflow_id':legacy,'name':'【模拟验收】旧工作流','total_steps':1}}},actor='user')
    await log.append('workflow.step',{'wf_name':legacy,'step_idx':0,'action':'旧日志未记录任务标识','state':'started'},actor='system')
    await log.append('workflow.step',{'wf_name':legacy,'step_idx':0,'action':'旧日志未记录任务标识','state':'done'},actor='system')
    scenarios['legacy']={'sid':sid,'name':legacy}
    sid,log=await session('长轨迹 520 条')
    for i in range(520):await log.append('agent.message',{'content':f'【模拟验收】长轨迹事件 {i}'},actor='agent')
    scenarios['long']={'sid':sid}
    @app.api.post('/__trace_test__/approval-result')
    async def result(request:Request):
        if request.headers.get('x-pyharness-token')!=app._api_token:raise HTTPException(401)
        item=scenarios['approval'];owned=await app.service.require_session(item['sid'])
        await owned.append('approval.granted',{'approval_id':item['approval_seq'],'by':'synthetic-browser'},actor='user')
        await owned.append('tool.result',{'name':'fs.write_file','call_id':'approval-call','ok':True,'summary':'【模拟验收】审批后结果记录','truncated':False},actor='tool')
        return {'ok':True}
    metadata=Path('tmp/trajectory-ui.json')
    metadata.write_text(json.dumps({'token':app._api_token,'port':60471,'pid':os.getpid(),'scenarios':scenarios}),encoding='utf-8')
    print('Synthetic PyHarness UI acceptance server ready on 127.0.0.1:60471',flush=True)
    server=uvicorn.Server(uvicorn.Config(app.api,host='127.0.0.1',port=60471,log_level='warning'))
    try:await server.serve()
    finally:await app.service.shutdown()

if __name__=='__main__':asyncio.run(main())
