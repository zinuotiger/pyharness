"""Strict public resource envelopes and bounded immutable definition shapes."""
import pytest
from pydantic import ValidationError
from pyharness.application.context_manifest import ContextManifest, TaskVersionReference
from pyharness.events.payload import WorkspaceResourceCommittedPayload


@pytest.mark.parametrize('kind,prefix', [('context_manifest','cm-'), ('task_definition','pt-')])
@pytest.mark.parametrize('invalid', [{}, {'schema_version':True}, {'revision':True}, {'parent_revision':1},
                                   {'resource_id':'p-0123456789abcdef'}, {'extra':True}])
def test_resource_envelope_strict_dispatch(kind, prefix, invalid):
    body=dict(resource_kind=kind,resource_id=prefix+'0123456789abcdef',revision=1,parent_revision=0,
              blob_ref='a'*64,digest='a'*64,command_id='synthetic',request_digest='b'*64,actor_ref='synthetic')
    if invalid:
        with pytest.raises(ValidationError): WorkspaceResourceCommittedPayload(**{**body,**invalid})
    else: assert WorkspaceResourceCommittedPayload(**body).resource_kind==kind


def definition(**changes):
    return dict(manifest_id='cm-0123456789abcdef',revision=1,project_id='p-0123456789abcdef',project_revision=1,
                grant_id='sg-0123456789abcdef',grant_revision=1,snapshot_id='ss-0123456789abcdef',snapshot_digest='a'*64,
                snapshot_source_seq=4,items=[dict(input_index=0,path='synthetic.txt',size_bytes=1,content_digest='b'*64)],
                total_size_bytes=1,created_by='synthetic',**changes)


@pytest.mark.parametrize('change', [dict(snapshot_revision=True),dict(items=[]),dict(total_size_bytes=2),
    dict(items=[dict(input_index=1,path='x.txt',size_bytes=1,content_digest='b'*64)]),
    dict(items=[dict(input_index=i,path=name,size_bytes=1,content_digest='b'*64) for i,name in enumerate(['A.txt','a.txt'])],total_size_bytes=2)])
def test_definition_bounds_and_coherence(change):
    with pytest.raises(ValidationError): ContextManifest.model_validate({**definition(),**change})


@pytest.mark.parametrize('change', [dict(revision=True),dict(source_seq=True),dict(digest='bad'),dict(extra=1)])
def test_task_version_reference_rejects_forgery_shape(change):
    with pytest.raises(ValidationError):
        TaskVersionReference.model_validate({**dict(product_task_id='pt-0123456789abcdef',revision=1,digest='a'*64,source_seq=1),**change})
