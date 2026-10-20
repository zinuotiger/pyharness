"""Task-local proof of a real governed product provider, not client authority."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class DelegationInvocation:
    ctx: object
    binding: object
    decision_seq: int
    started_seq: int
    execution_id: str
    authorization_digest: str


current_delegation=ContextVar('pyharness_product_delegation',default=None)


def normalized(value):
    if hasattr(value,'model_dump'):return normalized(value.model_dump())
    if isinstance(value,dict):return {k:normalized(v) for k,v in value.items()}
    if isinstance(value,(set,frozenset)):return sorted(normalized(v) for v in value)
    if isinstance(value,(tuple,list)):return [normalized(v) for v in value]
    return value


def scope_digest(scope):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(policy=normalized(scope.policy),limits=normalized(scope.limits))))


def own_lifecycle(events,sid):
    events=list(events)
    root=[e for e in events if e.type=='execution.lifecycle']
    return root or [e for e in events if e.type=='execution.child' and e.payload['phase']!='authorized'
        and e.payload['child_session_id']==sid]


def spec_value(args,parent_depth):
    from pyharness.core.subagent_models import DEFAULT_BUDGET_RATIO
    return dict(task=str(args.get('task') or ''),tools_subset=args.get('tools_subset'),
        deny_extra=list(args.get('deny_extra') or []),budget_ratio=float(args.get('budget_ratio') or DEFAULT_BUDGET_RATIO),
        depth=int(args.get('depth') or parent_depth+1),creds_allowed=list(args.get('creds_allowed') or []),
        notify=True if args.get('notify') is None else bool(args['notify']))
