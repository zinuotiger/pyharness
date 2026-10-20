"""Prediction is metadata-only and conveys no execution authority."""
import pytest
from pyharness.config import Settings
from pyharness.application.platform_models import AgentDefinition


async def predict(name,args,**overrides):
    from pyharness.application.permission_observation import predict_permission
    preset=overrides.pop('preset',None)
    agent=AgentDefinition(name='Preview only',allowed_tools=[name],
        sandbox_profile={'mode':'isolated'},**overrides)
    cfg=Settings();cfg.security.policy.preset=preset or agent.permission_policy
    return await predict_permission(agent.model_dump(mode='json'),cfg,
        {'bundle':{'files':[{'path':'sample.txt'}]}},name,args)


@pytest.mark.parametrize(('name','args','expected'),[
    ('fs.read_file',{'path':'sample.txt'},'allow'),
    ('file.read',{'path':'sample.txt','char_start':0,'char_limit':1200},'reject'),
    ('fs.write_file',{'path':'sample.txt','content':'updated'},'approval'),
    ('fs.read_file',{'path':'../outside.txt'},'reject'),
    ('fs.read_file',{'path':'credentials.yaml'},'reject'),
    ('fs.read_file',{'path':17},'reject'),
])
async def test_original_builtin_rules_and_exact_platform_schemas(name,args,expected):
    result=await predict(name,args)
    assert result['prediction']==expected,result
    assert result['execution_authorized'] is False and result['side_effects_executed'] is False
    assert result['live_recheck_required'] is True and 'authorization_token' not in result


async def test_scope_deny_is_not_downgraded_to_unknown_for_unsupported_tool():
    result=await predict('exec.shell_run',{'command':'echo never'},preset='locked')
    assert result['prediction']=='reject' and 'scope-hidden' in result['guard_ids']


async def test_shell_and_external_capabilities_are_unknown_without_loading_or_running():
    result=await predict('exec.shell_run',{'command':'echo never'},permission_policy='standard')
    assert result['prediction']=='unknown' and result['execution_authorized'] is False


async def test_prediction_does_not_mutate_cfg_or_register_a_scope(monkeypatch):
    from pyharness.application.permission_observation import predict_permission
    from pyharness.core.scope import _scopes
    cfg=Settings();cfg.security.policy.preset='standard';before=cfg.model_dump(mode='json')
    scopes=dict(_scopes);agent=AgentDefinition(name='Read only',allowed_tools=['fs.read_file'],sandbox_profile={'mode':'isolated'})
    def forbidden(*a,**kw):raise AssertionError('preview acquired a runtime/provider')
    monkeypatch.setattr('pyharness.core.scope.build_scope',forbidden)
    monkeypatch.setattr('pyharness.core.tool_fs.read_file',forbidden)
    result=await predict_permission(agent.model_dump(mode='json'),cfg,{'bundle':{'files':[{'path':'sample.txt'}]}},'fs.read_file',{'path':'sample.txt'})
    assert result['prediction']=='allow' and cfg.model_dump(mode='json')==before and _scopes==scopes


async def test_disabled_sandbox_and_published_denial_are_preserved():
    from pyharness.application.permission_observation import predict_permission
    agent=AgentDefinition(name='Disabled',allowed_tools=['fs.read_file'])
    result=await predict_permission(agent.model_dump(mode='json'),Settings(),{'bundle':{'files':[]}},'fs.read_file',{'path':'sample.txt'})
    assert result['prediction']=='reject' and result['reason']=='sandbox_disabled'
    result=await predict('fs.read_file',{'path':'sample.txt'},approval_rules=['deny:fs.read_file'])
    assert result['prediction']=='reject'


async def test_explicit_approval_rule_is_applied_on_the_same_definition():
    result=await predict('fs.read_file',{'path':'sample.txt'},approval_rules=['approve:fs.read_file'])
    assert result['prediction']=='approval'


async def test_standard_alias_uses_the_platform_bounded_read_schema():
    result=await predict('file.read',{'path':'sample.txt','char_start':0,'char_limit':1200},permission_policy='standard')
    assert result['prediction']=='allow'
    result=await predict('file.read',{'path':'sample.txt','char_limit':1501},permission_policy='standard')
    assert result['prediction']=='reject' and result['reason']=='schema_invalid'


@pytest.mark.parametrize('name,preset',[('fs.list_dir','strict'),('file.list','standard')])
async def test_original_optional_list_path_matches_explicit_workspace_root(name,preset):
    default=await predict(name,{},permission_policy=preset)
    explicit=await predict(name,{'path':'.'},permission_policy=preset)
    assert default['prediction']==explicit['prediction']=='allow'
    assert not default['execution_authorized'] and not default['side_effects_executed']
