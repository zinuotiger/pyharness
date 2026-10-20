"""Trusted attempt preparation is distinct from the old isolated-only prepare seam."""
import asyncio
import hashlib
from types import SimpleNamespace
import pytest
from pyharness.core.sandbox import SandboxManager, WorkspaceInputPreparation
from pyharness.core.sandbox_models import SandboxProfile
from pyharness.errors import PyHError


class PreparedBackend:
    """In-process protocol backend; no claim of actual Docker or remote isolation."""
    def __init__(self):self.created=[]; self.destroyed=[]; self.after_create=None
    async def create(self, record, root, profile):
        assert (root/'input.txt').read_bytes() == b'captured\r\n'
        self.created.append((record.backend, root, profile.workspace_mode))
        if self.after_create:self.after_create()
    async def destroy(self, record):self.destroyed.append(record.sandbox_id)


def preparation(state):
    def gate():
        if not state.allowed:raise PyHError('GRD-403')
    def populate(root):
        gate(); (root/'input.txt').write_bytes(b'captured\r\n')
    return WorkspaceInputPreparation(hashlib.sha256(b'captured\r\n').hexdigest(), populate, gate, lambda _:gate()), gate


@pytest.mark.parametrize('mode', ['host_approved','isolated','remote'])
async def test_attempt_seam_uses_captured_empty_workspace_without_mutating_published_profile(tmp_path, mode):
    backend=PreparedBackend(); manager=SandboxManager(tmp_path/'sandbox'); manager.backends[mode]=backend
    state=SimpleNamespace(allowed=True); transfer, authorize=preparation(state)
    profile=SandboxProfile(mode=mode, workspace_mode='private_git_clone')
    record=await manager.create_for_attempt('owned-run', profile, transfer, authorize=authorize)
    assert profile.workspace_mode == 'private_git_clone'
    assert record.workspace_mode == 'empty' and len(backend.created)==1
    assert record.network == ('host_unrestricted' if mode=='host_approved' else 'none')
    await manager.destroy(record.sandbox_id)


async def test_waiting_for_sandbox_lock_rechecks_authority_before_allocating(tmp_path):
    manager=SandboxManager(tmp_path/'sandbox'); state=SimpleNamespace(allowed=True)
    transfer, authorize=preparation(state)
    await manager.lock.acquire()
    task=asyncio.create_task(manager.create_for_attempt('owned-run', SandboxProfile(mode='host_approved'), transfer, authorize=authorize))
    await asyncio.sleep(0); state.allowed=False; manager.lock.release()
    with pytest.raises(PyHError, match='GRD-403'):await task
    assert not manager.root.exists() and manager.records == {}


async def test_revocation_during_backend_creation_cleans_owned_resource_without_host_fallback(tmp_path):
    backend=PreparedBackend(); manager=SandboxManager(tmp_path/'sandbox'); manager.backends['isolated']=backend
    state=SimpleNamespace(allowed=True); transfer, authorize=preparation(state)
    backend.after_create=lambda:setattr(state,'allowed',False)
    with pytest.raises(PyHError, match='GRD-403'):
        await manager.create_for_attempt('owned-run', SandboxProfile(mode='isolated'), transfer, authorize=authorize)
    assert len(backend.created)==len(backend.destroyed)==1 and manager.records=={}
    assert not any(manager.root.iterdir())


async def test_client_dict_is_not_a_preparation_or_authority(tmp_path):
    manager=SandboxManager(tmp_path/'sandbox')
    with pytest.raises(PyHError):
        await manager.create_for_attempt('owned-run', SandboxProfile(mode='host_approved'), {}, authorize=lambda:None)
    assert not manager.root.exists()
