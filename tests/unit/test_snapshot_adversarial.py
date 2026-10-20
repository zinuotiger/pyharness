"""GIVEN synthetic sources WHEN adversarial transitions occur THEN no authority leaks."""
import asyncio
import hashlib
import os
from pathlib import Path
import httpx
import pytest
from tests.unit.test_source_grants import grant_app, issue, PID, GID, URL
from tests.unit.test_source_snapshot import PREVIEW, SNAPS, BODY, SID, command_for


@pytest.mark.parametrize('operation', ['preview', 'capture', 'list'])
async def test_role_revocation_while_waiting_for_owner_lock(grant_app, operation):
    from pyharness.application.accounts import Accounts
    app, operator, source = grant_app
    accounts = Accounts(app.service._storage_root())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1') as owner:
        assert (await operator.post('/api/account/users', json={'username': 'snapshot-owner', 'password': 'synthetic-only-password', 'tenant': 'snap-team'})).status_code == 201
        assert (await owner.post('/api/account/login', json={'username': 'snapshot-owner', 'password': 'synthetic-only-password'})).status_code == 200
        assert (await owner.post('/api/v1/projects', json={'project_id': PID, 'expected_revision': 0, 'name': 'synthetic'}, headers={'Idempotency-Key': 'project'})).status_code == 200
        assert (await owner.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})).status_code == 200
        command = await command_for(owner)
        assert (await operator.post('/api/account/users', json={'username': 'snapshot-other', 'password': 'synthetic-only-password', 'tenant': 'other-team'})).status_code == 201
        accounts.membership('snap-team', 'snapshot-other', 'owner')
        service = app._service_registry.get('snap-team')
        store = service.projects.store
        await store._lock.acquire()
        try:
            if operation == 'list': task = asyncio.create_task(owner.get(SNAPS))
            else: task = asyncio.create_task(owner.post(PREVIEW if operation == 'preview' else SNAPS,
                json=BODY if operation == 'preview' else command, headers={'Idempotency-Key': 'capture'}))
            # Let the actual HTTP/auth chain reach the held owner lock.
            for _ in range(50):
                if store._lock._waiters: break
                await asyncio.sleep(0.01)
            assert store._lock._waiters
            accounts.membership('snap-team', 'snapshot-owner', 'remove')
        finally: store._lock.release()
        response = await task
        assert response.status_code in (401, 403)
    for service in app._service_registry.all(): await service.shutdown()


@pytest.mark.parametrize('condition', ['revoked', 'expired', 'archived', 'stale', 'cross-project', 'unknown'])
async def test_grant_admission_before_content(grant_app, monkeypatch, condition):
    if condition == 'expired':
        from datetime import datetime, timezone
        class Future(datetime):
            @classmethod
            def now(cls, tz=None): return datetime(2100,1,1,tzinfo=timezone.utc)
        app,c,source=grant_app
        await c.post(URL,json=issue(source),headers={'Idempotency-Key':'grant'})
        monkeypatch.setattr('pyharness.application.snapshot_store.datetime',Future)
        assert (await c.post(PREVIEW,json=BODY)).status_code==403
        return
    from tests.unit import test_source_preflight as contract
    monkeypatch.setattr(contract, 'PREFLIGHT', PREVIEW)
    monkeypatch.setattr(contract, 'BODY', BODY)
    def forbidden(*a): raise AssertionError('unavailable grant read source')
    monkeypatch.setattr('pyharness.application.snapshot_store.collect_selection', forbidden)
    await contract.test_unavailable_grants_are_denied_without_metadata(grant_app, monkeypatch, condition)


@pytest.mark.parametrize('corruption', ['torn', 'invalid', 'blob', 'missing-blob', 'disk-cache'])
async def test_disk_integrity(grant_app, monkeypatch, corruption):
    from tests.unit import test_source_preflight as contract
    monkeypatch.setattr(contract, 'PREFLIGHT', PREVIEW); monkeypatch.setattr(contract, 'BODY', BODY)
    await contract.test_preflight_corrupt_disk_fails_closed_without_repair(grant_app, corruption)


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind,ancestor', [('junction', False), ('junction', True), ('symlink', False)])
async def test_real_root_links(grant_app, monkeypatch, kind, ancestor):
    from tests.unit import test_source_preflight as contract
    monkeypatch.setattr(contract, 'PREFLIGHT', PREVIEW); monkeypatch.setattr(contract, 'BODY', BODY)
    await contract.test_real_windows_link_replacement_is_denied(grant_app, kind, ancestor)


@pytest.mark.parametrize('paths', [['../outside'], ['.env'], ['unchanged.txt','UNCHANGED.TXT'],
    ['unchanged.txt','unchanged.txt'], ['missing'], ['.'], ['CON.txt'], ['x:ads']])
async def test_selection_rejected_all_or_nothing(grant_app, paths):
    app, client, source = grant_app
    await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})
    before = app.service.projects.store.log_path.read_bytes()
    assert (await client.post(PREVIEW, json={**BODY, 'paths': paths})).status_code == 403
    assert app.service.projects.store.log_path.read_bytes() == before


@pytest.mark.parametrize('change', [{'paths': []}, {'paths': ['x']*201}, {'expected_revision': True},
    {'schema_version': True}, {'root_identity': 'forged'}, {'tenant': 'forged'}, {'paths': [1]}])
