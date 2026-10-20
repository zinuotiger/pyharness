"""Real host filesystem and queue; scripted inference is not external model evidence."""
import json
import asyncio
from types import SimpleNamespace
import pytest
from tests.unit.test_product_execution_start import running_product, wait_terminal
from tests.unit.test_source_grants import PID,GID


RAW=('\ufeff精确资料🙂\r\n'*9000).encode('utf-8')
OPTIONS=dict(raw=RAW, agent=dict(sandbox_profile=dict(mode='host_approved', workspace_mode='private_copy')),
    script=[dict(id='read-index',name='fs.read_file',args=dict(path='_pyharness_context/index.json',char_start=0,char_limit=1200)),
            dict(id='read-body',name='fs.read_file',args=dict(path='_pyharness_context/file-0000-0000.txt',char_start=0,char_limit=1200))])


@pytest.mark.parametrize('running_product',[OPTIONS,dict(OPTIONS,raw=b'captured\r\n'*120000)],indirect=True)
async def test_large_context_executes_in_real_captured_host_workspace_with_complete_ranges(running_product):
    env=running_product;projects=env.app.service.projects
    captured=(env.source/'unchanged.txt').read_bytes()
    backend=env.app.service.platform.sandboxes.backends['host_approved']
    create=backend.create;observed={}
    async def inspect(record,root,profile):
        observed.update({p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()})
        await create(record,root,profile)
    backend.create=inspect
    (env.source/'unchanged.txt').write_bytes(b'new-host-body')
    preview=await projects.execution_preview(PID,env.selection)
    assert preview['context_layout']=='workspace'
    ident='ex-'+'9'*16
    await projects.start_execution(PID,dict(env.selection,execution_id=ident,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'large-host')
    result=await wait_terminal(projects,ident)
    assert result['status']=='completed' and env.adapter.n==2,result
    assert observed['unchanged.txt']==captured
    index=json.loads(observed['_pyharness_context/index.json'])
    bundle=b''.join(observed[index['bundle']['prefix']+f'{n:04d}.txt'] for n in range(index['bundle']['count']))
    assert json.loads(bundle)['files'][0]['text'].encode('utf-8')==captured
    log=env.app.service.surface_mgr()._logs[result['session_id']]
    reads=[e.payload for e in log.events_after(0) if e.type=='tool.result']
    assert len(reads)==2 and all(not p.get('truncated') and not p.get('spill_ref') for p in reads)
    expected=observed['_pyharness_context/file-0000-0000.txt'].decode('utf-8')[:1200]
    assert any(p['summary']==expected for p in reads)
    assert (env.source/'unchanged.txt').read_bytes()==b'new-host-body'
    assert all(r.status=='destroyed' for r in env.app.service.platform.sandboxes.records.values())
    assert not backend.roots
    assert not any(e.type=='platform.artifact' and e.payload['action']=='generated' for e in log.events_after(0)), 'Unmodified captured input became a false generated output'


def test_initial_snapshot_keeps_all_200_inputs_including_hidden_and_inputs_directory(tmp_path):
    from pyharness.application.execution_file_runtime import text_snapshot,visible_output
    paths=['.captured.txt','inputs/captured.txt',*[f'dir/item-{n:03d}.txt' for n in range(198)]]
    for name in paths:
        path=tmp_path/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b'\xef\xbb\xbfcaptured\r\n')
    (tmp_path/'_ctx').mkdir();(tmp_path/'_ctx/derived.txt').write_text('reference')
    value=SimpleNamespace(workspace_plan={'context_directory':'_ctx'},materials={'bundle':{'files':[{'path':p} for p in paths]}})
    snapshot=text_snapshot(value,tmp_path)
    assert set(snapshot)==set(paths)
    assert all(raw.encode()==b'\xef\xbb\xbfcaptured\r\n' for raw in snapshot.values())
    assert visible_output(value,'.captured.txt') and visible_output(value,'inputs/captured.txt')
    assert not visible_output(value,'_ctx/derived.txt')


