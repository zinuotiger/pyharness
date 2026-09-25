"""Application-owned policy adapter inside the existing runner/provider chain."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
from pyharness.application.platform_models import AgentDefinition
from pyharness.core.sandbox import fail, safe_path, read_regular
from pyharness.core.provider_result import ProviderOutcome


class PlatformRuntime:
    def __init__(self, platform, definition: AgentDefinition, session_id: str):
        self.platform = platform
        self.definition = definition
        self.session_id = session_id
        self.sandboxes = {}
        self.calls = {}
        self.locks = {}
        self.baselines = {}
        self.baseline_hashes = {}
        self.command_results = {}

    async def sandbox(self, ctx):
        task = str(ctx.task_id or '')
        if not task or ctx.session.sid != self.session_id:
            fail('sandbox_owner_mismatch')
        async with self.locks.setdefault(task, asyncio.Lock()):
            if task not in self.sandboxes:
                record = await self.platform.sandboxes.create(f'{self.session_id}~{task}',
                    self.definition.sandbox_profile,
                    Path(self.platform.service._session_workspace(self.session_id)))
                try:
                    await ctx.session.append('platform.sandbox', {'action':'created', 'record':record.model_dump()},
                        actor='system', task_id=task, sync=True)
                except BaseException:
                    await self.platform.sandboxes.destroy(record.sandbox_id)
                    raise
                self.sandboxes[task] = record.sandbox_id
                async with self.platform.sandboxes.snapshot(record.sandbox_id) as snapshot_root:
                    for event in ctx.session.events_after(0):
                        meta=(event.payload.get('meta') or {}).get('platform',{}) if event.type=='user.message' else {}
                        if meta.get('task_id')==task:
                            for ident in meta.get('artifact_ids',[]):
                                item,raw=await self.platform.artifact_content(ident)
                                if item['session_id']!=ctx.session.sid: fail('artifact_owner_mismatch')
                                target=safe_path(snapshot_root,'inputs/'+item['filename'])
                                target.parent.mkdir(parents=True,exist_ok=True)
                                from pyharness.core.tenant_settings import TenantSettingsStore
                                TenantSettingsStore._atomic_write(target,raw)
                                import os
                                if os.name!='nt' and os.getuid()==0:
                                    os.chown(target.parent,65534,65534)
                                    os.chown(target,65534,65534)
                    self.baselines[task] = self._text_files(snapshot_root)
                    self.baseline_hashes[task] = {name:hashlib.sha256(read_regular(safe_path(snapshot_root,name),1048576)).hexdigest()
                        for name in self.baselines[task]}
            return self.sandboxes[task]

    @staticmethod
    def _text_files(root):
        result={}
        for item in root.rglob('*'):
            relative=item.relative_to(root).as_posix()
            if any(p.startswith('.') or p=='__pycache__' for p in item.relative_to(root).parts):
                continue
            path=safe_path(root,relative)
            if path.is_file() and path.stat().st_size<=1048576:
                try:
                    result[relative]=read_regular(path,1048576).decode('utf-8')
                except UnicodeError:
                    continue
            if len(result)>50 or sum(len(t.encode()) for t in result.values())>5*1048576:
                fail('artifact_limit')
        return result

    async def finish(self, ctx):
        if self.definition.output_schema:
            from pyharness.core.tools_registry import _compile_model
            model=_compile_model('platform.output',self.definition.output_schema)
            messages=[e.payload['content'] for e in ctx.session.events_after(0) if e.type=='agent.message']
            try:
                model.model_validate(json.loads(messages[-1]))
            except (ValueError,IndexError):
                fail('agent_output_invalid')
        task=str(ctx.task_id or '')
        ident=self.sandboxes.get(task)
        if ident is None:
            return
        import difflib
        from pyharness.application.platform_projection import summary
        async with self.platform.sandboxes.snapshot(ident) as root:
            after=self._text_files(root)
        before=self.baselines.get(task,{})
        changes=[];patch=[]
        for name in sorted(set(before)|set(after)):
            value=after.get(name)
            if before.get(name)==value:
                continue
            original=before.get(name)
            changes.append({'path':name,'before_sha256':self.baseline_hashes.get(task,{}).get(name),'after':value})
            patch.extend(difflib.unified_diff((original or '').splitlines(True),(value or '').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
        if changes:
            await self.platform.record_generated(ctx,'changes.patch',''.join(patch).encode(),manifest=changes)
        commands=self.command_results.get(task,[])
        if commands:
            await self.platform.record_generated(ctx,'test-report.json',json.dumps(commands,ensure_ascii=False,indent=2).encode())

    async def release(self, ctx):
        ident=self.sandboxes.get(str(ctx.task_id or ''))
        if ident:
            record=await self.platform.sandboxes.destroy(ident)
            await ctx.session.append('platform.sandbox',{'action':'destroyed','record':record.model_dump()},actor='system',task_id=ctx.task_id,sync=True)

    async def execute(self, name, args, ctx, original):
        if name in {'workspace.apply_patch','workspace.export_artifact'}:
            if getattr(ctx,'_platform_action',None)!=(name,args['artifact_id']):
                fail('agent_tool_denied','artifact review operations are initiated by the user')
            return await self.platform.commit_artifact_action(args['artifact_id'],name,ctx)
        if name not in self.definition.allowed_tools:
            fail('agent_tool_denied')
        name = {'file.read':'fs.read_file','file.write':'fs.write_file','file.list':'fs.list_dir','file.patch':'fs.patch'}.get(name,name)
        task = str(ctx.task_id or '')
        self.calls[task] = self.calls.get(task, 0) + 1
        if self.calls[task] > self.definition.budget.tool_calls:
            fail('agent_tool_budget_exceeded')
        if name.startswith(('exec.', 'proc.', 'fs.')):
            if self.definition.sandbox_profile.mode == 'disabled':
                fail('sandbox_disabled')
            ident = await self.sandbox(ctx)
            if name in ('exec.shell_run', 'exec.python_run'):
                result = await self.platform.sandboxes.execute(ident, args.get('command', args.get('code', '')),
                    python=name == 'exec.python_run')
                from pyharness.application.platform_projection import summary
                self.command_results.setdefault(task,[]).append({'tool':name,'exit_code':result['exit_code'],
                    'output':summary(result.get('output',''),4000),'truncated':result.get('truncated',False)})
                return ProviderOutcome({**result,'ok':result.get('ok',result.get('exit_code')==0)})
            if name=='proc.start':
                return json.dumps(await self.platform.sandboxes.start_process(ident,args['command']))
            if name=='proc.status':
                return json.dumps(self.platform.sandboxes.process_status(ident,args['pid']))
            if name=='proc.kill':
                return json.dumps(await self.platform.sandboxes.kill_process(ident,args['pid']))
            if name=='exec.pty':
                fail('sandbox_operation_unsupported', 'PTY is disabled; use governed shell execution')
            if self.definition.sandbox_profile.mode=='isolated':
                if any(len(str(args.get(k,'')).encode())>1048576 for k in ('content','old','new')):
                    fail('file_too_large')
                return await self.platform.sandboxes.file_operation(ident,name,args)
            root = self.platform.sandboxes.workspace(ident)
            path = args.get('path', '')
            if path in ('', '.') and name == 'fs.list_dir':
                target = root
            else:
                target = safe_path(root, path)
            if name == 'fs.read_file':
                if not target.is_file() or target.stat().st_size > 65536:
                    fail('file_too_large')
                return read_regular(target,65536).decode('utf-8')
            if name == 'fs.list_dir':
                return json.dumps([p.name for p in list(target.iterdir())[:500]], ensure_ascii=False)
            if name == 'fs.write_file':
                content = str(args.get('content', ''))
                if len(content.encode()) > 1048576:
                    fail('file_too_large')
                if args.get('mode') == 'append' and target.exists():
                    if target.stat().st_size > 1048576:
                        fail('file_too_large')
                    content = read_regular(target,1048576).decode('utf-8') + content
                if len(content.encode())>1048576:
                    fail('file_too_large')
                target.parent.mkdir(parents=True, exist_ok=True)
                from pyharness.core.tenant_settings import TenantSettingsStore
                TenantSettingsStore._atomic_write(target, content.encode('utf-8'))
                return 'Private sandbox file written'
            if name == 'fs.patch':
                if not target.is_file() or target.stat().st_size>1048576:
                    fail('file_too_large')
                before=read_regular(target,1048576).decode('utf-8')
                if not args['old'] or before.count(args['old'])!=1:
                    fail('patch_baseline_mismatch')
                after=before.replace(args['old'],args['new'],1)
                if len(after.encode())>1048576:
                    fail('file_too_large')
                from pyharness.core.tenant_settings import TenantSettingsStore
                TenantSettingsStore._atomic_write(target,after.encode())
                return 'Private sandbox patch applied'
            fail('sandbox_operation_unsupported')
        # Privileged connectors and orchestration cannot bypass the sandbox.
        if name.startswith('mcp.') and self.definition.sandbox_profile.mode=='host_approved':
            server=name.split('.')[1]
            if server in self.definition.mcp_connections:
                return await original()
        if name.startswith(('mcp.', 'plugin.', 'job.', 'subagent.', 'web.', 'net.')):
            fail('sandbox_external_capability_denied')
        return await original()

    def configure_spine(self, spine):
        from pyharness.core.tools_registry import ToolDefinition
        async def boundary_only(args,ctx):
            fail('platform_runtime_required')
        aliases={'file.read':('fs.read_file','low'),'file.write':('fs.write_file','high'),'file.list':('fs.list_dir','low')}
        for alias,(original,danger) in aliases.items():
            old=spine.tool_registry.lookup(original)
            data=old.model_dump(by_alias=True)
            data['schema']=data.pop('schema_',data.get('schema',{}))
            data.update(name=alias,danger=danger,approval='always' if danger=='high' else 'policy')
            # Read aliases preserve the original approval enum.
            if danger=='low': data['approval']=old.approval
            spine.tool_registry.register_tool(ToolDefinition.model_validate(data),provider=boundary_only)
        for name in ('file.patch','fs.patch'):
            spine.tool_registry.register_tool(ToolDefinition(name=name,description='Replace one exact text occurrence in the private workspace',owner='builtin',danger='high',approval='always',
                schema={'type':'object','properties':{k:{'type':'string'} for k in ('path','old','new')},'required':['path','old','new'],'additionalProperties':False}),provider=boundary_only)
        # Narrow the existing Scope; never broaden an existing denial.
        all_names = list(spine.tool_registry._tools)
        spine.scope.policy.deny_tools.update(set(all_names) - set(self.definition.allowed_tools))
        for name in ('fs.write_file',):
            if spine.tool_registry.has(name):
                old = spine.tool_registry.lookup(name)
                from pyharness.core.tool_fs import write_file
                data = old.model_dump(by_alias=True)
                data['schema'] = data.pop('schema_', data.get('schema', {}))
                data.update(danger='high', approval='always')
                from pyharness.core.tools_registry import ToolDefinition
                spine.tool_registry.unregister(name)
                spine.tool_registry.register_tool(ToolDefinition.model_validate(data), provider=write_file)
        from pyharness.core.system_prompt import TemplatePart
        spine.sysprompt.template.parts.insert(0, TemplatePart('text', body=self.definition.system_prompt))
        if self.definition.output_schema:
            spine.sysprompt.template.parts.insert(1,TemplatePart('text',body='Return JSON matching this schema: '+json.dumps(self.definition.output_schema)))
        for name in self.definition.skills:
            skill=spine.skills.load(name)
            spine.sysprompt.template.parts.insert(1,TemplatePart('text',body=str(skill.get('content') or skill.get('body') or '')))
        spine.platform_runtime = self
        spine.tools.hidden_tools={'workspace.apply_patch','workspace.export_artifact'}
        from pyharness.core.tools_registry import ToolDefinition
        async def artifact_handler(args,ctx):
            return await self.platform.commit_artifact_action(args['artifact_id'],'workspace.apply_patch',ctx)
        for name,description in [('workspace.apply_patch','Apply the reviewed artifact patch to the session input workspace; requires approval and matching baseline'),
                                  ('workspace.export_artifact','Approve download of a generated artifact; requires human approval')]:
            spine.tool_registry.register_tool(ToolDefinition(name=name,description=description,danger='high',approval='always',owner='builtin',
                schema={'type':'object','properties':{'artifact_id':{'type':'string','minLength':1}},'required':['artifact_id'],'additionalProperties':False}),provider=artifact_handler)
