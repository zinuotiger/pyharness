"""GIVEN real control logs WHEN paging THEN fixed-watermark facts are bounded."""
import pytest
import base64
import json
import time
import hashlib
from pydantic import ValidationError
from pyharness.application.workspace_audit import _sign
from pyharness.errors import PyHError
from pyharness.application.workspace_store import WorkspaceStore
from pyharness.application.workspace_contracts import ProjectRevision


async def seed(store, count=4):
    for rev in range(1, count + 1):
        await store.commit(ProjectRevision(project_id='p-0123456789abcdef', revision=rev, name='synthetic'),
                           expected_revision=rev-1, command_id=f'rev-{rev}', actor_ref='operator')


async def test_cursor_does_not_expose_dictionary_checkable_identity_hashes(tmp_path):
    store = WorkspaceStore(tmp_path, tenant_id='private-tenant')
    try:
        await seed(store, 3)
        first = await store.audit_page(limit=2, subject='private-operator')
        body = first['next_cursor'].split('.')[0]
        decoded = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
        claims = json.loads(decoded)
        for field, value in [('tenant','private-tenant'),('subject','private-operator')]:
            assert value.encode() not in decoded
            assert claims[field] != hashlib.sha256(json.dumps(value).encode()).hexdigest()
    finally:
        await store.close()


async def test_fixed_watermark_pages_survive_writes_and_restart(tmp_path):
    store = WorkspaceStore(tmp_path / 'control-fixture', tenant_id='default')
    try:
        await seed(store)
        first = await store.audit_page(limit=2, subject='operator')
        assert first['schema_version'] == 1
        assert first['total'] == 5 and first['page_count'] == 2
        assert first['has_more'] and first['next_cursor']
        assert [e['seq'] for e in first['events']] == [1, 2]
        assert set(first['events'][1]) == {'seq','type','ts','resource_kind','resource_id','revision','digest'}
        await store.commit(ProjectRevision(project_id='p-0123456789abcdef', revision=5, name='later'),
                           expected_revision=4, command_id='later', actor_ref='operator')
        await store.close()
        store = WorkspaceStore(tmp_path / 'control-fixture', tenant_id='default')
        second = await store.audit_page(limit=2, cursor=first['next_cursor'], subject='operator')
        last = await store.audit_page(limit=2, cursor=second['next_cursor'], subject='operator')
        assert second['total'] == last['total'] == 5
        assert second['source_watermark'] == last['source_watermark'] == first['source_watermark']
        assert [e['seq'] for e in second['events'] + last['events']] == [3, 4, 5]
        assert last['page_count'] == 1 and not last['has_more'] and last['next_cursor'] is None
        assert (await store.audit_page(limit=200, subject='operator'))['total'] == 6
    finally:
        await store.close()


@pytest.mark.parametrize('change', [
    {'v': 2}, {'v': True}, {'watermark': True}, {'after': True}, {'issued': True},
    {'watermark': 999}, {'after': 0}, {'after': 4}, {'after': 1.5},
    {'issued': 0}, {'issued': 'future_at_request'}, {'unknown': 'field'},
    {'digest': '0' * 64}, {'subject': '0' * 64}, {'tenant': '0' * 64},
    {'project_id': 'p-0000000000000000'}, {'limit': 1},
])
async def test_signed_invalid_claims_fail_closed_without_log_changes(tmp_path, change):
    store = WorkspaceStore(tmp_path, tenant_id='default')
    try:
        await seed(store, 3)
        first = await store.audit_page(limit=2, subject='operator')
        body = first['next_cursor'].split('.')[0]
        claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        # Collection may precede this negative test by more than an hour. Keep
        # the injected claim in the future at the actual validation boundary.
        if change.get('issued')=='future_at_request':
            change={**change,'issued':int(time.time())+3600}
            assert change['issued']>int(time.time())
        claims.update(change)
        forged = _sign(claims, (tmp_path / '.audit-cursor-key').read_bytes())
        before = store.log_path.read_bytes()
        with pytest.raises(PyHError, match='EVT-100'):
            await store.audit_page(limit=2, subject='operator', cursor=forged)
        assert store.log_path.read_bytes() == before
    finally:
        await store.close()


