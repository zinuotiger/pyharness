"""Immutable prepared input definitions; no execution or live directory authority."""
from typing import Annotated,Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.workspace_manifest import StrictModel,WorkspaceSourceReference
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.context_manifest import ManifestId,TaskVersionReference,ContextItem
from pyharness.application.source_snapshot import LIMIT
from pyharness.application.source_selection import selection_name_reason

WorkspaceInputId=Annotated[str,Field(pattern=r'^wi-[0-9a-f]{16}$')]


class ManifestReference(StrictModel):
    manifest_id:ManifestId
    revision:int=Field(ge=1,le=200)
    digest:Digest
    source_seq:int=Field(ge=1)


class PrepareWorkspaceInput(StrictModel):
    schema_version:Literal[1]=1
    input_id:WorkspaceInputId
    project_revision:int=Field(ge=1)
    manifest_ref:ManifestReference

    @field_validator('schema_version',mode='before')
    @classmethod
    def version(cls,value):
        if type(value) is not int or value!=1:raise ValueError('explicit preparation version one')
        return value


class PreparedFile(ContextItem):
    native_mode:int=Field(ge=0,le=511)
    applied_mode:Literal[384]=384


class DeletedInput(StrictModel):
    input_index:int=Field(ge=0,le=199)
    path:str=Field(min_length=1,max_length=1024)


class WorkspaceInput(PrepareWorkspaceInput):
    revision:Literal[1]=1
    project_id:ProjectId
    created_by:str=Field(min_length=1,max_length=200)
    source_ref:WorkspaceSourceReference
    task_ref:TaskVersionReference|None
    files:list[PreparedFile]=Field(max_length=200)
    deletions:list[DeletedInput]=Field(max_length=200)
    total_size_bytes:int=Field(ge=0,le=LIMIT)
    policy:Literal['private-working-copy-v1']='private-working-copy-v1'

    @field_validator('revision',mode='before')
    @classmethod
    def first_revision(cls,value):
        if type(value) is not int or value!=1:raise ValueError('immutable first revision')
        return value

    @model_validator(mode='after')
    def coherent(self):
        rows=[*self.files,*self.deletions];paths=[r.path for r in rows];indexes=[r.input_index for r in rows]
        if (not rows or len(rows)>200 or len({p.casefold() for p in paths})!=len(paths)
            or len(set(indexes))!=len(indexes) or sorted(indexes)!=list(range(len(rows)))
            or [f.input_index for f in self.files]!=sorted(f.input_index for f in self.files)
            or [f.input_index for f in self.deletions]!=sorted(f.input_index for f in self.deletions)
            or any(selection_name_reason(p,set()) for p in paths)
            or sum(f.size_bytes for f in self.files)!=self.total_size_bytes):raise ValueError('incoherent exact input set')
        return self


def derive_inputs(project_id,command,manifest,snapshot,actor):
    cm=manifest['manifest'];working={i['input_index']:i for i in snapshot['workspace_snapshot']['observation']['items']}
    return WorkspaceInput(**command.model_dump(),project_id=project_id,created_by=actor,
        source_ref=cm['source_ref'],task_ref=cm['task_ref'],
        files=[{**i,'native_mode':working[i['input_index']]['working']['native_mode'],'applied_mode':384} for i in cm['items']],
        deletions=[{'input_index':i['input_index'],'path':i['path']} for i in working.values() if i['working'] is None],
        total_size_bytes=cm['total_size_bytes'])
