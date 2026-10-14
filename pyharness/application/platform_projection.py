"""Pure, bounded public views of the existing SessionLog facts."""
from __future__ import annotations
import json
import re
from datetime import datetime
from pyharness.events import call_id_of
from pyharness.core.llm_diagnostics import public_diagnostics

TERMINAL = {"completed", "failed", "cancelled"}


def task_events(events, task_id):
    """Resolve legacy untagged facts only within their explicit segment fence."""
    active = None
    for event in events:
        payload = event.payload
        if event.type == 'segment.start':
            active = payload.get('task_id')
        owner = getattr(event,'task_id',None) or payload.get('task_id')
        if event.type == 'user.message':
            owner = (payload.get('meta') or {}).get('platform',{}).get('task_id') or owner
        if (owner or active) == task_id:
            yield event
        if event.type == 'segment.end':
            active = None

def error_kind(code):
    if str(code).startswith("LLM-"): return "model"
    if str(code).startswith("APR-"): return "approval"
    if str(code).startswith("TLB-"): return "tool"
    if "sandbox" in str(code): return "sandbox"
    if code == "cancelled": return "cancellation"
    return "execution"

def finalize(run, status, ts, code=None, diagnostics=None):
    if run.status in TERMINAL:
        return
    run.status, run.finished_at = status, ts
    run.error_code = code
    if code:
        run.error_kind = error_kind(code)
        run.error_diagnostics = diagnostics if run.error_kind == "model" else None
        run.error_message = diagnostics["summary"] if diagnostics else "Execution failed (" + run.error_kind + ")"

from pyharness.application.platform_models import Run, TraceSpan, RunStep
from pyharness.application.event_projection import redact
from pyharness.config import redact as redact_text


def summary(value, limit=800):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
    text = re.sub(r'(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', text)
    return redact_text(text)[:limit]


def subtask_history(session_id, events, active_children=()):
    """Read delegation facts without restarting children or trusting summaries as instructions."""
    rows = {}
    active = {row['sub_id'] for row in active_children}
    anchors = {}
    task = None
    for event in events:
        p = event.payload
        if event.type == 'segment.start':
            task = p.get('task_id')
        anchors[event.seq] = getattr(event, 'task_id', None) or task
        if event.type == 'subagent.spawned':
            child = p['sub_id']
            parent_task = anchors.get(p.get('parent_seq')) or task
            rows[child] = {
                'session_id': child, 'parent_session_id': session_id,
                'parent_run_id': f'{session_id}~{parent_task}' if parent_task else None,
                'task': summary(p.get('task', ''), 800), 'status': 'running',
                'created_at': event.ts, 'finished_at': None, 'summary': '',
                'error_code': None,
            }
        child = p.get('sub_id')
        if event.type == 'system.cancelled' and str(p.get('what', '')).startswith('subagent:'):
            child = p['what'].removeprefix('subagent:')
        if child in rows and rows[child]['status'] == 'running':
            status = {'subagent.joined':'completed', 'subagent.failed':'failed',
                      'system.cancelled':'cancelled'}.get(event.type)
            if status:
                rows[child].update(status=status, finished_at=event.ts,
                                   summary=summary(p.get('summary', '')))
        if event.type == 'segment.end':
            task = None
    for child, row in rows.items():
        if row['status'] == 'running' and child not in active:
            row.update(status='failed', error_code='runtime_interrupted')
    return list(rows.values())


