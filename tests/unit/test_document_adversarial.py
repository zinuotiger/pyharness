"""Negative source/version/permission and actual owned-parser retirement paths."""
import asyncio
from datetime import datetime, timezone
import sys
import pytest
from tests.unit.test_source_grants import grant_app, PID
from tests.unit.test_documents import document, DOCS, DOC, CITES, DELIVERIES
from tests.unit.test_document_boundaries import citation
from tests.unit.test_context_manifest_permissions import context_team, join
from tests.unit.test_context_manifests import save, MANIFESTS, manifest_command
from tests.unit.test_workspace_owned_inputs import INPUTS, command as input_command
from tests.unit.test_product_tasks import ref


@pytest.mark.parametrize('field,value', [('start', -1), ('end', 999), ('block_id', 'b-9999'), ('start', True)])
async def test_citation_wrong_ranges_or_blocks_do_not_commit(grant_app, field, value):
    app, client, source = grant_app
    doc, _ = await document(client, source)
    _, body = await citation(client, doc)
    before = app.service.projects.store.log_path.read_bytes()
    body['citation_id'] = 'cit-1111111111111111'; body['locator'][field] = value
    result = await client.post(CITES, json=body, headers={'Idempotency-Key': 'bad-locator'})
    assert result.status_code in (409, 422), result.text
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_parser_change_and_source_missing_invalidate_citations(grant_app, monkeypatch):
    from pyharness.application import document_text
    _, client, source = grant_app
    doc, _ = await document(client, source); cite, _ = await citation(client, doc)
    monkeypatch.setattr(document_text, 'TEXT_PARSER', 'next-synthetic-parser')
    result = (await client.get(f'{CITES}/{cite["citation_id"]}')).json()
    assert result['status'] == 'stale' and 'parser_version_changed' in result['stale_reasons']
    (source / 'unchanged.txt').unlink()
    result = (await client.get(f'{CITES}/{cite["citation_id"]}')).json()
    assert result['status'] == 'missing' and result['quote'] is None


async def test_bounded_pages_are_exact_and_wrong_subject_cursor_is_rejected(grant_app):
    _, client, source = grant_app
    _, body = await document(client, source)
    for i in range(2):
        result = await client.post(DOCS, json={**body, 'document_id': 'doc-' + str(i+1)*16},
                                   headers={'Idempotency-Key': f'document-{i}'})
        assert result.status_code == 200, result.text
    first = (await client.get(DOCS + '?limit=1')).json()
    assert first['total'] == 3 and first['page_count'] == 1 and first['has_more']
    second = (await client.get(DOCS + '?limit=1&cursor=' + first['next_cursor'])).json()
    assert second['documents'][0]['document_id'] != first['documents'][0]['document_id']
    assert (await client.get(DOCS + '?limit=2&cursor=' + first['next_cursor'])).status_code == 422
    assert (await client.get(DOCS + '?limit=51')).status_code == 422
    assert (await client.get(DOCS + '?limit=1&limit=2')).status_code == 422


async def member_document(context_team):
    app, owner, member, accounts, snapshot = context_team
    await join(member, accounts)
    cm = (await save(member, MANIFESTS, manifest_command(snapshot), 'member-manifest')).json()
    result = await member.post(INPUTS, json=input_command(cm, schema_version=2), headers={'Idempotency-Key': 'member-input'})
    assert result.status_code == 200, result.text
    wi = result.json()
    body = {'schema_version': 1, 'document_id': DOC, 'expected_revision': 0, 'project_revision': 1,
            'title': 'Member document', 'source': {'kind': 'workspace_input', 'input_ref': ref(wi, 'input_id'), 'file_index': 0}}
    result = await member.post(DOCS, json=body, headers={'Idempotency-Key': 'member-document'})
    assert result.status_code == 200, result.text
    return app, member, accounts, app._service_registry.get('context-team').projects.store, body


