"""Additional acceptance checks of existing frozen implementation, no new behavior."""
import hashlib
import httpx
import pytest
from tests.unit.test_source_grants import grant_app, issue, PID, GID, URL
from tests.unit.test_source_snapshot import PREVIEW,SNAPS,BODY,SID,command_for

async def test_two_tenants_same_snapshot_ids_hold_distinct_bytes(tmp_path):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.workspace_contracts import ProjectRevision
    from pyharness.application.source_grants import IssueGrant
    from pyharness.application.source_selection import SelectionPreview
    from pyharness.application.source_snapshot import CaptureSnapshot
    stores=[WorkspaceStore(tmp_path/tenant,tenant_id=tenant) for tenant in ('alpha','beta')]
    try:
        digests=[]
        for i,store in enumerate(stores):
            source=tmp_path/f'source-{i}';source.mkdir();raw=f'synthetic tenant {i}'.encode();(source/'unchanged.txt').write_bytes(raw)
            await store.commit(ProjectRevision(project_id=PID,revision=1,name='synthetic'),expected_revision=0,command_id='project',actor_ref='operator')
            await store.issue_source_grant(PID,IssueGrant(**issue(source)),command_id='grant',actor_ref='operator')
            preview=await store.preview_snapshot(PID,GID,SelectionPreview(**BODY),authorize=lambda:'operator')
            result=await store.capture_snapshot(PID,GID,CaptureSnapshot(**BODY,snapshot_id=SID,confirmation_digest=preview['confirmation_digest'],policy='reject-secrets-utf8-v1'),command_id='capture',actor_ref='operator',authorize=lambda:'operator')
            assert (store.root/'snapshot-content'/(hashlib.sha256(raw).hexdigest()+'.bin')).read_bytes()==raw
            digests.append(result['digest'])
        assert digests[0]!=digests[1]
        assert len(list((stores[0].root/'snapshot-content').iterdir()))==1
        assert len(list((stores[1].root/'snapshot-content').iterdir()))==1
    finally:
        for store in stores:await store.close()

async def test_http_role_matrix_and_tenant_binding(grant_app):
    from pyharness.application.accounts import Accounts
    app,operator,source=grant_app;accounts=Accounts(app.service._storage_root())
    transport=httpx.ASGITransport(app=app.api)
    try:
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as owner,httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as member:
            assert (await member.post(PREVIEW,json=BODY)).status_code==401
            for c,name,tenant in [(owner,'snapshot-role-owner','snapshot-team'),(member,'snapshot-role-member','other-team')]:
                assert (await operator.post('/api/account/users',json={'username':name,'password':'synthetic-only-password','tenant':tenant})).status_code==201
                assert (await c.post('/api/account/login',json={'username':name,'password':'synthetic-only-password'})).status_code==200
            assert (await owner.post('/api/v1/projects',json={'project_id':PID,'expected_revision':0,'name':'synthetic'},headers={'Idempotency-Key':'project'})).status_code==200
            assert (await owner.post(URL,json=issue(source),headers={'Idempotency-Key':'grant'})).status_code==200
            command=await command_for(owner)
            assert (await member.post(PREVIEW,json=BODY)).status_code==403
            assert (await member.post(SNAPS,json=command,headers={'Idempotency-Key':'capture'})).status_code==403
            accounts.membership('snapshot-team','snapshot-role-member','member')
            assert (await member.post('/api/account/login',json={'username':'snapshot-role-member','password':'synthetic-only-password','tenant':'snapshot-team'})).status_code==200
            assert (await member.post(PREVIEW,json=BODY)).status_code==200
            assert (await member.post(SNAPS,json=command,headers={'Idempotency-Key':'capture'})).status_code==200
            accounts.membership('snapshot-team','snapshot-role-member','viewer')
            assert (await member.post(PREVIEW,json=BODY)).status_code==403
            assert (await member.post(SNAPS,json=command,headers={'Idempotency-Key':'capture'})).status_code==403
            assert (await member.get(SNAPS)).status_code==200
            accounts.membership('snapshot-team','snapshot-role-member','remove')
            assert (await member.get(SNAPS)).status_code in (401,403)
    finally:
        for service in app._service_registry.all():await service.shutdown()

@pytest.mark.parametrize('sizes',[(10485761,), (6291456,5242880)])
async def test_content_budget_never_commits_partial_selection(grant_app,sizes):
    app,c,source=grant_app
    paths=[]
    for i,size in enumerate(sizes):
        name=f'file-{i}.txt';paths.append(name);(source/name).write_bytes(b'x'*size)
    await c.post(URL,json=issue(source),headers={'Idempotency-Key':'grant'})
    store=app.service.projects.store;before=store.log_path.read_bytes()
    response=await c.post(PREVIEW,json={**BODY,'paths':paths})
    assert response.status_code==403
    assert store.log_path.read_bytes()==before
    assert not (store.root/'snapshot-content').exists()
