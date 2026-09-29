"""Run-owned execution backends; isolated execution never falls back to host.

Docker is an operator-managed dependency. Images must already exist locally;
this module never pulls images or mounts a repository, HOME, or Docker socket.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid
import re
import hashlib
import stat
from typing import Protocol, Any
from contextlib import asynccontextmanager

from pyharness.application.platform_models import SandboxProfile, SandboxRecord
from pyharness.errors import PyHError


def fail(code: str, detail: str = ''):
    raise PyHError(code, ctx={'detail': detail})


def safe_path(root: Path, name: str) -> Path:
    """One strict relative path boundary, including symlinks and junctions."""
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        fail('path_rejected')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('.', '..') for x in name.split('/')):
        fail('path_rejected')
    base = root.resolve()
    if root.is_symlink() or (hasattr(root,'is_junction') and root.is_junction()):
        fail('path_rejected','workspace root must not be a link')
    dest = root.joinpath(*p.parts)
    current = root
    for part in p.parts:
        if part.endswith(('.', ' ')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',part):
            fail('path_rejected','reserved path component')
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            fail('path_rejected', 'links are not accepted')
    if not dest.resolve().is_relative_to(base):
        fail('path_rejected')
    return dest


def read_regular(path: Path, limit: int) -> bytes:
    """Reject pipes/devices without a blocking open, and bind checks to the fd."""
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode): fail('file_not_regular')
        if info.st_size>limit: fail('file_too_large')
        raw=stream.read(limit+1)
        if len(raw)>limit: fail('file_too_large')
        return raw


def private_copy(source: Path, destination: Path) -> None:
    """Copy bounded ordinary input files; reject links and omit credentials."""
    count = total = 0
    for entry in source.rglob('*'):
        rel = entry.relative_to(source)
        if any(p.startswith('.') or p.lower() in {'credentials.yaml', 'credentials.yml', 'node_modules', '__pycache__', 'venv'} for p in rel.parts):
            continue
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            fail('path_rejected', 'input contains a link')
        if entry.is_file():
            count += 1
            total += entry.stat().st_size
            if count > 500 or total > 10 * 1024 * 1024:
                fail('workspace_limit')
            target = safe_path(destination, rel.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry, target)


async def private_git_clone(source: Path, destination: Path) -> None:
    """Bounded local bare clone and verified archive; no hooks, filters or network."""
    import io
    import tarfile
    git = shutil.which('git')
    metadata=source/'.git'
    if not git or not metadata.is_dir() or metadata.is_symlink():
        fail('workspace_clone_unavailable','Requires a local repository with an ordinary .git directory')
    total=count=0
    for item in metadata.rglob('*'):
        safe_path(metadata,item.relative_to(metadata).as_posix())
        count+=1
        if item.is_file(): total+=item.stat().st_size
        if total>20*1048576 or count>5000:
            fail('workspace_limit')
    clone=destination/'.private-clone'
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT'}}
    env.update(HOME=str(destination),USERPROFILE=str(destination),GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',GIT_ALLOW_PROTOCOL='file')
    async def command(args,limit):
        kw={'creationflags':0x08000000} if os.name=='nt' else {}
        proc=await asyncio.create_subprocess_exec(git,'-c','core.hooksPath='+os.devnull,*args,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=env,**kw)
        raw=bytearray()
        async def collect():
            while chunk:=await proc.stdout.read(8192):
                raw.extend(chunk)
                if len(raw)>limit: fail('workspace_limit')
            if await proc.wait(): fail('workspace_clone_failed')
        try:
            await asyncio.wait_for(collect(),20)
        except BaseException:
            if proc.returncode is None: proc.kill()
            await proc.wait()
            raise
        return bytes(raw)
    try:
        await command(['clone','--bare','--no-local','--no-hardlinks','--',str(source.resolve()),str(clone.resolve())],65536)
        raw=await command(['--git-dir='+str(clone.resolve()),'archive','--format=tar','HEAD'],12*1048576)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members=archive.getmembers()
            if len(members)>500 or sum(m.size for m in members)>10*1048576: fail('workspace_limit')
            for member in members:
                if not member.isfile() and not member.isdir(): fail('path_rejected')
                if any(p.startswith('.') or p.lower() in {'credentials.yaml','credentials.yml','node_modules','venv'} for p in Path(member.name).parts): continue
                target=safe_path(destination,member.name.rstrip('/'))
                if member.isdir(): target.mkdir(parents=True,exist_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
    finally:
        if clone.exists():
            # Git marks objects read-only on Windows. Only this verified private clone is touched.
            for item in clone.rglob('*'):
                if item.is_file(): item.chmod(0o600)
            shutil.rmtree(clone)


class SandboxBackend(Protocol):
    async def create(self, record: SandboxRecord, root: Path, profile: SandboxProfile) -> None: ...
    async def execute(self, record: SandboxRecord, command: str, *, python: bool = False) -> dict: ...
    async def destroy(self, record: SandboxRecord) -> None: ...


class DisabledSandboxBackend:
    async def create(self, record, root, profile):
        fail('sandbox_disabled')

    async def execute(self, record, command, *, python=False):
        fail('sandbox_disabled')

    async def destroy(self, record):
        return None


class HostApprovedSandboxBackend:
    """Explicit operator choice. No OS isolation, network or memory guarantee."""
    def __init__(self):
        self.roots = {}
        self.profiles = {}

    async def create(self, record, root, profile):
        self.roots[record.sandbox_id] = root
        self.profiles[record.sandbox_id] = profile

    async def execute(self, record, command, *, python=False):
        from pyharness.core.proc import wait_result_async
        result=await wait_result_async(self.roots[record.sandbox_id], command,
            timeout_s=self.profiles[record.sandbox_id].timeout, python=python)
        limit=self.profiles[record.sandbox_id].output_limit
        output=result.get('stdout','')+result.get('stderr','')
        return {**result,'output':output[:limit],'truncated':len(output)>limit or result.get('truncated',False)}

    async def destroy(self, record):
        self.roots.pop(record.sandbox_id, None)
        self.profiles.pop(record.sandbox_id, None)


class DockerSandboxBackend:
    def __init__(self, client_config: Path):
        self.profiles = {}
        self.executable = shutil.which('docker')
        self.client_config = client_config
        self.owner_label=hashlib.sha256(str(client_config.resolve()).encode()).hexdigest()

    async def _command(self, args: list[str], *, timeout: int = 15, limit: int = 65536, input_data: bytes | None = None) -> dict:
        if not self.executable:
            fail('sandbox_unavailable', 'Docker executable not found')
        env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT'}}
        self.client_config.mkdir(parents=True, exist_ok=True)
        env.update(HOME=str(self.client_config), USERPROFILE=str(self.client_config), DOCKER_CONFIG=str(self.client_config))
        kw = {'creationflags': 0x08000000} if os.name == 'nt' else {}
        proc = await asyncio.create_subprocess_exec(self.executable, '--config', str(self.client_config), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            env=env, **kw)
        output = bytearray()
        truncated = False
        async def consume():
            nonlocal truncated
            while chunk := await proc.stdout.read(8192):
                room = max(0, limit - len(output))
                output.extend(chunk[:room])
                truncated |= len(chunk) > room
            return await proc.wait()
        try:
            async def send():
                if input_data is not None:
                    proc.stdin.write(input_data)
                    await proc.stdin.drain()
                    proc.stdin.close()
            async def exchange():
                _,code=await asyncio.gather(send(),consume())
                return code
            code = await asyncio.wait_for(exchange(), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        return {'ok':code==0,'exit_code': code, 'output': output.decode('utf-8', errors='replace'),
                'truncated': truncated, 'timed_out': False}

    async def health(self):
        try:
            r = await self._command(['info', '--format', '{{.ServerVersion}}'], timeout=5)
            return {'status': 'available' if r['exit_code'] == 0 else 'unavailable',
                    'backend': 'docker', 'network_allowlist': False}
        except (OSError, PyHError, asyncio.TimeoutError):
            return {'status': 'unavailable', 'backend': 'docker', 'network_allowlist': False}

    async def create(self, record, root, profile):
        if (await self.health())['status'] != 'available':
            fail('sandbox_unavailable', 'Docker daemon is unavailable')
        args = ['run', '--detach', '--pull=never', '--name', record.sandbox_id,
            '--label', 'pyharness.managed=true', '--label', 'pyharness.owner='+self.owner_label, '--network=none',
            '--user=' + (f'{os.getuid()}:{os.getgid()}' if os.name != 'nt' and os.getuid() != 0 else '65534:65534'),
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--read-only',
            '--cpus', str(profile.cpus), '--memory', f'{profile.memory_mb}m',
            '--memory-swap', f'{profile.memory_mb}m', '--pids-limit', str(profile.pids_limit),
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m', '--workdir', '/workspace',
            '--env', 'HOME=/tmp', '--mount', f'type=bind,source={root.resolve()},target=/workspace',
            profile.image, 'python', '-I', '-B', '-c', 'import time; time.sleep(86400)']
        try:
            r = await self._command(args)
            if r['exit_code'] != 0:
                fail('sandbox_unavailable', 'Container creation failed; preinstall the configured image')
        except BaseException as primary:
            try:
                await asyncio.shield(self.destroy(record))
            except BaseException as cleanup:
                record.status='cleanup_failed'
                raise BaseExceptionGroup('container creation and cleanup failed',[primary,cleanup])
            raise
        self.profiles[record.sandbox_id] = profile

    async def execute(self, record, command, *, python=False):
        profile = self.profiles[record.sandbox_id]
        argv = ['python', '-c', command] if python else ['sh', '-lc', command]
        try:
            return await self._command(['exec', record.sandbox_id, *argv],
                timeout=profile.timeout, limit=profile.output_limit)
        except BaseException:
            await asyncio.shield(self.destroy(record))
            raise

    async def destroy(self, record):
        last = None
        for _ in range(2):
            try:
                r = await self._command(['rm', '--force', record.sandbox_id])
                if r['exit_code'] == 0 or 'No such container' in r['output']:
                    self.profiles.pop(record.sandbox_id, None)
                    return
                last = 'Docker remove failed'
            except (OSError, PyHError, asyncio.TimeoutError) as exc:
                last = type(exc).__name__
        fail('sandbox_destroy_failed', str(last))

    async def freeze(self, record):
        result=await self._command(['pause',record.sandbox_id])
        if result['exit_code']: fail('sandbox_snapshot_unavailable')

    async def verify_owner(self, record):
        result=await self._command(['inspect','--format','{{json .Config.Labels}}',record.sandbox_id])
        if result['exit_code']:
            if 'No such' in result['output']: return
            fail('sandbox_unavailable')
        labels=json.loads(result['output'])
        if labels.get('pyharness.managed')!='true' or labels.get('pyharness.owner')!=self.owner_label:
            fail('sandbox_owner_mismatch')

    async def thaw(self, record):
        result=await self._command(['unpause',record.sandbox_id])
        if result['exit_code']: fail('sandbox_snapshot_unavailable')

    async def file_operation(self, record, name, args):
        # All path traversal here takes place INSIDE the container. Even an
        # adversarial symlink race cannot make this process access host HOME.
        program = '''import json, pathlib, os, stat, sys
a=json.loads(sys.stdin.read(8*1048576))
root=pathlib.Path('/workspace')
name=%r
raw=a.get('path','')
if raw in ('','.') and name=='fs.list_dir': path=root
else:
 if not raw or '\\\\' in raw or ':' in raw or raw.startswith('/') or any(x in ('.','..','') for x in raw.split('/')): raise ValueError('path_rejected')
 path=root/raw
 current=root
 for part in raw.split('/'):
  current=current/part
  if current.is_symlink(): raise ValueError('path_rejected')
 if not path.resolve().is_relative_to(root): raise ValueError('path_rejected')
if name=='fs.list_dir': result=json.dumps([p.name for p in list(path.iterdir())[:500]],ensure_ascii=False)
elif name=='fs.read_file':
 fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as source:
  meta=os.fstat(source.fileno())
  if not stat.S_ISREG(meta.st_mode) or meta.st_size>65536: raise ValueError('file_rejected')
  result=source.read(65537).decode('utf-8')
else:
 content=a.get('content','')
 before=None
 if name=='fs.patch' or (a.get('mode')=='append' and path.exists()):
  fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
  with os.fdopen(fd,'rb') as source:
   meta=os.fstat(source.fileno())
   if not stat.S_ISREG(meta.st_mode) or meta.st_size>1048576: raise ValueError('file_rejected')
   before=source.read(1048577).decode('utf-8')
 if name=='fs.patch':
  content=before
  if not a['old'] or content.count(a['old'])!=1: raise ValueError('patch_baseline_mismatch')
  content=content.replace(a['old'],a['new'],1)
 elif before is not None: content=before+content
 if len(content.encode())>1048576: raise ValueError('file_too_large')
 path.parent.mkdir(parents=True,exist_ok=True)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_NONBLOCK|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as target:
  if not stat.S_ISREG(os.fstat(target.fileno()).st_mode): raise ValueError('file_rejected')
  target.truncate(0)
  target.write(content.encode())
 result='Private sandbox file updated'
print(json.dumps({'value':result},ensure_ascii=False))
''' % name
        profile=self.profiles[record.sandbox_id]
        result=await self._command(['exec','-i',record.sandbox_id,'python','-I','-B','-c',program],
            timeout=profile.timeout,limit=400000,input_data=json.dumps(args,ensure_ascii=False).encode())
        if result['exit_code']!=0:
            fail('sandbox_file_failed')
        if result.get('truncated'): fail('file_too_large')
        return json.loads(result['output'])['value']


class SandboxManager:
    def __init__(self, root: Path, *, docker: SandboxBackend | None = None):
        self.root = root
        self.backends = {'disabled': DisabledSandboxBackend(), 'host_approved': HostApprovedSandboxBackend(),
                         'isolated': docker or DockerSandboxBackend(root.parent / 'docker-client')}
        self.records: dict[str, SandboxRecord] = {}
        self.lock = asyncio.Lock()
        self.processes = {}

    async def create(self, run_id: str, profile: SandboxProfile, source: Path | None = None):
        if profile.mode == 'disabled':
            fail('sandbox_disabled')
        async with self.lock:
            ident = 'ph-' + uuid.uuid4().hex
            record = SandboxRecord(sandbox_id=ident, run_id=run_id, backend=profile.mode,
                status='creating', workspace_mode=profile.workspace_mode, cpus=profile.cpus,
                memory_mb=profile.memory_mb, pids_limit=profile.pids_limit,
                network='none' if profile.mode=='isolated' else 'host_unrestricted')
            root = safe_path(self.root, ident)
            root.mkdir(parents=True)
            try:
                if source is not None and profile.workspace_mode == 'private_copy':
                    private_copy(source, root)
                elif profile.workspace_mode == 'private_git_clone':
                    if source is None: fail('workspace_clone_unavailable')
                    await private_git_clone(source,root)
                if os.name != 'nt':
                    # Private container copy is writable to the non-root container uid.
                    for item in [root, *root.rglob('*')]:
                        if os.getuid() == 0:
                            os.chown(item, 65534, 65534)
                        item.chmod(0o700 if item.is_dir() else 0o600)
                await self.backends[profile.mode].create(record, root, profile)
                record.status = 'running'
            except BaseException as original:
                # A backend may create a resource before reporting failure.
                # Keep an owned handle when cleanup fails, so close/retry can reap it.
                try:
                    if record.status=='cleanup_failed' or not (isinstance(original,PyHError) and original.code=='sandbox_unavailable'):
                        await asyncio.shield(self.backends[profile.mode].destroy(record))
                    shutil.rmtree(root)
                except BaseException:
                    record.status='cleanup_failed'
                    record.error_code='sandbox_destroy_failed'
                    self.records[ident]=record
                raise
            self.records[ident] = record
            return record

    def workspace(self, ident: str) -> Path:
        if ident not in self.records or self.records[ident].status != 'running':
            fail('sandbox_not_running')
        return safe_path(self.root, ident)

    @asynccontextmanager
    async def snapshot(self, ident):
        record=self.records[ident]
        backend=self.backends[record.backend]
        if record.backend=='isolated':
            await backend.freeze(record)
        try:
            yield self.workspace(ident)
        finally:
            if record.backend=='isolated':
                await backend.thaw(record)

    async def file_operation(self, ident, name, args):
        self.workspace(ident)
        record=self.records[ident]
        if record.backend!='isolated': fail('sandbox_operation_unsupported')
        return await self.backends['isolated'].file_operation(record,name,args)

    async def execute(self, ident, command, *, python=False):
        self.workspace(ident)
        record = self.records[ident]
        return await self.backends[record.backend].execute(record, command, python=python)

    async def start_process(self, ident, command):
        self.workspace(ident)
        if sum(owner==ident and not task.done() for owner,task in self.processes.values())>=8:
            fail('sandbox_process_limit')
        token='process-'+uuid.uuid4().hex
        task=asyncio.create_task(self.execute(ident,command))
        self.processes[token]=(ident,task)
        # Read exceptions even when the caller never polls the process.
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return {'pid':token,'running':True}

    def process_status(self, ident, token):
        item=self.processes.get(token)
        if item is None or item[0]!=ident:
            fail('sandbox_process_not_found')
        task=item[1]
        if not task.done():
            return {'pid':token,'running':True}
        if task.cancelled():
            return {'pid':token,'running':False,'cancelled':True}
        if task.exception():
            return {'pid':token,'running':False,'error_code':'sandbox_process_failed'}
        return {'pid':token,'running':False,**task.result()}

    async def kill_process(self, ident, token):
        self.process_status(ident,token)
        task=self.processes[token][1]
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        # Docker cancellation terminates the whole run-owned container.
        if self.records[ident].backend=='isolated':
            await self.destroy(ident)
        return self.process_status(ident,token)

    async def destroy(self, ident):
        record = self.records.get(ident)
        if record is None:
            fail('sandbox_not_found')
        if record.status == 'destroyed':
            return record
        try:
            tasks=[task for owner,task in self.processes.values() if owner==ident and not task.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks,return_exceptions=True)
            await self.backends[record.backend].destroy(record)
            root = safe_path(self.root, ident)
            if root.exists():
                shutil.rmtree(root)
            record.status = 'destroyed'
        except BaseException:
            record.status = 'cleanup_failed'
            record.error_code = 'sandbox_destroy_failed'
            raise
        return record

    async def recover(self, record):
        if record.backend!='isolated' or not re.fullmatch(r'ph-[a-f0-9]{32}',record.sandbox_id):
            fail('sandbox_recovery_unsupported')
        await self.backends['isolated'].verify_owner(record)
        record.status='cleanup_failed'
        self.records[record.sandbox_id]=record

    async def close(self):
        failures = []
        for ident in list(self.records):
            try:
                await self.destroy(ident)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup('sandbox cleanup failed', failures)
