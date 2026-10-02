import asyncio
from types import SimpleNamespace
import pytest
from pyharness.core.subagent import SubagentManager,SubagentSpec,SubagentSpawnProvider
from pyharness.core.scope import ScopePolicy
from pyharness.errors import PyHError
from tests.unit.test_subagent import _parent,_fake_scope,GatedRunner,FakeTools


async def test_subset_and_nested_scope_never_expand(tmp_path):
    runner=GatedRunner();parent=await _parent()
    broad=_fake_scope(policy=ScopePolicy(workspace_root=str(tmp_path/'parent')))
    narrow=_fake_scope(policy=ScopePolicy(workspace_root=str(tmp_path/'narrow'),allowed_tools={'fs.read_file'},deny_tools={'fs.delete_file'}))
    manager=SubagentManager(session=parent,runner=runner,parent_scope=broad,tools=FakeTools(['fs.read_file','fs.write_file']))
    try:
        ident=await manager.spawn(SubagentSpec(task='bounded',depth=1,tools_subset=['fs.read_file','fs.write_file']),SimpleNamespace(scope=narrow,round_seq=2))
        await asyncio.wait_for(runner.started.wait(),2)
        scope=runner.envs[0].scope
        assert scope.can_use('fs.read_file')
        assert not scope.can_use('fs.write_file') and not scope.can_use('fs.delete_file')
        assert narrow.policy.allowed_tools=={'fs.read_file'}
        assert await manager.join(ident,timeout=.01) is None
        assert manager.status().running==1, 'Join timeout is not cancellation'
    finally:await manager.close_children(reason='test cleanup')


async def test_provider_cancel_waits_for_its_owned_child(tmp_path):
    runner=GatedRunner();manager=SubagentManager(session=await _parent(),runner=runner,
        parent_scope=_fake_scope(policy=ScopePolicy(workspace_root=str(tmp_path/'parent'))))
    operation=asyncio.create_task(SubagentSpawnProvider(manager).handle({'task':'wait'},SimpleNamespace(round_seq=2)))
    await asyncio.wait_for(runner.started.wait(),2)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):await operation
    assert manager.status().running==0 and manager._active==0
    runner.started.clear();runner.gate.set()
    assert await SubagentSpawnProvider(manager).handle({'task':'followup'},SimpleNamespace(round_seq=2))=='gated-ok'
    await manager.close_children(reason='test cleanup')


async def test_failed_scope_construction_does_not_start_child(tmp_path):
    runner=GatedRunner();manager=SubagentManager(session=await _parent(),runner=runner,
        parent_scope=_fake_scope(policy=ScopePolicy(workspace_root=str(tmp_path/'parent'))),child_scope_factory=lambda *args:None)
    with pytest.raises(PyHError,match='GRD-403'):
        await manager.spawn(SubagentSpec(task='must not run',depth=1))
    assert not runner.started.is_set() and manager.status().running==0 and manager._active==0
