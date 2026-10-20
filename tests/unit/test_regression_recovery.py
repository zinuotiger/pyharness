"""An ambiguous data commit is queried by its original key, never blindly duplicated."""
import pytest
from pyharness.application.workspace_store import WorkspaceStore
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_regression_dataset_api import BASE,CID,case_command,save_case,saved


@pytest.mark.parametrize('fault',['before-append','after-disk-commit'])
async def test_unknown_case_commit_query_then_reopen_keeps_exactly_one_original_command(running_product,monkeypatch,fault):
    env=running_product;body=await case_command(env);store=env.app.service.projects.store
    before=store.log_path.read_bytes();calls=env.adapter.n
    with monkeypatch.context() as patch:
        if fault=='before-append':
            async def append(*args,**kwargs):raise OSError('controlled data append failure')
            patch.setattr(store._session,'append',append)
        else:
            def verify(*args,**kwargs):raise OSError('controlled loss of commit acknowledgement')
            patch.setattr(store,'_verify_commit',verify)
        response=await save_case(env,body,'uncertain-regression-command')
        assert response.status_code==503,response.text
        assert store._failed
        response=await env.client.get(BASE+'/regression-submissions/uncertain-regression-command')
        assert response.status_code==200 and response.headers['cache-control']=='no-store',response.text
        query=response.json()
        assert query['status']==('not_found' if fault=='before-append' else 'committed')
        assert query['writer_requires_reopen']
        assert (await save_case(env,body,'uncertain-regression-command')).status_code==503
    after=store.log_path.read_bytes()
    assert len(after.splitlines())==len(before.splitlines())+(fault=='after-disk-commit')
    await store.close();env.app.service.projects.store=WorkspaceStore(store.root,tenant_id=store.tenant_id)
    response=await save_case(env,body,'uncertain-regression-command')
    assert response.status_code==200 and response.json()['case_id']==CID,response.text
    current=env.app.service.projects.store
    assert len(current.log_path.read_bytes().splitlines())==len(before.splitlines())+1
    query=await env.client.get(BASE+'/regression-submissions/uncertain-regression-command')
    assert query.status_code==200 and query.json()['receipt']==response.json(),query.text
    assert env.adapter.n==calls


async def test_submission_reader_never_returns_a_different_resource_kind(running_product):
    env=running_product;await saved(env);store=env.app.service.projects.store;before=store.log_path.read_bytes()
    response=await env.client.get(BASE+'/regression-submissions/project')
    assert response.status_code==403 and response.headers['cache-control']=='no-store',response.text
    response=await env.client.get(BASE+'/regression-submissions/not-used')
    assert response.status_code==200 and response.json()['status']=='not_found',response.text
    assert store.log_path.read_bytes()==before
