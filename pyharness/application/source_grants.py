"""Read-only source authorization definitions. Never reads source contents."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyharness.application.workspace_contracts import Digest, ProjectId
from pyharness.core.sandbox import safe_path
from pyharness.errors import PyHError, raise_code

GrantId = Annotated[str, Field(pattern=r'^sg-[0-9a-f]{16}$')]


class GrantModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Literal[1] = 1

    @field_validator('schema_version', mode='before')
    @classmethod
    def version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('unsupported grant version')
        return value


class IssueGrant(GrantModel):
    grant_id: GrantId
    expected_revision: int = Field(ge=0, le=0)
    source_path: str = Field(min_length=1, max_length=1024)
    actions: list[Literal['read']] = Field(min_length=1, max_length=1)
    expires_at: str = Field(min_length=1, max_length=40)

    @field_validator('expires_at')
    @classmethod
    def utc_expiry(cls, value):
        parse_expiry(value)
        return value


class RevokeGrant(GrantModel):
    expected_revision: int = Field(ge=1)


class SourceGrant(GrantModel):
    grant_id: GrantId
    project_id: ProjectId
    revision: int = Field(ge=1)
    root_identity: Digest
    canonical_root: str = Field(min_length=1, max_length=1024)
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    actions: list[Literal['read']] = Field(min_length=1, max_length=1)
    expires_at: str = Field(min_length=1, max_length=40)
    revoked: bool = False
    approved_by: str = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def coherent_identity(self):
        if self.root_identity != root_identity(self.canonical_root, self.device, self.inode):
            raise ValueError('incoherent root identity')
        return self

    @field_validator('expires_at')
    @classmethod
    def utc_expiry(cls, value):
        parse_expiry(value)
        return value


def parse_expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not value.endswith(('Z', '+00:00')) or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise ValueError('expiry must be UTC ISO8601') from None


def root_identity(path: str, device: int, inode: int) -> str:
    data = {'normalized_root': os.path.normcase(path), 'device': device, 'inode': inode}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def authorize_root(value: str) -> dict:
    """One geometry boundary via safe_path, rooted at a local volume anchor."""
    try:
        text = value.replace('\\', '/')
        path = Path(text)
        if (text.startswith('//') or '\x00' in text or not path.is_absolute()
                or not path.anchor or any(p in {'.', '..'} for p in text.split('/'))):
            raise ValueError
        relative = path.relative_to(path.anchor).as_posix()
        checked = safe_path(Path(path.anchor), relative)
        resolved = checked.resolve(strict=True)
        info = resolved.stat()
        if not resolved.is_dir() or not info.st_ino:
            raise ValueError
        canonical_root = str(resolved)
        return {'canonical_root': canonical_root, 'device': info.st_dev, 'inode': info.st_ino,
                'root_identity': root_identity(canonical_root, info.st_dev, info.st_ino)}
    except (OSError, ValueError, PyHError):
        raise_code('EVT-100', reason='source_root_invalid')


def grant_summary(row):
    grant = row['grant']
    return {**{k: grant[k] for k in ('grant_id', 'revision', 'root_identity', 'actions', 'expires_at', 'revoked')},
            'digest': row['digest'], 'source_seq': row['source_seq']}
