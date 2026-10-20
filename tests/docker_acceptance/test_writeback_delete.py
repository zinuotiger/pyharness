"""Opt-in real container deletion -> independent review -> exact Windows writeback."""
import os
import pytest
from tests.unit.test_delivery_handoffs import delivery_team,post
from tests.unit.test_writeback_application import prepared,plan
from tests.unit.test_delivery_packages import BASE

DELETE=dict(script=[dict(id='actual-delete-input-copy',name='exec.python_run',args=dict(code="from pathlib import Path\nPath('unchanged.txt').unlink()"))],
    agent=dict(sandbox_profile=dict(mode='isolated',image='python:3.13.2-slim-bookworm'),permission_policy='standard'))


@pytest.mark.controlled_process
@pytest.mark.skipif(os.name!='nt',reason='Exclusive Windows writeback application backend')
@pytest.mark.parametrize('delivery_team',[DELETE],indirect=True)
async def test_actual_deletion_receipt_is_required_and_bound_to_the_original_input(delivery_team,tmp_path):
    env=await prepared(delivery_team,tmp_path)
    result=await env.owner.get(BASE+'/delivery-packages/'+env.package['package_id']+'/merge-source-options')
    assert result.status_code==200,result.text
    options=result.json();assert options['baseline_files'][0]['path']=='unchanged.txt'
    deletion=next(d for d in options['deletions'] if d['path']=='unchanged.txt')
    env.merge['files'][0].update(output_ref=None,deletion_ref=deletion)
    await plan(env);assert env.plan['files'][0]['action']=='delete'
    result=await post(env.owner,'/writeback-applications',env.apply,'apply-observed-deletion')
    assert result.status_code==200 and result.json()['status']=='completed',result.text
    assert not (env.target/'result.txt').exists() and (env.source/'unchanged.txt').read_bytes()==env.original
    manager=env.service.platform.sandboxes
    assert manager.records and all(record.status=='destroyed' for record in manager.records.values())
    backend=manager.backends['isolated']
    remaining=await backend._command(['ps','-aq','--filter','label=pyharness.owner='+backend.owner_label])
    assert remaining['exit_code']==0 and not remaining['output'].strip()