def test_context_reads_reject_altered_derived_materials_and_validate_unicode_ranges():
    from pyharness.application.execution_file_runtime import checked_context_read,bounded_read
    from pyharness.errors import PyHError
    body='资料🙂\r\n'
    value=SimpleNamespace(workspace_plan=dict(context_directory='_ctx',contents={'_ctx/data.txt':body.encode()}))
    assert bounded_read(checked_context_read(value,'_ctx/data.txt',body),dict(char_start=1,char_limit=2))=='料🙂'
    with pytest.raises(PyHError):checked_context_read(value,'_CTX/data.txt','altered')
    with pytest.raises(PyHError):bounded_read(body,dict(char_start=-1,char_limit=1))
    with pytest.raises(PyHError):bounded_read(body,dict(char_start=True,char_limit=1))
    with pytest.raises(PyHError):bounded_read(body,dict(char_start=False,char_limit=1))
    with pytest.raises(PyHError):bounded_read(body,dict(char_start=0,char_limit=0))


@pytest.mark.parametrize('running_product',[dict(OPTIONS,raw=b'captured\r\n',agent=dict(OPTIONS['agent'],timeout=2))],indirect=True)
async def test_declared_deadline_stops_backend_preparation_before_first_model_and_cleans_root(running_product):
    env=running_product;projects=env.app.service.projects
    backend=env.app.service.platform.sandboxes.backends['host_approved']
    create=backend.create;entered=asyncio.Event();destroyed=[];destroy=backend.destroy
    async def blocked(record,root,profile):
        await create(record,root,profile);entered.set()
        await asyncio.Event().wait()
    async def cleanup(record):
        destroyed.append(record.sandbox_id);await destroy(record)
    backend.create=blocked;backend.destroy=cleanup
    preview=await projects.execution_preview(PID,env.selection);ident='ex-'+'a'*16
    started=await projects.start_execution(PID,dict(env.selection,execution_id=ident,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'preparation-deadline')
    await asyncio.wait_for(entered.wait(),4)
    queue=env.app.service._queues[started['session_id']]
    result=await queue.wait_for(started['task_id'],timeout=4)
    assert not result.ok and env.adapter.n==0
    observed=await wait_terminal(projects,ident)
    assert observed['status']=='failed'
    log=env.app.service.surface_mgr()._logs[observed['session_id']]
    phases=[e.payload for e in log.events_after(0) if e.type=='execution.lifecycle']
    assert phases[-1]['reason']=='execution_deadline_expired'
    assert len(destroyed)==1 and not backend.roots
    assert not list(env.app.service.platform.sandboxes.root.iterdir())


@pytest.mark.parametrize('running_product',[dict(OPTIONS,raw=b'captured\r\n')],indirect=True)
async def test_actual_source_revocation_commits_while_backend_waits_and_blocks_first_model(running_product):
    env=running_product;projects=env.app.service.projects;manager=env.app.service.platform.sandboxes
    backend=manager.backends['host_approved'];create=backend.create
    entered=asyncio.Event();resume=asyncio.Event()
    async def blocked(record,root,profile):
        await create(record,root,profile);entered.set();await resume.wait()
    backend.create=blocked
    preview=await projects.execution_preview(PID,env.selection);ident='ex-'+'c'*16
    started=await projects.start_execution(PID,dict(env.selection,execution_id=ident,
        confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=True),'revoke-preparation')
    queue=env.app.service._queues[started['session_id']]
    try:
        await asyncio.wait_for(entered.wait(),4)
        revoked=await asyncio.wait_for(env.client.post(f'/api/v1/source-grants/{GID}/revoke',
            json=dict(expected_revision=1),headers={'Idempotency-Key':'revoke-during-backend'}),2)
        assert revoked.status_code==200,revoked.text
    finally:resume.set()
    result=await queue.wait_for(started['task_id'],timeout=4)
    assert not result.ok and env.adapter.n==0
    assert not backend.roots and not list(manager.root.iterdir())
    log=env.app.service.surface_mgr()._logs[started['session_id']]
    phases=[e.payload['phase'] for e in log.events_after(0) if e.type=='execution.lifecycle']
    assert phases==['bound','running','failed']
    assert not any(e.type=='execution.effect' for e in log.events_after(0))
