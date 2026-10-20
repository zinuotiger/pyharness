"""Ephemeral root metadata observation; never a file access or execution grant."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field
from pyharness.application.source_grants import GrantModel, authorize_root
from pyharness.errors import PyHError, raise_code


class PreflightGrant(GrantModel):
    schema_version: Literal[1] = Field(...)
    expected_revision: int = Field(ge=1)


def check_root_metadata(row):
    """Use the issuance geometry boundary; do not enumerate or open source files."""
    grant = row['grant']
    try:
        observed = authorize_root(grant['canonical_root'])
    except PyHError:
        raise_code('GRD-403', reason='source_root_unavailable')
    if observed['root_identity'] != grant['root_identity']:
        raise_code('GRD-403', reason='source_root_identity_changed')
    return {'schema_version': 1, 'project_id': grant['project_id'], 'grant_id': grant['grant_id'],
            'revision': grant['revision'], 'root_identity': grant['root_identity'],
            'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'metadata_match',
            'digest': row['digest'], 'source_seq': row['source_seq']}
