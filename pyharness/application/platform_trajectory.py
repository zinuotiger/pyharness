"""Read-only trajectory queries composed into the platform application service."""
from __future__ import annotations

from pyharness.core.sandbox import fail


class PlatformTrajectory:
    async def trajectory(self, sid, *, after_seq=0, limit=200, run_id=None, workflow=None):
        """Return a bounded, session-authorized execution trajectory projection."""
        if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or not 1 <= limit <= 500:
            fail('invalid_trace_cursor')
        if run_id is not None and workflow is not None:
            fail('invalid_trace_scope')

        log = await self.service.require_session(sid)
        queue = self.service._queues.get(sid)
        status = queue.status() if queue else None
        live = [status.running, *status.waiting] if status else []
        spine = self.service._engines.get(sid)
        children = spine.subagent.status().active_children if spine is not None else []
        events = list(log.events_after(0))
        workflows = [name for name, task in self.workflows.tasks.items() if not task.done()]
        child_state = [(child['sub_id'], child.get('state')) for child in children]
        stamp = (log.stats()['seq'], repr(live), repr(child_state), repr(workflows))

        from pyharness.application.trajectory import project_trajectory, trajectory_page
        view = self.trajectory_cache.read(
            sid, stamp, lambda: project_trajectory(sid, events, live, children, workflows))
        if run_id is not None and not any(run['run_id'] == run_id for run in view['runs']):
            fail('run_not_found')
        if workflow is not None and workflow not in view['workflows']:
            fail('workflow_not_found')
        return trajectory_page(sid, events, view, after_seq=after_seq, limit=limit,
                               run_id=run_id, workflow=workflow)
