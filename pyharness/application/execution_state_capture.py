"""Bounded real backend bytes and context, attached to the existing run journal."""
import asyncio,base64,os,stat
from pyharness.application.execution_delegation import normalized
from pyharness.application.execution_effects import confirmed_events
from pyharness.application.state_snapshot_contracts import ExecutionStateSnapshot,MAX_STATE_BYTES,MAX_STATE_FILE_BYTES,MAX_STATE_TOTAL_FILE_BYTES
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.sandbox import safe_path
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.errors import PyHError,raise_code

MAX_EXECUTION_STATE_BYTES=256*1048576


class StateUnavailable(Exception):
    def __init__(self,reason):self.reason=reason


def state_regular(path,limit):
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o7000:raise StateUnavailable('unsafe_state_path')
        if info.st_size>limit:raise StateUnavailable('snapshot_limit')
        raw=f.read(limit+1)
        if len(raw)>limit:raise StateUnavailable('snapshot_limit')
        return raw,stat.S_IMODE(info.st_mode)


def workspace_state(root):
    files=[];directories=[];stack=[''];visited=0;total=0;seen=set()
    while stack:
        relative=stack.pop();directory=root if not relative else safe_path(root,relative)
        for entry in sorted(directory.iterdir(),key=lambda p:p.name):
            visited+=1
            if visited>8192:raise StateUnavailable('snapshot_limit')
            name=entry.relative_to(root).as_posix();path=safe_path(root,name);fold=name.casefold()
            if fold in seen:raise StateUnavailable('unsafe_state_path')
            seen.add(fold)
            if path.is_dir():
                info=path.stat()
                if info.st_mode&0o7000:raise StateUnavailable('unsafe_state_path')
                if len(directories)>=4096:raise StateUnavailable('snapshot_limit')
                directories.append(dict(path=name,mode=stat.S_IMODE(info.st_mode)));stack.append(name);continue
            raw,mode=state_regular(path,MAX_STATE_FILE_BYTES);total+=len(raw)
            if total>MAX_STATE_TOTAL_FILE_BYTES or len(files)>=4096:raise StateUnavailable('snapshot_limit')
            files.append(dict(path=name,mode=mode,size=len(raw),sha256=digest_of(raw),data_b64=base64.b64encode(raw).decode('ascii')))
    return sorted(files,key=lambda f:f['path']),sorted(directories,key=lambda d:d['path'])


async def capture_state(boundary,ctx,messages):
    boundary._context(ctx);boundary.checked();runtime=ctx.platform_runtime
    if runtime.definition.sandbox_profile.mode=='remote':return dict(unavailable_reason='unsupported_backend')
    if runtime.definition.memory_scope=='team':return dict(unavailable_reason='shared_memory_not_captured')
    try:
        if len(canonical(messages))>16*1048576:raise StateUnavailable('snapshot_limit')
        task=str(ctx.task_id);files=[];directories=[];mode=runtime.definition.sandbox_profile.mode
        if mode!='disabled':
            ident=runtime.sandboxes.get(task)
            if ident is None:raise StateUnavailable('snapshot_state_invalid')
            async with runtime.locks.setdefault(task,asyncio.Lock()):
                async with runtime.platform.sandboxes.observe(ident) as root:
                    boundary.checked();files,directories=workspace_state(root);boundary.checked()
        kv=safe_path(boundary.projects.service._tenant_sessions_dir(),ctx.session.sid+'.kv.json')
        if kv.exists():kv_raw,_=state_regular(kv,4*1048576)
        else:kv_raw=None
        scope=ctx.scope;counter=getattr(scope,'_counters',None)
        injected=counter is not None and callable(getattr(counter,'task_total',None))
        budget=dict(counter_injected=injected,limits=normalized(scope.limits),usage=normalized(scope._used_dict(scope._usage())),policy=normalized(scope.policy),cost_observation='runtime_estimate')
        from pyharness.application.execution_state_contract import continuation_contract
        contract=continuation_contract(boundary,ctx,messages)
        boundary.checked()
        return dict(messages=messages,messages_digest=digest_of(canonical(messages)),budget=budget,continuation_contract=contract,workspace_backend=mode,
            filesystem_capture='no_workspace' if mode=='disabled' else 'files_and_directories',files=files,directories=directories,
            kv_present=kv_raw is not None,kv_data_b64=base64.b64encode(kv_raw).decode('ascii') if kv_raw is not None else None)
    except StateUnavailable as exc:return dict(unavailable_reason=exc.reason)
    except PyHError as exc:
        if exc.code in ('GRD-403','EVT-101'):raise
        return dict(unavailable_reason='unsafe_state_path' if exc.code=='path_rejected' else 'snapshot_io_failed')
    except OSError:return dict(unavailable_reason='snapshot_io_failed')
    except (ValueError,TypeError):return dict(unavailable_reason='snapshot_state_invalid')


async def append_state_checkpoint(boundary,ctx,checkpoint,captured):
    boundary._context(ctx);boundary.checked();receipt=boundary.receipt
    checkpoint_digest=digest_of(canonical(checkpoint.model_dump(mode='json')))
    identity=dict(schema_version=1,execution_id=receipt['execution_id'],authorization_digest=receipt['digest'],
        authorization_seq=receipt['source_seq'],checkpoint_id=checkpoint.payload['checkpoint_id'],
        checkpoint_seq=checkpoint.seq,checkpoint_digest=checkpoint_digest,capture_scope='session_local_state',replay_authorized=False)
    reason=captured.get('unavailable_reason');raw=None;bundle=None
    if reason is None:
        try:
            bundle=ExecutionStateSnapshot(**identity,session_id=ctx.session.sid,task_id=str(ctx.task_id),
                materials_digest=boundary.materials['materials_digest'],**captured)
            raw=canonical(bundle.model_dump(mode='json'))
            existing=sum(e.payload['state_size'] for e in ctx.session.events_after(0) if e.type=='execution.state_checkpoint')
            if len(raw)>MAX_STATE_BYTES:raise StateUnavailable('snapshot_limit')
            if existing+len(raw)>MAX_EXECUTION_STATE_BYTES:raise StateUnavailable('snapshot_quota')
            root=ctx.platform_runtime.platform.root/'execution-state';root.mkdir(parents=True,exist_ok=True,mode=0o700)
            path=safe_path(root,digest_of(raw))
            if not path.exists():TenantSettingsStore._atomic_write(path,raw)
            actual,_=state_regular(path,MAX_STATE_BYTES)
            if actual!=raw:raise StateUnavailable('snapshot_io_failed')
        except StateUnavailable as exc:reason=exc.reason;raw=None
        except OSError:reason='snapshot_io_failed';raw=None
        except (ValueError,TypeError):reason='snapshot_state_invalid';raw=None
    boundary.checked()
    payload=dict(identity,status='captured' if raw is not None else 'unavailable',state_digest=digest_of(raw) if raw is not None else None,
        state_size=len(raw) if raw is not None else 0,file_count=len(bundle.files) if raw is not None else 0,
        kv_present=bundle.kv_present if raw is not None else None,unavailable_reason=reason)
    event=await ctx.session.append('execution.state_checkpoint',payload,actor='system',task_id=str(ctx.task_id),sync=True)
    if confirmed_events(ctx.session)[-1]!=event:raise_code('PERS-202',reason='execution_state_checkpoint_unconfirmed')
    boundary.checked();return event
