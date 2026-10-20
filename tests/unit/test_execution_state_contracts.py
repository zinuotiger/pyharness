"""Continuation needs fixed contracts and non-reset usage, not only file bytes."""
import json
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_execution_liveness import held
from tests.unit.test_execution_state_snapshots import state_blob,digest


async def test_state_binds_actual_tool_schema_configuration_and_possible_request_cost(running_product):
    env=running_product;started,release=await held(env)
    try:
        ctx=env.controls.ctx;fact=next(e for e in ctx.session.events_after(0) if e.type=='execution.state_checkpoint')
        body=state_blob(env,fact);contract=body.get('continuation_contract')
        assert contract is not None,'captured bytes lack a fixed continuation/runtime/request contract'
        assert contract['config_fingerprint']==digest(ctx.config.model_dump(mode='json'))
        assert contract['config_fingerprint']==started['model_fingerprint']
        assert contract['tools_digest']==digest(ctx.tools.schemas_for(ctx.scope))
        assert contract['input_upper_bound']==len(json.dumps([body['messages'],ctx.tools.schemas_for(ctx.scope)],ensure_ascii=False).encode('utf-8'))
        assert contract['output_upper_bound']==min(ctx.config.llm.max_tokens,ctx.platform_runtime.definition.budget.output_tokens)
        assert contract['pricing_known'] is True and contract['input_rate']>=0 and contract['output_rate']>0
        assert 0<contract['remaining_timeout_seconds']<=ctx.platform_runtime.definition.timeout
        assert contract['completed_rounds']==0 and contract['tool_calls_used']==0
        assert len(contract['runtime_digest'])==len(contract['tool_definitions_digest'])==64
        assert env.adapter.n==1
    finally:release.set()


@pytest.mark.parametrize('change',['tool_contract','private_blob'])
async def test_changed_state_before_actual_egress_is_zero_model(running_product,monkeypatch,change):
    from pyharness.application.platform_runtime import PlatformRuntime
    from tests.unit.test_execution_state_snapshots import launch
    original=PlatformRuntime.before_model_attempt
    async def mutate(self,model,method,ctx):
        if change=='tool_contract':ctx.tools._r.lookup('fs.read_file').schema_['description']='changed after actual checkpoint'
        else:
            fact=next(e for e in ctx.session.events_after(0) if e.type=='execution.state_checkpoint')
            (self.platform.root/'execution-state'/fact.payload['state_digest']).write_bytes(b'changed before real model boundary')
        return await original(self,model,method,ctx)
    monkeypatch.setattr(PlatformRuntime,'before_model_attempt',mutate)
    result,_=await launch(running_product,'state-egress-mutation')
    assert result['status']=='failed' and running_product.adapter.n==0


async def test_unknown_fallback_price_never_claims_free_recovery_budget(running_product):
    env=running_product
    env.app.service.ctx.settings.llm.usage.unit_price.clear()
    started,release=await held(env)
    try:
        ctx=env.controls.ctx;fact=next(e for e in ctx.session.events_after(0) if e.type=='execution.state_checkpoint')
        contract=state_blob(env,fact).get('continuation_contract')
        assert contract is not None and contract['pricing_known'] is False
        assert contract['input_rate'] is None and contract['output_rate'] is None
        assert env.adapter.n==1
    finally:release.set()


def test_readonly_runtime_inventory_accepts_normal_package_cache_hardlinks(tmp_path,monkeypatch):
    import os
    from types import SimpleNamespace
    from pyharness.application import execution_state_contract as module
    cache=tmp_path/'cache.py';cache.write_bytes(b'VALUE = 1\n')
    package=tmp_path/'package';(package/'application').mkdir(parents=True)
    os.link(cache,package/'cached_module.py')
    monkeypatch.setattr(module,'__file__',str(package/'application'/'execution_state_contract.py'))
    fingerprint=module.runtime_contract_digest(SimpleNamespace())
    assert len(fingerprint)==64 and cache.read_bytes()==b'VALUE = 1\n'


def test_new_service_runtime_identity_reads_bytes_when_metadata_is_preserved(tmp_path,monkeypatch):
    import os,sys
    from types import SimpleNamespace
    from pyharness.application import execution_state_contract as module
    package=tmp_path/'package';(package/'application').mkdir(parents=True)
    source=package/'module.py';source.write_bytes(b'VALUE = 1\n');info=source.stat()
    monkeypatch.setattr(module,'__file__',str(package/'application'/'execution_state_contract.py'))
    before=module.runtime_contract_digest(SimpleNamespace())
    source.write_bytes(b'VALUE = 2\n');os.utime(source,ns=(info.st_atime_ns,info.st_mtime_ns))
    after=module.runtime_contract_digest(SimpleNamespace())
    assert before!=after,'New Services must not trust metadata-only runtime caches'
    assert after==digest(dict(files={'module.py':module.digest_of(source.read_bytes())},
        python=list(sys.version_info[:3]),platform=sys.platform,os_name=os.name))


async def test_unavailable_runtime_inventory_preserves_explicit_snapshot_unavailability(running_product,monkeypatch):
    from pyharness.application import execution_state_contract as module
    from pyharness.application.execution_state_capture import StateUnavailable
    from tests.unit.test_execution_state_snapshots import launch
    def unavailable(service):raise StateUnavailable('snapshot_limit')
    monkeypatch.setattr(module,'runtime_contract_digest',unavailable)
    result,log=await launch(running_product,'unavailable-runtime-preparation')
    assert result['status']=='completed' and running_product.adapter.n==1
    facts=[e.payload for e in log.events_after(0) if e.type=='execution.state_checkpoint']
    assert facts and all(p['status']=='unavailable' and p['unavailable_reason']=='snapshot_limit' and
        p['state_digest'] is None and not p['replay_authorized'] for p in facts)
