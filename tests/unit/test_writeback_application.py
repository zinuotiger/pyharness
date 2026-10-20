"""Real independent delivery, explicit host target scope, and actual file effects."""
import os
import pytest
from tests.unit.test_delivery_handoffs import delivery_team,handoff,ack,acceptance,post
from tests.unit.test_product_tasks import ref
from tests.unit.test_delivery_packages import BASE


async def prepared(env,tmp_path,paths=('result.txt',)):
    await handoff(env);await ack(env)
    response=await post(env.reviewer,'/delivery-acceptances',acceptance(env),'independent-before-writeback')
    assert response.status_code==200,response.text
    env.accepted=response.json();env.target=tmp_path/'explicit-target';env.target.mkdir()
    original=(env.source/'unchanged.txt').read_bytes()
    for path in paths:(env.target/path).write_bytes(original)
    env.original=original
    command=dict(schema_version=1,grant_id='wg-'+'1'*16,project_revision=1,root_path=str(env.target.resolve()),
        recipient_username='delivery-owner',paths=list(paths),actions=['replace','create','delete'],expires_at='2099-01-01T00:00:00Z')
    response=await post(env.owner,'/writeback-grants',command,'explicit-target-grant')
    assert response.status_code==200,response.text
    env.write_grant=response.json()
    env.merge=dict(schema_version=1,project_revision=1,grant_ref=ref(env.write_grant,'grant_id'),
        package_ref=ref(env.package,'package_id'),acceptance_ref=ref(env.accepted,'acceptance_id'),
        files=[dict(target_path=path,input_path='unchanged.txt',output_ref=env.output) for path in paths])
    return env


async def plan(env):
    response=await post(env.owner,'/merge-previews',env.merge,'preview-has-no-command-key')
    assert response.status_code==200,response.text
    preview=response.json();assert preview['status']=='mergeable' and preview['applicable'] is False
    command=dict(env.merge,plan_id='mp-'+'2'*16,confirmation_digest=preview['confirmation_digest'])
    response=await post(env.owner,'/merge-plans',command,'fixed-merge-plan')
    assert response.status_code==200,response.text
    env.plan=response.json()
    env.apply=dict(schema_version=1,application_id='wa-'+'3'*16,plan_ref=ref(env.plan,'plan_id'),approval_digest=env.plan['approval_digest'])
    return env.plan


@pytest.mark.skipif(os.name!='nt',reason='Exclusive Windows write handles are the supported application backend; preview is cross-platform')
async def test_actual_approved_writeback_and_original_key_do_not_replay(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);before=env.service.projects.store.log_path.read_bytes()
    preview=await post(env.owner,'/merge-previews',env.merge,'unused')
    assert preview.status_code==200 and preview.json()['status']=='mergeable',preview.text
    assert env.service.projects.store.log_path.read_bytes()==before
    assert (env.target/'result.txt').read_bytes()==env.original
    await plan(env);calls=env.adapter.n
    response=await post(env.owner,'/writeback-applications',env.apply,'apply-once')
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='completed' and result['complete'] is True
    assert (env.target/'result.txt').read_bytes()==b'done'
    assert (env.source/'unchanged.txt').read_bytes()==env.original and env.adapter.n==calls
    before=env.service.projects.store.log_path.read_bytes()
    response=await post(env.owner,'/writeback-applications',env.apply,'apply-once')
    assert response.status_code==200 and response.json()==result,response.text
    assert env.service.projects.store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_conflict_preview_has_no_writes_and_cannot_freeze_an_applicable_plan(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);(env.target/'result.txt').write_bytes(b'User edited the same line\r\n')
    before=env.service.projects.store.log_path.read_bytes()
    response=await post(env.owner,'/merge-previews',env.merge,'no-command')
    assert response.status_code==200,response.text
    preview=response.json();assert preview['status']=='conflict' and preview['files'][0]['conflicts']
    assert (env.target/'result.txt').read_bytes()==b'User edited the same line\r\n'
    assert env.service.projects.store.log_path.read_bytes()==before
    response=await post(env.owner,'/merge-plans',dict(env.merge,plan_id='mp-'+'2'*16,confirmation_digest=preview['confirmation_digest']),'cannot-save-conflict')
    assert response.status_code==409,response.text
    assert env.service.projects.store.log_path.read_bytes()==before


