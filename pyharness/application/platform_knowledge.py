"""PlatformKnowledge operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import hashlib
import httpx
import uuid
from datetime import datetime, timezone
from pyharness.application.platform_models import KnowledgeSource
from pyharness.core.sandbox import fail

class PlatformKnowledge:
    """PlatformKnowledge responsibility; state is owned by the composing facade."""

    async def add_knowledge(self, body):
        name = str(body.get('name') or '').strip()
        text = str(body.get('text') or '')
        if not name or len(name) > 150 or not text or len(text.encode()) > 1048576:
            fail('invalid_document')
        ident = 'k-' + uuid.uuid4().hex
        record = KnowledgeSource(source_id=ident, name=name, size=len(text.encode()),
            sha256=hashlib.sha256(text.encode()).hexdigest())
        with self.store.transaction() as data:
            data['knowledge'][ident] = {**record.model_dump(), 'text':text}
        await self.sync_knowledge()
        return record.model_dump()

    def knowledge_list(self):
        data=self.store.read()
        provider=data['preferences'].get('knowledge_provider','exact_keyword')
        sync=data['preferences'].get('knowledge_sync',{}).get(provider,{})
        return {'sources':[{k:v for k,v in row.items() if k != 'text'} for row in data['knowledge'].values()],
                'provider':provider, 'sync_error':sync.get('last_error'),
                'sync_pending':sum(sync.get('documents',{}).get(k,{}).get('sha256') != v['sha256']
                    for k,v in data['knowledge'].items()) + len(set(sync.get('documents',{}))-set(data['knowledge']))
                    if sync else 0,
                'status':'available' if provider=='exact_keyword' else data['connections'].get(provider,{}).get('status','unavailable')}

    def knowledge_action(self, ident, action, body):
        with self.store.transaction() as data:
            if action=='provider':
                provider=body.get('provider')
                if provider!='exact_keyword' and (provider not in data['connections'] or not provider.startswith('remote_rag:')):
                    fail('knowledge_provider_not_found')
                data['preferences']['knowledge_provider']=provider
            else:
                if ident not in data['knowledge']:
                    fail('knowledge_not_found')
                if action=='retry':
                    data['knowledge'][ident]['status']='indexed'
                elif action=='replace':
                    text=body.get('text')
                    if not isinstance(text,str) or not text or len(text.encode())>1048576:
                        fail('invalid_document')
                    row=data['knowledge'][ident]
                    row.update(text=text,size=len(text.encode()),sha256=hashlib.sha256(text.encode()).hexdigest(),
                        version=row['version']+1,updated_at=datetime.now(timezone.utc).isoformat())
                else: fail('invalid_action')
        return {'ok':True}

    def delete_knowledge(self, ident):
        with self.store.transaction() as data:
            if ident not in data['knowledge']:
                fail('knowledge_not_found')
            definitions=list(data['agents'].values())+[v['snapshot'] for versions in data['versions'].values() for v in versions]
            if any(ident in a.get('knowledge_sources',[]) for a in definitions):
                fail('knowledge_in_use')
            del data['knowledge'][ident]
        return {'ok':True}

    async def query_knowledge(self, query, sources=None):
        from pyharness.core.knowledge import (KnowledgeError, validate_query,
            ExactKeywordKnowledgeProvider, RemoteRagKnowledgeProvider)
        data=self.store.read()
        provider=data['preferences'].get('knowledge_provider','exact_keyword')
        capabilities = (ExactKeywordKnowledgeProvider if provider == 'exact_keyword'
                        else RemoteRagKnowledgeProvider).capabilities
        try:
            validate_query(query, capabilities)
        except KnowledgeError as error:
            fail(error.code)
        if provider != 'exact_keyword':
            await self.sync_knowledge(provider)
        docs = list(data['knowledge'].values())
        if sources is not None:
            docs = [d for d in docs if d['source_id'] in sources]
        if provider=='exact_keyword':
            result=await self.knowledge.query(query,docs)
        else:
            from pyharness.core.knowledge import RemoteRagKnowledgeProvider
            row=data['connections'].get(provider)
            if not row or not row['config'].get('enabled'):
                fail('knowledge_unavailable')
            try:
                result=await asyncio.wait_for(self.rag_provider(row).query(query,docs),6)
            except KnowledgeError as error:
                if error.code not in ('invalid_query', 'invalid_query_length'):
                    with self.store.transaction() as current:
                        current['connections'][provider]['status']='unavailable'
                fail(error.code)
            except (TimeoutError, httpx.TimeoutException):
                with self.store.transaction() as current:
                    current['connections'][provider]['status']='unavailable'
                fail('knowledge_timeout')
            except Exception:
                with self.store.transaction() as current:
                    current['connections'][provider]['status']='unavailable'
                fail('knowledge_unavailable')
        return {**result,'diagnostics':{'documents':len(docs),'strategy':'exact evidence','semantic_default':False}}

    def connection_definitions(self):
        return self.store.read()['connections']

    def rag_provider(self, row):
        from pyharness.core.knowledge import RemoteRagKnowledgeProvider
        sync = self.store.read()['preferences'].get('knowledge_sync', {}).get(row['connection_id'], {}) if row['config'].get('sync_mode') == 'sync-enabled' else {}
        return RemoteRagKnowledgeProvider(row['config']['endpoint'],
            source_owner=sync.get('owner'),
            secret_ref=row['config'].get('secret_ref'),
            secret_resolver=lambda ref, endpoint: self.service._settings_store.resolve_service_secret(
                ref, self.service.tenant_id, endpoint))

    async def sync_knowledge(self, connection_id=None):
        """Reconcile explicit sync-enabled connections. State survives restart.

        No startup network dependency, hidden retries or manual-mode uploads.
        Local mutation remains authoritative after a remote failure; the next
        explicit sync/query retries the pending desired version idempotently.
        """
        data = self.store.read()
        ident = connection_id or data['preferences'].get('knowledge_provider')
        row = data['connections'].get(ident)
        if not row or row['type'] != 'remote_rag' or row['config'].get('sync_mode','manual') != 'sync-enabled':
            return {'status':'manual','operations':0}
        if not row['config'].get('enabled'):
            return {'status':'disabled','operations':0}
        lock = self.locks.setdefault('knowledge-sync:'+ident, asyncio.Lock())
        async with lock:
            with self.store.transaction() as current:
                sync = current['preferences'].setdefault('knowledge_sync', {}).setdefault(ident,
                    {'owner':uuid.uuid4().hex,'documents':{},'endpoint':row['config']['endpoint']})
                if sync['endpoint'] != row['config']['endpoint']:
                    fail('knowledge_sync_endpoint_conflict')
            desired = self.store.read()['knowledge']
            provider = self.rag_provider(row)
            completed = 0
            try:
                for source in sorted(set(desired) | set(sync['documents'])):
                    saved = sync['documents'].get(source)
                    document = desired.get(source)
                    if document and saved and saved['sha256'] == document['sha256'] and saved['version'] == document['version']:
                        continue
                    if completed >= 100:
                        fail('knowledge_sync_batch_pending')
                    path = '/platform-sources/'+sync['owner']+'/'+source
                    if document:
                        result = await provider.request('PUT',path,{k:document[k] for k in ('text','version','sha256')})
                        answer = result.get('data',{})
                        if (result.get('status') != 'ok' or answer.get('source_id') != source
                                or answer.get('version') != document['version'] or answer.get('sha256') != document['sha256']):
                            fail('knowledge_sync_invalid_response')
                        entry = {k:document[k] for k in ('version','sha256')}
                    else:
                        result = await provider.request('DELETE',path+'?version='+str(saved['version']))
                        if result.get('status') != 'ok' or result.get('data',{}).get('deleted') is not True:
                            fail('knowledge_sync_invalid_response')
                        entry = None
                    with self.store.transaction() as current:
                        records = current['preferences']['knowledge_sync'][ident]['documents']
                        if entry is None: records.pop(source, None)
                        else: records[source] = entry
                        current['preferences']['knowledge_sync'][ident]['last_error'] = None
                    completed += 1
            except Exception as error:
                from pyharness.core.knowledge import KnowledgeError
                from pyharness.errors import PyHError
                code = error.code if isinstance(error, KnowledgeError) else (
                    error.code if isinstance(error, PyHError) and error.code in (
                        'knowledge_sync_batch_pending','knowledge_sync_invalid_response') else 'knowledge_sync_failed')
                with self.store.transaction() as current:
                    current['preferences']['knowledge_sync'][ident]['last_error'] = code
                fail(code)
            return {'status':'synced','operations':completed}

    def save_connection(self, body):
        import re
        from pyharness.config import McpServerCfg
        kind=body.get('type')
        if kind=='mcp':
            config=McpServerCfg.model_validate(body.get('config',{})).model_dump()
            name=config['name']
            if not re.fullmatch(r'[a-z][a-z0-9_]{1,40}',name):
                fail('invalid_connection_name')
            if any(len(arg)>2000 or re.search(r'(?i)(sk-[a-z0-9]{12}|--?(token|password|secret|api[-_]?key))',arg) for arg in config['command']):
                fail('secret_in_command_not_supported')
        elif kind=='remote_rag':
            from pyharness.core.knowledge import RemoteRagKnowledgeProvider
            name=str(body.get('name') or '')
            if not re.fullmatch(r'[a-z][a-z0-9_]{1,40}',name):
                fail('invalid_connection_name')
            provider=RemoteRagKnowledgeProvider(str(body.get('endpoint') or ''))
            from urllib.parse import urlsplit
            url=urlsplit(provider.endpoint)
            if url.username or url.password or url.query or url.fragment:
                fail('secret_in_url_not_supported')
            previous = self.connection_definitions().get('remote_rag:'+name, {}).get('config', {})
            mode = body.get('sync_mode', previous.get('sync_mode', 'manual'))
            if mode not in ('manual', 'sync-enabled'):
                fail('invalid_sync_mode')
            config={'endpoint':provider.endpoint,'enabled':previous.get('enabled', False),
                    'sync_mode':mode, 'secret_ref':previous.get('secret_ref', '')}
            if 'bearer_token' in body:
                config['secret_ref'] = self.service._settings_store.set_service_secret(
                    self.service.tenant_id, 'remote_rag:'+name, provider.endpoint, body['bearer_token'])
        else:
            fail('invalid_connection_type')
        ident=kind+':'+name
        with self.store.transaction() as data:
            data['connections'][ident]={'connection_id':ident,'name':name,'type':kind,
                'config':config,'status':'not_probed','last_test':None,'last_used':None}
        return {'connection_id':ident,'name':name,'type':kind,'status':'not_probed','restart_required':bool(self.service._engines)}

    def connections(self):
        rows=[]
        agents=self.store.read()['agents'].values()
        for ident,row in self.connection_definitions().items():
            client=self.connection_clients.get(ident)
            rows.append({k:v for k,v in row.items() if k!='config'}|{
                'enabled':row['config'].get('enabled',False),
                'has_secret':bool(row['config'].get('secret_ref')),
                'sync_mode':row['config'].get('sync_mode','manual'),
                'endpoint':row['config'].get('endpoint') if row['type']=='remote_rag' else None,
                'status':'connected' if client and client.connected else ('disabled' if not row['config'].get('enabled') else 'not_probed'),
                'tool_count':len(client._tools) if client and client.connected else None,
                'scopes':row['config'].get('allowed_tools') or [],
                'agents':[a['name'] for a in agents if row['name'] in a['mcp_connections']]})
        return rows

    async def connection_action(self, ident, action):
        if ident.startswith('model:') and action=='test':
            from pyharness.application.model_probe import probe
            profile=next((p for p in self.service.tenant_state()['profiles'] if p['id']==ident[6:]),None)
            if profile is None: fail('connection_not_found')
            lock=self.locks.setdefault('probe:'+ident,asyncio.Lock())
            if lock.locked(): fail('model_probe_busy')
            async with lock:
                result=await probe(self.service,profile)
            with self.store.transaction() as data:
                data['preferences'].setdefault('model_probes',{})[ident[6:]]={**result,'checked_at':datetime.now(timezone.utc).isoformat()}
            return result
        row=self.connection_definitions().get(ident)
        if row is None:
            fail('connection_not_found')
        if action == 'sync' and row['type'] == 'remote_rag':
            return await self.sync_knowledge(ident)
        if action in {'test','start'}:
            if ident in self.connection_clients:
                await self.connection_clients.pop(ident).close()
            if row['type']=='mcp':
                from pyharness.core.mcp import McpClient,StdioTransport
                client=McpClient(row['name'],StdioTransport(row['config']['command'],timeout_s=min(30,row['config']['timeout_s'])))
                try:
                    await asyncio.wait_for(client.connect(),35)
                    self.connection_clients[ident]=client
                    status='connected'
                except Exception:
                    await client.close()
                    status='unavailable'
            else:
                try:
                    await self.rag_provider(row).probe()
                    status='available'
                except Exception:
                    status='unavailable'
            with self.store.transaction() as data:
                if ident in data['connections']:
                    data['connections'][ident]['last_test']=datetime.now(timezone.utc).isoformat()
                    data['connections'][ident]['status']=status
            return {'status':status}
        if action not in {'stop','enable','disable','delete'}:
            fail('invalid_action')
        if action=='delete' and row['type']=='remote_rag':
            synced=self.store.read()['preferences'].get('knowledge_sync',{}).get(ident,{})
            if synced.get('documents'):
                fail('knowledge_sync_documents_remaining')
        if action in {'stop','disable','delete'}:
            if ident in self.connection_clients:
                await self.connection_clients.pop(ident).close()
            for spine in self.service._engines.values():
                for client in list(getattr(spine,'_mcp_clients',[])):
                    if client.name==row['name']:
                        await client.close()
        with self.store.transaction() as data:
            if action=='delete':
                if row['type'] == 'remote_rag' and row['config'].get('secret_ref'):
                    self.service._settings_store.set_service_secret(self.service.tenant_id,
                        ident, row['config']['endpoint'], '')
                del data['connections'][ident]
            else:
                if action!='stop':
                    data['connections'][ident]['config']['enabled']=action=='enable'
                data['connections'][ident]['status']='stopped' if action=='stop' else 'not_probed'
        return {'ok':True,'restart_required':bool(self.service._engines)}

