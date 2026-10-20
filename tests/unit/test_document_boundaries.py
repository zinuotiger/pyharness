"""S05 exact revisions, ownership, loss/withdrawal, CAS and unknown commits."""
import asyncio
import hashlib
import pytest
from tests.unit.test_source_grants import grant_app, PID
from tests.unit.test_documents import DOC, DOCS, CITES, DELIVERIES, document
from tests.unit.test_product_tasks import ref, post, body
from tests.unit.test_context_manifest_permissions import context_team, join
from tests.unit.test_context_manifests import save, MANIFESTS, manifest_command
from tests.unit.test_workspace_owned_inputs import INPUTS, command as input_command


async def citation(client, doc, **changes):
    block = doc['blocks'][0]
    command = {'schema_version': 1, 'citation_id': 'cit-0123456789abcdef', 'expected_revision': 0,
               'project_revision': 1, 'locator': {'document_ref': ref(doc, 'document_id'),
               'block_id': block['block_id'], 'start': block['start'], 'end': block['end'],
               'quote_digest': block['quote_digest']}, **changes}
    result = await client.post(CITES, json=command, headers={'Idempotency-Key': 'citation'})
    assert result.status_code == 200, result.text
    return result.json(), command


async def test_document_cas_history_and_citation_never_jump_version(grant_app):
    _, client, source = grant_app
    doc, command = await document(client, source)
    cite, _ = await citation(client, doc)
    second = {**command, 'expected_revision': 1, 'title': 'Second revision'}
    result = await client.post(DOCS, json=second, headers={'Idempotency-Key': 'revise'})
    assert result.status_code == 200, result.text
    assert result.json()['revision'] == 2
    old = (await client.get(f'{DOCS}/{DOC}/revisions/1')).json()
    assert old['status'] == 'stale' and old['title'] == command['title']
    located = (await client.get(CITES + '/' + cite['citation_id'])).json()
    assert located['status'] == 'stale' and located['locator']['document_ref'] == ref(doc, 'document_id')
    assert located['quote'] == doc['blocks'][0]['text']
    assert (await client.post(DOCS, json=command, headers={'Idempotency-Key': 'stale'})).status_code == 409
    assert (await client.post(DOCS, json=command, headers={'Idempotency-Key': 'document'})).json()['revision'] == 1


async def test_document_retirement_hides_all_old_text_without_revival(grant_app):
    _, client, source = grant_app
    doc, command = await document(client, source); cite, _ = await citation(client, doc)
    retired = {**command, 'expected_revision': 1, 'retired': True}
    result = await client.post(DOCS, json=retired, headers={'Idempotency-Key': 'retire'})
    assert result.status_code == 200 and result.json()['status'] == 'missing'
    assert (await client.get(f'{DOCS}/{DOC}/revisions/1')).json()['blocks'] == []
    assert (await client.get(CITES + '/' + cite['citation_id'])).json()['quote'] is None
    assert (await client.post(DOCS, json={**command, 'expected_revision': 2}, headers={'Idempotency-Key': 'revive'})).status_code == 403


async def test_delivery_definition_binds_formal_task_citation_and_never_accepts_itself(grant_app):
    app, client, source = grant_app
    doc, _ = await document(client, source); cite, _ = await citation(client, doc)
    task = (await post(client, body())).json()
    command = {'schema_version': 1, 'delivery_id': 'del-0123456789abcdef', 'expected_revision': 0,
        'project_revision': 1, 'task_ref': ref(task, 'product_task_id'), 'retired': False,
        'items': [{'item_id': 'report', 'title': 'Review report', 'required': True,
                   'acceptance_criteria': ['Explain the cited input'], 'citation_refs': [ref(cite, 'citation_id')]}]}
    result = await client.post(DELIVERIES, json=command, headers={'Idempotency-Key': 'delivery'})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['status'] == 'defined' and value['acceptance_status'] == 'not_evaluated'
    assert value['execution_authorized'] is False and value['export_authorized'] is False
    assert (await client.get(DELIVERIES + '/del-0123456789abcdef/revisions/1')).json() == value
    assert (await client.post(DELIVERIES, json=command, headers={'Idempotency-Key': 'delivery'})).json() == value
    assert (await client.post(DELIVERIES, json={**command, 'accepted': True}, headers={'Idempotency-Key': 'forge'})).status_code == 422
    assert (await post(client, body(expected_revision=1, title='Changed task'), 'change-task')).status_code == 200
    assert (await client.get(DELIVERIES + '/del-0123456789abcdef/revisions/1')).json()['status'] == 'stale'
    assert not app.service._engines and not app.service._queues