@pytest.mark.parametrize('role', ['viewer', 'remove'])
async def test_membership_changes_reject_all_private_document_operations(context_team, role):
    app, member, accounts, store, body = await member_document(context_team)
    accounts.membership('context-team', 'ctx-member', role)
    before = store.log_path.read_bytes()
    for response in [await member.get(DOCS), await member.get(f'{DOCS}/{DOC}/revisions/1'),
                     await member.get(CITES), await member.get(DELIVERIES),
                     await member.post(DOCS, json=body, headers={'Idempotency-Key': 'member-document'})]:
        assert response.status_code in (401, 403), response.text
    assert store.log_path.read_bytes() == before


async def test_waiting_document_write_rechecks_membership(context_team):
    _, member, accounts, store, body = await member_document(context_team)
    before = store.log_path.read_bytes(); await store._lock.acquire()
    try:
        request = asyncio.create_task(member.post(DOCS, json={**body, 'expected_revision': 1}, headers={'Idempotency-Key': 'waiting-document'}))
        for _ in range(60):
            if store._lock._waiters:
                break
            await asyncio.sleep(.01)
        assert store._lock._waiters
        accounts.membership('context-team', 'ctx-member', 'remove')
    finally:
        store._lock.release()
    assert (await request).status_code in (401, 403)
    assert store.log_path.read_bytes() == before


async def test_private_io_tail_rechecks_role_and_does_not_return_old_text(context_team, monkeypatch):
    _, member, accounts, store, _ = await member_document(context_team)
    original = store._document_raw
    def changed(value, gate):
        raw = original(value, gate)
        accounts.membership('context-team', 'ctx-member', 'remove')
        return raw
    monkeypatch.setattr(store, '_document_raw', changed)
    before = store.log_path.read_bytes()
    result = await member.get(f'{DOCS}/{DOC}/revisions/1')
    assert result.status_code == 403 and 'unchanged' not in result.text
    assert store.log_path.read_bytes() == before


async def test_private_io_tail_rechecks_utc(grant_app, monkeypatch):
    from pyharness.application import snapshot_store
    app, client, source = grant_app
    await document(client, source)
    store = app.service.projects.store; original = store._document_raw
    class Expired:
        @staticmethod
        def now(tz):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    def expire(value, gate):
        raw = original(value, gate); monkeypatch.setattr(snapshot_store, 'datetime', Expired); return raw
    monkeypatch.setattr(store, '_document_raw', expire)
    result = await client.get(f'{DOCS}/{DOC}/revisions/1')
    assert result.status_code == 403 and 'synthetic selected' not in result.text


@pytest.mark.controlled_process
def test_timed_out_real_parser_carrier_is_destroyed_and_never_reused(tmp_path, monkeypatch):
    from pyharness.application import document_pdf
    from pyharness.core.process_owner import ProcessOwner
    from pyharness.errors import PyHError
    from tests.unit.test_document_pdf import pdf_bytes
    owned = []
    def factory(argv, **kwargs):
        first = not owned
        if first:
            argv = [sys.executable, '-I', '-c', 'import time; time.sleep(60)']
        owner = ProcessOwner(argv, **kwargs); owned.append(owner)
        if first:
            collect = owner.collect
            owner.collect = lambda timeout: collect(.1)
        return owner
    monkeypatch.setattr(document_pdf, 'ProcessOwner', factory)
    with pytest.raises(PyHError) as error:
        document_pdf._extract(tmp_path/'private-parser', pdf_bytes())
    assert error.value.code == 'EVT-104' and owned[0]._closed and owned[0].proc.poll() is not None
    result = document_pdf._extract(tmp_path/'private-parser', pdf_bytes(('Actual fresh worker',)))
    assert result['blocks'][0].text == 'Actual fresh worker'
    assert len(owned) == 2 and owned[1] is not owned[0] and owned[1]._closed
    assert not list((tmp_path/'private-parser').iterdir())


async def test_reading_and_resolving_never_open_live_source_body(grant_app, monkeypatch):
    import os
    from pathlib import Path
    _, client, source = grant_app
    doc, _ = await document(client, source); cite, _ = await citation(client, doc)
    original = os.open
    def guarded(path, *args, **kwargs):
        assert not Path(path).resolve().is_relative_to(source.resolve()), 'live source body reopened'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', guarded)
    assert (await client.get(f'{DOCS}/{DOC}/revisions/1')).status_code == 200
    assert (await client.get(f'{CITES}/{cite["citation_id"]}')).status_code == 200
