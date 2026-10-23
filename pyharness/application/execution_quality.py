"""Delivery readiness is independent of runner termination and human acceptance."""


def response_completeness(reason):
    if reason=='stop':return 'complete'
    if reason in ('length','max_tokens','content_filter'):return 'incomplete'
    return 'unknown'


def codex_completeness(events,message):
    """Codex has public turn facts rather than an OpenAI llm.response trace.

    Require the same explicit task/turn, a confirmed terminal and exact public
    bytes. Neither a missing finish reason nor an unrelated turn is success.
    """
    task=getattr(message,'task_id',None)
    if not task:return 'unknown'
    facts=[e for e in events if e.seq<message.seq and e.type=='executor.event'
        and getattr(e,'actor',None)=='system' and getattr(e,'task_id',None)==task
        and e.payload.get('executor')=='codex']
    starts=[e for e in facts if e.payload['action']=='turn_started']
    if not starts:return 'unknown'
    start=starts[-1];turn=start.payload['record'].get('turn_id')
    terminals=[e for e in facts if e.seq>start.seq and e.payload['action']=='turn_completed'
        and e.payload['record'].get('turn_id')==turn]
    if not turn or not terminals or terminals[-1].payload['record'].get('status')!='completed':return 'unknown'
    end=terminals[-1]
    texts=[e.payload['record'].get('text') for e in facts if start.seq<e.seq<end.seq
        and e.payload['action']=='item_completed' and e.payload['record'].get('type')=='agentMessage']
    texts=[s for s in texts if isinstance(s,str) and s]
    content=message.payload['content']
    if texts and (content in texts or content=='\n\n'.join(texts)):return 'complete'
    if any(len(s)>32000 and content==s[:32000] for s in texts):return 'incomplete'
    return 'unknown'


def quality_projection(events):
    messages=[e for e in events if e.type=='agent.message']
    errors=[e for e in events if e.type=='system.error']
    from pyharness.core.budget_diagnostics import public_budget
    budget=next((public_budget(e.payload.get('ctx',{}).get('diagnostics')) for e in reversed(errors)
        if e.payload.get('code')=='LLM-305'),None)
    base=dict(output_completeness='unknown',finish_reason='unknown',calculation_status='not_configured',
        delivery_readiness='needs_review',budget_diagnostics=budget,effective_output_limit=None,
        configured_output_limit=None,calculation=None)
    invalid=[e for e in events if e.type=='platform.resource' and e.payload.get('action')=='order_review_invalid']
    if invalid:base.update(calculation_status='invalid_input',calculation=invalid[-1].payload['record'])
    if not messages:return base
    message=messages[-1];parent=(message.trace or {}).get('parent_seq')
    response=next((e for e in events if e.seq==parent and e.type=='llm.response'),None)
    if response is not None:
        reason=response.payload['finish_reason'];base.update(finish_reason=reason,output_completeness=response_completeness(reason))
        request=next((e for e in events if e.seq==response.payload.get('request_seq') and e.type=='llm.request'),None)
        if request:base.update(effective_output_limit=request.payload.get('effective_output_limit'),
            configured_output_limit=request.payload.get('configured_output_limit'))
    else:
        completeness=codex_completeness(events,message)
        if completeness!='unknown':base.update(output_completeness=completeness,
            finish_reason='executor_completed' if completeness=='complete' else 'executor_public_text_clipped')
    calculation=(message.payload.get('quality') or {}).get('calculation')
    if calculation:base.update(calculation_status=calculation['status'],calculation=calculation)
    if base['output_completeness']=='complete' and base['calculation_status'] in ('passed','not_configured'):
        base['delivery_readiness']='ready_for_independent_review'
    return base


def require_package_quality(service,execution,*,selected=None):
    from pyharness.application.product_execution_sessions import read_execution_events
    from pyharness.application.execution_child_reader import strict_child_events
    from pyharness.errors import raise_code
    value=execution['execution_authorization'];directory=service._tenant_sessions_dir()
    root=read_execution_events(directory,execution,service.tenant_id)
    sessions={value['session_id']}
    if selected:sessions.update(s['session_id'] for s in selected)
    # Existing delivery gates validate child admission and each output binding.
    for sid in sessions:
        events=root if sid==value['session_id'] else strict_child_events(directory,sid,service.tenant_id)
        selected_messages=[s['source_seq'] for s in (selected or []) if s['session_id']==sid and s['kind']=='message']
        views=[events]
        for seq in selected_messages:
            if not any(e.seq==seq and e.type=='agent.message' for e in events):raise_code('GRD-403',reason='delivery_quality_not_ready')
            views.append([e for e in events if e.type!='agent.message' or e.seq==seq])
        for view in views:
            quality=quality_projection(view)
            if quality['delivery_readiness']!='ready_for_independent_review':
                raise_code('GRD-403',reason='delivery_quality_not_ready',
                    output_completeness=quality['output_completeness'],calculation_status=quality['calculation_status'])
