"""Unknown outcomes, current authority, and actual cross-process exclusion."""
import json
import os
import subprocess
import sys
import hashlib
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.unit.test_delivery_handoffs import delivery_team,post,acceptance
from tests.unit.test_writeback_application import prepared,plan
from tests.unit.test_delivery_packages import BASE
from tests.unit.test_source_grants import GID


@pytest.mark.parametrize('withdraw',['source','reviewer','owner','grant_expiry','new_review'])
async def test_current_authority_is_rechecked_before_any_write(delivery_team,tmp_path,monkeypatch,withdraw):
    env=await prepared(delivery_team,tmp_path);await plan(env)
    if withdraw=='source':
        r=await env.owner.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-source'})
        assert r.status_code==200,r.text
    if withdraw=='reviewer':env.accounts.membership('delivery-team','delivery-reviewer','remove')
    if withdraw=='owner':
        env.accounts.membership('delivery-team','delivery-stranger','owner')
        env.accounts.membership('delivery-team','delivery-owner','member')
    if withdraw=='grant_expiry':
        import pyharness.application.writeback_support as support
        from datetime import datetime,timezone
        class FutureClock:
            @staticmethod
            def now(tz):return datetime(2100,1,1,tzinfo=timezone.utc)
        monkeypatch.setattr(support,'datetime',FutureClock)
    if withdraw=='new_review':
        command=dict(acceptance(env,'fail'),acceptance_id='av-'+'5'*16)
        r=await post(env.reviewer,'/delivery-acceptances',command,'later-independent-failure');assert r.status_code==200,r.text
    before=env.service.projects.store.log_path.read_bytes();calls=env.adapter.n
    response=await post(env.owner,'/writeback-applications',env.apply,'withdrawn-approval')
    assert response.status_code in (403,409),response.text
    assert (env.target/'result.txt').read_bytes()==env.original and env.adapter.n==calls
    assert env.service.projects.store.log_path.read_bytes()==before


async def test_read_grant_and_unrelated_member_cannot_create_write_authority(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);before=env.service.projects.store.log_path.read_bytes()
    invalid=dict(env.merge,grant_ref=dict(env.merge['grant_ref'],grant_id=GID))
    response=await post(env.owner,'/merge-previews',invalid,'read-is-not-write')
    assert response.status_code==422,response.text
    command=dict(schema_version=1,grant_id='wg-'+'8'*16,project_revision=1,root_path=str(env.target.resolve()),
        recipient_username='delivery-stranger',paths=['result.txt'],actions=['replace'],expires_at='2099-01-01T00:00:00Z')
    response=await post(env.stranger,'/writeback-grants',command,'member-is-not-owner')
    assert response.status_code==403,response.text
    response=await env.stranger.get(BASE+'/delivery-packages/'+env.package['package_id']+'/merge-source-options')
    assert response.status_code==403,response.text
    assert env.service.projects.store.log_path.read_bytes()==before and (env.target/'result.txt').read_bytes()==env.original


@pytest.mark.skipif(os.name!='nt',reason='Supported Windows application backend')
async def test_unknown_result_reconciles_cold_without_replaying_or_reading_current_target(delivery_team,tmp_path,monkeypatch):
    env=await prepared(delivery_team,tmp_path);await plan(env);store=env.service.projects.store
    append=store._append_context
    async def lost_result(value,kind,*args,**kwargs):
        if kind=='writeback_application' and value.revision==2:
            raise PyHError('PERS-202',ctx=dict(reason='synthetic_outcome_commit_unavailable'))
        return await append(value,kind,*args,**kwargs)
    monkeypatch.setattr(store,'_append_context',lost_result)
    response=await post(env.owner,'/writeback-applications',env.apply,'unknown-original-key')
    assert response.status_code==503,response.text
    assert (env.target/'result.txt').read_bytes()==b'done'
    (env.target/'result.txt').write_bytes(b'User inspection after unknown result')
    before={p:p.read_bytes() for p in env.service.tenant_root().rglob('*.jsonl')};calls=env.adapter.n
    response=await post(env.owner,'/writeback-applications',env.apply,'unknown-original-key')
    assert response.status_code==200 and response.json()['status']=='unknown' and response.json()['replay_allowed'] is False,response.text
    assert (env.target/'result.txt').read_bytes()==b'User inspection after unknown result'
    response=await post(env.owner,'/writeback-applications',dict(env.apply,application_id='wa-'+'6'*16),'new-key-must-not-replay-plan')
    assert response.status_code==409,response.text
    cold=DesktopApp(cli.assemble_ctx(env.app.service.ctx.settings))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1') as client:
            r=await client.post('/api/account/login',json=dict(username='delivery-owner',password='synthetic-only-password',tenant='delivery-team'));assert r.status_code==200,r.text
            r=await client.get(BASE+'/writeback-applications/'+env.apply['application_id'])
            assert r.status_code==200 and r.json()['status']=='unknown',r.text
            r=await client.get(BASE+'/writeback-submissions/by-key?key=unknown-original-key')
            assert r.status_code==200 and r.json()['status']=='committed' and r.json()['receipt']['resource_id']==env.apply['application_id'],r.text
        scoped=cold._service_registry.get('delivery-team')
        assert not scoped._engines and not scoped._queues and scoped.projects.store._store is None
    finally:
        for service in cold._service_registry.all():await service.shutdown()
    assert env.adapter.n==calls and all(p.read_bytes()==value for p,value in before.items())
    assert (env.target/'result.txt').read_bytes()==b'User inspection after unknown result'


@pytest.mark.skipif(os.name!='nt',reason='Windows mandatory sharing exclusion')
@pytest.mark.controlled_process
def test_existing_targets_reject_a_real_independent_writer_and_release_on_exit(tmp_path):
    from pyharness.application.writeback_io import LockedTargets,observe_target
    root=tmp_path/'explicit-root';root.mkdir();target=root/'result.txt';target.write_bytes(b'original')
    observed,_=observe_target(root,'result.txt',lambda:None)
    item=dict(target_path='result.txt',target=observed.model_dump(),action='replace')
    script='from pathlib import Path\nPath('+repr(str(target))+').write_bytes(b"foreign overwrite")'
    with LockedTargets(root,[item],lambda:None):
        result=subprocess.run([sys.executable,'-I','-c',script],capture_output=True,timeout=10)
        assert result.returncode!=0 and b'PermissionError' in result.stderr
    assert target.read_bytes()==b'original'
    result=subprocess.run([sys.executable,'-I','-c',script],capture_output=True,timeout=10)
    assert result.returncode==0 and target.read_bytes()==b'foreign overwrite'


async def test_hardlinked_target_cannot_be_frozen_or_written(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path)
    link=tmp_path/'outside-alias.txt';os.link(env.target/'result.txt',link)
    before=env.service.projects.store.log_path.read_bytes()
    r=await post(env.owner,'/merge-previews',env.merge,'hardlink-rejected')
    assert r.status_code==403,r.text
    assert link.read_bytes()==env.original and env.service.projects.store.log_path.read_bytes()==before
