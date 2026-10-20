"""Actual SQL membership withdrawal after strong commit, before HTTP receipt."""
import pytest
from tests.unit.test_delivery_handoffs import delivery_team,post
from tests.unit.test_writeback_application import prepared,plan
from tests.unit.test_product_tasks import ref


@pytest.mark.parametrize('operation',['plan','apply','export','grant','revoke'])
async def test_committed_receipt_after_membership_withdrawal_stays_unknown(delivery_team,tmp_path,monkeypatch,operation):
    env=await prepared(delivery_team,tmp_path)
    env.accounts.membership('delivery-team','delivery-stranger','owner')
    if operation!='plan':await plan(env)
    if operation=='plan':
        preview=await post(env.owner,'/merge-previews',env.merge,'preview-before-race');assert preview.status_code==200,preview.text
        path='/merge-plans';command=dict(env.merge,plan_id='mp-'+'7'*16,confirmation_digest=preview.json()['confirmation_digest']);kind='merge_plan'
    elif operation=='apply':path='/writeback-applications';command=env.apply;kind='writeback_application'
    elif operation=='export':path='/merge-plan-exports';command=dict(schema_version=1,export_id='we-'+'7'*16,plan_ref=ref(env.plan,'plan_id'));kind='merge_export'
    elif operation=='grant':
        path='/writeback-grants';command=dict(schema_version=1,grant_id='wg-'+'7'*16,project_revision=1,root_path=str(env.target.resolve()),recipient_username='delivery-owner',paths=['result.txt'],actions=['replace'],expires_at='2099-01-01T00:00:00Z');kind='writeback_grant'
    else:path='/writeback-grants/'+env.write_grant['grant_id']+'/revoke';command=dict(schema_version=1,expected_revision=1);kind='writeback_grant'
    store=env.service.projects.store;original=store._append_context;withdrawals=[]
    async def withdraw_after_commit(value,resource_kind,*args,**kwargs):
        result=await original(value,resource_kind,*args,**kwargs)
        if resource_kind==kind and (operation!='apply' or value.revision==2) and not withdrawals:
            env.accounts.membership('delivery-team','delivery-owner','remove');withdrawals.append(True)
        return result
    monkeypatch.setattr(store,'_append_context',withdraw_after_commit)
    response=await post(env.owner,path,command,'receipt-withdrawal-'+operation)
    assert withdrawals,'Test must hit the actual post-commit window'
    assert response.status_code==503,response.text
    assert response.json().get('reason')=='writeback_commit_receipt_unavailable',response.text
    expected=b'done' if operation=='apply' else env.original
    assert (env.target/'result.txt').read_bytes()==expected
    resources,commands,_=await store._execution_read_state()
    assert any(key[1]=='receipt-withdrawal-'+operation for key in commands),'Already committed command must remain reconcilable'