async def test_strict_preview_dto(grant_app, change):
    _, client, _ = grant_app
    assert (await client.post(PREVIEW, json={**BODY, **change})).status_code == 422


async def test_idempotency_restart_revoke_and_content_corruption(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.application.source_grants import RevokeGrant
    from pyharness.errors import PyHError
    app, c, source = grant_app
    await c.post(URL, json=issue(source), headers={'Idempotency-Key': 'grant'})
    command = await command_for(c)
    responses = await asyncio.gather(*[c.post(SNAPS, json=command, headers={'Idempotency-Key': 'capture'}) for _ in range(2)])
    assert [r.status_code for r in responses] == [200,200]
    assert responses[0].json() == responses[1].json()
    assert (await c.get(SNAPS)).json()['total'] == 1
    conflict = await c.post(SNAPS, json={**command,'snapshot_id':'ss-1111111111111111'},headers={'Idempotency-Key':'capture'})
    assert conflict.status_code == 409
    store = app.service.projects.store
    rival = WorkspaceStore(store.root, tenant_id='default')
    with pytest.raises(PyHError, match='PERS-202'): await rival.snapshots(PID,GID,authorize=lambda:None)
    await store.close()
    try:
        assert (await rival.snapshots(PID,GID,authorize=lambda:None))['snapshots'] == [responses[0].json()]
        await rival.revoke_source_grant(GID,RevokeGrant(expected_revision=1),command_id='revoke',actor_ref='operator')
        with pytest.raises(PyHError,match='GRD-403'): await rival.snapshots(PID,GID,authorize=lambda:None)
        content = next((store.root/'snapshot-content').glob('*.bin'))
        content.write_bytes(b'corrupt synthetic')
        before = rival.log_path.read_bytes()
        with pytest.raises(PyHError,match='PERS-202'): await rival.snapshots(PID,GID,authorize=lambda:None)
        assert content.read_bytes() == b'corrupt synthetic' and rival.log_path.read_bytes() == before
    finally: await rival.close()


async def test_confirmation_change_rejected_and_source_never_written_or_scanned(grant_app,monkeypatch):
    app,c,source=grant_app
    (source/'bom.txt').write_bytes(b'\xef\xbb\xbfhello\r\n')
    await c.post(URL,json=issue(source),headers={'Idempotency-Key':'grant'})
    command=await command_for(c)
    (source/'unchanged.txt').write_bytes(b'externally changed')
    assert (await c.post(SNAPS,json=command,headers={'Idempotency-Key':'capture'})).status_code==409
    original_scan,original_open=os.scandir,os.open
    original_path_open=Path.open
    def scanned(path):
        assert not Path(path).is_relative_to(source), 'source enumeration'
        return original_scan(path)
    def opened(path,flags,*args,**kw):
        if Path(path).is_relative_to(source): assert not flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC)
        return original_open(path,flags,*args,**kw)
    def path_opened(path,mode='r',*args,**kw):
        if path.is_relative_to(source): assert not any(c in mode for c in 'wa+x')
        return original_path_open(path,mode,*args,**kw)
    before={p.name:p.read_bytes() for p in source.iterdir()}
    with monkeypatch.context() as m:
        m.setattr(os,'scandir',scanned);m.setattr(os,'open',opened);m.setattr(Path,'open',path_opened)
        body={**BODY,'paths':['bom.txt']}
        preview=(await c.post(PREVIEW,json=body)).json()
        result=await c.post(SNAPS,json={**command,**body,'confirmation_digest':preview['confirmation_digest']},headers={'Idempotency-Key':'bom'})
        assert result.status_code==200,result.text
    assert before=={p.name:p.read_bytes() for p in source.iterdir()}
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('stage',['read','final','write','append'])
async def test_faults_do_not_create_snapshot(grant_app,monkeypatch,stage):
    app,c,source=grant_app
    await c.post(URL,json=issue(source),headers={'Idempotency-Key':'grant'})
    command=await command_for(c)
    store=app.service.projects.store
    before=store.log_path.read_bytes()
    if stage=='append':
        async def append(type_,payload,*,actor,sync):raise OSError('synthetic disk failure')
        monkeypatch.setattr(store._session,'append',append)
        response=await c.post(SNAPS,json=command,headers={'Idempotency-Key':'capture'})
        assert response.status_code==503
        assert store._failed
    else:
        if stage=='write':
            def write(*args):raise OSError('synthetic disk full')
            monkeypatch.setattr('pyharness.core.tenant_settings.TenantSettingsStore._atomic_write',write)
        else:
            from pyharness.application import source_snapshot as capture
            original=capture.read_selected
            calls=0
            def read(root,name,**kwargs):
                nonlocal calls
                calls+=1
                if (stage=='read' and calls==1) or (stage=='final' and calls==3):
                    (source/'unchanged.txt').write_bytes(b'external change')
                return original(root,name,**kwargs)
            monkeypatch.setattr(capture,'read_selected',read)
        response=await c.post(SNAPS,json=command,headers={'Idempotency-Key':'capture'})
        assert response.status_code in (409,503),response.text
    assert store.log_path.read_bytes()==before
