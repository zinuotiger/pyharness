"""Explicit selected-file inputs; no invented Git lineage or live host fallback."""
from typing import Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_owned_inputs import ManifestReference,WorkspaceInputId,PreparedFile,DeletedInput
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.source_grants import GrantId
from pyharness.application.source_snapshot import SnapshotId,LIMIT
from pyharness.application.context_manifest import TaskVersionReference
from pyharness.application.source_selection import selection_name_reason


class SelectedSourceReference(StrictModel):
    source_kind:Literal['source_snapshot']='source_snapshot'
    snapshot_id:SnapshotId
    revision:Literal[1]=1
    digest:Digest
    source_seq:int=Field(ge=1)
    grant_id:GrantId
    grant_revision:int=Field(ge=1)
    grant_source_seq:int=Field(ge=1)
    root_identity:Digest
    confirmation_digest:Digest

    @field_validator('revision',mode='before')
    @classmethod
    def one(cls,value):
        if type(value) is not int or value!=1:raise ValueError('immutable selected source revision')
        return value


class PrepareSelectedInput(StrictModel):
    schema_version:Literal[2]=2
    input_id:WorkspaceInputId
    project_revision:int=Field(ge=1)
    manifest_ref:ManifestReference

    @field_validator('schema_version',mode='before')
    @classmethod
    def two(cls,value):
        if type(value) is not int or value!=2:raise ValueError('explicit selected input version two')
        return value


def check_selected_set(files,total):
    paths=[f.path for f in files]
    if (not files or len(files)>200 or [f.input_index for f in files]!=list(range(len(files)))
        or len({p.casefold() for p in paths})!=len(paths) or any(selection_name_reason(p,set()) for p in paths)
        or sum(f.size_bytes for f in files)!=total):raise ValueError('incoherent explicit selected input set')


class SelectedWorkspaceInput(PrepareSelectedInput):
    revision:Literal[1]=1
    project_id:ProjectId
    created_by:str=Field(min_length=1,max_length=200)
    source_ref:SelectedSourceReference
    task_ref:TaskVersionReference|None
    files:list[PreparedFile]=Field(min_length=1,max_length=200)
    deletions:list[DeletedInput]=Field(default_factory=list,max_length=0)
    total_size_bytes:int=Field(ge=0,le=LIMIT)
    policy:Literal['private-selected-copy-v1']='private-selected-copy-v1'

    @field_validator('revision',mode='before')
    @classmethod
    def one(cls,value):
        if type(value) is not int or value!=1:raise ValueError('immutable selected input revision')
        return value

    @model_validator(mode='after')
    def coherent(self):
        check_selected_set(self.files,self.total_size_bytes);return self


class SelectedInputDescriptor(StrictModel):
    schema_version:Literal[1]=1
    status:Literal['selected_inputs_observed']='selected_inputs_observed'
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    manifest_ref:ManifestReference
    source_ref:SelectedSourceReference
    task_ref:TaskVersionReference|None
    files:list[PreparedFile]=Field(min_length=1,max_length=200)
    deletions:list[DeletedInput]=Field(default_factory=list,max_length=0)
    total_size_bytes:int=Field(ge=0,le=LIMIT)
    policy:Literal['private-selected-copy-v1']='private-selected-copy-v1'
    file_count:int=Field(ge=1,le=200)
    deletion_count:Literal[0]=0
    persisted:Literal[False]=False
    executable:Literal[False]=False
    descriptor_digest:Digest

    @model_validator(mode='after')
    def coherent(self):
        check_selected_set(self.files,self.total_size_bytes)
        if self.file_count!=len(self.files):raise ValueError('selected descriptor count')
        return self


def selected_parts(manifest,snapshot,grant):
    from pyharness.application.snapshot_context import build_context_preview
    cm=manifest['manifest'];s=snapshot['snapshot'];g=grant['grant']
    if (cm['schema_version']!=1 or s['project_id']!=cm['project_id'] or s['grant_id']!=cm['grant_id']
        or s['grant_revision']!=cm['grant_revision'] or g['revision']!=s['grant_revision']
        or g['project_id']!=cm['project_id'] or g['root_identity']!=s['root_identity']
        or snapshot['digest']!=cm['snapshot_digest'] or snapshot['source_seq']!=cm['snapshot_source_seq']
        or build_context_preview(snapshot)['items']!=cm['items']):raise ValueError('selected source binding')
    source=SelectedSourceReference(snapshot_id=s['snapshot_id'],digest=snapshot['digest'],source_seq=snapshot['source_seq'],
        grant_id=s['grant_id'],grant_revision=s['grant_revision'],grant_source_seq=grant['source_seq'],
        root_identity=s['root_identity'],confirmation_digest=s['confirmation_digest'])
    return {'source_ref':source.model_dump(),'task_ref':cm['task_ref'],
        'files':[{**item,'native_mode':s['files'][item['input_index']]['mode'],'applied_mode':384} for item in cm['items']],
        'deletions':[],'total_size_bytes':cm['total_size_bytes'],'policy':'private-selected-copy-v1'}


def derive_selected_inputs(project_id,command,manifest,snapshot,grant,actor):
    return SelectedWorkspaceInput(**command.model_dump(),project_id=project_id,created_by=actor,
        **selected_parts(manifest,snapshot,grant))
