"""Explicit-selection capture, never a Git/whole-workspace or OS snapshot."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
import stat
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pyharness.application.source_grants import GrantModel, GrantId
from pyharness.application.source_selection import SelectionPreview, observe_selection
from pyharness.application.workspace_contracts import Digest, ProjectId
from pyharness.core.sandbox import safe_path
from pyharness.errors import PyHError, raise_code

LIMIT = 10 * 1024 * 1024
POLICY = 'reject-secrets-utf8-v1'
SnapshotId = Annotated[str, Field(pattern=r'^ss-[0-9a-f]{16}$')]


class CaptureSnapshot(SelectionPreview):
    snapshot_id: SnapshotId
    confirmation_digest: Digest
    policy: Literal['reject-secrets-utf8-v1']


class SnapshotFile(GrantModel):
    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=LIMIT)
    sha256: Digest
    mtime_ns: str = Field(pattern=r'^[0-9]{1,24}$')
    mode: int = Field(ge=0, le=511)
    device: int = Field(ge=0)
    inode: int = Field(gt=0)


class SourceSnapshot(GrantModel):
    snapshot_id: SnapshotId
    project_id: ProjectId
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    root_identity: Digest
    revision: Literal[1] = 1
    kind: Literal['explicit_selection'] = 'explicit_selection'
    policy: Literal['reject-secrets-utf8-v1'] = POLICY
    capture_stability: Literal['stable_observed'] = 'stable_observed'
    confirmation_digest: Digest
    total_size_bytes: int = Field(ge=0, le=LIMIT)
    files: list[SnapshotFile] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def coherent(self):
        if (len({f.path.casefold() for f in self.files}) != len(self.files)
                or sum(f.size_bytes for f in self.files) != self.total_size_bytes
                or self.confirmation_digest != selection_digest(self.project_id, self.grant_id,
                    self.grant_revision, self.root_identity, self.files)):
            raise ValueError('incoherent snapshot')
        return self


def selection_digest(project_id, grant_id, revision, root_identity, files):
    from pyharness.application.workspace_store import canonical, digest_of
    return digest_of(canonical({'project_id': project_id, 'grant_id': grant_id,
        'grant_revision': revision, 'root_identity': root_identity, 'policy': POLICY,
        'files': [f.model_dump() for f in files]}))


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IMODE(info.st_mode), info.st_nlink)


def check_content(raw):
    from pyharness.config import redact
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise_code('GRD-403', reason='snapshot_content_rejected')
    if (any(ord(c) < 32 and c not in '\t\r\n' for c in text)
            or redact(text) != text
            or re.search(r'(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_|github_pat_|AKIA|ASIA)[A-Za-z0-9_]+|\bBearer\s+\S+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|[a-z][a-z0-9+.-]*://[^\s/]+:[^\s/]+@|["\x27]?(?:password|passwd|pwd|(?:api[_-]?)?key|(?:access[_-]?|refresh[_-]?)?token|secret|client[_-]?secret)["\x27]?\s*[:=]\s*\S+)', text)):
        raise_code('GRD-403', reason='snapshot_content_rejected')


def _checked_file(root, name, *, before_read=None, max_bytes=LIMIT):
    """Bound the descriptor read and compare it with safe path metadata on both sides."""
    gate_failed=False
    try:
        path = safe_path(root, name)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or not before.st_ino or before.st_nlink != 1:
            raise_code('GRD-403', reason='snapshot_file_rejected')
        if before.st_size > max_bytes:
            raise_code('EVT-100', reason='snapshot_size_limit')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or identity(before) != identity(opened):
                raise_code('EVT-101', reason='snapshot_source_changed')
            if before_read is not None:
                try:before_read()
                except BaseException:
                    gate_failed=True
                    raise
            # Authority may perform blocking root/account I/O. Bind the FD and
            # current path again after that I/O, immediately before first bytes.
            if identity(before)!=identity(os.fstat(stream.fileno())) or identity(before)!=identity(safe_path(root,name).lstat()):
                raise_code('EVT-101',reason='snapshot_source_changed')
            raw = stream.read(max_bytes + 1)
            after_fd = os.fstat(stream.fileno())
        after = safe_path(root, name).lstat()
        if len(raw) != before.st_size or identity(before) != identity(after_fd) or identity(before) != identity(after):
            raise_code('EVT-101', reason='snapshot_source_changed')
        return SnapshotFile(path=name, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
            mtime_ns=str(before.st_mtime_ns), mode=stat.S_IMODE(before.st_mode),
            device=before.st_dev, inode=before.st_ino), raw
    except OSError:
        if gate_failed:raise
        raise_code('GRD-403', reason='snapshot_file_unavailable')
    except PyHError as exc:
        if gate_failed:raise
        if exc.code not in {'GRD-403', 'EVT-100', 'EVT-101'}:
            raise_code('GRD-403', reason='snapshot_file_rejected')
        raise


def read_selected(root,name,*,before_read=None):
    return _checked_file(root,name,before_read=before_read)


def checked_private_bytes(path,*,limit=LIMIT,before_read=None):
    """Same checked FD boundary, distinct private-storage semantics; never host fallback."""
    gate_failed=False
    def gate():
        nonlocal gate_failed
        try:before_read()
        except BaseException:
            gate_failed=True
            raise
    try:return _checked_file(path.parent,path.name,max_bytes=limit,before_read=gate if before_read is not None else None)[1]
    except (OSError,PyHError):
        if gate_failed:raise
        raise_code('PERS-202',reason='private_blob_unavailable')


def collect_selection(row, command, *, before_read=None):
    observed = observe_selection(row, command)
    if observed['summary']['excluded']:
        raise_code('GRD-403', reason='snapshot_selection_rejected')
    root = Path(row['grant']['canonical_root'])
    files, contents = [], {}
    for name in command.paths:
        check_content(name.encode('utf-8'))
        file, raw = read_selected(root, name, before_read=before_read) if before_read is not None else read_selected(root, name)
        check_content(raw)
        files.append(file)
        if sum(f.size_bytes for f in files) > LIMIT:
            raise_code('EVT-100', reason='snapshot_size_limit')
        contents[file.sha256] = raw
    verify_selection(row, files, before_read=before_read)
    grant = row['grant']
    digest = selection_digest(grant['project_id'], grant['grant_id'], grant['revision'], grant['root_identity'], files)
    return files, contents, digest


def verify_selection(row, files, *, before_read=None):
    from pyharness.application.source_preflight import check_root_metadata
    check_root_metadata(row)
    root = Path(row['grant']['canonical_root'])
    for file in files:
        current, _ = read_selected(root, file.path, before_read=before_read) if before_read is not None else read_selected(root, file.path)
        if current != file:
            raise_code('EVT-101', reason='snapshot_source_changed')
    check_root_metadata(row)


def snapshot_summary(row):
    s = row['snapshot']
    return {**{k: s[k] for k in ('snapshot_id', 'project_id', 'grant_id', 'revision', 'kind', 'policy',
        'capture_stability', 'total_size_bytes', 'confirmation_digest')}, 'file_count': len(s['files']),
        'digest': row['digest'], 'source_seq': row['source_seq']}
