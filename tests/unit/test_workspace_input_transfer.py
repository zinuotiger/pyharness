"""Actual API facts feed a synthetic isolated backend; never execute commands or Docker."""
from pathlib import Path
import os
import pytest
from pyharness.core.sandbox import SandboxManager
from pyharness.core.sandbox_models import SandboxProfile
from pyharness.errors import PyHError
from tests.unit.test_source_grants import grant_app,PID
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_owned_inputs import context,command,INPUTS,WI


class SyntheticBackend:
    def __init__(self,mutation=None):self.mutation=mutation;self.created=[];self.destroyed=[];self.executions=0
    async def create(self,record,root,profile):
        self.created.append(root)
        if self.mutation=='bytes':(root/'unchanged.txt').write_bytes(b'synthetic poisoned backend bytes')
        elif self.mutation=='extra':(root/'unexpected.txt').write_bytes(b'synthetic unexpected')
        elif self.mutation=='deleted':(root/'delete.txt').write_bytes(b'synthetic resurrected deletion')
        elif self.mutation=='failure':raise OSError('synthetic unavailable isolated backend')
    async def execute(self,*args,**kwargs):self.executions+=1;raise AssertionError('preparation must not execute')
    async def destroy(self,record):self.destroyed.append(record.sandbox_id)


async def prepared(app,client,root):
    _,cm=await context(client,root,is_dirty=True)
    response=await client.post(INPUTS,json=command(cm),headers={'Idempotency-Key':'prepare'})
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.controlled_process
async def test_actual_committed_inputs_populate_only_exact_private_bytes(git_repo,tmp_path):
    app,client,root=git_repo;receipt=await prepared(app,client,root)
    (root/'unchanged.txt').write_bytes(b'synthetic newer host content');(root/'unselected.txt').write_bytes(b'synthetic never selected')
    store=app.service.projects.store;backend=SyntheticBackend();manager=SandboxManager(tmp_path/'owned-sandboxes',docker=backend)
    before=store.log_path.read_bytes()
    async with store.workspace_input_transfer(PID,WI,authorize=lambda:'local-operator') as transfer:
        record=await manager.create('synthetic-run',SandboxProfile(mode='isolated',workspace_mode='empty'),prepare_inputs=transfer)
        destination=manager.workspace(record.sandbox_id)
        names={p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
        assert names=={i['path'] for i in receipt['files']}
        assert (destination/'unchanged.txt').read_bytes()==b'\xef\xbb\xbfworking\r\n'
        assert (destination/'.ai-coding/RULES.md').read_bytes()==b'explicit dot rules\r\n'
        assert not (destination/'delete.txt').exists()
        if os.name!='nt':assert all((destination/i['path']).stat().st_mode&0o777==0o600 for i in receipt['files'])
    with pytest.raises(PyHError):await manager.create('synthetic-reuse',SandboxProfile(mode='isolated',workspace_mode='empty'),prepare_inputs=transfer)
    assert len(backend.created)==1 and backend.executions==0 and store.log_path.read_bytes()==before
    await manager.destroy(record.sandbox_id);assert not destination.exists()


@pytest.mark.controlled_process
@pytest.mark.parametrize('mutation',['bytes','extra','deleted','failure'])
async def test_backend_mutation_cannot_pass_prepared_input_validation(git_repo,tmp_path,mutation):
    app,client,root=git_repo;await prepared(app,client,root)
    backend=SyntheticBackend(mutation);manager=SandboxManager(tmp_path/'owned-sandboxes',docker=backend)
    rejected=False
    async with app.service.projects.store.workspace_input_transfer(PID,WI,authorize=lambda:'local-operator') as transfer:
        try:await manager.create('synthetic-run',SandboxProfile(mode='isolated',workspace_mode='empty'),prepare_inputs=transfer)
        except (PyHError,OSError):rejected=True
    assert rejected,'isolated backend changed the exact input set but preparation was accepted'
    assert len(backend.created)==len(backend.destroyed)==1 and backend.executions==0
    assert not list(manager.root.iterdir())


@pytest.mark.controlled_process
@pytest.mark.parametrize('mode,workspace,host_source',[('host_approved','empty',False),('disabled','empty',False),('isolated','private_copy',False),('isolated','private_git_clone',False),('remote','empty',False),('isolated','empty',True)])
async def test_transfer_never_falls_back_to_host_or_legacy_copy(git_repo,tmp_path,mode,workspace,host_source):
    app,client,root=git_repo;await prepared(app,client,root)
    backend=SyntheticBackend();manager=SandboxManager(tmp_path/'owned-sandboxes',docker=backend)
    async with app.service.projects.store.workspace_input_transfer(PID,WI,authorize=lambda:'local-operator') as transfer:
        with pytest.raises(PyHError) as exc:await manager.create('synthetic-run',SandboxProfile(mode=mode,workspace_mode=workspace),root if host_source else None,prepare_inputs=transfer)
        assert exc.value.code=='sandbox_operation_unsupported'
    assert backend.created==[] and backend.executions==0 and not manager.root.exists()


@pytest.mark.controlled_process
async def test_transfer_source_overlap_rejects_before_creating_any_directory(git_repo):
    app,client,root=git_repo;await prepared(app,client,root)
    backend=SyntheticBackend();manager=SandboxManager(root/'synthetic-must-not-be-created',docker=backend)
    async with app.service.projects.store.workspace_input_transfer(PID,WI,authorize=lambda:'local-operator') as transfer:
        with pytest.raises(PyHError):await manager.create('synthetic-run',SandboxProfile(mode='isolated',workspace_mode='empty'),prepare_inputs=transfer)
    assert not manager.root.exists(),'prepared seam wrote inside read-only source before rejecting overlap'
    assert backend.created==[] and backend.executions==0
