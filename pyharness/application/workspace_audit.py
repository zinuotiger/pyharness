"""Versioned audit projection and authenticated, restart-stable keyset cursors.

Tokens are integrity-protected, not bearer authorization. Only identity digests
are encoded; callers must reauthorize every request. Reads still replay the log.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Literal,Annotated

from pydantic import BaseModel, ConfigDict, Field
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.source_grants import GrantId
from pyharness.application.source_snapshot import SnapshotId
from pyharness.application.context_manifest import ManifestId, ProductTaskId
from pyharness.application.workspace_snapshot import WorkspaceSnapshotId
from pyharness.application.workspace_owned_inputs import WorkspaceInputId
from pyharness.application.document_contracts import DocumentId, CitationId, DeliveryId
from pyharness.application.run_attempt_contracts import AttemptId
from pyharness.application.execution_contracts import ExecutionId
from pyharness.application.correction_contracts import CorrectionId
from pyharness.application.index_repair_contracts import IndexRepairId
from pyharness.application.task_graph_contracts import TaskGraphId,TaskGraphRunId
from pyharness.errors import raise_code


class AuditQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    pagination_version: int = Field(default=1, ge=1, le=1)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    project_id: ProjectId | None = None


class Watermark(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    seq: int = Field(ge=1)
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class CursorClaims(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    v: int = Field(ge=1, le=1)
    tenant: str = Field(pattern=r'^[0-9a-f]{64}$')
    subject: str = Field(pattern=r'^[0-9a-f]{64}$')
    project_id: ProjectId | None
    limit: int = Field(ge=1, le=200)
    watermark: int = Field(ge=1)
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    after: int = Field(ge=1)
    issued: int = Field(ge=1)


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    seq: int = Field(ge=1)
    type: Literal['session.created', 'workspace.resource_committed']
    ts: str
    resource_kind: Literal['project', 'source_grant', 'source_snapshot', 'context_manifest', 'task_definition', 'workspace_snapshot', 'workspace_input', 'document', 'citation', 'delivery_definition', 'run_attempt', 'execution_authorization','execution_cancellation','execution_correction','index_repair','task_graph','task_graph_run','task_graph_dispatch','task_graph_cancellation','delivery_package','role_contract','role_handoff','handoff_ack','delivery_acceptance','delivery_export','handoff_budget','writeback_grant','merge_plan','writeback_application','merge_export','regression_case','regression_dataset','experiment_preparation','experiment_definition','experiment_run','experiment_cancellation','experiment_report'] | None = None
    resource_id: ProjectId | GrantId | SnapshotId | ManifestId | ProductTaskId | WorkspaceSnapshotId | WorkspaceInputId | DocumentId | CitationId | DeliveryId | AttemptId | ExecutionId | CorrectionId | IndexRepairId | TaskGraphId | TaskGraphRunId | Annotated[str,Field(pattern=r'^(?:dp|rc|ho|ha|av|xp|hb|wg|mp|wa|we)-[0-9a-f]{16}$')] | Annotated[str,Field(pattern=r'^(?:case|ds|prp|exp|er|ecx|epr)-[0-9a-f]{16}$')] | Annotated[str,Field(pattern=r'^g[dc]-[0-9a-f]{16}$')] | None = None
    revision: int | None = Field(default=None, ge=1)
    digest: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class AuditPage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1] = 1
    events: list[AuditEvent] = Field(max_length=200)
    total: int = Field(ge=0)
    page_count: int = Field(ge=0, le=200)
    has_more: bool
    next_cursor: str | None
    source_watermark: Watermark


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _encode(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _sign(claims, key):
    body = _encode(_canonical(claims))
    return body + '.' + _encode(hmac.digest(key, body.encode('ascii'), 'sha256'))


def page_events(events, query: AuditQuery, *, tenant: str, subject: str, key: bytes):
    """Build one bounded public page from an already validated complete log."""
    # Keyed, domain-separated identity bindings resist offline tenant/username
    # dictionary checks even though the signed token is not encrypted.
    binding = {'v': 1, 'tenant': hmac.digest(key, _canonical(['tenant', tenant]), 'sha256').hex(),
               'subject': hmac.digest(key, _canonical(['subject', subject]), 'sha256').hex(),
               'project_id': query.project_id, 'limit': query.limit}
    watermark = len(events)
    after = 0
    issued = int(time.time())
    if query.cursor:
        try:
            body, signature = query.cursor.split('.')
            if not hmac.compare_digest(_encode(hmac.digest(key, body.encode('ascii'), 'sha256')), signature):
                raise ValueError('signature')
            data = base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True)
            claims = CursorClaims.model_validate_json(data).model_dump()
            if _encode(_canonical(claims)) != body:
                raise ValueError('canonical')
            if any(claims.get(k) != value for k, value in binding.items()):
                raise ValueError('binding')
            watermark, after, issued = claims['watermark'], claims['after'], claims['issued']
            if issued > int(time.time()) or int(time.time()) - issued > 3600:
                raise_code('EVT-100', reason='audit_cursor_expired')
            if not (0 < after < watermark <= len(events)):
                raise ValueError('range')
            if claims['digest'] != _digest(events[:watermark]):
                raise ValueError('watermark')
        except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error):
            raise_code('EVT-100', reason='audit_cursor_invalid')
    frozen = events[:watermark]
    digest = _digest(frozen)
    matching = [event for event in frozen if query.project_id is None or
                event['payload'].get('resource_id') == query.project_id]
    if query.cursor and after not in {event['seq'] for event in matching[:-1]}:
        raise_code('EVT-100', reason='audit_cursor_invalid')
    selected = [event for event in matching if event['seq'] > after][:query.limit]
    has_more = bool(selected and selected[-1]['seq'] < matching[-1]['seq'])
    cursor = _sign({**binding, 'watermark': watermark, 'digest': digest,
                    'after': selected[-1]['seq'], 'issued': issued}, key) if has_more else None
    public = [AuditEvent(seq=e['seq'], type=e['type'], ts=e['ts'],
                         **{k: e['payload'][k] for k in ('resource_kind', 'resource_id', 'revision', 'digest')
                            if k in e['payload']}) for e in selected]
    return AuditPage(events=public, total=len(matching), page_count=len(public), has_more=has_more,
                     next_cursor=cursor, source_watermark=Watermark(seq=watermark, digest=digest)).model_dump()
