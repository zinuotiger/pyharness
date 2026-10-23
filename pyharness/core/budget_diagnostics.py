"""Numeric-only budget diagnostics; reservation estimates are never billed usage."""


def budget_detail(dimension, *, limit, used, reserved=0, requested=0, account='task', basis='tokens'):
    remaining = max(0, limit-used-reserved)
    return dict(code='LLM-305', category='budget', dimension=dimension, account=account,
        limit=limit, used=used, reserved=reserved, requested=requested,
        remaining=remaining, shortfall=max(0, requested-remaining), basis=basis,
        request_sent=False, summary='预算不足；本次模型请求尚未发送')


def public_budget(value):
    if not isinstance(value, dict):return None
    import math
    if value.get('dimension') not in ('input','output','cost'):return None
    result = {}
    for key in ('limit','used','reserved','requested','remaining','shortfall'):
        number=value.get(key)
        if type(number) not in (int,float) or not math.isfinite(number) or number<0:return None
        result[key]=number
    return dict(result, code='LLM-305',category='budget',dimension=value['dimension'],
        account='task' if value.get('account')=='task' else 'parent',
        basis=value.get('basis') if value.get('basis') in ('tokens','conservative_utf8_bytes','estimated_yuan') else 'tokens',
        request_sent=False,summary='预算不足；本次模型请求尚未发送')


def exhausted_detail(scope):
    counters=getattr(scope,'_counters',None)
    accounts=list(counters.ancestors()) if counters is not None and hasattr(counters,'ancestors') else []
    pairs=[(a._limits,a.task_total(),'task' if a is counters else 'parent') for a in accounts if a._limits is not None]
    if not pairs and hasattr(scope,'limits'):pairs=[(scope.limits,scope._usage(),'task')]
    for limits,used,owner in pairs:
        for dimension,cap,spent in [('input',limits.max_in_tokens,used.in_tokens),
                ('output',limits.max_out_tokens,used.out_tokens),('cost',limits.max_cost_yuan,used.cost_est)]:
            if spent>=cap:return budget_detail(dimension,limit=cap,used=spent,requested=1 if dimension!='cost' else 0,
                account=owner,basis='estimated_yuan' if dimension=='cost' else 'tokens')
    return None
