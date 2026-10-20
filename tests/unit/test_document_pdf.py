"""Synthetic PDFs exercise actual bounded pypdf workers and true physical pages."""
from io import BytesIO
import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, DecodedStreamObject
from tests.unit.test_source_grants import grant_app, issue, URL, PID, GID
from tests.unit.test_documents import DOC, DOCS

PREVIEWS = f'/api/v1/projects/{PID}/document-pdf-previews'
pytestmark = pytest.mark.controlled_process


def pdf_bytes(texts=('First page', '', 'Third page'), *, encrypted=False, active=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 40 780 Td (' + text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('synthetic-only-password')
    if active:
        writer.add_js('app.alert("synthetic never executed")')
    out = BytesIO(); writer.write(out); return out.getvalue()


async def pdf_command(client, source, raw=None):
    (source / 'source.pdf').write_bytes(pdf_bytes() if raw is None else raw)
    assert (await client.post(URL, json=issue(source), headers={'Idempotency-Key': 'pdf-grant'})).status_code == 200
    preview = await client.post(PREVIEWS, json={'schema_version': 1, 'project_revision': 1,
        'grant_id': GID, 'grant_revision': 1, 'path': 'source.pdf'})
    assert preview.status_code == 200, preview.text
    value = preview.json(); assert value['persisted'] is False
    return {'schema_version': 1, 'document_id': DOC, 'expected_revision': 0, 'project_revision': 1,
            'title': 'Synthetic PDF', 'retired': False, 'source': {'kind': 'pdf_grant', 'grant_id': GID,
            'grant_revision': 1, 'path': 'source.pdf', 'confirmation_digest': value['confirmation_digest']}}


async def test_real_pdf_parser_preserves_blank_physical_page_and_private_bytes(grant_app):
    from pyharness.application.workspace_store import WorkspaceStore
    app, client, source = grant_app
    command = await pdf_command(client, source)
    result = await client.post(DOCS, json=command, headers={'Idempotency-Key': 'pdf'})
    assert result.status_code == 200, result.text
    doc = result.json()
    assert doc['format'] == 'pdf' and doc['status'] == 'current'
    assert doc['page_count'] == 3 and doc['unlocatable_pages'] == [2]
    assert [b['page'] for b in doc['blocks']] == [1, 3]
    assert [b['text'] for b in doc['blocks']] == ['First page', 'Third page']
    assert doc['parser_version'].startswith('physical-page-text-v1:pypdf-')
    store = app.service.projects.store; raw = (source / 'source.pdf').read_bytes()
    assert store._snapshot_content(doc['content_digest']).read_bytes() == raw
    assert not list((store.root / 'document-staging').iterdir())
    before = store.log_path.read_bytes()
    await store.close(); app.service.projects.store = WorkspaceStore(store.root, tenant_id='default')
    assert (await client.get(f'{DOCS}/{DOC}/revisions/1')).json() == doc
    assert app.service.projects.store.log_path.read_bytes() == before
    assert not app.service._engines and not app.service._queues


@pytest.mark.parametrize('raw,reason,count', [(pdf_bytes(('', '')), 'no_text_layer', 2),
    (pdf_bytes(encrypted=True), 'encrypted_pdf', 0), (b'%PDF-synthetic-invalid', 'invalid_pdf', 0)], ids=['scan', 'encrypted', 'invalid'])
async def test_unlocatable_pdf_never_invents_pages_or_ocr(grant_app, raw, reason, count):
    _, client, source = grant_app
    command = await pdf_command(client, source, raw)
    result = await client.post(DOCS, json=command, headers={'Idempotency-Key': 'pdf'})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['status'] == 'unlocatable' and value['capability_reason'] == reason
    assert value['page_count'] == count and value['blocks'] == []


async def test_active_pdf_is_denied_without_persistent_content(grant_app):
    app, client, source = grant_app
    command = await pdf_command(client, source, pdf_bytes(active=True))
    store = app.service.projects.store; before = store.log_path.read_bytes()
    prior = set(store.root.rglob('*.bin'))
    result = await client.post(DOCS, json=command, headers={'Idempotency-Key': 'active'})
    assert result.status_code == 403, result.text
    assert result.json()['reason'] == 'document_active_content'
    assert store.log_path.read_bytes() == before and set(store.root.rglob('*.bin')) == prior
    assert not list((store.root / 'document-staging').iterdir())


@pytest.mark.parametrize('path', ['../outside.pdf', '/outside.pdf', 'nested/../../outside.pdf', '.ssh/private.pdf', 'C:/private.pdf'])
async def test_pdf_selection_rejects_paths_before_body_read(grant_app, path):
    app, client, source = grant_app
    await pdf_command(client, source)
    before = app.service.projects.store.log_path.read_bytes()
    result = await client.post(PREVIEWS, json={'schema_version': 1, 'project_revision': 1,
        'grant_id': GID, 'grant_revision': 1, 'path': path})
    assert result.status_code in (403, 422), result.text
    assert app.service.projects.store.log_path.read_bytes() == before


async def test_pdf_confirmation_stale_or_deleted_source_and_old_exact_body(grant_app):
    _, client, source = grant_app
    command = await pdf_command(client, source)
    path = source / 'source.pdf'; path.write_bytes(pdf_bytes(('Later version',)))
    assert (await client.post(DOCS, json=command, headers={'Idempotency-Key': 'changed'})).status_code == 409
    preview = (await client.post(PREVIEWS, json={'schema_version': 1, 'project_revision': 1,
        'grant_id': GID, 'grant_revision': 1, 'path': 'source.pdf'})).json()
    command['source']['confirmation_digest'] = preview['confirmation_digest']
    doc = await client.post(DOCS, json=command, headers={'Idempotency-Key': 'fresh'})
    assert doc.status_code == 200, doc.text
    path.write_bytes(pdf_bytes(('Another version',)))
    old = (await client.get(f'{DOCS}/{DOC}/revisions/1')).json()
    assert old['status'] == 'stale' and old['blocks'][0]['text'] == 'Later version'
    path.unlink()
    old = (await client.get(f'{DOCS}/{DOC}/revisions/1')).json()
    assert old['status'] == 'missing' and old['blocks'] == []


async def test_text_snapshot_policy_still_rejects_binary_pdf(grant_app):
    from tests.unit.test_source_snapshot import SNAPS, BODY
    _, client, source = grant_app
    await pdf_command(client, source)
    result = await client.post(SNAPS.replace('/snapshots', '/snapshot-previews'), json={**BODY, 'paths': ['source.pdf']})
    assert result.status_code == 403, result.text


@pytest.mark.parametrize('page_stream_sizes', [
    [(3 * 1024**2,)],
    [(1536 * 1024, 1536 * 1024)],
    [(1900 * 1024,)] * 5,
], ids=['one-stream', 'contents-array', 'cumulative-pages'])
async def test_decoded_pdf_stream_limits_precede_extraction_and_persistence(grant_app, page_stream_sizes):
    """GIVEN compressed whitespace WHEN decoded size exceeds budget THEN deny with no saved body."""
    writer = PdfWriter()
    for stream_sizes in page_stream_sizes:
        page = writer.add_blank_page(width=595, height=842)
        streams = []
        for size in stream_sizes:
            stream = DecodedStreamObject(); stream.set_data(b' ' * size)
            streams.append(writer._add_object(stream.flate_encode()))
        page[NameObject('/Contents')] = streams[0] if len(streams) == 1 else ArrayObject(streams)
    out = BytesIO(); writer.write(out); raw = out.getvalue()
    assert len(raw) < 2 * 1024**2
    app, client, source = grant_app
    command = await pdf_command(client, source, raw)
    store = app.service.projects.store; before = store.log_path.read_bytes()
    prior = set(store.root.rglob('*.bin'))
    result = await client.post(DOCS, json=command, headers={'Idempotency-Key': 'decoded-limit'})
    assert result.status_code == 403, result.text
    assert result.json()['reason'] == 'document_stream_limit'
    assert store.log_path.read_bytes() == before and set(store.root.rglob('*.bin')) == prior
    assert not list((store.root / 'document-staging').iterdir())
