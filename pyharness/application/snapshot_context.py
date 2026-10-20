"""Bounded context references from committed copies, never actual model context or live source."""
from pyharness.application.source_preflight import PreflightGrant
from pyharness.application.source_snapshot import SnapshotId
from pyharness.application.workspace_contracts import Digest


class SnapshotContextPreview(PreflightGrant):
    snapshot_id: SnapshotId
    snapshot_digest: Digest


def build_context_preview(row):
    from pyharness.application.workspace_store import canonical, digest_of
    snapshot = row['snapshot']
    result = {
        'schema_version': 1, 'project_id': snapshot['project_id'], 'grant_id': snapshot['grant_id'],
        'grant_revision': snapshot['grant_revision'], 'snapshot_id': snapshot['snapshot_id'],
        'snapshot_revision': snapshot['revision'], 'snapshot_digest': row['digest'],
        'source_seq': row['source_seq'], 'policy': snapshot['policy'], 'status': 'context_observed',
        'items': [{'input_index': index, 'path': file['path'], 'size_bytes': file['size_bytes'],
                   'content_digest': file['sha256']} for index, file in enumerate(snapshot['files'])],
        'file_count': len(snapshot['files']), 'total_size_bytes': snapshot['total_size_bytes'],
    }
    return {**result, 'context_digest': digest_of(canonical(result))}
