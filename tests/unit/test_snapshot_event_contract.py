"""GIVEN immutable snapshot commits WHEN revision changes THEN reject before append."""
import pytest
from pydantic import ValidationError
from pyharness.events.payload import WorkspaceResourceCommittedPayload, WorkspaceSourceSnapshotCommittedPayload

@pytest.mark.parametrize('model', [WorkspaceResourceCommittedPayload, WorkspaceSourceSnapshotCommittedPayload])
def test_snapshot_event_rejects_a_second_revision(model):
    with pytest.raises(ValidationError):
        model(resource_kind='source_snapshot', resource_id='ss-0123456789abcdef',
              revision=2, parent_revision=1, blob_ref='a'*64, digest='a'*64,
              command_id='synthetic', request_digest='b'*64, actor_ref='operator')
