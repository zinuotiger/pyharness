"""Creation, observed deletion, admission races and lost strong acknowledgements."""
import asyncio
import os
import pytest
from tests.unit.test_delivery_handoffs import delivery_team,post
from tests.unit.test_writeback_application import prepared,plan
from tests.unit.test_product_tasks import ref
from tests.unit.test_delivery_packages import BASE

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Actual application is supported by exclusive Windows handles')


async def test_new_target_is_created_only_after_explicit_approved_plan(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);path=env.target/'result.txt';path.unlink()
    env.merge['files'][0]['input_path']=None;await plan(env)
    assert not path.exists()
    result=await post(env.owner,'/writeback-applications',env.apply,'create-once')
    assert result.status_code==200 and result.json()['status']=='completed',result.text
    assert path.read_bytes()==b'done' and result.json()['effects'][0]['action']=='create'
    assert (env.source/'unchanged.txt').read_bytes()==env.original


async def test_changed_target_during_exclusive_open_refuses_before_intent(delivery_team,tmp_path,monkeypatch):
    import pyharness.application.writeback_io as io
    env=await prepared(delivery_team,tmp_path);await plan(env);opening=io._open_stream;changed=[]
    def change_before_open(path,**kwargs):
        if not changed:
            changed.append(True);path.write_bytes(b'User changed while the preview was being checked')
        return opening(path,**kwargs)
    monkeypatch.setattr(io,'_open_stream',change_before_open);before=env.service.projects.store.log_path.read_bytes()
    result=await post(env.owner,'/writeback-applications',env.apply,'changed-at-open')
    assert result.status_code==409,result.text
    assert env.service.projects.store.log_path.read_bytes()==before
    assert (env.target/'result.txt').read_bytes()==b'User changed while the preview was being checked'


async def test_two_keys_cannot_apply_one_fixed_plan_twice(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path);await plan(env)
    results=await asyncio.gather(post(env.owner,'/writeback-applications',env.apply,'first-racing-key'),
        post(env.owner,'/writeback-applications',dict(env.apply,application_id='wa-'+'7'*16),'second-racing-key'))
    assert sorted(r.status_code for r in results)==[200,409],[r.text for r in results]
    assert (env.target/'result.txt').read_bytes()==b'done'
    resources,_,_=await env.service.projects.store._execution_read_state()
    assert sum('writeback_application' in row for row in resources.values())==1


async def test_unconfirmed_intent_never_touches_a_target(delivery_team,tmp_path,monkeypatch):
    env=await prepared(delivery_team,tmp_path);await plan(env);store=env.service.projects.store;verify=store._verify_commit
    def unconfirmed(storage,event):
        if event.payload.get('resource_kind')=='writeback_application' and event.payload['revision']==1:
            raise OSError('synthetic acknowledgement failure after intent commit')
        return verify(storage,event)
    monkeypatch.setattr(store,'_verify_commit',unconfirmed)
    result=await post(env.owner,'/writeback-applications',env.apply,'unconfirmed-intent')
    assert result.status_code==503,result.text
    assert store._failed and (env.target/'result.txt').read_bytes()==env.original


async def test_incomplete_file_bytes_are_unknown_and_are_not_automatically_rolled_back(delivery_team,tmp_path,monkeypatch):
    import pyharness.application.writeback_io as io
    env=await prepared(delivery_team,tmp_path);await plan(env);calls=[]
    def partial(locked,item,raw):
        calls.append(True);stream=locked.streams[item['target_path']]
        stream.seek(0);stream.write(b'partial bytes');stream.truncate();stream.flush();os.fsync(stream.fileno())
        raise OSError('synthetic provider failure after writing partial bytes')
    monkeypatch.setattr(io,'apply_one',partial)
    result=await post(env.owner,'/writeback-applications',env.apply,'partial-byte-write')
    assert result.status_code==200 and result.json()['status']=='unknown' and result.json()['complete'] is False,result.text
    assert result.json()['effects'][0]['status']=='unknown' and (env.target/'result.txt').read_bytes()==b'partial bytes'
    result=await post(env.owner,'/writeback-applications',env.apply,'partial-byte-write')
    assert result.status_code==200 and result.json()['status']=='unknown' and len(calls)==1
    assert (env.target/'result.txt').read_bytes()==b'partial bytes'
