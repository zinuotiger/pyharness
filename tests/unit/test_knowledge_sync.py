"""Synthetic transport only: durable, explicit RAG document ownership."""
import hashlib
import pytest

from pyharness import cli
from pyharness.core.knowledge import KnowledgeError, RemoteRagKnowledgeProvider
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings


async def test_manual_sync_and_restart_pending_versions(tmp_path, monkeypatch):
    cfg = isolated_settings(tmp_path)
    app = DesktopApp(cli.assemble_ctx(cfg))
    calls = []
    fail_next = False
    async def request(self, method, path, body=None):
        nonlocal fail_next
        calls.append((method, path, body))
        if fail_next:
            fail_next = False
            raise KnowledgeError('provider_unavailable')
        if method == 'DELETE':
            return {'status':'ok','data':{'deleted':True}}
        assert body['sha256'] == hashlib.sha256(body['text'].encode()).hexdigest()
        return {'status':'ok','data':{'source_id':path.split('/')[-1],
            'version':body['version'],'sha256':body['sha256']}}
    monkeypatch.setattr(RemoteRagKnowledgeProvider, 'request', request)
    try:
        p = app.service.platform
        body = {'type':'remote_rag','name':'remote','endpoint':'http://127.0.0.1:1'}
        ident = p.save_connection(body)['connection_id']
        await p.connection_action(ident,'enable')
        p.knowledge_action('settings','provider',{'provider':ident})
        doc = await p.add_knowledge({'name':'synthetic.txt','text':'initial synthetic content'})
        assert (await p.sync_knowledge())['status'] == 'manual'
        assert calls == []
        p.save_connection({**body,'sync_mode':'sync-enabled'})
        assert (await p.sync_knowledge())['operations'] == 1
        original_path = calls[-1][1]
        assert (await p.sync_knowledge())['operations'] == 0
        p.knowledge_action(doc['source_id'],'replace',{'text':'replacement synthetic content'})
        fail_next = True
        with pytest.raises(PyHError, match='provider_unavailable'):
            await p.sync_knowledge()
        assert len(calls) == 2  # No hidden retry.
        await app.service.shutdown()
        app = DesktopApp(cli.assemble_ctx(cfg))
        p = app.service.platform
        assert (await p.sync_knowledge())['operations'] == 1
        assert calls[-1][1] == original_path and calls[-1][2]['version'] == 2
        assert (await p.sync_knowledge())['operations'] == 0
        p.delete_knowledge(doc['source_id'])
        assert (await p.sync_knowledge())['operations'] == 1
        assert calls[-1] == ('DELETE',original_path+'?version=2',None)
        assert (await p.sync_knowledge())['operations'] == 0
        assert p.store.read()['preferences']['knowledge_sync'][ident]['documents'] == {}
    finally:
        await app.service.shutdown()
