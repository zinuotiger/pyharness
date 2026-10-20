"""Strict source discriminant and exact immutable selected-file geometry."""
import copy
import pytest
from pydantic import ValidationError
from pyharness.application.selected_owned_inputs import SelectedWorkspaceInput,SelectedInputDescriptor,PrepareSelectedInput


def definition():
    return {'schema_version':2,'input_id':'wi-0123456789abcdef','project_revision':1,
        'manifest_ref':{'manifest_id':'cm-0123456789abcdef','revision':1,'digest':'a'*64,'source_seq':4},
        'revision':1,'project_id':'p-0123456789abcdef','created_by':'synthetic-operator',
        'source_ref':{'source_kind':'source_snapshot','snapshot_id':'ss-0123456789abcdef','revision':1,'digest':'b'*64,'source_seq':3,
            'grant_id':'sg-0123456789abcdef','grant_revision':1,'grant_source_seq':2,'root_identity':'c'*64,'confirmation_digest':'d'*64},
        'task_ref':None,'files':[{'input_index':0,'path':'file.txt','size_bytes':1,'content_digest':'e'*64,'native_mode':0o644,'applied_mode':0o600}],
        'deletions':[],'total_size_bytes':1,'policy':'private-selected-copy-v1'}


def test_selected_strict_definition_and_observation_are_distinct():
    v=definition();assert SelectedWorkspaceInput.model_validate(v).model_dump()==v
    raw={k:v[k] for k in ('project_id','project_revision','manifest_ref','source_ref','task_ref','files','deletions','total_size_bytes','policy')}
    d=SelectedInputDescriptor(**raw,schema_version=1,status='selected_inputs_observed',file_count=1,deletion_count=0,persisted=False,executable=False,descriptor_digest='f'*64)
    assert 'input_id' not in d.model_dump() and d.persisted is False and d.executable is False
    with pytest.raises(ValidationError):SelectedInputDescriptor(**{**d.model_dump(),'file_count':2})


@pytest.mark.parametrize('fault',['bool-schema','bool-revision','bool-source-revision','git-ref','empty','duplicate-path','sparse','casefold','total','traversal','bad-mode','bad-applied','delete','too-many','oversize','extra-authority'])
def test_selected_definition_rejects_incoherent_or_forged_shape(fault):
    v=definition();f=v['files'][0]
    if fault=='bool-schema':v['schema_version']=True
    elif fault=='bool-revision':v['revision']=True
    elif fault=='bool-source-revision':v['source_ref']['revision']=True
    elif fault=='git-ref':v['source_ref']['head_oid']='a'*40
    elif fault=='empty':v['files']=[];v['total_size_bytes']=0
    elif fault in ('duplicate-path','casefold'):
        v['files'].append({**f,'input_index':1,'path':'FILE.txt' if fault=='casefold' else f['path']});v['total_size_bytes']=2
    elif fault=='sparse':f['input_index']=1
    elif fault=='total':v['total_size_bytes']=2
    elif fault=='traversal':f['path']='../outside.txt'
    elif fault=='bad-mode':f['native_mode']=512
    elif fault=='bad-applied':f['applied_mode']=0o777
    elif fault=='delete':v['deletions']=[{'input_index':1,'path':'deleted.txt'}]
    elif fault=='too-many':v['files']=[{**f,'input_index':i,'path':f'file-{i}.txt'} for i in range(201)];v['total_size_bytes']=201
    elif fault=='oversize':f['size_bytes']=v['total_size_bytes']=10485761
    else:v['destination']='C:/synthetic-outside'
    with pytest.raises(ValidationError):SelectedWorkspaceInput.model_validate(v)
