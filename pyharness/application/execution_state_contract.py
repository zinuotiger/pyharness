"""Fixed runtime/tools and conservative per-attempt bounds; never billing data."""
import asyncio,json,os,sys
from pathlib import Path
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.application.execution_state_capture import StateUnavailable
from pyharness.core.sandbox import safe_path,read_regular
from pyharness.errors import raise_code


def runtime_contract_digest(service):
    cached=getattr(service,'_execution_runtime_contract_digest',None)
    if cached is not None:return cached
    root=Path(__file__).resolve().parents[1];files={};total=0
    for path in sorted(root.rglob('*.py')):
        if len(files)>=2048:raise StateUnavailable('snapshot_limit')
        # Installed source may legitimately share a package-cache hardlink.
        # This inventory only reads code; workspace/KV restoration stays nlink=1.
        relative=path.relative_to(root).as_posix();raw=read_regular(safe_path(root,relative),2*1048576);total+=len(raw)
        if total>64*1048576:raise StateUnavailable('snapshot_limit')
        files[relative]=digest_of(raw)
    if not files:raise StateUnavailable('snapshot_state_invalid')
    value=digest_of(canonical(dict(files=files,python=list(sys.version_info[:3]),platform=sys.platform,os_name=os.name)));service._execution_runtime_contract_digest=value
    return value


def wire_tools(messages,ctx):
    vision=any(isinstance(m.get('content'),list) and any(isinstance(p,dict) and p.get('type')=='image_url' for p in m['content']) for m in messages)
    return [] if vision else ctx.tools.schemas_for(ctx.scope)


def tool_definitions_digest(ctx):
    return digest_of(canonical([d.model_dump(mode='json',by_alias=True) for d in ctx.tools._r.iter_definitions()]))


def continuation_contract(boundary,ctx,messages):
    from pyharness.core.llm_usage import _price_table
    cfg=ctx.config;tools=wire_tools(messages,ctx);runtime=ctx.platform_runtime;task=str(ctx.task_id)
    models=list(dict.fromkeys([cfg.llm.model,*(cfg.llm.fallback_models or [])]));prices=_price_table(cfg.llm.usage.unit_price)
    known=all(model in prices for model in models)
    used=ctx.scope._usage().out_tokens
    remaining=min(cfg.llm.max_tokens,max(0,runtime.definition.budget.output_tokens-used),max(0,ctx.scope.limits.max_out_tokens-used))
    deadline=boundary.deadline
    if deadline is None:raise StateUnavailable('snapshot_state_invalid')
    return dict(schema_version=1,config_fingerprint=digest_of(canonical(cfg.model_dump(mode='json'))),
        runtime_digest=runtime_contract_digest(boundary.projects.service),tools_digest=digest_of(canonical(tools)),
        tool_definitions_digest=tool_definitions_digest(ctx),models=models,
        input_upper_bound=len(json.dumps([messages,tools],ensure_ascii=False).encode('utf-8')),output_upper_bound=remaining,
        pricing_known=known,input_rate=max(prices[m]['in_per_million'] for m in models)/1e6 if known else None,
        output_rate=max(prices[m]['out_per_million'] for m in models)/1e6 if known else None,
        remaining_timeout_seconds=min(float(runtime.definition.timeout),deadline-asyncio.get_running_loop().time()),
        completed_rounds=getattr(boundary,'inherited_rounds',0)+sum(e.type=='execution.checkpoint' and e.task_id==task for e in ctx.session.events_after(0)),
        tool_calls_used=runtime.calls.get(task,0))


def verify_state_contract(boundary,ctx,events):
    from pyharness.application.execution_state_reader import read_state_snapshot
    from pyharness.errors import PyHError
    fact=next((e for e in reversed(events) if e.type=='execution.state_checkpoint'),None)
    if fact is None or fact.payload['status']!='captured':return
    cp=next((e for e in events if e.seq==fact.payload['checkpoint_seq']),None)
    try:body=read_state_snapshot(ctx.platform_runtime.platform,fact,cp)
    except (OSError,ValueError,StateUnavailable,PyHError):raise_code('PERS-202',reason='execution_model_state_unconfirmed')
    contract=body.continuation_contract
    if contract is None:return  # Legacy observation retains its original execution behavior.
    if (contract.config_fingerprint!=digest_of(canonical(ctx.config.model_dump(mode='json'))) or
            contract.runtime_digest!=runtime_contract_digest(boundary.projects.service) or
            contract.tools_digest!=digest_of(canonical(wire_tools(body.messages,ctx))) or
            contract.tool_definitions_digest!=tool_definitions_digest(ctx)):
        raise_code('GRD-403',reason='execution_state_contract_changed')