async def test_unknown_document_commit_reopens_and_recovers_original_key(grant_app, monkeypatch):
    from pyharness.application.workspace_store import WorkspaceStore
    from pyharness.errors import raise_code
    app, client, source = grant_app
    _, command = await document(client, source)
    command = {**command, 'expected_revision': 1, 'title': 'Unknown acknowledged revision'}
    store = app.service.projects.store; original = store._verify_commit
    def lost_ack(disk, event):
        original(disk, event); raise_code('PERS-202', reason='synthetic_ack_lost')
    monkeypatch.setattr(store, '_verify_commit', lost_ack)
    assert (await client.post(DOCS, json=command, headers={'Idempotency-Key': 'unknown'})).status_code == 503
    log = store.log_path.read_bytes(); await store.close()
    app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    result = await client.post(DOCS, json=command, headers={'Idempotency-Key': 'unknown'})
    assert result.status_code == 200, result.text
    assert result.json()['revision'] == 2 and app.service.projects.store.log_path.read_bytes() == log


async def test_corrupt_private_content_fails_closed_and_is_preserved(grant_app):
    app, client, source = grant_app
    doc, _ = await document(client, source)
    path = app.service.projects.store._snapshot_content(doc['content_digest'])
    path.write_bytes(b'synthetic corrupt original content'); damaged = path.read_bytes()
    assert (await client.get(f'{DOCS}/{DOC}/revisions/1')).status_code == 503
    assert path.read_bytes() == damaged


async def test_two_document_revisions_serialize_cas(grant_app):
    app, client, source = grant_app
    _, command = await document(client, source)
    results = await asyncio.gather(*[client.post(DOCS, json={**command, 'expected_revision': 1, 'title': str(i)},
        headers={'Idempotency-Key': f'revise-{i}'}) for i in range(2)])
    assert sorted(r.status_code for r in results) == [200, 409]


async def test_same_team_other_subject_cannot_read_or_take_over(context_team):
    app, owner, member, accounts, snapshot = context_team
    cm = (await save(owner, MANIFESTS, manifest_command(snapshot), 'manifest')).json()
    wi = (await owner.post(INPUTS, json=input_command(cm, schema_version=2), headers={'Idempotency-Key': 'prepare'})).json()
    command = {'schema_version': 1, 'document_id': DOC, 'expected_revision': 0, 'project_revision': 1,
               'title': 'Owner private document', 'source': {'kind': 'workspace_input', 'input_ref': ref(wi, 'input_id'), 'file_index': 0}}
    result = await owner.post(DOCS, json=command, headers={'Idempotency-Key': 'owner-document'})
    assert result.status_code == 200, result.text
    assert (await member.get(f'{DOCS}/{DOC}/revisions/1')).status_code == 403
    await join(member, accounts)
    assert (await member.get(DOCS)).json()['total'] == 0
    assert (await member.get(f'{DOCS}/{DOC}/revisions/1')).status_code == 403
    assert (await member.post(DOCS, json={**command, 'expected_revision': 1}, headers={'Idempotency-Key': 'take-over'})).status_code == 403
    assert (await member.post(DOCS, json={**command, 'document_id': 'doc-1111111111111111'}, headers={'Idempotency-Key': 'steal-source'})).status_code == 403
    store = app._service_registry.get('context-team').projects.store
    assert DOC in store._grant_state()[0]
    assert DOC not in app.service.projects.store._grant_state()[0]


def test_unicode_bom_crlf_paragraph_offsets_are_exact():
    from pyharness.application.document_text import parse_text
    raw = '\ufeff第一行😀\r\n第二行\r\n\r\n  第二段\r\n'.encode()
    result = parse_text(raw); text = raw.decode('utf-8-sig')
    assert result['page_count'] == 0 and len(result['blocks']) == 2
    for block in result['blocks']:
        assert text[block.start:block.end] == block.text
        assert block.quote_digest == hashlib.sha256(block.text.encode()).hexdigest()
    assert result['blocks'][0].text == '第一行😀\r\n第二行'