def project(session_id, events, live_tasks=(), *, trace_run_id=None, trace_limit=500):
    runs, spans, steps, approvals = {}, [], {}, {}
    bindings, active, tool_spans = {}, [], {}
    default_binding = {}
    successful = set()
    events = list(events)
    requests, usage_counts, model_steps, last_request = {}, {}, {}, {}
    estimated_usage = set()
    for e in events:
        p, kind = e.payload, e.type
        if kind == 'user.message' and isinstance(p.get('meta'), dict):
            meta = p['meta'].get('platform')
            if isinstance(meta, dict) and 'definition' in meta:
                default_binding = meta
            if isinstance(meta, dict) and meta.get('task_id'):
                bindings[meta['task_id']] = meta
        task = p.get('task_id') or getattr(e, 'task_id', None)
        if kind == 'task.enqueued' and task not in runs:
            meta = bindings.get(task, default_binding)
            runs[task] = Run(run_id=f'{session_id}~{task}', session_id=session_id, task_id=task,
                queued_at=e.ts,
                executor=meta.get('definition',{}).get('executor','engine'),
                name=summary(meta.get('name', task), 120), agent_id=meta.get('agent_id'),
                agent_version=meta.get('agent_version'), retry_count=meta.get('retry_count', 0))
        if kind == 'segment.start' and task not in active:
            # One session queue runs one task at a time. After a restart, an old
            # unclosed segment must not make the next task's untagged model
            # events ambiguous. Preserve its failure, never infer its success.
            for previous in list(active):
                if previous in runs and runs[previous].status not in TERMINAL:
                    finalize(runs[previous], 'failed', e.ts, 'runtime_interrupted')
            active.clear()
            active.append(task)
        current = task if task in runs else (active[-1] if not task and len(active) == 1 and active[-1] in runs else None)
        run = runs.get(current)
        context = p.get('ctx') or {}
        diag = p.get('diagnostics') or context.get('diagnostics') or {}
        role = diag.get('role') or p.get('role') or context.get('role')
        # Legacy system.error inside an owned segment came from the main loop;
        # auto_title never emitted system.error in the old schema. No order guess.
        fatal = kind == 'system.error' and (context.get('fatal') is True or
            (role is None and str(p.get('code', '')).startswith('LLM-') and current is not None))
        if run:
            if kind == 'agent.message': successful.add(current)
            if kind == 'executor.event' and p.get('action') == 'turn_completed' and p.get('record',{}).get('status') == 'completed':
                successful.add(current)
            if kind == 'executor.event' and p.get('action') == 'usage':
                usage_counts[current] = usage_counts.get(current,0)+1
                run.token_usage += p['record'].get('input_tokens',0)+p['record'].get('output_tokens',0)
            if kind == 'llm.request':
                requests[current] = requests.get(current, 0) + 1
                last_request[current] = {'request_seq': e.seq, 'model': p.get('model')}
                if role in (None, 'main_agent'):
                    key = ('model', current, e.seq)
                    steps[key] = RunStep(step_id=f'{session_id}~{e.seq}', run_id=run.run_id,
                        name='model request', status='running', started_at=e.ts)
                    model_steps[current] = key
            elif kind == 'llm.response' and role not in ('title_generation', 'probe'):
                key = ('model', current, p['request_seq']) if p.get('request_seq') else model_steps.get(current)
                step = steps.get(key)
                if step and step.status == 'running':
                    step.status, step.finished_at = 'completed', e.ts
            if kind == 'llm.error' and role == 'main_agent':
                step = steps.get(('model', current, diag.get('request_seq')))
                if step and step.status == 'running':
                    step.status, step.finished_at, step.error_code = 'failed', e.ts, p.get('code')
            if fatal and role not in ('title_generation', 'probe', 'other') and run.status not in TERMINAL:
                d = public_diagnostics(diag, code=p.get('code'), role='main_agent', **last_request.get(current, {}))
                finalize(run, 'failed', e.ts, p.get('code'), d)
                step = steps.get(model_steps.get(current))
                if step is None:
                    key = ('model_error', current)
                    step = steps.setdefault(key, RunStep(step_id=f'{session_id}~{e.seq}',
                        run_id=run.run_id, name='main execution', status='running', started_at=e.ts))
                if step.status not in ('failed', 'cancelled'):
                    step.status, step.finished_at, step.error_code = 'failed', e.ts, p.get('code')
            if kind == 'task.started' and run.status not in TERMINAL:
                run.status, run.started_at = 'running', e.ts
            elif kind == 'task.completed':
                if current in successful:
                    finalize(run, 'completed', e.ts)
            elif kind == 'task.failed':
                code = p.get('error') or 'unknown'
                finalize(run, 'cancelled' if p.get('reason') == 'cancelled' else 'failed', e.ts, code,
                         public_diagnostics(code=code, role='main_agent'))
            elif kind == 'approval.requested' and run.status not in {'completed','failed','cancelled'}:
                run.status = 'waiting_approval'
            elif kind in ('approval.granted', 'approval.denied', 'approval.timeout') and run.status not in {'completed','failed','cancelled'}:
                run.status = 'running'
            elif kind == 'queue.suspended' and run.status not in {'waiting_approval','completed','failed','cancelled'}:
                run.status = 'paused'
            elif kind == 'queue.resumed' and run.status not in {'completed','failed','cancelled'}:
                run.status = 'running'
            elif kind == 'llm.usage':
                usage_counts[current] = usage_counts.get(current, 0) + 1
                if p.get('estimated') or p.get('input_unknown'):
                    estimated_usage.add(current)
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
            if step and step.status not in TERMINAL:
                step.status='waiting_approval'
        elif kind in ('approval.granted', 'approval.denied', 'approval.timeout'):
            if p['approval_id'] in approvals:
                approvals[p['approval_id']]['status'] = {'approval.granted':'approved', 'approval.denied':'rejected', 'approval.timeout':'expired'}[kind]
                approvals[p['approval_id']]['by'] = p.get('by', 'unknown')
                step=steps.get(approvals[p['approval_id']]['call_id'])
                if step and step.status not in TERMINAL:
                    step.status='running' if kind=='approval.granted' else 'failed'
        if kind == 'approval.reviewed' and p['approval_id'] in approvals:
            approvals[p['approval_id']]['review'] = p
        cid = call_id_of(e)
        if kind == 'tool.call' and cid and cid not in steps:
            steps[cid] = RunStep(step_id=f'{session_id}~{e.seq}',
                run_id=run.run_id if run else '', name=p.get('name') or p.get('tool') or 'tool', status='running', started_at=e.ts)
        elif kind in ('tool.result', 'tool.error') and cid in steps and steps[cid].status not in TERMINAL:
            step = steps[cid]
            step.status = 'completed' if kind == 'tool.result' and p.get('ok') else 'failed'
            step.finished_at = e.ts
        if (kind == 'system.error' or kind.startswith(('task.', 'executor.', 'tool.', 'llm.', 'approval.', 'decision.', 'platform.', 'agent.'))) and kind not in ('llm.chunk',):
            # Never expose llm.request/response bodies or private reasoning fields.
            public = {k: p[k] for k in ('name','code','reason','ok','model','in_tokens','out_tokens','cost_est') if k in p}
            if kind == 'executor.event':
                public = {'executor':p['executor'],'action':p['action'],
                    'record':redact(p['record'])}
            if kind == 'platform.artifact':
                public = {'action':p['action'], **{k:p.get('record',{}).get(k)
                    for k in ('artifact_id','filename','size','validation_status')}}
            if kind=='platform.sandbox':
                record=p.get('record',{})
                public.update({k:record[k] for k in ('sandbox_id','backend','status','network','cpus','memory_mb','pids_limit') if k in record})
                public.update({k:p[k] for k in ('action','command_summary','exit_code') if k in p})
            if kind in ('llm.error', 'system.error'):
                public = public_diagnostics(diag, code=p.get('code'), role=role or 'main_agent')
            span=TraceSpan(span_id=f'{session_id}~{e.seq}', run_id=run.run_id if run else None,
                type=kind, name=str(p.get('name') or kind), status=('warning' if role in ('title_generation','probe','other') else 'failed') if kind.endswith('error') else 'recorded',
                parent_span_id=f"{session_id}~{e.trace['parent_seq']}" if getattr(e,'trace',None) and e.trace.get('parent_seq') else None,
                started_at=e.ts, output_summary=summary(public), error_code=p.get('code'),
                error_diagnostics=public if kind in ('llm.error','system.error') else None,
                token_usage=(p.get('in_tokens',0)+p.get('out_tokens',0)) if kind=='llm.usage' else 0,cost=p.get('cost_est'))
            if kind=='platform.sandbox' and re.fullmatch(r'ph-[0-9a-f]{32}',str(p.get('record',{}).get('sandbox_id',''))):
                # Typed opaque identifier, not arbitrary command/output text.
                span.sandbox_id=p['record']['sandbox_id']
            if kind=='tool.call' and cid:
                span.status='running'
                tool_spans[cid]=span
            elif kind in ('tool.result','tool.error') and cid in tool_spans:
                parent=tool_spans[cid]
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
        run.usage_unknown_requests = max(0, requests.get(run.task_id, 0) - usage_counts.get(run.task_id, 0))
        run.usage_status = ('unknown' if run.usage_unknown_requests or not usage_counts.get(run.task_id)
                            else 'estimated' if run.task_id in estimated_usage else 'known')
        run.total_steps = len(own_steps)
        run.current_step = sum(s.status == 'completed' for s in own_steps)
        if run.status in {'running','queued','waiting_approval','paused','retrying'} and run.task_id not in live_tasks:
            finalize(run, 'failed', events[-1].ts if events else run.queued_at, 'runtime_interrupted')
        if run.status in {'cancelled','failed'}:
            for step in own_steps:
                if step.status in {'pending','running','waiting_approval'}:
                    step.status='cancelled' if run.status=='cancelled' else 'failed'
                    step.finished_at = run.finished_at
                    step.error_code = run.error_code
    selected = [s for s in spans if trace_run_id is None or s.run_id == trace_run_id]
    if trace_limit is not None:
        selected = selected[-trace_limit:]
    return {'runs': [r.model_dump() for r in runs.values()],
            'trace': [s.model_dump() for s in selected],
            'steps': [s.model_dump() for s in steps.values()], 'approvals': list(approvals.values())}
