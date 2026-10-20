"""Actual binary/empty-directory restoration into a fresh private tree only."""
import stat
import pytest
from pyharness.errors import PyHError
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_product_execution_file_effects import OPTIONS,execute_writes
from tests.unit.test_execution_state_snapshots import state_blob

@pytest.fixture
async def captured_files(running_product,monkeypatch):
    from pyharness.application.platform_runtime import PlatformRuntime
    from pyharness.application.state_snapshot_contracts import ExecutionStateSnapshot
    original=PlatformRuntime.materialize_messages
    async def seeded(self,messages,ctx):
        ident=self.sandboxes.get(str(ctx.task_id))
        if ident is not None:
            root=self.platform.sandboxes.workspace(ident)
            (root/'empty-retained').mkdir(exist_ok=True)
            (root/'.retained-binary').write_bytes(b'\x00\xff\xfe\r\n')
        return await original(self,messages,ctx)
    monkeypatch.setattr(PlatformRuntime,'materialize_messages',seeded)
    result,log=await execute_writes(running_product)
    fact=[e for e in log.events_after(0) if e.type=='execution.state_checkpoint'][-1]
    return running_product,ExecutionStateSnapshot.model_validate(state_blob(running_product,fact))

@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
async def test_actual_bytes_empty_directory_and_hidden_binary_are_preserved(captured_files,tmp_path):
    from pyharness.application.execution_state_restore import restore_state_workspace,verify_restored_workspace
    env,body=captured_files;target=tmp_path/'new-owned-attempt';target.mkdir(mode=0o700);gates=[]
    restore_state_workspace(target,body,lambda:gates.append(True));verify_restored_workspace(target,body,lambda:gates.append(True))
    assert (target/'unchanged.txt').read_bytes()==b'synthetic-output\n'
    assert (target/'.retained-binary').read_bytes()==b'\x00\xff\xfe\r\n' and (target/'empty-retained').is_dir()
    assert len(gates)>len(body.files) and env.adapter.n==2
    assert (env.source/'unchanged.txt').read_bytes()==b'exact-selected-input\r\n'
    assert all(stat.S_IMODE((target/f.path).stat().st_mode)==f.mode for f in body.files)

@pytest.mark.parametrize('running_product',[OPTIONS],indirect=True)
@pytest.mark.parametrize('case',['nonempty','legacy','revoked','modified','extra-file','hardlink'])
async def test_restore_rejects_unconfirmed_or_existing_state(captured_files,tmp_path,case):
    import os
    from pyharness.application.execution_state_restore import restore_state_workspace,verify_restored_workspace
    env,body=captured_files;target=tmp_path/'new-owned-attempt';target.mkdir(mode=0o700)
    if case=='nonempty':
        (target/'sentinel').write_bytes(b'keep existing user bytes')
        with pytest.raises(PyHError):restore_state_workspace(target,body,lambda:None)
        assert list(target.iterdir())==[target/'sentinel'] and (target/'sentinel').read_bytes()==b'keep existing user bytes'
    elif case=='legacy':
        with pytest.raises(PyHError):restore_state_workspace(target,body.model_copy(update=dict(filesystem_capture='files_only_legacy')),lambda:None)
        assert not list(target.iterdir())
    elif case=='revoked':
        def deny():raise PyHError('GRD-403')
        with pytest.raises(PyHError,match='GRD-403'):restore_state_workspace(target,body,deny)
        assert not list(target.iterdir())
    else:
        restore_state_workspace(target,body,lambda:None)
        if case=='modified':(target/'unchanged.txt').write_bytes(b'actual changed restored bytes')
        if case=='extra-file':(target/'extra.txt').write_bytes(b'unknown file')
        if case=='hardlink':os.link(target/'unchanged.txt',tmp_path/'external-hardlink')
        with pytest.raises(PyHError):verify_restored_workspace(target,body,lambda:None)
    assert env.adapter.n==2 and (env.source/'unchanged.txt').read_bytes()==b'exact-selected-input\r\n'


def test_replaced_destination_cannot_change_external_hardlink_permissions(tmp_path):
    import os
    from pyharness.application.execution_state_restore import write_new_state_file
    outside=tmp_path/'outside-sentinel';outside.write_bytes(b'preserve external metadata and bytes');outside.chmod(0o444)
    original=stat.S_IMODE(outside.stat().st_mode);root=tmp_path/'owned';root.mkdir();calls=0
    def replace_after_close():
        nonlocal calls
        calls+=1
        if calls==3:
            (root/'result').rename(root/'old-owned-result');os.link(outside,root/'result')
    try:
        with pytest.raises(Exception):write_new_state_file(root,'result',b'owned bytes',0o600,replace_after_close)
        assert outside.read_bytes()==b'preserve external metadata and bytes'
        assert stat.S_IMODE(outside.stat().st_mode)==original,'late path chmod changed metadata outside the owned workspace'
    finally:outside.chmod(0o600)


@pytest.mark.parametrize('running_product',[[dict(id='restore-kv-seed',name='storage.kv',args=dict(op='set',key='retained',value='original actual KV bytes'))]],indirect=True)
async def test_actual_kv_restores_only_new_namespace_without_overwriting_original(running_product,tmp_path):
    import base64
    from pyharness.application.execution_state_restore import restore_state_kv
    from pyharness.application.state_snapshot_contracts import ExecutionStateSnapshot
    from tests.unit.test_execution_state_snapshots import launch
    env=running_product;_,log=await launch(env,'restore-kv-original')
    facts=[e for e in log.events_after(0) if e.type=='execution.state_checkpoint']
    before,after=[ExecutionStateSnapshot.model_validate(state_blob(env,f)) for f in facts]
    original=env.app.service._tenant_sessions_dir()/(log.sid+'.kv.json');original_bytes=original.read_bytes()
    directory=tmp_path/'new-namespaces';directory.mkdir(mode=0o700);new_sid='s-pa-'+'4'*32
    restore_state_kv(directory,new_sid,after,lambda:None)
    target=directory/(new_sid+'.kv.json');assert target.read_bytes()==base64.b64decode(after.kv_data_b64,validate=True)==original_bytes
    with pytest.raises(PyHError):restore_state_kv(directory,new_sid,after,lambda:None)
    with pytest.raises(PyHError):restore_state_kv(directory,log.sid,after,lambda:None)
    absent_sid='s-pa-'+'5'*32;restore_state_kv(directory,absent_sid,before,lambda:None)
    assert not (directory/(absent_sid+'.kv.json')).exists() and target.read_bytes()==original.read_bytes()==original_bytes
    assert env.adapter.n==2
