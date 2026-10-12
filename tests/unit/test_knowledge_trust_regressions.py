"""Authorized source metadata, strict answers and synthetic service secrets."""
import asyncio
import json

import httpx
import pytest

from pyharness import cli
from pyharness.core.knowledge import RemoteRagKnowledgeProvider, KnowledgeError
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings


async def test_remote_metadata_is_rebuilt_from_owned_source(monkeypatch):
    provider = RemoteRagKnowledgeProvider('http://127.0.0.1:1')
    async def request(*args):
        return {'answer_allowed': True, 'citations': [{'source_id':'owned','quote':'valid text',
            'line':1,'name':'forged','version':999,'untrusted':'discard'}]}
    monkeypatch.setattr(provider, '_request', request)
    result = await provider.query('valid', [{'source_id':'owned','text':'valid text','name':'actual','version':2}])
    assert result['citations'] == [{'source_id':'owned','quote':'valid text','line':1,'name':'actual','version':2}]
    assert result['answer_allowed']


async def test_query_length_500_allowed_501_rejected_before_transport(monkeypatch):
    provider = RemoteRagKnowledgeProvider('http://127.0.0.1:1')
    calls = []
    async def request(*args):
        calls.append(args)
        return {'answer_allowed':False,'citations':[]}
    monkeypatch.setattr(provider, '_request', request)
    documents = [{'source_id':'owned','text':'synthetic','name':'actual'}]
    await provider.query('x'*500, documents)
    with pytest.raises(KnowledgeError, match='invalid_query'):
        await provider.query('x'*501, documents)
    assert len(calls) == 1


@pytest.mark.parametrize('status,code', [(401,'authentication_error'),
    (403,'authentication_error'), (422,'invalid_query'), (503,'provider_unavailable')])
async def test_authenticated_provider_redacts_errors_and_resolves_rotation(tmp_path, monkeypatch, status, code):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    original_client = httpx.AsyncClient
    observed = []
    def handler(request):
        observed.append(request.headers['authorization'])
        return httpx.Response(status, json={'message':'synthetic-sensitive-upstream-message'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    try:
        platform = app.service.platform
        body = {'type':'remote_rag','name':'test','endpoint':'http://127.0.0.1:1', 'bearer_token':'synthetic-first'}
        ident = platform.save_connection(body)['connection_id']
        provider = platform.rag_provider(platform.connection_definitions()[ident])
        for secret in ['synthetic-first','synthetic-rotated']:
            platform.save_connection({**body,'bearer_token':secret})
            with pytest.raises(KnowledgeError) as error:
                await provider.probe()
            assert str(error.value) == code
            assert secret not in repr(error.value)
        assert observed == ['Bearer synthetic-first','Bearer synthetic-rotated']
        public = json.dumps(platform.connections())
        persisted = (platform.root/'configuration.json').read_text('utf-8')
        assert 'synthetic-first' not in public+persisted
        assert 'synthetic-rotated' not in public+persisted
        assert platform.connections()[0]['has_secret']
        await platform.connection_action(ident, 'delete')
        with pytest.raises(KnowledgeError, match='authentication_error'):
            await provider.probe()
    finally:
        await app.service.shutdown()


@pytest.mark.parametrize('length', [500, 501, 4000, 4001])
async def test_local_query_original_capability_has_no_network(tmp_path, monkeypatch, length):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    async def forbidden(*args, **kwargs):
        pytest.fail('Local provider must not touch remote transport or synchronization')
    monkeypatch.setattr(RemoteRagKnowledgeProvider, '_http', forbidden)
    monkeypatch.setattr(app.service.platform, 'sync_knowledge', forbidden)
    try:
        query = 'x' * length
        if length > 4000:
            with pytest.raises(PyHError, match='invalid_query_length'):
                await app.service.platform.query_knowledge(query)
        else:
            result = await app.service.platform.query_knowledge(query)
            assert result['provider'] == 'exact_keyword'
    finally:
        await app.service.shutdown()


async def test_remote_platform_length_rejection_precedes_sync(tmp_path, monkeypatch):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    calls = []
    async def request(self, method, path, body, headers):
        calls.append(body['query'])
        return {'answer_allowed': False, 'citations': []}
    monkeypatch.setattr(RemoteRagKnowledgeProvider, '_http', request)
    try:
        p = app.service.platform
        await p.add_knowledge({'name':'synthetic.txt','text':'synthetic'})
        ident = p.save_connection({'type':'remote_rag','name':'remote','endpoint':'http://127.0.0.1:1'})['connection_id']
        await p.connection_action(ident, 'enable')
        p.knowledge_action('settings', 'provider', {'provider':ident})
        await p.query_knowledge('x'*500)
        async def forbidden(*args, **kwargs):
            pytest.fail('Invalid query must be rejected before any synchronization')
        monkeypatch.setattr(p, 'sync_knowledge', forbidden)
        with pytest.raises(PyHError, match='invalid_query_length'):
            await p.query_knowledge('x'*501)
        assert calls == ['x'*500]
        assert p.connection_definitions()[ident]['status'] != 'unavailable'
    finally:
        await app.service.shutdown()


@pytest.mark.parametrize('valid', [True, False])
async def test_strict_knowledge_validates_claims_before_final_answer(tmp_path, adapter_factory, valid):
    cfg = isolated_settings(tmp_path)
    adapter = adapter_factory(cfg)
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        platform = app.service.platform
        doc = await platform.add_knowledge({'name':'limits.txt','text':'The synthetic limit is seven.'})
        original = adapter.chat
        async def answer(*args, **kwargs):
            response = await original(*args, **kwargs)
            response.content = json.dumps({'claims':[{'source_id':doc['source_id'],'line':1,'version':1,
                'quote':'The synthetic limit is seven.' if valid else 'The synthetic limit is 999.'}]})
            return response
        adapter.chat = adapter.chat_stream = answer
        agent = platform.save_agent({'name':'strict evidence','strict_knowledge':True,'knowledge_sources':[doc['source_id']]})
        platform.publish_agent(agent['agent_id'])
        run = await platform.start_run({'text':'synthetic limit','agent_id':agent['agent_id'],'agent_version':1})
        result = await asyncio.wait_for(app.service._queues[run['session_id']].wait_for(run['task_id']),8)
        assert result.ok is valid
        log = await app.service.require_session(run['session_id'])
        answers = [e.payload['content'] for e in log.events_after(0) if e.type == 'agent.message']
        if valid:
            assert 'seven' in answers[-1] and 'limits.txt' in answers[-1]
        else:
            assert not answers
            assert result.code == 'knowledge_answer_invalid'
    finally:
        await app.service.shutdown()
