"""S05 real API->control log->private bytes->exact reader/citation consumers."""
import hashlib
import pytest
from tests.unit.test_source_grants import grant_app, PID, GID
from tests.unit.test_product_tasks import prepared, ref, post, body

DOC = 'doc-0123456789abcdef'
DOCS = f'/api/v1/projects/{PID}/documents'
CITES = f'/api/v1/projects/{PID}/citations'
DELIVERIES = f'/api/v1/projects/{PID}/delivery-definitions'


async def document(client, source, **changes):
    _, wi, _ = await prepared(client, source)
    command = {'schema_version': 1, 'document_id': DOC, 'expected_revision': 0,
               'project_revision': 1, 'title': 'Synthetic text', 'retired': False,
               'source': {'kind': 'workspace_input', 'input_ref': ref(wi, 'input_id'), 'file_index': 0}, **changes}
    result = await client.post(DOCS, json=command, headers={'Idempotency-Key': 'document'})
    assert result.status_code == 200, result.text
    return result.json(), command


async def test_exact_document_bytes_read_only_and_reopen(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, source = grant_app
    doc, command = await document(client, source)
    assert doc['status'] == 'current' and doc['format'] == 'text'
    assert doc['source']['input_ref'] == command['source']['input_ref']
    assert doc['offset_unit'] == 'unicode-codepoint' and doc['page_count'] == 0
    assert doc['blocks'][0]['text'] == 'synthetic selected'
    assert doc['blocks'][0]['page'] is None
    assert doc['content_digest'] == hashlib.sha256(b'\xef\xbb\xbfsynthetic selected\r\n').hexdigest()
    assert 'local-operator' not in str(doc) and str(source) not in str(doc)
    path = f'{DOCS}/{DOC}/revisions/1'
    store = app.service.projects.store; log = store.log_path.read_bytes()
    assert (await client.get(path)).json() == doc
    assert store.log_path.read_bytes() == log and not app.service._engines and not app.service._queues
    await store.close(); app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    assert (await client.get(path)).json() == doc
    assert app.service.projects.store.log_path.read_bytes() == log


async def test_citation_exact_range_digest_and_revision(grant_app):
    _, client, source = grant_app
    doc, _ = await document(client, source); block = doc['blocks'][0]
    command = {'schema_version': 1, 'citation_id': 'cit-0123456789abcdef', 'expected_revision': 0,
               'project_revision': 1, 'locator': {'document_ref': ref(doc, 'document_id'),
               'block_id': block['block_id'], 'start': block['start'], 'end': block['end'],
               'quote_digest': hashlib.sha256(block['text'].encode()).hexdigest()}}
    result = await client.post(CITES, json=command, headers={'Idempotency-Key': 'cite'})
    assert result.status_code == 200, result.text
    citation = result.json(); assert citation['quote'] == block['text'] and citation['status'] == 'current'
    assert (await client.get(CITES + '/' + command['citation_id'])).json() == citation
    command['locator']['quote_digest'] = '0' * 64
    response = await client.post(CITES, json={**command, 'citation_id': 'cit-1111111111111111'}, headers={'Idempotency-Key': 'bad-quote'})
    assert response.status_code == 409


async def test_source_withdrawal_hides_document_citation_and_delivery(grant_app):
    app, client, source = grant_app
    doc, _ = await document(client, source)
    assert (await client.post(f'/api/v1/source-grants/{GID}/revoke', json={'expected_revision': 1},
           headers={'Idempotency-Key': 'withdraw'})).status_code == 200
    log = app.service.projects.store.log_path.read_bytes()
    assert (await client.get(f'{DOCS}/{DOC}/revisions/1')).status_code == 403
    assert (await client.get(DOCS)).json()['total'] == 0
    assert app.service.projects.store.log_path.read_bytes() == log


@pytest.mark.parametrize('extra', [{'actor_ref': 'forged'}, {'tenant_id': 'forged'},
    {'blocks': []}, {'parser_version': 'forged'}, {'schema_version': True}, {'expected_revision': True}])
async def test_document_command_does_not_accept_client_authority(grant_app, extra):
    app, client, source = grant_app
    _, command = await document(client, source)
    before = app.service.projects.store.log_path.read_bytes()
    result = await client.post(DOCS, json={**command, 'document_id': 'doc-1111111111111111', **extra},
                               headers={'Idempotency-Key': 'forged'})
    assert result.status_code == 422, result.text
    assert app.service.projects.store.log_path.read_bytes() == before
