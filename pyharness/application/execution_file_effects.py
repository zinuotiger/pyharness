"""Bounded physical observations, never predicted writes or transactional attribution."""
import hashlib
from pathlib import PurePosixPath
from pyharness.core.sandbox import safe_path,read_regular,fail

ALIASES={'file.write':'fs.write_file','file.patch':'fs.patch'}
TARGET_TOOLS={'fs.write_file','fs.patch'}
WORKSPACE_TOOLS={'exec.shell_run','exec.python_run','proc.start','proc.kill'}


def validate_file_observation(payload,events):
    if payload.get('file_observation') is None:return
    from pyharness.errors import raise_code
    name=ALIASES.get(payload['tool'],payload['tool'])
    scope='target' if name in TARGET_TOOLS else 'workspace' if name in WORKSPACE_TOOLS else None
    if scope is None or payload['observation_scope']!=scope:raise_code('PERS-202',reason='file_observation_tool_mismatch')
    if scope=='target' and payload['file_changes'] is not None:
        call=next((e for e in events if e.seq==payload['tool_call_seq']),None)
        if call is None:raise_code('PERS-202',reason='file_observation_call_missing')
        path=PurePosixPath(call.payload['args']['path']).as_posix()
        if any(c['path']!=path for c in payload['file_changes']):raise_code('PERS-202',reason='file_observation_path_mismatch')


def file_observer(runtime,name,args,ctx):
    normalized=ALIASES.get(name,name)
    if (getattr(runtime,'product_execution',None) is None or name not in runtime.definition.allowed_tools or
            runtime.definition.sandbox_profile.mode=='disabled' or
            normalized not in TARGET_TOOLS|WORKSPACE_TOOLS):return None
    return FileObservation(runtime,normalized,args,ctx)


class FileObservation:
    def __init__(self,runtime,name,args,ctx):
        self.runtime=runtime;self.name=name;self.args=args;self.ctx=ctx
        self.boundary=runtime.product_execution;self.ident=None;self.initial=None
        self.scope='target' if name in TARGET_TOOLS else 'workspace'

    async def capture(self):
        self.boundary.authorize()
        if self.ident is None:self.ident=await self.runtime.sandbox(self.ctx)
        task=str(self.ctx.task_id)
        import asyncio
        async with self.runtime.locks.setdefault(task,asyncio.Lock()):
            async with self.runtime.platform.sandboxes.observe(self.ident) as root:
                self.boundary.authorize()
                if self.scope=='target':
                    path=safe_path(root,self.args['path']);name=path.relative_to(root).as_posix()
                    if path.exists() and not path.is_file():fail('file_not_regular')
                    raw=read_regular(path,10*1048576) if path.exists() else None
                    result={} if raw is None else {name:hashlib.sha256(raw).hexdigest()}
                else:
                    from pyharness.application.execution_file_runtime import visible_output
                    result={};total=0
                    for index,entry in enumerate(root.rglob('*'),1):
                        if index>8192:fail('artifact_limit')
                        name=entry.relative_to(root).as_posix()
                        if not visible_output(self.boundary,name):continue
                        path=safe_path(root,name)
                        if not path.is_file():continue
                        if len(result)>=250:fail('artifact_limit')
                        raw=read_regular(path,10*1048576);total+=len(raw)
                        if total>20*1048576:fail('artifact_limit')
                        result[name]=hashlib.sha256(raw).hexdigest()
                self.boundary.authorize()
                return result

    async def before(self):self.initial=await self.capture()

    async def after(self,result):
        if self.name=='proc.start':return dict(file_observation='unavailable',observation_scope=self.scope,
            observation_reason='process_running',file_changes=None)
        try:final=await self.capture()
        except Exception as exc:
            return dict(file_observation='unavailable',observation_scope=self.scope,file_changes=None,
                observation_reason='authorization_changed' if getattr(exc,'code',None)=='GRD-403' else 'snapshot_failed')
        changes=[]
        for name in sorted(set(self.initial)|set(final)):
            before=self.initial.get(name);after=final.get(name)
            if before==after:continue
            changes.append(dict(path=name,kind='created' if before is None else 'deleted' if after is None else 'modified',
                before_sha256=before,after_sha256=after))
        return dict(file_observation='observed',observation_scope=self.scope,observation_reason=None,file_changes=changes)