async def test_project_filter_empty_and_limit_boundaries(tmp_path):
    store = WorkspaceStore(tmp_path, tenant_id='default')
    try:
        await seed(store, 3)
        for limit in (1, 200):
            page = await store.audit_page(limit=limit, subject='operator', project_id='p-0123456789abcdef')
            assert page['total'] == 3 and page['page_count'] == min(limit, 3)
            assert page['events'][0]['seq'] == 2
        empty = await store.audit_page(subject='operator', project_id='p-0000000000000000')
        assert empty['events'] == [] and empty['total'] == empty['page_count'] == 0
        assert not empty['has_more'] and empty['next_cursor'] is None
        before = store.log_path.read_bytes()
        for limit in (0, 201, True, '2', 1.5):
            with pytest.raises(ValidationError):
                await store.audit_page(limit=limit, subject='operator')
        assert store.log_path.read_bytes() == before
    finally:
        await store.close()


@pytest.mark.parametrize('variant', ['subject','tenant','filter','limit','tamper','oversize','malformed','lost-key','bad-key'])
async def test_cursor_identity_integrity_and_key_failure(tmp_path, variant):
    store = WorkspaceStore(tmp_path / 'alpha', tenant_id='alpha')
    other = WorkspaceStore(tmp_path / 'beta', tenant_id='beta')
    try:
        await seed(store, 3)
        first = await store.audit_page(limit=2, subject='operator')
        cursor = first['next_cursor']
        target = store
        params = {'limit':2, 'subject':'operator', 'cursor':cursor}
        if variant == 'subject': params['subject'] = 'another'
        if variant == 'filter': params['project_id'] = 'p-0123456789abcdef'
        if variant == 'limit': params['limit'] = 1
        if variant == 'tamper': params['cursor'] = cursor[:-1] + ('A' if cursor[-1] != 'A' else 'B')
        if variant == 'malformed': params['cursor'] = 'not.a.cursor'
        if variant == 'oversize': params['cursor'] = 'x' * 2049
        if variant == 'lost-key': (store.root / '.audit-cursor-key').unlink()
        if variant == 'bad-key': (store.root / '.audit-cursor-key').write_bytes(b'bad')
        if variant == 'tenant':
            await seed(other, 3)
            await other.audit_page(subject='operator')
            # Even the same signing key cannot make tenant binding transferable.
            (other.root / '.audit-cursor-key').write_bytes((store.root / '.audit-cursor-key').read_bytes())
            target = other
        before = store.log_path.read_bytes()
        with pytest.raises((PyHError, ValidationError)):
            await target.audit_page(**params)
        assert store.log_path.read_bytes() == before
    finally:
        await store.close()
        await other.close()


@pytest.mark.parametrize('corruption', ['blob','truncated','invalid-line','watermark'])
async def test_page_rechecks_disk_not_live_cache_and_does_not_repair(tmp_path, corruption):
    store = WorkspaceStore(tmp_path, tenant_id='default')
    try:
        await seed(store, 3)
        first = await store.audit_page(limit=2, subject='operator')
        target = store.log_path
        if corruption == 'blob':
            row = (await store.projects())[0]
            target = tmp_path / 'blobs' / (row['digest'] + '.json')
            target.write_bytes(b'bad')
        elif corruption == 'watermark':
            rows = [json.loads(line) for line in target.read_text().splitlines()]
            rows[0]['payload']['title'] = 'changed-prefix'
            target.write_text(''.join(json.dumps(row) + '\n' for row in rows),encoding='utf8')
        else:
            with target.open('ab') as stream:
                stream.write(b'{torn' if corruption == 'truncated' else b'{bad-json}\n')
        before = target.read_bytes()
        with pytest.raises(PyHError) as failure:
            await store.audit_page(limit=2, subject='operator', cursor=first['next_cursor'])
        assert failure.value.code == ('EVT-100' if corruption == 'watermark' else 'PERS-202')
        assert target.read_bytes() == before
    finally:
        await store.close()
