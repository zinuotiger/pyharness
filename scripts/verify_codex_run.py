"""Opt-in real Codex run via PyHarness queue and explicit local approval.

This probe asks for a fixed text answer only. It never authorizes file/command
approval requests and uses isolated temporary PyHarness storage.
"""
import asyncio
import json
from pathlib import Path
import tempfile
from pyharness import cli
from pyharness.application.service import ApplicationService
from tests.support import isolated_settings


async def main():
    with tempfile.TemporaryDirectory(prefix='pyharness-codex-acceptance-') as directory:
        service = ApplicationService(cli.assemble_ctx(isolated_settings(Path(directory))))
        try:
            platform = service.platform
            agent = platform.save_agent({'name':'Codex acceptance','executor':'codex','codex_model':'gpt-6.1-sol',
                'permission_policy':'standard','allowed_tools':['codex.run'],
                'sandbox_profile':{'mode':'host_approved'},'timeout':120})
            platform.publish_agent(agent['agent_id'])
            run = await platform.start_run({'text':'Reply with exactly PYHARNESS_CODEX_OK. Do not use tools or change files.',
                'agent_id':agent['agent_id'],'agent_version':1})
            queue = service._queues.get(run['session_id'])
            if queue:
                for _ in range(240):
                    pending = await service.pending_approvals()
                    for approval in pending['pending']:
                        if approval['session_id']==run['session_id'] and approval['tool']=='codex.run':
                            await service.decide_approval(approval['approval_id'],'approve',sid=run['session_id'])
                    if queue.status().running is None and not queue.status().waiting:
                        break
                    await asyncio.sleep(0.5)
                result = await queue.wait_for(run['task_id'],timeout=5)
                print(json.dumps({'queue_ok':result.ok,'queue_code':result.code}))
            detail = await platform.run(run['run_id'])
            events = list((await service.require_session(run['session_id'])).events_after(0))
            print(json.dumps({'status':detail['status'],'error_code':detail['error_code'],
                'executor_events':[{'action':e.payload['action'],'record':e.payload['record']} for e in events if e.type=='executor.event'],
                'answers':[e.payload['content'] for e in events if e.type=='agent.message']},ensure_ascii=False))
        finally:
            await service.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
