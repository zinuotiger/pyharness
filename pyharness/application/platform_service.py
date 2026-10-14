"""Web platform application service, sharing the existing execution owners."""
from __future__ import annotations
import asyncio
import base64
import copy
import difflib
import hashlib
import json
import mimetypes
import httpx
from pathlib import Path
import uuid
from datetime import datetime, timezone

from pyharness.application.platform_models import AgentDefinition, AgentVersion, ArtifactRecord, KnowledgeSource
from pyharness.application.platform_projection import project, summary
from pyharness.application.platform_store import PlatformStore
from pyharness.core.knowledge import ExactKeywordKnowledgeProvider
from pyharness.core.sandbox import SandboxManager, safe_path, fail


from pyharness.application.platform_templates import (TEMPLATES)

from pyharness.application.platform_agents import PlatformAgents
from pyharness.application.platform_history import PlatformHistory
from pyharness.application.platform_runs import PlatformRuns
from pyharness.application.platform_artifacts import PlatformArtifacts
from pyharness.application.platform_knowledge import PlatformKnowledge

class PlatformService(PlatformAgents, PlatformHistory, PlatformRuns, PlatformArtifacts, PlatformKnowledge):
    def __init__(self, service):
        self.service = service
        self.root = service.tenant_root() / 'platform'
        self.store = PlatformStore(self.root)
        self.sandboxes = SandboxManager(self.root / 'sandboxes')
        from pyharness.application.chat_setup import ChatSetup
        from pyharness.core.remote_sandbox import RemoteSandboxBackend
        self.chat_setup = ChatSetup(self)
        self.sandboxes.backends['remote'] = RemoteSandboxBackend(self.chat_setup.remote_settings)
        self.knowledge = ExactKeywordKnowledgeProvider()
        self.locks = {}
        self.actions = {}
        self.connection_clients = {}
        self.history_index = None
        from pyharness.application.projection_cache import ProjectionCache
        self.projection_cache = ProjectionCache()
        self.artifact_cache = ProjectionCache()
        from pyharness.application.list_index import ListIndex
        self.list_index = ListIndex(self.root / 'lists-v1.db')
        from pyharness.application.platform_workflow import PlatformWorkflows
        self.workflows = PlatformWorkflows(self)


    async def health(self):
        docker = await self.sandboxes.backends['isolated'].health()
        state = self.service.tenant_state()
        return {'components':[
            {'component':'Agent Runtime','status':'stopping' if getattr(self.service,'_closing',False) else 'available'},
            {'component':'TaskQueue','status':'available','active':len(self.service._queues)},
            {'component':'数据存储','status':'available' if self.root.exists() else 'not_initialized'},
            {'component':'Docker Sandbox','status':docker['status']},
            {'component':'模型连接','status':'configured' if state.get('profiles') else 'not_configured'},
            {'component':'Knowledge Provider','status':self.knowledge_list()['status'],'provider':self.knowledge_list()['provider']},
            {'component':'MCP','status':'not_configured' if not self.connection_definitions() and not self.service.effective_settings().plugins.mcp_servers else 'not_probed'},
            {'component':'Schedule','status':'available'},
            {'component':'Web 服务','status':'available'}], 'docker':docker}

    async def sandbox_records(self):
        records={}
        for row in (await self.service.list_sessions())['sessions']:
            log=await self.service.require_session(row['sid'])
            for e in log.events_after(0):
                if e.type=='platform.sandbox':
                    record=dict(e.payload['record'])
                    if record['status']=='running':
                        record['status']='orphaned'
                        record['error_code']='runtime_interrupted'
                    records[record['sandbox_id']]=record
        records.update({ident:r.model_dump() for ident,r in self.sandboxes.records.items()})
        return list(records.values())

    async def destroy_sandbox(self, ident):
        item=next((r for r in await self.sandbox_records() if r['sandbox_id']==ident),None)
        if item is None: fail('sandbox_not_found')
        if item['status']=='destroyed': return item
        if ident not in self.sandboxes.records:
            from pyharness.application.platform_models import SandboxRecord
            await self.sandboxes.recover(SandboxRecord.model_validate(item))
        record=await self.sandboxes.destroy(ident)
        sid,task=record.run_id.split('~',1)
        log=await self.service.require_session(sid)
        await log.append('platform.sandbox',{'action':'destroyed','record':record.model_dump()},actor='user',task_id=task,sync=True)
        return record.model_dump()


    async def dashboard(self):
        view = await self.all_views()
        artifacts = (await self.artifacts())['artifacts']
        usage = await self.usage(view)
        month=datetime.now(timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0).isoformat()
        month_usage=await self.usage(view,filters={'from':month})
        state=self.service.tenant_state()
        current=next((p for p in state['profiles'] if p['id']==state['active']),None)
        from pyharness import __version__
        return {'operator':self.service.tenant_id,'version':__version__,'current_model':current['model'] if current else None,
            'month_usage':month_usage,'recent_artifacts':artifacts[-5:],
            'sessions':[s for s in (await self.sessions())['sessions'] if not s.get('archived')][:5],
            'runs':sorted(view['runs'],key=lambda r:r['queued_at'] or r['started_at'] or '',reverse=True)[:5],
            'pending_approvals':sum(a['status'] == 'pending' for a in (await self.approvals())['approvals']),
            'failed':sum(r['status'] == 'failed' for r in view['runs']),
            'completed':sum(r['status'] == 'completed' for r in view['runs']),
            'running':sum(r['status'] in {'running','waiting_approval'} for r in view['runs']),
            'usage':usage, 'knowledge':{'count':len(self.store.read()['knowledge']),
                'bytes':sum(d['size'] for d in self.store.read()['knowledge'].values())},
            'artifact_bytes':sum(a['size'] for a in artifacts), 'health':await self.health(),
            'trace':list(reversed(view['trace']))[:5]}

    async def usage(self, view=None, filters=None):
        from pyharness.application.platform_usage import usage
        return await usage(self, view or await self.all_views(), filters or {})

    async def close(self):
        await self.stop_actions()
        if self.history_index is not None:
            await self.history_index.detach()
            self.history_index = None
        errors=[]
        for client in list(self.connection_clients.values()):
            try:
                await client.close()
            except BaseException as exc:
                errors.append(exc)
        self.connection_clients.clear()
        await self.sandboxes.close()
        if errors:
            raise BaseExceptionGroup('connection cleanup failed',errors)

    async def stop_actions(self):
        await self.workflows.close()
        tasks=list(self.actions.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)

    @staticmethod
    def _write_artifact(path, raw):
        """The owning platform service is the only artifact byte writer."""
        path.write_bytes(raw)

