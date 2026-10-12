"""Explicit local acceptance: real Docker worker/client or synthetic-model UI.

Never loads user model credentials; all data stays under the supplied NEW directory.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import json
from pathlib import Path
import sys
from contextlib import asynccontextmanager
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


@asynccontextmanager
async def loopback_worker(app):
    import socket
    import uvicorn
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_level='warning'))
    serving=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started: break
            if serving.done(): await serving
            await asyncio.sleep(.05)
        assert server.started
        yield 'http://127.0.0.1:'+str(port)
    finally:
        server.should_exit=True
        await asyncio.wait_for(serving,30)
        sock.close()


async def docker_acceptance(root):
    import httpx
    from pyharness.core.sandbox import SandboxManager
    from pyharness.core.remote_sandbox import RemoteSandboxBackend
    from pyharness.application.platform_models import SandboxProfile
    from pyharness.remote_sandbox import create_app
    worker=create_app(root/'worker',{'synthetic-account':'local-test-only-'+'x'*40})
    backend=RemoteSandboxBackend(lambda ident:(address,'local-test-only-'+'x'*40))
    manager=SandboxManager(root/'client');manager.backends['remote']=backend
    outcomes={'nature':'local loopback HTTP integration with REAL Docker; not cross-machine'}
    async with loopback_worker(worker) as address:
        try:
            outcomes['health']=await backend.health('test')
            assert outcomes['health']['ready'],outcomes
            source=root/'input';source.mkdir();(source/'input.txt').write_text('synthetic input',encoding='utf-8')
            record=await manager.create('remote-real-test',SandboxProfile(mode='remote',remote_connection='test',timeout=10),source)
            result=await manager.execute(record.sandbox_id,"from pathlib import Path; assert Path('input.txt').read_text()=='synthetic input'; Path('hello.txt').write_text('这是我的第一次沙盒测试',encoding='utf-8'); Path('output.bin').write_bytes(bytes([0,255,17])); print(Path('hello.txt').read_text(encoding='utf-8'))",python=True)
            assert result['exit_code']==0 and '第一次沙盒测试' in result['output'],result
            outcomes['execute']=result
            async with manager.snapshot(record.sandbox_id) as workspace:
                assert (workspace/'hello.txt').read_text('utf-8')=='这是我的第一次沙盒测试'
                assert (workspace/'output.bin').read_bytes()==bytes([0,255,17])
            outcomes['binary_artifact_roundtrip']=True
            await manager.destroy(record.sandbox_id)
            # Cancel a real running remote container and check the worker tombstone.
            record=await manager.create('remote-cancel',SandboxProfile(mode='remote',remote_connection='test',timeout=10))
            handle=backend.handles[record.sandbox_id][1]
            running=asyncio.create_task(manager.execute(record.sandbox_id,'import time; time.sleep(30)',python=True))
            for _ in range(100):
                if worker.state.tasks[handle]['status']=='running': break
                await asyncio.sleep(.05)
            running.cancel();await asyncio.gather(running,return_exceptions=True)
            await manager.destroy(record.sandbox_id)
            assert worker.state.tasks[handle]['status']=='deleted'
            outcomes['cancel']=True
            # Real worker deadline; no code retry or host fallback.
            record=await manager.create('remote-timeout',SandboxProfile(mode='remote',remote_connection='test',timeout=1))
            try: await manager.execute(record.sandbox_id,'import time; time.sleep(10)',python=True)
            except Exception: outcomes['timeout']=True
            else: raise AssertionError('timeout was not enforced')
            await manager.destroy(record.sandbox_id)
            # Explicit local execution, same private-copy input; no Docker used.
            local=await manager.create('host-explicit',SandboxProfile(mode='host_approved',timeout=5))
            host=await manager.execute(local.sandbox_id,"print('explicit host test')",python=True)
            assert host['exit_code']==0 and 'explicit host test' in host['output']
            outcomes['host_explicit']=True
            await manager.destroy(local.sandbox_id)
            outcomes['platform_remote_workflow']=await platform_remote_acceptance(root,address)
        finally: await manager.close()
    (root/'acceptance.json').write_text(json.dumps(outcomes,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(outcomes,ensure_ascii=True,indent=2))


async def platform_remote_acceptance(root,address):
    """Real engine/queue/reviewer/remote Docker; model responses are scripted."""
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from pyharness.core import llm
    from tests.support import isolated_settings
    from tests.conftest import ScriptedAdapter
    cfg=isolated_settings(root/'platform')
    cfg.llm.model='synthetic-remote-workflow'
    adapter=ScriptedAdapter(cfg.llm.model,[
        {'id':'automatic-file','name':'fs.write_file','args':{'path':'reviewed.txt','content':'自动审核写入'}},
        {'id':'human-python','name':'exec.python_run','args':{'code':"from pathlib import Path; assert Path('inputs/input.txt').read_text()=='uploaded input'; assert Path('reviewed.txt').read_text(encoding='utf-8')=='自动审核写入'; Path('result.bin').write_bytes(bytes([0,255,17])); print('remote platform workflow passed')"}},
    ])
    previous=llm.adapters.get(cfg.llm.model)
    llm.adapters[cfg.llm.model]=adapter
    app=DesktopApp(cli.assemble_ctx(cfg));p=app.service.platform
    try:
        connection=p.chat_setup.save_remote({'name':'local integration','address':address,'token':'local-test-only-'+'x'*40})
        assistant=p.chat_setup.create_use({'idempotency_key':'remote-platform-workflow','definition':{
            'name':'本地集成测试助手','permission_policy':'standard','allowed_tools':['fs.write_file','exec.python_run'],
            'sandbox_profile':{'mode':'remote','remote_connection':connection['id'],'timeout':10},'review_mode':'automatic'}})
        sid=await app.service.create_session()
        item=await p.upload({'session_id':sid,'filename':'input.txt','data_base64':base64.b64encode(b'uploaded input').decode()})
        run=await p.start_run({'session_id':sid,'agent_id':assistant['agent_id'],'agent_version':assistant['version'],
            'artifact_ids':[item['artifact_id']],'text':'Test automatic write reviewed.txt, human approved Python and binary artifact retrieval.'})
        for _ in range(300):
            approvals=(await p.approvals())['approvals']
            pending=next((a for a in approvals if a['session_id']==sid and a['status']=='pending' and a['tool']=='exec.python_run'),None)
            if pending: break
            await asyncio.sleep(.1)
        assert pending and pending['tool']=='exec.python_run',approvals
        assert any(a['status']=='approved' and a.get('review',{}).get('decision')=='approve' for a in approvals),approvals
        await p.decide(pending['id'],'approve',{'session_id':sid,'call_id':pending['call_id'],'run_id':run['run_id']})
        result=await asyncio.wait_for(app.service._queues[sid].wait_for(run['task_id']),30)
        assert result.ok,result
        artifacts=(await p.artifacts())['artifacts']
        binary=next(a for a in artifacts if a['session_id']==sid and a['filename']=='result.bin')
        await p.request_artifact_action(binary['artifact_id'],'export')
        for _ in range(100):
            approvals=(await p.approvals())['approvals']
            pending=next((a for a in approvals if a['session_id']==sid and a['status']=='pending' and a['tool']=='workspace.export_artifact'),None)
            if pending: break
            await asyncio.sleep(.1)
        assert pending,approvals
        await p.decide(pending['id'],'approve',{'session_id':sid,'call_id':pending['call_id'],'run_id':run['run_id']})
        assert (await asyncio.wait_for(p.actions[binary['artifact_id']+':export'],10))['ok']
        assert (await p.artifact_content(binary['artifact_id']))[1]==bytes([0,255,17])
        return {'model':'scripted test double','execution':'real remote Docker over loopback HTTP',
            'input_upload':True,'automatic_review':True,'human_approval':True,'task_success':True,'binary_artifact':True,'export_human_approved':True}
    finally:
        await app.service.shutdown()
        if previous is None: llm.adapters.pop(cfg.llm.model,None)
        else: llm.adapters[cfg.llm.model]=previous


async def serve_ui(root,port):
    import uvicorn
    from pyharness import cli,web
    from pyharness.desktop.app import DesktopApp
    from pyharness.core import llm
    from tests.conftest import ScriptedAdapter
    cfg=web.settings_for(root/'data');cfg.llm.model='synthetic-chat-test'
    cfg.llm.api_key='env:PYHARNESS_SYNTHETIC_KEY'
    cfg.shell.web.token='synthetic-chat-test-token-only-20261010'
    class Adapter(ScriptedAdapter):
        async def chat(self,messages,tools=None,*,ctx):
            task=str(getattr(ctx,'task_id',''))
            seen=any(e.type=='tool.call' and e.payload.get('call_id')=='synthetic-'+task for e in ctx.session.events_after(0))
            user=next((m.get('content','') for m in reversed(messages) if m.get('role')=='user'),'')
            self.n=1 if seen else 0
            self.calls=[] if '只聊天' in str(user) else [{'id':'synthetic-'+task,'name':'exec.python_run','args':{'code':"from pathlib import Path; Path('hello.txt').write_text('沙盒执行成功',encoding='utf-8'); print(Path('hello.txt').read_text(encoding='utf-8'))"}}]
            return await super().chat(messages,tools,ctx=ctx)
    llm.adapters[cfg.llm.model]=Adapter(cfg.llm.model)
    app=DesktopApp(cli.assemble_ctx(cfg));app.publish_operator_token()
    try:
        print(f'Synthetic model UI only: http://127.0.0.1:{port}/login',flush=True)
        await uvicorn.Server(uvicorn.Config(app.api,host='127.0.0.1',port=port,access_log=False,log_level='warning')).serve()
    finally:
        for service in app._service_registry.all(): await service.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--real-docker',action='store_true')
    parser.add_argument('--serve-ui',action='store_true')
    parser.add_argument('--port',type=int,default=62040)
    parser.add_argument('--resume-test-ui',action='store_true')
    args=parser.parse_args()
    if args.resume_test_ui:
        if not args.serve_ui or not (args.output/'synthetic-test-runtime').exists(): parser.error('Only a marked synthetic UI directory can be resumed')
    else:
        args.output.mkdir(parents=True,exist_ok=False)
        if args.serve_ui: (args.output/'synthetic-test-runtime').write_text('Synthetic model only; no provider credentials',encoding='utf-8')
    asyncio.run(serve_ui(args.output,args.port) if args.serve_ui else docker_acceptance(args.output) if args.real_docker else parser.error('Select --real-docker or --serve-ui'))


if __name__=='__main__': main()
