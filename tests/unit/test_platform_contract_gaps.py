"""Regression coverage for the externally reproduced Platform contract gaps."""
import httpx
import pytest
import asyncio
from copy import deepcopy
from pydantic import ValidationError

from pyharness import cli
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings
from pyharness.errors import PyHError
from pyharness.application.accounts import Accounts, current_actor, public_actor
from pyharness.application.platform_models import AgentDefinition


def test_fallback_explicit_empty_is_persisted_and_omitted_preserves(tmp_path):
    store = TenantSettingsStore(tmp_path)
    body = dict(id='bounded',model='synthetic',base_url='http://127.0.0.1:1')
    store.upsert_profile('default',{**body,'fallback_models':['backup']})
    assert store.upsert_profile('default',body)['profile']['fallback_models']==['backup']
    assert store.upsert_profile('default',{**body,'fallback_models':[]})['profile']['fallback_models']==[]
    assert TenantSettingsStore(tmp_path).state('default')['profiles'][0]['fallback_models']==[]


@pytest.mark.parametrize('content,expected',[(None,200),(b'{}',200),(b'{invalid',422),(b'[]',422)])
async def test_owner_publish_body_contract(tmp_path,content,expected):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/account/users',headers={'X-PyHarness-Token':app._api_token},
                json={'username':'publisher','password':'synthetic-publisher-password','tenant':'publishing'})).status_code==201
            assert (await client.post('/api/account/login',json={'username':'publisher','password':'synthetic-publisher-password'})).status_code==200
            created=await client.post('/api/v1/agents',json={'name':'publish-body'})
            assert created.status_code==200
            ident=created.json()['agent_id']
            response=await client.post(f'/api/v1/agents/{ident}/publish',content=content,headers={'Content-Type':'application/json'})
            assert response.status_code==expected
    finally:
        for service in app._service_registry.all():await service.shutdown()


@pytest.mark.parametrize('path,value',[
    ('loop.step_timeout_s',61),('loop.tool_concurrency',2),('loop.thread_pool_size',8),
    ('security.sandbox.proc_mem_limit_mb',64),('security.credentials.cache_ttl_s',0),
    ('plugins.ctx_lazy',False),('plugins.pre_activate',[]),
])
def test_unsupported_config_overrides_rejected(tmp_path,path,value):
    from pyharness.config import Settings
    data=isolated_settings(tmp_path).model_dump()
    node=data
    parts=path.split('.')
    for key in parts[:-1]:node=node[key]
    node[parts[-1]]=value
    with pytest.raises(ValidationError,match=parts[-1]):Settings.model_validate(data)


@pytest.mark.parametrize('rule',['unknown','allow:fs.write_file','approve:unknown','deny:*'])
def test_approval_unknown_or_weakening_rules_rejected(rule):
    with pytest.raises(ValidationError):AgentDefinition(name='policy',approval_rules=[rule])


async def test_evidence_required_rejects_before_model_and_queue(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path);adapter=adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg));p=app.service.platform
    try:
        source=await p.add_knowledge({'name':'evidence.txt','text':'alpha evidence'})
        draft=p.save_agent({'name':'grounded','require_evidence':True,'knowledge_sources':[source['source_id']]})
        p.publish_agent(draft['agent_id'])
        with pytest.raises(PyHError,match='knowledge_no_evidence'):
            await p.start_run({'text':'zzmissingzz','agent_id':draft['agent_id'],'agent_version':1})
        assert adapter.n==0 and not app.service._queues
        missing=p.save_agent({'name':'missing source','require_evidence':True,'knowledge_sources':['missing']})
        p.publish_agent(missing['agent_id'])
        with pytest.raises(PyHError,match='knowledge_source_missing'):
            await p.start_run({'text':'alpha','agent_id':missing['agent_id'],'agent_version':1})
        plain=await p.start_run({'text':'ordinary non-knowledge chat'})
        assert (await asyncio.wait_for(app.service._queues[plain['session_id']].wait_for(plain['task_id']),10)).ok
    finally:await app.service.shutdown()


@pytest.mark.parametrize('role',['owner','member','viewer'])
async def test_memory_service_uses_current_membership_and_rejects_cross_team(tmp_path,role):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    accounts=Accounts(tmp_path)
    owner=accounts.provision('owner','synthetic-owner-password','team')
    member=accounts.provision('other','synthetic-other-password','other-team')
    accounts.membership('team','other',role)
    service=app._service_registry.get('team')
    token=current_actor.set({'user_id':member['user_id'],'tenant':'team'})
    try:
        if role=='owner':
            await service.memory_action({'op':'set','key':'sample','value':'value'})
            accounts.membership('team','other','member')
        for op in ['set','delete']:
            with pytest.raises(PyHError,match='GRD-403'):
                await service.memory_action({'op':op,'key':'sample',**({'value':'wrong'} if op=='set' else {})})
        assert (await service.memory_action({'op':'keys'}))['scope']=='team'
        with pytest.raises(PyHError,match='GRD-403'):
            await app._service_registry.get('other-team').memory_action({'op':'keys'})
    finally:
        current_actor.reset(token)
        for service in app._service_registry.all():await service.shutdown()


