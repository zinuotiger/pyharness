"""Opt-in real file/test/resume/cancel evidence in disposable private workspaces."""
import asyncio
import base64
import ast
import json
from pathlib import Path
import tempfile

from pyharness import cli
from pyharness.application.service import ApplicationService
from tests.support import isolated_settings


async def main():
    with tempfile.TemporaryDirectory(prefix='pyharness-codex-change-') as directory:
        service = ApplicationService(cli.assemble_ctx(isolated_settings(Path(directory))))
        try:
            p = service.platform
            agent = p.save_agent({'name':'Codex change acceptance','executor':'codex','codex_model':'gpt-6.1-sol',
                'permission_policy':'standard','allowed_tools':['codex.run','fs.read_file','fs.list_dir','fs.write_file','exec.python_run'],
                'sandbox_profile':{'mode':'host_approved'},'timeout':240,
                'budget':{'input_tokens':100000,'output_tokens':6000,'tool_calls':30,'cost':1}})
            p.publish_agent(agent['agent_id'])
            sid = await service.create_session()
            inputs=[]
            for name,content in [('add.py','def add(a, b):\n    return a - b\n'),
                ('check.py','from add import add\nassert add(2, 3) == 5\nprint("PYHARNESS_TEST_PASSED")\n')]:
                record = await p.upload({'session_id':sid,'filename':name,'data_base64':base64.b64encode(content.encode()).decode()})
                inputs.append(record['artifact_id'])
            run = await p.start_run({'session_id':sid,'text':'In this disposable acceptance workspace, fix inputs/add.py so add returns a+b. Then execute python inputs/check.py and report its actual output. Do not access other files or the network.',
                'agent_id':agent['agent_id'],'agent_version':1,'artifact_ids':inputs})
            async def drive(run, cancel=False):
                q=service._queues[sid]
                for _ in range(500):
                    pending=await service.pending_approvals()
                    for item in pending['pending']:
                        if item['session_id']==sid and item['tool'] in {'codex.run','fs.write_file','exec.python_run'}:
                            # This script owns only synthetic code and explicitly requested local tests.
                            await service.decide_approval(item['approval_id'],'approve',sid=sid)
                    log=await service.require_session(sid)
                    if cancel and any(e.type=='executor.event' and e.task_id==run['task_id'] and e.payload['action']=='turn_started' for e in log.events_after(0)):
                        await p.cancel(run['run_id'])
                        cancel=False
                    if q.status().running is None and not q.status().waiting:
                        break
                    await asyncio.sleep(0.5)
                return await q.wait_for(run['task_id'],timeout=5)
            first = await drive(run)
            detail = await p.run(run['run_id'])
            if not first.ok:
                log = await service.require_session(sid)
                print(json.dumps({'failure':first.code,'executor_facts':[e.payload for e in log.events_after(0) if e.type=='executor.event']},ensure_ascii=False),flush=True)
            assert first.ok, first.code
            patch = next(a for a in detail['artifacts'] if a['filename']=='changes.patch')
            report = next(a for a in detail['artifacts'] if a['filename']=='test-report.json')
            assert patch['validation_status']=='passed' and report['validation_status']=='passed'
            record = await p.artifact(patch['artifact_id'])
            manifest = json.loads((p.root/'artifacts'/(record['storage_path']+'.manifest')).read_text(encoding='utf-8'))
            source = next(c['after'] for c in manifest if c['path']=='inputs/add.py')
            tree = ast.parse(source)
            assert any(isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add) and
                isinstance(node.left,ast.Name) and node.left.id=='a' and
                isinstance(node.right,ast.Name) and node.right.id=='b' for node in ast.walk(tree))
            _, raw = await p.artifact_content(report['artifact_id'],preview=True)
            commands = json.loads(raw)
            assert any(c['exit_code']==0 and 'PYHARNESS_TEST_PASSED' in c['output'] for c in commands)
            print(json.dumps({'phase':'change_test','ok':first.ok,'code':first.code,'status':detail['status'],
                'actual_addition_verified':True,'actual_test_output_verified':True,
                'result':detail['result_summary'],'artifacts':detail['artifacts']},ensure_ascii=False),flush=True)
            first_threads = [e.payload['record']['thread_id'] for e in (await service.require_session(sid)).events_after(0)
                if e.type=='executor.event' and e.task_id==run['task_id'] and e.payload['action']=='thread_bound']
            second = await p.start_run({'session_id':sid,'text':'Reply only RESUMED_OK. Do not use tools.'})
            resumed = await drive(second)
            assert resumed.ok, resumed.code
            log=await service.require_session(sid)
            resumed_bindings = [e.payload['record'] for e in log.events_after(0)
                if e.type=='executor.event' and e.task_id==second['task_id'] and e.payload['action']=='thread_bound']
            assert resumed_bindings[-1]['resumed'] and resumed_bindings[-1]['thread_id']==first_threads[-1]
            print(json.dumps({'phase':'resume','ok':resumed.ok,'bound':[e.payload['record'] for e in log.events_after(0)
                if e.type=='executor.event' and e.task_id==second['task_id'] and e.payload['action']=='thread_bound']},ensure_ascii=False),flush=True)
            third = await p.start_run({'session_id':sid,'text':'Explain the implementation of a minimal interpreter in detail. Do not use tools.'})
            cancelled = await drive(third,cancel=True)
            assert not cancelled.ok and cancelled.code=='cancelled'
            print(json.dumps({'phase':'cancel','ok':cancelled.ok,'code':cancelled.code,
                'status':(await p.run(third['run_id']))['status']},ensure_ascii=False),flush=True)
            runner=service._engines[sid].external_runner
            assert runner.transport is None and not runner.runtime.sandboxes
            assert runner.last_process_closed
            print(json.dumps({'phase':'resources','active_transport':runner.transport is not None,
                'active_sandboxes':len(runner.runtime.sandboxes),'process_closed':runner.last_process_closed},ensure_ascii=False),flush=True)
        finally:
            await service.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
