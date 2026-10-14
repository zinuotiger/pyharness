"""Read-only public operation projection shared by all trajectory entry points.

Event references remain session-qualified. No model bodies, tool arguments or
private reasoning are copied; relations must be backed by explicit identifiers.
"""
from datetime import datetime
from pyharness.application.platform_projection import project, subtask_history, summary
from pyharness.events import call_id_of


def project_trajectory(sid, events, live_tasks=(), active_children=(), active_workflows=()):
    events = list(events)
    view = project(sid, events, live_tasks, trace_limit=None)
    spans = {s['span_id']: s for s in view['trace']}
    steps = {s['step_id']: s for s in view['steps']}
    children = {s['session_id']: s for s in subtask_history(sid, events, active_children)}
    nodes, aliases, calls, approvals, workflows, artifacts = {}, {}, {}, {}, {}, {}
    active_task = None
    pause = None
    questions = {}

    def ident(seq):
        return f'{sid}~{seq}'

    def node(e, name, type_=None, node_id=None):
        key = node_id or ident(e.seq)
        if key not in nodes:
            span = spans.get(ident(e.seq), {})
            nodes[key] = dict(span, span_id=key, session_id=sid,
                run_id=span.get('run_id') or (f'{sid}~{active_task}' if active_task else None),
                parent_span_id=span.get('parent_span_id'), type=type_ or e.type,
                name=summary(name,120), status=span.get('status','recorded'),
                started_at=e.ts, finished_at=span.get('finished_at'),
                duration_ms=span.get('duration_ms'), output_summary=span.get('output_summary',''),
                input_summary='', event_seqs=[], transitions=[], lane=sid,
                error_code=span.get('error_code'), token_usage=None, cost=None,
                waits=[], artifacts=[])
        aliases[ident(e.seq)] = key
        item = nodes[key]
        if e.seq not in item['event_seqs']:
            item['event_seqs'].append(e.seq)
        return item

    def touch(item,e,status=None):
        aliases[ident(e.seq)]=item['span_id']
        if e.seq not in item['event_seqs']:
            item['event_seqs'].append(e.seq)
        if status:
            item['transitions'].append({'ts':e.ts,'status':status})

    for e in events:
        p,kind=e.payload,e.type
        if kind=='segment.start':active_task=p.get('task_id')
        if kind=='task.enqueued':
            task=p.get('task_id') or getattr(e,'task_id',None)
            item=node(e,'排队等待','task.queue',f'{sid}~queue~{task}')
            item.update(run_id=f'{sid}~{task}',status='queued')
            touch(item,e,'queued')
        elif kind=='task.started':
            task=p.get('task_id') or getattr(e,'task_id',None)
            item=nodes.get(f'{sid}~queue~{task}')
            if item:
                item.update(status='completed',finished_at=e.ts)
                touch(item,e,'completed')
        elif kind in {'llm.request','tool.call'}:
            role=(p.get('diagnostics') or {}).get('role') or p.get('role')
            if role in {'title_generation','probe','other'}:continue
            tool=p.get('name') or p.get('tool') or '工具调用'
            item=node(e,'模型请求' if kind=='llm.request' else '等待子任务 · '+tool if tool in {'subagent.join','subagent_join'} else tool)
            touch(item,e,'running')
            if kind=='tool.call' and call_id_of(e):calls[call_id_of(e)]=item
            step=steps.get(ident(e.seq))
            if step:
                item.update(status=step['status'],finished_at=step['finished_at'],error_code=step.get('error_code'))
        elif kind in {'llm.response','llm.error','llm.retry','llm.usage'}:
            request=p.get('request_seq') or (p.get('diagnostics') or {}).get('request_seq')
            item=nodes.get(ident(request)) if request else None
            if item:
                status='failed' if kind=='llm.error' else 'retrying' if kind=='llm.retry' else 'completed' if kind=='llm.response' else None
                touch(item,e,status)
                if kind=='llm.response':
                    item.update(status='completed',finished_at=e.ts)
                elif kind=='llm.error':item.update(status='failed',finished_at=e.ts,error_code=p.get('code'))
                elif kind=='llm.usage':item.update(token_usage=p.get('in_tokens',0)+p.get('out_tokens',0),cost=p.get('cost_est'))
            elif kind in {'llm.error','llm.retry'}:
                item=node(e,'模型失败' if kind=='llm.error' else '模型重试')
                item['status']='failed' if kind=='llm.error' else 'recorded'
                if kind=='llm.retry':item['output_summary']='已记录重试行为；缺少请求关联，无法确认对应结果。'
        elif kind in {'tool.result','tool.error'}:
            item=calls.get(call_id_of(e))
            if item:touch(item,e,'completed' if kind=='tool.result' and p.get('ok') else 'failed')
            else:node(e,'未配对的工具结果')
        elif kind=='approval.requested':
            item=calls.get(call_id_of(e)) or node(e,'等待审批')
            approvals[e.seq]=item
            item['waits'].append({'approval_id':f'{sid}~{e.seq}','started_at':e.ts,'finished_at':None,'status':'pending'})
            touch(item,e,'waiting_approval')
            if not item.get('finished_at'):item['status']='waiting_approval'
        elif kind in {'approval.granted','approval.denied','approval.timeout'}:
            item=approvals.get(p.get('approval_id'))
            if item:
                status='running' if kind=='approval.granted' else 'failed'
                touch(item,e,status)
                wait=next((w for w in item['waits'] if w['approval_id']==f"{sid}~{p.get('approval_id')}"),None)
                if wait:wait.update(finished_at=e.ts,status='approved' if kind=='approval.granted' else 'rejected')
                if not item.get('finished_at'):item['status']=status
        elif kind=='subagent.spawned':
            child=children[p['sub_id']]
            item=node(e,'子任务：'+summary(p.get('task') or p['sub_id'],80),'subagent')
            item.update(child_session_id=p['sub_id'],lane=p['sub_id'],parent_span_id=ident(p['parent_seq']),
                run_id=child.get('parent_run_id'),status=child['status'],finished_at=child['finished_at'],
                output_summary=child.get('summary',''),error_code=child.get('error_code'))
            if child.get('error_code')=='runtime_interrupted':
                item.update(status='unknown',output_summary='父会话未记录子任务结束，运行句柄不可得；展开查看子会话事实。')
            touch(item,e,'running')
        elif kind in {'subagent.joined','subagent.failed'}:
            item=next((n for n in nodes.values() if n.get('child_session_id')==p['sub_id']),None)
            if item:touch(item,e,'completed' if kind=='subagent.joined' else 'failed')
        elif kind=='workflow.step':
            group=(p['wf_name'],p['step_idx'])
            base_key=f"{sid}~workflow~{p['wf_name']}~{p['step_idx']}"
            linked=f"{sid}~{p['task_id']}" if p.get('task_id') else None
            existing=[n for n in nodes.values() if n.get('workflow_name')==group[0] and n.get('step_index')==group[1]]
            exact=next((n for n in existing if linked and n.get('linked_run_id')==linked),None)
            unbound=[n for n in existing if not n.get('linked_run_id') and n['status']=='running']
            if exact:key=exact['span_id']
            elif linked:
                key=unbound[0]['span_id'] if len(unbound)==1 else base_key+'~task~'+p['task_id']
            elif p.get('state')=='started':
                key=base_key if not existing else base_key+'~start~'+str(e.seq)
            else:
                # Legacy endings without task identities can close only a unique
                # currently open operation; concurrent ambiguous facts stay separate.
                candidates=[n for n in existing if n['status']=='running']
                key=candidates[0]['span_id'] if len(candidates)==1 else base_key+'~end~'+str(e.seq)
            item=node(e,f"步骤 {p['step_idx']+1}：{summary(p['action'],100)}",'workflow.step',key)
            status={'started':'running','done':'completed','failed':'failed'}.get(p.get('state'),'recorded')
            item.update(workflow_name=p['wf_name'],step_index=p['step_idx'],status=status)
            if linked:item['linked_run_id']=linked
            if status in {'completed','failed'}:item['finished_at']=e.ts
            touch(item,e,status)
            workflows.setdefault(p['wf_name'],[])
            if key not in workflows[p['wf_name']]:workflows[p['wf_name']].append(key)
        elif kind=='platform.artifact':
            record=p.get('record',{})
            aid=record.get('artifact_id')
            if aid:
                if p.get('action')=='deleted':artifacts.pop(aid,None)
                else:artifacts[aid]={k:summary(record[k],180) if k in {'filename','media_type'} else record[k]
                    for k in ('artifact_id','run_id','filename','media_type','size') if k in record}
        elif kind=='queue.suspended':
            pause=node(e,'队列暂停等待')
            pause.update(status='paused',output_summary=summary(p.get('reason',''),400))
            touch(pause,e,'paused')
        elif kind=='queue.resumed':
            if pause:
                pause.update(status='completed',finished_at=e.ts)
                touch(pause,e,'completed')
                pause=None
            else:node(e,'队列恢复（未记录暂停起点）')
        elif kind=='user.answer':
            item=questions.get(p.get('ask_id'))
            if item:
                item.update(status='completed',finished_at=e.ts)
                touch(item,e,'completed')
        elif kind in {'agent.message','system.error','user.question'}:
            item=node(e,{'agent.message':'已生成回复','system.error':'执行异常','queue.suspended':'队列已暂停','queue.resumed':'队列已恢复','user.question':'等待用户回答'}[kind])
            if kind=='agent.message':item.update(status='completed',output_summary=summary(p.get('content',''),800))
            elif kind=='system.error':item['status']=spans.get(ident(e.seq),{}).get('status','failed')
            elif kind in {'queue.suspended','user.question'}:item['status']='paused'
            item['output_summary']=summary(p.get('reason') or item['output_summary'],800)
            if kind=='user.question':questions[e.seq]=item
        if kind in {'task.failed','task.completed','segment.end','system.cancelled'}:
            task=p.get('task_id') or getattr(e,'task_id',None) or active_task
            for item in nodes.values():
                if item['run_id']==f'{sid}~{task}':touch(item,e)
        if kind=='segment.end':active_task=None

    # Map response/result anchors to the merged operation, never to neighboring events.
    for item in nodes.values():
        parent=item.get('parent_span_id')
        item['parent_span_id']=aliases.get(parent,parent)
        if item['parent_span_id']==item['span_id']:item['parent_span_id']=None
        if item.get('finished_at') and item.get('duration_ms') is None:
            try:
                elapsed=(datetime.fromisoformat(item['finished_at'])-datetime.fromisoformat(item['started_at'])).total_seconds()*1000
                item['duration_ms']=elapsed if elapsed>=0 else None
            except (ValueError,TypeError):pass
        if item['type']=='workflow.step':
            # Explicit workflow order is a dependency, not a claim of parallel branches.
            prior=[n for n in nodes.values() if n.get('workflow_name')==item['workflow_name']
                and n.get('step_index')==item['step_index']-1]
            if len(prior)==1:item['parent_span_id']=prior[0]['span_id']
            if item['status']=='running' and item['workflow_name'] not in active_workflows:
                linked=next((r for r in view['runs'] if r['run_id']==item.get('linked_run_id')),None)
                if not linked or linked['status'] not in {'running','queued','waiting_approval','paused','retrying'}:
                    item['status']='unknown'
                    item['output_summary']='已记录步骤开始，但没有可确认的运行句柄或结束记录。'
        if item['run_id']:
            item['artifacts']=[a for a in artifacts.values() if a.get('run_id')==item['run_id']]
        if item['status'] in {'running','waiting_approval','retrying','queued'} and item['type'] not in {'subagent','workflow.step'}:
            run=next((r for r in view['runs'] if r['run_id']==item['run_id']),None)
            if run and run['status'] in {'failed','cancelled'}:
                item.update(status=run['status'],error_code=run.get('error_code'),finished_at=run['finished_at'])
        if item.get('error_code'):item['error_code']=summary(item['error_code'],120)
    public_runs=[{k:summary(v,400) if k in {'name','error_code','error_message'} and isinstance(v,str) else v for k,v in r.items()
        if k in {'run_id','session_id','task_id','name','status','started_at','queued_at','finished_at',
                 'error_code','error_message','usage_status','token_usage','estimated_cost','retry_count'}} for r in view['runs']]
    return {'nodes':list(nodes.values()),'runs':public_runs,'workflows':workflows,'artifacts':list(artifacts.values())}