async def test_tighten_rule_enters_real_approval_before_memory_side_effect(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path)
    adapter_factory(cfg,calls=[{'id':'write','name':'storage.kv','args':{'op':'set','key':'sample','value':'value'}}])
    ctx=cli.assemble_ctx(cfg);app=DesktopApp(ctx)
    requested=asyncio.Event()
    async def observe(*args,**kwargs):requested.set()
    ctx.bus.subscribe('approval.requested',observe,owner='regression')
    try:
        p=app.service.platform
        draft=p.save_agent({'name':'extra approval','memory_scope':'team','allowed_tools':['storage.kv'],
            'approval_rules':['approve:storage.kv']})
        p.publish_agent(draft['agent_id'])
        run=await p.start_run({'text':'request governed write','agent_id':draft['agent_id'],'agent_version':1})
        await asyncio.wait_for(requested.wait(),10)
        assert not (app.service.tenant_root()/'memory.kv.json').exists()
        assert (await p.run(run['run_id']))['status']=='waiting_approval'
        await p.cancel(run['run_id'])
        assert not (app.service.tenant_root()/'memory.kv.json').exists()
    finally:await app.service.shutdown()


@pytest.mark.parametrize('danger', ['low', 'high', 'critical'])
def test_tighten_registry_preserves_binding_validation_and_frozen_contract(danger):
    from pyharness.core.tools_registry import ToolRegistry, ToolDefinition
    registry = ToolRegistry()
    provider = object()
    original = ToolDefinition(name='sample.read', owner='builtin', description='Synthetic read',
        danger=danger, approval='auto', schema={'type':'object',
        'properties':{'path':{'type':'string'}}, 'required':['path'], 'additionalProperties':False})
    registry.register_tool(original, provider=provider)
    registry.require_approval('sample.read')
    assert registry.lookup_provider('sample.read') is provider
    assert registry.lookup('sample.read').approval == 'always'
    assert registry.lookup('sample.read').danger == ('critical' if danger == 'critical' else 'high')
    assert original.approval == 'auto' and original.danger == danger
    assert registry.validate_args('sample.read', {'path':'calculator.py'}) == {'path':'calculator.py'}
    with pytest.raises(PyHError):
        registry.validate_args('sample.read', {'path':'calculator.py', 'extra':True})
    tightened = registry.lookup('sample.read')
    registry.mark_running('sample.read')
    with pytest.raises(PyHError, match='BUSY'):
        registry.require_approval('sample.read')
    assert registry.lookup('sample.read') is tightened
    assert registry.lookup_provider('sample.read') is provider


def test_profile_invalid_update_leaves_previous_fallback(tmp_path):
    store=TenantSettingsStore(tmp_path)
    body={'id':'safe','model':'test','base_url':'http://127.0.0.1:1','fallback_models':['prior']}
    store.upsert_profile('default',body)
    with pytest.raises(PyHError):
        store.upsert_profile('default',{**body,'fallback_models':[],'max_tokens':0})
    assert TenantSettingsStore(tmp_path).state('default')['profiles'][0]['fallback_models']==['prior']


async def test_member_run_cannot_write_owner_published_team_memory(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path)
    adapter=adapter_factory(cfg,calls=[{'id':'write','name':'storage.kv','args':{'op':'set','key':'shared','value':'forbidden'}}])
    app=DesktopApp(cli.assemble_ctx(cfg))
    accounts=Accounts(tmp_path)
    accounts.provision('owner','synthetic-owner-password','team')
    member=accounts.provision('member','synthetic-member-password','another')
    accounts.membership('team','member','member')
    service=app._service_registry.get('team');p=service.platform
    draft=p.save_agent({'name':'shared memory','memory_scope':'team','allowed_tools':['storage.kv']})
    p.publish_agent(draft['agent_id'])
    # A test adapter supplies deterministic responses; no provider credential.
    service.save_model_profile({'id':'synthetic','model':cfg.llm.model,'base_url':cfg.llm.base_url,'api_key':'synthetic-only'})
    token=current_actor.set({'user_id':member['user_id'],'tenant':'team'})
    try:
        run=await p.start_run({'text':'attempt write','agent_id':draft['agent_id'],'agent_version':1})
    finally:current_actor.reset(token)
    try:
        await asyncio.wait_for(service._queues[run['session_id']].wait_for(run['task_id']),10)
        log=await service.require_session(run['session_id'])
        assert any('GRD-403' in str(e.payload) for e in log.events_after(0))
        assert not (service.tenant_root()/'memory.kv.json').exists()
    finally:
        for service in app._service_registry.all():await service.shutdown()
