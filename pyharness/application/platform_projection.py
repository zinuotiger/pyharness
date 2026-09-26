"""Pure, bounded public views of the existing SessionLog facts."""
from __future__ import annotations
import json
import re
from datetime import datetime
from pyharness.events import call_id_of
from pyharness.core.llm_diagnostics import public_diagnostics

TERMINAL = {"completed", "failed", "cancelled"}

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
    successful = set()
    events = list(events)
    requests, usage_counts, model_steps, last_request = {}, {}, {}, {}
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
                name=summary(meta.get('name', task), 120), agent_id=meta.get('agent_id'),
                agent_version=meta.get('agent_version'), retry_count=meta.get('retry_count', 0))
        if kind == 'segment.start' and task not in active:
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
                step=steps.get(approvals[p['approval_id']]['call_id'])
                if step and step.status not in TERMINAL:
                    step.status='running' if kind=='approval.granted' else 'failed'
        if kind == 'tool.call' and p['call_id'] not in steps:
            steps[p['call_id']] = RunStep(step_id=f'{session_id}~{e.seq}',
                run_id=run.run_id if run else '', name=p['name'], status='running', started_at=e.ts)
        elif kind in ('tool.result', 'tool.error') and p['call_id'] in steps and steps[p['call_id']].status not in TERMINAL:
            step = steps[p['call_id']]
            step.status = 'completed' if kind == 'tool.result' and p.get('ok') else 'failed'
            step.finished_at = e.ts
        if (kind == 'system.error' or kind.startswith(('tool.', 'llm.', 'approval.', 'decision.', 'platform.', 'agent.'))) and kind not in ('llm.chunk',):
            # Never expose llm.request/response bodies or private reasoning fields.
            public = {k: p[k] for k in ('name','code','reason','ok','model','in_tokens','out_tokens','cost_est') if k in p}
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
        run.usage_unknown_requests = max(0, requests.get(run.task_id, 0) - usage_counts.get(run.task_id, 0))
        run.usage_status = 'unknown' if run.usage_unknown_requests or not usage_counts.get(run.task_id) else 'known'
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
    return {'runs': [r.model_dump() for r in runs.values()],
            'trace': [s.model_dump() for s in spans[-500:]],
            'steps': [s.model_dump() for s in steps.values()], 'approvals': list(approvals.values())}
