import copy
from types import SimpleNamespace
import pytest
from pyharness import engine
from pyharness.config import Settings
from pyharness.core.scope import ScopePolicy


class Fault:
    def __init__(self, field, **values):
        object.__setattr__(self,'field',field)
        self.__dict__.update(values)
    def __setattr__(self,key,value):
        if key==self.field: raise RuntimeError('synthetic setter failed')
        object.__setattr__(self,key,value)


@pytest.mark.parametrize('field',['preset','sandbox_level','level','deny_tools'])
def test_preset_setter_failure_does_not_mutate_live_policy(field):
    cfg=Settings()
    policy=ScopePolicy()
    policy.deny_tools={'exec.shell_run'}
    if field=='preset':
        cfg=SimpleNamespace(security=SimpleNamespace(policy=Fault(field,preset='strict',deny_tools_extra=[]),sandbox=SimpleNamespace(level='strict')))
    elif field=='level':
        cfg=SimpleNamespace(security=SimpleNamespace(policy=SimpleNamespace(preset='strict',deny_tools_extra=[]),sandbox=Fault(field,level='strict')))
    else: policy=Fault(field,deny_tools={'exec.shell_run'},sandbox_level='strict')
    spine=SimpleNamespace(settings=cfg,scope=SimpleNamespace(policy=policy),
        tool_registry=SimpleNamespace(iter_definitions=lambda:[]))
    before=copy.deepcopy(policy.deny_tools)
    with pytest.raises(Exception): engine.compile_preset(spine,'standard')
    assert policy.deny_tools==before
    assert cfg.security.policy.preset=='strict'
