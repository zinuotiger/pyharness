"""Usage aggregation over authoritative events, with explicit unknown values."""
from datetime import datetime
import math


async def usage(platform, view, filters):
    allowed = {'session_id','agent_id','run_id','model','tool','from','to'}
    if set(filters) - allowed:
        from pyharness.core.sandbox import fail
        fail('invalid_usage_filter')
    boundaries = {k:datetime.fromisoformat(filters[k]) for k in ('from','to') if filters.get(k)}
    totals = dict(input_tokens=0, output_tokens=0, cache_hit=0, model_calls=0,
                  tool_calls=0, estimated_cost=None, sandbox_minutes=0, artifact_bytes=0)
    selected = {r['run_id']:r for r in view['runs'] if all(not filters.get(k) or str(r.get(k))==filters[k]
                for k in ('session_id','agent_id','run_id'))}
    latencies=[]; sandbox_starts={}; included=set()
    for row in view['sessions']:
        sid=row['sid']
        if filters.get('session_id') and filters['session_id']!=sid:
            continue
        log=await platform.service.require_session(sid)
        active=None; model=None; model_start=None
        for e in log.events_after(0):
            p=e.payload
            if e.type=='segment.start': active=p.get('task_id') or e.task_id
            task=p.get('task_id') or e.task_id or active
            rid=f'{sid}~{task}'
            if e.type=='llm.request': model=p.get('model');model_start=datetime.fromisoformat(e.ts)
            moment=datetime.fromisoformat(e.ts)
            if ('from' in boundaries and moment<boundaries['from']) or ('to' in boundaries and moment>boundaries['to']): continue
            if any(filters.get(k) for k in ('run_id','agent_id')) and rid not in selected: continue
            if filters.get('model') and filters['model']!=str(p.get('model') or model): continue
            if filters.get('tool') and p.get('name')!=filters['tool']: continue
            included.add(rid)
            if e.type=='llm.usage':
                for output,source in [('input_tokens','in_tokens'),('output_tokens','out_tokens'),('cache_hit','cache_hit')]:
                    totals[output]+=p.get(source) or 0
                totals['model_calls']+=1
                if p.get('cost_est') is not None: totals['estimated_cost']=(totals['estimated_cost'] or 0)+p['cost_est']
                if model_start: latencies.append(max(0,(moment-model_start).total_seconds()*1000));model_start=None
            elif e.type=='tool.call': totals['tool_calls']+=1
            elif e.type=='platform.sandbox':
                ident=p['record']['sandbox_id']
                if p['action']=='created': sandbox_starts[ident]=moment
                elif p['action']=='destroyed' and ident in sandbox_starts:
                    totals['sandbox_minutes']+=max(0,(moment-sandbox_starts.pop(ident)).total_seconds()/60)
    runs=[r for ident,r in selected.items() if ident in included]
    artifacts=(await platform.artifacts())['artifacts']
    totals['artifact_bytes']=sum(a['size'] for a in artifacts if (not filters or a['run_id'] in included))
    latencies.sort()
    n=len(runs)
    totals.update(tokens=totals['input_tokens']+totals['output_tokens'], run_count=n,
        success_rate=sum(r['status']=='completed' for r in runs)/n if n else None,
        error_rate=sum(r['status']=='failed' for r in runs)/n if n else None,
        retry_rate=sum(r['retry_count']>0 for r in runs)/n if n else None,
        cache_hit_rate=totals['cache_hit']/totals['input_tokens'] if totals['input_tokens'] else None,
        average_latency_ms=sum(latencies)/len(latencies) if latencies else None,
        p95_latency_ms=latencies[math.ceil(len(latencies)*.95)-1] if latencies else None,
        filters=filters)
    return totals