def trajectory_page(sid, events, projection, *, after_seq=0, limit=200, run_id=None, workflow=None):
    events=list(events)
    later=[e for e in events if e.seq>after_seq]
    batch=later[:limit]
    end=batch[-1].seq if batch else after_seq
    nodes=projection['nodes']
    if workflow is not None:
        workflow_nodes=[n for n in nodes if n.get('workflow_name')==workflow]
        linked={n.get('linked_run_id') for n in workflow_nodes if n.get('linked_run_id')}
        nodes=[n for n in nodes if n in workflow_nodes or n.get('run_id') in linked]
    elif run_id is not None:nodes=[n for n in nodes if n.get('run_id')==run_id]
    relevant=({n.get('run_id') for n in nodes}|{n.get('linked_run_id') for n in nodes})-{None}
    runs=[r for r in projection['runs'] if run_id==r['run_id'] or (run_id is None and (workflow is None or r['run_id'] in relevant))]
    active=next((r for r in reversed(runs) if r['status'] in {'running','waiting_approval','queued','paused','retrying'}),None)
    status=active['status'] if active else runs[-1]['status'] if runs else 'recorded'
    if workflow is None:
        if any(n['status']=='waiting_approval' for n in nodes):status='waiting_approval'
        elif any(n['status']=='running' for n in nodes):status='running'
        elif not runs and any(n['status']=='failed' for n in nodes):status='failed'
    if workflow is not None:
        states=[n['status'] for n in nodes if n.get('workflow_name')==workflow]
        status='failed' if 'failed' in states else 'unknown' if 'unknown' in states else 'running' if 'running' in states else 'completed' if states and all(s=='completed' for s in states) else 'recorded'
    selected=[n for n in nodes if any(after_seq<s<=end for s in n['event_seqs'])]
    return {'session_id':sid,'run_id':run_id or sid,'workflow':workflow,'name':workflow or (runs[0]['name'] if run_id and runs else '会话执行轨迹'),
        'status':status,'started_at':runs[0]['started_at'] if runs else events[0].ts if events else None,
        'finished_at':runs[-1]['finished_at'] if runs and not active and status in {'completed','failed','cancelled'} else None,
        'trace':selected,'trace_total':len(nodes),'trace_truncated':len(later)>len(batch),
        'trace_cursor':end,'to_seq':end,'latest_seq':events[-1].seq if events else 0,
        'steps':[],'runs':runs,'artifacts':[a for a in projection['artifacts'] if workflow is None and run_id is None or a.get('run_id') in relevant]}
