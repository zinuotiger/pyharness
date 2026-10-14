"""PlatformArtifacts operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import base64
import copy
import hashlib
import json
import mimetypes
from pathlib import Path
import uuid
from datetime import datetime, timezone
from pyharness.application.platform_models import ArtifactRecord
from pyharness.core.sandbox import safe_path, fail

class PlatformArtifacts:
    """PlatformArtifacts responsibility; state is owned by the composing facade."""

    async def artifacts(self, *, cursor=None, limit=None):
        authorized = await self.list_index.refresh(self.service)
        selected,meta = self.list_index.page('artifacts',authorized,cursor=cursor,limit=limit)
        return {'artifacts':selected, **meta}

    async def artifact(self, ident):
        item = next((r for r in (await self.artifacts())['artifacts'] if r['artifact_id'] == ident), None)
        if item is None:
            fail('artifact_not_found')
        return item

    async def upload(self, body):
        sid = str(body.get('session_id') or '')
        log = await self.service.require_session(sid)
        name = str(body.get('filename') or '')
        if len(name) > 150 or '/' in name or '\\' in name:
            fail('path_rejected')
        safe_path(self.root, name)
        encoded = str(body.get('data_base64') or '')
        if len(encoded) > 14_000_000:
            fail('file_too_large')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            fail('invalid_file_encoding')
        if len(raw) > 10 * 1024 * 1024:
            fail('file_too_large')
        media_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        image = name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))
        if image:
            from pyharness.application.vision import preview_image
            media_type = preview_image(raw, name)['mime']
        ident = 'f-' + uuid.uuid4().hex
        path = safe_path(self.root / 'artifacts', ident)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_artifact(path, raw)
        record = ArtifactRecord(artifact_id=ident, session_id=sid, filename=name,
            size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), storage_path=ident,
            media_type=media_type,
            download_allowed=True, approval_required=False,
            preview_type='image' if image else 'text')
        try:
            await log.append('platform.artifact', {'action':'uploaded','record':record.model_dump()}, actor='user', sync=True)
        except BaseException:
            path.unlink()
            raise
        return record.model_dump()

    async def artifact_content(self, ident, *, preview=False):
        item = await self.artifact(ident)
        if not preview and not item['download_allowed']:
            fail('artifact_approval_required')
        path = safe_path(self.root / 'artifacts', item['storage_path'])
        if item.get('retention_until') and datetime.fromisoformat(item['retention_until']) < datetime.now(timezone.utc):
            fail('artifact_expired')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            fail('artifact_integrity_failed')
        return item, raw

    async def record_generated(self, ctx, name, raw, *, manifest=None, validation_status='not_run'):
        from pyharness.config import redact
        text=raw.decode('utf-8', errors='ignore')
        if redact(text)!=text:
            fail('artifact_contains_secret')
        ident='f-'+uuid.uuid4().hex
        root=self.root/'artifacts'
        root.mkdir(parents=True,exist_ok=True)
        record=ArtifactRecord(artifact_id=ident,session_id=ctx.session.sid,
            run_id=f'{ctx.session.sid}~{ctx.task_id}',filename=name,size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),storage_path=ident,
            media_type='text/x-diff' if manifest else mimetypes.guess_type(name)[0] or 'application/octet-stream',
            preview_type='patch' if manifest else 'image' if name.lower().endswith(('.png','.jpg','.jpeg','.webp','.gif')) else 'text',validation_status=validation_status)
        path=safe_path(root,ident)
        from pyharness.core.tenant_settings import TenantSettingsStore
        TenantSettingsStore._atomic_write(path,raw)
        manifest_path=safe_path(root,ident+'.manifest')
        try:
            if manifest is not None:
                encoded=json.dumps(manifest,ensure_ascii=False).encode()
                record.manifest_sha256=hashlib.sha256(encoded).hexdigest()
                TenantSettingsStore._atomic_write(manifest_path,encoded)
            await ctx.session.append('platform.artifact',{'action':'generated','record':record.model_dump()},
                actor='system',task_id=ctx.task_id,sync=True)
        except BaseException:
            path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            raise
        return record.model_dump()

    async def request_artifact_action(self, ident, action):
        item=await self.artifact(ident)
        sid=item['session_id']
        if action not in {'apply','export'} or (action=='apply' and not item['manifest_sha256']):
            fail('invalid_artifact_action')
        if action=='apply' and item.get('validation_status')=='failed':
            fail('patch_validation_failed')
        queue=self.service._queues.get(sid)
        if queue is not None and (queue.status().depth or queue.status().running):
            fail('session_busy')
        key=f'{ident}:{action}'
        if key in self.actions and not self.actions[key].done():
            fail('artifact_action_pending')
        log,spine=await self.service.public_spine_for(sid)
        if not getattr(spine,'platform_runtime',None):
            fail('artifact_requires_platform_session')
        from pyharness.engine import agent_context
        from pyharness.core.tools_guard import ToolCall
        ctx=copy.copy(await agent_context(spine,log))
        ctx.task_id=(item['run_id'] or '').split('~',1)[-1]
        ctx._platform_action=('workspace.'+('apply_patch' if action=='apply' else 'export_artifact'),ident)
        async def execute():
            result=await spine.tools.execute(ToolCall(name='workspace.'+('apply_patch' if action=='apply' else 'export_artifact'),
                raw_args={'artifact_id':ident},call_id='artifact-'+uuid.uuid4().hex),ctx)
            return {'ok':result.ok,'summary':result.summary}
        task=asyncio.create_task(execute())
        task.add_done_callback(lambda t:None if t.cancelled() else t.exception())
        self.actions[key]=task
        # An EventBus-driven approval will appear in the existing approval centre.
        return {'operation_id':key,'status':'submitted'}

    async def commit_artifact_action(self, ident, name, ctx):
        item,raw=await self.artifact_content(ident,preview=True)
        if item['session_id']!=ctx.session.sid:
            fail('artifact_owner_mismatch')
        if name=='workspace.export_artifact':
            item['download_allowed']=True
            item['approval_required']=False
            action='export_approved'
            result={'download_allowed':True}
        else:
            if item.get('validation_status')=='failed':
                fail('patch_validation_failed')
            if not item['manifest_sha256']:
                fail('invalid_patch')
            data=safe_path(self.root/'artifacts',item['storage_path']+'.manifest').read_bytes()
            if hashlib.sha256(data).hexdigest()!=item['manifest_sha256']:
                fail('artifact_integrity_failed')
            from pyharness.core.patches import apply_manifest, digest
            root=Path(self.service._session_workspace(ctx.session.sid))
            async with self.locks.setdefault('patch:'+ctx.session.sid,asyncio.Lock()):
                changes=json.loads(data)
                inverse=[]
                for change in changes:
                    path=safe_path(root,change['path'])
                    before=path.read_bytes() if path.exists() else None
                    after=change['after'].encode() if change['after'] is not None else None
                    inverse.append({'path':change['path'],'before_sha256':digest(after),
                        'after':before.decode('utf-8') if before is not None else None})
                result=apply_manifest(root,changes)
                try:
                    await ctx.session.append('platform.artifact',{'action':'applied','record':item},actor='system',task_id=ctx.task_id,sync=True)
                except BaseException:
                    apply_manifest(root,inverse)
                    raise
            return json.dumps(result)
        await ctx.session.append('platform.artifact',{'action':action,'record':item},actor='system',task_id=ctx.task_id,sync=True)
        return json.dumps(result)

    async def delete_artifact(self, ident):
        item = await self.artifact(ident)
        log = await self.service.require_session(item['session_id'])
        path = safe_path(self.root / 'artifacts', item['storage_path'])
        await log.append('platform.artifact', {'action':'deleted','record':item}, actor='user', sync=True)
        path.unlink(missing_ok=True)
        safe_path(self.root/'artifacts',item['storage_path']+'.manifest').unlink(missing_ok=True)
        return {'ok':True}