@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive handle backend')
async def test_changed_target_after_approval_is_rejected_before_intent_and_writes(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);await plan(env)
    (env.target/'result.txt').write_bytes(b'New uncommitted user edit\r\n');before=env.service.projects.store.log_path.read_bytes()
    response=await post(env.owner,'/writeback-applications',env.apply,'stale-approval')
    assert response.status_code==409,response.text
    assert (env.target/'result.txt').read_bytes()==b'New uncommitted user edit\r\n'
    assert env.service.projects.store.log_path.read_bytes()==before


@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive handle backend')
async def test_revoked_write_scope_does_not_reuse_read_source_authority(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);await plan(env)
    response=await post(env.owner,'/writeback-grants/'+env.write_grant['grant_id']+'/revoke',dict(schema_version=1,expected_revision=1),'revoke-write-scope')
    assert response.status_code==200,response.text
    before=env.service.projects.store.log_path.read_bytes()
    response=await post(env.owner,'/writeback-applications',env.apply,'revoked-write')
    assert response.status_code==403,response.text
    assert (env.target/'result.txt').read_bytes()==env.original
    assert env.service.projects.store.log_path.read_bytes()==before
    source=await env.owner.get(BASE+'/delivery-packages/'+env.package['package_id'])
    assert source.status_code==200,'Revoking a new write grant must not revoke original read evidence'


@pytest.mark.parametrize('name',['../outside.txt','.git/config','file.txt:stream','CON','nested/../outside.txt'])
async def test_target_paths_cannot_escape_or_modify_sensitive_metadata(delivery_team,tmp_path,name):
    env=delivery_team;root=tmp_path/'target';root.mkdir()
    body=dict(schema_version=1,grant_id='wg-'+'1'*16,project_revision=1,root_path=str(root.resolve()),
        recipient_username='delivery-owner',paths=[name],actions=['replace'],expires_at='2099-01-01T00:00:00Z')
    before=env.service.projects.store.log_path.read_bytes()
    response=await post(env.owner,'/writeback-grants',body,'bad-path')
    assert response.status_code in (403,422),response.text
    assert list(root.iterdir())==[] and env.service.projects.store.log_path.read_bytes()==before


@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive handle backend')
async def test_partial_application_is_reported_and_never_replayed(delivery_team,tmp_path,monkeypatch):
    import pyharness.application.writeback_io as io
    env=await prepared(delivery_team,tmp_path,('first.txt','second.txt'));await plan(env)
    original=io.apply_one;touched=[]
    def fail_second(*args,**kwargs):
        touched.append(True)
        if len(touched)==2:raise OSError('synthetic second-file failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(io,'apply_one',fail_second)
    response=await post(env.owner,'/writeback-applications',env.apply,'partial-once')
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='partial' and result['complete'] is False
    assert (env.target/'first.txt').read_bytes()==b'done' and (env.target/'second.txt').read_bytes()==env.original
    response=await post(env.owner,'/writeback-applications',env.apply,'partial-once')
    assert response.status_code==200 and response.json()==result and len(touched)==2,response.text


async def test_merge_export_needs_its_own_explicit_authorization(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);await plan(env)
    url=BASE+'/merge-plan-exports/we-'+'4'*16+'/files/0'
    assert (await env.owner.get(url)).status_code==403
    response=await post(env.owner,'/merge-plan-exports',dict(schema_version=1,export_id='we-'+'4'*16,plan_ref=ref(env.plan,'plan_id')),'explicit-merge-export')
    assert response.status_code==200,response.text
    response=await env.owner.get(url)
    assert response.status_code==200 and response.content==b'done' and response.headers['cache-control']=='no-store'
    assert (await env.stranger.get(url)).status_code==403
    assert (env.target/'result.txt').read_bytes()==env.original
