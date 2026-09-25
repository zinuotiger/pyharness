"""Pure, bounded public views of the existing SessionLog facts."""
from __future__ import annotations
import json
import re
from datetime import datetime
from pyharness.events import call_id_of
from pyharness.application.platform_models import Run, TraceSpan, RunStep
from pyharness.desktop.projection import redact
from pyharness.config import redact as redact_text


def summary(value, limit=800):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
    text = re.sub(r'(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', text)
    return redact_text(text)[:limit]


def project(session_id, events, live_tasks=()):
    runs, spans, steps, approvals = {}, [], {}, {}
    bindings, active, tool_spans = {}, [], {}
    default_binding = {}
    for e in events:
        p, kind = e.payload, e.type
        if kind == 'user.message' and isinstance(p.get('meta'), dict):
            meta = p['meta'].get('platform')
            if isinstance(meta, dict) and 'definition' in meta:
                default_binding = meta
            if isinstance(meta, dict) and meta.get('task_id'):
                bindings[meta['task_id']] = meta
        task = p.get('task_id') or getattr(e, 'task_id', None)
        if kind == 'task.enqueued':
            meta = bindings.get(task, default_binding)
            runs[task] = Run(run_id=f'{session_id}~{task}', session_id=session_id, task_id=task,
                queued_at=e.ts,
                name=summary(meta.get('name', task), 120), agent_id=meta.get('agent_id'),
                agent_version=meta.get('agent_version'), retry_count=meta.get('retry_count', 0))
        if kind == 'segment.start':
            active.append(task)
        current = task if task in runs else (active[-1] if len(active) == 1 and active[-1] in runs else None)
        run = runs.get(current)
        if run:
            if kind == 'task.started':
                run.status, run.started_at = 'running', e.ts
            elif kind in ('task.completed', 'task.failed'):
                run.status = ('completed' if kind == 'task.completed' else
                              'cancelled' if p.get('reason') == 'cancelled' else 'failed')
                run.finished_at = e.ts
                run.error_code = p.get('error') if kind == 'task.failed' else None
                run.error_message = summary(p.get('reason')) if kind == 'task.failed' else None
            elif kind == 'approval.requested' and run.status not in {'completed','failed','cancelled'}:
                run.status = 'waiting_approval'
            elif kind in ('approval.granted', 'approval.denied', 'approval.timeout') and run.status not in {'completed','failed','cancelled'}:
                run.status = 'running'
            elif kind == 'queue.suspended' and run.status not in {'waiting_approval','completed','failed','cancelled'}:
                run.status = 'paused'
            elif kind == 'queue.resumed' and run.status not in {'completed','failed','cancelled'}:
                run.status = 'running'
            elif kind == 'llm.usage':
                run.token_usage += p['in_tokens'] + p['out_tokens']
                if p.get('cost_est') is not None:
                    run.estimated_cost = (run.estimated_cost or 0) + p['cost_est']
            elif kind == 'llm.retry':
                run.retry_count += 1
            elif kind == 'platform.sandbox':
                run.sandbox_id = p['record']['sandbox_id']
        if kind == 'approval.requested':
            approvals[e.seq] = {'id': f'{session_id}~{e.seq}', 'session_id': session_id,
                'approval_id': e.seq, 'status': 'pending', 'created_at': e.ts,
                'run_id': run.run_id if run else None, **{k: summary(v) if isinstance(v, str) else v for k,v in p.items()}}
            approvals[e.seq]['call_id']=call_id_of(e)
            step=steps.get(call_id_of(e))
            if step:
                step.status='waiting_approval'
        elif kind in ('approval.granted', 'approval.denied', 'approval.timeout'):
            if p['approval_id'] in approvals:
                approvals[p['approval_id']]['status'] = {'approval.granted':'approved', 'approval.denied':'rejected', 'approval.timeout':'expired'}[kind]
                step=steps.get(approvals[p['approval_id']]['call_id'])
                if step:
                    step.status='running' if kind=='approval.granted' else 'failed'
        if kind == 'tool.call':
            steps[p['call_id']] = RunStep(step_id=f'{session_id}~{e.seq}',
                run_id=run.run_id if run else '', name=p['name'], status='running', started_at=e.ts)
        elif kind in ('tool.result', 'tool.error') and p['call_id'] in steps:
            step = steps[p['call_id']]
            step.status = 'completed' if kind == 'tool.result' and p.get('ok') else 'failed'
            step.finished_at = e.ts
        if kind.startswith(('tool.', 'llm.', 'approval.', 'decision.', 'platform.', 'agent.')) and kind not in ('llm.chunk',):
            # Never expose llm.request/response bodies or private reasoning fields.
            public = {k: p[k] for k in ('name','code','reason','ok','model','in_tokens','out_tokens','cost_est') if k in p}
            if kind=='platform.sandbox':
                record=p.get('record',{})
                public.update({k:record[k] for k in ('sandbox_id','backend','status','network','cpus','memory_mb','pids_limit') if k in record})
                public.update({k:p[k] for k in ('action','command_summary','exit_code') if k in p})
            span=TraceSpan(span_id=f'{session_id}~{e.seq}', run_id=run.run_id if run else None,
                type=kind, name=str(p.get('name') or kind), status='failed' if kind.endswith('error') else 'recorded',
                parent_span_id=f"{session_id}~{e.trace['parent_seq']}" if getattr(e,'trace',None) and e.trace.get('parent_seq') else None,
                started_at=e.ts, output_summary=summary(public), error_code=p.get('code'),
                token_usage=(p.get('in_tokens',0)+p.get('out_tokens',0)) if kind=='llm.usage' else 0,cost=p.get('cost_est'))
            if kind=='platform.sandbox' and re.fullmatch(r'ph-[0-9a-f]{32}',str(p.get('record',{}).get('sandbox_id',''))):
                # Typed opaque identifier, not arbitrary command/output text.
                span.sandbox_id=p['record']['sandbox_id']
            if kind=='tool.call':
                span.status='running'
                tool_spans[p['call_id']]=span
            elif kind in ('tool.result','tool.error') and p['call_id'] in tool_spans:
                parent=tool_spans[p['call_id']]
                parent.finished_at=e.ts
                parent.status='completed' if kind=='tool.result' and p.get('ok') else 'failed'
                parent.duration_ms=(datetime.fromisoformat(e.ts)-datetime.fromisoformat(parent.started_at)).total_seconds()*1000
                parent.output_summary=summary(p.get('summary') or p.get('message') or '')
                span.parent_span_id=parent.span_id
            spans.append(span)
        if kind == 'segment.end' and task in active:
            active.remove(task)
    for run in runs.values():
        own_steps = [s for s in steps.values() if s.run_id == run.run_id]
        run.total_steps = len(own_steps)
        run.current_step = sum(s.status == 'completed' for s in own_steps)
        if run.status in {'running','queued','waiting_approval','paused','retrying'} and run.task_id not in live_tasks:
            run.status = 'failed'
            run.error_code = 'runtime_interrupted'
            run.error_message = '没有活跃执行句柄；历史任务未自动重放'
        if run.status in {'cancelled','failed'}:
            for step in own_steps:
                if step.status in {'pending','running','waiting_approval'}:
                    step.status='cancelled' if run.status=='cancelled' else 'failed'
    return {'runs': [r.model_dump() for r in runs.values()],
            'trace': [s.model_dump() for s in spans[-500:]],
            'steps': [s.model_dump() for s in steps.values()], 'approvals': list(approvals.values())}
