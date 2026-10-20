"""Immutable tracked Git input plus explicitly selected untracked; never executable."""
from typing import Annotated,Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.source_grants import GrantModel,GrantId
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.workspace_git import GitPreviewCommand
from pyharness.application.workspace_git_contracts import GitSourcePreview

WorkspaceSnapshotId=Annotated[str,Field(pattern=r'^ws-[0-9a-f]{16}$')]


class WorkspaceSelectionCommand(GitPreviewCommand):
    project_revision: int = Field(ge=1)


class CaptureWorkspaceSnapshot(WorkspaceSelectionCommand):
    snapshot_id: WorkspaceSnapshotId
    confirmation_digest: Digest


class WorkspaceSnapshot(GrantModel):
    snapshot_id: WorkspaceSnapshotId
    revision: Literal[1] = 1
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    created_by: str = Field(min_length=1,max_length=200)
    root_identity: Digest
    metadata_root_identity: Digest | None
    metadata_grant_source_seq: int | None = Field(ge=1)
    observation: GitSourcePreview
    tracked_paths: list[str] = Field(max_length=200)
    confirmation_digest: Digest
    source_identity_digest: Digest
    scope: Literal['tracked_plus_selected_untracked'] = 'tracked_plus_selected_untracked'
    change_basis: Literal['raw_layer_comparison'] = 'raw_layer_comparison'
    exclusions: list[str] = Field(default_factory=list,max_length=0)

    @field_validator('revision',mode='before')
    @classmethod
    def first_revision(cls,value):
        if type(value) is not int or value!=1: raise ValueError('immutable snapshot revision')
        return value

    @model_validator(mode='after')
    def coherent(self):
        from pyharness.application.workspace_store import canonical,digest_of
        o=self.observation
        data=o.model_dump();expected=data.pop('observation_digest')
        selected=[i.path for i in o.items]
        if (self.project_id!=o.project_id or digest_of(canonical(data))!=expected
                or self.tracked_paths!=sorted(set(self.tracked_paths))
                or self.tracked_paths!=sorted(i.path for i in o.items if i.head is not None or i.index is not None)
                or not set(self.tracked_paths)<=set(selected)
                or (o.metadata_grant_id is None)!=(self.metadata_root_identity is None)
                or (o.metadata_grant_id is None)!=(self.metadata_grant_source_seq is None)):
            raise ValueError('incoherent immutable Git capture')
        return self


def working_items(snapshot):
    o=snapshot['observation']
    return [{'input_index':i['input_index'],'path':i['path'],'size_bytes':i['working']['size_bytes'],
             'content_digest':i['working']['digest']} for i in o['items'] if i['working'] is not None]


def change_flags(item):
    key=lambda layer: None if layer is None else (layer['git_oid'],layer['mode'])
    content=lambda layer: None if layer is None else layer['git_oid']
    # Never infer source core.filemode/autocrlf/filter policy from native permissions.
    return {'staged':key(item['head'])!=key(item['index']),
            'unstaged':content(item['index'])!=content(item['working']),
            'working_mode_differs':item['index'] is not None and item['working'] is not None
                and item['index']['mode']!=item['working']['mode']}
