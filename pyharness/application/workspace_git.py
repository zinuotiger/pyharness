"""Bounded selected Git-layer observation through a private metadata view.

No source configuration, filters, hooks, status refresh, lazy fetching or writes.
This is an ephemeral preview, deliberately not a committed WorkspaceSnapshot.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from pydantic import Field, model_validator
from pyharness.application.source_grants import GrantId
from pyharness.application.source_selection import SelectionPreview, observe_selection
from pyharness.application.source_snapshot import LIMIT, check_content, read_selected
from pyharness.core.sandbox import safe_path
from pyharness.errors import PyHError, raise_code

MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_METADATA_FILES = 5000
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
OID = re.compile(rb'(?:[0-9a-f]{40}|[0-9a-f]{64})')


class GitPreviewCommand(SelectionPreview):
    metadata_grant_id: GrantId | None = None
    metadata_grant_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def paired_metadata_authority(self):
        if (self.metadata_grant_id is None) != (self.metadata_grant_revision is None):
            raise ValueError('metadata grant identity and revision must be paired')
        return self


def _unavailable():
    raise_code('GRD-403', reason='git_source_unavailable_or_unsupported')


def _safe(root, name):
    try: return safe_path(root, name)
    except PyHError: _unavailable()


def _metadata(root, name, limit=4096):
    """Read an authorized regular metadata file; never follow a link or pipe."""
    try:
        path = _safe(root, name)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            _unavailable()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_nlink)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if identity(before) != identity(opened) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise_code('EVT-101', reason='git_metadata_changed')
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if (len(raw) != before.st_size or identity(before) != identity(opened)
                or identity(before) != identity(after) or identity(before) != identity(_safe(root, name).lstat())):
            raise_code('EVT-101', reason='git_metadata_changed')
        return raw
    except FileNotFoundError:
        raise
    except OSError:
        _unavailable()
    except PyHError as error:
        if error.code not in {'GRD-403', 'EVT-101'}:
            _unavailable()
        raise


def _line(raw):
    try:
        text = raw.decode('utf-8').rstrip('\r\n')
        if not text or any(ord(c) < 32 for c in text): _unavailable()
        return text
    except UnicodeDecodeError:
        _unavailable()


def _layout(root, metadata_root):
    marker = _safe(root, '.git')
    if marker.is_dir():
        common = marker
        if metadata_root is not None and Path(metadata_root) != common: _unavailable()
        if (common / 'commondir').exists(): _unavailable()
        return common, common, 'ordinary', b''
    # Reading this pointer is within the source grant. No external metadata access yet.
    raw = _metadata(root, '.git')
    pointer = _line(raw)
    if not pointer.startswith('gitdir: ') or metadata_root is None: _unavailable()
    candidate = Path(pointer[8:])
    if not candidate.is_absolute(): candidate = root / candidate
    # Resolve only geometry; the explicit metadata grant must authorize the exact common root.
    candidate = candidate.absolute()
    common = Path(metadata_root)
    try:
        relative = candidate.relative_to(common).as_posix()
        parts = relative.split('/')
        if len(parts) != 2 or parts[0] != 'worktrees' or not parts[1]: _unavailable()
        gitdir = _safe(common, relative)
        if not gitdir.is_dir(): _unavailable()
        declared = _line(_metadata(common, relative + '/commondir'))
        resolved_common = (gitdir / declared).resolve(strict=True)
        if resolved_common != common.resolve(strict=True): _unavailable()
        backlink = Path(_line(_metadata(common, relative + '/gitdir')))
        if not backlink.is_absolute() or backlink != marker: _unavailable()
        return common, gitdir, 'linked_worktree', raw
    except (ValueError, OSError):
        _unavailable()


def _head(common, gitdir):
    relative = gitdir.relative_to(common).as_posix()
    prefix = '' if relative == '.' else relative + '/'
    raw = _metadata(common, prefix + 'HEAD')
    for _ in range(8):
        value = raw.strip()
        if OID.fullmatch(value): return value
        if not value.startswith(b'ref: '): _unavailable()
        name = _line(value[5:])
        if not name.startswith('refs/'): _unavailable()
        try:
            raw = _metadata(common, name)
        except FileNotFoundError:
            try: packed = _metadata(common, 'packed-refs', 512 * 1024)
            except FileNotFoundError: _unavailable()
            for line in packed.splitlines():
                if line.startswith((b'#', b'^')): continue
                fields = line.split(b' ', 1)
                if len(fields) == 2 and fields[1] == name.encode('utf-8') and OID.fullmatch(fields[0]): return fields[0]
            _unavailable()
    _unavailable()


def _index(common, gitdir):
    relative = gitdir.relative_to(common).as_posix()
    try: return _metadata(common, ('' if relative == '.' else relative + '/') + 'index', MAX_INDEX_BYTES)
    except FileNotFoundError: return None


def _metadata_boundary(common):
    """Only granted metadata; reject indirections that Git could otherwise follow."""
    for name in ('objects/info/alternates', 'objects/info/http-alternates'):
        if _safe(common, name).exists(): _unavailable()
    total = count = 0
    for item in common.rglob('*'):
        relative = item.relative_to(common)
        if len(relative.parts)>32: raise_code('EVT-100',reason='git_metadata_depth_limit')
        checked = _safe(common, relative.as_posix())
        info = checked.lstat()
        count += 1
        if item.name.endswith('.promisor') or item.name.startswith('sharedindex.'): _unavailable()
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1: _unavailable()
            total += info.st_size
        elif not stat.S_ISDIR(info.st_mode): _unavailable()
        if count > MAX_METADATA_FILES or total > MAX_METADATA_BYTES:
            raise_code('EVT-100', reason='git_metadata_limit')


async def _git(view, common, root, argv, limit, *, timeout=20):
    executable = shutil.which('git')
    if not executable: raise_code('PERS-202', reason='git_executable_unavailable')
    env = {k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT'}}
    env.update(HOME=str(view), USERPROFILE=str(view), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_DIR=str(view), GIT_WORK_TREE=str(root), GIT_OBJECT_DIRECTORY=str(common/'objects'),
               GIT_INDEX_FILE=str(view/'index'), GIT_OPTIONAL_LOCKS='0', GIT_NO_LAZY_FETCH='1',
               GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='', GIT_NO_REPLACE_OBJECTS='1', GIT_ATTR_NOSYSTEM='1')
    options = {'creationflags': 0x08000000} if os.name == 'nt' else {}
    process = await asyncio.create_subprocess_exec(executable, '--no-pager', '-c', 'core.hooksPath='+os.devnull,
        '-c', 'core.fsmonitor=false', *argv, cwd=view, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **options)
    raw = bytearray()
    async def collect():
        while chunk := await process.stdout.read(8192):
            raw.extend(chunk)
            if len(raw) > limit: raise_code('EVT-100', reason='git_output_limit')
        if await process.wait(): _unavailable()
    try:
        await asyncio.wait_for(collect(), timeout)
    except BaseException:
        if process.returncode is None: process.kill()
        # wait() alone can deadlock when the killed child's stdout pipe is full.
        # Drain the bounded pipe remainder, with an independent cleanup deadline.
        try: await asyncio.wait_for(process.communicate(),2)
        except TimeoutError: raise_code('PERS-202',reason='git_reader_exit_unconfirmed')
        raise
    return bytes(raw)


def _records(raw, *, index=False):
    result, folded = {}, set()
    if raw and not raw.endswith(b'\0'): _unavailable()
    for record in raw.split(b'\0')[:-1]:
        try:
            header, name = record.split(b'\t', 1)
            fields = header.split(b' ')
            if index:
                tag, mode, oid, stage = fields
                if tag != b'H' or stage != b'0': _unavailable()  # sparse/assume-unchanged/unmerged unsupported
            else:
                mode, type_, oid = fields
                if type_ != b'blob': _unavailable()
            if mode not in (b'100644', b'100755') or not OID.fullmatch(oid): _unavailable()
            path = name.decode('utf-8')
            if path.casefold() in folded: _unavailable()
            folded.add(path.casefold())
            result[path] = {'git_oid': oid.decode('ascii'), 'mode': int(mode, 8)}
            if len(result) > MAX_METADATA_FILES: raise_code('EVT-100', reason='git_entry_limit')
        except (ValueError, UnicodeError): _unavailable()
    return result


async def preview_git_layers(source_row, metadata_row, command, *, capture=None,inventory=None,before_body_read=None):
    """Read explicitly selected UTF-8 bytes in memory; return hashes, never bodies."""
    from pyharness.application.workspace_store import canonical, digest_of
    from pyharness.application.workspace_git_contracts import GitSourcePreview
    root = Path(source_row['grant']['canonical_root'])
    read_guard = capture.before_body_read if capture is not None else before_body_read
    def selected(name):
        if read_guard is None: return read_selected(root,name)
        return read_selected(root,name,before_read=read_guard)
    initial = {}
    if inventory is not None and capture is not None:_unavailable()
    if inventory is None:
        # Reject every selected name before reading even the first selected body.
        for name in command.paths:
            check_content(name.encode('utf-8'))
        observation = observe_selection(source_row, command)
        for item in observation['items']:
            if item['status'] == 'excluded' and item['reason'] != 'file_unavailable': _unavailable()
        for number,name in enumerate(command.paths):
            path = _safe(root,name)
            try: path.lstat()
            except FileNotFoundError:
                if observation['items'][number]['status'] == 'accepted': raise_code('EVT-101',reason='git_worktree_changed')
                initial[name] = None
            else:
                definition,raw = selected(name)
                check_content(raw)
                observed = observation['items'][number]
                if (observed['status'] != 'accepted' or definition.size_bytes != observed['size_bytes']
                        or definition.mtime_ns != observed['mtime_ns']):
                    raise_code('EVT-101',reason='git_worktree_changed')
                initial[name] = definition  # full inode/device/mode and byte hash, not just size/mtime
    metadata_root = Path(metadata_row['grant']['canonical_root']) if metadata_row else None
    try:
        common, gitdir, kind, pointer = _layout(root, metadata_root)
        _metadata_boundary(common)
        head, index = _head(common, gitdir), _index(common, gitdir)
        temporary_root = Path(tempfile.gettempdir()).resolve()
        protected_roots = (root.resolve(strict=True), common.resolve(strict=True))
        if any(temporary_root.is_relative_to(protected) for protected in protected_roots):
            _unavailable()  # do not even create a directory inside a read-only source
        with tempfile.TemporaryDirectory(prefix='ph-git-preview-', dir=temporary_root) as owned:
            view = Path(owned)
            resolved_view = view.resolve(strict=True)
            resolved_view.relative_to(temporary_root)  # cleanup confined to this owned subtree
            if resolved_view == temporary_root or any(
                    resolved_view.is_relative_to(protected) or protected.is_relative_to(resolved_view)
                    for protected in protected_roots):
                _unavailable()
            (view/'objects').mkdir(); (view/'refs').mkdir()
            (view/'HEAD').write_bytes(head+b'\n')
            config = '[core]\n bare = true\n repositoryformatversion = '+('1' if len(head)==64 else '0')+'\n'
            if len(head)==64: config += '[extensions]\n objectformat = sha256\n'
            (view/'config').write_text(config,encoding='utf-8')
            if index is not None: (view/'index').write_bytes(index)
            commit = (await _git(view,common,root,['rev-parse','--verify','HEAD^{commit}'],128)).strip()
            if not OID.fullmatch(commit): _unavailable()
            heads = _records(await _git(view,common,root,['ls-tree','-rz','--full-tree',commit.decode('ascii')],MAX_RESPONSE_BYTES))
            staged = _records(await _git(view,common,root,['ls-files','--stage','--sparse','-v','-z'],MAX_RESPONSE_BYTES),index=True)
            if inventory is not None:
                return await inventory.observe(source_row,metadata_row,root,metadata_root,common,gitdir,kind,pointer,head,index,view,commit,heads,staged)
            if capture is not None:
                tracked=tuple(sorted(set(heads)|set(staged)))
                if not set(tracked)<=set(command.paths):
                    raise_code('EVT-100',reason='git_tracked_selection_incomplete')
                capture.tracked_paths=tracked
            for records in (heads,staged):
                spellings = {name.casefold():name for name in records}
                if any(name.casefold() in spellings and spellings[name.casefold()]!=name for name in command.paths):
                    _unavailable()  # never let Windows casing turn a tracked path into untracked
            cache, sizes, identities, items = {}, {}, {}, []
            def size_bound(digest, size):
                sizes[digest] = size
                if sum(sizes.values()) > LIMIT: raise_code('EVT-100',reason='git_content_limit')
            async def layer(record):
                if record is None: return None
                oid = record['git_oid']
                if oid not in cache:
                    if read_guard is not None: read_guard()
                    size = await _git(view,common,root,['cat-file','-s',oid],64)
                    if not re.fullmatch(rb'[0-9]+\n?',size) or int(size)>LIMIT: raise_code('EVT-100',reason='git_content_limit')
                    if read_guard is not None: read_guard()
                    raw = await _git(view,common,root,['cat-file','blob',oid],LIMIT)
                    if len(raw)!=int(size): raise_code('EVT-101',reason='git_object_changed')
                    algorithm = hashlib.sha1 if len(oid)==40 else hashlib.sha256
                    if algorithm(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=oid: raise_code('EVT-101',reason='git_object_changed')
                    check_content(raw)
                    if raw.startswith(b'version https://git-lfs.github.com/spec/v1'): _unavailable()
                    digest = hashlib.sha256(raw).hexdigest()
                    size_bound(digest,len(raw))
                    if capture is not None: capture.contents[digest]=raw
                    cache[oid] = {'digest':digest,'size_bytes':len(raw)}
                return {**record,**cache[oid]}
            for number,name in enumerate(command.paths):
                h, i = await layer(heads.get(name)), await layer(staged.get(name))
                path = _safe(root,name)
                try: path.lstat()
                except FileNotFoundError:
                    if initial[name] is not None: raise_code('EVT-101',reason='git_worktree_changed')
                    if h is None and i is None: _unavailable()
                    working = None
                else:
                    definition, raw = selected(name)
                    if definition != initial[name]: raise_code('EVT-101',reason='git_worktree_changed')
                    observed = observation['items'][number]
                    if (observed['status'] != 'accepted' or definition.size_bytes != observed['size_bytes']
                            or definition.mtime_ns != observed['mtime_ns']):
                        raise_code('EVT-101',reason='git_worktree_changed')
                    check_content(raw)
                    if raw.startswith(b'version https://git-lfs.github.com/spec/v1'): _unavailable()
                    size_bound(definition.sha256,len(raw))
                    if capture is not None: capture.contents[definition.sha256]=raw
                    algorithm = hashlib.sha1 if len(head)==40 else hashlib.sha256
                    working = {'digest':definition.sha256,'size_bytes':len(raw),'mode':0o100755 if definition.mode & 0o111 else 0o100644,
                               'native_mode':definition.mode,
                               'git_oid':algorithm(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}
                    identities[name] = definition
                items.append({'input_index':number,'path':name,'head':h,'index':i,'working':working,
                              'deletion_marker':working is None,'index_deletion_marker':h is not None and i is None,
                              'selected_untracked':i is None and working is not None})
            # This verifier also runs after private capture writes, before commit.
            def verify():
                for item in items:
                    name=item['path']
                    if item['working'] is None:
                        if _safe(root,name).exists(): raise_code('EVT-101',reason='git_worktree_changed')
                    elif selected(name)[0]!=identities[name]: raise_code('EVT-101',reason='git_worktree_changed')
                after_common,after_gitdir,after_kind,after_pointer=_layout(root,metadata_root)
                if ((after_common,after_gitdir,after_kind,after_pointer)!=(common,gitdir,kind,pointer)
                        or _head(common,gitdir)!=head or _index(common,gitdir)!=index):
                    raise_code('EVT-101',reason='git_metadata_changed')
                _metadata_boundary(common)
            verify()
            if capture is not None:
                capture.verify=verify
                capture.identity_digest=digest_of(canonical({'files':{name:value.model_dump() if value else None for name,value in initial.items()},
                    'pointer_digest':digest_of(pointer),'gitdir_relative':gitdir.relative_to(common).as_posix()}))
            result = {'schema_version':1,'status':'git_layers_observed','repository_kind':kind,
                      'project_id':source_row['grant']['project_id'],'grant_id':source_row['grant']['grant_id'],
                      'grant_revision':source_row['grant']['revision'],'source_seq':source_row['source_seq'],
                      'metadata_grant_id':metadata_row['grant']['grant_id'] if metadata_row else None,
                      'metadata_grant_revision':metadata_row['grant']['revision'] if metadata_row else None,
                      'head_oid':commit.decode('ascii'),'index_digest':digest_of(index or b''),'items':items,
                      'file_count':len(items),'unique_content_size_bytes':sum(sizes.values()),
                      'capture_stability':'stable_observed','scope':'selected_git_layers','persisted':False,'executable':False}
            result = GitSourcePreview.model_validate({**result,'observation_digest':'0'*64}).model_dump()
            result.pop('observation_digest')
            result['observation_digest'] = digest_of(canonical(result))
            if len(canonical(result))>MAX_RESPONSE_BYTES: raise_code('EVT-100',reason='git_response_limit')
            return result
    except (OSError, ValueError): _unavailable()
