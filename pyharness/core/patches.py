"""Bounded patch manifests: baseline checks, path checks and failure rollback."""
from __future__ import annotations
import hashlib
from pathlib import Path
from pyharness.core.sandbox import fail, safe_path
from pyharness.core.tenant_settings import TenantSettingsStore


def digest(data: bytes | None):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def apply_manifest(root: Path, changes: list[dict], *, writer=None):
    if not changes or len(changes) > 50:
        fail('invalid_patch')
    writer = writer or TenantSettingsStore._atomic_write
    prepared, paths, total = [], set(), 0
    for change in changes:
        if set(change) != {'path','before_sha256','after'} or (change['after'] is not None and not isinstance(change['after'],str)):
            fail('invalid_patch')
        path = safe_path(root,change['path'])
        if path in paths:
            fail('invalid_patch')
        paths.add(path)
        if path.exists() and (not path.is_file() or path.stat().st_size>1048576):
            fail('invalid_patch')
        before = path.read_bytes() if path.exists() else None
        if digest(before) != change['before_sha256']:
            fail('patch_baseline_changed',change['path'])
        after=change['after'].encode('utf-8') if change['after'] is not None else None
        total += len(after or b'')
        if total>5*1048576:
            fail('patch_too_large')
        prepared.append((path,before,after,change['path']))
    completed=[]
    created_dirs=[]
    try:
        for path,before,after,relative in prepared:
            safe_path(root,relative)
            if digest(path.read_bytes() if path.exists() else None)!=digest(before):
                fail('patch_baseline_changed',relative)
            missing=[]
            parent=path.parent
            while not parent.exists() and parent!=root:
                missing.append(parent);parent=parent.parent
            path.parent.mkdir(parents=True,exist_ok=True)
            created_dirs.extend(reversed(missing))
            # Include the current path: an injected writer can fail after replacement.
            completed.append((path,before,after,relative))
            if after is None:
                path.unlink(missing_ok=True)
            else:
                writer(path,after)
    except BaseException as primary:
        errors=[]
        for path,before,after,relative in reversed(completed):
            try:
                safe_path(root,relative)
                current=path.read_bytes() if path.exists() else None
                if current not in (after,before):
                    fail('patch_rollback_conflict',relative)
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    TenantSettingsStore._atomic_write(path,before)
            except BaseException as exc:
                errors.append(exc)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass  # Concurrently populated directories must not be removed.
        if errors:
            raise BaseExceptionGroup('patch rollback requires recovery',[primary,*errors])
        raise
    return {'changed_files':[relative for _,_,_,relative in prepared]}
